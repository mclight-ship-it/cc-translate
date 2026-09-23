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
                        DisclosureNativeField(value: "Unsaved value", identifier: "disclosure-child-editor")
                            .frame(height: 24)
                    }
                    Button("Next control") {}
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .pearlSurface(),
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
            try await DisclosureTestInput.wait(in: surface) {
                self.accessibilityIdentifiers(in: surface.host).contains("disclosure-child-editor")
            }
            let child = try XCTUnwrap(editors(in: surface.host).first)
            XCTAssertTrue(child.isAccessibilityElement())
            XCTAssertEqual(child.accessibilityRole(), .textField)
            XCTAssertEqual(child.stringValue, "Unsaved value")
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "test-details", label: title, target: .emptyRow)
            try await DisclosureTestInput.wait(in: surface) {
                !button.isAccessibilityExpanded() && self.editors(in: surface.host).isEmpty &&
                    !self.accessibilityIdentifiers(in: surface.host).contains("disclosure-child-editor")
            }
            XCTAssertNil(child.window, "Collapsed native content must leave the window, not merely become hidden.")
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
            .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .pearlSurface())
        defer { surface.close() }
        let control = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "keyboard-details", label: "Details", kind: .button)
        let button = try await disclosure(in: surface.host, id: "keyboard-details", label: "Details")
        try control.focus(in: surface.window)
        XCTAssertTrue(control.isFocused)
        XCTAssertTrue(button.canBecomeKeyView)
        XCTAssertEqual(button.focusRingType, .exterior)
        XCTAssertGreaterThan(button.focusRingMaskBounds.width, 24)
        XCTAssertGreaterThanOrEqual(button.focusRingMaskBounds.height, 24)
        let focused = try NativeRenderEvidence.doubleResolutionBitmap(size: surface.host.bounds.size)
        surface.host.cacheDisplay(in: surface.host.bounds, to: focused)
        try NativeRenderEvidence.retainPNG(
            XCTUnwrap(focused.representation(using: .png, properties: [:])), named: "settings-disclosure-native-focus")
        for (characters, code, expanded) in [(" ", UInt16(49), true), ("\r", UInt16(36), false)] {
            try DisclosureTestInput.key(characters, code: code, in: surface.window)
            try await DisclosureTestInput.wait(in: surface) { button.isAccessibilityExpanded() == expanded }
            XCTAssertTrue(control.isFocused, "Expanding or collapsing must keep keyboard focus on the native button.")
        }
        let next = try XCTUnwrap(editors(in: surface.host).first { $0.stringValue == "Next" })
        surface.window.recalculateKeyViewLoop()
        try DisclosureTestInput.key("\t", code: 48, in: surface.window)
        try await DisclosureTestInput.wait(in: surface) { next.currentEditor() != nil }
        XCTAssertNotNil(next.currentEditor(), "Tab order must reach the next control, skipping collapsed content.")
        try DisclosureTestInput.key("\u{19}", code: 48, modifiers: .shift, in: surface.window)
        try await DisclosureTestInput.wait(in: surface) { control.isFocused }
        XCTAssertTrue(control.isFocused, "Reverse Tab order must return to the disclosure.")
        let disabled = try await disclosure(in: surface.host, id: "disabled-details", label: "Unavailable")
        XCTAssertFalse(disabled.isEnabled)
        XCTAssertFalse(disabled.acceptsFirstResponder)
        XCTAssertFalse(disabled.canBecomeKeyView)
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
        Set(NativeSettingsTestAccessibility.elements(in: root).compactMap(\.identifier))
    }
}

@MainActor
private struct DisclosureNativeField: NSViewRepresentable {
    let value: String
    let identifier: String

    func makeNSView(context: Context) -> Field {
        let field = Field(frame: .zero)
        field.isEditable = true
        field.isSelectable = true
        field.setAccessibilityIdentifier(identifier)
        field.setAccessibilityLabel("Private draft")
        field.stringValue = value
        return field
    }

    func updateNSView(_ field: Field, context: Context) { field.stringValue = value }

    final class Field: NSTextField {
        override func isAccessibilityElement() -> Bool { true }
        override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    }
}

@MainActor
private enum DisclosureTestInput {
    private enum Failure: Error { case timeout }

    static func key(_ characters: String, code: UInt16, modifiers: NSEvent.ModifierFlags = [],
                    in window: NSWindow) throws {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            let event = try XCTUnwrap(NSEvent.keyEvent(
                with: type, location: .zero, modifierFlags: modifiers, timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil, characters: characters,
                charactersIgnoringModifiers: code == 48 ? "\t" : characters, isARepeat: false, keyCode: code))
            window.sendEvent(event)
        }
    }

    static func wait<Content: View>(in surface: NativeSettingsTestHost<Content>,
                                   file: StaticString = #filePath, line: UInt = #line,
                                   _ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(2)
        repeat {
            surface.flush()
            if condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        } while Date() < deadline
        XCTFail("Disclosure state did not settle; responder=\(String(describing: surface.window.firstResponder)); " +
                "native/AX identifiers=\(NativeSettingsTestAccessibility.elements(in: surface.host).compactMap(\.identifier))",
                file: file, line: line)
        throw Failure.timeout
    }
}

