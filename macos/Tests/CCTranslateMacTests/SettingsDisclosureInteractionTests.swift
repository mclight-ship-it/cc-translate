import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class SettingsDisclosureInteractionTests: XCTestCase {
    @MainActor
    func testLabelAndBlankRowToggleNativeDisclosureWithLocalizedStateAndRemovedChildren() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.interfaceLanguage = language
            let title = fixture.model.text("Details", "详细信息")
            let surface = NativeSettingsTestHost(
                VStack {
                    NativeSettingsDisclosure(title, model: fixture.model, identifier: "test-details") {
                        TextField("Private draft", text: .constant("Unsaved value"))
                            .accessibilityIdentifier("disclosure-child-editor")
                    }
                    Button("Next control") {}
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading),
                size: NSSize(width: 530, height: 300))
            defer { surface.close() }
            let button = try await disclosure(in: surface.host, id: "test-details", label: title)
            XCTAssertGreaterThan(button.bounds.width, 450)
            XCTAssertGreaterThanOrEqual(button.bounds.height, 24)
            XCTAssertEqual(button.accessibilityRole(), .button)
            XCTAssertFalse(button.isAccessibilityExpanded())
            XCTAssertEqual(button.accessibilityValue() as? String, fixture.model.text("Collapsed", "已折叠"))
            XCTAssertTrue(editors(in: surface.host).isEmpty)
            XCTAssertFalse(accessibilityIdentifiers(in: surface.host).contains("disclosure-child-editor"))

            try await NativeSettingsTestControls.pressDisclosure(in: surface.host, identifier: "test-details", label: title)
            try await surface.waitFor { button.isAccessibilityExpanded() && self.editors(in: surface.host).count == 1 }
            XCTAssertEqual(button.accessibilityValue() as? String, fixture.model.text("Expanded", "已展开"))
            XCTAssertTrue(accessibilityIdentifiers(in: surface.host).contains("disclosure-child-editor"))
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "test-details", label: title, target: .emptyRow)
            try await surface.waitFor { !button.isAccessibilityExpanded() && self.editors(in: surface.host).isEmpty }
            XCTAssertFalse(accessibilityIdentifiers(in: surface.host).contains("disclosure-child-editor"))
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
    }

    @MainActor
    func testNativeKeyboardFocusSpaceReturnAndDisabledDisclosure() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        let surface = NativeSettingsTestHost(
            VStack {
                NativeSettingsDisclosure("Details", model: fixture.model, identifier: "keyboard-details") {
                    TextField("Draft", text: .constant("Unchanged"))
                }
                NativeSettingsDisclosure("Unavailable", model: fixture.model, identifier: "disabled-details") {
                    TextField("Hidden", text: .constant(""))
                }.disabled(true)
                TextField("Next control", text: .constant("Next"))
            }
            .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading))
        defer { surface.close() }
        let control = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "keyboard-details", label: "Details", kind: .button)
        let button = try await disclosure(in: surface.host, id: "keyboard-details", label: "Details")
        try control.focus(in: surface.window)
        XCTAssertTrue(control.isFocused)
        XCTAssertEqual(button.focusRingType, .exterior)
        XCTAssertGreaterThan(button.focusRingMaskBounds.width, 24)
        XCTAssertGreaterThanOrEqual(button.focusRingMaskBounds.height, 24)
        let focused = try NativeRenderEvidence.doubleResolutionBitmap(size: surface.host.bounds.size)
        surface.host.cacheDisplay(in: surface.host.bounds, to: focused)
        try NativeRenderEvidence.retainPNG(
            XCTUnwrap(focused.representation(using: .png, properties: [:])), named: "settings-disclosure-native-focus")
        for (characters, code, expanded) in [(" ", UInt16(49), true), ("\r", UInt16(36), false)] {
            let event = try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: surface.window.windowNumber, context: nil, characters: characters,
                charactersIgnoringModifiers: characters, isARepeat: false, keyCode: code))
            button.keyDown(with: event)
            try await surface.waitFor { button.isAccessibilityExpanded() == expanded }
            XCTAssertTrue(control.isFocused, "Expanding or collapsing must keep keyboard focus on the native button.")
        }
        let next = try XCTUnwrap(editors(in: surface.host).first { $0.stringValue == "Next" })
        surface.window.recalculateKeyViewLoop()
        surface.window.selectNextKeyView(button)
        XCTAssertNotNil(next.currentEditor(), "Tab order must reach the next control, skipping collapsed content.")
        surface.window.selectPreviousKeyView(next)
        XCTAssertTrue(control.isFocused, "Reverse Tab order must return to the disclosure.")
        let disabled = try await disclosure(in: surface.host, id: "disabled-details", label: "Unavailable")
        XCTAssertFalse(disabled.isEnabled)
        XCTAssertFalse(disabled.acceptsFirstResponder)
        disabled.performClick(nil)
        XCTAssertFalse(disabled.isAccessibilityExpanded())
        XCTAssertEqual(editors(in: surface.host).map(\.stringValue), ["Next"])
    }

    @MainActor
    func testAllProductionSettingsDisclosuresOpenByLabelAndCloseByBlankRowWithoutSaving() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            let model = try XCTUnwrap(fixture.model)
            model.interfaceLanguage = language
            let navigation = SettingsNavigation()
            let surface = NativeSettingsTestHost(
                TranslationSettingsView(model: model, showDiagnostics: {}, showAbout: {}, navigation: navigation),
                size: NSSize(width: 760, height: 1400))
            defer { surface.close() }
            try await cycle(in: surface, id: "custom-model-details", label: model.text("Custom model", "自定义模型"))
            try await cycle(in: surface, id: "dictionary-information-details",
                            label: model.text("Dictionary information", "词库信息"))
            let installationTitle = model.text("Codex installation", "Codex 安装位置")
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "provider-installation-details", label: installationTitle)
            try await cycle(in: surface, id: "provider-version-details", label: model.text("Version check", "版本检查"))
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "provider-installation-details", label: installationTitle, target: .emptyRow)
            navigation.pane = .shortcuts
            try await cycle(in: surface, id: "copy-timing-details", label: model.text("Double-copy timing", "双击间隔"))
            navigation.pane = .more
            try await cycle(in: surface, id: "input-limit-counting-details",
                            label: model.text("How text length is counted", "字数如何计算"))
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertFalse(model.cliBusy)
            XCTAssertNil(model.permissionSnapshot, "Expanding settings must not check or request system permissions.")
        }
    }

    @MainActor
    func testCatalogDetailsUsesSameNativeLabelAndBlankRowInteraction() async throws {
        let fixture = try CatalogAppFixture()
        defer { fixture.cleanUp() }
        let client = try fixture.ready()
        fixture.model.refreshModels()
        try fixture.complete(client)
        fixture.model.modelProfile = "fixture/model-a"
        for language in ["en", "zh"] {
            fixture.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(
                ModelCatalogSettingsView(model: fixture.model).padding(20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading))
            defer { surface.close() }
            try await cycle(in: surface, id: "model-catalog-details",
                            label: fixture.model.text("Model details", "模型详情"))
        }
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    func testCollapsingCustomModelKeepsUnappliedDraftAndSavedModel() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let saved = fixture.model.modelSettings.savedProfile
        let surface = NativeSettingsTestHost(
            CodexModelSettingsView(model: fixture.model).padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading))
        defer { surface.close() }
        try await NativeSettingsTestControls.pressDisclosure(
            in: surface.host, identifier: "custom-model-details", label: "Custom model")
        fixture.model.editCustomModelID("fixture/Unapplied-Draft")
        try await surface.waitFor {
            self.editors(in: surface.host).contains { $0.stringValue == "fixture/Unapplied-Draft" }
        }
        try await NativeSettingsTestControls.pressDisclosure(
            in: surface.host, identifier: "custom-model-details", label: "Custom model", target: .emptyRow)
        try await surface.waitFor { self.editors(in: surface.host).isEmpty }
        XCTAssertEqual(fixture.model.modelSettings.draft, "fixture/Unapplied-Draft")
        try await NativeSettingsTestControls.pressDisclosure(
            in: surface.host, identifier: "custom-model-details", label: "Custom model")
        try await surface.waitFor {
            self.editors(in: surface.host).contains { $0.stringValue == "fixture/Unapplied-Draft" }
        }
        XCTAssertEqual(fixture.model.modelSettings.savedProfile, saved)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    private func cycle<Content: View>(in surface: NativeSettingsTestHost<Content>, id: String,
                                     label: String) async throws {
        let button = try await disclosure(in: surface.host, id: id, label: label)
        XCTAssertFalse(button.isAccessibilityExpanded())
        try await NativeSettingsTestControls.pressDisclosure(in: surface.host, identifier: id, label: label)
        try await surface.waitFor { button.isAccessibilityExpanded() }
        try await NativeSettingsTestControls.pressDisclosure(in: surface.host, identifier: id, label: label, target: .emptyRow)
        try await surface.waitFor { !button.isAccessibilityExpanded() }
    }

    @MainActor
    private func disclosure(in root: NSView, id: String, label: String) async throws -> NativeSettingsDisclosureButton {
        _ = try await NativeSettingsTestControls.resolveWhenReady(in: root, identifier: id, label: label, kind: .button)
        return try XCTUnwrap(InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: root).first {
            $0.identifier?.rawValue == id
        })
    }

    @MainActor
    private func editors(in root: NSView) -> [NSTextField] {
        InputLimitNativeViews.views(NSTextField.self, in: root).filter(\.isEditable)
    }

    @MainActor
    private func accessibilityIdentifiers(in root: NSView) -> Set<String> {
        var visited = Set<ObjectIdentifier>()
        func visit(_ object: Any) -> Set<String> {
            guard let element = object as? any NSAccessibilityProtocol,
                  visited.insert(ObjectIdentifier(element as AnyObject)).inserted else { return [] }
            var result = Set<String>()
            if let identifier = element.accessibilityIdentifier() { result.insert(identifier) }
            for child in element.accessibilityChildren() ?? [] { result.formUnion(visit(child)) }
            return result
        }
        return visit(root)
    }
}
