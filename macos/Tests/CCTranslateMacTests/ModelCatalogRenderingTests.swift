import XCTest
import AppKit
import SwiftUI
import Vision
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testCatalogSettingsStayExplicitAndRenderAppliedDiscoveredNameAndIDInBothThemes() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let initial = try renderCatalog(f, name: "model-catalog-idle-light", scheme: .light)
        XCTAssertTrue(try catalogWords(initial).contains("refresh models"))
        XCTAssertTrue(client.catalogRequests.isEmpty)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        f.model.refreshModels()
        try f.complete(client)
        f.model.applyModelProfile("fixture/model-b")
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        client.base.event("completed", id: save.id)
        try f.finishSettings(client, config: save.config)
        var images: [Data] = []
        for scheme in [ColorScheme.light, .dark] {
            let png = try renderCatalog(f, name: "model-catalog-loaded-\(scheme == .light ? "light" : "dark")",
                                        scheme: scheme)
            images.append(png)
            let words = try catalogWords(png)
            XCTAssertTrue(words.contains("fixture model 2"))
            XCTAssertTrue(words.contains("fixture/model-b"))
            XCTAssertTrue(words.contains("2 models loaded"))
            XCTAssertTrue(words.contains("saved and read back"))
        }
        XCTAssertNotEqual(images[0], images[1])
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertEqual(client.base.configurationSaves.count, 1)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testCatalogSettingsRenderConnectingLoadingCancellingAndCancelledStates() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready(native: false)
        f.cliAvailable = true
        f.model.refreshModels()
        let connecting = try renderCatalog(f, name: "model-catalog-connecting-dark", scheme: .dark)
        XCTAssertTrue(try catalogWords(connecting).contains("preparing codex"))
        old.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        try f.ready(current)
        let loading = try renderCatalog(f, name: "model-catalog-loading-zh-light", scheme: .light, chinese: true)
        let chinese = try catalogWords(loading, chinese: true).filter { !$0.isWhitespace }
        XCTAssertTrue(chinese.contains("正在加载模型"))
        XCTAssertTrue(chinese.contains("取消刷新"))
        f.model.cancelModelCatalog()
        let cancelling = try renderCatalog(f, name: "model-catalog-cancelling-dark", scheme: .dark)
        XCTAssertTrue(try catalogWords(cancelling).contains("stopping refresh"))
        current.base.event("cancelled", id: try XCTUnwrap(current.catalogRequests.last))
        let cancelled = try renderCatalog(f, name: "model-catalog-cancelled-light", scheme: .light)
        XCTAssertTrue(try catalogWords(cancelled).contains("refresh cancelled"))
        XCTAssertEqual(current.catalogRequests.count, 1)
        XCTAssertTrue(f.clients.allSatisfy { $0.base.translations.isEmpty && $0.base.configurationSaves.isEmpty })
    }

    @MainActor
    func testCatalogFailureAndEmptyListRenderWithManualIDAndExplicitRetryStillAvailable() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.editCustomModelID("fixture/Manual-ID")
        f.model.refreshModels()
        client.base.event("failed", id: try XCTUnwrap(client.catalogRequests.last),
                          payload: ["code": .string("model_catalog_failed")])
        let failure = try renderCatalog(f, name: "model-catalog-failure-light", scheme: .light)
        let words = try catalogWords(failure)
        XCTAssertTrue(words.contains("retry model refresh"))
        XCTAssertTrue(words.contains("fixture/manual-id"))
        XCTAssertTrue(words.contains("apply model"))
        XCTAssertTrue(f.model.canApplyModelSetting)
        f.model.refreshModels()
        try f.complete(client, payload: CatalogAppFixture.payload([]))
        let empty = try renderCatalog(f, name: "model-catalog-empty-zh-dark", scheme: .dark, chinese: true)
        XCTAssertTrue(try catalogWords(empty, chinese: true).filter { !$0.isWhitespace }.contains("未返回模型"))
        XCTAssertEqual(f.model.modelSettings.draft, "fixture/Manual-ID")
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    func testCatalogControlsDoNotTakeCommandReturnOrNativeModelEditorComposition() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.editCustomModelID("fixture/Editor-ID")
        _ = try renderCatalog(f, name: "model-catalog-ime-light", scheme: .light, inspect: { host in
            guard let field = self.catalogTextFields(host).first(where: { $0.isEditable }),
                  let window = host.window else {
                XCTFail("The production Settings must retain its native model ID field.")
                return
            }
            XCTAssertTrue(window.makeFirstResponder(field))
            guard let editor = field.currentEditor() as? NSTextView else {
                XCTFail("The native field editor is required for composition.")
                return
            }
            editor.setMarkedText("拼", selectedRange: NSRange(location: 1, length: 0),
                                 replacementRange: NSRange(location: NSNotFound, length: 0))
            XCTAssertTrue(editor.hasMarkedText())
            guard let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command,
                                               timestamp: 0, windowNumber: window.windowNumber, context: nil,
                                               characters: "\r", charactersIgnoringModifiers: "\r",
                                               isARepeat: false, keyCode: 36) else {
                XCTFail("Could not construct the window-local, never-posted event.")
                return
            }
            XCTAssertFalse(host.performKeyEquivalent(with: event))
            XCTAssertTrue(editor.hasMarkedText())
            XCTAssertTrue(client.catalogRequests.isEmpty)
            XCTAssertTrue(client.base.configurationSaves.isEmpty)
            XCTAssertTrue(client.base.translations.isEmpty)
            editor.unmarkText()
        })
    }

    @MainActor
    func testDiscoveredChoiceAppearsInBothRealTranslatorAndCapturePickers() async throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        try f.complete(client)
        f.model.applyModelProfile("fixture/model-a")
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        client.base.event("completed", id: save.id)
        try f.finishSettings(client, config: save.config)
        f.model.interfaceLanguage = "en"
        f.model.appearance = "light"
        f.model.reuseHistory(.init(id: "fixture", input: "Original text", output: "Preserved result"))
        for (name, size) in [
            ("model-catalog-main-light", NSSize(width: 1120, height: 760)),
            ("model-catalog-main-narrow-light", NSSize(width: 660, height: 540))
        ] {
            let main = try render(TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
                                  named: name, size: size, scheme: .light)
            let words = try catalogWords(main)
            XCTAssertTrue(words.contains("fixture model 1"), words)
            XCTAssertTrue(words.contains("fixture/model-a"), words)
            XCTAssertTrue(words.contains("translate to"), words)
        }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let screen = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                 notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        f.model.appearance = "dark"
        let preview = try render(
            CaptureView(capture: capture, model: f.model, captureAgain: {}, reselect: {}, close: {}),
            named: "model-catalog-capture-dark", size: NSSize(width: 860, height: 720), scheme: .dark)
        let previewWords = try catalogWords(preview)
        XCTAssertTrue(previewWords.contains("fixture model 1"), previewWords)
        XCTAssertTrue(previewWords.contains("fixture/model-a"), previewWords)
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    private func renderCatalog(_ f: CatalogAppFixture, name: String, scheme: ColorScheme,
                               chinese: Bool = false, inspect: ((NSView) -> Void)? = nil) throws -> Data {
        f.model.loadPresentation()
        f.model.interfaceLanguage = chinese ? "zh" : "en"
        f.model.appearance = scheme == .dark ? "dark" : "light"
        return try render(TranslationSettingsView(model: f.model, showDiagnostics: {}, showAbout: {}),
                          named: name, size: NSSize(width: 820, height: 3200), scheme: scheme, inspect: inspect)
    }

    @MainActor
    private func catalogTextFields(_ view: NSView) -> [NSTextField] {
        (view as? NSTextField).map { [$0] } ?? view.subviews.flatMap { catalogTextFields($0) }
    }

    @MainActor
    private func catalogWords(_ png: Data, chinese: Bool = false) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        var words: [String] = []
        let height = min(image.height, 2400)
        for y in stride(from: 0, to: height, by: 1000) {
            let tile = try XCTUnwrap(image.cropping(to: CGRect(
                x: 0, y: CGFloat(y), width: CGFloat(image.width), height: CGFloat(min(1100, height - y)))))
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = false
            request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
            try VNImageRequestHandler(cgImage: tile).perform([request])
            words += (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        }
        return words.joined(separator: " ").lowercased()
    }
}
