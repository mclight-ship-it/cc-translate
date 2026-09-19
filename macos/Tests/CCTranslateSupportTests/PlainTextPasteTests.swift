import AppKit
import ApplicationServices
import Combine
import CoreServices
import UniformTypeIdentifiers
import XCTest
import Darwin
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
    case publicationTimeout
}

private struct PasteboardFixtureGeneration {
    let name: String
    var releaseQueued = false
    var releaseRequested = false
}

// Metadata only: retaining boards or C references here would change the lifecycle under test.
@MainActor private var pasteboardFixtureGenerations: [PasteboardFixtureGeneration] = []
@MainActor private var pasteboardFixtureLatestGeneration: [String: Int] = [:]

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

private let pasteboardFixtureIdentityType = NSPasteboard.PasteboardType("org.cctranslate.tests.item-identity")

private func pasteboardFixtureIdentity(_ item: PasteboardItemID) -> Data {
    Data(String(UInt(bitPattern: item), radix: 16).utf8)
}

private func publishPasteboardFixtureIdentity(_ reference: Pasteboard, item: PasteboardItemID) throws {
    try checkPasteboardFixture(PasteboardPutItemFlavor(reference, item, pasteboardFixtureIdentityType.rawValue as CFString,
                                                      pasteboardFixtureIdentity(item) as CFData, PasteboardFlavorFlags(rawValue: 0)))
}

@MainActor
private func verifyKnownPasteboardItems(_ board: NSPasteboard, reference: Pasteboard,
                                       expected: [(PasteboardItemID, [String])]) throws {
    let changeCount = board.changeCount
    var copiedName: CFString?
    try checkPasteboardFixture(PasteboardCopyName(reference, &copiedName))
    XCTAssertEqual(copiedName.map { $0 as String }, board.name.rawValue)
    var count = 0
    try checkPasteboardFixture(PasteboardGetItemCount(reference, &count))
    XCTAssertEqual(count, expected.count)
    for (id, types) in expected {
        var array: CFArray?
        try checkPasteboardFixture(PasteboardCopyItemFlavors(reference, id, &array))
        let cTypes = try XCTUnwrap(array as? [String])
        for type in types + [pasteboardFixtureIdentityType.rawValue] {
            XCTAssertTrue(cTypes.contains(type), "Known published C ID must expose \(type)")
        }
        var flags = PasteboardFlavorFlags(rawValue: 0)
        try checkPasteboardFixture(PasteboardGetItemFlavorFlags(reference, id,
            pasteboardFixtureIdentityType.rawValue as CFString, &flags))
        XCTAssertEqual(flags.rawValue & (1 << 9), 0, "Fixture identity must be eager")
        guard flags.rawValue & (1 << 9) == 0 else { throw PasteboardFixtureError.missingItemData }
        var identity: CFData?
        try checkPasteboardFixture(PasteboardCopyItemFlavorData(reference, id,
            pasteboardFixtureIdentityType.rawValue as CFString, &identity))
        XCTAssertEqual(identity.map { $0 as Data }, pasteboardFixtureIdentity(id))
    }
    XCTAssertEqual(board.changeCount, changeCount)
}

@MainActor
private func verifyPublishedPasteboardItems(_ board: NSPasteboard, reference: Pasteboard,
                                           expected: [(PasteboardItemID, [String])]) throws -> [NSPasteboardItem] {
    // Run after the product read: cross-check order against independently validated C IDs
    // without priming AppKit's reader. Only the eager identity is fetched, never promised data.
    try verifyKnownPasteboardItems(board, reference: reference, expected: expected)
    let changeCount = board.changeCount
    let items = try XCTUnwrap(board.pasteboardItems)
    XCTAssertEqual(items.count, expected.count)
    guard items.count == expected.count else { throw PasteboardFixtureError.missingItemData }
    for (index, entry) in expected.enumerated() {
        let (id, types) = entry
        let item = items[index]
        XCTAssertEqual(board.index(of: item), index)
        for type in types + [pasteboardFixtureIdentityType.rawValue] {
            XCTAssertTrue(item.types.contains(NSPasteboard.PasteboardType(type)), "AppKit item must expose \(type)")
        }
        XCTAssertEqual(item.data(forType: pasteboardFixtureIdentityType), pasteboardFixtureIdentity(id),
                       "AppKit item order must match the byte-exact published identity")
    }
    XCTAssertEqual(board.changeCount, changeCount)
    return items
}

