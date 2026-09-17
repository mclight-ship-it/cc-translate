import AppKit
import ApplicationServices
import Combine
import UniformTypeIdentifiers
import XCTest
@testable import CCTranslateSupport

private actor PasteClipboardDouble: PlainTextPasteClipboard {
    private(set) var reads = 0
    private(set) var writes: [PlainTextPasteSnapshot] = []
    private(set) var verifications = 0
    var readResult: PlainTextPasteRead
    var writeResult: PlainTextPasteWrite = .written(12)
    var writeEffect: PlainTextPasteOutcome.ClipboardEffect = .plainTextWritten
    var owned = true
    let holdRead: Bool
    let holdWrite: Bool
    let holdVerification: Bool
    var enteredRead: XCTestExpectation?
    let enteredWrite: XCTestExpectation?
    let enteredVerification: XCTestExpectation?
    private var readContinuation: CheckedContinuation<PlainTextPasteRead, Never>?
    private var writeContinuation: CheckedContinuation<PlainTextPasteWrite, Never>?
    private var ownershipContinuation: CheckedContinuation<Bool, Never>?
    private var writingToken: PlainTextPasteCancellation?

    init(read: PlainTextPasteRead = .text(PlainTextPasteSnapshot(changeCount: 11, text: "synthetic")),
         holdRead: Bool = false, holdWrite: Bool = false, holdVerification: Bool = false,
         enteredRead: XCTestExpectation? = nil, enteredWrite: XCTestExpectation? = nil,
         enteredVerification: XCTestExpectation? = nil) {
        readResult = read
        self.holdRead = holdRead
        self.holdWrite = holdWrite
        self.holdVerification = holdVerification
        self.enteredRead = enteredRead
        self.enteredWrite = enteredWrite
        self.enteredVerification = enteredVerification
    }

    func read(cancellation: PlainTextPasteCancellation) async -> PlainTextPasteRead {
        reads += 1
        if holdRead {
            return await withCheckedContinuation {
                readContinuation = $0
                enteredRead?.fulfill()
            }
        }
        enteredRead?.fulfill()
        return readResult
    }

    func replace(_ snapshot: PlainTextPasteSnapshot, cancellation: PlainTextPasteCancellation) async -> PlainTextPasteWrite {
        writes.append(snapshot)
        if holdWrite {
            cancellation.record(clipboard: .mayHaveChanged)
            writingToken = cancellation
            return await withCheckedContinuation {
                writeContinuation = $0
                enteredWrite?.fulfill()
            }
        }
        cancellation.record(clipboard: writeEffect)
        enteredWrite?.fulfill()
        return writeResult
    }

    func stillOwns(_ count: Int) async -> Bool {
        verifications += 1
        if holdVerification {
            return await withCheckedContinuation {
                ownershipContinuation = $0
                enteredVerification?.fulfill()
            }
        }
        enteredVerification?.fulfill()
        return owned
    }

    func finishRead(_ result: PlainTextPasteRead) {
        let continuation = readContinuation
        readContinuation = nil
        continuation?.resume(returning: result)
    }

    func finishWrite(_ result: PlainTextPasteWrite, effect: PlainTextPasteOutcome.ClipboardEffect) {
        writingToken?.record(clipboard: effect)
        writingToken = nil
        let continuation = writeContinuation
        writeContinuation = nil
        continuation?.resume(returning: result)
    }

    func finishVerification(_ owned: Bool) {
        let continuation = ownershipContinuation
        ownershipContinuation = nil
        continuation?.resume(returning: owned)
    }

    func configureWrite(_ result: PlainTextPasteWrite, effect: PlainTextPasteOutcome.ClipboardEffect) {
        writeResult = result
        writeEffect = effect
    }

    func observeNextRead(_ entered: XCTestExpectation) { enteredRead = entered }
}

@MainActor
private final class PasteInputDouble: PlainTextPasteInput {
    let target = PlainTextPasteTarget(application: FocusTarget(pid: 123))
    var captureFailure: PlainTextPasteReason?
    var validationFailure: PlainTextPasteReason?
    var released = true
    var captureCalls = 0
    var validationCalls = 0
    var observedKeys: [[CGKeyCode]] = []
    var posts: [PlainTextPasteTarget] = []
    var onCapture: (() -> Void)?
    var onValidate: (() -> Void)?
    var onPost: (() -> Void)?
    var postResult = PlainTextPastePost(reason: .eventsSubmitted, events: .submittedUnconfirmed)

    func captureTarget() -> PlainTextPasteTargetResult {
        captureCalls += 1
        onCapture?()
        if let captureFailure { return .failure(captureFailure) }
        return .success(target)
    }
    func validate(_ target: PlainTextPasteTarget) -> PlainTextPasteReason? {
        validationCalls += 1
        onValidate?()
        return validationFailure
    }
    func keysReleased(_ keys: [CGKeyCode]) -> Bool {
        observedKeys.append(keys)
        return released
    }
    func postPaste(to target: PlainTextPasteTarget, releasing keys: [CGKeyCode],
                   cancellation: PlainTextPasteCancellation) -> PlainTextPastePost {
        onPost?()
        guard !cancellation.isCancelled else { return PlainTextPastePost(reason: .cancelled, events: .notPosted) }
        posts.append(target)
        return postResult
    }
}

private final class PasteTimerDouble: PlainTextPasteScheduled, @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
    var active: Bool {
        lock.lock()
        defer { lock.unlock() }
        return !cancelled
    }
}

