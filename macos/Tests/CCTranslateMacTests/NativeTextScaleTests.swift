import AppKit
import Combine
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class NativeTextScaleModelTests: XCTestCase {
    @MainActor
    private func offline(_ defaults: UserDefaults, persists: Bool = true) -> ProbeModel {
        ProbeModel(preferences: defaults, persistsPreferences: persists, makeConnection: { notice in
            XCTFail("Presentation must not construct a helper.")
            return ProductTestHelper(notice: notice)
        }, runtimeProvider: {
            XCTFail("Presentation must not request a runtime.")
            throw ProbeError.bundleMissing
        }, locateCandidates: { _, _ in
            XCTFail("Presentation must not inspect CLI installations.")
            return []
        })
    }

    @MainActor
    func testMissingAndInvalidNativeScaleDefaultsTo100WithoutStartingServices() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let fresh = offline(f.preferences)
        XCTAssertEqual(fresh.nativeTextScale, .standard)
        fresh.loadPresentation()
        XCTAssertEqual(fresh.nativeTextScale, .standard)
        XCTAssertNil(f.preferences.object(forKey: NativeTextScale.preferenceKey))
        let invalidValues: [Any] = ["", "12", "125.5", "0", "-100", "200", "nan", ["150"],
                                    ["scale": "150"], Data([0xff]), false]
        for invalid in invalidValues {
            f.preferences.set(invalid, forKey: NativeTextScale.preferenceKey)
            let model = offline(f.preferences)
            model.loadPresentation()
            XCTAssertEqual(model.nativeTextScale, .standard, "\(invalid)")
            XCTAssertEqual(model.nativeTextScale.points(15), 15)
            XCTAssertEqual(model.permissions, "Not checked.")
            XCTAssertFalse(model.hasProcesses)
        }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
    }

    @MainActor
    func testAllScalesPersistReopenAndLoadOnlyOnceAsNativePresentation() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        XCTAssertEqual(NativeTextScale.allCases.map(\.rawValue), ["90", "100", "125", "150"])
        for scale in NativeTextScale.allCases {
            let model = offline(f.preferences)
            model.loadPresentation()
            model.nativeTextScale = scale
            model.interfaceLanguage = "zh"
            model.appearance = "dark"
            var notifications = 0
            model.onPresentationChanged = { notifications += 1 }
            model.persistPresentation()
            XCTAssertEqual(notifications, 1)
            let reopened = offline(f.preferences)
            reopened.loadPresentation()
            XCTAssertEqual(reopened.nativeTextScale, scale)
            XCTAssertEqual(reopened.interfaceLanguage, "zh")
            XCTAssertEqual(reopened.preferredColorScheme, .dark)
            XCTAssertEqual(f.preferences.string(forKey: NativeTextScale.preferenceKey), scale.rawValue)
            f.preferences.set("invalid", forKey: NativeTextScale.preferenceKey)
            reopened.loadPresentation()
            XCTAssertEqual(reopened.nativeTextScale, scale, "A view reopening must not reload preferences.")
            XCTAssertEqual(reopened.input, "")
            XCTAssertEqual(reopened.output, "")
            XCTAssertTrue(reopened.historyPage.isEmpty)
        }
    }

    @MainActor
    func testDiagnosticPersistenceOptOutNeitherLoadsNorOverwritesNativeScale() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set("150", forKey: NativeTextScale.preferenceKey)
        let model = offline(f.preferences, persists: false)
        model.loadPresentation()
        XCTAssertEqual(model.nativeTextScale, .standard)
        model.nativeTextScale = .smaller
        model.persistPresentation()
        XCTAssertEqual(f.preferences.string(forKey: NativeTextScale.preferenceKey), "150")
        XCTAssertFalse(model.hasProcesses)
        XCTAssertFalse(model.monitorEnabled)
    }

    @MainActor
    func testScaleDoesNotMutateBusinessConfigHistoryOrActiveTranslation() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        var config = ProductTestHarness.configuration()
        config["font_size"] = .integer(12)
        let helper = try f.ready(configuration: config)
        f.model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        ProductTestHarness.historyEntry(input: "Saved original", output: "Saved translation")
                     ], total: 1))
        let row = try XCTUnwrap(f.model.historyPage.first)
        let input = "A multiword synthetic translation already in progress."
        f.model.input = input
        f.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("delta", id: request.id, payload: ["text": .string("Partial result"), "submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { f.model.output == "Partial result" }
        let operations = helper.operations
        for scale in NativeTextScale.allCases {
            f.model.nativeTextScale = scale
            f.model.persistPresentation()
            XCTAssertEqual(helper.operations, operations)
            XCTAssertEqual(f.model.input, input)
            XCTAssertEqual(f.model.output, "Partial result")
            XCTAssertTrue(f.model.active)
            XCTAssertEqual(f.model.productPhase, .translating)
            XCTAssertEqual(f.model.historyPage.first?.id, row.id)
            XCTAssertEqual(f.model.historyPage.first?.input, row.input)
            XCTAssertEqual(f.model.historyPage.first?.output, row.output)
        }
        helper.event("completed", id: request.id, payload: ScaleTestSupport.result("Final result"))
        try await CaptureProductFixture.waitFor { !f.model.active }
        f.model.saveSettings(history: false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["font_size"], .integer(12))
        XCTAssertNil(save.config[NativeTextScale.preferenceKey])
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }
}