@MainActor
private var nextPasteboardFixtureItemID: UInt = 1

@MainActor
private func newPasteboardFixtureItem(_ board: NSPasteboard) throws -> PasteboardItemID {
    // Keep identity values unique across fixture lifetimes; never dereference or reuse them.
    guard pasteboardFixtureLatestGeneration[board.name.rawValue] != nil else {
        throw PasteboardFixtureError.untrackedBoard
    }
    guard nextPasteboardFixtureItemID <= UInt(Int32.max),
          let item = PasteboardItemID(bitPattern: nextPasteboardFixtureItemID) else {
        throw PasteboardFixtureError.identifierExhausted
    }
    nextPasteboardFixtureItemID += 1
    return item
}

enum PastePromiseState {
    enum Response {
        case unavailable, text(Data), replaceOwner
        case blockedText(Data, entered: XCTestExpectation, release: DispatchSemaphore)
    }
}

enum ClipboardTestExecutables {
    static func product(_ name: String) throws -> URL {
        if name == "CCTranslateMac", let app = ProcessInfo.processInfo.environment["CC_TRANSLATE_APP"] {
            let url = URL(fileURLWithPath: app).appendingPathComponent("Contents/MacOS/CCTranslateMac")
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                throw CocoaError(.fileNoSuchFile)
            }
            return url
        }
        // XCTest's bundle, not Bundle.main (which can be xctest), identifies the products directory.
        let directory = Bundle(for: PlainTextPasteTests.self).bundleURL.deletingLastPathComponent()
        let url = directory.appendingPathComponent(name)
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            throw CocoaError(.fileNoSuchFile)
        }
        return url
    }
}

private struct PasteProducerConfiguration: Encodable {
    let name: String
    let identity: Data
    let fileIdentity: Data?
    let promisedType: String
    let plainText: String?
    let response: String
    let text: Data?
    let fileURL: String?
}

private struct PasteProducerReceipt: Decodable {
    let event: String
    let pid: Int32
    let mainThread: Bool
    let calls: Int
    let itemMatches: Bool
    let boardMatches: Bool
    let typeMatches: Bool
}

// The delayed provider lives in another process. Both reader and provider can use
// their main threads without parking the XCTest/host UI on a synchronous promise.
final class PastePromiseFixture: @unchecked Sendable {
    private let board: NSPasteboard
    private let published: [(identity: Data, types: [NSPasteboard.PasteboardType])]
    private let lock = NSLock()
    private var process: ClipboardProcess?
    private var pending = Data()
    private var requested = 0
    private var receiptPID: Int32?
    private var buffered: [String: Int] = [:]
    private var waiters: [String: [CheckedContinuation<Void, Error>]] = [:]
    private var result: ClipboardProcessResult?
    private let entered: XCTestExpectation?

    @MainActor
    init(_ board: NSPasteboard, plainText: String? = nil, promisedType: NSPasteboard.PasteboardType = .string,
         response: PastePromiseState.Response = .unavailable, fileURL: String? = nil,
         trace: (@Sendable (String) -> Void)? = nil) async throws {
        let trace: @Sendable (String) -> Void = trace ?? { print("PasteboardFixture promise: \($0)") }
        self.board = board
        var types = [promisedType]
        if plainText != nil {
            XCTAssertNotEqual(promisedType, .string, "Eager text must not replace the promised representation")
            types.append(.string)
        }
        let identity = Data(UUID().uuidString.utf8)
        let fileIdentity = fileURL == nil ? nil : Data(UUID().uuidString.utf8)
        var expected = [(identity: identity, types: types)]
        if let fileIdentity { expected.append((identity: fileIdentity, types: [.fileURL])) }
        published = expected
        let mode: String
        let text: Data?
        var release: DispatchSemaphore?
        switch response {
        case .unavailable: mode = "unavailable"; text = nil; entered = nil
        case .text(let data): mode = "text"; text = data; entered = nil
        case .replaceOwner: mode = "replaceOwner"; text = nil; entered = nil
        case .blockedText(let data, let expectation, let gate):
            mode = "blocked"; text = data; entered = expectation; release = gate
        }
        let configuration = PasteProducerConfiguration(name: board.name.rawValue, identity: identity,
            fileIdentity: fileIdentity, promisedType: promisedType.rawValue, plainText: plainText,
            response: mode, text: text, fileURL: fileURL)
        var bytes = try JSONEncoder().encode(configuration)
        bytes.append(0x0A)
        let executable = try ClipboardTestExecutables.product("CCClipboardTestProducer")
        let child = ClipboardProcess(timeout: 30, trace: trace, receive: { [weak self] in
            try self?.receive($0)
        }, completion: { [weak self] in self?.completed($0) })
        process = child
        child.start(executable: executable, arguments: [], input: bytes, keepInputOpen: true)
        if let release {
            DispatchQueue.global().async {
                if release.wait(timeout: .now() + 15) == .success { child.send(Data("release\n".utf8)) }
                else { child.stop() }
            }
        }
        try await next("ready")
        XCTAssertEqual(calls, 0, "Publishing metadata must not fulfill promised data")
        XCTAssertNotEqual(pid, getpid(), "The blocked provider must not live in the host process")
    }