extension ProductRenderingTests {
    @MainActor
    func testLongNativeDisclosureLabelsWrapWithoutClippingInBothLanguages() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        for language in ["en", "zh"] {
            fixture.model.interfaceLanguage = language
            let title = fixture.model.text("How text length is counted when using translation shortcuts",
                                           "使用翻译快捷键时如何计算输入文字长度")
            let size = NSSize(width: 300, height: 180)
            var textLines: [NSRect] = []
            let png = try render(
                NativeSettingsDisclosure(title, model: fixture.model, identifier: "wrapped-details") {
                    Text("Hidden details")
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .pearlSurface(),
                named: "settings-disclosure-wrapped-\(language)", size: size,
                scheme: language == "zh" ? .dark : .light,
                inspect: { host in
                    let button = try XCTUnwrap(InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: host).first)
                    let titleRect = try XCTUnwrap(button.cell).titleRect(forBounds: button.bounds)
                    XCTAssertGreaterThan(button.bounds.height, 28, "A multiline title must grow its native click target.")
                    XCTAssertTrue(button.bounds.contains(titleRect), "The entire title must fit inside the native button.")
                    XCTAssertEqual(button.bounds.height, button.requiredHeight(for: button.bounds.width), accuracy: 1)
                    XCTAssertFalse(button.isAccessibilityExpanded())
                    XCTAssertEqual(button.accessibilityLabel(), title)
                    XCTAssertEqual(button.attributedTitle.string, title)
                    let storage = NSTextStorage(attributedString: button.attributedTitle)
                    let layout = NSLayoutManager()
                    let container = NSTextContainer(size: titleRect.size)
                    container.lineFragmentPadding = 0
                    storage.addLayoutManager(layout)
                    layout.addTextContainer(container)
                    layout.ensureLayout(for: container)
                    let glyphs = layout.glyphRange(for: container)
                    XCTAssertEqual(layout.characterRange(forGlyphRange: glyphs, actualGlyphRange: nil),
                                   NSRange(location: 0, length: (title as NSString).length),
                                   "Every character must fit; a clipped final line is a failure.")
                    let titleInHost = button.convert(titleRect, to: host)
                    let top = host.isFlipped ? titleInHost.minY - host.bounds.minY
                        : host.bounds.maxY - titleInHost.maxY
                    layout.enumerateLineFragments(forGlyphRange: glyphs) { _, used, _, range, _ in
                        XCTAssertEqual(layout.truncatedGlyphRange(inLineFragmentForGlyphAt: range.location).location,
                                       NSNotFound)
                        XCTAssertLessThanOrEqual(used.maxY, titleRect.height + 1)
                        textLines.append(NSRect(x: titleInHost.minX - host.bounds.minX + used.minX,
                                                y: top + used.minY, width: used.width, height: used.height))
                    }
                }, highResolution: true)
            XCTAssertGreaterThanOrEqual(textLines.count, 2)
            try assertDisclosureInkOnEveryLine(png, lines: textLines, size: size)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            if language == "en" {
                XCTAssertTrue(words.contains(title.lowercased().filter { !$0.isWhitespace }), words)
            }
            // Vision misreads the retained Chinese glyphs; complete glyph layout and
            // per-line rendered ink above, including the final line, are the clipping oracle.
            try NativeRenderEvidence.record("Wrapped disclosure \(language) OCR: \(words)")
            XCTAssertFalse(words.contains("hiddendetails"), "Collapsed details must not be drawn.")
            let surface = NativeSettingsTestHost(
                NativeSettingsDisclosure(title, model: fixture.model, identifier: "wrapped-details") {
                    Text("Hidden details")
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .pearlSurface(), size: NSSize(width: 300, height: 180))
            defer { surface.close() }
            let control = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "wrapped-details", label: title, kind: .button)
            let buttons = InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: surface.host)
                .filter { $0.identifier?.rawValue == "wrapped-details" }
            XCTAssertEqual(buttons.count, 1)
            let button = try XCTUnwrap(buttons.first)
            XCTAssertEqual(control.frame, RenderedGeometry.frame(button))
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "wrapped-details", label: title)
            try await DisclosureTestInput.wait(in: surface) { button.isAccessibilityExpanded() }
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "wrapped-details", label: title, target: .emptyRow)
            try await DisclosureTestInput.wait(in: surface) { !button.isAccessibilityExpanded() }
        }
    }

    @MainActor
    private func assertDisclosureInkOnEveryLine(_ png: Data, lines: [NSRect], size: NSSize) throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(data: png))
        let background = try XCTUnwrap(bitmap.colorAt(x: 0, y: 0)?.usingColorSpace(.deviceRGB))
        let scaleX = CGFloat(bitmap.pixelsWide) / size.width
        let scaleY = CGFloat(bitmap.pixelsHigh) / size.height
        for line in lines {
            XCTAssertTrue(NSRect(origin: .zero, size: size).contains(line))
            let left = max(0, Int(floor(line.minX * scaleX)))
            let right = min(bitmap.pixelsWide, Int(ceil(line.maxX * scaleX)))
            let top = max(0, Int(floor(line.minY * scaleY)))
            let bottom = min(bitmap.pixelsHigh, Int(ceil(line.maxY * scaleY)))
            guard left < right, top < bottom else {
                XCTFail("A wrapped text line must intersect the rendered bitmap.")
                continue
            }
            var ink = 0
            for y in top..<bottom {
                for x in left..<right {
                    let color = try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                    let difference = max(abs(color.redComponent - background.redComponent),
                                         max(abs(color.greenComponent - background.greenComponent),
                                             abs(color.blueComponent - background.blueComponent)))
                    if difference > 0.2 { ink += 1 }
                }
            }
            XCTAssertGreaterThan(ink, 16, "Every wrapped line, including the final Chinese line, must actually be drawn.")
        }
    }
}
