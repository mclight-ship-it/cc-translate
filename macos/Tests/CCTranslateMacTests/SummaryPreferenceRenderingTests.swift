import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct SummarySettingsSurface: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Form {
            TranslationSettingsView(model: model, showDiagnostics: {}, showAbout: {}).translationSection
        }
        .formStyle(.grouped)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

@MainActor
private enum SummarySettingsControls {
    static func toggle(in root: NSView, model: ProbeModel) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(
            in: root, identifier: "automatic-long-text-summary",
            label: model.text("Automatic long-text summary", "长文自动摘要"), kind: .toggle)
    }

    static func reload(in root: NSView, model: ProbeModel) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(
            in: root, identifier: "reload-summary-setting",
            label: model.text("Reload saved summary setting", "重新读取已保存的摘要设置"), kind: .button)
    }
}

@MainActor
private final class SummarySettingsHost {
    private let windowFocus = NativeTestWindowFocus()
    let host: NSHostingView<SummarySettingsSurface>
    let window: NSWindow
    let model: ProbeModel

    init(model: ProbeModel) {
        _ = NSApplication.shared
        self.model = model
        host = NSHostingView(rootView: SummarySettingsSurface(model: model))
        let size = NSSize(width: 640, height: 860)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .titled,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func waitFor(file: StaticString = #filePath, line: UInt = #line,
                 diagnostics: @MainActor () -> String = { "" },
                 _ condition: @MainActor () -> Bool) async throws {
        try await CaptureProductFixture.waitFor(file: file, line: line, diagnostics: {
            "Summary enabled=\(String(describing: self.model.summaryEnabled)), " +
                "phase=\(self.model.summaryPreferencePhase), busy=\(self.model.settingsBusy); \(diagnostics())"
        }) {
            self.flush()
            return condition()
        }
    }

    func toggle() throws -> NativeSettingsTestControl {
        flush()
        return try SummarySettingsControls.toggle(in: host, model: model)
    }

    func reload() async throws -> NativeSettingsTestControl {
        flush()
        return try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: "reload-summary-setting",
            label: model.text("Reload saved summary setting", "重新读取已保存的摘要设置"), kind: .button)
    }

    func close() {
        windowFocus.close(window)
    }
}