@MainActor
private final class PasteSchedulerDouble: PlainTextPasteScheduler {
    var now: TimeInterval = 0
    private var scheduled: [(TimeInterval, PasteTimerDouble, @MainActor () -> Void)] = []
    var scheduledCount: Int { scheduled.filter { $0.1.active }.count }
    func schedule(after delay: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any PlainTextPasteScheduled {
        let timer = PasteTimerDouble()
        scheduled.append((now + delay, timer, action))
        return timer
    }
    func advance(_ seconds: TimeInterval) {
        now += seconds
        let due = scheduled.filter { $0.0 <= now }
        scheduled.removeAll { $0.0 <= now }
        for (_, timer, action) in due where timer.active { action() }
    }
    func deliverCancelledCallbacks() {
        let cancelled = scheduled.filter { !$0.1.active }
        scheduled.removeAll { !$0.1.active }
        for (_, _, action) in cancelled { action() }
    }
}

private enum PasteboardFixtureError: Error {
    case systemPasteboard, status(OSStatus), missingReference, missingItemData
}

@MainActor
private func privatePasteboardReference(_ board: NSPasteboard) throws -> Pasteboard {
    guard board.name != .general else { throw PasteboardFixtureError.systemPasteboard }
    var reference: Pasteboard?
    let status = PasteboardCreate(board.name.rawValue as CFString, &reference)
    guard status == noErr else { throw PasteboardFixtureError.status(status) }
    guard let reference else { throw PasteboardFixtureError.missingReference }
    return reference
}

private func checkPasteboardFixture(_ status: OSStatus) throws {
    guard status == noErr else { throw PasteboardFixtureError.status(status) }
}

@MainActor
private var pasteboardFixtureItems: [NSObject] = []

@MainActor
private func newPasteboardFixtureItem() -> PasteboardItemID {
    // Keep opaque item identities unique for the entire test process, including delayed promises.
    let item = NSObject()
    pasteboardFixtureItems.append(item)
    return Unmanaged.passUnretained(item).toOpaque()
}

private final class PastePromiseState {
    enum Response {
        case unavailable, text(Data), replaceOwner
        case blockedText(Data, entered: XCTestExpectation, release: DispatchSemaphore)
    }
    private let lock = NSLock()
    private var requested = 0
    let response: Response
    init(_ response: Response) { self.response = response }
    func provide(_ board: Pasteboard, item: PasteboardItemID, flavor: CFString) -> OSStatus {
        lock.lock()
        requested += 1
        lock.unlock()
        switch response {
        case .unavailable:
            return OSStatus(badPasteboardFlavorErr)
        case .text(let data):
            return PasteboardPutItemFlavor(board, item, flavor, data as CFData, PasteboardFlavorFlags(rawValue: 0))
        case .blockedText(let data, let entered, let release):
            entered.fulfill()
            XCTAssertFalse(Thread.isMainThread, "The deliberate synthetic blocking keeper must not block the main actor")
            guard !Thread.isMainThread, release.wait(timeout: .now() + 10) == .success else {
                return OSStatus(badPasteboardFlavorErr)
            }
            return PasteboardPutItemFlavor(board, item, flavor, data as CFData, PasteboardFlavorFlags(rawValue: 0))
        case .replaceOwner:
            let clear = PasteboardClear(board)
            guard clear == noErr else { return clear }
            _ = PasteboardSynchronize(board)
            let put = PasteboardPutItemFlavor(board, item, "public.utf8-plain-text" as CFString,
                                             Data("new provider owner".utf8) as CFData, PasteboardFlavorFlags(rawValue: 0))
            _ = PasteboardSynchronize(board)
            return put == noErr ? OSStatus(badPasteboardSyncErr) : put
        }
    }
    var calls: Int {
        lock.lock()
        defer { lock.unlock() }
        return requested
    }
}

// Only these fixtures install promise keepers. Ordinary fixtures publish eager CFData, not
// NSPasteboard.writeObjects' same-process AppKit item providers.
private final class PastePromiseFixture {
    private var reference: Pasteboard?
    let state: PastePromiseState

    @MainActor
    init(_ board: NSPasteboard, plainText: String? = nil, promisedType: NSPasteboard.PasteboardType = .string,
         response: PastePromiseState.Response = .unavailable, fileURL: String? = nil) throws {
        let reference = try privatePasteboardReference(board)
        self.reference = reference
        state = PastePromiseState(response)
        try checkPasteboardFixture(PasteboardClear(reference))
        _ = PasteboardSynchronize(reference)
        let item = newPasteboardFixtureItem()
        if let plainText {
            try checkPasteboardFixture(PasteboardPutItemFlavor(reference, item, "public.utf8-plain-text" as CFString,
                                                              Data(plainText.utf8) as CFData, PasteboardFlavorFlags(rawValue: 0)))
        }
        try checkPasteboardFixture(PasteboardSetPromiseKeeper(reference, { board, item, flavor, context in
            guard let context else { return OSStatus(noPasteboardPromiseKeeperErr) }
            return Unmanaged<PastePromiseState>.fromOpaque(context).takeUnretainedValue().provide(board, item: item, flavor: flavor)
        }, Unmanaged.passUnretained(state).toOpaque()))
        try checkPasteboardFixture(PasteboardPutItemFlavor(reference, item, promisedType.rawValue as CFString,
                                                          nil, PasteboardFlavorFlags(rawValue: 0)))
        if let fileURL {
            let file = newPasteboardFixtureItem()
            try checkPasteboardFixture(PasteboardPutItemFlavor(reference, file, "public.file-url" as CFString,
                                                              Data(fileURL.utf8) as CFData, PasteboardFlavorFlags(rawValue: 0)))
        }
        _ = PasteboardSynchronize(reference)
    }

    deinit {
        withExtendedLifetime(state) {
            if let reference {
                // Retire the borrowed callback context before ARC releases the owner reference.
                let status = PasteboardSetPromiseKeeper(reference, { _, _, _, _ in
                    OSStatus(noPasteboardPromiseKeeperErr)
                }, nil)
                XCTAssertEqual(status, noErr)
            }
            reference = nil
        }
    }

    var calls: Int { state.calls }

    @MainActor
    func itemCount() throws -> Int {
        let reference = try XCTUnwrap(reference)
        var count: ItemCount = 0
        try checkPasteboardFixture(PasteboardGetItemCount(reference, &count))
        return Int(count)
    }
}

final class PlainTextPasteTests: XCTestCase {
    @MainActor
    private func publish(_ board: NSPasteboard, items: [NSPasteboardItem]) throws {
        let representations = try items.map { item in
            try item.types.map { type in
                guard let data = item.data(forType: type) else { throw PasteboardFixtureError.missingItemData }
                return (type.rawValue, data)
            }
        }
        try publish(board, representations: representations)
    }

