import AppKit
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

private struct WorkspaceJourneyRandom {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func index(_ count: Int) -> Int {
        state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return Int((state >> 32) % UInt64(count))
    }
}

private enum WorkspaceJourneyOperation: String, CaseIterable {
    case history, dictionary, settings, about, editDraft, undoAfterNavigation
    case historySearch, dictionaryLookup, disclosure, keyboardFocus
    case closeReopen, hideRestore, minimizeRestore, resize, quickCancel, quickSubmit, resultActions
    case navigateWhileStreaming, scrollDraft, quickReplaceBusy
}

@MainActor
private struct WorkspaceJourneyAccessibility {
    let identifiers: Set<String>
    let labels: Set<String>
    let values: Set<String>
    let objects: Set<ObjectIdentifier>

    init(in root: NSView) {
        var identifiers = Set<String>()
        var labels = Set<String>()
        var values = Set<String>()
        var objects = Set<ObjectIdentifier>()
        func visit(_ object: Any) {
            guard let object = object as? NSObject,
                  objects.insert(ObjectIdentifier(object)).inserted else { return }
            let element = object as? any NSAccessibilityProtocol
            // SwiftUI virtual AX objects can use NSObject's public attribute API
            // without adopting NSAccessibilityProtocol, like the shared control resolver.
            let attributes = object.accessibilityAttributeNames()
            func attribute(_ name: NSAccessibility.Attribute) -> Any? {
                attributes.contains(name) ? object.accessibilityAttributeValue(name) : nil
            }
            if let identifier = element?.accessibilityIdentifier() ?? attribute(.identifier) as? String {
                identifiers.insert(identifier)
            }
            if let label = element?.accessibilityLabel() ?? attribute(.description) as? String { labels.insert(label) }
            if let value = element?.accessibilityValue() as? String ?? attribute(.value) as? String { values.insert(value) }
            for child in element?.accessibilityChildren() ?? [] { visit(child) }
            for child in attribute(.children) as? [Any] ?? [] { visit(child) }
            for child in element?.accessibilityContents() ?? [] { visit(child) }
            for child in attribute(.contents) as? [Any] ?? [] { visit(child) }
        }
        if let window = root.window { visit(window) }
        visit(root)
        for child in NSAccessibility.unignoredChildren(from: [root]) { visit(child) }
        self.identifiers = identifiers
        self.labels = labels
        self.values = values
        self.objects = objects
    }

    func containsText(_ text: String) -> Bool { labels.contains(text) || values.contains(text) }
}

@MainActor
private enum WorkspaceJourneyPixels {
    static func png(in root: NSView, rectangle: NSRect? = nil) throws -> Data {
        let rectangle = rectangle ?? root.bounds
        XCTAssertTrue(root.bounds.insetBy(dx: -1, dy: -1).contains(rectangle))
        root.layoutSubtreeIfNeeded()
        root.displayIfNeeded()
        let bitmap = try NativeRenderEvidence.doubleResolutionBitmap(size: rectangle.size)
        root.effectiveAppearance.performAsCurrentDrawingAppearance {
            root.cacheDisplay(in: rectangle, to: bitmap)
        }
        return try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
    }

    static func assertHint(_ hint: String, visible: Bool, in root: NSView, named name: String) async throws {
        let deadline = Date().addingTimeInterval(2)
        var png: Data
        var matches: [Range<String.Index>]
        repeat {
            try await Task.sleep(nanoseconds: 10_000_000)
            png = try self.png(in: root)
            let words = try NativeRenderEvidence.settingsWords(png)
            matches = NativeSettingsTestControls.ranges(of: hint, in: words)
        } while (!matches.isEmpty != visible) && Date() < deadline
        try NativeRenderEvidence.retainPNG(png, named: name)
        XCTAssertEqual(!matches.isEmpty, visible, "The actual quick-input pixels must match the requested hint state.")
        let editors = InputLimitNativeViews.views(NativeTranslationTextView.self, in: root)
        XCTAssertEqual(editors.count, 1)
        let editor = try XCTUnwrap(editors.first)
        try NativeRenderEvidence.record(
            "COLD QUICK INPUT HINT \(name): expectedVisible=\(visible), pixelsVisible=\(!matches.isEmpty), " +
            "nativeEditorHelpExposed=\(editor.accessibilityHelp() == hint).")
        XCTAssertEqual(editor.accessibilityHelp() == hint, visible,
                       "The visible recovery hint must also be available from the native editor's AX help.")
    }
}

@MainActor
private final class WorkspaceJourneyFixture {
    let product: ProductTestHarness
    let helper: ProductTestHelper
    let source: CaptureTestSource
    let application: AppDelegate
    let main: NSPanel
    let mainContent: NSView
    private let focus: NativeTestWindowFocus
    private let previousDelegate: NSApplicationDelegate?
    private let previousMenu: NSMenu?
    private let previousWindowsMenu: NSMenu?
    private let aboutBundle: AboutBundleFixture?
    private var servedHistory = Set<String>()
    private var visitedPages: [ProductSection: NSView] = [:]
    private var navigationButtons: [ProductSection: NSButton] = [:]
    private(set) var editor: NativeTranslationTextView?
    private(set) var expectedDraft = ""
    private(set) var expectedQuery = ""
    private(set) var expectedSubmissions = 0
    private var step = 0
    private let run: String
    private var trace: [String] = []