    var calls: Int { lock.lock(); defer { lock.unlock() }; return requested }
    var pid: Int32? { lock.lock(); defer { lock.unlock() }; return receiptPID }
    var publishedItemCount: Int { published.count }

    private func receive(_ data: Data) throws {
        pending.append(data)
        while let end = pending.firstIndex(of: 0x0A) {
            let receipt = try JSONDecoder().decode(PasteProducerReceipt.self, from: pending[..<end])
            pending.removeSubrange(...end)
            guard receipt.mainThread, receipt.pid != getpid(), receipt.pid > 1,
                  receipt.itemMatches, receipt.boardMatches, receipt.typeMatches else {
                throw ClipboardReadWireError.malformed
            }
            lock.lock()
            let matchingPID = receiptPID == nil || receiptPID == receipt.pid
            receiptPID = receipt.pid
            requested = receipt.calls
            lock.unlock()
            guard matchingPID else { throw ClipboardReadWireError.malformed }
            if receipt.event == "call" { entered?.fulfill() }
            deliver(receipt.event)
        }
        guard pending.count <= 8192 else { throw ClipboardReadWireError.malformed }
    }

    private func deliver(_ event: String) {
        lock.lock()
        let waiting = waiters[event]?.isEmpty == false ? waiters[event]?.removeFirst() : nil
        if waiting == nil { buffered[event, default: 0] += 1 }
        lock.unlock()
        waiting?.resume()
    }

    private func next(_ event: String) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            lock.lock()
            if buffered[event, default: 0] > 0 {
                buffered[event, default: 0] -= 1
                lock.unlock()
                continuation.resume()
            } else if result != nil {
                lock.unlock()
                continuation.resume(throwing: ClipboardReadWireError.malformed)
            } else {
                waiters[event, default: []].append(continuation)
                lock.unlock()
            }
        }
    }

    private func completed(_ result: ClipboardProcessResult) {
        lock.lock()
        self.result = result
        let all = waiters
        waiters.removeAll()
        lock.unlock()
        for (event, pending) in all {
            for waiter in pending {
                if event == "exit", result.exitCode == 0, result.failure == nil { waiter.resume() }
                else { waiter.resume(throwing: ClipboardReadWireError.malformed) }
            }
        }
        deliver("exit")
    }

    func close() async throws {
        process?.send(Data("shutdown\n".utf8))
        try await next("exit")
        let result = processResult
        XCTAssertEqual(result?.exitCode, 0)
        XCTAssertNil(result?.failure)
        XCTAssertEqual(result?.pid, pid)
        process = nil
    }

    private var processResult: ClipboardProcessResult? {
        lock.lock()
        defer { lock.unlock() }
        return result
    }

    func waitForFulfillment() async throws { try await next("fulfilled") }

    @MainActor
    func verifyIdentity(_ board: NSPasteboard) async throws {
        process?.send(Data("status\n".utf8))
        try await next("status")
        let before = calls
        let count = board.changeCount
        XCTAssertEqual(board.name, self.board.name)
        // Inspect reader items only after the product interaction; no setup cache priming.
        let items = try XCTUnwrap(board.pasteboardItems)
        XCTAssertEqual(items.count, published.count)
        for (index, pair) in zip(items, published).enumerated() {
            let (item, expected) = pair
            XCTAssertEqual(board.index(of: item), index)
            XCTAssertTrue(expected.types.allSatisfy { item.types.contains($0) })
            XCTAssertEqual(item.data(forType: pasteboardFixtureIdentityType), expected.identity)
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(calls, before, "AppKit metadata and identity reads must not request promised data")
    }

    @MainActor
    func itemCount() throws -> Int {
        try XCTUnwrap(board.pasteboardItems).count
    }
}

