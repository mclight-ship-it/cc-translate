import XCTest
import AppKit
import SwiftUI
import Vision
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct InputLimitSettingsSurface: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Form {
            Section {
                InputLimitSettingsView(model: model)
            } header: {
                Text(model.text("Input length", "输入长度"))
            }
        }
        .formStyle(.grouped)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

@MainActor
private enum InputLimitNativeViews {
    static func views<T: NSView>(_ type: T.Type, in root: NSView) -> [T] {
        (root as? T).map { [$0] } ?? root.subviews.flatMap { views(type, in: $0) }
    }

    static func field(in root: NSView) throws -> NSTextField {
        let fields = views(NSTextField.self, in: root).filter { $0.isEditable }
        XCTAssertEqual(fields.count, 1)
        return try XCTUnwrap(fields.count == 1 ? fields.first : nil)
    }

    static func button(in root: NSView, id: String, label: String) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(in: root, identifier: id, label: label, kind: .button)
    }

    static func assertVisible(_ control: any NativeRenderedTestRegion, file: StaticString = #filePath,
                              line: UInt = #line) {
        XCTAssertGreaterThan(control.visibleRect.height, 0, file: file, line: line)
        XCTAssertEqual(control.visibleRect.height, control.frame.height, accuracy: 1, file: file, line: line)
        XCTAssertEqual(control.visibleRect.width, control.frame.width, accuracy: 1, file: file, line: line)
    }
}

@MainActor
final class NativeSettingsTestHost<Content: View> {
    private let windowFocus = NativeTestWindowFocus()
    let host: NSHostingView<Content>
    let window: NSWindow

    init(_ content: Content, size: NSSize = NSSize(width: 760, height: 720)) {
        _ = NSApplication.shared
        host = NSHostingView(rootView: content)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .titled,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        window.makeKeyAndOrderFront(nil)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func waitFor(file: StaticString = #filePath, line: UInt = #line,
                 diagnostics: @MainActor () -> String = { "" },
                 _ condition: @MainActor () -> Bool) async throws {
        try await CaptureProductFixture.waitFor(file: file, line: line, diagnostics: diagnostics) {
            self.flush()
            return condition()
        }
    }

    func enterLimit(_ text: String, model: ProbeModel) async throws {
        try await enterValue(text, draft: { model.inputLimit.draft })
    }

    func visibleButtons() -> [NSButton] {
        InputLimitNativeViews.views(NSButton.self, in: host).filter {
            !$0.isHiddenOrHasHiddenAncestor &&
                !host.convert($0.visibleRect, from: $0).intersection(host.visibleRect).isEmpty
        }
    }

    func waitForFieldValue(_ text: String) async throws {
        try await waitFor {
            let fields = InputLimitNativeViews.views(NSTextField.self, in: self.host)
                .filter { $0.isEditable && $0.isEnabled }
            return fields.count == 1 && fields.first?.stringValue == text
        }
    }

    func enterValue(_ text: String, draft: @MainActor () -> String) async throws {
        flush()
        let field = try InputLimitNativeViews.field(in: host)
        XCTAssertTrue(window.makeFirstResponder(field))
        let editor = try XCTUnwrap(field.currentEditor() as? NSTextView)
        XCTAssertTrue(window.firstResponder === editor)
        editor.selectAll(nil)
        editor.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertTrue(window.makeFirstResponder(nil))
        try await waitFor { draft() == text }
    }

    func editor() throws -> NSTextView {
        flush()
        let editors = InputLimitNativeViews.views(NSTextView.self, in: host).filter { $0.isEditable }
        XCTAssertEqual(editors.count, 1)
        return try XCTUnwrap(editors.count == 1 ? editors.first : nil)
    }

    func enterText(_ text: String) throws -> NSTextView {
        let editor = try editor()
        XCTAssertTrue(window.makeFirstResponder(editor))
        XCTAssertTrue(window.firstResponder === editor)
        editor.selectAll(nil)
        editor.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        return editor
    }

    func button(_ id: String, _ label: String) throws -> NativeSettingsTestControl {
        flush()
        return try InputLimitNativeViews.button(in: host, id: id, label: label)
    }

    func buttonWhenReady(_ id: String, _ label: String) async throws -> NativeSettingsTestControl {
        flush()
        return try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: id, label: label, kind: .button)
    }

    func close() {
        windowFocus.close(window)
    }
}

