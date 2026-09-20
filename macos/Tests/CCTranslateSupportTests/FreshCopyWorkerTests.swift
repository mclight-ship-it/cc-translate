import AppKit
import Darwin
import XCTest
@testable import CCTranslateSupport

final class FreshCopyWorkerTests: XCTestCase {
    @MainActor private var boards: [NSPasteboard] = []
    @MainActor private var providers: [PastePromiseFixture] = []

    override func tearDown() async throws {
        for provider in await providers {
            do { try await provider.close() }
            catch { XCTFail("Fresh-copy external provider did not exit cleanly") }
        }
        await MainActor.run {
            self.providers.removeAll()
            for board in self.boards { board.releaseGlobally() }
            self.boards.removeAll()
        }
        try await super.tearDown()
    }

    @MainActor
    private func board() -> NSPasteboard {
        let board = NSPasteboard.withUniqueName()
        boards.append(board)
        return board
    }

    @MainActor
    private func read(_ board: NSPasteboard, cancellation: PlainTextPasteCancellation = .init(),
                      timeout: TimeInterval = 5) async throws -> SelectionResult {
        let reader = SystemFreshCopyClipboard(pasteboard: { board },
            readerExecutable: try ClipboardTestExecutables.product("CCTranslateMac"))
        return await withCheckedContinuation { continuation in
            reader.read(revision: board.changeCount, timeout: timeout, cancellation: cancellation,
                        whileValid: { true }, completion: { continuation.resume(returning: $0) })
        }
    }

    @MainActor
    func testActualFreshWorkerReadsPlainTextWithPDFAndPrivateRepresentationsWithoutMutation() async throws {
        let board = board()
        let text = "PDF selection \u{4E2D}\u{6587}\r\n\u{1F642}"
        let item = NSPasteboardItem()
        item.setData(Data(text.utf8), forType: .string)
        for type in ["com.adobe.pdf", "public.png", "org.chromium.web-custom-data", "public.html"] {
            item.setData(Data([0xFF, 0, 0xFE]), forType: .init(type))
        }
        board.clearContents()
        XCTAssertTrue(board.writeObjects([item]))
        let revision = board.changeCount
        let result = try await read(board)
        XCTAssertEqual(result, .present(text))
        XCTAssertEqual(board.changeCount, revision)
        XCTAssertEqual(board.data(forType: .string), Data(text.utf8))
        XCTAssertEqual(board.data(forType: .init("com.adobe.pdf")), Data([0xFF, 0, 0xFE]))
    }

    @MainActor
    func testActualFreshWorkerRejectsOversizedTextAndChangedRevision() async throws {
        let board = board()
        board.clearContents()
        XCTAssertTrue(board.setString(String(repeating: "x", count: 8193), forType: .string))
        let revision = board.changeCount
        let result = try await read(board)
        XCTAssertEqual(result, .unknown(.tooLarge))
        XCTAssertEqual(board.changeCount, revision)
        let run = ClipboardReadRun(request: UUID(), revision: revision - 1, freshCopy: true, trace: { _ in })
        let stale = await run.read(executable: try ClipboardTestExecutables.product("CCTranslateMac"),
                                   name: board.name.rawValue, cancellation: .init(), timeout: 5)
        XCTAssertEqual(stale, .failure(.clipboardChanged))
    }

    @MainActor
    func testPromisedFreshTextArrivesAsynchronouslyWhileHostMainThreadRemainsResponsive() async throws {
        let board = board()
        let entered = expectation(description: "Fresh worker requests external promised bytes")
        let release = DispatchSemaphore(value: 0)
        let provider = try await PastePromiseFixture(board,
            response: .blockedText(Data("promised selection".utf8), entered: entered, release: release))
        providers.append(provider)
        defer { release.signal() }
        let revision = board.changeCount
        let task = Task { try await self.read(board) }
        await fulfillment(of: [entered], timeout: 5)
        let responsive = expectation(description: "UI actor is not waiting on NSPasteboard data")
        DispatchQueue.main.async { responsive.fulfill() }
        await fulfillment(of: [responsive], timeout: 1)
        release.signal()
        let result = try await task.value
        XCTAssertEqual(result, .present("promised selection"))
        try await provider.waitForFulfillment()
        try await provider.verifyIdentity(board)
        XCTAssertEqual(provider.calls, 1)
        XCTAssertEqual(board.changeCount, revision)
    }

    @MainActor
    func testCancellationAndTimeoutTerminateOnlyFreshReaderWithoutPublishingOrClearingCopy() async throws {
        for cancel in [true, false] {
            let board = board()
            let entered = expectation(description: "Fresh reader has entered the blocking provider")
            let release = DispatchSemaphore(value: 0)
            let provider = try await PastePromiseFixture(board,
                response: .blockedText(Data("late copy".utf8), entered: entered, release: release))
            providers.append(provider)
            defer { release.signal() }
            let revision = board.changeCount
            let cancellation = PlainTextPasteCancellation()
            let task = Task { try await self.read(board, cancellation: cancellation) }
            await fulfillment(of: [entered], timeout: 5)
            if cancel { cancellation.cancel() }
            let result = try await task.value
            XCTAssertEqual(result, .unknown(cancel ? .clipboardChanged : .clipboardUnavailable))
            XCTAssertEqual(kill(try XCTUnwrap(provider.pid), 0), 0, "Do not kill the user's copy provider.")
            release.signal()
            try await provider.waitForFulfillment()
            try await provider.verifyIdentity(board)
            XCTAssertEqual(board.changeCount, revision)
            XCTAssertEqual(board.data(forType: .string), Data("late copy".utf8))
        }
    }

    func testFreshDecoderRefusesBodiesBeyondSelectionBudgetBeforeAccumulatingPayload() throws {
        let request = UUID()
        var decoder = ClipboardReadDecoder(request: request, revision: 7, maximumBodyBytes: 8192)
        func frame(_ frame: ClipboardReadFrame) throws -> Data {
            try JSONEncoder().encode(frame) + Data([0x0A])
        }
        try decoder.append(frame(.init(request: request, event: "hello", pid: 42, mainThread: true)))
        XCTAssertThrowsError(try decoder.append(frame(.init(request: request, event: "result",
                                                             bytes: 8193, revision: 7))))
    }
}