@MainActor
private final class ScaleTextState: ObservableObject {
    @Published var text: String
    @Published var scale: NativeTextScale = .standard
    @Published var streaming = false
    var prefix: String?

    init(_ text: String, prefix: String? = nil) {
        self.text = text
        self.prefix = prefix
    }
}

@MainActor
private struct ScaleTextSurface: View {
    @ObservedObject var state: ScaleTextState
    var body: some View {
        NativeResultText(text: state.text, formatted: true, streaming: state.streaming,
                         label: "Synthetic result", verbatimPrefix: state.prefix, textScale: state.scale)
    }
}

@MainActor
private final class ScaleTestHost<Content: View> {
    let host: NSHostingView<Content>
    let window: NSWindow

    init(_ content: Content, size: NSSize = NSSize(width: 600, height: 400)) {
        _ = NSApplication.shared
        host = NSHostingView(rootView: content)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .borderless,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        window.orderFront(nil)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func waitFor(_ condition: @MainActor () -> Bool) async throws {
        try await CaptureProductFixture.waitFor {
            self.flush()
            return condition()
        }
    }

    func text(editable: Bool = false) throws -> NSTextView {
        flush()
        return try XCTUnwrap(ScaleTestSupport.views(NSTextView.self, in: host).first { $0.isEditable == editable })
    }

    func close() {
        window.contentView = nil
        window.close()
    }
}

@MainActor
enum ScaleTestSupport {
    static func describeLayout(_ view: NSTextView, phase: String) {
        guard let scroll = view.enclosingScrollView else {
            XCTFail("An editor must remain attached to its scroll view.")
            return
        }
        print("Native editor \(phase): frame=\(view.frame) bounds=\(view.bounds) flipped=\(view.isFlipped) " +
              "clip=\(scroll.contentView.bounds) documentVisible=\(scroll.documentVisibleRect) " +
              "min=\(view.minSize) max=\(view.maxSize) selected=\(view.selectedRange()) " +
              "container=\(String(describing: view.textContainer?.containerSize)) origin=\(view.textContainerOrigin) " +
              "windowVisible=\(view.window?.isVisible == true)")
    }

    static func views<T: NSView>(_ type: T.Type, in root: NSView) -> [T] {
        (root as? T).map { [$0] } ?? root.subviews.flatMap { views(type, in: $0) }
    }

    static func font(_ view: NSTextView, at text: String) throws -> NSFont {
        let range = (view.string as NSString).range(of: text)
        XCTAssertNotEqual(range.location, NSNotFound, view.string)
        guard range.location != NSNotFound else { throw CaptureFixtureError.timeout }
        return try XCTUnwrap(view.textStorage?.attribute(.font, at: range.location, effectiveRange: nil) as? NSFont)
    }

