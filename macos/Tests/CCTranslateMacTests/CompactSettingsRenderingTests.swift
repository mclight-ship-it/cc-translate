import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testCompactSettingsRendersFourCategoriesAtNormalWindowSizeWithoutTechnicalWall() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            fixture.model.interfaceLanguage = language
            fixture.model.appearance = scheme == .dark ? "dark" : "light"
            for pane in SettingsPane.allCases {
                let png = try render(
                    TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {}, pane: pane),
                    named: "settings-compact-\(pane.rawValue)-\(language)",
                    size: NSSize(width: 660, height: 650), scheme: scheme, highResolution: true)
                if pane == .translation && language == "en" {
                    let words = try NativeRenderEvidence.settingsWords(png)
                    try NativeRenderEvidence.record("Compact default settings OCR (\(words.count) characters): \(words)")
                    XCTAssertTrue(words.contains("automatic long-text summary"), words)
                    XCTAssertTrue(words.contains("save translation history"), words)
                    XCTAssertTrue(words.contains("dictionary information"),
                                  "Normal translation settings must fit without a multi-page technical form: \(words)")
                    for implementationDetail in ["utf-8", "sha-256", "code points", "read back", "schema"] {
                        XCTAssertFalse(words.contains(implementationDetail), implementationDetail)
                    }
                    XCTAssertLessThan(words.count, 1_000, "Normal settings should be scannable, not an implementation manual.")
                }
            }
            _ = try render(
                TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {}),
                named: "settings-compact-narrow-\(language)",
                size: NSSize(width: 530, height: 460), scheme: scheme, highResolution: true)
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertFalse(fixture.model.cliBusy)
        XCTAssertFalse(fixture.model.monitorEnabled)
        XCTAssertEqual(fixture.model.permissions, "Not checked.")
        XCTAssertTrue(helper.dictionaryRequests.allSatisfy { $0.request == .status })
    }

    @MainActor
    func testNativeSettingsCategoryActionsPreserveCustomDraftAndDoNotSaveOrTranslate() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.interfaceLanguage = language
            fixture.model.editCustomModelID("fixture/Keep-My-Draft")
            let surface = NativeSettingsTestHost(
                TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {}),
                size: NSSize(width: 760, height: 900))
            defer { surface.close() }
            try await surface.waitFor {
                self.compactSettingsFields(surface.host).contains { $0.stringValue == "fixture/Keep-My-Draft" }
            }
            let picker = try XCTUnwrap(compactSettingsViews(NSSegmentedControl.self, surface.host).first)
            XCTAssertEqual(picker.segmentCount, SettingsPane.allCases.count)
            for (index, pane) in SettingsPane.allCases.enumerated() {
                XCTAssertEqual(picker.label(forSegment: index), pane.title(using: fixture.model))
            }
            let action = try XCTUnwrap(picker.action)
            picker.selectedSegment = 3
            XCTAssertTrue(picker.sendAction(action, to: picker.target))
            try await surface.waitFor { self.compactSettingsFields(surface.host).count == 2 }
            picker.selectedSegment = 2
            XCTAssertTrue(picker.sendAction(action, to: picker.target))
            try await surface.waitFor { self.compactSettingsFields(surface.host).isEmpty }
            picker.selectedSegment = 0
            XCTAssertTrue(picker.sendAction(action, to: picker.target))
            try await surface.waitFor {
                self.compactSettingsFields(surface.host).contains { $0.stringValue == "fixture/Keep-My-Draft" }
            }
            XCTAssertEqual(fixture.model.modelSettings.draft, "fixture/Keep-My-Draft")
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.resultActions.isEmpty)
            XCTAssertFalse(fixture.model.cliBusy)
            XCTAssertEqual(fixture.model.permissions, "Not checked.")
        }
    }

    @MainActor
    func testCustomModelDetailsAreCollapsedUntilOpenedThroughNativeControl() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.interfaceLanguage = "en"
        let surface = NativeSettingsTestHost(
            TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {}),
            size: NSSize(width: 760, height: 900))
        defer { surface.close() }
        XCTAssertTrue(compactSettingsFields(surface.host).isEmpty)
        try NativeSettingsTestControls.pressCaption(
            in: surface.host, identifier: "custom-model-details", label: "Custom model")
        try await surface.waitFor { self.compactSettingsFields(surface.host).count == 1 }
        let editor = try XCTUnwrap(compactSettingsFields(surface.host).first)
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        XCTAssertNotNil(editor.currentEditor(), "The newly disclosed native editor must accept focus.")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(fixture.model.cliBusy)
        XCTAssertEqual(fixture.model.permissions, "Not checked.")
    }

    @MainActor
    private func compactSettingsViews<T: NSView>(_ type: T.Type, _ root: NSView) -> [T] {
        (root as? T).map { [$0] } ?? root.subviews.flatMap { compactSettingsViews(type, $0) }
    }

    @MainActor
    private func compactSettingsFields(_ root: NSView) -> [NSTextField] {
        compactSettingsViews(NSTextField.self, root).filter {
            $0.isEditable && !$0.isHiddenOrHasHiddenAncestor && !$0.visibleRect.isEmpty
        }
    }
}
