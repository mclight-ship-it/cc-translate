import AppKit
import Carbon
import XCTest
@testable import CCTranslateSupport

final class FreshCopyClipboardTests: XCTestCase {
    @MainActor private static var nextItemID: UInt = 0x460000
    @MainActor private var privateBoards: [NSPasteboard] = []
    @MainActor private var publishedReferences: [Pasteboard] = []
    private enum PublicationError: Error {
        case systemPasteboard, identifierExhausted, status(OSStatus)
        case notVisible(revision: Int, items: Int?)
    }

    override func tearDown() async throws {
        await MainActor.run {
            // Retire C publishers before deleting the resources their cleanup may still use.
            autoreleasepool { self.publishedReferences.removeAll() }
            XCTAssertTrue(self.publishedReferences.isEmpty)
            for board in self.privateBoards { board.releaseGlobally() }
            self.privateBoards.removeAll()
        }
        try await super.tearDown()
    }

    @MainActor
    private func newPrivateBoard() -> NSPasteboard {
        let board = NSPasteboard.withUniqueName()
        privateBoards.append(board)
        return board
    }

    @MainActor
    private func publish(_ bytes: Data, to board: NSPasteboard,
                         types: [NSPasteboard.PasteboardType] = [.string]) async throws -> Pasteboard {
        try await publish(types.map { ($0.rawValue, bytes) }, to: board)
    }

    @MainActor
    private func publish(_ representations: [(String, Data)], to board: NSPasteboard) async throws -> Pasteboard {
        guard board.name != .general else { throw PublicationError.systemPasteboard }
        var raw: Pasteboard?
        try check(PasteboardCreate(board.name.rawValue as CFString, &raw))
        let reference = try XCTUnwrap(raw)
        publishedReferences.append(reference)
        var copiedName: CFString?
        try check(PasteboardCopyName(reference, &copiedName))
        XCTAssertEqual(copiedName.map { $0 as String }, board.name.rawValue)
        let previousRevision = board.changeCount
        try check(PasteboardClear(reference))
        _ = PasteboardSynchronize(reference)
        guard Self.nextItemID <= UInt(Int32.max) else { throw PublicationError.identifierExhausted }
        let identifier = try XCTUnwrap(PasteboardItemID(bitPattern: Self.nextItemID))
        Self.nextItemID += 1
        // Use eager external bytes, not NSPasteboardItem's potentially converted aliases.
        // The fixture retains publishers until teardown, before releasing its private boards.
        for (type, bytes) in representations {
            try check(PasteboardPutItemFlavor(reference, identifier, type as CFString,
                                              bytes as CFData, PasteboardFlavorFlags(rawValue: 0)))
        }
        _ = PasteboardSynchronize(reference)
        for (type, bytes) in representations {
            var actual: CFData?
            try check(PasteboardCopyItemFlavorData(reference, identifier, type as CFString, &actual))
            XCTAssertEqual(actual.map { $0 as Data }, bytes, "Published raw bytes: \(type)")
        }
        var itemCount = 0
        try check(PasteboardGetItemCount(reference, &itemCount))
        XCTAssertEqual(itemCount, 1, "The external publisher must contain its one eager item.")
        print("Fresh-copy fixture publication: \(representations.map { "\($0.0):\($0.1.count)" }), C items=\(itemCount)")
        try await waitForPublication(on: board, after: previousRevision)
        _ = try assertPublished(representations, on: board)
        return reference
    }

    @MainActor
    private func waitForPublication(on board: NSPasteboard, after previousRevision: Int,
                                    timeout: TimeInterval = 2) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        var waits = 0
        while true {
            let revision = board.changeCount
            let count = board.pasteboardItems?.count
            if revision > previousRevision, let count, count > 0 {
                print("Fresh-copy fixture ready: revision=\(revision), metadata items=\(count), waits=\(waits)")
                return
            }
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                throw PublicationError.notVisible(revision: revision, items: count)
            }
            // Only prepare the eager external fixture. Do not retry writes, payload checks or the reader.
            waits += 1
            try await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    private func check(_ status: OSStatus) throws {
        guard status == noErr else { throw PublicationError.status(status) }
    }