final class SummaryPreferenceInteractionTests: XCTestCase {
    @MainActor
    func testEnglishAndChineseNativeCheckboxSavesReadbacksAndReopensWithoutCLI() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = SummarySettingsHost(model: f.model)
            defer { surface.close() }
            let button = try surface.toggle()
            XCTAssertTrue(button.isEnabled)
            XCTAssertEqual(button.state, .on)
            surface.window.makeKeyAndOrderFront(nil)
            try button.focus(in: surface.window)
            try await surface.waitFor { button.isFocused }
            let source = f.model.input
            try await button.press()
            try await surface.waitFor(diagnostics: {
                "saves=\(helper.configurationSaves.count), controlEnabled=\(button.isEnabled), state=\(String(describing: button.state))"
            }) { helper.configurationSaves.count == 1 && !button.isEnabled }
            let save = try XCTUnwrap(helper.configurationSaves.last)
            XCTAssertEqual(save.config["summary_enabled"], .bool(false))
            XCTAssertEqual(f.model.summaryEnabled, true)
            XCTAssertEqual(f.model.summaryPreferencePhase, .saving)
            helper.event("completed", id: save.id)
            try await surface.waitFor { f.model.summaryPreferencePhase == .readingBack && !button.isEnabled }
            try f.finishConfiguration(on: helper, configuration: save.config)
            try await surface.waitFor { button.state == .off && button.isEnabled }
            XCTAssertTrue(try surface.toggle().sameElement(as: button), "Keep the same real control through save/readback.")
            XCTAssertEqual(f.model.summaryPreferencePhase, .saved)
            XCTAssertEqual(f.model.input, source)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.resultActions.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)
            XCTAssertFalse(f.model.cliBusy)
            XCTAssertEqual(f.model.permissions, "Not checked.")

            f.model.stopHelper()
            helper.stopped()
            f.model.openProduct()
            let reopened = try f.ready(configuration: save.config)
            try await surface.waitFor { button.state == .off && button.isEnabled }
            try await button.press()
            try await surface.waitFor { reopened.configurationSaves.count == 1 }
            try SummaryPreferenceFixture.completeSave(f, helper: reopened)
            try await surface.waitFor { button.state == .on && button.isEnabled }
            XCTAssertEqual(f.model.summaryEnabled, true)
            XCTAssertTrue(reopened.translations.isEmpty)
            XCTAssertTrue(reopened.resultActions.isEmpty)
        }
    }

    @MainActor
    func testNativeReloadRecoversFailedSaveAndMismatchWithoutImplicitRewriteInBothLanguages() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = SummarySettingsHost(model: f.model)
            defer { surface.close() }
            let button = try surface.toggle()
            try await button.press()
            try await surface.waitFor(diagnostics: { "saves=\(helper.configurationSaves.count)" }) {
                helper.configurationSaves.count == 1
            }
            helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                         payload: ["code": .string("config_io_failed")])
            try await surface.waitFor { !button.isEnabled && f.model.summaryEnabled == nil }
            let reload = try await surface.reload()
            XCTAssertTrue(reload.isEnabled)
            surface.window.makeKeyAndOrderFront(nil)
            try reload.focus(in: surface.window)
            try await surface.waitFor { reload.isFocused }
            try await reload.press()
            try await surface.waitFor { helper.configurationLoads.count == 2 }
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { button.state == .on && button.isEnabled }
            XCTAssertEqual(helper.configurationSaves.count, 1)
            try await button.press()
            try await surface.waitFor { helper.configurationSaves.count == 2 }
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { button.isEnabled && button.state == .on }
            XCTAssertEqual(f.model.summaryPreferencePhase, .differentReadback)
            XCTAssertEqual(helper.configurationSaves.count, 2, "Mismatch is shown, not silently fixed with another save.")
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testAutomaticSummarySettingRendersConfirmedEnglishLightAndChineseDark() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for (language, scheme, name) in [
            ("en", ColorScheme.light, "summary-setting-en-light"),
            ("zh", .dark, "summary-setting-zh-dark")
        ] {
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .dark ? "dark" : "light"
            let png = try render(SummarySettingsSurface(model: f.model), named: name,
                                 size: NSSize(width: 640, height: 860), scheme: scheme, inspect: { host in
                let button = try SummarySettingsControls.toggle(in: host, model: f.model)
                XCTAssertTrue(button.isEnabled)
                XCTAssertEqual(button.state, .on)
                XCTAssertGreaterThan(button.visibleRect.height, 0)
            }, highResolution: true)
            let words = try summarySettingsWords(png, chinese: language == "zh")
            for expected in language == "zh"
                ? ["长文自动摘要", "保存翻译历史记录"]
                : ["automaticlong-textsummary", "savetranslationhistory"] {
                XCTAssertTrue(words.contains(expected), words)
            }
            XCTAssertFalse(words.contains("400"), "The implementation threshold belongs in help, not the normal form.")
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationLoads.count, 1)
        let negative = try render(
            Text("结果中的“生成译文”操作仍可使用。").font(.caption).padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .windowBackgroundColor)),
            named: "summary-setting-negative-control-zh-dark",
            size: NSSize(width: 640, height: 90), scheme: .dark, highResolution: true)
        let negativeWords = try summarySettingsWords(negative, chinese: true)
        XCTAssertTrue(negativeWords.contains("生成译文"), negativeWords)
        XCTAssertFalse(negativeWords.contains("生成摘要"), negativeWords)
    }

    @MainActor
    func testAutomaticSummaryFailureRendersUnknownStateAndExplicitRecoveryWithoutFalseSuccess() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.interfaceLanguage = "en"
        f.model.saveSummaryPreference(false)
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        let png = try render(SummarySettingsSurface(model: f.model),
                             named: "summary-setting-save-failed-en-light",
                             size: NSSize(width: 640, height: 860), scheme: .light, inspect: { host in
            XCTAssertFalse(try SummarySettingsControls.toggle(in: host, model: f.model).isEnabled)
            XCTAssertTrue(try SummarySettingsControls.reload(in: host, model: f.model).isEnabled)
        })
        let words = try summarySettingsWords(png)
        XCTAssertTrue(words.contains("couldnotbeconfirmed"), words)
        XCTAssertTrue(words.contains("reloadsavedsummarysetting"), words)
        XCTAssertFalse(words.contains("savedandreadback"), words)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 1)
    }

    @MainActor
    private func summarySettingsWords(_ png: Data, chinese: Bool = false) throws -> String {
        try NativeRenderEvidence.settingsWords(png, chinese: chinese).filter { !$0.isWhitespace }
    }
}
