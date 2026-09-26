import AppKit
import Darwin
import XCTest
@testable import CCTranslateSupport

private final class ClipboardTraceLog: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []
    func append(_ line: String) { lock.lock(); lines.append(line); lock.unlock() }
    var snapshot: [String] { lock.lock(); defer { lock.unlock() }; return lines }
}

final class ClipboardProcessTests: XCTestCase {
    @MainActor private var boards: [NSPasteboard] = []
    @MainActor private var providers: [PastePromiseFixture] = []

    override func tearDown() async throws {
        for provider in await providers {
            do { try await provider.close() }
            catch { XCTFail("Synthetic producer did not exit and drain cleanly") }
        }
        await MainActor.run {
            self.providers.removeAll()
            for board in self.boards { board.releaseGlobally() }
            self.boards.removeAll()
        }
        try await super.tearDown()
    }

    private func encoded(_ frame: ClipboardReadFrame) throws -> Data {
        var data = try JSONEncoder().encode(frame)
        data.append(0x0A)
        return data
    }

    private func successWire(_ text: Data, request: UUID, pid: Int32 = 42) throws -> Data {
        try encoded(ClipboardReadFrame(request: request, event: "hello", pid: pid, mainThread: true)) +
            encoded(ClipboardReadFrame(request: request, event: "result", bytes: text.count, revision: 7)) + text
    }

    func testDecoderPreservesFragmentedBodyBeyondTranslationFrameLimit() throws {
        let request = UUID()
        let text = "\u{FEFF}" + String(repeating: "\u{4E2D}\tCafe\u{0301}\r\n\u{1F642}\0", count: 20_000)
        let bytes = Data(text.utf8)
        XCTAssertGreaterThan(bytes.count, 65_536)
        let wire = try successWire(bytes, request: request)
        var decoder = ClipboardReadDecoder(request: request, revision: 7)
        var offset = 0
        while offset < wire.count {
            let end = min(offset + 137, wire.count)
            try decoder.append(wire[offset..<end])
            offset = end
        }
        guard case .success(let decoded) = decoder.result(process: .init(pid: 42, exitCode: 0, failure: nil)) else {
            return XCTFail("Complete byte-framed body must decode")
        }
        XCTAssertEqual(Data(decoded.utf8), bytes)
    }

    func testDecoderRejectsWrongRequestVersionAndBackgroundReader() throws {
        let request = UUID()
        var wrongVersion = ClipboardReadFrame(request: request, event: "hello", pid: 42, mainThread: true)
        wrongVersion.version = 2
        for frame in [wrongVersion,
                      ClipboardReadFrame(request: UUID(), event: "hello", pid: 42, mainThread: true),
                      ClipboardReadFrame(request: request, event: "hello", pid: 42, mainThread: false)] {
            var decoder = ClipboardReadDecoder(request: request, revision: 7)
            XCTAssertThrowsError(try decoder.append(encoded(frame)))
        }
    }

    func testDecoderRejectsEarlyResultDuplicateHelloAndOversizedMetadata() throws {
        let request = UUID()
        var decoder = ClipboardReadDecoder(request: request, revision: 7)
        XCTAssertThrowsError(try decoder.append(encoded(
            ClipboardReadFrame(request: request, event: "result", bytes: 0, revision: 7))))
        decoder = ClipboardReadDecoder(request: request, revision: 7)
        let hello = try encoded(ClipboardReadFrame(request: request, event: "hello", pid: 42, mainThread: true))
        try decoder.append(hello)
        XCTAssertThrowsError(try decoder.append(hello))
        decoder = ClipboardReadDecoder(request: request, revision: 7)
        XCTAssertThrowsError(try decoder.append(Data(repeating: 0x20, count: 8193)))
    }