    init(run: String, bundledAbout: Bool = false) throws {
        self.run = run
        focus = NativeTestWindowFocus()
        previousDelegate = NSApp.delegate
        previousMenu = NSApp.mainMenu
        previousWindowsMenu = NSApp.windowsMenu
        aboutBundle = try bundledAbout ? AboutBundleFixture() : nil
        product = try ProductTestHarness()
        helper = try product.ready()
        source = CaptureTestSource(image: try CaptureProductFixture.image())
        let source = self.source
        application = AppDelegate(model: product.model,
            capture: CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                                       notificationCenter: NotificationCenter())),
            diagnostics: NativePresentationTestSupport.offline(product.preferences, persists: false),
            about: aboutBundle.map { AboutModel(resources: $0.resources) },
            loginItems: LoginItemModel(service: LoginItemTestService()))
        NSApp.delegate = application
        application.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        // Initial launch entry only. Subsequent navigation uses the actual sidebar.
        application.navigate(to: .translator)
        main = try XCTUnwrap(application.inputPanel)
        mainContent = try XCTUnwrap(main.contentView)
    }

    func cleanUp() {
        if NSApp.isHidden { NSApp.unhide(nil) }
        for panel in [application.quickInputPanel, application.resultPanel, application.capturePanel,
                      application.updatesPanel, application.inputPanel].compactMap({ $0 }) {
            focus.close(panel)
        }
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        NSApp.delegate = previousDelegate
        NSApp.mainMenu = previousMenu
        NSApp.windowsMenu = previousWindowsMenu
        product.cleanUp()
        aboutBundle?.cleanUp()
    }

    func start() async throws {
        try NativeRenderEvidence.record(
            "WORKSPACE JOURNEY \(run): real production NSWindows/native controls; synthetic helper replies. " +
            "In-process AppKit mouse/key events and text-system commands, not a human, physical keyboard, " +
            "external-app acceptance, VoiceOver, or TCC approval run. No clipboard reads or model execution.")
        try NativeRenderEvidence.record(
            "BOUNDARY: Dock restoration invokes the production reopen callback; resizing uses NSWindow APIs. " +
            "Existing packaged tests inspect CC_TRANSLATE_APP resources or launch bundled helper processes, " +
            "not a synthetic-service GUI journey. No production launch switch or permission bypass was added. " +
            "PNGs capture attached production content views, not the external desktop.")
        try await settle()
        editor = try nativeEditor(in: main)
        try await replaceDraft("Synthetic unfinished draft: cafe\u{301}, \u{5317}\u{4eac}, line two.\nKeep this text.")
        try snapshot("start")
        try assertHealthy()
    }

    func settle() async throws {
        for _ in 0..<2 {
            for request in helper.historyLoads where !servedHistory.contains(request.id) {
                servedHistory.insert(request.id)
                helper.event("completed", id: request.id,
                    payload: ProductTestHarness.historyPage(entries: [], total: 0))
            }
            main.contentView?.layoutSubtreeIfNeeded()
            main.displayIfNeeded()
            try await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    private func page(_ section: ProductSection) throws -> NSView {
        let container = try XCTUnwrap(
            InputLimitNativeViews.views(RetainedWorkspaceContainer.self, in: mainContent).first)
        XCTAssertEqual(container.selection, section)
        XCTAssertEqual(container.subviews.count, 1, "Inactive hosts must be detached, not merely hidden.")
        let pages = container.subviews.filter { $0.identifier?.rawValue == "workspace-page-\(section.rawValue)" }
        XCTAssertEqual(pages.count, 1, "Only the selected page belongs to native layout and focus traversal.")
        let page = try XCTUnwrap(pages.first)
        if let existing = visitedPages[section] {
            XCTAssertTrue(existing === page, "Each section must reuse its original native hosting view.")
        }
        visitedPages[section] = page
        return page
    }

    private func nativeEditor(in window: NSWindow) throws -> NativeTranslationTextView {
        let root = try XCTUnwrap(window.contentView)
        let editors = InputLimitNativeViews.views(NativeTranslationTextView.self, in: root)
            .filter { $0.isEditable && !RenderedGeometry.visibleRect($0).isEmpty }
        XCTAssertEqual(editors.count, 1)
        return try XCTUnwrap(editors.first)
    }

    private func press(_ identifier: String, _ label: String, in window: NSWindow,
                       root: NSView? = nil) async throws {
        let scope = try root ?? XCTUnwrap(window.contentView)
        let exact = InputLimitNativeViews.views(NSButton.self, in: scope).filter {
            $0.identifier?.rawValue == identifier && !RenderedGeometry.visibleRect($0).isEmpty
        }
        XCTAssertLessThanOrEqual(exact.count, 1, "A native action identifier must be unique.")
        let control = try await NativeSettingsTestControls.resolveWhenReady(
            in: exact.first ?? scope, identifier: identifier, label: label, kind: .button)
        InputLimitNativeViews.assertVisible(control)
        try control.focus(in: window)
        try await control.press()
        try await settle()
    }

    private func navigationControl(_ section: ProductSection) async throws -> NativeSettingsTestControl {
        let container = try XCTUnwrap(
            InputLimitNativeViews.views(RetainedWorkspaceContainer.self, in: mainContent).first)
        let pageFrame = container.convert(container.bounds, to: nil)
        let candidates = InputLimitNativeViews.views(NSButton.self, in: mainContent).filter {
            !($0 is NSPopUpButton) && !$0.isDescendant(of: container) &&
                !RenderedGeometry.visibleRect($0).isEmpty &&
                RenderedGeometry.frame($0).intersection(pageFrame).isEmpty
        }
        XCTAssertEqual(candidates.count, ProductSection.allCases.count,
                       "Only the six real sidebar buttons may sit outside the retained page viewport.")
        let identifier = "workspace-nav-\(section.rawValue)"
        let label = section.title(using: product.model)
        if NativeSettingsTestAccessibility.elements(in: mainContent).contains(where: { $0.identifier == identifier }) {
            let control = try await NativeSettingsTestControls.resolveWhenReady(
                in: mainContent, identifier: identifier, label: label, kind: .button)
            XCTAssertTrue(control.frame.intersection(pageFrame).isEmpty,
                          "The semantic navigation ID must resolve outside the retained page.")
            InputLimitNativeViews.assertVisible(control)
            return control
        }
        let identified = candidates.filter {
            $0.identifier?.rawValue == identifier || $0.accessibilityIdentifier() == identifier
        }
        XCTAssertLessThanOrEqual(identified.count, 1, "Duplicate public navigation IDs remain an error.")
        let target: NSButton
        if !identified.isEmpty {
            target = try XCTUnwrap(identified.count == 1 ? identified.first : nil)
        } else if let cached = navigationButtons[section], candidates.contains(where: { $0 === cached }) {
            target = cached
        } else {
            // Identify the expanded sidebar from pixels inside each actual control,
            // never from a global caption match or an assumed button order/coordinate.
            var matches: [NSButton] = []
            for button in candidates {
                let rectangle = mainContent.convert(button.bounds, from: button)
                let png = try WorkspaceJourneyPixels.png(in: mainContent, rectangle: rectangle)
                let words = try NativeRenderEvidence.settingsWords(png)
                let sections = ProductSection.allCases.filter {
                    !NativeSettingsTestControls.ranges(of: $0.title(using: product.model), in: words).isEmpty
                }
                XCTAssertLessThanOrEqual(sections.count, 1, "One sidebar button must not name two destinations.")
                if let recognized = sections.first {
                    if let previous = navigationButtons[recognized],
                       candidates.contains(where: { $0 === previous }) {
                        XCTAssertTrue(previous === button, "Two live sidebar buttons must not name the same destination.")
                    }
                    navigationButtons[recognized] = button
                    if recognized == section { matches.append(button) }
                }
            }
            XCTAssertEqual(matches.count, 1,
                           "Navigation needs a unique rendered label or a public ID; compact icons must retain their identified native button.")
            if matches.count != 1 {
                try NativeRenderEvidence.retainPNG(WorkspaceJourneyPixels.png(in: mainContent),
                                                  named: "workspace-\(run)-navigation-\(section.rawValue)")
            }
            target = try XCTUnwrap(matches.count == 1 ? matches.first : nil)
            try NativeRenderEvidence.record(
                "JOURNEY \(run) identified native sidebar \(section.rawValue) using its own rendered label; " +
                "other page controls were excluded by the actual retained-page frame.")
        }
        navigationButtons[section] = target
        let control = try await NativeSettingsTestControls.remainingActionWhenReady(
            in: target, identifier: identifier, label: label)
        InputLimitNativeViews.assertVisible(control)
        return control
    }

    func navigate(_ section: ProductSection) async throws {
        let control = try await navigationControl(section)
        try control.focus(in: main)
        try await control.press()
        try await settle()
        try await CaptureProductFixture.waitFor { self.application.workspaceSection == section }
        _ = try page(section)
        XCTAssertTrue(application.inputPanel === main)
        XCTAssertTrue(main.contentView === mainContent)
        if section == .translator {
            XCTAssertTrue(try nativeEditor(in: main) === editor, "Navigation must retain the native undo stack.")
        }
    }

    func replaceDraft(_ text: String) async throws {
        try await navigate(.translator)
        let editor = try nativeEditor(in: main)
        XCTAssertTrue(main.makeFirstResponder(editor))
        editor.selectAll(nil)
        editor.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        expectedDraft = text
        try await CaptureProductFixture.waitFor { self.product.model.input.utf8.elementsEqual(text.utf8) }
        XCTAssertEqual(Array(editor.string.utf8), Array(text.utf8))
    }

    private func key(_ characters: String, code: UInt16, flags: NSEvent.ModifierFlags = [],
                     in window: NSWindow) throws -> NSEvent {
        try XCTUnwrap(NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: flags,
            timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
            context: nil, characters: characters, charactersIgnoringModifiers: characters,
            isARepeat: false, keyCode: code))
    }

    private func invokeMenu(_ title: String) throws {
        func matches(_ menu: NSMenu) -> [(NSMenu, Int)] {
            menu.items.enumerated().flatMap { index, item -> [(NSMenu, Int)] in
                if let submenu = item.submenu { return matches(submenu) }
                return item.title == title ? [(menu, index)] : []
            }
        }
        let candidates = matches(try XCTUnwrap(NSApp.mainMenu))
        XCTAssertEqual(candidates.count, 1)
        let (menu, index) = try XCTUnwrap(candidates.first)
        menu.update()
        XCTAssertTrue(menu.items[index].isEnabled)
        XCTAssertNotNil(menu.items[index].action)
        menu.performActionForItem(at: index)
    }

    private func undoAfterNavigation() async throws {
        try await navigate(.translator)
        let editor = try nativeEditor(in: main)
        XCTAssertTrue(main.makeFirstResponder(editor))
        editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        editor.breakUndoCoalescing()
        let original = expectedDraft
        let undo = try XCTUnwrap(editor.undoManager)
        undo.beginUndoGrouping()
        editor.insertText(" [edit \(step)]", replacementRange: editor.selectedRange())
        undo.endUndoGrouping()
        editor.breakUndoCoalescing()
        expectedDraft += " [edit \(step)]"
        try await settle()
        editor.setSelectedRange(NSRange(location: 1, length: 5))
        let selection = editor.selectedRanges
        XCTAssertGreaterThan(editor.selectedRange().length, 0, "Exercise a selection, not only an insertion caret.")
        try await navigate(.history)
        try await navigate(.translator)
        XCTAssertTrue(try nativeEditor(in: main) === editor)
        XCTAssertEqual(editor.selectedRanges, selection)
        XCTAssertTrue(main.makeFirstResponder(editor))
        try invokeMenu("Undo")
        expectedDraft = original
        try await CaptureProductFixture.waitFor { editor.string.utf8.elementsEqual(original.utf8) }
        XCTAssertEqual(Array(product.model.input.utf8), Array(original.utf8))
        XCTAssertTrue(undo.canRedo)
        try invokeMenu("Redo")
        expectedDraft = original + " [edit \(step)]"
        try await CaptureProductFixture.waitFor { editor.string.utf8.elementsEqual(self.expectedDraft.utf8) }
        XCTAssertEqual(Array(product.model.input.utf8), Array(expectedDraft.utf8))
    }

    func retainedEditorAndAccessibilityJourney() async throws {
        let editor = try nativeEditor(in: main)
        let selection = (editor.string as NSString).range(of: "cafe\u{301}")
        XCTAssertNotEqual(selection.location, NSNotFound)
        XCTAssertGreaterThan(selection.length, 0)
        XCTAssertTrue(main.makeFirstResponder(editor))
        editor.setSelectedRange(selection)
        let undo = try XCTUnwrap(editor.undoManager)
        try assertInactivePageAccessibility()

        // The real menu shortcut leaves the editor focused until navigation runs;
        // focusing a sidebar button first would not exercise saved responder restoration.
        XCTAssertTrue(try XCTUnwrap(NSApp.mainMenu).performKeyEquivalent(
            with: key("y", code: 16, flags: .command, in: main)))
        try await CaptureProductFixture.waitFor { self.application.workspaceSection == .history }
        try await settle()
        _ = try page(.history)
        try assertInactivePageAccessibility()
        for section in [ProductSection.dictionary, .settings, .about, .translator] {
            try await navigate(section)
            try assertInactivePageAccessibility()
            XCTAssertEqual(editor.selectedRange(), selection, "The original nonempty selection must survive every page.")
        }
        XCTAssertTrue(main.firstResponder === editor, "Returning must restore the saved native responder, not hidden focus.")
        XCTAssertTrue(editor.undoManager === undo)
        try snapshot("retained-selection")
        try await undoAfterNavigation()
        try assertInactivePageAccessibility()
        try NativeRenderEvidence.record(
            "RETAINED PAGE: nonempty Unicode selection, native undo/redo and responder restored; " +
            "all five pages visited; inactive hosts detached and absent from the in-process public AX tree. " +
            "This does not assert VoiceOver speech or external AX permission.")
    }

    private func assertInactivePageAccessibility() throws {
        let selected = application.workspaceSection
        let active = try page(selected)
        let accessibility = WorkspaceJourneyAccessibility(in: mainContent)
        XCTAssertFalse(accessibility.objects.isEmpty)
        for (section, view) in visitedPages {
            if section == selected {
                XCTAssertTrue(view === active && view.window === main)
            } else {
                XCTAssertNil(view.superview)
                XCTAssertNil(view.window)
                XCTAssertFalse(view.isDescendant(of: mainContent))
                XCTAssertFalse(accessibility.objects.contains(ObjectIdentifier(view)))
                if let responder = main.firstResponder as? NSView {
                    XCTAssertFalse(responder.isDescendant(of: view))
                }
            }
        }
        for (section, identifier) in [(ProductSection.dictionary, "dictionary-search-word"),
                                      (.settings, "settings-category"), (.about, "about-support-author")] {
            if selected != section {
                XCTAssertFalse(accessibility.identifiers.contains(identifier),
                               "An inactive page must not publish its AX control: \(identifier).")
            }
        }
        XCTAssertEqual(accessibility.containsText("Text to translate"), selected == .translator)
        // SwiftUI's virtual label IDs are not all available to this in-process
        // traversal. Native host attachment and responder checks above cover every page.
        if selected != .history { XCTAssertFalse(accessibility.containsText("Search all history")) }
        if selected != .translator {
            let editor = try XCTUnwrap(editor)
            XCTAssertNil(editor.window)
            XCTAssertFalse(accessibility.objects.contains(ObjectIdentifier(editor)))
        }
        try assertVisibleFocus()
    }

    func attachedSheetNavigationJourney() async throws {
        try await navigate(.settings)
        let settingsPage = try page(.settings)
        let categories = InputLimitNativeViews.views(NSSegmentedControl.self, in: settingsPage)
        XCTAssertEqual(categories.count, 1)
        let category = try XCTUnwrap(categories.first)
        let originalSegment = category.selectedSegment
        XCTAssertGreaterThanOrEqual(originalSegment, 0)
        XCTAssertEqual(category.label(forSegment: originalSegment), "Translation")
        try await navigate(.about)
        let aboutPage = try page(.about)
        try await press("about-support-author", "Buy the author a coffee", in: main, root: aboutPage)
        try await CaptureProductFixture.waitFor { self.main.attachedSheet?.isVisible == true }
        let sheet = try XCTUnwrap(main.attachedSheet)
        try await CaptureProductFixture.waitFor { sheet.isKeyWindow }
        try snapshot("attached-sheet")

        // These are incoming navigation requests during a real production sheet,
        // not fabricated button clicks through an AppKit modal boundary.
        application.navigate(to: .dictionary)
        application.showSettings(pane: .more)
        application.showQuickInput()
        try await settle()
        XCTAssertEqual(application.workspaceSection, .about)
        XCTAssertTrue(main.attachedSheet === sheet)
        XCTAssertTrue(sheet.isVisible && sheet.isKeyWindow)
        XCTAssertTrue(aboutPage.window === main)
        XCTAssertTrue(try page(.about) === aboutPage)
        XCTAssertNil(application.quickInputPanel)
        XCTAssertNil(application.settingsPanel)
        XCTAssertNil(application.dictionaryPanel)
        XCTAssertNil(settingsPage.window)
        XCTAssertEqual(category.selectedSegment, originalSegment)
        try await press("about-support-done", "Done", in: sheet)
        try await CaptureProductFixture.waitFor { self.main.attachedSheet == nil }
        try await navigate(.settings)
        XCTAssertTrue(try page(.settings) === settingsPage)
        XCTAssertEqual(category.selectedSegment, originalSegment,
                       "A rejected settings request must not change the retained settings pane.")
        XCTAssertEqual(category.label(forSegment: category.selectedSegment), "Translation")
        try assertHealthy()
        try NativeRenderEvidence.record(
            "SHEET GUARD COMPLETE: actual About support sheet opened/dismissed through native buttons; " +
            "incoming navigation/settings/quick-input requests leave the visible sheet and settings pane unchanged. " +
            "No QR interaction, browser opening, payment, permission approval, or configuration write.")
    }

    private func enterField(_ text: String, section: ProductSection) async throws {
        let fields = InputLimitNativeViews.views(NSTextField.self, in: try page(section)).filter {
            $0.isEditable && $0.isEnabled && !RenderedGeometry.visibleRect($0).isEmpty
        }
        XCTAssertEqual(fields.count, 1)
        let field = try XCTUnwrap(fields.first)
        XCTAssertTrue(main.makeFirstResponder(field))
        let textView = try XCTUnwrap(field.currentEditor() as? NSTextView)
        textView.selectAll(nil)
        textView.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertTrue(main.makeFirstResponder(nil))
        try await settle()
    }

    private func historySearch() async throws {
        try await navigate(.history)
        let query = "synthetic journey \(step)"
        try await enterField(query, section: .history)
        let field = try InputLimitNativeViews.field(in: page(.history))
        XCTAssertTrue(main.makeFirstResponder(field))
        main.sendEvent(try key("\r", code: 36, in: main))
        try await CaptureProductFixture.waitFor { self.helper.historyLoads.last?.query == query }
        try await settle()
        XCTAssertEqual(product.model.historySearch, query)
        XCTAssertFalse(product.model.historyBusy)
    }

    private func dictionaryLookup() async throws {
        try await navigate(.dictionary)
        expectedQuery = "example"
        try await enterField(expectedQuery, section: .dictionary)
        let count = helper.dictionaryRequests.count
        helper.automaticDictionaryReplies = false
        defer { helper.automaticDictionaryReplies = true }
        try await press("dictionary-search-submit", "Look up", in: main, root: page(.dictionary))
        let request = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(helper.dictionaryRequests.count, count + 1)
        XCTAssertEqual(request.request, .lookup(text: expectedQuery, appLanguage: "en_US",
                                                origin: "text", useCache: true, recordHistory: false))
        helper.event("completed", id: request.id, payload: DictionaryModelTests.hit(history: "disabled"))
        try await CaptureProductFixture.waitFor { self.product.model.dictionarySearch.phase == .hit }
        XCTAssertEqual(product.model.dictionarySearch.output, DictionaryModelTests.senses)
    }

    private func disclosure() async throws {
        try await navigate(.settings)
        let identifier = "provider-installation-details"
        let buttons = InputLimitNativeViews.views(NSButton.self, in: try page(.settings))
            .filter { $0.identifier?.rawValue == identifier || $0.accessibilityIdentifier() == identifier }
        XCTAssertEqual(buttons.count, 1)
        let button = try XCTUnwrap(buttons.first)
        button.scrollToVisible(button.bounds)
        try await settle()
        let expanded = button.isAccessibilityExpanded()
        try await press(identifier, "\(product.model.translationProvider.displayName) installation",
                        in: main, root: page(.settings))
        try await CaptureProductFixture.waitFor { button.isAccessibilityExpanded() != expanded }
        XCTAssertTrue(main.makeFirstResponder(button))
        main.sendEvent(try key("\r", code: 36, in: main))
        try await CaptureProductFixture.waitFor { button.isAccessibilityExpanded() == expanded }
        XCTAssertTrue(button.window === main)
        XCTAssertTrue(helper.configurationSaves.isEmpty, "Reading disclosure details must not change settings.")
    }

    private func keyboardFocus() async throws {
        try await navigate(.translator)
        let editor = try nativeEditor(in: main)
        XCTAssertTrue(main.makeFirstResponder(editor))
        let selection = NSRange(location: min(4, (editor.string as NSString).length), length: 0)
        editor.setSelectedRange(selection)
        let button = try await navigationControl(.settings)
        try button.focus(in: main)
        let before = main.firstResponder
        main.sendEvent(try key("\t", code: 48, in: main))
        try await settle()
        XCTAssertFalse(main.firstResponder === before, "Tab must move native keyboard focus.")
        try assertVisibleFocus()
        main.sendEvent(try key("\t", code: 48, flags: .shift, in: main))
        try await settle()
        try assertVisibleFocus()
        XCTAssertTrue(main.makeFirstResponder(editor))
        XCTAssertEqual(editor.selectedRange(), selection)
    }

    private func closeAuxiliaries() {
        for window in [application.resultPanel, application.quickInputPanel].compactMap({ $0 })
            where window.isVisible {
            window.standardWindowButton(.closeButton)?.performClick(nil)
            XCTAssertFalse(window.isVisible)
        }
        main.makeKeyAndOrderFront(nil)
    }

    private func restoreFromDock() async throws {
        XCTAssertFalse(application.applicationShouldHandleReopen(NSApp, hasVisibleWindows: false))
        try await CaptureProductFixture.waitFor {
            !NSApp.isHidden && self.main.isVisible && !self.main.isMiniaturized && self.main.isKeyWindow
        }
        try await settle()
        XCTAssertTrue(application.inputPanel === main)
    }

    private func closeReopen() async throws {
        closeAuxiliaries()
        let close = try XCTUnwrap(main.standardWindowButton(.closeButton))
        close.performClick(nil)
        XCTAssertFalse(main.isVisible)
        XCTAssertNil(application.settingsPanel)
        XCTAssertNil(application.historyPanel)
        XCTAssertNil(application.dictionaryPanel)
        XCTAssertNil(application.aboutPanel)
        try await restoreFromDock()
        XCTAssertEqual(application.workspaceSection, .translator)
        XCTAssertTrue(try nativeEditor(in: main) === editor)
    }

    private func hideRestore() async throws {
        closeAuxiliaries()
        XCTAssertTrue(NSApp.isRunning, "Real Hide requires an isolated running AppKit application.")
        try await CaptureProductFixture.waitFor { NSApp.isActive && self.main.isKeyWindow }
        try invokeMenu("Hide CC Translate")
        try await CaptureProductFixture.waitFor { NSApp.isHidden }
        try await restoreFromDock()
    }

    private func minimizeRestore() async throws {
        closeAuxiliaries()
        try XCTUnwrap(main.standardWindowButton(.miniaturizeButton)).performClick(nil)
        try await CaptureProductFixture.waitFor { self.main.isMiniaturized }
        try await restoreFromDock()
    }

    private func resize() async throws {
        let screen = try XCTUnwrap(main.screen).visibleFrame
        let sizes = [NSSize(width: 717, height: 600), NSSize(width: 900, height: 660),
                     NSSize(width: 1060, height: 720)]
        let desired = sizes[step % sizes.count]
        let chrome = main.frame.height - main.contentLayoutRect.height
        let size = NSSize(width: min(desired.width, screen.width),
                          height: min(desired.height, screen.height - chrome))
        XCTAssertGreaterThanOrEqual(size.width, main.contentMinSize.width)
        XCTAssertGreaterThanOrEqual(size.height, main.contentMinSize.height)
        main.setContentSize(size)
        main.setFrameOrigin(NSPoint(x: screen.midX - main.frame.width / 2,
                                    y: screen.midY - main.frame.height / 2))
        try await settle()
        XCTAssertEqual(mainContent.bounds.width, size.width, accuracy: 1)
        XCTAssertEqual(mainContent.bounds.height, size.height, accuracy: 1)
        XCTAssertTrue(screen.insetBy(dx: -1, dy: -1).contains(main.frame))
    }

    private func openQuick() async throws -> NSPanel {
        try invokeMenu("Quick translate\u{2026}")
        try await settle()
        let panel = try XCTUnwrap(application.quickInputPanel)
        XCTAssertFalse(panel === main)
        XCTAssertTrue(panel.isVisible)
        XCTAssertTrue(panel.isKeyWindow)
        return panel
    }

    private func quickCancel() async throws {
        let previousOutput = product.model.output
        let previousSubmissions = helper.translations.count
        let panel = try await openQuick()
        let editor = try nativeEditor(in: panel)
        XCTAssertTrue(panel.makeFirstResponder(editor))
        editor.selectAll(nil)
        editor.insertText("Synthetic cancelled quick draft \(step)",
                          replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertFalse(editor.string.isEmpty)
        let unsent = editor.string
        let selection = editor.selectedRange()
        let alreadyOpen = try await openQuick()
        XCTAssertTrue(alreadyOpen === panel)
        XCTAssertEqual(editor.string, unsent, "Showing an already-open quick window must preserve its unsent draft.")
        XCTAssertEqual(editor.selectedRange(), selection)
        if run == "ordinary" {
            try await settle()
            try snapshot("quick-input-draft")
        }
        try await press("quick-input-cancel", "Cancel", in: panel)
        XCTAssertFalse(panel.isVisible)
        XCTAssertEqual(Array(product.model.input.utf8), Array(expectedDraft.utf8))
        let reopened = try await openQuick()
        XCTAssertTrue(reopened === panel)
        XCTAssertTrue(try nativeEditor(in: reopened) === editor)
        XCTAssertEqual(editor.string, "", "Reopening after Cancel must start with an empty editor.")
        XCTAssertEqual(Array(product.model.input.utf8), Array(expectedDraft.utf8))
        XCTAssertEqual(product.model.output, previousOutput)
        XCTAssertEqual(helper.translations.count, previousSubmissions,
                       "Opening or cancelling quick input must neither replace nor resubmit the main draft.")
        XCTAssertTrue(reopened.makeFirstResponder(editor))
        editor.insertText("Synthetic draft closed by window chrome.",
                          replacementRange: NSRange(location: NSNotFound, length: 0))
        try XCTUnwrap(reopened.standardWindowButton(.closeButton)).performClick(nil)
        XCTAssertFalse(reopened.isVisible)
        let afterWindowClose = try await openQuick()
        XCTAssertTrue(afterWindowClose === panel)
        XCTAssertEqual(try nativeEditor(in: afterWindowClose).string, "")
        XCTAssertEqual(Array(product.model.input.utf8), Array(expectedDraft.utf8))
        XCTAssertEqual(product.model.output, previousOutput)
        try await press("quick-input-cancel", "Cancel", in: afterWindowClose)
        main.makeKeyAndOrderFront(nil)
    }

    func submitMainWithKeyboard() async throws {
        try await navigate(.translator)
        XCTAssertTrue(try main.makeFirstResponder(nativeEditor(in: main)))
        XCTAssertTrue(try XCTUnwrap(NSApp.mainMenu).performKeyEquivalent(
            with: key("\r", code: 36, flags: .command, in: main)))
        expectedSubmissions += 1
        try await finishTranslation(text: expectedDraft)
        XCTAssertFalse(application.resultPanel?.isVisible == true,
                       "Typing in the main workspace must not spawn a result popup.")
    }

    private func quickSubmit() async throws {
        let panel = try await openQuick()
        let editor = try nativeEditor(in: panel)
        let text = "Synthetic quick submission \(run)-\(step)"
        XCTAssertTrue(panel.makeFirstResponder(editor))
        editor.selectAll(nil)
        editor.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        try await press("quick-input-submit", "Translate", in: panel)
        expectedSubmissions += 1
        expectedDraft = text // Explicit quick submission loads this source into the shared result model.
        XCTAssertFalse(panel.isVisible)
        let intent = product.model.translationIntentID
        application.submitQuickInput() // A queued action arriving after the window closed.
        XCTAssertEqual(product.model.translationIntentID, intent)
        try await finishTranslation(text: text)
        XCTAssertTrue(application.resultPanel?.isVisible == true)
        let reopened = try await openQuick()
        XCTAssertTrue(reopened === panel)
        XCTAssertTrue(try nativeEditor(in: reopened) === editor)
        XCTAssertEqual(editor.string, "", "A submitted quick draft must not be appended to on the next opening.")
        XCTAssertEqual(product.model.input, text)
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        try await press("quick-input-cancel", "Cancel", in: reopened)
        main.makeKeyAndOrderFront(nil)
    }

    private func quickReplaceBusy() async throws {
        closeAuxiliaries()
        try await navigate(.translator)
        try await press("translate-input-text", "Translate", in: main, root: page(.translator))
        expectedSubmissions += 1
        try await CaptureProductFixture.waitFor { self.helper.translations.count == self.expectedSubmissions }
        let original = try XCTUnwrap(helper.translations.last)
        XCTAssertTrue(product.model.active)
        let panel = try await openQuick()
        let editor = try nativeEditor(in: panel)
        XCTAssertEqual(editor.string, "")
        let replacementText = "Synthetic replacement while busy \(run)-\(step)"
        XCTAssertTrue(panel.makeFirstResponder(editor))
        editor.insertText(replacementText, replacementRange: NSRange(location: NSNotFound, length: 0))
        editor.setMarkedText("x", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertTrue(editor.hasMarkedText())
        let composingIntent = product.model.translationIntentID
        _ = try XCTUnwrap(NSApp.mainMenu).performKeyEquivalent(
            with: key("\r", code: 36, flags: .command, in: panel))
        XCTAssertEqual(product.model.translationIntentID, composingIntent,
                       "A consumed menu key must not submit marked text.")
        XCTAssertTrue(editor.hasMarkedText())
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        XCTAssertTrue(panel.isVisible)
        editor.unmarkText()
        editor.selectAll(nil)
        editor.insertText(replacementText, replacementRange: NSRange(location: NSNotFound, length: 0))
        let submit = try await NativeSettingsTestControls.resolveWhenReady(
            in: XCTUnwrap(panel.contentView), identifier: "quick-input-submit", label: "Translate", kind: .button)
        XCTAssertTrue(submit.isEnabled, "A prior busy request must not disable valid quick input.")
        let viaKeyboard = step % 2 == 1
        if viaKeyboard {
            XCTAssertTrue(try XCTUnwrap(NSApp.mainMenu).performKeyEquivalent(
                with: key("\r", code: 36, flags: .command, in: panel)),
                "Valid quick Command-Return must replace an earlier request, rather than silently doing nothing.")
        } else {
            try submit.focus(in: panel)
            try await submit.press()
        }
        expectedDraft = replacementText
        XCTAssertFalse(panel.isVisible)
        let intent = product.model.translationIntentID
        application.submitQuickInput() // Simulate only a stale queued action, never a click on a hidden control.
        XCTAssertEqual(product.model.translationIntentID, intent)
        let cancellations = helper.messages.filter {
            $0.type == "cancel" && $0.payload["request_id"] == .string(original.id)
        }
        XCTAssertEqual(cancellations.count, 1)
        let cancellation = try XCTUnwrap(cancellations.first)
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        helper.event("completed", id: cancellation.id)
        XCTAssertEqual(helper.translations.count, expectedSubmissions,
                       "Acknowledging cancellation is not the original request's terminal event.")
        helper.event("delta", id: original.id, payload: ["text": .string("Synthetic old partial"), "submitted": .bool(true)])
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        helper.event("cancelled", id: original.id, payload: ["submitted": .bool(true)])
        expectedSubmissions += 1
        try await CaptureProductFixture.waitFor { self.helper.translations.count == self.expectedSubmissions }
        let replacement = try XCTUnwrap(helper.translations.last)
        XCTAssertNotEqual(replacement.id, original.id)
        XCTAssertEqual(replacement.text, replacementText)
        XCTAssertEqual(replacement.origin, "text")
        try await finishTranslation(text: replacementText)
        helper.event("completed", id: original.id, payload: ScaleTestSupport.result("Synthetic obsolete answer"))
        try await settle()
        XCTAssertEqual(product.model.output, "Synthetic answer \(step)")
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        let reopened = try await openQuick()
        XCTAssertEqual(try nativeEditor(in: reopened).string, "")
        XCTAssertEqual(product.model.input, replacementText)
        try await press("quick-input-cancel", "Cancel", in: reopened)
        main.makeKeyAndOrderFront(nil)
        try NativeRenderEvidence.record(
            "BUSY QUICK REPLACEMENT: route=\(viaKeyboard ? "Command-Return" : "native button"); " +
            "native button enabled; marked-text Command-Return blocked; one replacement after the old terminal; " +
            "stale action and late old result ignored. " +
            "Marked-text protocol is synthetic, not an external input-method acceptance test.")
    }

    private func navigateWhileStreaming() async throws {
        closeAuxiliaries()
        try await navigate(.translator)
        try await press("translate-input-text", "Translate", in: main, root: page(.translator))
        expectedSubmissions += 1
        try await CaptureProductFixture.waitFor { self.helper.translations.count == self.expectedSubmissions }
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("delta", id: request.id,
                     payload: ["text": .string("Synthetic streaming preview"), "submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { self.product.model.output == "Synthetic streaming preview" }
        try await navigate(.settings)
        XCTAssertTrue(product.model.active, "Changing pages must not cancel an in-progress main translation.")
        XCTAssertFalse(application.resultPanel?.isVisible == true)
        try await finishTranslation(text: expectedDraft)
        try await navigate(.translator)
        let resultViews = InputLimitNativeViews.views(NSTextView.self, in: try page(.translator))
            .filter { !$0.isEditable && !RenderedGeometry.visibleRect($0).isEmpty }
        XCTAssertTrue(resultViews.contains { $0.string == self.product.model.output },
                      "The completed synthetic result must reach the real native result text view.")
    }

    private func scrollDraft() async throws {
        let text = (0..<64).map {
            "Synthetic line \($0): retain the draft, caret, and viewport."
        }.joined(separator: "\n")
        try await replaceDraft(text)
        let editor = try nativeEditor(in: main)
        let scroll = try XCTUnwrap(editor.enclosingScrollView)
        editor.moveToEndOfDocument(nil)
        editor.scrollRangeToVisible(editor.selectedRange())
        try await settle()
        XCTAssertGreaterThan(editor.bounds.height, scroll.contentSize.height)
        let offset = scroll.documentVisibleRect.origin
        XCTAssertGreaterThan(offset.y, 0, "The real editor must actually scroll, not just change its selection.")
        let selection = editor.selectedRange()
        try await navigate(.about)
        try await navigate(.translator)
        XCTAssertEqual(editor.selectedRange(), selection)
        XCTAssertEqual(scroll.documentVisibleRect.origin.y, offset.y, accuracy: 1,
                       "A page round trip must retain the reader's position.")
        editor.moveToBeginningOfDocument(nil)
        editor.scrollRangeToVisible(editor.selectedRange())
        try await settle()
        XCTAssertEqual(editor.selectedRange().location, 0)
        XCTAssertEqual(scroll.documentVisibleRect.origin.y, 0, accuracy: 1)
    }

    private func finishTranslation(text: String) async throws {
        try await CaptureProductFixture.waitFor { self.helper.translations.count == self.expectedSubmissions }
        let request = try XCTUnwrap(helper.translations.last)
        XCTAssertEqual(request.text, text)
        XCTAssertEqual(request.origin, "text")
        XCTAssertTrue(product.model.active)
        if main.isKeyWindow {
            let intent = product.model.translationIntentID
            _ = try XCTUnwrap(NSApp.mainMenu).performKeyEquivalent(
                with: key("\r", code: 36, flags: .command, in: main))
            XCTAssertEqual(product.model.translationIntentID, intent,
                           "A consumed menu key must not replace a busy main translation.")
            XCTAssertEqual(helper.translations.count, expectedSubmissions)
            XCTAssertTrue(product.model.active)
        }
        helper.event("delta", id: request.id, payload: ["text": .string("Synthetic answer"), "submitted": .bool(true)])
        helper.event("completed", id: request.id, payload: ScaleTestSupport.result("Synthetic answer \(step)"))
        try await CaptureProductFixture.waitFor { !self.product.model.active }
        try await settle()
        XCTAssertEqual(helper.translations.count, expectedSubmissions)
        XCTAssertEqual(product.model.output, "Synthetic answer \(step)")
    }

    private func resultActions() async throws {
        let menu = try XCTUnwrap(application.statusItem?.menu)
        menu.update()
        let indexes = menu.items.indices.filter { menu.items[$0].title == "Show last result" }
        XCTAssertEqual(indexes.count, 1)
        menu.performActionForItem(at: try XCTUnwrap(indexes.first))
        try await settle()
        let panel = try XCTUnwrap(application.resultPanel)
        let root = try XCTUnwrap(panel.contentView)
        let wasPinned = product.model.resultPinned
        try await press("result-toggle-pinned", wasPinned ? "Unpin result window" : "Pin result window", in: panel)
        XCTAssertEqual(product.model.resultPinned, !wasPinned)
        XCTAssertEqual(panel.level, wasPinned ? .normal : .floating)
        XCTAssertTrue(panel.contentView === root)
        let visible = try XCTUnwrap(panel.screen).visibleFrame
        XCTAssertTrue(visible.insetBy(dx: -1, dy: -1).contains(panel.frame))
        try snapshot("result-pinned-\(!wasPinned)")
        try await press("result-open-in-window", "Open in translation window", in: panel)
        XCTAssertTrue(application.inputPanel === main)
        XCTAssertEqual(application.workspaceSection, .translator)
        XCTAssertTrue(main.isKeyWindow)
    }

    func perform(_ operation: WorkspaceJourneyOperation) async throws {
        step += 1
        let entry = "\(step):\(operation.rawValue)"
        trace.append(entry)
        try NativeRenderEvidence.record("JOURNEY \(run) begin \(entry); section=\(application.workspaceSection.rawValue)")
        do {
            switch operation {
            case .history: try await navigate(.history)
            case .dictionary: try await navigate(.dictionary)
            case .settings: try await navigate(.settings)
            case .about: try await navigate(.about)
            case .editDraft:
                try await replaceDraft("Synthetic \(run) draft \(step): e\u{301} \u{5317}\u{4eac}\nNever truncate.")
            case .undoAfterNavigation: try await undoAfterNavigation()
            case .historySearch: try await historySearch()
            case .dictionaryLookup: try await dictionaryLookup()
            case .disclosure: try await disclosure()
            case .keyboardFocus: try await keyboardFocus()
            case .closeReopen: try await closeReopen()
            case .hideRestore: try await hideRestore()
            case .minimizeRestore: try await minimizeRestore()
            case .resize: try await resize()
            case .quickCancel: try await quickCancel()
            case .quickSubmit: try await quickSubmit()
            case .resultActions: try await resultActions()
            case .navigateWhileStreaming: try await navigateWhileStreaming()
            case .scrollDraft: try await scrollDraft()
            case .quickReplaceBusy: try await quickReplaceBusy()
            }
            try await settle()
            try assertHealthy()
        } catch {
            try NativeRenderEvidence.record("JOURNEY \(run) failed at \(entry); trace=\(trace.joined(separator: ","))")
            throw error
        }
        if step % 18 == 0 { try snapshot("step-\(step)") }
        if run == "ordinary", [.dictionaryLookup, .disclosure, .about].contains(operation) {
            try snapshot(operation.rawValue)
        }
    }

    private func assertVisibleFocus() throws {
        let window = try XCTUnwrap(NSApp.keyWindow)
        XCTAssertTrue(window.isVisible && !window.isMiniaturized)
        XCTAssertTrue(window === main || window === application.resultPanel || window === application.quickInputPanel)
        if let view = window.firstResponder as? NSView {
            XCTAssertTrue(view.window === window, "A detached page must not own the first responder.")
            XCTAssertFalse(RenderedGeometry.visibleRect(view).isEmpty, "Keyboard focus must not be invisible.")
        } else {
            XCTAssertTrue(window.firstResponder === window)
        }
    }

    func assertHealthy() throws {
        XCTAssertTrue(main.isVisible && !main.isMiniaturized)
        XCTAssertFalse(NSApp.isHidden)
        XCTAssertNil(NSApp.modalWindow)
        XCTAssertNil(main.attachedSheet)
        XCTAssertNil(application.capturePanel)
        XCTAssertNil(application.updatesPanel)
        XCTAssertFalse(application.quickInputPanel?.isVisible == true)
        XCTAssertTrue(application.inputPanel === main)
        XCTAssertTrue(main.contentView === mainContent)
        let owned = NSApp.windows.filter { $0.delegate === application && $0.isVisible }
        XCTAssertLessThanOrEqual(owned.count, 2, "Only the main workspace and optional result may remain open.")
        let workspace = owned.filter {
            $0 !== application.resultPanel && $0 !== application.quickInputPanel &&
                $0 !== application.capturePanel && $0 !== application.updatesPanel
        }
        XCTAssertEqual(workspace.count, 1, "Navigation must not accumulate main/settings/history/about windows.")
        XCTAssertTrue(workspace.first === main)
        for (section, alias) in [(ProductSection.history, application.historyPanel),
                                 (.dictionary, application.dictionaryPanel), (.settings, application.settingsPanel),
                                 (.about, application.aboutPanel)] {
            if application.workspaceSection == section { XCTAssertTrue(alias === main) }
            else { XCTAssertNil(alias) }
        }
        let activePage = try page(application.workspaceSection)
        XCTAssertGreaterThan(activePage.bounds.width, 0)
        XCTAssertGreaterThan(activePage.bounds.height, 0)
        XCTAssertTrue(activePage.bounds.width.isFinite && activePage.bounds.height.isFinite)
        let pageFrame = activePage.convert(activePage.bounds, to: mainContent)
        XCTAssertTrue(mainContent.bounds.insetBy(dx: -1, dy: -1).contains(pageFrame))
        try assertVisibleFocus()
        XCTAssertEqual(Array(product.model.input.utf8), Array(expectedDraft.utf8), "An unsent draft was lost.")
        XCTAssertEqual(product.model.dictionarySearch.query, expectedQuery)
        XCTAssertEqual(helper.translations.count, expectedSubmissions, "Unexpected or duplicated model submission.")
        XCTAssertEqual(Set(helper.translations.map(\.id)).count, expectedSubmissions)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(product.copiedText.isEmpty)
        XCTAssertFalse(product.model.monitorEnabled)
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertTrue(source.requests.isEmpty)
        if application.workspaceSection == .translator {
            let editor = try nativeEditor(in: main)
            XCTAssertTrue(editor === self.editor)
            XCTAssertEqual(Array(editor.string.utf8), Array(expectedDraft.utf8))
            let range = editor.selectedRange()
            XCTAssertLessThanOrEqual(range.location, (editor.string as NSString).length)
            XCTAssertLessThanOrEqual(NSMaxRange(range), (editor.string as NSString).length)
            let scroll = try XCTUnwrap(editor.enclosingScrollView)
            XCTAssertGreaterThan(scroll.contentSize.width, 0)
            XCTAssertGreaterThan(scroll.contentSize.height, 0)
            let container = try XCTUnwrap(editor.textContainer)
            XCTAssertGreaterThan(container.containerSize.width, 0)
            XCTAssertTrue(container.containerSize.width.isFinite)
            XCTAssertLessThanOrEqual(container.containerSize.width, scroll.contentSize.width + 1)
        }
    }

    func snapshot(_ suffix: String) throws {
        let window = NSApp.keyWindow ?? main
        let root = try XCTUnwrap(window.contentView)
        XCTAssertTrue(window.isVisible)
        root.layoutSubtreeIfNeeded()
        let bitmap = try XCTUnwrap(root.bitmapImageRepForCachingDisplay(in: root.bounds))
        root.cacheDisplay(in: root.bounds, to: bitmap)
        try NativeRenderEvidence.retainPNG(
            XCTUnwrap(bitmap.representation(using: .png, properties: [:])), named: "workspace-\(run)-\(suffix)")
    }

    func finish() throws {
        try assertHealthy()
        try snapshot("complete")
        try NativeRenderEvidence.record(
            "JOURNEY \(run) COMPLETE steps=\(step), submissions=\(expectedSubmissions), " +
            "native-controls/synthetic-services; trace=\(trace.joined(separator: ","))")
    }
}

final class WorkspaceUserJourneyTests: XCTestCase {
    @MainActor
    func testAttachedNativeSheetBlocksWorkspaceAndSettingsPaneMutation() throws {
        try WorkspaceJourneyProcess.run(#function) {
            let fixture = try WorkspaceJourneyFixture(run: "sheet-navigation", bundledAbout: true)
            defer { fixture.cleanUp() }
            try await fixture.start()
            try await fixture.attachedSheetNavigationJourney()
            try fixture.finish()
        }
    }

    @MainActor
    func testRetainedNativeSelectionUndoAndInactiveAccessibilityPages() throws {
        try WorkspaceJourneyProcess.run(#function) {
            let fixture = try WorkspaceJourneyFixture(run: "retained-pages")
            defer { fixture.cleanUp() }
            try await fixture.start()
            try await fixture.retainedEditorAndAccessibilityJourney()
            try fixture.finish()
        }
    }

    @MainActor
    func testColdSelectionFallbackUsesQuickInputWithoutCreatingMainWindow() throws {
        try WorkspaceJourneyProcess.run(#function) {
            let focus = NativeTestWindowFocus()
            let previousDelegate = NSApp.delegate
            let previousMenu = NSApp.mainMenu
            let previousWindowsMenu = NSApp.windowsMenu
            let product = try ProductTestHarness()
            defer { product.cleanUp() }
            let app = AppDelegate(model: product.model, capture: CaptureModel(),
                diagnostics: NativePresentationTestSupport.offline(product.preferences, persists: false),
                loginItems: LoginItemModel(service: LoginItemTestService()))
            NSApp.delegate = app
            app.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
            defer {
                for window in [app.quickInputPanel, app.resultPanel, app.inputPanel].compactMap({ $0 }) {
                    focus.close(window)
                }
                app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
                NSApp.delegate = previousDelegate
                NSApp.mainMenu = previousMenu
                NSApp.windowsMenu = previousWindowsMenu
            }
            try NativeRenderEvidence.record(
                "COLD QUICK INPUT: synthetic selection callback and helper replies; actual native editor/buttons. " +
                "No selection read, clipboard access, TCC approval, or physical-keyboard claim.")
            app.handleSelection(.absent)
            let quick = try XCTUnwrap(app.quickInputPanel)
            let root = try XCTUnwrap(quick.contentView)
            var firstQuickClose: (resultVisible: Bool, policy: NSApplication.ActivationPolicy)?
            var captureSubmissionClose = false
            let closeObserver = NotificationCenter.default.addObserver(
                forName: NSWindow.willCloseNotification, object: quick, queue: .main) { _ in
                    MainActor.assumeIsolated {
                        if captureSubmissionClose && firstQuickClose == nil {
                            firstQuickClose = (app.resultPanel?.isVisible == true, app.desiredActivationPolicy)
                        }
                    }
                }
            defer { NotificationCenter.default.removeObserver(closeObserver) }
            try await CaptureProductFixture.waitFor {
                root.layoutSubtreeIfNeeded()
                return quick.isVisible && quick.isKeyWindow &&
                    !InputLimitNativeViews.views(NativeTranslationTextView.self, in: root).isEmpty
            }
            XCTAssertNil(app.inputPanel)
            XCTAssertNil(app.resultPanel)
            XCTAssertEqual(product.model.input, "")
            XCTAssertEqual(product.model.output, "")
            XCTAssertTrue(product.helpers.isEmpty, "Opening quick input must not construct even a synthetic helper.")
            XCTAssertEqual(product.runtimeRequests, 0)
            XCTAssertEqual(product.locatorRequests, 0)
            let editors = InputLimitNativeViews.views(NativeTranslationTextView.self, in: root)
            XCTAssertEqual(editors.count, 1)
            let editor = try XCTUnwrap(editors.first)
            XCTAssertEqual(editor.accessibilityRole(), .textArea)
            XCTAssertEqual(editor.accessibilityLabel(), "Text to translate")
            XCTAssertTrue(editor.isEditable && editor.isSelectable)
            XCTAssertFalse(RenderedGeometry.visibleRect(editor).isEmpty)
            let quickAccessibility = WorkspaceJourneyAccessibility(in: root)
            XCTAssertTrue(quickAccessibility.identifiers.contains("quick-input-editor"),
                          "The real native editor must expose its stable accessibility identifier.")
            try NativeRenderEvidence.record(
                "COLD QUICK INPUT AX: native editor role=\(editor.accessibilityRole()?.rawValue ?? "nil"), " +
                "labelConfirmed=\(editor.accessibilityLabel() == "Text to translate"), " +
                "nativeIdentifierExposed=\(quickAccessibility.identifiers.contains("quick-input-editor")), " +
                "publicGraphObjects=\(quickAccessibility.objects.count). " +
                "Native editor identity and semantics are asserted.")
            XCTAssertTrue(quick.makeFirstResponder(editor))
            editor.insertText("Synthetic unsent cold-start draft.",
                              replacementRange: NSRange(location: NSNotFound, length: 0))
            let initialCancel = try await NativeSettingsTestControls.resolveWhenReady(
                in: root, identifier: "quick-input-cancel", label: "Cancel", kind: .button)
            try await initialCancel.press()
            XCTAssertFalse(quick.isVisible)
            XCTAssertTrue(product.helpers.isEmpty)
            XCTAssertEqual(product.runtimeRequests, 0)
            XCTAssertEqual(product.locatorRequests, 0)
            XCTAssertEqual(product.model.input, "")
            app.handleSelection(.absent)
            try await CaptureProductFixture.waitFor {
                root.layoutSubtreeIfNeeded()
                return quick.isVisible && quick.isKeyWindow && editor.string.isEmpty
            }
            XCTAssertTrue(app.quickInputPanel === quick)
            XCTAssertTrue(product.helpers.isEmpty)
            XCTAssertNil(app.inputPanel)
            XCTAssertTrue(quick.makeFirstResponder(editor))
            let source = "Synthetic quick startup source."
            editor.insertText(source, replacementRange: NSRange(location: NSNotFound, length: 0))
            let submit = try await NativeSettingsTestControls.resolveWhenReady(
                in: root, identifier: "quick-input-submit", label: "Translate", kind: .button)
            InputLimitNativeViews.assertVisible(submit)
            captureSubmissionClose = true
            try await submit.press()
            XCTAssertFalse(product.helpers.isEmpty, "Only explicit submission may initialize the helper.")
            let helper = try product.ready()
            try await CaptureProductFixture.waitFor { helper.translations.count == 1 }
            let request = try XCTUnwrap(helper.translations.first)
            XCTAssertEqual(request.text, source)
            XCTAssertEqual(request.origin, "text")
            XCTAssertNil(app.inputPanel)
            XCTAssertFalse(quick.isVisible)
            let close = try XCTUnwrap(firstQuickClose)
            XCTAssertTrue(close.resultVisible, "The result must be visible before the quick panel starts closing.")
            XCTAssertEqual(close.policy, .regular, "Submitting quick input must not temporarily remove Dock identity.")
            helper.event("completed", id: request.id, payload: ScaleTestSupport.result("Synthetic cold-start result."))
            try await CaptureProductFixture.waitFor {
                !product.model.active && product.model.output == "Synthetic cold-start result." &&
                    app.resultPanel?.isVisible == true
            }
            let result = try XCTUnwrap(app.resultPanel)
            let resultRoot = try XCTUnwrap(result.contentView)
            try await CaptureProductFixture.waitFor {
                resultRoot.layoutSubtreeIfNeeded()
                return InputLimitNativeViews.views(NSTextView.self, in: resultRoot).contains {
                    !$0.isEditable && $0.string == "Synthetic cold-start result." &&
                        !RenderedGeometry.visibleRect($0).isEmpty
                }
            }
            try XCTUnwrap(result.standardWindowButton(.closeButton)).performClick(nil)
            for selection in [SelectionResult.absent, .unknown(.unsupported)] {
                app.handleSelection(selection)
                try await CaptureProductFixture.waitFor { quick.isVisible && quick.isKeyWindow }
                XCTAssertTrue(app.quickInputPanel === quick)
                XCTAssertNil(app.inputPanel)
                XCTAssertFalse(result.isVisible)
                XCTAssertEqual(product.model.input, source)
                XCTAssertEqual(product.model.output, "Synthetic cold-start result.")
                XCTAssertEqual(editor.string, "", "Quick input must reopen blank after submission or cancellation.")
                let cancel = try await NativeSettingsTestControls.resolveWhenReady(
                    in: root, identifier: "quick-input-cancel", label: "Cancel", kind: .button)
                let hint = "Couldn't read the selection. Press Command V to paste."
                let suffix: String
                let expectsHint: Bool
                if case .unknown = selection {
                    suffix = "unknown"
                    expectsHint = true
                } else {
                    suffix = "absent"
                    expectsHint = false
                }
                try await WorkspaceJourneyPixels.assertHint(hint, visible: expectsHint, in: root,
                                                           named: "workspace-cold-quick-\(suffix)")
                if case .unknown = selection {
                    let items = try XCTUnwrap(NSApp.mainMenu).items.flatMap { $0.submenu?.items ?? [] }
                        .filter { $0.action == #selector(AppDelegate.showQuickInput) }
                    XCTAssertEqual(items.count, 1)
                    let item = try XCTUnwrap(items.first)
                    let menu = try XCTUnwrap(item.menu)
                    menu.performActionForItem(at: menu.index(of: item))
                    try await WorkspaceJourneyPixels.assertHint(hint, visible: false, in: root,
                                                               named: "workspace-cold-quick-manual")
                    XCTAssertTrue(app.quickInputPanel === quick && quick.isVisible)
                    XCTAssertNil(app.inputPanel)
                    XCTAssertEqual(product.model.input, source)
                    XCTAssertEqual(product.model.output, "Synthetic cold-start result.")
                }
                try await cancel.press()
                XCTAssertFalse(quick.isVisible)
                XCTAssertEqual(helper.translations.count, 1)
                XCTAssertTrue(product.copiedText.isEmpty)
            }
            XCTAssertNil(app.inputPanel)
            XCTAssertEqual(product.model.input, source)
            XCTAssertEqual(product.model.output, "Synthetic cold-start result.")
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertFalse(product.model.monitorEnabled)
            try NativeRenderEvidence.record(
                "COLD QUICK INPUT COMPLETE: one native submission with origin=text; absent/unknown callbacks " +
                "reopen the same quick panel, preserve the prior source/result, and never create a main workspace.")
        }
    }

    @MainActor
    func testOrdinaryNativeUserDraftLookupTranslateAndRestoreJourney() throws {
        try WorkspaceJourneyProcess.run(#function) {
            let fixture = try WorkspaceJourneyFixture(run: "ordinary")
            defer { fixture.cleanUp() }
            try await fixture.start()
            for operation in [WorkspaceJourneyOperation.editDraft, .undoAfterNavigation, .historySearch,
                              .dictionaryLookup, .settings, .disclosure, .about, .keyboardFocus, .scrollDraft] {
                try await fixture.perform(operation)
            }
            try await fixture.submitMainWithKeyboard()
            for operation in [WorkspaceJourneyOperation.quickCancel, .quickSubmit, .resultActions, .resize,
                              .minimizeRestore, .hideRestore, .closeReopen, .navigateWhileStreaming, .quickReplaceBusy] {
                try await fixture.perform(operation)
            }
            try fixture.finish()
        }
    }

    @MainActor
    func testSeededNativeInteractionsSeed20260923() throws { try seeded(20_260_923, function: #function) }

    @MainActor
    func testSeededNativeInteractionsSeedC0FFEE() throws { try seeded(0xC0FFEE, function: #function) }

    @MainActor
    func testSeededNativeInteractionsSeed5EED() throws { try seeded(0x5EED, function: #function) }

    @MainActor
    private func seeded(_ seed: UInt64, function: String) throws {
        try WorkspaceJourneyProcess.run(function) {
            let fixture = try WorkspaceJourneyFixture(run: "seed-\(seed)")
            defer { fixture.cleanUp() }
            try await fixture.start()
            try await fixture.submitMainWithKeyboard()
            var random = WorkspaceJourneyRandom(seed: seed)
            var operations = (0..<3).flatMap { _ in WorkspaceJourneyOperation.allCases }
            for index in stride(from: operations.count - 1, through: 1, by: -1) {
                operations.swapAt(index, random.index(index + 1))
            }
            XCTAssertEqual(operations.count, 60)
            for operation in WorkspaceJourneyOperation.allCases {
                XCTAssertEqual(operations.filter { $0 == operation }.count, 3,
                               "Every seed must cover every interaction, not merely hope to sample it.")
            }
            let replacements = operations.indices.filter { operations[$0] == .quickReplaceBusy }
            XCTAssertTrue(replacements.contains { ($0 + 1) % 2 == 0 }, "Every seed must replace via the native button.")
            XCTAssertTrue(replacements.contains { ($0 + 1) % 2 == 1 }, "Every seed must replace via Command-Return.")
            try NativeRenderEvidence.record("JOURNEY seed=\(seed), LCG64-v1, operations=\(operations.map(\.rawValue))")
            for operation in operations { try await fixture.perform(operation) }
            try fixture.finish()
        }
    }
}