final class PlainTextPasteTests: XCTestCase {
    @MainActor
    private var workerExecutable: URL?
    @MainActor
    private var promiseFixtures: [PastePromiseFixture] = []
    @MainActor
    private var publishedReferences: [Pasteboard] = []
    @MainActor
    private var privateBoards: [NSPasteboard] = []
    @MainActor
    private var publishedItems: [NSPasteboard.Name: [(PasteboardItemID, [String])]] = [:]

    override func setUp() async throws {
        try await super.setUp()
        let executable = try ClipboardTestExecutables.product("CCTranslateMac")
        _ = try ClipboardTestExecutables.product("CCClipboardTestProducer")
        await MainActor.run { self.workerExecutable = executable }
    }

    override func tearDown() async throws {
        for fixture in await promiseFixtures {
            do { try await fixture.close() }
            catch { XCTFail("Synthetic producer did not exit and drain cleanly") }
        }
        await MainActor.run { self.promiseFixtures.removeAll() }
        await retirePrivatePasteboardResources()
        try await super.tearDown()
    }

    @MainActor
    private func clipboard(name: NSPasteboard.Name,
                           trace: (@Sendable (String) -> Void)? = nil) -> SystemPlainTextPasteClipboard {
        XCTAssertNotNil(workerExecutable, "The actual App executable must be built before tests")
        return SystemPlainTextPasteClipboard(name: name, readerExecutable: workerExecutable, trace: trace)
    }

    @MainActor
    private func promiseFixture(_ board: NSPasteboard, plainText: String? = nil,
                                promisedType: NSPasteboard.PasteboardType = .string,
                                response: PastePromiseState.Response = .unavailable, fileURL: String? = nil,
                                trace: (@Sendable (String) -> Void)? = nil) async throws -> PastePromiseFixture {
        let fixture = try await PastePromiseFixture(board, plainText: plainText, promisedType: promisedType,
                                                    response: response, fileURL: fileURL, trace: trace)
        promiseFixtures.append(fixture)
        return fixture
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
        publishedItems.removeAll()
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
    private func publish(_ board: NSPasteboard, items: [NSPasteboardItem]) async throws -> [PasteboardItemID] {
        let representations = try items.map { item in
            try item.types.map { type in
                guard let data = item.data(forType: type) else { throw PasteboardFixtureError.missingItemData }
                return (type.rawValue, data)
            }
        }
        return try await publish(board, representations: representations)
    }

    @MainActor
    @discardableResult
    private func publish(_ board: NSPasteboard, representations: [[(String, Data)]]) async throws -> [PasteboardItemID] {
        let previousChangeCount = board.changeCount
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
            try publishPasteboardFixtureIdentity(reference, item: identifier)
        }
        let publishedFlags = PasteboardSynchronize(reference)
        print("PasteboardFixture eager published sync \(publishedFlags.rawValue)")
        publishedItems[board.name] = zip(identifiers, representations).map { pair in
            (pair.0, pair.1.map { $0.0 })
        }
        // C publication can precede AppKit's change-count notification. Wait for metadata
        // readiness without reading representations or priming the product's AppKit reader.
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        while board.changeCount <= previousChangeCount {
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                throw PasteboardFixtureError.publicationTimeout
            }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        print("PasteboardFixture publication visible: change count \(previousChangeCount)/\(board.changeCount)")
        try verifyPublication(board, identifiers: identifiers, representations: representations)
        return identifiers
    }

