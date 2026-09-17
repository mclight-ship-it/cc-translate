import AppKit
import ApplicationServices
import Combine
import CoreServices
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
    case systemPasteboard, status(OSStatus), missingReference, missingItemData, identifierExhausted, untrackedBoard
}

private struct PasteboardFixtureGeneration {
    let name: String
    var releaseQueued = false
    var releaseRequested = false
}

// Metadata only: retaining boards or C references here would change the lifecycle under test.
@MainActor private var pasteboardFixtureGenerations: [PasteboardFixtureGeneration] = []
@MainActor private var pasteboardFixtureLatestGeneration: [String: Int] = [:]
@MainActor private var pasteboardFixtureItemGeneration: [UInt: Int] = [:]

@MainActor
private func makePrivatePasteboard() -> NSPasteboard {
    let board = NSPasteboard.withUniqueName()
    let name = board.name.rawValue
    let previous = pasteboardFixtureLatestGeneration[name]
    let generation = pasteboardFixtureGenerations.count
    pasteboardFixtureGenerations.append(PasteboardFixtureGeneration(name: name))
    pasteboardFixtureLatestGeneration[name] = generation
    print("PasteboardFixture namespace generation \(generation), name \(name), previous generation \(previous.map { String($0) } ?? "none"), previous release requested \(previous.map { String(pasteboardFixtureGenerations[$0].releaseRequested) } ?? "n/a")")
    return board
}

@MainActor
private func releasePrivatePasteboard(_ board: NSPasteboard) {
    let name = board.name.rawValue
    guard let generation = pasteboardFixtureLatestGeneration[name] else {
        XCTFail("Private board release must have a recorded namespace generation")
        return
    }
    // The test body can finish before its retained C publishers are retired in tearDown.
    // Keep the server resource available for C-reference cleanup, including promise retirement.
    pasteboardFixtureGenerations[generation].releaseQueued = true
    print("PasteboardFixture global release queued generation \(generation), name \(name)")
}

@MainActor
private func pasteboardFixtureItemOrigin(_ item: PasteboardItemID?, requestedName: String) -> String {
    guard let item else { return "nil item" }
    let id = UInt(bitPattern: item)
    guard let generation = pasteboardFixtureItemGeneration[id] else {
        return "item \(String(id, radix: 16)) has no fixture allocation record"
    }
    let origin = pasteboardFixtureGenerations[generation]
    return "item \(String(id, radix: 16)) allocated in generation \(generation), name \(origin.name), release requested \(origin.releaseRequested), same requested name \(origin.name == requestedName), current requested generation \(pasteboardFixtureLatestGeneration[requestedName].map { String($0) } ?? "none")"
}

private func pasteboardFixtureReferenceID(_ reference: Pasteboard) -> String {
    String(UInt(bitPattern: Unmanaged.passUnretained(reference).toOpaque()), radix: 16)
}

@MainActor
private func privatePasteboardReference(_ board: NSPasteboard) throws -> Pasteboard {
    guard board.name != .general else { throw PasteboardFixtureError.systemPasteboard }
    var reference: Pasteboard?
    let status = PasteboardCreate(board.name.rawValue as CFString, &reference)
    print("PasteboardFixture create name \(board.name.rawValue), reference \(reference.map(pasteboardFixtureReferenceID) ?? "nil"), status \(status), main \(Thread.isMainThread)")
    guard status == noErr else { throw PasteboardFixtureError.status(status) }
    guard let reference else { throw PasteboardFixtureError.missingReference }
    return reference
}

private func checkPasteboardFixture(_ status: OSStatus) throws {
    guard status == noErr else { throw PasteboardFixtureError.status(status) }
}