    static func hasFont(_ view: NSTextView, size: CGFloat) -> Bool {
        guard let storage = view.textStorage, storage.length > 0,
              let font = storage.attribute(.font, at: 0, effectiveRange: nil) as? NSFont else { return false }
        return abs(font.pointSize - size) < 0.001
    }

    static func result(_ text: String) -> [String: JSONValue] {
        ["text": .string(text), "submitted": .bool(true), "cached": .bool(false),
         "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
         "history": .string("disabled"), "history_error": .null]
    }

    static var longText: String {
        (0..<80).map { "Line \($0) bilingual words \u{5317}\u{4eac} 0123456789." }.joined(separator: "\n")
    }
}

final class NativeTextScaleRenderingTests: XCTestCase {
    @MainActor
    func testActualRichFontsKeepDefaultSizesAndTraitsAcrossEveryScaleAndRoundTrips() async throws {
        let state = ScaleTextState("# Heading\n### Subheading\nBody **bold** *italic* `inline`\n```\nfenced\n```")
        let surface = ScaleTestHost(ScaleTextSurface(state: state))
        defer { surface.close() }
        let view = try surface.text()
        let original = view.string
        for scale in NativeTextScale.allCases + Array(NativeTextScale.allCases.reversed()) {
            state.scale = scale
            try await surface.waitFor {
                return ScaleTestSupport.hasFont(view, size: scale.points(18))
            }
            let baselines: [(String, CGFloat)] = [("Heading", 18), ("Subheading", 16), ("Body", 15),
                                                  ("bold", 15), ("italic", 15), ("inline", 14), ("fenced", 14)]
            for (word, base) in baselines {
                XCTAssertEqual(try ScaleTestSupport.font(view, at: word).pointSize, scale.points(base), accuracy: 0.001)
            }
            XCTAssertTrue(NSFontManager.shared.traits(of: try ScaleTestSupport.font(view, at: "bold"))
                .contains(.boldFontMask))
            XCTAssertTrue(NSFontManager.shared.traits(of: try ScaleTestSupport.font(view, at: "italic"))
                .contains(.italicFontMask))
            XCTAssertTrue(try ScaleTestSupport.font(view, at: "inline").isFixedPitch)
            XCTAssertEqual(view.string, original)
            XCTAssertTrue(try surface.text() === view)
        }
    }

    @MainActor
    func testScaleOnlyEditsAttributesAndPreservesSelectionStorageFocusAndReadingAnchor() async throws {
        let prefix = "# Literal dictionary\n**Not Markdown**\n"
        let state = ScaleTextState(prefix + ScaleTestSupport.longText, prefix: prefix)
        let surface = ScaleTestHost(ScaleTextSurface(state: state), size: NSSize(width: 440, height: 250))
        defer { surface.close() }
        let view = try surface.text()
        let storage = try XCTUnwrap(view.textStorage)
        let scroll = try XCTUnwrap(view.enclosingScrollView)
        let selected = (view.string as NSString).range(of: "Line 40")
        view.setSelectedRange(selected)
        view.scrollRangeToVisible(selected)
        let layout = try XCTUnwrap(view.layoutManager)
        let container = try XCTUnwrap(view.textContainer)
        layout.ensureLayout(for: container)
        let point = NSPoint(x: 0, y: max(0, scroll.documentVisibleRect.minY - view.textContainerOrigin.y))
        let anchor = layout.characterIndexForGlyph(at: layout.glyphIndex(for: point, in: container))
        XCTAssertGreaterThan(scroll.documentVisibleRect.minY, 0)
        let responder = surface.window.firstResponder
        let original = view.string
        var edits: [NSTextStorageEditActions] = []
        let observation = NotificationCenter.default.publisher(
            for: NSTextStorage.didProcessEditingNotification, object: storage
        ).sink { _ in edits.append(storage.editedMask) }
        defer { observation.cancel() }
        state.scale = .largest
        try await surface.waitFor { ScaleTestSupport.hasFont(view, size: 22.5) }
        XCTAssertFalse(edits.isEmpty, "Observe actual native text-storage updates, not just model publication.")
        XCTAssertFalse(edits.contains { $0.contains(.editedCharacters) })
        XCTAssertTrue(try surface.text() === view)
        XCTAssertTrue(view.textStorage === storage)
        XCTAssertTrue(surface.window.firstResponder === responder)
        XCTAssertEqual(view.string, original)
        XCTAssertTrue(view.string.hasPrefix(prefix), "Literal dictionary text must not be reinterpreted on resize.")
        XCTAssertEqual(view.selectedRange(), selected)
        let line = layout.lineFragmentRect(forGlyphAt: layout.glyphIndexForCharacter(at: anchor), effectiveRange: nil)
            .offsetBy(dx: view.textContainerOrigin.x, dy: view.textContainerOrigin.y)
        XCTAssertTrue(scroll.documentVisibleRect.intersects(line), "The same passage stays visible after reflow.")
    }