final class InputLimitInteractionTests: XCTestCase {
    @MainActor
    func testNativeEntryApplyReadbackAndReopenOfflineInEnglishAndChinese() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 20_001))
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(InputLimitSettingsSurface(model: f.model))
            defer { surface.close() }
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "20001")
            let operations = helper.operations
            try await surface.enterLimit("0", model: f.model)
            let label = f.model.text("Apply input limit", "应用输入上限")
            XCTAssertFalse(try surface.button("apply-input-limit", label).isEnabled)
            try await surface.enterLimit("17", model: f.model)
            XCTAssertEqual(helper.operations, operations, "Typing is not a configuration save.")
            try await surface.button("apply-input-limit", label).press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            XCTAssertEqual(f.model.inputLimit.saved, 20_001)
            let save = try XCTUnwrap(helper.configurationSaves.last)
            XCTAssertEqual(save.config["max_chars"], .integer(17))
            helper.event("completed", id: save.id)
            XCTAssertEqual(f.model.inputLimit.saved, 20_001)
            try f.finishConfiguration(on: helper, configuration: save.config)
            try await surface.waitFor { f.model.inputLimit.phase == .saved }
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "17")
            f.model.stopHelper()
            helper.stopped()
            f.model.openProduct()
            let reopened = try f.ready(configuration: save.config)
            try await surface.waitFor { f.model.canEditInputLimit }
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "17")
            XCTAssertEqual(f.model.inputLimit.saved, 17)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(reopened.configurationSaves.isEmpty)
            XCTAssertTrue(reopened.translations.isEmpty)
        }
    }

    @MainActor
    func testNativeInvalidSavedValueCorrectionFailureReloadAndMismatchKeepDraft() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: -7))
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(InputLimitSettingsSurface(model: f.model))
            defer { surface.close() }
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "-7")
            try await surface.enterLimit("20001", model: f.model)
            let apply = f.model.text("Apply input limit", "应用输入上限")
            try await surface.button("apply-input-limit", apply).press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                         payload: ["code": .string("config_io_failed")])
            try await surface.waitFor { f.model.inputLimit.saved == nil }
            XCTAssertFalse(try surface.button("apply-input-limit", apply).isEnabled)
            try await surface.buttonWhenReady("reload-input-limit",
                               f.model.text("Reload saved input limit", "重新读取已保存输入上限")).press()
            try await surface.waitFor { helper.configurationLoads.count == 2 }
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { f.model.canEditInputLimit }
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "20001")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            try await surface.button("apply-input-limit", apply).press()
            try await surface.waitFor { helper.configurationSaves.count == 2 }
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(maxChars: 20_000))
            try await surface.waitFor { f.model.inputLimit.phase == .differentReadback }
            XCTAssertEqual(f.model.inputLimit.saved, 20_000)
            XCTAssertEqual(try InputLimitNativeViews.field(in: surface.host).stringValue, "20001")
            XCTAssertEqual(helper.configurationSaves.count, 2)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.historyLoads.isEmpty)
        }
    }

    @MainActor
    func testMainNativeEditorAndTranslateActionUseScalarLimitWithoutRewritingUserText() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 1))
        f.model.interfaceLanguage = "en"
        let surface = NativeSettingsTestHost(
            TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
            size: NSSize(width: 960, height: 720))
        defer { surface.close() }
        let editor = try surface.enterText("e\u{301}")
        try await surface.waitFor { f.model.input.unicodeScalars.count == 2 }
        XCTAssertFalse(try surface.button("translate-input-text", "Translate").isEnabled)
        XCTAssertEqual(Array(editor.string.utf8), Array("e\u{301}".utf8))
        let selection = editor.selectedRange()
        let viewport = editor.enclosingScrollView?.contentView.bounds.origin
        let settings = NativeSettingsTestHost(InputLimitSettingsSurface(model: f.model))
        defer { settings.close() }
        try await settings.enterLimit("2", model: f.model)
        try await settings.button("apply-input-limit", "Apply input limit").press()
        try await settings.waitFor { helper.configurationSaves.count == 1 }
        try InputLimitFixture.finish(f, helper)
        let translate = try surface.button("translate-input-text", "Translate")
        try await surface.waitFor { f.model.inputLimit.saved == 2 && translate.isEnabled }
        XCTAssertTrue(translate.isEnabled)
        XCTAssertTrue(try surface.editor() === editor)
        XCTAssertEqual(Array(editor.string.utf8), Array("e\u{301}".utf8))
        XCTAssertEqual(editor.selectedRange(), selection)
        XCTAssertEqual(editor.enclosingScrollView?.contentView.bounds.origin, viewport)
        XCTAssertTrue(helper.translations.isEmpty)
        try await surface.button("translate-input-text", "Translate").press()
        try await surface.waitFor { helper.translations.count == 1 }
        XCTAssertEqual(Array(try XCTUnwrap(helper.translations.last?.text).utf8), Array("e\u{301}".utf8))
        XCTAssertTrue(helper.messages.isEmpty)
    }

    @MainActor
    func testOCRNativeEditorAndTranslateTextActionUseSameLimitInChinese() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(language: "zh_CN", maxChars: 1))
        f.model.interfaceLanguage = "zh"
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let screen = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(text: "é") },
                                 notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let surface = NativeSettingsTestHost(CaptureView(capture: capture, model: f.model,
                                                       captureAgain: {}, reselect: {}, close: {}),
                                           size: NSSize(width: 980, height: 900))
        defer { surface.close() }
        let editor = try surface.enterText("e\u{301}")
        try await surface.waitFor { capture.text.unicodeScalars.count == 2 }
        XCTAssertFalse(try surface.button("translate-capture-text", "翻译文字").isEnabled)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(Array(editor.string.utf8), Array("e\u{301}".utf8))
        _ = try surface.enterText("é")
        try await surface.waitFor { capture.text.unicodeScalars.count == 1 }
        try await surface.button("translate-capture-text", "翻译文字").press()
        try await surface.waitFor { helper.translations.count == 1 }
        XCTAssertEqual(helper.translations.last?.text, "é")
        XCTAssertEqual(helper.translations.last?.origin, "ocr")
        XCTAssertEqual(helper.translations.last?.useCache, false)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
    }
}