@MainActor
private func diagnosePasteboardPublication(_ reference: Pasteboard, name: String,
                                          expected: [(PasteboardItemID, [String])],
                                          publisher: Pasteboard? = nil,
                                          trace: (String) -> Void) {
    func flavors(_ source: Pasteboard, item: PasteboardItemID, types: [String], label: String) {
        var array: CFArray?
        let status = PasteboardCopyItemFlavors(source, item, &array)
        trace("\(label) item \(String(UInt(bitPattern: item), radix: 16)), flavors status \(status), types \((array as? [String]).map { $0.joined(separator: ",") } ?? "nil")")
        for type in types {
            var flags = PasteboardFlavorFlags(rawValue: 0)
            let flagStatus = PasteboardGetItemFlavorFlags(source, item, type as CFString, &flags)
            trace("\(label) flavor \(type), flags status \(flagStatus), value \(flags.rawValue)")
        }
    }
    @MainActor
    func snapshot(_ source: Pasteboard, label: String, enumerate: Bool) {
        var copiedName: CFString?
        let nameStatus = PasteboardCopyName(source, &copiedName)
        let actualName = copiedName.map { $0 as String }
        trace("\(label) reference \(pasteboardFixtureReferenceID(source)), requested name \(name), actual name \(actualName ?? "nil"), name status \(nameStatus), main \(Thread.isMainThread)")
        guard nameStatus == noErr, actualName == name else {
            trace("\(label) metadata skipped: private resource identity is unproven")
            return
        }
        if !enumerate {
            // Check published IDs before any count query can affect enumeration state.
            for (item, types) in expected {
                flavors(source, item: item, types: types, label: "\(label) published-ID lookup")
            }
        }
        var count = 0
        let countStatus = PasteboardGetItemCount(source, &count)
        trace("\(label) count status \(countStatus), actual \(count), expected \(expected.count)")
        if enumerate {
            guard countStatus == noErr, count > 0 else { return }
            let limit = min(count, expected.count + 1)
            trace("\(label) enumerating \(limit) of \(count) items")
            for index in 1...limit {
                var item: PasteboardItemID?
                let status = PasteboardGetItemIdentifier(source, index, &item)
                trace("\(label) index \(index), identifier status \(status), value \(item.map { String(UInt(bitPattern: $0), radix: 16) } ?? "nil")")
                trace("\(label) origin: \(pasteboardFixtureItemOrigin(item, requestedName: name))")
                if status == noErr, let item {
                    flavors(source, item: item, types: [], label: label)
                }
            }
        }
    }

    // Run only after the original lookup has failed. These observations never replace its
    // result, resynchronize the publisher, or request promised data.
    snapshot(reference, label: "failed reference", enumerate: false)
    if let publisher {
        if pasteboardFixtureReferenceID(publisher) == pasteboardFixtureReferenceID(reference) {
            trace("publisher aliases failed reference")
        } else {
            snapshot(publisher, label: "actual publisher", enumerate: false)
        }
    }
    var fresh: Pasteboard?
    let createStatus = PasteboardCreate(name as CFString, &fresh)
    trace("independent reference create status \(createStatus), reference \(fresh.map(pasteboardFixtureReferenceID) ?? "nil")")
    if createStatus == noErr, let fresh {
        defer { withExtendedLifetime(fresh) {} }
        guard pasteboardFixtureReferenceID(fresh) != pasteboardFixtureReferenceID(reference),
              publisher.map(pasteboardFixtureReferenceID) != pasteboardFixtureReferenceID(fresh) else {
            trace("independent reference aliases an existing reference; skipping sync and enumeration")
            return
        }
        let flags = PasteboardSynchronize(fresh)
        trace("independent reference sync \(flags.rawValue)")
        snapshot(fresh, label: "independent reference", enumerate: true)
    }
}

@MainActor
private var nextPasteboardFixtureItemID: UInt = 1

@MainActor
private func newPasteboardFixtureItem(_ board: NSPasteboard) throws -> PasteboardItemID {
    // Process-unique opaque IDs identify earlier publications when native enumeration is stale.
    // The values are not dereferenced or reset for each private board.
    guard let generation = pasteboardFixtureLatestGeneration[board.name.rawValue] else {
        throw PasteboardFixtureError.untrackedBoard
    }
    guard nextPasteboardFixtureItemID <= UInt(Int32.max),
          let item = PasteboardItemID(bitPattern: nextPasteboardFixtureItemID) else {
        throw PasteboardFixtureError.identifierExhausted
    }
    pasteboardFixtureItemGeneration[nextPasteboardFixtureItemID] = generation
    nextPasteboardFixtureItemID += 1
    return item
}