    @MainActor
    func testStreamingAndSelectedPlainTextKeepUniformScaleWithoutPrematureMarkdownConversion() async throws {
        let state = ScaleTextState("Body **bold**")
        state.streaming = true
        let surface = ScaleTestHost(ScaleTextSurface(state: state))
        defer { surface.close() }
        let view = try surface.text()
        let selected = NSRange(location: 0, length: 4)
        view.setSelectedRange(selected)
        state.scale = .largest
        state.text += "\nNext `inline`"
        try await surface.waitFor { view.string == state.text && ScaleTestSupport.hasFont(view, size: 22.5) }
        XCTAssertEqual(try ScaleTestSupport.font(view, at: "Next").pointSize, 22.5)
        state.streaming = false
        state.scale = .smaller
        try await surface.waitFor { ScaleTestSupport.hasFont(view, size: 13.5) }
        XCTAssertEqual(view.string, state.text)
        XCTAssertEqual(view.selectedRange(), selected)
        XCTAssertEqual(try ScaleTestSupport.font(view, at: "`inline`").pointSize, 13.5)
        XCTAssertTrue(try surface.text() === view)
        view.setSelectedRange(NSRange(location: 0, length: 0))
        state.scale = .standard
        try await surface.waitFor { view.string == "Body bold\nNext inline" }
        XCTAssertEqual(try ScaleTestSupport.font(view, at: "inline").pointSize, 14)
    }