    @MainActor
    @discardableResult
    private func assertPublished(_ representations: [(String, Data)], on board: NSPasteboard) throws
        -> NSPasteboardItem {
        // Refresh AppKit's revision after the external Carbon publication before enumerating items.
        let revision = board.changeCount
        let items = try XCTUnwrap(board.pasteboardItems)
        XCTAssertEqual(items.count, 1, "AppKit revision \(revision), representations \(representations.map { "\($0.0):\($0.1.count)" })")
        let item = try XCTUnwrap(items.first)
        for (type, bytes) in representations {
            XCTAssertTrue(item.types.contains(.init(type)), "Missing published type: \(type)")
            XCTAssertEqual(item.data(forType: .init(type)), bytes, "AppKit source bytes: \(type)")
        }
        XCTAssertEqual(board.changeCount, revision)
        return item
    }

    @MainActor
    private func assertRead(_ representations: [(String, Data)], expected: String) async throws {
        let board = newPrivateBoard()
        let reference = try await publish(representations, to: board)
        defer { withExtendedLifetime(reference) {} }
        let revision = board.changeCount
        let before = try assertPublished(representations, on: board)
        let types = before.types
        XCTAssertEqual(PasteboardTextRepresentation.preferred(in: types.map(\.rawValue))?.type,
                       representations.first?.0, "Actual ordered advertisements: \(types.map(\.rawValue))")
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        guard case .present(let text) = reader.read(revision: revision, whileValid: { true }) else {
            return XCTFail("Expected plain text from actual item advertisements: \(types.map(\.rawValue))")
        }
        XCTAssertEqual(Data(text.utf8), Data(expected.utf8))
        XCTAssertEqual(board.changeCount, revision)
        XCTAssertEqual(try assertPublished(representations, on: board).types, types)
    }

    @MainActor
    func testReaderConstructionAndInvalidAuthorizationDoNotAccessAnyPasteboard() async throws {
        var accesses = 0
        let privateBoard = newPrivateBoard()
        let reader = FreshCopyPasteboardReader { accesses += 1; return privateBoard }
        XCTAssertEqual(accesses, 0)
        XCTAssertEqual(reader.read(revision: 1, whileValid: { false }), .unknown(.clipboardChanged))
        XCTAssertEqual(accesses, 0)
        let revision = privateBoard.changeCount
        do {
            try await waitForPublication(on: privateBoard, after: revision, timeout: 0.02)
            XCTFail("An unpublished fixture must not be admitted as ready.")
        } catch PublicationError.notVisible(_, let items) {
            XCTAssertTrue(items == nil || items == 0)
        }
        XCTAssertEqual(accesses, 0)
    }

