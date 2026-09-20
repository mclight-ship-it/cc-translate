import XCTest
import AppKit
import SwiftUI
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testPlainPasteFullSettingsRenderNativeOwnAppDispatchAndMissingEditorWithoutExternalPaste() throws {
        func stage(_ value: String) throws {
            try NativeRenderEvidence.record("Synthetic paste render stage: \(value)")
        }
        try stage("starting")
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.routing.foreground = .ownApplication
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        let native = try renderPasteSettings(fixture, name: "plain-paste-settings-own-native-light", scheme: .light)
        try stage("native rendered")
        let words = try pasteSettingsWords(native)
        try stage("native recognized")
        XCTAssertTrue(words.contains("native paste and match style was dispatched"))
        XCTAssertFalse(words.contains("pasted successfully"))
        fixture.routing.handlesNativePaste = false
        lease.fire(.released)
        lease.fire(.pressed)
        let unavailable = try renderPasteSettings(fixture, name: "plain-paste-settings-own-unavailable-zh-dark",
                                                 scheme: .dark, chinese: true)
        try stage("unavailable rendered")
        let chinese = try pasteSettingsWords(unavailable, chinese: true).filter { !$0.isWhitespace }
        try NativeRenderEvidence.record("Synthetic paste settings OCR: \(chinese)")
        XCTAssertTrue(chinese.contains("没有原生编辑器可处理"), chinese)
        XCTAssertTrue(chinese.contains("未请求外部粘贴"), chinese)
        XCTAssertTrue(chinese.contains("文件和仅含图片的内容保持不变"), chinese)
        XCTAssertTrue(chinese.contains("辅助功能权限"), chinese)
        XCTAssertEqual(fixture.routing.nativePastes, 2)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        assertPasteRenderHasNoExternalEffects(fixture)
    }

    @MainActor
    func testPlainPasteFullSettingsRenderOffAndEnabledInBothAppearancesWithoutPaste() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        var images: [Data] = []
        for enabled in [false, true] {
            if enabled {
                fixture.model.setPlainPasteEnabled(true)
                _ = try fixture.finishSave()
            }
            for scheme in [ColorScheme.light, .dark] {
                let png = try renderPasteSettings(fixture, name:
                    "plain-paste-settings-\(enabled ? "enabled" : "off")-\(scheme == .light ? "light" : "dark")",
                    scheme: scheme)
                images.append(png)
                let words = try pasteSettingsWords(png)
                XCTAssertTrue(words.contains("appearance"), "Keep the complete settings navigation, not an isolated paste control.")
                XCTAssertFalse(words.contains("custom model id"), "Model controls belong to Translation, not Shortcuts.")
                XCTAssertTrue(words.contains("plain-text paste"))
                XCTAssertTrue(words.contains("files and image-only content"))
                XCTAssertTrue(words.contains("left unchanged"), words)
                XCTAssertTrue(words.contains(enabled ? "shortcut reserved" : "shortcut not registered"), words)
                XCTAssertTrue(words.contains("more"), "Advanced settings remain reachable in the native category picker.")
            }
        }
        XCTAssertNotEqual(images[0], images[1])
        XCTAssertNotEqual(images[2], images[3])
        XCTAssertEqual(helper.configurationSaves.count, 1)
        assertPasteRenderHasNoExternalEffects(fixture)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testPlainPasteFullSettingsRenderSaveReadbackAndExclusiveConflictHonestly() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        let saving = try renderPasteSettings(fixture, name: "plain-paste-settings-saving-dark", scheme: .dark)
        XCTAssertTrue(try pasteSettingsWords(saving).contains("saving paste preference"))
        XCTAssertEqual(fixture.registrar.registrations, 0)
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        let reading = try renderPasteSettings(fixture, name: "plain-paste-settings-readback-light", scheme: .light)
        XCTAssertTrue(try pasteSettingsWords(reading).contains("confirming saved paste preference"))
        XCTAssertEqual(fixture.registrar.registrations, 0)
        fixture.registrar.failure = .conflict
        try fixture.finishRead(configuration: try XCTUnwrap(helper.configurationSaves.last?.config))
        let conflict = try renderPasteSettings(fixture, name: "plain-paste-settings-conflict-dark", scheme: .dark)
        let words = try pasteSettingsWords(conflict)
        XCTAssertTrue(words.contains("already reserved"))
        XCTAssertTrue(words.contains("retry shortcut registration"))
        XCTAssertFalse(fixture.service.state.enabled)
        assertPasteRenderHasNoExternalEffects(fixture)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testPlainPasteFullSettingsRenderBusyChineseDrainingAndClipboardPartialResult() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        try XCTUnwrap(fixture.registrar.leases.last).fire(.pressed)
        fixture.service.progress(.waitingForKeys)
        let busy = try renderPasteSettings(fixture, name: "plain-paste-settings-busy-light",
                                          scheme: .light, highResolution: true)
        let busyWords = try pasteSettingsWords(busy)
        try NativeRenderEvidence.record("Synthetic paste busy OCR: \(busyWords)")
        XCTAssertTrue(busyWords.contains("release the shortcut keys"), busyWords)
        XCTAssertTrue(busyWords.contains("cancel paste action"))
        fixture.model.setPlainPasteEnabled(false)
        let draining = try renderPasteSettings(fixture, name: "plain-paste-settings-draining-zh-dark",
                                              scheme: .dark, chinese: true, highResolution: true)
        let chinese = try pasteSettingsWords(draining, chinese: true).filter { !$0.isWhitespace }
        try NativeRenderEvidence.record("Synthetic paste draining OCR: \(chinese)")
        try NativeRenderEvidence.record("Synthetic paste draining matches: title=\(chinese.contains("纯文本粘贴")), " +
                                       "message=\(chinese.contains("等待剪贴板任务结束")), stopping=\(fixture.paste.stoppingAction)")
        XCTAssertTrue(chinese.contains("纯文本粘贴"), chinese)
        XCTAssertTrue(chinese.contains("等待剪贴板任务结束"), chinese)
        let differentStatus = try render(
            Text("正在停止，等待文件任务结束。").font(.callout).padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .windowBackgroundColor)),
            named: "plain-paste-draining-negative-control-zh-dark",
            size: NSSize(width: 820, height: 90), scheme: .dark, highResolution: true)
        let negativeWords = try pasteSettingsWords(differentStatus, chinese: true).filter { !$0.isWhitespace }
        try NativeRenderEvidence.record("Synthetic paste negative-control OCR: \(negativeWords)")
        XCTAssertFalse(negativeWords.contains("等待剪贴板任务结束"))
        XCTAssertTrue(fixture.paste.stoppingAction)
        fixture.service.finish(.cancelled, clipboard: .plainTextWritten)
        _ = try fixture.finishSave()
        let partial = try renderPasteSettings(fixture, name: "plain-paste-settings-partial-light", scheme: .light)
        let words = try pasteSettingsWords(partial)
        try NativeRenderEvidence.record("Synthetic paste partial OCR: \(words)")
        XCTAssertTrue(words.contains("plain text was written to the clipboard"))
        XCTAssertTrue(words.contains("no paste key events"))
        XCTAssertFalse(words.contains("pasted successfully"))
        XCTAssertEqual(fixture.service.requests.count, 1)
        assertPasteRenderHasNoExternalEffects(fixture)
    }

    @MainActor
    func testPlainPasteFullSettingsRenderUnconfirmedEventsAndExplicitChinesePermissionRecovery() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.service.finish(.eventPostingFailed, clipboard: .mayHaveChanged, events: .mayHavePosted)
        let partial = try renderPasteSettings(fixture, name: "plain-paste-settings-unconfirmed-dark", scheme: .dark)
        let words = try pasteSettingsWords(partial)
        XCTAssertTrue(words.contains("formatting may have changed"))
        XCTAssertTrue(words.contains("insertion is not confirmed"))
        XCTAssertTrue(words.contains("do not retry automatically"))
        XCTAssertFalse(words.contains("pasted successfully"))
        fixture.service.finish(.accessibilityUnavailable)
        let permission = try renderPasteSettings(fixture, name: "plain-paste-settings-permission-zh-light",
                                                scheme: .light, chinese: true)
        let chinese = try pasteSettingsWords(permission, chinese: true).filter { !$0.isWhitespace }
        XCTAssertTrue(chinese.contains("此操作需要辅助功能权限"), chinese)
        XCTAssertTrue(chinese.contains("此操作未更改剪贴板"))
        assertPasteRenderHasNoExternalEffects(fixture)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testPlainPasteSettingsAdditionPreservesNativeModelEditorCompositionAndCommandReturn() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(true)
        fixture.model.editCustomModelID("fixture/Native-editor")
        _ = try renderPasteSettings(fixture, name: "plain-paste-settings-ime-light", scheme: .light,
                                    pane: .translation,
                                    inspect: { host in
            guard let field = self.pasteTextFields(host).first(where: { $0.isEditable }),
                  let window = host.window else {
                XCTFail("The full settings form must retain its real native text field.")
                return
            }
            XCTAssertTrue(window.makeFirstResponder(field))
            guard let editor = field.currentEditor() as? NSTextView else {
                XCTFail("The native field editor is required for IME composition.")
                return
            }
            editor.setMarkedText("拼", selectedRange: NSRange(location: 1, length: 0),
                                 replacementRange: NSRange(location: NSNotFound, length: 0))
            XCTAssertTrue(editor.hasMarkedText())
            guard let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command,
                                               timestamp: 0, windowNumber: window.windowNumber, context: nil,
                                               characters: "\r", charactersIgnoringModifiers: "\r",
                                               isARepeat: false, keyCode: 36) else {
                XCTFail("Could not construct the local, never-posted Command-Return event.")
                return
            }
            XCTAssertFalse(host.performKeyEquivalent(with: event))
            XCTAssertTrue(editor.hasMarkedText())
            XCTAssertTrue(fixture.service.requests.isEmpty)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            editor.unmarkText()
        })
        assertPasteRenderHasNoExternalEffects(fixture)
    }

    @MainActor
    private func renderPasteSettings(_ fixture: PasteAppFixture, name: String, scheme: ColorScheme,
                                     chinese: Bool = false, highResolution: Bool = false,
                                     pane: SettingsPane = .shortcuts,
                                     inspect: ((NSView) -> Void)? = nil) throws -> Data {
        fixture.model.loadPresentation()
        fixture.model.interfaceLanguage = chinese ? "zh" : "en"
        fixture.model.appearance = scheme == .dark ? "dark" : "light"
        let diagnostics = ProbeModel(persistsPreferences: false,
                                     plainPaste: PlainPasteModel(service: PasteTestService(), registrar: PasteTestRegistrar()))
        let application = AppDelegate(model: fixture.model, capture: CaptureModel(), diagnostics: diagnostics)
        let settings = application.settingsContent(pane: pane)
        XCTAssertTrue(settings.model === fixture.model)
        // Show the complete chosen category without expanding unrelated advanced settings.
        return try render(settings, named: name, size: NSSize(width: 820, height: 1800),
                          scheme: scheme, inspect: inspect, highResolution: highResolution)
    }

    @MainActor
    private func assertPasteRenderHasNoExternalEffects(_ fixture: PasteAppFixture,
                                                       file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty && $0.resultActions.isEmpty },
                      file: file, line: line)
        XCTAssertEqual(fixture.locatorCalls, 0, file: file, line: line)
        XCTAssertFalse(fixture.model.cliBusy, file: file, line: line)
        XCTAssertFalse(fixture.model.monitorEnabled, file: file, line: line)
        XCTAssertEqual(fixture.model.permissions, "Not checked.", file: file, line: line)
    }

    @MainActor
    private func pasteTextFields(_ view: NSView) -> [NSTextField] {
        (view as? NSTextField).map { [$0] } ?? view.subviews.flatMap { pasteTextFields($0) }
    }

    @MainActor
    private func pasteSettingsWords(_ png: Data, chinese: Bool = false) throws -> String {
        try NativeRenderEvidence.settingsWords(png, chinese: chinese)
    }
}