    @MainActor
    func testMainEditorPreservesUserEditsCaretScrollUndoAndIdentityWhenScaling() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.input = ScaleTestSupport.longText
        let surface = ScaleTestHost(TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
                                    size: NSSize(width: 900, height: 620))
        defer { surface.close() }
        let editor = try surface.text(editable: true)
        XCTAssertEqual(try ScaleTestSupport.font(editor, at: "Line 0").pointSize, 15)
        let scroll = try XCTUnwrap(editor.enclosingScrollView)
        XCTAssertGreaterThan(editor.bounds.height, scroll.contentView.bounds.height,
                             "Initial text must have a scrollable document before the first edit.")
        ScaleTestSupport.describeLayout(editor, phase: "main initial")
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        let undo = try XCTUnwrap(editor.undoManager)
        undo.removeAllActions()
        let insertion = (editor.string as NSString).range(of: "Line 40").location
        undo.beginUndoGrouping()
        editor.insertText("EDIT ", replacementRange: NSRange(location: insertion, length: 0))
        undo.endUndoGrouping()
        let edited = (ScaleTestSupport.longText as NSString).replacingCharacters(
            in: NSRange(location: insertion, length: 0), with: "EDIT ")
        try await surface.waitFor { f.model.input == edited }
        ScaleTestSupport.describeLayout(editor, phase: "main edited")
        editor.scrollRangeToVisible(editor.selectedRange())
        ScaleTestSupport.describeLayout(editor, phase: "main scrolled")
        let selection = editor.selectedRange()
        XCTAssertGreaterThan(scroll.documentVisibleRect.minY, 0)
        XCTAssertEqual(NativeTextViewport(view: editor, scroll: scroll).origin, scroll.documentVisibleRect.origin)
        let layout = try XCTUnwrap(editor.layoutManager)
        let container = try XCTUnwrap(editor.textContainer)
        let point = NSPoint(x: 0, y: max(0, scroll.documentVisibleRect.minY - editor.textContainerOrigin.y))
        let anchor = layout.characterIndexForGlyph(at: layout.glyphIndex(for: point, in: container))
        var inputWrites = 0
        let observation = f.model.$input.dropFirst().sink { _ in inputWrites += 1 }
        defer { observation.cancel() }
        f.model.nativeTextScale = .largest
        try await surface.waitFor { ScaleTestSupport.hasFont(editor, size: 22.5) }
        ScaleTestSupport.describeLayout(editor, phase: "main scaled")
        XCTAssertEqual(inputWrites, 0)
        XCTAssertEqual(f.model.input, edited)
        XCTAssertEqual(editor.string, edited)
        XCTAssertEqual(editor.selectedRange(), selection)
        XCTAssertGreaterThan(scroll.documentVisibleRect.minY, 0)
        let line = layout.lineFragmentRect(forGlyphAt: layout.glyphIndexForCharacter(at: anchor), effectiveRange: nil)
            .offsetBy(dx: editor.textContainerOrigin.x, dy: editor.textContainerOrigin.y)
        XCTAssertTrue(scroll.documentVisibleRect.intersects(line), "The same passage stays visible after reflow.")
        XCTAssertTrue(try surface.text(editable: true) === editor)
        XCTAssertTrue(surface.window.firstResponder === editor)
        XCTAssertTrue(undo.canUndo)
        observation.cancel()
        undo.undo()
        try await surface.waitFor { f.model.input == ScaleTestSupport.longText }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testMainEditorKeepsUncommittedMarkedTextAndSelectionAcrossScaleChange() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        _ = try f.ready()
        f.model.input = "Original "
        let surface = ScaleTestHost(TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
                                    size: NSSize(width: 900, height: 620))
        defer { surface.close() }
        let editor = try surface.text(editable: true)
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        editor.setMarkedText("\u{62fc}", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: 9, length: 0))
        XCTAssertTrue(editor.hasMarkedText())
        let text = editor.string
        let marked = editor.markedRange()
        let selection = editor.selectedRange()
        let committedBeforeScaling = f.model.input
        let storage = try XCTUnwrap(editor.textStorage)
        var characterEdits = 0
        let updates = NotificationCenter.default.publisher(
            for: NSTextStorage.didProcessEditingNotification, object: storage
        ).sink { _ in
            if storage.editedMask.contains(.editedCharacters) { characterEdits += 1 }
        }
        defer { updates.cancel() }
        f.model.nativeTextScale = .larger
        try await surface.waitFor { ScaleTestSupport.hasFont(editor, size: 18.75) }
        XCTAssertTrue(editor.hasMarkedText())
        XCTAssertEqual(editor.markedRange(), marked)
        XCTAssertEqual(editor.selectedRange(), selection)
        XCTAssertEqual(editor.string, text)
        XCTAssertTrue(surface.window.firstResponder === editor)
        XCTAssertEqual(try ScaleTestSupport.font(editor, at: "\u{62fc}").pointSize, 18.75)
        XCTAssertEqual(f.model.input, committedBeforeScaling)
        XCTAssertEqual(characterEdits, 0, "Scaling composition must never replace text storage characters.")
        updates.cancel()
        editor.unmarkText()
        try await surface.waitFor { f.model.input == text }
        XCTAssertFalse(editor.hasMarkedText())
        XCTAssertTrue(try surface.text(editable: true) === editor)
        XCTAssertTrue(f.helpers.allSatisfy { $0.translations.isEmpty })
    }

    @MainActor
    func testOCREditorKeepsMarkedTextThroughRepeatedScaleChangesAndCommitsTheSelectedCandidate() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let original = "Reviewed "
        let job = CaptureTestOCR(text: original)
        var jobs = 0
        let capture = CaptureModel(screen: ScreenProbe(
            source: source, makeOCRJob: { jobs += 1; return job }, notificationCenter: NotificationCenter()))
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let surface = ScaleTestHost(CaptureView(capture: capture, model: f.model,
                                                captureAgain: {}, reselect: {}, close: {}),
                                    size: NSSize(width: 760, height: 680))
        defer { surface.close() }
        let editor = try surface.text(editable: true)
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        let offset = (editor.string as NSString).length
        editor.setMarkedText("\u{62fc}", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: offset, length: 0))
        XCTAssertTrue(editor.hasMarkedText())
        let text = editor.string
        let marked = editor.markedRange()
        let selection = editor.selectedRange()
        let boundBeforeScaling = capture.text
        var writes = 0
        let observation = capture.$text.dropFirst().sink { _ in writes += 1 }
        defer { observation.cancel() }
        for scale in [NativeTextScale.larger, .largest, .smaller, .standard] {
            f.model.nativeTextScale = scale
            try await surface.waitFor { ScaleTestSupport.hasFont(editor, size: scale.points(15)) }
            XCTAssertTrue(editor.hasMarkedText())
            XCTAssertEqual(editor.markedRange(), marked)
            XCTAssertEqual(editor.selectedRange(), selection)
            XCTAssertEqual(editor.string, text)
            XCTAssertEqual(capture.text, boundBeforeScaling)
            XCTAssertEqual(writes, 0)
            XCTAssertTrue(surface.window.firstResponder === editor)
            XCTAssertTrue(try surface.text(editable: true) === editor)
        }
        observation.cancel()
        editor.insertText("\u{62fc}\u{97f3}", replacementRange: NSRange(location: NSNotFound, length: 0))
        let committed = String(text.prefix(offset)) + "\u{62fc}\u{97f3}"
        try await surface.waitFor { capture.text == committed }
        XCTAssertFalse(editor.hasMarkedText())
        XCTAssertEqual(editor.string, committed)
        XCTAssertEqual(jobs, 1)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testExplicitClearAndHistoryReuseReplaceCompositionWithoutResurrectingItOnScaleChange() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.input = "Original "
        let surface = ScaleTestHost(TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
                                    size: NSSize(width: 900, height: 620))
        defer { surface.close() }
        let editor = try surface.text(editable: true)
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        editor.setMarkedText("\u{62fc}", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: 9, length: 0))
        XCTAssertTrue(editor.hasMarkedText())
        f.model.clearTranslation()
        try await surface.waitFor { editor.string.isEmpty && !editor.hasMarkedText() }
        f.model.nativeTextScale = .largest
        f.model.reuseHistory(.init(id: "replacement", input: "Saved original", output: "Saved result"))
        try await surface.waitFor { editor.string == "Saved original" && ScaleTestSupport.hasFont(editor, size: 22.5) }
        XCTAssertEqual(f.model.input, "Saved original")
        XCTAssertEqual(f.model.output, "Saved result")
        XCTAssertFalse(editor.hasMarkedText())
        XCTAssertTrue(try surface.text(editable: true) === editor)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testOCREditorScalesReviewedTextWithoutRepeatingCaptureRecognitionOrTranslation() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let job = CaptureTestOCR(text: ScaleTestSupport.longText)
        var jobs = 0
        let capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { jobs += 1; return job },
                                                       notificationCenter: NotificationCenter()))
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let surface = ScaleTestHost(CaptureView(capture: capture, model: f.model,
                                                captureAgain: {}, reselect: {}, close: {}),
                                    size: NSSize(width: 760, height: 680))
        defer { surface.close() }
        let editor = try surface.text(editable: true)
        XCTAssertEqual(try ScaleTestSupport.font(editor, at: "Line 0").pointSize, 15)
        let scroll = try XCTUnwrap(editor.enclosingScrollView)
        XCTAssertGreaterThan(editor.bounds.height, scroll.contentView.bounds.height,
                             "Recognized text must have a scrollable document before review.")
        ScaleTestSupport.describeLayout(editor, phase: "OCR initial")
        XCTAssertTrue(surface.window.makeFirstResponder(editor))
        editor.insertText("Reviewed ", replacementRange: NSRange(location: 0, length: 0))
        try await surface.waitFor { capture.text.hasPrefix("Reviewed ") }
        let reviewed = capture.text
        let selected = (editor.string as NSString).range(of: "Line 30")
        editor.setSelectedRange(selected)
        editor.scrollRangeToVisible(selected)
        ScaleTestSupport.describeLayout(editor, phase: "OCR scrolled")
        XCTAssertGreaterThan(scroll.documentVisibleRect.minY, 0)
        XCTAssertEqual(NativeTextViewport(view: editor, scroll: scroll).origin, scroll.documentVisibleRect.origin)
        let layout = try XCTUnwrap(editor.layoutManager)
        let container = try XCTUnwrap(editor.textContainer)
        let point = NSPoint(x: 0, y: max(0, scroll.documentVisibleRect.minY - editor.textContainerOrigin.y))
        let anchor = layout.characterIndexForGlyph(at: layout.glyphIndex(for: point, in: container))
        let cancellations = job.cancelCount
        var writes = 0
        let observation = capture.$text.dropFirst().sink { _ in writes += 1 }
        defer { observation.cancel() }
        f.model.nativeTextScale = .largest
        try await surface.waitFor { ScaleTestSupport.hasFont(editor, size: 22.5) }
        ScaleTestSupport.describeLayout(editor, phase: "OCR scaled")
        XCTAssertEqual(writes, 0)
        XCTAssertEqual(capture.text, reviewed)
        XCTAssertEqual(editor.string, reviewed)
        XCTAssertEqual(editor.selectedRange(), selected)
        XCTAssertTrue(try surface.text(editable: true) === editor)
        XCTAssertGreaterThan(scroll.documentVisibleRect.minY, 0)
        let line = layout.lineFragmentRect(forGlyphAt: layout.glyphIndexForCharacter(at: anchor), effectiveRange: nil)
            .offsetBy(dx: editor.textContainerOrigin.x, dy: editor.textContainerOrigin.y)
        XCTAssertTrue(scroll.documentVisibleRect.intersects(line), "Reviewed text keeps its reading position.")
        XCTAssertEqual(capture.phase, .ready)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(job.cancelCount, cancellations)
        XCTAssertEqual(jobs, 1)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
    }

    @MainActor
    func testHistoryOriginalTranslationAndOutputOnlyOCRScaleWithoutInventingOrMutatingInput() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        for row in [
            ProbeModel.HistoryRow(id: "text", input: "Saved original", output: "Saved translation"),
            ProbeModel.HistoryRow(id: "dictionary", input: "word", output: "**Literal definition**",
                                   kind: "dict", signature: "local-dictionary|synthetic"),
            ProbeModel.HistoryRow(id: "image", input: "", output: "Image output", kind: "ocr", hasOriginalInput: false)
        ] {
            f.model.nativeTextScale = .standard
            let surface = ScaleTestHost(HistoryTranslationDetail(model: f.model, row: row, useEntry: {}))
            defer { surface.close() }
            let views = ScaleTestSupport.views(NSTextView.self, in: surface.host)
            XCTAssertEqual(views.count, row.hasOriginalInput ? 2 : 1)
            for view in views { XCTAssertTrue(ScaleTestSupport.hasFont(view, size: 15)) }
            let strings = views.map(\.string)
            f.model.nativeTextScale = .larger
            try await surface.waitFor { views.allSatisfy { ScaleTestSupport.hasFont($0, size: 18.75) } }
            XCTAssertEqual(views.map(\.string), strings)
            XCTAssertTrue(views.contains { $0.string == row.output })
            if row.hasOriginalInput { XCTAssertTrue(views.contains { $0.string == row.input }) }
            else { XCTAssertFalse(views.contains { $0.string.isEmpty }) }
            XCTAssertTrue(f.helpers.isEmpty)
        }
    }

    @MainActor
    func testFloatingResultAndLiveSupplementRetainRootAndScaleThroughCancellation() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.reuseHistory(.init(id: "scale-result", input: "Original", output: "Primary result"))
        let surface = ScaleTestHost(TranslationResultView(model: f.model, compact: true))
        defer { surface.close() }
        let view = try surface.text()
        f.model.performResultAction(.summary)
        let action = try XCTUnwrap(helper.resultActions.last)
        helper.event("delta", id: action.id, payload: ["text": .string("Supplement"), "submitted": .bool(true)])
        try await surface.waitFor { view.string.contains("Supplement") }
        let operations = helper.operations
        f.model.nativeTextScale = .largest
        try await surface.waitFor { ScaleTestSupport.hasFont(view, size: 22.5) }
        XCTAssertEqual(helper.operations, operations)
        XCTAssertEqual(try ScaleTestSupport.font(view, at: "Supplement").pointSize, 22.5)
        XCTAssertTrue(try surface.text() === view)
        f.model.cancel()
        helper.event("cancelled", id: action.id, payload: ["submitted": .bool(true)])
        f.model.nativeTextScale = .smaller
        try await surface.waitFor { ScaleTestSupport.hasFont(view, size: 13.5) }
        XCTAssertTrue(try surface.text() === view)
        XCTAssertEqual(f.model.primaryResult, "Primary result")
        XCTAssertEqual(helper.resultActions.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testNativePickerIsUsableOfflinePersistsItsSelectionAndKeepsControlTypography() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        let surface = ScaleTestHost(Form { NativeTextScalePicker(model: f.model) }.formStyle(.grouped))
        defer { surface.close() }
        let button = try XCTUnwrap(ScaleTestSupport.views(NSPopUpButton.self, in: surface.host).first)
        XCTAssertTrue(button.isEnabled)
        XCTAssertFalse(f.model.settingsReady)
        let fontSize = button.font?.pointSize
        print("Native text-size menu before opening: \(button.itemTitles.map(\.debugDescription)); button action=\(String(describing: button.action))")
        surface.window.orderFront(nil)
        var inspectedMenu = false
        var invokedItem = false
        let timer = Timer(timeInterval: 0.05, repeats: false) { _ in
            MainActor.assumeIsolated {
                inspectedMenu = true
                guard let menu = button.menu else {
                    XCTFail("The opened native picker has no menu.")
                    return
                }
                defer { menu.cancelTrackingWithoutAnimation() }
                let inventory = menu.items.enumerated().map { index, item in
                    "\(index): \(item.title.debugDescription) scalars=\(item.title.unicodeScalars.map(\.value)) action=\(String(describing: item.action))"
                }.joined(separator: "\n")
                print("Native text-size menu after opening:\n\(inventory)")
                let matches = menu.items.indices.filter {
                    menu.items[$0].title.filter { !$0.isWhitespace } == "150%"
                }
                XCTAssertEqual(matches.count, 1, inventory)
                guard matches.count == 1, let index = matches.first else { return }
                let item = menu.items[index]
                XCTAssertTrue(item.isEnabled, inventory)
                XCTAssertNotNil(item.action, inventory)
                guard item.isEnabled, item.action != nil else { return }
                menu.performActionForItem(at: index)
                invokedItem = true
            }
        }
        RunLoop.main.add(timer, forMode: .eventTracking)
        defer { timer.invalidate() }
        // SwiftUI owns item actions and may populate the menu only when it opens.
        button.performClick(nil)
        XCTAssertTrue(inspectedMenu, "Selection must inspect the opened native menu, not guessed titles before tracking.")
        XCTAssertTrue(invokedItem, "Selection must dispatch the real NSMenuItem action.")
        try await surface.waitFor {
            f.model.nativeTextScale == .largest &&
                f.preferences.string(forKey: NativeTextScale.preferenceKey) == "150" &&
                button.title.filter { !$0.isWhitespace } == "150%"
        }
        XCTAssertEqual(button.font?.pointSize, fontSize)
        XCTAssertEqual(button.title.filter { !$0.isWhitespace }, "150%")
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }
}