    @MainActor
    private func verifyPublication(_ board: NSPasteboard, identifiers: [PasteboardItemID],
                                   representations: [[(String, Data)]]) throws {
        let reader = try privatePasteboardReference(board)
        defer {
            print("PasteboardFixture finished eager reader reference \(pasteboardFixtureReferenceID(reader))")
            withExtendedLifetime(reader) {}
        }
        let published = zip(identifiers, representations).map { pair in
            (pair.0, pair.1.map { $0.0 })
        }
        _ = PasteboardSynchronize(reader)
        try verifyKnownPasteboardItems(board, reference: reader, expected: published)
        let changeCount = board.changeCount
        for (index, id) in identifiers.enumerated() {
            for (type, expectedData) in representations[index] {
                // An empty stored representation may be unavailable on macOS; all nonempty
                // eager data must round-trip byte-for-byte without invoking any provider.
                if !expectedData.isEmpty {
                    var flags = PasteboardFlavorFlags(rawValue: 0)
                    let flagStatus = PasteboardGetItemFlavorFlags(reader, id, type as CFString, &flags)
                    try checkPasteboardFixture(flagStatus)
                    XCTAssertEqual(flags.rawValue & (1 << 9), 0, "Eager fixture data must not become a promise")
                    guard flags.rawValue & (1 << 9) == 0 else { throw PasteboardFixtureError.missingItemData }
                    var actual: CFData?
                    let copyStatus = PasteboardCopyItemFlavorData(reader, id, type as CFString, &actual)
                    XCTAssertEqual(copyStatus, noErr)
                    XCTAssertEqual(actual.map { $0 as Data }, expectedData)
                }
            }
        }
        XCTAssertEqual(board.changeCount, changeCount)
    }

