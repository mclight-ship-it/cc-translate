import AppKit
import Combine
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

private final class PasteDataProvider: NSObject, NSPasteboardItemDataProvider {
    private let lock = NSLock()
    private var requested = 0
    private let replaceOwner: Bool
    init(replaceOwner: Bool = false) { self.replaceOwner = replaceOwner }
    func pasteboard(_ pasteboard: NSPasteboard?, item: NSPasteboardItem,
                    provideDataForType type: NSPasteboard.PasteboardType) {
        lock.lock()
        requested += 1
        lock.unlock()
        if replaceOwner, let pasteboard {
            pasteboard.declareTypes([.string], owner: nil)
            _ = pasteboard.setString("new provider owner", forType: .string)
            _ = item.setString("stale provider text", forType: .string)
        }
    }
    var calls: Int {
        lock.lock()
        defer { lock.unlock() }
        return requested
    }
}

final class PlainTextPasteTests: XCTestCase {
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
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let cancellation = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: cancellation) else {
            return XCTFail("Expected exact plain representation")
        }
        XCTAssertEqual(snapshot.text, text)
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        guard case .written(let count) = await adapter.replace(snapshot, cancellation: cancellation) else {
            return XCTFail("Expected intentional formatting removal")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.types, [.string])
        XCTAssertEqual(board.string(forType: .string), text)
        XCTAssertEqual(board.string(forType: .string).map { Array($0.utf8) }, Array(text.utf8))
    }

    @MainActor
    func testPrivateTextWithAlternativeImageRepresentationDoesNotReadImageData() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "name\tvalue\n\u{4E2D}\u{6587}\t42"
        let provider = PasteDataProvider()
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(text, forType: .string))
        XCTAssertTrue(item.setDataProvider(provider, forTypes: [.png]))
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("An alternative image representation must not hide usable plain text")
        }
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        guard case .written = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Expected explicit plain-text conversion")
        }
        XCTAssertEqual(board.types, [.string])
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
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected RTF text") }
        XCTAssertEqual(snapshot.text, text)
        guard case .written = await adapter.replace(snapshot, cancellation: token) else { return XCTFail("Expected write") }
        XCTAssertEqual(board.string(forType: .string), text)
        XCTAssertEqual(board.types, [.string])
    }

    @MainActor
    func testPrivateMultipleTextItemsHaveExplicitNewlineBoundariesAndNoTranslationCharacterLimit() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let strings = [" first\t", String(repeating: "a", count: 20_000), "\nlast  "]
        let items = strings.map { text -> NSPasteboardItem in
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setString(text, forType: .string))
            return item
        }
        XCTAssertTrue(board.writeObjects(items))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected all items") }
        XCTAssertEqual(snapshot.text, strings.joined(separator: "\n"))
        guard case .written = await adapter.replace(snapshot, cancellation: token) else { return XCTFail("Expected write") }
        XCTAssertEqual(board.string(forType: .string), strings.joined(separator: "\n"))
    }

    @MainActor
    func testPrivateImageFileAndMixedFileTextClipboardsRemainUntouched() async {
        let types: [NSPasteboard.PasteboardType] = [.png, .fileURL, NSPasteboard.PasteboardType("com.apple.pasteboard.promised-file-url")]
        for type in types {
            let board = NSPasteboard.withUniqueName()
            defer { board.releaseGlobally() }
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setData(Data([1, 2, 3]), forType: type))
            if type == .fileURL { XCTAssertTrue(item.setString("filename.txt", forType: .string)) }
            XCTAssertTrue(board.writeObjects([item]))
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
    func testPrivateHTMLOnlyIsNotRenderedOrConvertedViaNetworkCapableImporter() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let html = "<html><img src='https://invalid.example/not-fetched'><b>text</b></html>"
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(html, forType: .html))
        XCTAssertTrue(board.writeObjects([item]))
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.unsupportedRepresentation) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("HTML-only must not start an external-resource importer")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.string(forType: .html), html)
    }

    @MainActor
    func testPrivateMixedTextAndFileItemsAreNotPartiallyConverted() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = NSPasteboardItem()
        XCTAssertTrue(text.setString("keep with file", forType: .string))
        let file = NSPasteboardItem()
        XCTAssertTrue(file.setString("file:///synthetic/never-opened.txt", forType: .fileURL))
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
    func testPrivateTabularTextRetainsTabsAndLineBreaks() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let text = "name\tvalue\r\nfirst\t 42 \r\n"
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString(text, forType: .tabularText))
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .text(let snapshot) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Expected system tabular text representation")
        }
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
    }

    @MainActor
    func testPrivateInvalidRTFDoesNotClearClipboard() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setData(Data([0, 255, 1, 2]), forType: .rtf))
        XCTAssertTrue(board.writeObjects([item]))
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.invalidRichText) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Malformed RTF must fail explicitly")
        }
        XCTAssertEqual(board.changeCount, count)
    }

    @MainActor
    func testPrivatePromisedButUnavailableTextReturnsFailureWithoutChangingClipboard() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = PasteDataProvider()
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setDataProvider(provider, forTypes: [.string]))
        XCTAssertTrue(board.writeObjects([item]))
        let count = board.changeCount
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.unavailableData) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Missing promised data must not become an empty successful paste")
        }
        XCTAssertGreaterThan(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
    }

    @MainActor
    func testPrivateRichHTMLProviderIsNeverAskedWhenPlainTextIsAvailable() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = PasteDataProvider()
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString("plain wins", forType: .string))
        XCTAssertTrue(item.setDataProvider(provider, forTypes: [.html]))
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .text(let snapshot) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Expected plain representation")
        }
        XCTAssertEqual(snapshot.text, "plain wins")
        XCTAssertEqual(provider.calls, 0)
    }

    @MainActor
    func testPrivateProviderChangingOwnerDuringReadCannotProduceStaleSnapshot() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let provider = PasteDataProvider(replaceOwner: true)
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setDataProvider(provider, forTypes: [.string]))
        XCTAssertTrue(board.writeObjects([item]))
        let adapter = SystemPlainTextPasteClipboard(name: board.name)
        guard case .failure(.clipboardChanged) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Provider changed ownership while returning old text")
        }
        XCTAssertEqual(board.string(forType: .string), "new provider owner")
    }

    @MainActor
    func testPrivatePasteboardServiceStripsFormattingAndRequestsExactlyOneInjectedPaste() async {
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        let item = NSPasteboardItem()
        XCTAssertTrue(item.setString("private synthetic text", forType: .string))
        XCTAssertTrue(item.setString("<b>private synthetic text</b>", forType: .html))
        XCTAssertTrue(board.writeObjects([item]))
        let input = PasteInputDouble()
        let paste = service(SystemPlainTextPasteClipboard(name: board.name), input: input,
                            scheduler: PasteSchedulerDouble())
        paste.requestPaste(releasing: [40])
        await wait(paste, for: finished(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed))
        XCTAssertEqual(input.posts, [input.target])
        XCTAssertEqual(board.types, [.string])
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
}