    func testDecoderRejectsTruncatedTrailingAndMalformedUTF8Bodies() throws {
        let request = UUID()
        let process = ClipboardProcessResult(pid: 42, exitCode: 0, failure: nil)
        var decoder = ClipboardReadDecoder(request: request, revision: 7)
        let wire = try successWire(Data("synthetic".utf8), request: request)
        try decoder.append(wire.dropLast())
        XCTAssertEqual(decoder.result(process: process), .failure(.unavailableData))
        decoder = ClipboardReadDecoder(request: request, revision: 7)
        XCTAssertThrowsError(try decoder.append(wire + Data([0])))
        decoder = ClipboardReadDecoder(request: request, revision: 7)
        try decoder.append(successWire(Data([0xEF, 0xBB, 0xBF, 0xFF]), request: request))
        XCTAssertEqual(decoder.result(process: process), .failure(.unavailableData))
    }

    func testDecoderRequiresMatchingSuccessfulProcessReceipt() throws {
        let request = UUID()
        var decoder = ClipboardReadDecoder(request: request, revision: 7)
        try decoder.append(successWire(Data("synthetic".utf8), request: request))
        for process in [
            ClipboardProcessResult(pid: nil, exitCode: 0, failure: nil),
            ClipboardProcessResult(pid: 43, exitCode: 0, failure: nil),
            ClipboardProcessResult(pid: 42, exitCode: 74, failure: nil),
            ClipboardProcessResult(pid: 42, exitCode: 0, failure: "pipe_read_failed")
        ] {
            XCTAssertEqual(decoder.result(process: process), .failure(.unavailableData))
        }
    }

    @MainActor
    func testUnrelatedLaunchDoesNotEnterClipboardWorker() {
        XCTAssertNil(ClipboardReadWorker.runIfRequested(arguments: ["CCTranslateMac"]))
        XCTAssertNil(ClipboardReadWorker.runIfRequested(arguments: ["CCTranslateMac", "-psn_synthetic"]))
    }

    @MainActor
    func testActualAppRejectsMalformedWorkerInvocationWithoutUIBootstrap() async throws {
        let executable = try ClipboardTestExecutables.product("CCTranslateMac")
        let result: (ClipboardProcessResult, Data) = await withCheckedContinuation { continuation in
            var output = Data()
            let process = ClipboardProcess(receive: { output.append($0) }, completion: {
                continuation.resume(returning: ($0, output))
            })
            process.start(executable: executable, arguments: [ClipboardReadWorker.argument])
        }
        XCTAssertEqual(result.0.exitCode, 64)
        XCTAssertNil(result.0.failure)
        XCTAssertNotNil(result.0.pid)
        XCTAssertNotEqual(result.0.pid, getpid())
        XCTAssertTrue(result.1.isEmpty, "Invalid mode must not start models or emit user data")
    }

    func testExitedWorkerIsNotTimedOutDuringMandatoryGroupCleanup() async {
        let began = ProcessInfo.processInfo.systemUptime
        let result: ClipboardProcessResult = await withCheckedContinuation { continuation in
            let process = ClipboardProcess(timeout: 0.15, receive: { _ in }, completion: {
                continuation.resume(returning: $0)
            })
            process.start(executable: URL(fileURLWithPath: "/usr/bin/true"), arguments: [])
        }
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertNil(result.failure, "An already exited worker must not time out while waiting to reap.")
        XCTAssertGreaterThanOrEqual(ProcessInfo.processInfo.systemUptime - began, 0.2)
    }

    @MainActor
    private func board(_ data: Data = Data("synthetic".utf8)) -> NSPasteboard {
        let board = NSPasteboard.withUniqueName()
        boards.append(board)
        board.clearContents()
        XCTAssertTrue(board.setData(data, forType: .string))
        return board
    }