extension ProductRenderingTests {
    @MainActor
    func testInputLimitSettingsRenderLargeSavedValueAndSeparateByteBudgetInEnglishLight() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 20_001))
        f.model.interfaceLanguage = "en"
        f.model.editInputLimit("20000")
        let image = try render(InputLimitSettingsSurface(model: f.model), named: "input-limit-settings-en-light",
                               size: NSSize(width: 760, height: 720), scheme: .light, inspect: { host in
            XCTAssertEqual(try InputLimitNativeViews.field(in: host).stringValue, "20000")
            let apply = try InputLimitNativeViews.button(in: host, id: "apply-input-limit", label: "Apply input limit")
            XCTAssertTrue(apply.isEnabled)
            InputLimitNativeViews.assertVisible(apply)
        })
        let words = try inputLimitWords(image, chinese: false)
        for expected in ["20001", "20000", "8192", "codepoints", "combining", "doesnotincrease", "applyinputlimit"] {
            XCTAssertTrue(words.contains(expected), words)
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testCaptureInputCountersRenderBothIndependentLimitsAndVisibleActionInChineseDark() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 5000))
        f.model.interfaceLanguage = "zh"
        f.model.appearance = "dark"
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let screen = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(text: String(repeating: "中", count: 2731)) },
                                 notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let image = try render(CaptureView(capture: capture, model: f.model, captureAgain: {}, reselect: {}, close: {}),
                               named: "input-limits-capture-zh-dark", size: NSSize(width: 980, height: 900),
                               scheme: .dark, inspect: { host in
            // Keep a failing lookup as a failure while retaining its actual review bitmap.
            XCTAssertNoThrow(try {
                let button = try InputLimitNativeViews.button(in: host, id: "translate-capture-text", label: "翻译文字")
                XCTAssertFalse(button.isEnabled)
                InputLimitNativeViews.assertVisible(button)
                for (id, label) in [("input-code-point-count", "2731 / 5000 Unicode 码点"),
                                    ("input-byte-count", "8193 / 8,192 UTF-8 字节")] {
                    let count = try NativeSettingsTestControls.caption(in: host, identifier: id, label: label)
                    InputLimitNativeViews.assertVisible(count)
                }
            }())
        })
        let words = try inputLimitWords(image, chinese: true)
        try NativeRenderEvidence.record("Synthetic capture counter OCR (repeated input omitted): \(words.replacingOccurrences(of: "中", with: ""))")
        for expected in ["2731", "5000", "8193", "8192", "码点", "字节", "翻译文字"] {
            XCTAssertTrue(words.contains(expected), words)
        }
        XCTAssertEqual(capture.text.unicodeScalars.count, 2731)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    private func inputLimitWords(_ png: Data, chinese: Bool) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.minimumTextHeight = 0
        request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: NativeRenderEvidence.recognitionImage(image)).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined().lowercased().filter { !$0.isWhitespace && $0 != "," }
    }
}