    @MainActor
    private func verifyPublishedIdentity(_ board: NSPasteboard) throws {
        let reference = try privatePasteboardReference(board)
        defer { withExtendedLifetime(reference) {} }
        _ = PasteboardSynchronize(reference)
        _ = try verifyPublishedPasteboardItems(board, reference: reference,
                                              expected: XCTUnwrap(publishedItems[board.name]))
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
        let adapter = clipboard(name: board.name,
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
        let provider = try await promiseFixture(board, plainText: text, promisedType: .png)
        defer { withExtendedLifetime(provider) {} }
        let adapter = clipboard(name: board.name,
            trace: { print("PasteboardTrace alternativeImage: \($0)") })
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("An alternative image representation must not hide usable plain text")
        }
        try await provider.verifyIdentity(board)
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
        try await publish(board, items: [item])
        let adapter = clipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected RTF text") }
        try verifyPublishedIdentity(board)
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
        try await publish(board, items: items)
        let adapter = clipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else { return XCTFail("Expected all items") }
        try verifyPublishedIdentity(board)
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
            try await publish(predecessor, representations: [[("public.html", bytes)]])
            let count = predecessor.changeCount
            let token = PlainTextPasteCancellation()
            let adapter = clipboard(name: predecessor.name)
            guard case .failure(.unsupportedRepresentation) = await adapter.read(cancellation: token) else {
                return XCTFail("The predecessor must remain an unsupported HTML-only resource")
            }
            try verifyPublishedIdentity(predecessor)
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
            try await publish(board, items: [item])
            let count = board.changeCount
            let adapter = clipboard(name: board.name)
            guard case .failure(.noText) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
                return XCTFail("Non-text payload must not be destroyed")
            }
            try verifyPublishedIdentity(board)
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
            let adapter = clipboard(name: board.name,
                trace: { print("PasteboardTrace legacy \(rawType): \($0)") })
            let token = PlainTextPasteCancellation()
            switch await adapter.read(cancellation: token) {
            case .failure(let reason): XCTAssertEqual(reason, .noText, rawType)
            case .text: XCTFail("Item inspection must reject legacy AppKit file flavors: \(rawType)")
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
        try await publish(board, items: [item])
        let count = board.changeCount
        let adapter = clipboard(name: board.name)
        guard case .failure(.unsupportedRepresentation) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("HTML-only must not start an external-resource importer")
        }
        try verifyPublishedIdentity(board)
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
        let adapter = clipboard(name: board.name)
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
        try await publish(board, items: [item])
        let adapter = clipboard(name: board.name)
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
            let identifiers = try await publish(board, items: [item])
            let identifier = UInt(bitPattern: try XCTUnwrap(identifiers.first))
            XCTAssertGreaterThan(identifier, previousIdentifier, "Fixture IDs must not reset on a new private board")
            previousIdentifier = identifier
            print("Synthetic malformed RTF case \(index): published item \(String(identifier, radix: 16))")
            let count = board.changeCount
            XCTAssertGreaterThan(count, 0, "The published fixture must be visible before the product read")
            let adapter = clipboard(name: board.name) {
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
            try verifyPublishedIdentity(board)
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
        let provider = try await promiseFixture(board, trace: trace)
        defer { withExtendedLifetime(provider) {} }
        let count = board.changeCount
        XCTAssertEqual(provider.calls, 0, "Publication and metadata must not fulfill a promise")
        let adapter = clipboard(name: board.name, trace: trace)
        let token = PlainTextPasteCancellation()
        switch await adapter.read(cancellation: token) {
        case .failure(let reason):
            XCTAssertEqual(reason, .unavailableData)
        case .text:
            XCTFail("Missing promised data must not become an empty successful paste")
        }
        try await provider.verifyIdentity(board)
        XCTAssertGreaterThan(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(token.outcome(.unavailableData).clipboard, .unchanged)
    }

    @MainActor
    func testPrivateRichHTMLProviderIsNeverAskedWhenPlainTextIsAvailable() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let provider = try await promiseFixture(board, plainText: "plain wins", promisedType: .html)
        defer { withExtendedLifetime(provider) {} }
        let adapter = clipboard(name: board.name)
        guard case .text(let snapshot) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Expected plain representation")
        }
        XCTAssertEqual(snapshot.text, "plain wins")
        try await provider.verifyIdentity(board)
        XCTAssertEqual(provider.calls, 0)
    }

    @MainActor
    func testPrivateProviderChangingOwnerDuringReadCannotProduceStaleSnapshot() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let provider = try await promiseFixture(board, response: .replaceOwner)
        defer { withExtendedLifetime(provider) {} }
        let adapter = clipboard(name: board.name)
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
        try await publish(board, items: [item])
        let input = PasteInputDouble()
        let paste = service(clipboard(name: board.name), input: input,
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
        let adapter = clipboard(name: board.name)
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
        let adapter = clipboard(name: board.name)
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
            ("public.utf16-plain-text", .utf16, "\u{FEFF}literal after BOM\r\n\u{1F642}"),
            ("public.utf16-external-plain-text", .utf16, "\u{FEFF}external literal after BOM\r\n"),
            ("public.utf8-plain-text", .utf8, "\u{FEFF}literal UTF-8 prefix\r\n"),
            ("public.utf8-tab-separated-values-text", .utf8, " \u{4E2D}\tCafe\u{0301}\r\n "),
            ("com.apple.traditional-mac-plain-text", .macOSRoman, " Caf\u{00E9}\t42\r\n "),
            ("public.utf8-plain-text", .utf8, "\u{FEFF}\u{FEFF}prefix\u{0000}\u{FFFD}\r\n"),
            ("public.utf8-tab-separated-values-text", .utf8, "\u{FEFF}\u{4E2D}\tvalue\r\n"),
            ("public.utf8-plain-text", .utf8, "\u{FEFF}")
        ]
        for (index, testCase) in cases.enumerated() {
            let (type, encoding, text) = testCase
            let label = "case \(index), \(type), encoding \(encoding.rawValue)"
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
            let bytes = try XCTUnwrap(text.data(using: encoding))
            // NSPasteboardItem may translate legacy aliases, including their line endings.
            // Publish the intended external bytes directly rather than pre-converting the fixture.
            let published = try await publish(board, representations: [[(type, bytes)]])
            let count = board.changeCount
            let sourceReference = try privatePasteboardReference(board)
            defer { withExtendedLifetime(sourceReference) {} }
            let flags = PasteboardSynchronize(sourceReference)
            var itemCount = 0
            let countStatus = PasteboardGetItemCount(sourceReference, &itemCount)
            print("PasteboardFixture \(label): sync \(flags.rawValue), count \(itemCount), status \(countStatus), change count \(count)")
            try checkPasteboardFixture(countStatus)
            XCTAssertEqual(itemCount, 1, label)
            let identifier = try XCTUnwrap(published.first)
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
            let copiedSource = expectation(description: "Read the published source representation: \(label)")
            copiedSource.assertForOverFulfill = true
            let adapter = clipboard(name: board.name, trace: {
                print("PasteboardTrace \(label): \($0)")
                if $0.hasPrefix("AppKit copy \(type),") { copiedSource.fulfill() }
            })
            let token = PlainTextPasteCancellation()
            let read = await adapter.read(cancellation: token)
            await fulfillment(of: [copiedSource], timeout: 1)
            guard case .text(let snapshot) = read else {
                if case .failure(let reason) = read {
                    XCTFail("Expected lossless platform decoding for \(label), reason \(reason), change count \(count)/\(board.changeCount)")
                }
                continue
            }
            XCTAssertEqual(board.changeCount, count, label)
            XCTAssertEqual(token.outcome(.unavailableData).clipboard, .unchanged, label)
            XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8), label)
            let appKitItems = try verifyPublishedPasteboardItems(board, reference: sourceReference,
                                                                expected: [(identifier, [type])])
            XCTAssertEqual(appKitItems[0].data(forType: NSPasteboard.PasteboardType(type)), bytes, label)
            let write = await adapter.replace(snapshot, cancellation: token)
            guard case .written = write else {
                XCTFail("Expected canonical UTF-8 publication for \(type): \(write), \(token.outcome(.writeFailed))")
                continue
            }
            if text.hasPrefix("\u{FEFF}") {
                // AppKit's string convenience conversion can consume a literal prefix.
                // These added cases use the canonical raw UTF-8 bytes as their oracle.
                let types = board.types ?? []
                XCTAssertTrue(types.contains(.string), label)
                XCTAssertTrue(types.allSatisfy {
                    $0.rawValue == "NSStringPboardType" || UTType($0.rawValue)?.conforms(to: .plainText) == true
                }, label)
            } else {
                assertPlainTextOnly(board, text: text)
            }
            XCTAssertEqual(board.data(forType: .string), Data(text.utf8), label)
        }

        // Opposite preferences on two items distinguish per-item source ordering from
        // either a fixed encoding rank or the whole board's union of declared types.
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let first = "\u{FEFF}UTF-8 first\r\n"
        let second = "UTF-16 first\r\n"
        let alternative = "not the preferred representation"
        try await publish(board, representations: [
            [("public.utf8-plain-text", Data(first.utf8)),
             ("public.utf16-external-plain-text", try XCTUnwrap(alternative.data(using: .utf16)))],
            [("public.utf16-external-plain-text", try XCTUnwrap(second.data(using: .utf16))),
             ("public.utf8-plain-text", Data(alternative.utf8))]
        ])
        let count = board.changeCount
        let adapter = clipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("Each item must preserve its own preferred representation")
        }
        let expected = Data([first, second].joined(separator: "\n").utf8)
        XCTAssertEqual(Data(snapshot.text.utf8), expected)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(token.outcome(.unavailableData).clipboard, .unchanged)
        try verifyPublishedIdentity(board)
        guard case .written = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Expected canonical publication of both preferred source representations")
        }
        XCTAssertEqual(board.data(forType: .string), expected)
    }

    @MainActor
    func testPrivateInvalidUTF8DoesNotBecomeReplacementCharacters() async throws {
        let cases: [[UInt8]] = [
            [0xFF, 0xFE, 0x41],
            [0xE2, 0x82],
            [0xC3, 0x28],
            [0xC0, 0xAF],
            [0xED, 0xA0, 0x80],
            [0xEF, 0xBB, 0xBF, 0xFF]
        ]
        for (index, codeUnits) in cases.enumerated() {
            let board = newPrivatePasteboard()
            defer { releasePrivatePasteboard(board) }
            let bytes = Data(codeUnits)
            let item = NSPasteboardItem()
            XCTAssertTrue(item.setData(bytes, forType: .string))
            try await publish(board, items: [item])
            let count = board.changeCount
            let adapter = clipboard(name: board.name)
            guard case .failure(.unavailableData) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
                return XCTFail("Invalid UTF-8 case \(index) must not become lossy successful text")
            }
            try verifyPublishedIdentity(board)
            XCTAssertEqual(board.changeCount, count, "case \(index)")
            XCTAssertEqual(board.data(forType: .string), bytes, "case \(index)")
        }
    }

    @MainActor
    func testPrivateFulfilledCPromisePreservesTextAndCanBeWrittenOnce() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let text = "  promised \u{4E2D}\u{6587}\tCafe\u{0301}\r\n\u{1F642}\n"
        let provider = try await promiseFixture(board, response: .text(Data(text.utf8)))
        defer { withExtendedLifetime(provider) {} }
        let originalCount = board.changeCount
        let adapter = clipboard(name: board.name)
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("Fulfilling data without clearing must not invalidate the ownership lease")
        }
        try await provider.verifyIdentity(board)
        XCTAssertEqual(Array(snapshot.text.utf8), Array(text.utf8))
        XCTAssertEqual(board.data(forType: .string), Data(text.utf8))
        XCTAssertEqual(board.changeCount, originalCount)
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
        XCTAssertEqual(board.data(forType: .string), Data(text.utf8))
    }

    @MainActor
    func testPrivateWrittenLeaseIsInvalidatedBySameProcessAppKitOwner() async {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        board.declareTypes([.string], owner: nil)
        XCTAssertTrue(board.setString("original", forType: .string))
        let adapter = clipboard(name: board.name)
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
        let first = clipboard(name: board.name)
        let second = clipboard(name: board.name)
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
        let entered = expectation(description: "Native AppKit promise entered")
        let release = DispatchSemaphore(value: 0)
        let provider = try await promiseFixture(board, response: .blockedText(Data("late synthetic".utf8),
                                                                           entered: entered, release: release))
        defer { release.signal(); withExtendedLifetime(provider) {} }
        let count = board.changeCount
        let input = PasteInputDouble()
        let paste = service(clipboard(name: board.name), input: input, scheduler: PasteSchedulerDouble())
        XCTAssertEqual(paste.requestPaste(), .accepted)
        await fulfillment(of: [entered], timeout: 5)
        let heartbeat = expectation(description: "Host main thread runs while external provider is blocked")
        DispatchQueue.main.async { heartbeat.fulfill() }
        await fulfillment(of: [heartbeat], timeout: 1)
        XCTAssertNotEqual(provider.pid, getpid())
        paste.cancel()
        XCTAssertEqual(paste.status, finished(.cancelled))
        XCTAssertTrue(paste.isBusy)
        XCTAssertEqual(paste.requestPaste(), .busy)
        release.signal()
        try await provider.waitForFulfillment()
        await drained(paste)
        XCTAssertEqual(paste.status, finished(.cancelled))
        XCTAssertTrue(input.posts.isEmpty)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.data(forType: .string), Data("late synthetic".utf8))
        XCTAssertEqual(provider.calls, 1)
        paste.shutdown()
        try await provider.verifyIdentity(board)
    }

    @MainActor
    func testPrivateMixedFileClipboardDoesNotFulfillEarlierTextPromise() async throws {
        let board = newPrivatePasteboard()
        defer { releasePrivatePasteboard(board) }
        let provider = try await promiseFixture(board, fileURL: "file:///synthetic/never-opened.txt")
        defer { withExtendedLifetime(provider) {} }
        XCTAssertEqual(provider.publishedItemCount, 2)
        XCTAssertEqual(provider.calls, 0)
        let count = board.changeCount
        let adapter = clipboard(name: board.name,
            trace: { print("PasteboardTrace mixedFile: \($0)") })
        let read = await adapter.read(cancellation: PlainTextPasteCancellation())
        guard case .failure(.noText) = read else {
            return XCTFail("Inspect all metadata before fulfilling text on a mixed-file clipboard: \(read), promise calls \(provider.calls)")
        }
        XCTAssertEqual(try provider.itemCount(), 2)
        try await provider.verifyIdentity(board)
        XCTAssertEqual(provider.calls, 0)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(try provider.itemCount(), 2)
        print("PasteboardFixture mixedFile after read: published=\(provider.publishedItemCount), AppKit=\(try provider.itemCount())")
        XCTAssertEqual(board.pasteboardItems?.count, 2)
    }
}