    @MainActor
    private func publish(_ board: NSPasteboard, representations: [[(String, Data)]]) throws {
        let reference = try privatePasteboardReference(board)
        try checkPasteboardFixture(PasteboardClear(reference))
        _ = PasteboardSynchronize(reference)
        for flavors in representations {
            let identifier = newPasteboardFixtureItem()
            for (type, data) in flavors {
                try checkPasteboardFixture(PasteboardPutItemFlavor(reference, identifier, type as CFString,
                                                                  data as CFData, PasteboardFlavorFlags(rawValue: 0)))
            }
        }
        _ = PasteboardSynchronize(reference)
    }

    @MainActor
    private func service(_ board: any PlainTextPasteClipboard, input: PasteInputDouble,
                         scheduler: PasteSchedulerDouble, enabled: Bool = true) -> PlainTextPasteService {
        PlainTextPasteService(enabled: enabled, clipboard: board, input: input, scheduler: scheduler,
                             clipboardTimeout: 2)
    }

    @MainActor
    private func wait(_ service: PlainTextPasteService, for expected: PlainTextPasteStatus) async {
        let arrived = expectation(description: "status \(expected)")
        let observation = service.$status.first(where: { $0 == expected }).sink { _ in arrived.fulfill() }
        await fulfillment(of: [arrived], timeout: 5)
        observation.cancel()
    }

    @MainActor
    private func drained(_ service: PlainTextPasteService) async {
        let arrived = expectation(description: "worker drained")
        let observation = service.$isBusy.first(where: { !$0 }).sink { _ in arrived.fulfill() }
        await fulfillment(of: [arrived], timeout: 5)
        observation.cancel()
    }

    private func finished(_ reason: PlainTextPasteReason,
                          clipboard: PlainTextPasteOutcome.ClipboardEffect = .unchanged,
                          events: PlainTextPasteOutcome.EventEffect = .notPosted) -> PlainTextPasteStatus {
        .finished(PlainTextPasteOutcome(reason: reason, clipboard: clipboard, events: events))
    }

    @MainActor
    private func assertPlainTextOnly(_ board: NSPasteboard, text: String,
                                     file: StaticString = #filePath, line: UInt = #line) {
        let types = Set(board.types ?? [])
        let legacyString = NSPasteboard.PasteboardType("NSStringPboardType")
        XCTAssertTrue(types.contains(.string), file: file, line: line)
        XCTAssertTrue(types.allSatisfy { $0 == legacyString || UTType($0.rawValue)?.conforms(to: .plainText) == true },
                      "Unexpected rich/file/image representation: \(types)",
                      file: file, line: line)
        for type in types {
            XCTAssertEqual(board.string(forType: type).map { Array($0.utf8) }, Array(text.utf8),
                           file: file, line: line)
        }
    }

