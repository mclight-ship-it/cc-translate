import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testPearlPrimaryActionsRemainNativePressableButtons() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.input = "A synthetic sentence for native button validation."
        let translator = NativeSettingsTestHost(
            TranslatorView(model: model, showHistory: {}, showSettings: {}, showCapture: {}, embedded: true),
            size: NSSize(width: 660, height: 540))
        defer { translator.close() }
        try await translator.buttonWhenReady("translate-input-text", "Translate").press()
        try await translator.waitFor { helper.translations.count == 1 }
        model.cancel()
        helper.event("cancelled", id: try XCTUnwrap(helper.translations.last?.id),
                     payload: ["submitted": .bool(true)])
        try await translator.waitFor { !model.active }

        var reuseCount = 0
        let row = ProbeModel.HistoryRow(id: "pearl-reuse", input: "Saved original.", output: "Saved result.")
        let history = NativeSettingsTestHost(
            HistoryTranslationDetail(model: model, row: row, useEntry: { reuseCount += 1 }).pearlSurface(),
            size: NSSize(width: 480, height: 440))
        defer { history.close() }
        try await history.buttonWhenReady("reuse-history-entry", "Reuse original").press()
        try await history.waitFor { reuseCount == 1 }
        XCTAssertEqual(model.input, row.input)
        XCTAssertEqual(model.output, row.output)
        XCTAssertEqual(helper.translations.count, 1, "Reusing history must not submit another translation.")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testPearlEmbeddedTranslatorRendersLightDarkAndMinimumWidthWithoutPromotionalCopy() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.appearance = "system"
        model.reuseHistory(.init(id: "pearl-translation", input: "A synthetic original sentence.",
                                 output: "A synthetic translated sentence."))
        var images: [Data] = []
        for scheme in [ColorScheme.light, .dark] {
            let png = try render(
                TranslatorView(model: model, showHistory: {}, showSettings: {}, showCapture: {}, embedded: true),
                named: "pearl-translator-embedded-narrow-\(scheme)",
                size: NSSize(width: 660, height: 540), scheme: scheme, inspect: { host in
                    let textViews = ScaleTestSupport.views(NSTextView.self, in: host)
                    XCTAssertEqual(textViews.count, 2)
                    XCTAssertEqual(textViews.first(where: \.isEditable)?.string, model.input)
                    XCTAssertEqual(textViews.first(where: { !$0.isEditable })?.string, model.output)
                    for view in textViews {
                        let scroll = try XCTUnwrap(view.enclosingScrollView)
                        XCTAssertGreaterThan(scroll.contentSize.height, 80)
                        XCTAssertTrue(host.bounds.insetBy(dx: -1, dy: -1)
                            .contains(host.convert(scroll.bounds, from: scroll)))
                    }
                }, highResolution: true)
            let words = try LocalOCR.recognize(XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)).text.lowercased()
            XCTAssertTrue(words.contains("translate"), words)
            XCTAssertTrue(words.contains("characters"), words)
            XCTAssertFalse(words.contains("your words"), words)
            XCTAssertFalse(words.contains("a little more understanding"), words)
            images.append(png)
        }
        XCTAssertNotEqual(images[0], images[1])
        XCTAssertEqual(model.appearance, "system", "Rendering must not force a demo appearance.")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testPearlCompactResultKeepsScrollingAndFullActionsAt420By300() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.appearance = "system"
        let output = String(repeating: "A synthetic result line that remains selectable.\n", count: 80)
        model.reuseHistory(.init(id: "pearl-compact", input: "Original sentence.", output: output))
        for scheme in [ColorScheme.light, .dark] {
            let png = try render(
                TranslationResultView(model: model, compact: true, openInWindow: {}, togglePinned: {}),
                named: "pearl-result-minimum-\(scheme)", size: NSSize(width: 420, height: 300),
                scheme: scheme, inspect: { host in
                    let view = try XCTUnwrap(ScaleTestSupport.views(NSTextView.self, in: host).first)
                    let scroll = try XCTUnwrap(view.enclosingScrollView)
                    XCTAssertEqual(view.string, output)
                    XCTAssertTrue(view.isSelectable)
                    XCTAssertFalse(view.isEditable)
                    XCTAssertFalse(scroll.drawsBackground)
                    XCTAssertGreaterThan(scroll.contentSize.height, 20)
                    XCTAssertGreaterThan(view.bounds.height, scroll.contentSize.height)
                    XCTAssertTrue(host.bounds.insetBy(dx: -1, dy: -1)
                        .contains(host.convert(scroll.bounds, from: scroll)))
                    for button in ScaleTestSupport.views(NSButton.self, in: host)
                    where !button.isHiddenOrHasHiddenAncestor && !button.title.isEmpty {
                        XCTAssertTrue(host.bounds.insetBy(dx: -1, dy: -1)
                            .contains(host.convert(button.bounds, from: button)), button.title)
                    }
                }, highResolution: true)
            let words = try LocalOCR.recognize(XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)).text.lowercased()
            XCTAssertTrue(words.contains("copy bilingual"), words)
            XCTAssertTrue(words.contains("retranslate"), words)
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
    }

    @MainActor
    func testPearlHistoryDetailAndSettingsKeepNativeContentInBothAppearances() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "en"
        model.appearance = "system"
        let row = ProbeModel.HistoryRow(id: "pearl-history", input: "Saved original.", output: "Saved result.")
        for scheme in [ColorScheme.light, .dark] {
            _ = try render(
                HistoryTranslationDetail(model: model, row: row, useEntry: {}).pearlSurface(),
                named: "pearl-history-detail-\(scheme)", size: NSSize(width: 480, height: 440),
                scheme: scheme, inspect: { host in
                    let views = ScaleTestSupport.views(NSTextView.self, in: host)
                    XCTAssertEqual(Set(views.map(\.string)), Set([row.input, row.output]))
                    XCTAssertTrue(views.allSatisfy { $0.isSelectable && !$0.isEditable })
                }, highResolution: true)
            _ = try render(
                TranslationSettingsView(model: model, showDiagnostics: {}, showAbout: {}, pane: .appearance),
                named: "pearl-settings-appearance-narrow-\(scheme)", size: NSSize(width: 530, height: 460),
                scheme: scheme, inspect: { host in
                    let categories = try XCTUnwrap(ScaleTestSupport.views(NSSegmentedControl.self, in: host).first)
                    XCTAssertEqual(categories.segmentCount, SettingsPane.allCases.count)
                    XCTAssertEqual(categories.selectedSegment, 2)
                }, highResolution: true)
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }

    @MainActor
    func testPearlAppearanceChangesPreserveNativeSelectionAndUncommittedIMEText() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.reuseHistory(.init(id: "pearl-appearance", input: "Original.", output: "Selected result."))
        let surface = NativeSettingsTestHost(
            TranslatorView(model: model, showHistory: {}, showSettings: {}, showCapture: {}, embedded: true),
            size: NSSize(width: 760, height: 620))
        defer { surface.close() }
        let editor = try XCTUnwrap(ScaleTestSupport.views(NativeTranslationTextView.self, in: surface.host).first)
        let result = try XCTUnwrap(ScaleTestSupport.views(NSTextView.self, in: surface.host).first { !$0.isEditable })
        result.setSelectedRange(NSRange(location: 0, length: 8))
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        editor.setMarkedText("拼音", selectedRange: NSRange(location: 2, length: 0),
                             replacementRange: NSRange(location: NSNotFound, length: 0))
        let marked = editor.markedRange()
        let draft = editor.string
        for (preference, appearance) in [("dark", NSAppearance.Name.darkAqua), ("light", .aqua)] {
            model.appearance = preference
            surface.window.appearance = try XCTUnwrap(NSAppearance(named: appearance))
            try await surface.waitFor {
                editor.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == appearance
            }
            XCTAssertTrue(editor.hasMarkedText())
            XCTAssertEqual(editor.markedRange(), marked)
            XCTAssertEqual(editor.string, draft)
            XCTAssertEqual(model.input, "Original.")
            XCTAssertEqual(result.selectedRange(), NSRange(location: 0, length: 8))
            XCTAssertTrue(ScaleTestSupport.views(NSTextView.self, in: surface.host).contains { $0 === result })
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }
}
