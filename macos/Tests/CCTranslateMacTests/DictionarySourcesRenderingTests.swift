import AppKit
import Combine
import SwiftUI
import Vision
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private final class DictionarySourcesHost {
    let host: NSHostingView<TranslationResultView>
    let window: NSWindow

    init(model: ProbeModel) {
        _ = NSApplication.shared
        let size = NSSize(width: 620, height: 580)
        host = NSHostingView(rootView: TranslationResultView(model: model, compact: true))
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        window.center()
        // NSPopover requires a visible anchor. Never activate the app or send global CGEvents.
        window.orderFront(nil)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func close() {
        for button in Self.views(DictionarySourcesButton.self, in: host) { button.retire() }
        window.contentView = nil
        window.close()
    }

    func button() throws -> DictionarySourcesButton {
        flush()
        let buttons = Self.views(DictionarySourcesButton.self, in: host)
        XCTAssertEqual(buttons.count, 1)
        return try XCTUnwrap(buttons.first)
    }

    static func views<T: NSView>(_ type: T.Type, in root: NSView) -> [T] {
        (root as? T).map { [$0] } ?? root.subviews.flatMap { views(type, in: $0) }
    }

    func press(_ button: DictionarySourcesButton,
               duringTracking update: @escaping @MainActor () throws -> Void) throws {
        let point = button.convert(NSPoint(x: button.bounds.midX, y: button.bounds.midY), to: nil)
        let down = try XCTUnwrap(NSEvent.mouseEvent(
            with: .leftMouseDown, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1))
        let up = try XCTUnwrap(NSEvent.mouseEvent(
            with: .leftMouseUp, location: point, modifierFlags: [], timestamp: down.timestamp + 0.1,
            windowNumber: window.windowNumber, context: nil, eventNumber: 2, clickCount: 1, pressure: 0))
        var updatedDuringTracking = false
        let timer = Timer(timeInterval: 0.01, repeats: false) { _ in
            MainActor.assumeIsolated {
                defer { NSApp.postEvent(up, atStart: true) }
                updatedDuringTracking = true
                do { try update() }
                catch { XCTFail("Async source update failed: \(error)") }
            }
        }
        RunLoop.main.add(timer, forMode: .eventTracking)
        defer { timer.invalidate() }
        button.mouseDown(with: down)
        XCTAssertTrue(updatedDuringTracking, "The update must execute between real native down/up events.")
        flush()
    }
}

final class DictionarySourcesInteractionTests: XCTestCase {
    @MainActor
    func testNativePressAsyncSupplementReleaseRetainsButtonAndOpensSources() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let local = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: local)
        let surface = DictionarySourcesHost(model: f.model)
        defer { surface.close() }
        let button = try surface.button()
        XCTAssertFalse(button.sourcesPopover.isShown)
        try surface.press(button) {
            f.model.performResultAction(.summary)
            local.stopped()
            let provider = try f.ready()
            let request = try XCTUnwrap(provider.resultActions.last)
            provider.event("delta", id: request.id, payload: ["text": .string("async"), "submitted": .bool(true)])
            provider.event("completed", id: request.id,
                           payload: DictionarySourcesFixture.supplement("async supplement"))
            surface.flush()
            XCTAssertTrue(try surface.button() === button)
            XCTAssertFalse(button.sourcesPopover.isShown, "Opening is a mouse-up action.")
        }
        XCTAssertTrue(try surface.button() === button)
        XCTAssertTrue(button.sourcesPopover.isShown)
        XCTAssertTrue(DictionarySourcesHost.views(NSTextView.self, in: surface.host)
            .contains { $0.string.contains("async supplement") })
        XCTAssertEqual(button.sourcesContent.textView.string.components(separatedBy: "Source ID:").count - 1, 2)
    }

    @MainActor
    func testOpenPopoverSurvivesEqualSourceNewResultLocaleThemeAndKeepsNativeSelection() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        let surface = DictionarySourcesHost(model: f.model)
        defer { surface.close() }
        let button = try surface.button()
        button.performClick(nil)
        XCTAssertTrue(button.sourcesPopover.isShown)
        let controller = button.sourcesPopover.contentViewController
        let text = button.sourcesContent.textView
        text.setSelectedRange(NSRange(location: 0, length: 9))
        try DictionarySourcesFixture.publish(f, helper: helper, text: DictionaryModelTests.senses + "\nAnother result")
        f.model.interfaceLanguage = "zh"
        f.model.appearance = "dark"
        surface.window.appearance = NSAppearance(named: .darkAqua)
        surface.flush()
        XCTAssertTrue(try surface.button() === button)
        XCTAssertTrue(button.sourcesPopover.contentViewController === controller)
        XCTAssertTrue(button.sourcesContent.textView === text)
        XCTAssertTrue(button.sourcesPopover.isShown)
        XCTAssertEqual(text.selectedRange(), NSRange(location: 0, length: 9))
        XCTAssertEqual(button.title, f.model.text("Sources & licenses", "\u{6765}\u{6e90}\u{4e0e}\u{8bb8}\u{53ef}"))
        XCTAssertEqual(button.accessibilityLabel(), button.title)
        XCTAssertTrue(text.string.contains("Literal <license> [link](https://example.invalid)"))
        XCTAssertFalse(text.isEditable)
        XCTAssertTrue(text.isSelectable)
        XCTAssertFalse(text.isAutomaticLinkDetectionEnabled)
        XCTAssertFalse(text.isAutomaticDataDetectionEnabled)
        XCTAssertTrue(f.copiedText.isEmpty, "Opening source metadata must not access the clipboard.")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testChangedSourceDuringPressCannotOpenWrongPopoverAndClearRetiresOpenContext() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        let surface = DictionarySourcesHost(model: f.model)
        defer { surface.close() }
        let button = try surface.button()
        try surface.press(button) {
            try DictionarySourcesFixture.publish(f, helper: helper, version: "replacement")
            surface.flush()
            XCTAssertTrue(try surface.button() === button)
        }

        XCTAssertFalse(button.sourcesPopover.isShown, "The old press does not authorize the changed source.")
        button.performClick(nil)
        XCTAssertTrue(button.sourcesPopover.isShown)
        XCTAssertTrue(button.sourcesContent.textView.string.contains("replacement"))
        try DictionarySourcesFixture.publish(f, helper: helper, version: "changed again")
        surface.flush()
        XCTAssertFalse(button.sourcesPopover.isShown)
        button.performClick(nil)
        XCTAssertTrue(button.sourcesPopover.isShown)
        f.model.clearTranslation()
        surface.flush()
        XCTAssertTrue(DictionarySourcesHost.views(DictionarySourcesButton.self, in: surface.host).isEmpty)
        XCTAssertFalse(button.sourcesPopover.isShown)
        button.performClick(nil)
        XCTAssertFalse(button.sourcesPopover.isShown)
    }

    @MainActor
    func testOpenPopoverSurvivesStreamingSupplementAndCancellation() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let local = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: local)
        let surface = DictionarySourcesHost(model: f.model)
        defer { surface.close() }
        let button = try surface.button()
        button.performClick(nil)
        let controller = button.sourcesPopover.contentViewController
        let text = button.sourcesContent.textView
        text.setSelectedRange(NSRange(location: 0, length: 9))
        f.model.performResultAction(.summary)
        local.stopped()
        let provider = try f.ready()
        let request = try XCTUnwrap(provider.resultActions.last)
        let rendered = expectation(description: "Streaming dictionary supplement is published")
        let observation = f.model.$output.filter { $0.contains("streaming supplement") }
            .prefix(1).sink { _ in rendered.fulfill() }
        defer { observation.cancel() }
        provider.event("delta", id: request.id, payload: ["text": .string("streaming supplement"),
                                                         "submitted": .bool(true)])
        await fulfillment(of: [rendered], timeout: 2)
        surface.flush()
        XCTAssertTrue(f.model.output.contains("streaming supplement"))
        XCTAssertTrue(try surface.button() === button)
        XCTAssertTrue(button.sourcesPopover.isShown)
        XCTAssertTrue(button.sourcesPopover.contentViewController === controller)
        f.model.cancel()
        provider.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        surface.flush()
        XCTAssertTrue(try surface.button() === button)
        XCTAssertTrue(button.sourcesPopover.isShown)
        XCTAssertTrue(button.sourcesContent.textView === text)
        XCTAssertEqual(text.selectedRange(), NSRange(location: 0, length: 9))
        XCTAssertEqual(button.sources, f.model.resultSources)
    }

    @MainActor
    func testPopoverNativeEscapeAndExplicitCloseRestoreFocusToTrigger() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        let surface = DictionarySourcesHost(model: f.model)
        defer { surface.close() }
        let button = try surface.button()
        for escape in [true, false] {
            button.performClick(nil)
            XCTAssertTrue(button.sourcesPopover.isShown)
            let text = button.sourcesContent.textView
            XCTAssertTrue(text.window?.firstResponder === text)
            if escape {
                let event = try XCTUnwrap(NSEvent.keyEvent(
                    with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                    windowNumber: try XCTUnwrap(text.window?.windowNumber), context: nil,
                    characters: "\u{1b}", charactersIgnoringModifiers: "\u{1b}", isARepeat: false, keyCode: 53))
                text.keyDown(with: event)
            } else {
                button.sourcesContent.closeButton.performClick(nil)
            }
            XCTAssertFalse(button.sourcesPopover.isShown)
            XCTAssertTrue(surface.window.firstResponder === button)
        }
    }
}