    @MainActor
    func testDefaultConstructionAndSettingsDoNotTouchClipboardInputOrRegisterShortcut() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler, enabled: false)
        XCTAssertEqual(paste.status, .idle)
        XCTAssertEqual(paste.requestPaste(), .disabled)
        paste.setEnabled(true)
        paste.setEnabled(false)
        paste.cancel()
        paste.shutdown()
        let reads = await board.reads
        XCTAssertEqual(reads, 0)
        XCTAssertEqual(input.captureCalls, 0)
        XCTAssertTrue(input.posts.isEmpty)
        XCTAssertEqual(scheduler.scheduledCount, 0)
        let native = PlainTextPasteService()
        native.shutdown()
        XCTAssertFalse(native.isBusy)
    }

    @MainActor
    func testExplicitPasteWritesThenSubmitsOneUnconfirmedChordToCapturedTarget() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        XCTAssertEqual(paste.requestPaste(releasing: [40, 40]), .accepted)
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        let writes = await board.writes
        XCTAssertEqual(writes.count, 1)
        XCTAssertEqual(writes.first?.text, "synthetic")
        XCTAssertEqual(input.posts, [input.target])
        XCTAssertTrue(input.observedKeys.allSatisfy { $0 == [40] })
        XCTAssertFalse(paste.isBusy)
    }

    @MainActor
    func testInitialTrustSecureInputOrTargetFailureNeverReadsClipboard() async {
        let failures: [PlainTextPasteReason] = [.accessibilityUnavailable, .secureInput, .targetUnavailable]
        for failure in failures {
            let board = PasteClipboardDouble()
            let input = PasteInputDouble()
            input.captureFailure = failure
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            XCTAssertEqual(paste.requestPaste(), .accepted)
            XCTAssertEqual(paste.status, finished(failure))
            let reads = await board.reads
            XCTAssertEqual(reads, 0)
            XCTAssertTrue(input.posts.isEmpty)
        }
    }

    @MainActor
    func testCaptureBoundaryReentrantCancelCannotStartLateClipboardRead() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        input.onCapture = { paste.cancel() }
        paste.requestPaste()
        XCTAssertEqual(paste.status, finished(.cancelled))
        let reads = await board.reads
        XCTAssertEqual(reads, 0)
    }

    @MainActor
    func testDelayedReadCancelKeepsMainActorResponsiveAndRejectsAnotherWorker() async {
        let entered = expectation(description: "delayed clipboard entered")
        let board = PasteClipboardDouble(holdRead: true, enteredRead: entered)
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        paste.cancel()
        XCTAssertEqual(paste.status, finished(.cancelled))
        XCTAssertTrue(paste.isBusy)
        XCTAssertEqual(paste.requestPaste(), .busy)
        await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 11, text: "late old text")))
        await drained(paste)
        let writes = await board.writes
        XCTAssertTrue(writes.isEmpty)
        XCTAssertTrue(input.posts.isEmpty)
        XCTAssertEqual(paste.status, finished(.cancelled))
    }

    @MainActor
    func testDefaultClipboardDeadlineAllowsDelayedTransferBeyondTwoSeconds() async {
        let entered = expectation(description: "delayed system transfer")
        let board = PasteClipboardDouble(holdRead: true, enteredRead: entered)
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = PlainTextPasteService(enabled: true, clipboard: board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        scheduler.advance(29)
        XCTAssertEqual(paste.status, .reading)
        XCTAssertTrue(paste.isBusy)
        await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 11, text: "delayed synthetic text")))
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        let writes = await board.writes
        XCTAssertEqual(writes.map(\.text), ["delayed synthetic text"])
        XCTAssertEqual(input.posts.count, 1)
    }

    @MainActor
    func testTimedOutPromisedReadCannotWriteWhenItEventuallyReturns() async {
        let entered = expectation(description: "promised clipboard entered")
        let board = PasteClipboardDouble(holdRead: true, enteredRead: entered)
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        scheduler.advance(2)
        XCTAssertEqual(paste.status, finished(.clipboardTimedOut))
        XCTAssertEqual(paste.requestPaste(), .busy)
        await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 11, text: "late")))
        await drained(paste)
        let writes = await board.writes
        XCTAssertTrue(writes.isEmpty)
        XCTAssertTrue(input.posts.isEmpty)
    }

    @MainActor
    func testCancelledOldCallbacksCannotCancelNewExplicitRequestAfterReaderDrains() async {
        let first = expectation(description: "first delayed read")
        let second = expectation(description: "second explicit read")
        let board = PasteClipboardDouble(holdRead: true, enteredRead: first)
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await fulfillment(of: [first], timeout: 5)
        paste.cancel()
        XCTAssertEqual(paste.requestPaste(), .busy)
        await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 11, text: "old")))
        await drained(paste)
        await board.observeNextRead(second)
        XCTAssertEqual(paste.requestPaste(), .accepted)
        await fulfillment(of: [second], timeout: 5)
        scheduler.deliverCancelledCallbacks()
        XCTAssertEqual(paste.status, .reading)
        await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 20, text: "latest explicit")))
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        let writes = await board.writes
        XCTAssertEqual(writes.map(\.text), ["latest explicit"])
        XCTAssertEqual(input.posts.count, 1)
    }

    @MainActor
    func testDisableAndShutdownDiscardLateReadsWithoutReplayWhenEnabledAgain() async {
        for shutdown in [false, true] {
            let entered = expectation(description: "read entered \(shutdown)")
            let board = PasteClipboardDouble(holdRead: true, enteredRead: entered)
            let input = PasteInputDouble()
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            paste.requestPaste()
            await fulfillment(of: [entered], timeout: 5)
            if shutdown { paste.shutdown() } else { paste.setEnabled(false) }
            paste.setEnabled(true)
            XCTAssertEqual(paste.requestPaste(), shutdown ? .shutDown : .busy)
            await board.finishRead(.text(PlainTextPasteSnapshot(changeCount: 11, text: "stale")))
            await drained(paste)
            XCTAssertEqual(paste.status, finished(shutdown ? .shutDown : .disabled))
            let writes = await board.writes
            XCTAssertTrue(writes.isEmpty)
            XCTAssertTrue(input.posts.isEmpty)
        }
    }

    @MainActor
    func testUnavailableInvalidOrChangedClipboardReadCannotReachWriteOrPost() async {
        let reasons: [PlainTextPasteReason] = [.unavailableData, .invalidRichText, .clipboardChanged, .noText, .unsupportedRepresentation]
        for reason in reasons {
            let board = PasteClipboardDouble(read: .failure(reason))
            let input = PasteInputDouble()
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            paste.requestPaste()
            await wait(paste, for: finished(reason))
            let writes = await board.writes
            XCTAssertTrue(writes.isEmpty)
            XCTAssertTrue(input.posts.isEmpty)
        }
    }

    @MainActor
    func testModifiersAndTriggerKeyMustReleaseBeforeConversionAndPaste() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        input.released = false
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste(releasing: [40])
        await wait(paste, for: .waitingForKeys)
        let before = await board.writes
        XCTAssertTrue(before.isEmpty)
        XCTAssertTrue(input.posts.isEmpty)
        input.released = true
        scheduler.advance(0.02)
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        XCTAssertEqual(input.posts.count, 1)
    }

    @MainActor
    func testHeldModifiersTimeoutDoesNotChangeClipboard() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        input.released = false
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await wait(paste, for: .waitingForKeys)
        scheduler.advance(3)
        XCTAssertEqual(paste.status, finished(.keyReleaseTimedOut))
        let writes = await board.writes
        XCTAssertTrue(writes.isEmpty)
    }

    @MainActor
    func testTargetTrustOrSecureInputChangeWhileWaitingPreventsWrite() async {
        let reasons: [PlainTextPasteReason] = [.targetChanged, .accessibilityUnavailable, .secureInput]
        for reason in reasons {
            let board = PasteClipboardDouble()
            let input = PasteInputDouble()
            input.released = false
            let scheduler = PasteSchedulerDouble()
            let paste = service(board, input: input, scheduler: scheduler)
            paste.requestPaste()
            await wait(paste, for: .waitingForKeys)
            input.validationFailure = reason
            scheduler.advance(0.02)
            XCTAssertEqual(paste.status, finished(reason))
            let writes = await board.writes
            XCTAssertTrue(writes.isEmpty)
        }
    }

    @MainActor
    func testCancelWhileWaitingDiscardsTimerAndNeverReplaysAfterRelease() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        input.released = false
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await wait(paste, for: .waitingForKeys)
        paste.cancel()
        input.released = true
        scheduler.advance(10)
        XCTAssertFalse(paste.isBusy)
        XCTAssertEqual(paste.status, finished(.cancelled))
        let writes = await board.writes
        XCTAssertTrue(writes.isEmpty)
    }

    @MainActor
    func testWriteFailureDistinguishesUntouchedAndClearedClipboardWithoutRestore() async {
        let effects: [PlainTextPasteOutcome.ClipboardEffect] = [.unchanged, .cleared]
        for effect in effects {
            let board = PasteClipboardDouble()
            await board.configureWrite(.failure(.writeFailed), effect: effect)
            let input = PasteInputDouble()
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            paste.requestPaste()
            await wait(paste, for: finished(.writeFailed, clipboard: effect))
            let writes = await board.writes
            XCTAssertEqual(writes.count, 1)
            XCTAssertTrue(input.posts.isEmpty)
        }
    }

    @MainActor
    func testCancellationDuringWriteReportsUncertainThenActualPartialEffect() async {
        let entered = expectation(description: "write entered")
        let board = PasteClipboardDouble(holdWrite: true, enteredWrite: entered)
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        paste.cancel()
        XCTAssertEqual(paste.status, finished(.cancelled, clipboard: .mayHaveChanged))
        XCTAssertTrue(paste.isBusy)
        await board.finishWrite(.written(12), effect: .plainTextWritten)
        await drained(paste)
        XCTAssertEqual(paste.status, finished(.cancelled, clipboard: .plainTextWritten))
        XCTAssertTrue(input.posts.isEmpty)
        let writes = await board.writes
        XCTAssertEqual(writes.count, 1)
    }

    @MainActor
    func testDestinationChangeAfterConversionIsPartialNotSuccessfulPaste() async {
        let entered = expectation(description: "write entered")
        let board = PasteClipboardDouble(holdWrite: true, enteredWrite: entered)
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        input.validationFailure = .targetChanged
        await board.finishWrite(.written(12), effect: .plainTextWritten)
        await wait(paste, for: finished(.targetChanged, clipboard: .plainTextWritten))
        XCTAssertTrue(input.posts.isEmpty)
    }

    @MainActor
    func testNewClipboardOwnerAfterWritePreventsPostAndDoesNotRestore() async {
        let entered = expectation(description: "ownership verification entered")
        let board = PasteClipboardDouble(holdVerification: true, enteredVerification: entered)
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        await board.finishVerification(false)
        await wait(paste, for: finished(.clipboardChanged, clipboard: .plainTextWritten))
        let writes = await board.writes
        XCTAssertEqual(writes.count, 1)
        XCTAssertTrue(input.posts.isEmpty)
    }

    @MainActor
    func testTargetIsRevalidatedAfterAsynchronousClipboardOwnershipCheck() async {
        let entered = expectation(description: "ownership verification entered")
        let board = PasteClipboardDouble(holdVerification: true, enteredVerification: entered)
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        input.validationFailure = .secureInput
        await board.finishVerification(true)
        await wait(paste, for: finished(.secureInput, clipboard: .plainTextWritten))
        XCTAssertTrue(input.posts.isEmpty)
    }

    @MainActor
    func testModifiersPressedAgainAfterWriteWaitWithoutConvertingTwice() async {
        let entered = expectation(description: "write entered")
        let board = PasteClipboardDouble(holdWrite: true, enteredWrite: entered)
        let input = PasteInputDouble()
        let scheduler = PasteSchedulerDouble()
        let paste = service(board, input: input, scheduler: scheduler)
        paste.requestPaste()
        await fulfillment(of: [entered], timeout: 5)
        input.released = false
        await board.finishWrite(.written(12), effect: .plainTextWritten)
        await wait(paste, for: .waitingForKeys)
        XCTAssertTrue(input.posts.isEmpty)
        input.released = true
        scheduler.advance(0.02)
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        let writes = await board.writes
        XCTAssertEqual(writes.count, 1)
    }

    @MainActor
    func testDisableOrShutdownAfterWriteCannotPostLateVerificationResult() async {
        for shutdown in [false, true] {
            let entered = expectation(description: "verification entered \(shutdown)")
            let board = PasteClipboardDouble(holdVerification: true, enteredVerification: entered)
            let input = PasteInputDouble()
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            paste.requestPaste()
            await fulfillment(of: [entered], timeout: 5)
            if shutdown { paste.shutdown() } else { paste.setEnabled(false) }
            XCTAssertEqual(paste.status, finished(shutdown ? .shutDown : .disabled, clipboard: .plainTextWritten))
            await board.finishVerification(true)
            await drained(paste)
            XCTAssertTrue(input.posts.isEmpty)
            XCTAssertEqual(paste.status, finished(shutdown ? .shutDown : .disabled, clipboard: .plainTextWritten))
        }
    }

    @MainActor
    func testPostFailureAndPartialSubmissionNeverClaimPasteSucceeded() async {
        let failures: [(PlainTextPasteReason, PlainTextPasteOutcome.EventEffect)] = [
            (.eventCreationFailed, .notPosted), (.eventPostingFailed, .notPosted),
            (.eventPostingFailed, .mayHavePosted)
        ]
        for (reason, events) in failures {
            let board = PasteClipboardDouble()
            let input = PasteInputDouble()
            input.postResult = PlainTextPastePost(reason: reason, events: events)
            let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
            paste.requestPaste()
            await wait(paste, for: finished(reason, clipboard: .plainTextWritten, events: events))
            let writes = await board.writes
            XCTAssertEqual(writes.count, 1)
            XCTAssertEqual(input.posts.count, 1)
        }
    }

    @MainActor
    func testReentrantCancelAtPostingBoundaryPreventsKeysAndPreservesPartialOutcome() async {
        let board = PasteClipboardDouble()
        let input = PasteInputDouble()
        let paste = service(board, input: input, scheduler: PasteSchedulerDouble())
        input.onPost = { paste.cancel() }
        paste.requestPaste()
        await wait(paste, for: finished(.cancelled, clipboard: .plainTextWritten))
        XCTAssertTrue(input.posts.isEmpty)
        XCTAssertFalse(paste.isBusy)
    }

    @MainActor
    func testLateCancelDisableAndShutdownDoNotClaimSubmittedEventsWerePrevented() async {
        let input = PasteInputDouble()
        let paste = service(PasteClipboardDouble(), input: input, scheduler: PasteSchedulerDouble())
        paste.requestPaste()
        let receipt = finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed)
        await wait(paste, for: receipt)
        paste.cancel()
        paste.setEnabled(false)
        paste.shutdown()
        XCTAssertEqual(paste.status, receipt)
        XCTAssertFalse(paste.isEnabled)
        XCTAssertEqual(paste.requestPaste(), .shutDown)
        XCTAssertEqual(input.posts.count, 1)
    }

    @MainActor
    func testPrivateRichAndPlainClipboardPrefersExactUnicodePlainText() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "  \u{4E2D}\u{6587}\tCafe\u{0301} \u{1F642}\r\nsecond\n\u{2028}  "
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(text, forType: .string))
        XCTAssertTrue(item.setString("<b>not the selected plain text</b><img src='https://invalid.example/no-fetch'>", forType: .html))
        board.clearContents()
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            trace: { print("PasteboardTrace richPlain: \($0)") })
        let cancellation = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: cancellation) else {
            return XCTFail("Expected exact plain representation")
        }
        XCTAssertEqual(snapshot.text, text)
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        guard case .written(let count) = await adapter.replace(snapshot, cancellation: cancellation) else {
            return XCTFail("Expected intentional formatting removal")
        }
        let owns = await adapter.stillOwns(count)
        XCTAssertTrue(owns)
        assertPlainTextOnly(board, text: text)
        XCTAssertEqual(board.string(forType: .string), text)
        XCTAssertEqual(board.string(forType: .string).map { Array($0.utf8) }, Array(text.utf8))
    }

    @MainActor
    func testPrivateTextWithAlternativeImageRepresentationDoesNotReadImageData() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "name\tvalue\n\u{4E2D}\u{6587}\t42"
        let provider = try PastePromiseFixture(board, plainText: text, promisedType: .png)
        defer { withExtendedLifetime(provider) {} }
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            trace: { print("PasteboardTrace alternativeImage: \($0)") })
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("An alternative image representation must not hide usable plain text")
        }
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        guard case .written = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Expected explicit plain-text conversion")
        }
        assertPlainTextOnly(board, text: text)
        XCTAssertEqual(board.string(forType: .string), text)
        XCTAssertEqual(provider.calls, 0)
    }

    @MainActor
    func testPrivateRTFOnlyClipboardUsesAppKitConversion() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "Bold \u{4E2D}\u{6587}\t123\nnext line"
        let attributed = NSAttributedString(string: text, attributes: [.font: NSFont.boldSystemFont(ofSize: 14)])
        let data = try attributed.data(from: NSRange(location: 0, length: attributed.length),
                                       documentAttributes: [.documentType: NSAttributedString.DocumentType.rtf])
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setData(data, forType: .rtf))
        try publish(board, items: [item])
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected RTF text") }
        XCTAssertEqual(snapshot.text, text)
        guard case .written = await adapter.replace(snapshot, cancellation: token) else { return XCTFail("Expected write") }
        XCTAssertEqual(board.string(forType: .string), text)
        assertPlainTextOnly(board, text: text)
    }

    @MainActor
    func testPrivateMultipleTextItemsHaveExplicitNewlineBoundariesAndNoTranslationCharacterLimit() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let strings = [" first\t", String(repeating: "a", count: 20_000), "\nlast  "]
        let items = strings.map { text -> NSPasteboardItem in
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setString(text, forType: .string))
            return item
        }
        try publish(board, items: items)
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected all items") }
        XCTAssertEqual(snapshot.text, strings.joined(separator: "\n"))
        guard case .written = await adapter.replace(snapshot, cancellation: token) else { return XCTFail("Expected write") }
        XCTAssertEqual(board.string(forType: .string), strings.joined(separator: "\n"))
    }

    @MainActor
    func testPrivateImageFileAndMixedFileTextClipboardsRemainUntouched() async throws {
        let types: [NSPasteboard.PasteboardType] = [.png, .fileURL, NSPasteboard.PasteboardType("com.apple.pasteboard.promised-file-url")]
        for type in types {
            let board = NSPasteboard.withUniqueName()
            defer { board.releaseGlobally() }
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setData(Data([1, 2, 3]), forType: type))
            if type == .fileURL { XCTAssertTrue(item.setString("filename.txt", forType: .string)) }
            try publish(board, items: [item])
            let count = board.changeCount
            let adapter = SystemPlainTextPasteClipboard(name: board.name)
            guard case .failure(.noText) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
                return XCTFail("Non-text payload must not be destroyed")
            }
            XCTAssertEqual(board.changeCount, count)
            XCTAssertEqual(board.data(forType: type), Data([1, 2, 3]))
        }
    }

    @MainActor
    func testPrivateHTMLOnlyIsNotRenderedOrConvertedViaNetworkCapableImporter() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let html = "<html><img src='https://invalid.example/not-fetched'><b>text</b></html>"
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(html, forType: .html))
        try publish(board, items: [item])
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.unsupportedRepresentation) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("HTML-only must not start an external-resource importer")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.string(forType: .html), html)
    }

    @MainActor
    func testPrivateMixedTextAndFileItemsAreNotPartiallyConverted() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = NSPasteboardItem()
        XCTAssertTrue(text.setString("keep with file", forType: .string))
        let file = NSPasteboardItem()
        XCTAssertTrue(file.setString("file:///synthetic/never-opened.txt", forType: .fileURL))
        board.clearContents()
        XCTAssertTrue(board.writeObjects([text, file]))
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.noText) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Do not destroy a file item after reading a text item")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.pasteboardItems?.count, 2)
    }

    @MainActor
    func testPrivateTabularTextRetainsTabsAndLineBreaks() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "name\tvalue\r\nfirst\t 42 \r\n"
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(text, forType: .tabularText))
        try publish(board, items: [item])
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .text(let snapshot) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Expected system tabular text representation")
        }
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
    }

    @MainActor
    func testPrivateInvalidRTFDoesNotClearClipboard() async throws {
        let cases = [Data(), Data([0, 255, 1, 2]), Data("{\\rtf".utf8), Data("{\\rtfish}".utf8)]
        for (index, data) in cases.enumerated() {
            let board = NSPasteboard.withUniqueName()
            defer { board.releaseGlobally() }
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setData(data, forType: .rtf))
            try publish(board, items: [item])
            let count = board.changeCount
            let adapter = SystemPlainTextPasteClipboard(name: board.name)
            let token = PlainTextPasteCancellation()
            let result = await adapter.read(cancellation: token)
            switch result {
            case .failure(let reason):
                print("Synthetic malformed RTF case \(index), bytes \(data.count), reason \(reason)")
                XCTAssertTrue(reason == .invalidRichText || (data.isEmpty && reason == .unavailableData),
                              "Case \(index): unexpected rejection reason \(reason)")
            case .text:
                XCTFail("Case \(index): malformed RTF must never become a successful empty paste")
            }
            XCTAssertEqual(token.outcome(.invalidRichText).clipboard, .unchanged)
            XCTAssertEqual(board.changeCount, count)
            XCTAssertTrue(board.types?.contains(.rtf) == true)
            let stored = board.data(forType: .rtf)
            if data.isEmpty {
                XCTAssertTrue(stored == nil || stored == data, "Empty data can be unavailable, not replacement bytes")
            } else {
                XCTAssertEqual(stored, data)
            }
        }
    }

    @MainActor
    func testPrivatePromisedButUnavailableTextReturnsFailureWithoutChangingClipboard() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = try PastePromiseFixture(board)
        defer { withExtendedLifetime(provider) {} }
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.unavailableData) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Missing promised data must not become an empty successful paste")
        }
        XCTAssertGreaterThan(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
    }

    @MainActor
    func testPrivateRichHTMLProviderIsNeverAskedWhenPlainTextIsAvailable() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = try PastePromiseFixture(board, plainText: "plain wins", promisedType: .html)
        defer { withExtendedLifetime(provider) {} }
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .text(let snapshot) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Expected plain representation")
        }
        XCTAssertEqual(snapshot.text, "plain wins")
        XCTAssertEqual(provider.calls, 0)
    }

    @MainActor
    func testPrivateProviderChangingOwnerDuringReadCannotProduceStaleSnapshot() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = try PastePromiseFixture(board, response: .replaceOwner)
        defer { withExtendedLifetime(provider) {} }
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.clipboardChanged) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Provider changed ownership while returning old text")
        }
        XCTAssertEqual(board.string(forType: .string), "new provider owner")
    }

    @MainActor
    func testPrivatePasteboardServiceStripsFormattingAndRequestsExactlyOneInjectedPaste() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString("private synthetic text", forType: .string))
        XCTAssertTrue(item.setString("<b>private synthetic text</b>", forType: .html))
        try publish(board, items: [item])
        let input = PasteInputDouble()
        let paste = service(SystemPlainTextPasteClipboard(name: board.name), input: input,
                            scheduler: PasteSchedulerDouble())
        paste.requestPaste(releasing: [40])
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        XCTAssertEqual(input.posts, [input.target])
        assertPlainTextOnly(board, text: "private synthetic text")
        XCTAssertEqual(board.string(forType: .string), "private synthetic text")
    }

    @MainActor
    func testPrivateNewOwnerBetweenReadAndWriteIsNeverOverwrittenOrRestored() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("old synthetic", forType: .string))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected old text") }
        let replacementCount = board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("new owner", forType: .string))
        guard case .failure(.clipboardChanged) = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Stale read must not overwrite newer clipboard")
        }
        XCTAssertEqual(board.changeCount, replacementCount)
        XCTAssertEqual(board.string(forType: .string), "new owner")
        XCTAssertEqual(token.outcome(.clipboardChanged).clipboard, .unchanged)
    }

    @MainActor
    func testPrivateCancelledSnapshotCannotWriteAndEmptyStringIsStillValidText() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("", forType: .string))
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Empty text has a representation") }
        XCTAssertEqual(snapshot.text, "")
        token.cancel()
        guard case .failure(.cancelled) = await adapter.replace(snapshot, cancellation: token) else { return XCTFail("Expected cancel") }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.string(forType: .string), "")
    }

    @MainActor
    func testPrivateUTF16AndLegacyPlainTextDecodeWithoutLoss() async throws {
        let cases: [(String, String.Encoding, String)] = [
            ("public.utf16-plain-text", .utf16, " \u{4E2D}\u{6587}\tCafe\u{0301}\r\n\u{1F642}  "),
            ("public.utf16-plain-text", .utf16LittleEndian, " \u{4E2D}\u{6587}\tno BOM\r\n "),
            ("public.utf16-external-plain-text", .utf16, " \u{4E2D}\u{6587}\texternal BOM\r\n "),
            ("com.apple.traditional-mac-plain-text", .macOSRoman, " Caf\u{00E9}\t42\r\n ")
        ]
        for (type, encoding, text) in cases {
            let board = NSPasteboard.withUniqueName()
            defer { board.releaseGlobally() }
            let bytes = try XCTUnwrap(text.data(using: encoding))
            // NSPasteboardItem may translate legacy aliases, including their line endings.
            // Publish the intended external bytes directly rather than pre-converting the fixture.
            try publish(board, representations: [[(type, bytes)]])
            let sourceReference = try privatePasteboardReference(board)
            _ = PasteboardSynchronize(sourceReference)
            var fixtureBytes: CFData?
            let fixtureStatus = PasteboardCopyItemFlavorData(sourceReference, try XCTUnwrap(PasteboardItemID(bitPattern: 1)),
                                                             type as CFString, &fixtureBytes)
            print("PasteboardFixture \(type), input \(Array(bytes)), copied \(fixtureBytes.map { Array($0 as Data) } ?? []), status \(fixtureStatus)")
            let adapter = SystemPlainTextPasteClipboard(name: board.name,
                trace: { print("PasteboardTrace encoding \(type): \($0)") })
            let token = PlainTextPasteCancellation()
            guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
                XCTFail("Expected lossless platform decoding for \(type)")
                continue
            }
            XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
            let write = await adapter.replace(snapshot, cancellation: token)
            guard case .written = write else {
                XCTFail("Expected canonical UTF-8 publication for \(type): \(write), \(token.outcome(.writeFailed))")
                continue
            }
            assertPlainTextOnly(board, text: text)
        }
    }

    @MainActor
    func testPrivateInvalidUTF8DoesNotBecomeReplacementCharacters() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let bytes = Data([0xFF, 0xFE, 0x41])
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setData(bytes, forType: .string))
        try publish(board, items: [item])
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.unavailableData) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Invalid UTF-8 must not become lossy successful text")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.data(forType: .string), bytes)
    }

    @MainActor
    func testPrivateFulfilledCPromisePreservesTextAndCanBeWrittenOnce() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "  promised \u{4E2D}\u{6587}\tCafe\u{0301}\r\n\u{1F642}\n"
        let provider = try PastePromiseFixture(board, response: .text(Data(text.utf8)))
        defer { withExtendedLifetime(provider) {} }
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("Fulfilling data without clearing must not invalidate the ownership lease")
        }
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        XCTAssertEqual(provider.calls, 1)
        let write = await adapter.replace(snapshot, cancellation: token)
        guard case .written(let lease) = write else {
            return XCTFail("Fulfilled text must support explicit formatting removal: \(write), \(token.outcome(.writeFailed))")
        }
        let owns = await adapter.stillOwns(lease)
        XCTAssertTrue(owns)
        let count = board.changeCount
        guard case .failure(.clipboardChanged) = await adapter.replace(snapshot, cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("A consumed read lease must not be reusable")
        }
        XCTAssertEqual(board.changeCount, count)
        assertPlainTextOnly(board, text: text)
    }

    @MainActor
    func testPrivateWrittenLeaseIsInvalidatedBySameProcessAppKitOwner() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("original", forType: .string))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token),
              case .written(let lease) = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Expected one native read/write")
        }
        let initiallyOwned = await adapter.stillOwns(lease)
        XCTAssertTrue(initiallyOwned)
        let newCount = board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("same-process new owner", forType: .string))
        let firstCheck = await adapter.stillOwns(lease)
        let secondCheck = await adapter.stillOwns(lease)
        XCTAssertFalse(firstCheck, "Application-wide ownership is not this operation's ownership")
        XCTAssertFalse(secondCheck, "Synchronizing must not resurrect an invalidated lease")
        XCTAssertEqual(board.changeCount, newCount)
        XCTAssertEqual(board.string(forType: .string), "same-process new owner")
    }

    @MainActor
    func testPrivateSnapshotCannotCrossAdaptersOrReplayAfterAnotherRead() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("original", forType: .string))
        let first = SystemPlainTextPasteClipboard(name: board.name)
        let second = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let old) = await first.read(cancellation: token),
              case .text(let foreign) = await second.read(cancellation: token) else {
            return XCTFail("Expected independent read leases")
        }
        XCTAssertEqual(old.changeCount, foreign.changeCount, "Local ordinals alone cannot distinguish references")
        guard case .failure(.clipboardChanged) = await second.replace(old, cancellation: token) else {
            return XCTFail("A different adapter must reject a foreign snapshot")
        }
        guard case .text(let current) = await first.read(cancellation: token) else { return XCTFail("Expected fresh read") }
        guard case .failure(.clipboardChanged) = await first.replace(old, cancellation: token) else {
            return XCTFail("A later read invalidates the earlier read lease")
        }
        guard case .written = await first.replace(current, cancellation: token) else { return XCTFail("Expected current lease write") }
        let count = board.changeCount
        guard case .failure(.clipboardChanged) = await second.replace(foreign, cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("The other reference must observe the write despite shared application ownership")
        }
        XCTAssertEqual(board.changeCount, count)
        assertPlainTextOnly(board, text: "original")
    }

    @MainActor
    func testPrivateCancelWhileCPromiseWaitsDrainsWithoutPosting() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let entered = expectation(description: "C promise entered")
        let release = DispatchSemaphore(value: 0)
        let provider = try PastePromiseFixture(board, response: .blockedText(Data("late synthetic".utf8),
                                                                           entered: entered, release: release))
        defer { release.signal(); withExtendedLifetime(provider) {} }
        let count = board.changeCount
        let input = PasteInputDouble()
        let paste = service(SystemPlainTextPasteClipboard(name: board.name), input: input, scheduler: PasteSchedulerDouble())
        XCTAssertEqual(paste.requestPaste(), .accepted)
        await fulfillment(of: [entered], timeout: 5)
        paste.cancel()
        XCTAssertEqual(paste.status, finished(.cancelled))
        XCTAssertTrue(paste.isBusy)
        XCTAssertEqual(paste.requestPaste(), .busy)
        release.signal()
        await drained(paste)
        XCTAssertEqual(paste.status, finished(.cancelled))
        XCTAssertTrue(input.posts.isEmpty)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(provider.calls, 1)
        paste.shutdown()
    }

    @MainActor
    func testPrivateMixedFileClipboardDoesNotFulfillEarlierTextPromise() async throws {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = try PastePromiseFixture(board, fileURL: "file:///synthetic/never-opened.txt")
        defer { withExtendedLifetime(provider) {} }
        XCTAssertEqual(try provider.itemCount(), 2)
        print("PasteboardFixture mixedFile before read: C=\(try provider.itemCount()), AppKit=\(board.pasteboardItems?.count ?? -1)")
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            trace: { print("PasteboardTrace mixedFile: \($0)") })
        let read = await adapter.read(cancellation: PlainTextPasteCancellation())
        guard case .failure(.noText) = read else {
            return XCTFail("Inspect all metadata before fulfilling text on a mixed-file clipboard: \(read), promise calls \(provider.calls)")
        }
        XCTAssertEqual(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(try provider.itemCount(), 2)
        print("PasteboardFixture mixedFile after read: C=\(try provider.itemCount()), AppKit=\(board.pasteboardItems?.count ?? -1)")
        XCTAssertEqual(board.pasteboardItems?.count, 2)
    }
}