    @MainActor
    func testConstructionAndPrecancelledReadDoNotLaunchAWorker() async throws {
        let board = board()
        let trace = ClipboardTraceLog()
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            readerExecutable: try ClipboardTestExecutables.product("CCTranslateMac"), trace: { trace.append($0) })
        XCTAssertTrue(trace.snapshot.isEmpty)
        let cancellation = PlainTextPasteCancellation()
        cancellation.cancel()
        guard case .failure(.cancelled) = await adapter.read(cancellation: cancellation) else {
            return XCTFail("A precancelled read must not launch")
        }
        XCTAssertTrue(trace.snapshot.isEmpty)
    }

    @MainActor
    func testMissingExecutableFailsWithoutChangingClipboard() async {
        let board = board()
        let count = board.changeCount
        let trace = ClipboardTraceLog()
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let adapter = SystemPlainTextPasteClipboard(name: board.name, readerExecutable: missing,
                                                    trace: { trace.append($0) })
        guard case .failure(.unavailableData) = await adapter.read(cancellation: PlainTextPasteCancellation()) else {
            return XCTFail("Missing executable must fail, not fall back to an in-process read")
        }
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.data(forType: .string), Data("synthetic".utf8))
        XCTAssertFalse(trace.snapshot.contains { $0.contains("started pid") })
    }

    @MainActor
    func testActualAppTransfersLargeExactBytesAndReapsBeforeGrantingLease() async throws {
        let text = "\u{FEFF}" + String(repeating: " \u{4E2D}\tCafe\u{0301}\r\n\u{1F642} ", count: 10_000)
        let bytes = Data(text.utf8)
        XCTAssertGreaterThan(bytes.count, 65_536)
        let board = board(bytes)
        let count = board.changeCount
        let trace = ClipboardTraceLog()
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            readerExecutable: try ClipboardTestExecutables.product("CCTranslateMac"), trace: { trace.append($0) })
        let token = PlainTextPasteCancellation()
        guard case .text(let snapshot) = await adapter.read(cancellation: token) else {
            return XCTFail("The actual App worker must return complete UTF-8")
        }
        XCTAssertEqual(Data(snapshot.text.utf8), bytes)
        XCTAssertEqual(board.changeCount, count)
        let lines = trace.snapshot
        XCTAssertEqual(lines.filter { $0.contains("started pid") }.count, 1)
        let reaped = try XCTUnwrap(lines.firstIndex { $0.contains("reaped pid") })
        let drained = try XCTUnwrap(lines.firstIndex(of: "clipboard process pipes drained"))
        XCTAssertLessThan(reaped, drained)
        guard case .written = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("A drained successful read must support exactly one write")
        }
        XCTAssertEqual(board.data(forType: .string), bytes)
        guard case .failure(.clipboardChanged) = await adapter.replace(snapshot, cancellation: token) else {
            return XCTFail("Lease replay must fail")
        }
    }

    @MainActor
    func testTimeoutReapsOnlyReaderAndDoesNotClaimExternalProducerStopped() async throws {
        let board = board()
        let entered = expectation(description: "External provider is really blocked")
        let release = DispatchSemaphore(value: 0)
        let provider = try await PastePromiseFixture(board,
            response: .blockedText(Data("late synthetic".utf8), entered: entered, release: release))
        providers.append(provider)
        defer { release.signal() }
        let count = board.changeCount
        let trace = ClipboardTraceLog()
        let adapter = SystemPlainTextPasteClipboard(name: board.name,
            readerExecutable: try ClipboardTestExecutables.product("CCTranslateMac"),
            workerTimeout: 5, trace: { trace.append($0) })
        let token = PlainTextPasteCancellation()
        let task = Task { await adapter.read(cancellation: token) }
        await fulfillment(of: [entered], timeout: 5)
        let heartbeat = expectation(description: "Host UI remains responsive")
        DispatchQueue.main.async { heartbeat.fulfill() }
        await fulfillment(of: [heartbeat], timeout: 1)
        guard case .failure(.clipboardTimedOut) = await task.value else {
            return XCTFail("A stuck reader must time out, reap and fail closed")
        }
        XCTAssertTrue(trace.snapshot.contains { $0.contains("reaped pid") })
        XCTAssertTrue(trace.snapshot.contains("clipboard process pipes drained"))
        XCTAssertEqual(trace.snapshot.filter { $0.contains("started pid") }.count, 1)
        XCTAssertEqual(kill(try XCTUnwrap(provider.pid), 0), 0, "Reader cleanup must not kill the external owner")
        release.signal()
        try await provider.waitForFulfillment()
        try await provider.verifyIdentity(board)
        XCTAssertEqual(provider.calls, 1)
        XCTAssertEqual(board.changeCount, count)
        XCTAssertEqual(board.data(forType: .string), Data("late synthetic".utf8))
        XCTAssertEqual(token.outcome(.clipboardTimedOut).clipboard, .unchanged)
    }
}
