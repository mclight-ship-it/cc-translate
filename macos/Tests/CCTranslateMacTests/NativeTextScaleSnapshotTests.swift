import AppKit
import SwiftUI
import Vision
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func test150PercentTranslatorRendersEnglishLightAtMinimumSizeWithoutClippingTextOrActions() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.nativeTextScale = .largest
        f.model.reuseHistory(.init(id: "scale-main", input: "Original first line\nEnglish editor text.\nORIGINAL END",
                                  output: "## Larger translation\nReadable translated text.\nRESULT END"))
        let png = try render(
            TranslatorView(model: f.model, showHistory: {}, showSettings: {}, showCapture: {}),
            named: "native-text-scale-translator-en-light-150", size: NSSize(width: 660, height: 540), scheme: .light,
            inspect: { host in
                let views = ScaleTestSupport.views(NSTextView.self, in: host)
                XCTAssertEqual(views.count, 2)
                for view in views { self.assertScaleTextIsNotClipped(view) }
                XCTAssertEqual(try ScaleTestSupport.font(
                    XCTUnwrap(views.first { $0.isEditable }), at: "ORIGINAL END").pointSize, 22.5)
                XCTAssertEqual(try ScaleTestSupport.font(
                    XCTUnwrap(views.first { !$0.isEditable }), at: "RESULT END").pointSize, 22.5)
            })
        let words = try scaleWords(png)
        for expected in ["originalend", "resultend", "translate"] { XCTAssertTrue(words.contains(expected), words) }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func test150PercentHistoryRendersChineseDarkWithEntireOriginalTranslationAndActions() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        f.model.nativeTextScale = .largest
        f.model.interfaceLanguage = "zh"
        let row = ProbeModel.HistoryRow(id: "scale-history", input: "保留原文。\n原文末行",
                                       output: "# 大号翻译\n中英文正文可以完整阅读。\n译文末行")
        let png = try render(
            HistoryTranslationDetail(model: f.model, row: row, useEntry: {})
                .background(Color(nsColor: .windowBackgroundColor)),
            named: "native-text-scale-history-zh-dark-150", size: NSSize(width: 600, height: 540), scheme: .dark,
            inspect: { host in
                let views = ScaleTestSupport.views(NSTextView.self, in: host)
                XCTAssertEqual(views.count, 2)
                for view in views { self.assertScaleTextIsNotClipped(view) }
            })
        let words = try scaleWords(png, chinese: true)
        for expected in ["原文末行", "译文末行", "复制结果"] { XCTAssertTrue(words.contains(expected), words) }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(row.input, "保留原文。\n原文末行")
    }

    @MainActor
    func test150PercentCaptureRendersChineseLightWithReadableReviewedTextAndSeparateActions() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        f.model.interfaceLanguage = "zh"
        f.model.nativeTextScale = .largest
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let text = "识别文字可继续编辑。\nReviewed local text.\n识别末行"
        let job = CaptureTestOCR(text: text)
        let capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { job },
                                                       notificationCenter: NotificationCenter()))
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let png = try render(
            CaptureView(capture: capture, model: f.model, captureAgain: {}, reselect: {}, close: {}),
            named: "native-text-scale-capture-zh-light-150", size: NSSize(width: 760, height: 680), scheme: .light,
            inspect: { host in
                let editors = ScaleTestSupport.views(NSTextView.self, in: host).filter(\.isEditable)
                XCTAssertEqual(editors.count, 1)
                for editor in editors {
                    XCTAssertTrue(ScaleTestSupport.hasFont(editor, size: 22.5))
                    self.assertScaleTextIsNotClipped(editor)
                }
            })
        let words = try scaleWords(png, chinese: true)
        for expected in ["识别末行", "翻译文字", "发送图片翻译"] { XCTAssertTrue(words.contains(expected), words) }
        XCTAssertEqual(capture.text, text)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    private func assertScaleTextIsNotClipped(_ view: NSTextView, file: StaticString = #filePath, line: UInt = #line) {
        guard let window = view.window else {
            XCTFail("Content needs native window backing.", file: file, line: line)
            return
        }
        let visible = view.visibleRect.insetBy(dx: -1, dy: -1)
        XCTAssertGreaterThan(visible.height, 22.5, file: file, line: line)
        let text = view.string as NSString
        for index in 0..<text.length {
            let character = text.substring(with: NSRange(location: index, length: 1))
            if character.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            // This geometry API supports both TextKit versions without changing the editor's layout engine.
            let screenRect = view.firstRect(forCharacterRange: NSRange(location: index, length: 1), actualRange: nil)
            let rect = view.convert(window.convertFromScreen(screenRect), from: nil)
            XCTAssertGreaterThan(rect.height, 0, file: file, line: line)
            XCTAssertTrue(visible.contains(rect), "Clipped content at \(index): \(rect), visible \(visible)",
                          file: file, line: line)
        }
    }

    @MainActor
    private func scaleWords(_ png: Data, chinese: Bool = false) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined().lowercased().filter { !$0.isWhitespace }
    }
}
