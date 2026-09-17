import XCTest
import AppKit
import SwiftUI
import Vision
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testCustomModelDraftAndConfirmedSettingRenderInLightAndDarkWithoutModelRequests() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady(configuration: ProductTestHarness.configuration(model: "provider/Saved-V1"))
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.editCustomModelID("provider/Exact-V2")
        for scheme in [ColorScheme.light, .dark] {
            let png = try renderModelSettings(model, name: "custom-model-draft-\(scheme == .light ? "light" : "dark")",
                                              scheme: scheme, inspect: { host in
                XCTAssertTrue(self.modelTextFields(in: host).contains {
                    $0.isEditable && $0.stringValue == "provider/Exact-V2"
                })
            })
            let words = try modelWords(png)
            XCTAssertTrue(words.contains("custom model id"))
            XCTAssertTrue(words.contains("apply model"))
            XCTAssertTrue(words.contains("unapplied draft"))
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        model.applyCustomModelID()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        let png = try renderModelSettings(model, name: "custom-model-confirmed", scheme: .light)
        XCTAssertTrue(try modelWords(png).contains("saved and read back"))
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertFalse(model.cliBusy)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testCustomModelSaveReadbackAndMismatchRenderWithoutClaimingFallbackSuccess() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.editCustomModelID("provider/Requested-ID")
        model.applyCustomModelID()
        let saving = try renderModelSettings(model, name: "custom-model-saving", scheme: .dark)
        XCTAssertTrue(try modelWords(saving).contains("saving"))
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("completed", id: save.id)
        let reading = try renderModelSettings(model, name: "custom-model-readback", scheme: .dark)
        XCTAssertTrue(try modelWords(reading).contains("reading back"))
        try fixture.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(model: "auto"))
        let failure = try renderModelSettings(model, name: "custom-model-readback-failure", scheme: .light)
        let words = try modelWords(failure)
        XCTAssertTrue(words.contains("requested"))
        XCTAssertTrue(words.contains("no model substitution"))
        XCTAssertEqual(model.modelProfile, "provider/Requested-ID")
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testLiteralMiniCustomIDRendersAndAppliesInChineseNativeSettings() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        var configuration = ProductTestHarness.configuration()
        configuration["codex_model_default_migrated"] = .bool(true)
        let helper = try fixture.localReady(configuration: configuration)
        fixture.model.interfaceLanguage = "zh"
        fixture.model.editCustomModelID("gpt-5.4-mini")
        let png = try renderModelSettings(fixture.model, name: "custom-model-mini-zh",
                                         scheme: .dark, size: NSSize(width: 530, height: 560),
                                         inspect: { host in
            XCTAssertTrue(self.modelTextFields(in: host).contains { $0.isEditable && $0.stringValue == "gpt-5.4-mini" })
        })
        let words = try modelWords(png, language: "zh-Hans").filter { !$0.isWhitespace }
        XCTAssertTrue(words.contains("模型"))
        XCTAssertTrue(words.contains("gpt-5.4-mini"))
        XCTAssertNil(CodexModelSettings.validateCustom(fixture.model.modelSettings.draft))
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        fixture.model.applyCustomModelID()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        XCTAssertEqual(save.config["codex_model"], .string("gpt-5.4-mini"))
        XCTAssertEqual(save.config["codex_model_default_migrated"], .bool(true))
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(fixture.model.modelSettings.phase, .applied("gpt-5.4-mini"))
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testCustomModelEditorKeepsNativeMarkedTextAndDoesNotBindCommandReturnToApply() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.interfaceLanguage = "en"
        fixture.model.editCustomModelID("provider/Editable-ID")
        _ = try renderModelSettings(fixture.model, name: "custom-model-keyboard", scheme: .light,
                                    inspect: { host in
            guard let field = self.modelTextFields(in: host).first(where: { $0.isEditable }),
                  let window = host.window else {
                XCTFail("The real native model text field and its window must exist.")
                return
            }
            XCTAssertTrue(window.makeFirstResponder(field))
            guard let editor = field.currentEditor() as? NSTextView else {
                XCTFail("The model ID uses the native field editor, not a custom key interceptor.")
                return
            }
            editor.setMarkedText("拼", selectedRange: NSRange(location: 1, length: 0),
                                 replacementRange: NSRange(location: NSNotFound, length: 0))
            XCTAssertTrue(editor.hasMarkedText())
            guard let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command,
                                               timestamp: 0, windowNumber: window.windowNumber, context: nil,
                                               characters: "\r", charactersIgnoringModifiers: "\r",
                                               isARepeat: false, keyCode: 36) else {
                XCTFail("Could not construct a window-local shortcut fixture.")
                return
            }
            XCTAssertFalse(host.performKeyEquivalent(with: event))
            XCTAssertTrue(editor.hasMarkedText(), "The view must not swallow native composition via a global Apply shortcut.")
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
            editor.unmarkText()
        })
    }

    @MainActor
    private func renderModelSettings(_ model: ProbeModel, name: String, scheme: ColorScheme,
                                     size: NSSize = NSSize(width: 640, height: 560),
                                     inspect: ((NSView) -> Void)? = nil) throws -> Data {
        try render(CodexModelSettingsView(model: model).padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(Color(nsColor: .windowBackgroundColor)),
                   named: name, size: size, scheme: scheme, inspect: inspect)
    }

    @MainActor
    private func modelTextFields(in view: NSView) -> [NSTextField] {
        (view as? NSTextField).map { [$0] } ?? view.subviews.flatMap { modelTextFields(in: $0) }
    }

    private func modelWords(_ png: Data, language: String = "en-US") throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = [language]
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined(separator: " ").lowercased().split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}
