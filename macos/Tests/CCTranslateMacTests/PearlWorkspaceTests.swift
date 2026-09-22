import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testPearlWorkspaceRendersRealTranslatorAndOfflineDictionaryInBothThemes() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        try DictionarySourcesFixture.publish(fixture, helper: helper)
        fixture.model.dictionarySearch.query = "example"
        fixture.model.dictionarySearch.search(language: "en_US")
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: DictionaryModelTests.hit(history: "disabled"))
        for scheme in [ColorScheme.light, .dark] {
            fixture.model.appearance = scheme == .dark ? "dark" : "light"
            let theme = scheme == .dark ? "dark" : "light"
            let translator = ProductWorkspace(model: fixture.model, selection: .translator, navigate: { _ in }) {
                TranslatorView(model: fixture.model, showHistory: {}, showSettings: {}, showCapture: {}, embedded: true)
            }
            let png = try render(translator, named: "pearl-workspace-translator-\(theme)",
                                 size: NSSize(width: 1120, height: 720), scheme: scheme, highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png)
            XCTAssertTrue(words.contains("local dictionary"))
            XCTAssertTrue(words.contains("example"))
            XCTAssertTrue(words.contains("settings"))
            let dictionary = ProductWorkspace(model: fixture.model, selection: .dictionary, navigate: { _ in }) {
                DictionaryLibraryView(model: fixture.model, search: fixture.model.dictionarySearch)
            }
            let lookup = try render(dictionary, named: "pearl-workspace-dictionary-\(theme)",
                                    size: NSSize(width: 960, height: 740), scheme: scheme, highResolution: true)
            let lookupWords = try NativeRenderEvidence.settingsWords(lookup)
            XCTAssertTrue(lookupWords.contains("look up"))
            XCTAssertTrue(lookupWords.contains("representative instance"))
        }
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testPearlWorkspaceCompactRailPreservesEditorWidthAndSettingsContent() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        _ = try fixture.localReady(automaticReplies: true)
        fixture.model.input = "The meeting has been moved to Thursday at 10 a.m."
        let translator = ProductWorkspace(model: fixture.model, selection: .translator, navigate: { _ in }) {
            TranslatorView(model: fixture.model, showHistory: {}, showSettings: {}, showCapture: {}, embedded: true)
        }
        let png = try render(translator, named: "pearl-workspace-translator-narrow",
                             size: NSSize(width: 717, height: 540), scheme: .light, highResolution: true)
        let words = try NativeRenderEvidence.settingsWords(png)
        XCTAssertTrue(words.contains("characters"))
        XCTAssertTrue(words.contains("thursday"))
        let settings = ProductWorkspace(model: fixture.model, selection: .settings, navigate: { _ in }) {
            TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {})
        }
        let settingsPNG = try render(settings, named: "pearl-workspace-settings-light",
                                     size: NSSize(width: 960, height: 720), scheme: .light, highResolution: true)
        XCTAssertTrue(try NativeRenderEvidence.settingsWords(settingsPNG).contains("settings"))
    }
}

