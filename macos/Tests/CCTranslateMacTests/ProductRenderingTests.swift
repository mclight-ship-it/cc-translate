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
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id), payload: [
            "entries": .array([
                ProductTestHarness.historyEntry(
                    input: "Synthetic history sentence", output: "合成历史句子"),
                ProductTestHarness.historyEntry(
                    input: "example", output: "A synthetic dictionary definition.", kind: "dict"),
                ProductTestHarness.historyEntry(
                    input: "Another synthetic history sentence", output: "另一个合成历史句子")
            ]),
            "revision": .string("rendering-fixture"), "next_cursor": .null
        ])
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
            id: "synthetic-word", input: "example", output: "An illustrative synthetic instance.",
            kind: "dict"))
        _ = try render(
            TranslationResultView(model: model, compact: true),
            named: "result-dictionary-light", size: NSSize(width: 460, height: 420), scheme: .light)

        XCTAssertNotEqual(full, compact)
        XCTAssertEqual(model.resultKind, "dict")
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty })
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    // This paints the actual SwiftUI/AppKit view in memory. It is not a screen capture,
    // human GUI acceptance test, or evidence of Accessibility/Screen Recording permission.
    @MainActor
    private func render<Content: View>(_ content: Content, named name: String, size: NSSize,
                                      scheme: ColorScheme) throws -> Data {
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
