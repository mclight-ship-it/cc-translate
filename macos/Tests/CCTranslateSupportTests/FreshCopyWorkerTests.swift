import AppKit
import Darwin
import PDFKit
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
    func testGestureWaitsForExternalPromiseBeyondTwoSecondsWithoutAXFocusOrClipboardMutation() async throws {
        let board = board()
        board.clearContents()
        XCTAssertTrue(board.setString("unrelated old clipboard", forType: .string))
        let reader = SystemFreshCopyClipboard(pasteboard: { board },
            readerExecutable: try ClipboardTestExecutables.product("CCTranslateMac"))
        let selection = FreshCopySelection(environment: .init(
            now: { ProcessInfo.processInfo.systemUptime },
            source: { _ in .init(target: .init(pid: 42), focusIdentity: nil) },
            securityFailure: { nil }, selection: { _ in .unknown(.unsupported) }), clipboard: reader)
        selection.setFallbackEnabled(true)
        // Producer launch is fixture setup between the native key events.
        selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 10)))
        selection.observe(time: ProcessInfo.processInfo.systemUptime, isCopy: true, isRepeat: false)
        let entered = expectation(description: "Gesture reader requests promised data")
        let finished = expectation(description: "Gesture delivers exactly one fresh selection")
        let release = DispatchSemaphore(value: 0)
        let provider = try await PastePromiseFixture(board,
            response: .blockedText(Data("delayed browser/PDF selection".utf8), entered: entered, release: release))
        providers.append(provider)
        let revision = board.changeCount
        var results: [SelectionResult] = []
        selection.onSelection = { results.append($0); finished.fulfill() }
        let timer = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { _ in
            MainActor.assumeIsolated { selection.poll() }
        }
        defer { release.signal(); timer.invalidate(); selection.cancel() }
        selection.observe(time: ProcessInfo.processInfo.systemUptime, isCopy: true, isRepeat: false)
        await fulfillment(of: [entered], timeout: 5)
        try await Task.sleep(nanoseconds: 2_200_000_000)
        XCTAssertTrue(results.isEmpty, "The old two-second gesture deadline must not reject a published promise.")
        release.signal()
        await fulfillment(of: [finished], timeout: 5)
        XCTAssertEqual(results, [.present("delayed browser/PDF selection")])
        try await provider.waitForFulfillment()
        try await provider.verifyIdentity(board)
        XCTAssertEqual(provider.calls, 1)
        XCTAssertEqual(board.changeCount, revision)
    }

    @MainActor
    func testNativeTextViewRepeatedIdenticalSelectionsPublishReadableFreshRevisions() async throws {
        let board = board()
        _ = NSApplication.shared
        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 180))
        let window = NSWindow(contentRect: textView.frame, styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = textView
        defer { window.close() }
        textView.isSelectable = true
        textView.string = "The same selected text"
        XCTAssertTrue(window.makeFirstResponder(textView))
        let selection = NSRange(location: 0, length: textView.string.utf16.count)
        textView.setSelectedRange(selection)
        for _ in 0..<3 {
            XCTAssertEqual(textView.selectedRange(), selection)
            // Use the text system's advertised identifiers, including legacy
            // aliases, rather than imposing NSPasteboard's modern read types.
            let writableTypes = textView.writablePasteboardTypes
            XCTAssertFalse(writableTypes.isEmpty, "A nonempty native selection must advertise copy formats.")
            let before = board.changeCount
            XCTAssertTrue(textView.writeSelection(to: board, types: writableTypes),
                          "Native copy failed for advertised formats: \(writableTypes.map(\.rawValue))")
            XCTAssertGreaterThan(board.changeCount, before)
            let revision = board.changeCount
            XCTAssertEqual(board.string(forType: .string), textView.string,
                           "The native writer must publish the selection before the worker reads it.")
            let items = try XCTUnwrap(board.pasteboardItems)
            XCTAssertEqual(items.count, 1)
            let types = try XCTUnwrap(items.first).types
            let result = try await read(board)
            XCTAssertEqual(result, .present(textView.string))
            XCTAssertEqual(board.changeCount, revision)
            XCTAssertEqual(board.pasteboardItems?.first?.types, types)
            XCTAssertEqual(board.string(forType: .string), textView.string)
            XCTAssertEqual(textView.selectedRange(), selection)
        }
        textView.setSelectedRange(NSRange(location: 0, length: 0))
        XCTAssertEqual(textView.selectedRange().length, 0)
    }

    @MainActor
    func testActualPDFKitSelectionAndRTFCanBeReadFromAnIsolatedPasteboard() async throws {
        let board = board()
        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 420, height: 180))
        let phrase = "PDFKit native selection"
        textView.string = phrase
        textView.font = .systemFont(ofSize: 16)
        textView.layoutManager?.ensureLayout(for: try XCTUnwrap(textView.textContainer))
        let pdfData = textView.dataWithPDF(inside: textView.bounds)
        let document = try XCTUnwrap(PDFDocument(data: pdfData))
        let selected = try XCTUnwrap(document.findString(phrase, withOptions: []).first)
        XCTAssertEqual(selected.string, phrase)
        let rich = try XCTUnwrap(selected.attributedString)
        let rtf = try rich.data(from: NSRange(location: 0, length: rich.length),
                               documentAttributes: [.documentType: NSAttributedString.DocumentType.rtf])
        // PDFView's Copy targets the user's general board. Exercise PDFKit's
        // actual selected text/RTF on a private board instead of touching it.
        for plain in [true, false] {
            let item = NSPasteboardItem()
            if plain { XCTAssertTrue(item.setString(phrase, forType: .string)) }
            XCTAssertTrue(item.setData(rtf, forType: .rtf))
            XCTAssertTrue(item.setData(pdfData, forType: .pdf))
            board.clearContents()
            XCTAssertTrue(board.writeObjects([item]))
            let revision = board.changeCount
            let result = try await read(board)
            XCTAssertEqual(result, .present(phrase))
            XCTAssertEqual(board.changeCount, revision)
            XCTAssertEqual(board.data(forType: .pdf), pdfData)
            XCTAssertEqual(board.data(forType: .rtf), rtf)
        }
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
