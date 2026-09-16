import XCTest
import AppKit
import SwiftUI
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class ProductRenderingTests: XCTestCase {
    @MainActor
    func testTranslatorRendersLightDarkAndNarrowNativeLayouts() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        model.reuseHistory(ProbeModel.HistoryRow(
            id: "synthetic-rendering", input: "A synthetic sentence for native layout validation.",
            output: "用于原生布局验证的合成句子。"))

        let light = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-light", size: NSSize(width: 1120, height: 760), scheme: .light)
        let dark = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-dark", size: NSSize(width: 1120, height: 760), scheme: .dark)
        _ = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-narrow-light", size: NSSize(width: 660, height: 540), scheme: .light)
        _ = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-narrow-dark", size: NSSize(width: 660, height: 540), scheme: .dark)

        XCTAssertNotEqual(light, dark, "The actual native view must respond to its color scheme.")
        for imageData in [light, dark] {
            let bitmap = try XCTUnwrap(NSBitmapImageRep(data: imageData))
            let image = try XCTUnwrap(bitmap.cgImage)
            let footer = try XCTUnwrap(image.cropping(to: CGRect(
                x: 0, y: Double(image.height) * 0.8,
                width: Double(image.width) * 0.5, height: Double(image.height) * 0.2)))
            let visibleWords = try LocalOCR.recognize(footer).text.lowercased()
                .components(separatedBy: CharacterSet.letters.inverted)
            XCTAssertTrue(visibleWords.contains("translate"),
                          "The primary Translate action must stay readable in light and dark inactive windows.")
        }
        XCTAssertEqual(model.output, "用于原生布局验证的合成句子。")
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty })
        XCTAssertFalse(model.cliBusy)
        XCTAssertFalse(model.monitorEnabled)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testAppendedResultActionKeepsPrimaryAndReadableNativeControls() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.reuseHistory(.init(id: "action-render", input: "Synthetic original", output: "Original."))
        model.performResultAction(.summary)
        let action = try XCTUnwrap(helper.resultActions.last)
        helper.event("completed", id: action.id, payload: [
            "text": .string("Short summary."), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("disabled"), "history_error": .null
        ])
        for scheme in [ColorScheme.light, .dark] {
            let imageData = try render(
                TranslationResultView(model: model, compact: true),
                named: "result-action-\(scheme == .light ? "light" : "dark")",
                size: NSSize(width: 420, height: 360), scheme: scheme)
            let image = try XCTUnwrap(NSBitmapImageRep(data: imageData)?.cgImage)
            let words = try LocalOCR.recognize(image).text.lowercased()
            XCTAssertTrue(words.contains("original"))
            XCTAssertTrue(words.contains("summary"))
            XCTAssertTrue(words.contains("actions"))
        }
        XCTAssertEqual(model.primaryResult, "Original.")
        XCTAssertTrue(model.output.hasSuffix("Short summary."))
        XCTAssertEqual(helper.resultActions.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testTranslatorRendersEmptyPreparingAndFailureStatesWithoutCLIExecution() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        _ = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-empty", size: NSSize(width: 960, height: 720), scheme: .light)
        model.input = "Synthetic pending translation"
        model.translate()
        XCTAssertEqual(model.productPhase, .preparing)
        _ = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-preparing", size: NSSize(width: 960, height: 720), scheme: .light)
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("config_unavailable")])
        XCTAssertEqual(model.productPhase, .failed)
        _ = try render(
            TranslatorView(model: model, showHistory: {}, showSettings: {}),
            named: "translator-failure", size: NSSize(width: 960, height: 720), scheme: .dark)

        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(model.cliBusy)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testHistoryRendersLoadedSyntheticEntriesInBothAppearances() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                ProductTestHarness.historyEntry(
                    input: "Synthetic history sentence", output: "合成历史句子"),
                ProductTestHarness.historyEntry(
                    input: "example", output: "A synthetic dictionary definition.", kind: "dict"),
                ProductTestHarness.historyEntry(
                    input: "Another synthetic history sentence", output: "另一个合成历史句子")
            ], total: 3))
        let light = try render(
            TranslationHistoryView(model: model, useEntry: {}),
            named: "history-light", size: NSSize(width: 860, height: 680), scheme: .light)
        let dark = try render(
            TranslationHistoryView(model: model, useEntry: {}),
            named: "history-dark", size: NSSize(width: 860, height: 680), scheme: .dark)

        XCTAssertNotEqual(light, dark)
        XCTAssertEqual(model.historyPage.count, 3)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testFullLibrarySearchResultsAndFilteredTotalAreReadableInBothAppearances() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.historySearch = "remote"
        model.historyFilter = "ocr"
        model.submitHistorySearch()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        ProductTestHarness.historyEntry(input: "Remote translation",
                                                        output: "Found outside the first page.", kind: "ocr")
                     ], total: 38, nextOffset: 1))
        for scheme in [ColorScheme.light, .dark] {
            let png = try render(
                TranslationHistoryView(model: model, useEntry: {}),
                named: "history-search-results-\(scheme == .light ? "light" : "dark")",
                size: NSSize(width: 960, height: 680), scheme: scheme)
            let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
            let words = try LocalOCR.recognize(image).text.lowercased()
            XCTAssertTrue(words.contains("remote"))
            XCTAssertTrue(words.contains("outside"))
            XCTAssertTrue(words.contains("38"))
            XCTAssertTrue(words.contains("load more"))
        }
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(helper.historyLoads.last?.query, "remote")
        XCTAssertEqual(helper.historyLoads.last?.kind, "ocr")
        XCTAssertEqual(model.historyTotal, 38)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty, "Rendering never confirms the global destructive action.")
    }

    @MainActor
    func testEmptyLibraryAndNoSearchMatchesRenderWithoutAutomaticReadReplay() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        for query in ["", "no such word"] {
            model.historySearch = query
            model.submitHistorySearch()
            helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                         payload: ProductTestHarness.historyPage(entries: [], total: 0))
            let reads = helper.historyLoads.count
            for scheme in [ColorScheme.light, .dark] {
                let png = try render(
                    TranslationHistoryView(model: model, useEntry: {}),
                    named: "history-empty-\(query.isEmpty ? "library" : "search")-\(scheme == .light ? "light" : "dark")",
                    size: NSSize(width: 860, height: 680), scheme: scheme)
                let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
                let words = try LocalOCR.recognize(image).text.lowercased()
                XCTAssertTrue(words.contains(query.isEmpty ? "no saved" : "no matching"))
                XCTAssertTrue(words.contains("0"))
            }
            XCTAssertEqual(helper.historyLoads.count, reads)
            XCTAssertEqual(model.historyPhase, .loaded)
            XCTAssertEqual(model.historyTotal, 0)
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }

    @MainActor
    func testHistoryDetailRendersLocalLiteralAndAIMarkdownBySignatureNotDictionaryKind() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        let local = "**literal meaning**\nSource: WordNet"
        let ai = "**Model meaning**\n\nReadable model explanation."
        model.historyFilter = "dict"
        model.submitHistorySearch()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        .object(["input": .string("local word"), "output": .string(local), "is_dict": .bool(true),
                                 "sig": .string("local-dictionary|fixture|native-plain-v1:en_US")]),
                        .object(["input": .string("AI word"), "output": .string(ai), "kind": .string("dict"),
                                 "sig": .string("model-prompt-signature")])
                     ], total: 2))
        for row in model.historyPage {
            for scheme in [ColorScheme.light, .dark] {
                let png = try render(
                    HistoryTranslationDetail(model: model, row: row, useEntry: {}),
                    named: "history-detail-\(row.isLocalDictionary ? "local" : "ai")-\(scheme == .light ? "light" : "dark")",
                    size: NSSize(width: 650, height: 500), scheme: scheme, inspect: { host in
                        let expected = row.isLocalDictionary ? local : "Model meaning\n\nReadable model explanation."
                        let rendered = self.textViews(in: host).first { $0.string == expected }
                        XCTAssertNotNil(rendered)
                        XCTAssertEqual(rendered?.isEditable, false)
                        XCTAssertEqual(rendered?.isSelectable, true)
                    })
                let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
                let words = try LocalOCR.recognize(image).text.lowercased()
                XCTAssertTrue(words.contains(row.isLocalDictionary ? "wordnet" : "explanation"))
                XCTAssertTrue(words.contains("copy"))
                XCTAssertTrue(words.contains("reuse"))
            }
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }

    @MainActor
    func testHistoryOwnWindowCloseNotificationCancelsDebounceButAnotherWindowDoesNot() async throws {
        _ = NSApplication.shared
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [], total: 0))
        let host = NSHostingView(rootView: TranslationHistoryView(model: model, useEntry: {}))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 680),
                              styleMask: .borderless, backing: .buffered, defer: false)
        let other = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300),
                             styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        other.isReleasedWhenClosed = false
        window.contentView = host
        defer {
            window.contentView = nil
            window.close()
            other.close()
        }
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        try await Task.sleep(nanoseconds: 50_000_000)
        model.historySearch = "survives unrelated close"
        NotificationCenter.default.post(name: NSWindow.willCloseNotification, object: other)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "survives unrelated close")
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [], total: 0))
        model.historySearch = "cancel on owning window close"
        NotificationCenter.default.post(name: NSWindow.willCloseNotification, object: window)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(model.historyPhase, .idle)
        model.historySearch = "late binding after close"
        model.historyFilter = "dict"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(model.historyPhase, .idle)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }

    @MainActor
    func testSettingsRendersLanguageThemeAndCLIWithoutPermissionsOrModelCalls() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.appearance = "light"
        let light = try render(
            TranslationSettingsView(model: model, showDiagnostics: {}),
            named: "settings-light", size: NSSize(width: 820, height: 860), scheme: .light)
        model.interfaceLanguage = "zh"
        model.appearance = "dark"
        let dark = try render(
            TranslationSettingsView(model: model, showDiagnostics: {}),
            named: "settings-dark-zh", size: NSSize(width: 820, height: 860), scheme: .dark)

        XCTAssertNotEqual(light, dark)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertFalse(model.cliBusy)
        XCTAssertFalse(model.monitorEnabled)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testResultRendersFullAndCompactNativeViewsInBothAppearances() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        model.reuseHistory(ProbeModel.HistoryRow(
            id: "synthetic-result", input: "Synthetic source text",
            output: "Synthetic translated text.\n\nA second paragraph checks wrapping and scrolling."))
        let full = try render(
            TranslationResultView(model: model, compact: false),
            named: "result-full-light", size: NSSize(width: 760, height: 560), scheme: .light)
        let compact = try render(
            TranslationResultView(model: model, compact: true),
            named: "result-compact-dark", size: NSSize(width: 460, height: 420), scheme: .dark)
        model.reuseHistory(ProbeModel.HistoryRow(
            id: "synthetic-word", input: "example", output: "**An illustrative synthetic instance.**",
            kind: "dict"))
        _ = try render(
            TranslationResultView(model: model, compact: true),
            named: "result-dictionary-light", size: NSSize(width: 460, height: 420), scheme: .light,
            inspect: { host in
                XCTAssertTrue(self.textViews(in: host).contains { $0.string == "An illustrative synthetic instance." },
                              "AI dictionary history retains Markdown presentation.")
            })

        XCTAssertNotEqual(full, compact)
        XCTAssertEqual(model.resultKind, "dict")
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty })
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testLocalDictionarySensesAndSourcesAreReadableAndLiteralInBothAppearances() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.input = "example"
        fixture.model.translate()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: DictionaryModelTests.hit())
        for scheme in [ColorScheme.light, .dark] {
            let png = try render(
                TranslationResultView(model: fixture.model, compact: true),
                named: "local-dictionary-senses-\(scheme == .light ? "light" : "dark")",
                size: NSSize(width: 620, height: 510), scheme: scheme, inspect: { host in
                    let view = self.textViews(in: host).first { $0.string == DictionaryModelTests.senses }
                    XCTAssertNotNil(view)
                    XCTAssertEqual(view?.isEditable, false)
                    XCTAssertEqual(view?.isSelectable, true)
                })
            let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
            let recognized = try LocalOCR.recognize(image).text.lowercased()
            XCTAssertTrue(recognized.contains("representative"))
            XCTAssertTrue(recognized.contains("imitate"))
            XCTAssertTrue(recognized.contains("wordnet"))
            XCTAssertTrue(recognized.contains("copy"))
        }
        let literal = "example\n**literal markers** <source>\n" + String(repeating: "Complete sense.\n", count: 1000)
        fixture.model.reuseHistory(.init(id: "literal", input: "example", output: literal, kind: "dict",
                                        signature: "local-dictionary|fixture|native-plain-v1:en_US"))
        _ = try render(TranslationResultView(model: fixture.model, compact: true),
                       named: "local-dictionary-complete-scroll", size: NSSize(width: 420, height: 360),
                       scheme: .light, inspect: { host in
            XCTAssertTrue(self.textViews(in: host).contains { $0.string == literal },
                          "Complete senses must remain selectable; Markdown-like source text is never removed.")
        })
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testDictionarySettingsRenderWithoutDownloadsOrDeletionUntilExplicitAction() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(savedCLI: false, dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.refreshDictionary()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        for scheme in [ColorScheme.light, .dark] {
            let png = try render(
                Form { DictionarySettingsSection(model: fixture.model, dictionary: fixture.model.dictionary) }
                    .formStyle(.grouped),
                named: "local-dictionary-settings-\(scheme == .light ? "light" : "dark")",
                size: NSSize(width: 650, height: 700), scheme: scheme)
            let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
            let text = try LocalOCR.recognize(image).text.lowercased()
            XCTAssertTrue(text.contains("dictionary"))
            XCTAssertTrue(text.contains("delete"))
            XCTAssertTrue(text.contains("license"))
        }
        XCTAssertTrue(downloader.tickets.isEmpty)
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request == .delete || $0.request == .prepareInstall })
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    private func textViews(in view: NSView) -> [NSTextView] {
        (view as? NSTextView).map { [$0] } ?? view.subviews.flatMap { textViews(in: $0) }
    }

    // This paints the actual SwiftUI/AppKit view in memory. It is not a screen capture,
    // human GUI acceptance test, or evidence of Accessibility/Screen Recording permission.
    @MainActor
    private func render<Content: View>(_ content: Content, named name: String, size: NSSize,
                                      scheme: ColorScheme, inspect: ((NSView) -> Void)? = nil) throws -> Data {
        _ = NSApplication.shared
        let host = NSHostingView(rootView: content.environment(\.colorScheme, scheme))
        let appearance = try XCTUnwrap(NSAppearance(named: scheme == .dark ? .darkAqua : .aqua))
        // Native text/list controls get window backing, but the window is never ordered onscreen.
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = appearance
        window.contentView = host
        defer {
            window.contentView = nil
            window.close()
        }
        host.appearance = appearance
        host.frame = NSRect(origin: .zero, size: size)
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        inspect?(host)
        XCTAssertEqual(host.bounds.size, size)
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        appearance.performAsCurrentDrawingAppearance {
            host.cacheDisplay(in: host.bounds, to: bitmap)
        }
        XCTAssertGreaterThanOrEqual(bitmap.pixelsWide, Int(size.width))
        XCTAssertGreaterThanOrEqual(bitmap.pixelsHigh, Int(size.height))

        var colors = Set<UInt32>()
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: max(1, bitmap.pixelsHigh / 100)) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: max(1, bitmap.pixelsWide / 100)) {
                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
                let r = UInt32(min(255, max(0, color.redComponent * 255)))
                let g = UInt32(min(255, max(0, color.greenComponent * 255)))
                let b = UInt32(min(255, max(0, color.blueComponent * 255)))
                colors.insert((r << 16) | (g << 8) | b)
            }
        }
        XCTAssertGreaterThan(colors.count, 8, "A blank or solid-color bitmap is not a rendered product view.")
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        XCTAssertGreaterThan(png.count, 1_000)
        XCTAssertEqual(Array(png.prefix(8)), [137, 80, 78, 71, 13, 10, 26, 10])

        if let path = ProcessInfo.processInfo.environment["CC_TRANSLATE_UI_SCREENSHOTS_DIR"],
           !path.isEmpty {
            let directory = URL(fileURLWithPath: path, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let destination = directory.appendingPathComponent(name).appendingPathExtension("png")
            try png.write(to: destination, options: .atomic)
            XCTAssertEqual(try Data(contentsOf: destination), png)
        }
        return png
    }
}