@MainActor
private struct DictionarySourcesContentPreview: NSViewControllerRepresentable {
    let sources: [DictionarySource]
    let labels: DictionarySourcesLabels

    func makeNSViewController(context: Context) -> DictionarySourcesContent {
        let controller = DictionarySourcesContent()
        controller.update(sources: sources, labels: labels)
        return controller
    }

    func updateNSViewController(_ controller: DictionarySourcesContent, context: Context) {
        controller.update(sources: sources, labels: labels)
    }
}

extension ProductRenderingTests {
    @MainActor
    func testDictionaryResultSourcesControlRendersInBothLanguagesThemesAndNarrowLayout() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        for (locale, scheme) in [("en", ColorScheme.light), ("en", .dark), ("zh", .light), ("zh", .dark)] {
            f.model.interfaceLanguage = locale
            f.model.appearance = scheme == .dark ? "dark" : "light"
            let png = try render(
                TranslationResultView(model: f.model, compact: true),
                named: "dictionary-sources-result-\(locale)-\(scheme == .dark ? "dark" : "light")",
                size: NSSize(width: 420, height: 300), scheme: scheme, inspect: { host in
                    let buttons = DictionarySourcesHost.views(DictionarySourcesButton.self, in: host)
                    XCTAssertEqual(buttons.count, 1)
                    guard let button = buttons.first else { return XCTFail("Missing actual result source control") }
                    XCTAssertGreaterThanOrEqual(button.bounds.height, 24)
                    XCTAssertTrue(host.bounds.contains(button.convert(button.bounds, to: host)),
                                  "The sources control must fit the existing floating-panel minimum.")
                    XCTAssertEqual(button.sources.count, 2)
                    XCTAssertFalse(button.sourcesPopover.isShown)
                    XCTAssertEqual(button.accessibilityLabel(), button.title)
                })
            let words = try sourceWords(png, locale: locale)
            let expected = locale == "zh" ? ["\u{6765}\u{6e90}\u{4e0e}\u{8bb8}\u{53ef}"] : ["sources", "licenses"]
            for label in expected { XCTAssertTrue(words.contains(label), words) }
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testSourcesPopoverContentRendersLiteralLongMetadataWithScrollingAndNoLinks() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        let license = "Synthetic license\n" + String(repeating: "Literal <license> [link](https://example.invalid)\n", count: 80)
        try DictionarySourcesFixture.publish(f, helper: helper, license: license)
        for (locale, scheme) in [("en", ColorScheme.light), ("en", .dark), ("zh", .light), ("zh", .dark)] {
            f.model.interfaceLanguage = locale
            f.model.appearance = scheme == .dark ? "dark" : "light"
            var labels: DictionarySourcesLabels?
            // Obtain the localized labels from the production result control, not a translated test mock.
            _ = try render(TranslationResultView(model: f.model, compact: true),
                           named: "dictionary-sources-long-result-\(locale)-\(scheme == .dark ? "dark" : "light")",
                           size: NSSize(width: 520, height: 640), scheme: scheme, inspect: { host in
                guard let button = DictionarySourcesHost.views(DictionarySourcesButton.self, in: host).first else {
                    return XCTFail("Missing source control")
                }
                labels = button.labels
            })
            let png = try render(
                DictionarySourcesContentPreview(sources: f.model.resultSources, labels: try XCTUnwrap(labels))
                    .background(Color(nsColor: .windowBackgroundColor)),
                named: "dictionary-sources-popover-\(locale)-\(scheme == .dark ? "dark" : "light")",
                size: NSSize(width: 380, height: 320), scheme: scheme, inspect: { host in
                    let text = DictionarySourcesHost.views(DictionarySourcesTextView.self, in: host).first
                    XCTAssertNotNil(text)
                    XCTAssertTrue(text?.string.contains(license) == true)
                    XCTAssertTrue(text?.isSelectable == true)
                    XCTAssertFalse(text?.isEditable ?? true)
                    XCTAssertTrue(text?.enclosingScrollView?.hasVerticalScroller == true)
                    XCTAssertGreaterThan(text?.bounds.height ?? 0, text?.enclosingScrollView?.contentSize.height ?? 0)
                    XCTAssertFalse(text?.isAutomaticLinkDetectionEnabled ?? true)
                    if let storage = text?.textStorage {
                        storage.enumerateAttribute(.link, in: NSRange(location: 0, length: storage.length)) {
                            value, _, _ in XCTAssertNil(value)
                        }
                    }
                })
            let words = try sourceWords(png, locale: locale)
            let title = locale == "zh" ? ["\u{6765}\u{6e90}\u{4e0e}\u{8bb8}\u{53ef}"] : ["sources", "licenses"]
            for label in ["syntheticsource", "syntheticlicense"] + title {
                XCTAssertTrue(words.contains(label), words)
            }
        }
    }

    @MainActor
    private func sourceWords(_ png: Data, locale: String) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = locale == "zh" ? ["zh-Hans", "en-US"] : ["en-US"]
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined().lowercased().filter { !$0.isWhitespace }
    }
}