final class PearlWorkspaceTests: XCTestCase {
    @MainActor
    func testDictionarySearchNativeButtonUsesLocalLookupOnly() async throws {
        _ = NSApplication.shared
        let focus = NativeTestWindowFocus()
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.input = "Retain the translation draft."
        model.dictionarySearch.query = "example"
        let host = NSHostingView(rootView: DictionaryLibraryView(model: model, search: model.dictionarySearch))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 780, height: 740),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { focus.close(window) }
        window.makeKeyAndOrderFront(nil)
        try await CaptureProductFixture.waitFor { model.dictionary.busy }
        let status = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(status.request, .status)
        helper.event("completed", id: status.id,
                     payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        let submit = try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: "dictionary-search-submit", label: "Look up", kind: .button)
        try submit.focus(in: window)
        XCTAssertTrue(submit.isFocused)
        try await submit.press()
        let lookup = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(lookup.request, .lookup(text: "example", appLanguage: "en_US", origin: "text",
                                              useCache: true, recordHistory: false))
        helper.event("completed", id: lookup.id, payload: DictionaryModelTests.hit(history: "disabled"))
        try await CaptureProductFixture.waitFor { model.dictionarySearch.phase == .hit }
        XCTAssertEqual(model.dictionarySearch.output, DictionaryModelTests.senses)
        XCTAssertEqual(model.input, "Retain the translation draft.")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(fixture.helpers.count, 1)
    }

    @MainActor
    func testSidebarNativeControlsCanReceiveFocusAndNavigate() async throws {
        _ = NSApplication.shared
        let focus = NativeTestWindowFocus()
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        var destinations: [ProductSection] = []
        let host = NSHostingView(rootView: ProductWorkspace(
            model: fixture.model, selection: .translator, navigate: { destinations.append($0) }) {
                Text("Translation content").frame(maxWidth: .infinity, maxHeight: .infinity)
            })
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 600),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { focus.close(window) }
        window.makeKeyAndOrderFront(nil)
        for section in [ProductSection.history, .settings, .capture] {
            let button = try await NativeSettingsTestControls.resolveWhenReady(
                in: host, identifier: "workspace-nav-\(section.rawValue)",
                label: section.title(using: fixture.model), kind: .button)
            try button.focus(in: window)
            XCTAssertTrue(button.isFocused)
            try await button.press()
            XCTAssertEqual(destinations.last, section)
        }
        XCTAssertEqual(destinations, [.history, .settings, .capture])
        XCTAssertTrue(fixture.helpers.isEmpty, "Navigation rendering alone must not connect or translate.")
    }

    @MainActor
    func testPearlSemanticTextAndFocusColorsHaveReadableContrast() throws {
        for name in [NSAppearance.Name.aqua, .darkAqua] {
            let appearance = try XCTUnwrap(NSAppearance(named: name))
            func luminance(_ color: Color) throws -> Double {
                var resolved: NSColor?
                appearance.performAsCurrentDrawingAppearance {
                    resolved = NSColor(color).usingColorSpace(.sRGB)
                }
                let value = try XCTUnwrap(resolved)
                func linear(_ component: CGFloat) -> Double {
                    let c = Double(component)
                    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
                }
                return 0.2126 * linear(value.redComponent) + 0.7152 * linear(value.greenComponent)
                    + 0.0722 * linear(value.blueComponent)
            }
            func ratio(_ foreground: Color, _ background: Color) throws -> Double {
                let a = try luminance(foreground)
                let b = try luminance(background)
                return (max(a, b) + 0.05) / (min(a, b) + 0.05)
            }
            for surface in [PearlTheme.surface, PearlTheme.panel, PearlTheme.sidebar, PearlTheme.inset] {
                XCTAssertGreaterThanOrEqual(try ratio(PearlTheme.text, surface), 4.5)
                XCTAssertGreaterThanOrEqual(try ratio(PearlTheme.secondary, surface), 4.5)
                XCTAssertGreaterThanOrEqual(try ratio(PearlTheme.accent, surface), 3.0)
            }
            XCTAssertGreaterThanOrEqual(try ratio(PearlTheme.onAccent, PearlTheme.accent), 4.5)
        }
    }

    @MainActor
    func testNavigationReusesWindowsAndPreservesDraftAndDictionaryQuery() throws {
        _ = NSApplication.shared
        let focus = NativeTestWindowFocus()
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let app = AppDelegate(model: fixture.model, capture: CaptureModel(),
                              diagnostics: NativePresentationTestSupport.offline(fixture.preferences, persists: false),
                              loginItems: LoginItemModel(service: LoginItemTestService()))
        defer {
            for panel in [app.inputPanel, app.resultPanel, app.settingsPanel, app.historyPanel, app.dictionaryPanel, app.aboutPanel] {
                if let panel { focus.close(panel) }
            }
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        }
        app.navigate(to: .translator)
        let editor = try XCTUnwrap(app.inputPanel)
        fixture.model.input = "Unsaved translation draft"
        let helper = try fixture.ready()
        app.navigate(to: .dictionary)
        let dictionary = try XCTUnwrap(app.dictionaryPanel)
        fixture.model.dictionarySearch.query = "example"
        app.navigate(to: .settings)
        let settings = try XCTUnwrap(app.settingsPanel)
        app.navigate(to: .translator)
        XCTAssertTrue(app.inputPanel === editor)
        XCTAssertEqual(fixture.model.input, "Unsaved translation draft")
        app.navigate(to: .dictionary)
        XCTAssertTrue(app.dictionaryPanel === dictionary)
        XCTAssertEqual(fixture.model.dictionarySearch.query, "example")
        app.showSettings(pane: .shortcuts)
        XCTAssertTrue(app.settingsPanel === settings)
        for (panel, minimum) in [(editor, NSSize(width: 717, height: 540)),
                                  (dictionary, NSSize(width: 637, height: 600)),
                                  (settings, NSSize(width: 587, height: 460))] {
            panel.contentView?.layoutSubtreeIfNeeded()
            XCTAssertEqual(panel.contentMinSize, minimum)
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(app.desiredActivationPolicy, .regular)
        app.showResult()
        let result = try XCTUnwrap(app.resultPanel)
        let resultContent = try XCTUnwrap(result.contentView)
        XCTAssertEqual(result.level, .floating)
        app.toggleResultPin()
        XCTAssertFalse(fixture.model.resultPinned)
        XCTAssertEqual(result.level, .normal)
        app.toggleResultPin()
        XCTAssertTrue(fixture.model.resultPinned)
        XCTAssertEqual(result.level, .floating)
        XCTAssertTrue(result.contentView === resultContent)
        XCTAssertEqual(fixture.model.input, "Unsaved translation draft")
        result.performClose(nil)
        editor.performClose(nil)
        settings.performClose(nil)
        dictionary.performClose(nil)
        XCTAssertEqual(app.desiredActivationPolicy, .accessory)
    }
}