private final class PastePromiseState {
    enum Response {
        case unavailable, text(Data), replaceOwner
        case blockedText(Data, entered: XCTestExpectation, release: DispatchSemaphore)
    }
    private let lock = NSLock()
    private var requested = 0
    let response: Response
    private let expectedItem: PasteboardItemID
    private let replacementItem: PasteboardItemID?
    private let trace: (@Sendable (String) -> Void)?
    init(_ response: Response, item: PasteboardItemID, replacementItem: PasteboardItemID?,
         trace: (@Sendable (String) -> Void)? = nil) {
        self.response = response
        expectedItem = item
        self.replacementItem = replacementItem
        self.trace = trace
    }
    func provide(_ board: Pasteboard, item: PasteboardItemID, flavor: CFString) -> OSStatus {
        lock.lock()
        requested += 1
        let call = requested
        lock.unlock()
        XCTAssertEqual(item, expectedItem, "A promise must be delivered for its published opaque item ID")
        trace?("keeper call \(call), item \(String(UInt(bitPattern: item), radix: 16)), flavor \(flavor), main \(Thread.isMainThread)")
        switch response {
        case .unavailable:
            trace?("keeper unavailable status \(badPasteboardFlavorErr)")
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
            guard let replacementItem else {
                XCTFail("Replacement ownership needs a fresh process-unique item ID")
                return OSStatus(badPasteboardItemErr)
            }
            let clear = PasteboardClear(board)
            guard clear == noErr else { return clear }
            _ = PasteboardSynchronize(board)
            let put = PasteboardPutItemFlavor(board, replacementItem, "public.utf8-plain-text" as CFString,
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
         response: PastePromiseState.Response = .unavailable, fileURL: String? = nil,
         trace: (@Sendable (String) -> Void)? = nil) throws {
        let trace: @Sendable (String) -> Void = trace ?? { print("PasteboardFixture promise: \($0)") }
        let reference = try privatePasteboardReference(board)
        self.reference = reference
        let item = try newPasteboardFixtureItem(board)
        var expected = [(item, [promisedType.rawValue])]
        let replacementItem: PasteboardItemID?
        if case .replaceOwner = response { replacementItem = try newPasteboardFixtureItem(board) }
        else { replacementItem = nil }
        state = PastePromiseState(response, item: item, replacementItem: replacementItem, trace: trace)
        let clearStatus = PasteboardClear(reference)
        trace("clear name \(board.name.rawValue), reference \(pasteboardFixtureReferenceID(reference)), status \(clearStatus)")
        try checkPasteboardFixture(clearStatus)
        let clearedFlags = PasteboardSynchronize(reference)
        trace("cleared sync \(clearedFlags.rawValue)")
        if let plainText {
            if promisedType.rawValue != "public.utf8-plain-text" {
                expected[0].1.append("public.utf8-plain-text")
            }
            try checkPasteboardFixture(PasteboardPutItemFlavor(reference, item, "public.utf8-plain-text" as CFString,
                                                              Data(plainText.utf8) as CFData, PasteboardFlavorFlags(rawValue: 0)))
        }
        try checkPasteboardFixture(PasteboardSetPromiseKeeper(reference, { board, item, flavor, context in
            guard let context else { return OSStatus(noPasteboardPromiseKeeperErr) }
            return Unmanaged<PastePromiseState>.fromOpaque(context).takeUnretainedValue().provide(board, item: item, flavor: flavor)
        }, Unmanaged.passUnretained(state).toOpaque()))
        let promiseStatus = PasteboardPutItemFlavor(reference, item, promisedType.rawValue as CFString,
                                                   nil, PasteboardFlavorFlags(rawValue: 0))
        trace("put promise item \(String(UInt(bitPattern: item), radix: 16)), flavor \(promisedType.rawValue), status \(promiseStatus)")
        try checkPasteboardFixture(promiseStatus)
        var promisedFlags = PasteboardFlavorFlags(rawValue: 0)
        try checkPasteboardFixture(PasteboardGetItemFlavorFlags(reference, item,
            promisedType.rawValue as CFString, &promisedFlags))
        XCTAssertNotEqual(promisedFlags.rawValue & (1 << 9), 0, "Fixture must publish an actual unfulfilled promise")
        trace("published promise item \(String(UInt(bitPattern: item), radix: 16)), flags \(promisedFlags.rawValue)")
        if let fileURL {
            let file = try newPasteboardFixtureItem(board)
            expected.append((file, ["public.file-url"]))
            let fileStatus = PasteboardPutItemFlavor(reference, file, "public.file-url" as CFString,
                                                     Data(fileURL.utf8) as CFData, PasteboardFlavorFlags(rawValue: 0))
            trace("put file item \(String(UInt(bitPattern: file), radix: 16)), status \(fileStatus)")
            try checkPasteboardFixture(fileStatus)
        }
        let publishedFlags = PasteboardSynchronize(reference)
        trace("published sync \(publishedFlags.rawValue), keeper calls \(state.calls)")
        // Preserve the original first identifier -> flavor lookup, without a preparatory
        // count/name query that could hide a stale enumeration cache.
        var returnedItem: PasteboardItemID?
        let identifierStatus = PasteboardGetItemIdentifier(reference, 1, &returnedItem)
        trace("first identifier status \(identifierStatus), returned \(returnedItem.map { String(UInt(bitPattern: $0), radix: 16) } ?? "nil"), expected \(String(UInt(bitPattern: item), radix: 16))")
        trace("first identifier origin: \(pasteboardFixtureItemOrigin(returnedItem, requestedName: board.name.rawValue))")
        if identifierStatus != noErr || returnedItem == nil {
            diagnosePasteboardPublication(reference, name: board.name.rawValue, expected: expected, trace: trace)
            trace("after diagnostics keeper calls \(state.calls)")
            XCTAssertEqual(state.calls, 0, "Metadata diagnostics must not fulfill promises")
        }
        try checkPasteboardFixture(identifierStatus)
        XCTAssertEqual(returnedItem, item, "Promise publication must retain its opaque item ID")
        var flavors: CFArray?
        let flavorStatus = PasteboardCopyItemFlavors(reference, try XCTUnwrap(returnedItem), &flavors)
        trace("first returned-ID flavors status \(flavorStatus), types \((flavors as? [String]).map { $0.joined(separator: ",") } ?? "nil")")
        if returnedItem != item || flavorStatus != noErr ||
            (flavors as? [String])?.contains(promisedType.rawValue) != true {
            diagnosePasteboardPublication(reference, name: board.name.rawValue, expected: expected, trace: trace)
            trace("after diagnostics keeper calls \(state.calls)")
        }
        XCTAssertEqual(state.calls, 0, "Publication and metadata diagnostics must not fulfill promises")
        try checkPasteboardFixture(flavorStatus)
        XCTAssertTrue(try XCTUnwrap(flavors as? [String]).contains(promisedType.rawValue))
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
        var count = 0
        try checkPasteboardFixture(PasteboardGetItemCount(reference, &count))
        return count
    }
}

final class PlainTextPasteTests: XCTestCase {
    @MainActor
    private var publishedReferences: [Pasteboard] = []
    @MainActor
    private var privateBoards: [NSPasteboard] = []

    override func tearDown() async throws {
        await retirePrivatePasteboardResources()
        try await super.tearDown()
    }

    @MainActor
    private func newPrivatePasteboard() -> NSPasteboard {
        let board = makePrivatePasteboard()
        privateBoards.append(board)
        return board
    }

    @MainActor
    private func retirePrivatePasteboardResources() {
        // CFRelease can resolve promises against the server resource. Retire local C users
        // (and their autoreleased objects) before AppKit releases that resource globally.
        autoreleasepool { releasePublishedReferences() }
        XCTAssertTrue(publishedReferences.isEmpty)
        for board in privateBoards {
            let name = board.name.rawValue
            if let generation = pasteboardFixtureLatestGeneration[name] {
                XCTAssertTrue(pasteboardFixtureGenerations[generation].releaseQueued,
                              "A fixture scope must finish before its server resource is released")
                XCTAssertFalse(pasteboardFixtureGenerations[generation].releaseRequested)
                // releaseGlobally is oneway: record the request, not completed destruction.
                pasteboardFixtureGenerations[generation].releaseRequested = true
                print("PasteboardFixture global release requested after C retirement generation \(generation), name \(name)")
            } else {
                XCTFail("Private board teardown must have a recorded namespace generation")
            }
            board.releaseGlobally()
        }
        privateBoards.removeAll()
    }

    @MainActor
    private func releasePublishedReferences() {
        if !publishedReferences.isEmpty {
            print("PasteboardFixture releasing eager references \(publishedReferences.map(pasteboardFixtureReferenceID)), main \(Thread.isMainThread)")
        }
        publishedReferences.removeAll()
    }

    @MainActor
    @discardableResult
    private func publish(_ board: NSPasteboard, items: [NSPasteboardItem]) throws -> [PasteboardItemID] {
        let representations = try items.map { item in
            try item.types.map { type in
                guard let data = item.data(forType: type) else { throw PasteboardFixtureError.missingItemData }
                return (type.rawValue, data)
            }
        }
        return try publish(board, representations: representations)
    }

    @MainActor
    @discardableResult
    private func publish(_ board: NSPasteboard, representations: [[(String, Data)]]) throws -> [PasteboardItemID] {
        let reference = try privatePasteboardReference(board)
        // A private global pasteboard can disappear when its final C reference is released.
        publishedReferences.append(reference)
        let clearStatus = PasteboardClear(reference)
        print("PasteboardFixture eager clear name \(board.name.rawValue), reference \(pasteboardFixtureReferenceID(reference)), status \(clearStatus)")
        try checkPasteboardFixture(clearStatus)
        let clearedFlags = PasteboardSynchronize(reference)
        print("PasteboardFixture eager cleared sync \(clearedFlags.rawValue)")
        var identifiers: [PasteboardItemID] = []
        for flavors in representations {
            let identifier = try newPasteboardFixtureItem(board)
            identifiers.append(identifier)
            for (type, data) in flavors {
                let status = PasteboardPutItemFlavor(reference, identifier, type as CFString,
                                                    data as CFData, PasteboardFlavorFlags(rawValue: 0))
                print("PasteboardFixture eager put reference \(pasteboardFixtureReferenceID(reference)), item \(String(UInt(bitPattern: identifier), radix: 16)), flavor \(type), bytes \(data.count), status \(status)")
                try checkPasteboardFixture(status)
            }
        }
        let publishedFlags = PasteboardSynchronize(reference)
        print("PasteboardFixture eager published sync \(publishedFlags.rawValue)")
        try verifyPublication(board, publisher: reference, identifiers: identifiers, representations: representations)
        return identifiers
    }

    @MainActor
    private func verifyPublication(_ board: NSPasteboard, publisher: Pasteboard, identifiers: [PasteboardItemID],
                                   representations: [[(String, Data)]]) throws {
        let reader = try privatePasteboardReference(board)
        defer {
            print("PasteboardFixture finished eager reader reference \(pasteboardFixtureReferenceID(reader))")
            withExtendedLifetime(reader) {}
        }
        let published = zip(identifiers, representations).map { pair in
            (pair.0, pair.1.map { $0.0 })
        }
        var diagnosed = false
        let diagnoseOnce: @MainActor () -> Void = {
            guard !diagnosed else { return }
            diagnosed = true
            diagnosePasteboardPublication(reader, name: board.name.rawValue, expected: published,
                                          publisher: publisher,
                                          trace: { print("PasteboardFixture eager coherence: \($0)") })
        }
        let synchronized = PasteboardSynchronize(reader)
        var count = 0
        let countStatus = PasteboardGetItemCount(reader, &count)
        print("PasteboardFixture eager reader reference \(pasteboardFixtureReferenceID(reader)), sync \(synchronized.rawValue), count status \(countStatus), actual \(count), expected \(identifiers.count)")
        if countStatus != noErr || count != identifiers.count { diagnoseOnce() }
        try checkPasteboardFixture(countStatus)
        XCTAssertEqual(count, identifiers.count, "Fresh C reference must enumerate every published item")
        for (index, expected) in identifiers.enumerated() {
            var returned: PasteboardItemID?
            let status = PasteboardGetItemIdentifier(reader, index + 1, &returned)
            let expectedID = String(UInt(bitPattern: expected), radix: 16)
            print("PasteboardFixture publication index \(index + 1): sync \(synchronized.rawValue), published \(expectedID), returned \(returned.map { String(UInt(bitPattern: $0), radix: 16) } ?? "nil"), status \(status)")
            print("PasteboardFixture publication origin: \(pasteboardFixtureItemOrigin(returned, requestedName: board.name.rawValue))")
            if status != noErr || returned == nil { diagnoseOnce() }
            try checkPasteboardFixture(status)
            XCTAssertEqual(returned, expected, "A fresh C reference must return the published opaque ID")
            let item = try XCTUnwrap(returned)
            var flavorArray: CFArray?
            let flavorStatus = PasteboardCopyItemFlavors(reader, item, &flavorArray)
            print("PasteboardFixture publication item \(expectedID): flavors status \(flavorStatus)")
            if returned != expected || flavorStatus != noErr || (flavorArray as? [String]) == nil { diagnoseOnce() }
            try checkPasteboardFixture(flavorStatus)
            let flavors = try XCTUnwrap(flavorArray as? [String])
            for (type, expectedData) in representations[index] {
                if !flavors.contains(type) { diagnoseOnce() }
                XCTAssertTrue(flavors.contains(type), "Published flavor must remain discoverable: \(type)")
                // An empty stored representation may be unavailable on macOS; all nonempty
                // eager data must round-trip byte-for-byte without invoking any provider.
                if !expectedData.isEmpty {
                    var flags = PasteboardFlavorFlags(rawValue: 0)
                    let flagStatus = PasteboardGetItemFlavorFlags(reader, item, type as CFString, &flags)
                    if flagStatus != noErr || flags.rawValue & (1 << 9) != 0 { diagnoseOnce() }
                    try checkPasteboardFixture(flagStatus)
                    XCTAssertEqual(flags.rawValue & (1 << 9), 0, "Eager fixture data must not become a promise")
                    guard flags.rawValue & (1 << 9) == 0 else { throw PasteboardFixtureError.missingItemData }
                    var actual: CFData?
                    let copyStatus = PasteboardCopyItemFlavorData(reader, item, type as CFString, &actual)
                    print("PasteboardFixture publication item \(expectedID), \(type): copy status \(copyStatus), expected bytes \(expectedData.count), actual bytes \(actual.map { CFDataGetLength($0) } ?? -1)")
                    if copyStatus != noErr || actual.map({ $0 as Data }) != expectedData { diagnoseOnce() }
                    XCTAssertEqual(copyStatus, noErr)
                    XCTAssertEqual(actual.map { $0 as Data }, expectedData)
                }
            }
        }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let predecessorGeneration: Int
        do {
            let predecessor = newPrivatePasteboard()
            defer { releasePrivatePasteboard(predecessor) }
            predecessorGeneration = try XCTUnwrap(pasteboardFixtureLatestGeneration[predecessor.name.rawValue])
            let bytes = Data("<b>synthetic predecessor</b>".utf8)
            try publish(predecessor, representations: [[("public.html", bytes)]])
            let count = predecessor.changeCount
            let token = PlainTextPasteCancellation()
            let adapter = SystemPlainTextPasteClipboard(name: predecessor.name)
            guard case .failure(.unsupportedRepresentation) = await adapter.read(cancellation: token) else {
                return XCTFail("The predecessor must remain an unsupported HTML-only resource")
            }
            XCTAssertEqual(predecessor.changeCount, count)
            XCTAssertEqual(predecessor.data(forType: .html), bytes)
            XCTAssertEqual(token.outcome(.unsupportedRepresentation).clipboard, .unchanged)
        }
        XCTAssertEqual(publishedReferences.count, 1)
        XCTAssertEqual(privateBoards.count, 1)
        XCTAssertTrue(pasteboardFixtureGenerations[predecessorGeneration].releaseQueued)
        XCTAssertFalse(pasteboardFixtureGenerations[predecessorGeneration].releaseRequested,
                       "Global release must not run while a C publisher still owns the resource")
        retirePrivatePasteboardResources()
        XCTAssertTrue(publishedReferences.isEmpty)
        XCTAssertTrue(privateBoards.isEmpty, "Finished resources must not be retained across fixtures")
        XCTAssertTrue(pasteboardFixtureGenerations[predecessorGeneration].releaseRequested)

        // Reopen after real retirement; existing publication checks still require exact
        // IDs, flavors and bytes even when the native allocator reuses a reference address.
        let types: [NSPasteboard.PasteboardType] = [.png, .fileURL, NSPasteboard.PasteboardType("com.apple.pasteboard.promised-file-url")]
        for type in types {
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
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
        let legacyFiles: [(String, Data)] = [
            ("NSFilenamesPboardType", try PropertyListSerialization.data(
                fromPropertyList: ["/synthetic/never-opened.txt"], format: .xml, options: 0)),
            ("NSFileContentsPboardType", Data("synthetic file contents".utf8)),
            ("NSFilesPromisePboardType", try PropertyListSerialization.data(
                fromPropertyList: ["txt"], format: .xml, options: 0))
        ]
        for (rawType, data) in legacyFiles {
            let tagClass = UTTagClass(rawValue: kUTTagClassNSPboardType as String)
            let uniformType = try XCTUnwrap(UTType(tag: rawType, tagClass: tagClass, conformingTo: nil))
            XCTAssertTrue(uniformType.tags[tagClass]?.contains(rawType) == true,
                          "The public UTI tag specification must preserve the legacy file flavor")
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
            let type = NSPasteboard.PasteboardType(rawType)
            board.declareTypes([type, .string], owner: nil)
            XCTAssertTrue(board.setData(data, forType: type))
            XCTAssertTrue(board.setString("keep text with its file", forType: .string))
            let count = board.changeCount
            let adapter = SystemPlainTextPasteClipboard(name: board.name,
                trace: { print("PasteboardTrace legacy \(rawType): \($0)") })
            let token = PlainTextPasteCancellation()
            switch await adapter.read(cancellation: token) {
            case .failure(let reason): XCTAssertEqual(reason, .noText, rawType)
            case .text: XCTFail("A C-only item inspection must still reject legacy AppKit file flavors: \(rawType)")
            }
            XCTAssertEqual(board.changeCount, count, rawType)
            XCTAssertEqual(board.data(forType: type), data, rawType)
            XCTAssertEqual(board.string(forType: .string), "keep text with its file", rawType)
            XCTAssertEqual(token.outcome(.noText).clipboard, .unchanged, rawType)
        }
    }

    @MainActor
    func testPrivateHTMLOnlyIsNotRenderedOrConvertedViaNetworkCapableImporter() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        var previousIdentifier: UInt = 0
        for (index, data) in cases.enumerated() {
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setData(data, forType: .rtf))
            let identifiers = try publish(board, items: [item])
            let identifier = UInt(bitPattern: try XCTUnwrap(identifiers.first))
            XCTAssertGreaterThan(identifier, previousIdentifier, "Fixture IDs must not reset on a new private board")
            previousIdentifier = identifier
            print("Synthetic malformed RTF case \(index): published item \(String(identifier, radix: 16))")
            let count = board.changeCount
            let adapter = SystemPlainTextPasteClipboard(name: board.name) {
                print("Synthetic malformed RTF case \(index): \($0)")
            }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let trace: @Sendable (String) -> Void = { print("PasteboardTrace unavailable promise: \($0)") }
        let provider = try PastePromiseFixture(board, trace: trace)
        defer { withExtendedLifetime(provider) {} }
        let count = board.changeCount
        XCTAssertEqual(provider.calls, 0, "Publication and metadata must not fulfill a promise")
        let adapter = SystemPlainTextPasteClipboard(name: board.name, trace: trace)
        let token = PlainTextPasteCancellation()
        switch await adapter.read(cancellation: token) {
        case .failure(let reason):
            XCTAssertEqual(reason, .unavailableData)
        case .text:
            XCTFail("Missing promised data must not become an empty successful paste")
        }
        XCTAssertGreaterThan(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(token.outcome(.unavailableData).clipboard, .unchanged)
    }

    @MainActor
    func testPrivateRichHTMLProviderIsNeverAskedWhenPlainTextIsAvailable() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        for (index, testCase) in cases.enumerated() {
            let (type, encoding, text) = testCase
            let label = "case \(index), \(type), encoding \(encoding.rawValue)"
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
            let bytes = try XCTUnwrap(text.data(using: encoding))
            // NSPasteboardItem may translate legacy aliases, including their line endings.
            // Publish the intended external bytes directly rather than pre-converting the fixture.
            let published = try publish(board, representations: [[(type, bytes)]])
            let count = board.changeCount
            let sourceReference = try privatePasteboardReference(board)
            defer { withExtendedLifetime(sourceReference) {} }
            let flags = PasteboardSynchronize(sourceReference)
            var itemCount = 0
            let countStatus = PasteboardGetItemCount(sourceReference, &itemCount)
            print("PasteboardFixture \(label): sync \(flags.rawValue), count \(itemCount), status \(countStatus), change count \(count)")
            try checkPasteboardFixture(countStatus)
            XCTAssertEqual(itemCount, 1, label)
            var firstItem: PasteboardItemID?
            let identifierStatus = PasteboardGetItemIdentifier(sourceReference, 1, &firstItem)
            print("PasteboardFixture \(label): published \(published.map { String(UInt(bitPattern: $0), radix: 16) }), returned \(firstItem.map { String(UInt(bitPattern: $0), radix: 16) } ?? "nil"), status \(identifierStatus)")
            try checkPasteboardFixture(identifierStatus)
            let identifier = try XCTUnwrap(firstItem)
            XCTAssertEqual(identifier, try XCTUnwrap(published.first), label)
            var flavorArray: CFArray?
            let flavorsStatus = PasteboardCopyItemFlavors(sourceReference, identifier, &flavorArray)
            print("PasteboardFixture \(label): flavors status \(flavorsStatus), count \(flavorArray.map { CFArrayGetCount($0) } ?? -1)")
            try checkPasteboardFixture(flavorsStatus)
            XCTAssertTrue(try XCTUnwrap(flavorArray as? [String]).contains(type), label)
            var fixtureBytes: CFData?
            let fixtureStatus = PasteboardCopyItemFlavorData(sourceReference, identifier,
                                                             type as CFString, &fixtureBytes)
            print("PasteboardFixture \(label): input bytes \(bytes.count), copied bytes \(fixtureBytes.map { CFDataGetLength($0) } ?? -1), status \(fixtureStatus)")
            XCTAssertEqual(fixtureStatus, noErr, label)
            XCTAssertEqual(fixtureBytes.map { $0 as Data }, bytes, label)
            XCTAssertEqual(board.changeCount, count, label)
            let adapter = SystemPlainTextPasteClipboard(name: board.name,
                trace: { print("PasteboardTrace \(label): \($0)") })
            let token = PlainTextPasteCancellation()
            let read = await adapter.read(cancellation: token)
            guard case .text(let snapshot) = read else {
                if case .failure(let reason) = read {
                    XCTFail("Expected lossless platform decoding for \(label), reason \(reason), change count \(count)/\(board.changeCount)")
                }
                continue
            }
            XCTAssertEqual(board.changeCount, count, label)
            XCTAssertEqual(token.outcome(.unavailableData).clipboard, .unchanged, label)
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
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