    @MainActor
    func testExactFreshUnicodeTextIsReadWithoutChangingAnyRepresentations() async throws {
        let board = newPrivateBoard()
        let original = "Copied \u{4e2d}\u{6587}\nline two \u{1f600}"
        let reference = try await publish(Data(original.utf8), to: board, types: [.string, .html])
        defer { withExtendedLifetime(reference) {} }
        let revision = board.changeCount
        let before = try XCTUnwrap(board.pasteboardItems?.first)
        let types = before.types
        let html = before.data(forType: .html)
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), .present(original))
        XCTAssertEqual(board.changeCount, revision)
        let after = try XCTUnwrap(board.pasteboardItems?.first)
        XCTAssertEqual(after.types, types)
        XCTAssertEqual(after.data(forType: .string), Data(original.utf8))
        XCTAssertEqual(after.data(forType: .html), html)

        let nativeUTF16: String.Encoding = UInt16(littleEndian: 1) == 1 ? .utf16LittleEndian : .utf16BigEndian
        let cases: [(String, String.Encoding, String)] = [
            ("public.utf8-plain-text", .utf8, "\u{FEFF}literal UTF-8 prefix\r\n"),
            ("public.utf8-plain-text", .utf8, "\u{FEFF}\u{FEFF}literal \u{FFFD}\r\n"),
            ("public.utf8-plain-text", .utf8, "\u{FEFF}"),
            ("public.utf16-plain-text", .utf16, "\u{FEFF}literal after BOM\r\n\u{1F642}"),
            ("public.utf16-plain-text", nativeUTF16, " \u{4E2D} no BOM\r\nline two "),
            ("public.utf16-external-plain-text", .utf16, "\u{FEFF}external BOM\r\n"),
            ("public.utf8-tab-separated-values-text", .utf8, "\u{FEFF}\u{4E2D}\tvalue\r\n"),
            ("com.apple.traditional-mac-plain-text", .macOSRoman, " Caf\u{00E9}\t42\r\n ")
        ]
        for (type, encoding, text) in cases {
            let bytes: Data
            if encoding == .utf8 { bytes = Data(text.utf8) }
            else { bytes = try XCTUnwrap(text.data(using: encoding)) }
            try await assertRead([(type, bytes)], expected: text)
        }
        let bigEndian = "\u{FEFF}big-endian BOM\r\n"
        let bigEndianBytes = Data([0xFE, 0xFF]) + (try XCTUnwrap(bigEndian.data(using: .utf16BigEndian)))
        try await assertRead([("public.utf16-external-plain-text", bigEndianBytes)], expected: bigEndian)

        // Fresh copy deliberately permits one item only. Use separate boards with
        // opposite per-item advertisement orders and distinct alternate contents.
        let utf8First = "\u{FEFF}UTF-8 first\r\n"
        let utf16First = "\u{FEFF}UTF-16 first\r\n"
        let alternate = "not the preferred representation"
        try await assertRead([
            ("public.utf8-plain-text", Data(utf8First.utf8)),
            ("public.utf16-external-plain-text", try XCTUnwrap(alternate.data(using: .utf16)))
        ], expected: utf8First)
        try await assertRead([
            ("public.utf16-external-plain-text", try XCTUnwrap(utf16First.data(using: .utf16))),
            ("public.utf8-plain-text", Data(alternate.utf8))
        ], expected: utf16First)
    }

    @MainActor
    func testOldRevisionAndChangeImmediatelyBeforeDataReadAreRejected() async throws {
        let board = newPrivateBoard()
        let reference = try await publish(Data("fresh".utf8), to: board)
        defer { withExtendedLifetime(reference) {} }
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        let revision = board.changeCount
        do {
            try await waitForPublication(on: board, after: revision, timeout: 0.02)
            XCTFail("Existing contents without a new revision are not a new publication.")
        } catch PublicationError.notVisible(let actualRevision, let items) {
            XCTAssertEqual(actualRevision, revision)
            XCTAssertEqual(items, 1)
        }
        XCTAssertEqual(reader.read(revision: revision - 1, whileValid: { true }), .unknown(.clipboardChanged))
        var checks = 0
        let result = reader.read(revision: revision) {
            checks += 1
            if checks == 3 { board.clearContents() }
            return true
        }
        XCTAssertEqual(result, .unknown(.clipboardChanged))
        XCTAssertEqual(checks, 3)
    }

    @MainActor
    func testCancellationAfterReadDiscardsTextWithoutClearingUserCopy() async throws {
        let board = newPrivateBoard()
        let reference = try await publish(Data("fresh".utf8), to: board)
        defer { withExtendedLifetime(reference) {} }
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        let revision = board.changeCount
        var checks = 0
        let result = reader.read(revision: revision) {
            checks += 1
            return checks < 5
        }
        XCTAssertEqual(result, .unknown(.clipboardChanged))
        XCTAssertEqual(checks, 5)
        XCTAssertEqual(board.changeCount, revision)
        XCTAssertEqual(board.pasteboardItems?.first?.data(forType: .string), Data("fresh".utf8))
    }

    @MainActor
    func testFileImageConcealedAndHTMLOnlyCopiesAreNotTextFallbacks() async throws {
        let representations: [[NSPasteboard.PasteboardType]] = [
            [.fileURL], [.string, .init("org.nspasteboard.ConcealedType")],
            [.init("com.apple.pasteboard.promised-file-url")], [.png], [.html]
        ]
        for types in representations {
            let board = newPrivateBoard()
            let reference = try await publish(Data("not an authorized plain selection".utf8), to: board, types: types)
            defer { withExtendedLifetime(reference) {} }
            let advertised = try XCTUnwrap(board.pasteboardItems?.first).types.map(\.rawValue)
            if types == [.html] {
                XCTAssertNil(PasteboardTextRepresentation.preferred(in: advertised),
                             "This fixture must actually advertise rich-only data, not converted plain aliases.")
            }
            let reader = FreshCopyPasteboardReader(pasteboard: { board })
            let revision = board.changeCount
            var checks = 0
            XCTAssertEqual(reader.read(revision: revision, whileValid: { checks += 1; return true }),
                           .unknown(.clipboardUnsupported))
            XCTAssertEqual(checks, 3, "Unsupported metadata must be refused before any data request.")
            XCTAssertEqual(board.changeCount, revision)
        }
    }

    @MainActor
    func testBrowserPlainTextIgnoresRichSourceAndVendorMetadataWithoutMutatingCopy() async throws {
        let text = "Selected browser text \u{4e2d}\u{6587}\nnot the source URL or HTML"
        for metadata in [
            ["public.html", "org.chromium.source-url", "org.chromium.web-custom-data"],
            ["public.html", "com.apple.WebKit.smartpaste", "com.apple.WebKit.custom-pasteboard-data"],
            ["public.html", "org.mozilla.clipboard.htmlcontext", "org.mozilla.clipboard.htmlinfo"],
            ["public.html", "com.example.unknown"],
            ["com.adobe.pdf", "com.apple.pdf", "org.chromium.web-custom-data"],
            ["public.png", "com.apple.iWork.TSPNativeData"],
            ["public.jpeg", "com.adobe.private-pdf-selection"],
            ["public.file-url", "com.adobe.pdf"],
            ["org.nspasteboard.TransientType", "org.nspasteboard.AutoGeneratedType"]
        ] {
            try await assertRead(
                [("public.utf8-plain-text", Data(text.utf8))] +
                metadata.map { ($0, Data([0xff, 0x00, 0xfe])) }, expected: text)
        }
    }

    @MainActor
    func testBrowserMetadataAloneNeverBecomesTextAndDoesNotHideSensitiveMarkers() async throws {
        let representations: [[NSPasteboard.PasteboardType]] = [
            [.html, .init("com.apple.WebKit.smartpaste")],
            [.string, .init("org.chromium.source-url"), .init("org.nspasteboard.ConcealedType")]
        ]
        for types in representations {
            let board = newPrivateBoard()
            let reference = try await publish(Data("not selected plain text".utf8), to: board, types: types)
            defer { withExtendedLifetime(reference) {} }
            let reader = FreshCopyPasteboardReader(pasteboard: { board })
            let revision = board.changeCount
            var checks = 0
            XCTAssertEqual(reader.read(revision: revision, whileValid: { checks += 1; return true }),
                           .unknown(.clipboardUnsupported))
            XCTAssertEqual(checks, 3)
            XCTAssertEqual(board.changeCount, revision)
        }
    }

    @MainActor
    func testMultipleTextItemsAreJoinedInOrderAndEmptyOrMixedBoardsAreNotTruncated() throws {
        let board = newPrivateBoard()
        board.clearContents()
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        XCTAssertEqual(reader.read(revision: board.changeCount, whileValid: { true }),
                       .absent)
        let first = NSPasteboardItem()
        let second = NSPasteboardItem()
        XCTAssertTrue(first.setString("first", forType: .string))
        XCTAssertTrue(second.setString("second", forType: .string))
        XCTAssertTrue(board.writeObjects([first, second]))
        let revision = board.changeCount
        XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), .present("first\nsecond"))
        XCTAssertEqual(board.changeCount, revision)
        // Published items stay bound to their original pasteboard publication,
        // even after clearContents; the next copy needs fresh item instances.
        let mixedText = NSPasteboardItem()
        let image = NSPasteboardItem()
        XCTAssertTrue(mixedText.setString("first", forType: .string))
        XCTAssertTrue(image.setData(Data([1, 2, 3]), forType: .png))
        board.clearContents()
        XCTAssertTrue(board.writeObjects([mixedText, image]))
        let mixedRevision = board.changeCount
        let mixedItems = try XCTUnwrap(board.pasteboardItems)
        XCTAssertEqual(mixedItems.count, 2)
        XCTAssertEqual(mixedItems.first?.string(forType: .string), "first")
        XCTAssertEqual(mixedItems.last?.data(forType: .png), Data([1, 2, 3]))
        XCTAssertEqual(reader.read(revision: mixedRevision, whileValid: { true }), .unknown(.clipboardUnsupported))
        XCTAssertEqual(board.changeCount, mixedRevision)
    }

    @MainActor
    func testUTF8BudgetInvalidUnicodeEmptyAndNULAreNotInventedSelections() async throws {
        let limit = String(repeating: "\u{1f600}", count: 2048)
        try await assertRead([("public.utf8-plain-text", Data(limit.utf8))], expected: limit)
        let asciiLimit = String(repeating: "a", count: 8192)
        let nativeUTF16: String.Encoding = UInt16(littleEndian: 1) == 1 ? .utf16LittleEndian : .utf16BigEndian
        let asciiBytes = try XCTUnwrap(asciiLimit.data(using: nativeUTF16))
        XCTAssertEqual(asciiBytes.count, 16_384)
        try await assertRead([("public.utf16-plain-text", asciiBytes)], expected: asciiLimit)
        let bomLimit = asciiLimit
        let bomBytes = try XCTUnwrap(bomLimit.data(using: .utf16))
        XCTAssertEqual(bomBytes.count, 16_386)
        try await assertRead([("public.utf16-external-plain-text", bomBytes)], expected: bomLimit)
        let prefixedLimit = "\u{FEFF}" + String(repeating: "a", count: 8189)
        XCTAssertEqual(prefixedLimit.utf8.count, 8192)
        try await assertRead([("public.utf8-plain-text", Data(prefixedLimit.utf8))], expected: prefixedLimit)
        let cases: [(String, Data, SelectionResult)] = [
            ("public.utf8-plain-text", Data(repeating: 97, count: 8193), .unknown(.tooLarge)),
            ("public.utf8-plain-text", Data((prefixedLimit + "a").utf8), .unknown(.tooLarge)),
            ("public.utf8-plain-text", Data(repeating: 97, count: 16_385), .unknown(.tooLarge)),
            ("public.utf16-external-plain-text",
             try XCTUnwrap((bomLimit + "a").data(using: .utf16)), .unknown(.tooLarge)),
            ("public.utf8-plain-text", Data([0xc0, 0xaf]), .unknown(.clipboardUnavailable)),
            ("public.utf8-plain-text", Data([0xff, 0xfe, 0x41, 0x00]), .unknown(.clipboardUnavailable)),
            ("public.utf8-plain-text", Data([0xe2, 0x82]), .unknown(.clipboardUnavailable)),
            ("public.utf8-plain-text", Data([0xed, 0xa0, 0x80]), .unknown(.clipboardUnavailable)),
            ("public.utf8-plain-text", Data(), .absent),
            ("public.utf8-plain-text", Data(" \n\t".utf8), .absent),
            ("public.utf8-plain-text", Data([65, 0, 66]), .unknown(.clipboardUnavailable)),
            ("public.utf16-plain-text",
             try XCTUnwrap(String(repeating: "\u{4E2D}", count: 2731).data(using: nativeUTF16)),
             .unknown(.tooLarge)),
            ("public.utf16-plain-text", try XCTUnwrap(" \r\n\t".data(using: nativeUTF16)),
             .absent),
            ("public.utf16-external-plain-text", try XCTUnwrap("A\0B".data(using: .utf16)),
             .unknown(.clipboardUnavailable)),
            ("com.apple.traditional-mac-plain-text",
             try XCTUnwrap(String(repeating: "\u{00E9}", count: 4097).data(using: .macOSRoman)),
             .unknown(.tooLarge))
        ]
        for (type, bytes, expected) in cases {
            let board = newPrivateBoard()
            let reference = try await publish([(type, bytes)], to: board)
            defer { withExtendedLifetime(reference) {} }
            let reader = FreshCopyPasteboardReader(pasteboard: { board })
            let revision = board.changeCount
            XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), expected,
                           "Source \(type), raw bytes \(bytes.count)")
            XCTAssertEqual(board.changeCount, revision)
            try assertPublished([(type, bytes)], on: board)
        }
        let board = newPrivateBoard()
        let malformed: [(String, Data)] = [
            ("public.utf8-plain-text", Data([0xc0, 0xaf])),
            ("public.utf16-external-plain-text", try XCTUnwrap("valid alternate representation".data(using: .utf16)))
        ]
        let reference = try await publish(malformed, to: board)
        defer { withExtendedLifetime(reference) {} }
        let item = try assertPublished(malformed, on: board)
        XCTAssertEqual(PasteboardTextRepresentation.preferred(in: item.types.map(\.rawValue))?.type,
                       "public.utf8-plain-text")
        let revision = board.changeCount
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), .present("valid alternate representation"))
        XCTAssertEqual(board.changeCount, revision)
        try assertPublished(malformed, on: board)
    }

    @MainActor
    func testEmptyFirstFlavorFallsThroughToValidCopiedTextWithoutReadingMetadata() async throws {
        let board = newPrivateBoard()
        let representations = [
            ("public.utf8-plain-text", Data()),
            ("public.utf16-external-plain-text", try XCTUnwrap("actual selection".data(using: .utf16))),
            ("public.html", Data([0xFF, 0, 0xFE]))
        ]
        let reference = try await publish(representations, to: board)
        defer { withExtendedLifetime(reference) {} }
        let revision = board.changeCount
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), .present("actual selection"))
        XCTAssertEqual(board.changeCount, revision)
        try assertPublished(representations, on: board)
    }

    @MainActor
    func testRTFTextUsesNativeConversionButMalformedDataIsNotASelection() async throws {
        let text = NSAttributedString(string: "Native rich selection")
        let data = try text.data(from: NSRange(location: 0, length: text.length),
                                 documentAttributes: [.documentType: NSAttributedString.DocumentType.rtf])
        let board = newPrivateBoard()
        let reference = try await publish([("public.rtf", data)], to: board)
        defer { withExtendedLifetime(reference) {} }
        let revision = board.changeCount
        let reader = FreshCopyPasteboardReader(pasteboard: { board })
        XCTAssertEqual(reader.read(revision: revision, whileValid: { true }), .present(text.string))
        XCTAssertEqual(board.changeCount, revision)
        try assertPublished([("public.rtf", data)], on: board)
        let invalidBoard = newPrivateBoard()
        let invalid = try await publish([("public.rtf", Data("not RTF".utf8))], to: invalidBoard)
        defer { withExtendedLifetime(invalid) {} }
        XCTAssertEqual(FreshCopyPasteboardReader(pasteboard: { invalidBoard })
            .read(revision: invalidBoard.changeCount, whileValid: { true }), .unknown(.clipboardUnavailable))
    }

    @MainActor
    func testCombinedTextItemsHonorTotalBudgetWithoutTruncation() {
        let board = newPrivateBoard()
        let first = NSPasteboardItem()
        let second = NSPasteboardItem()
        XCTAssertTrue(first.setString(String(repeating: "a", count: 4096), forType: .string))
        XCTAssertTrue(second.setString(String(repeating: "b", count: 4096), forType: .string))
        board.clearContents()
        XCTAssertTrue(board.writeObjects([first, second]))
        let revision = board.changeCount
        XCTAssertEqual(FreshCopyPasteboardReader(pasteboard: { board })
            .read(revision: revision, whileValid: { true }), .unknown(.tooLarge))
        XCTAssertEqual(board.changeCount, revision)
    }
}
