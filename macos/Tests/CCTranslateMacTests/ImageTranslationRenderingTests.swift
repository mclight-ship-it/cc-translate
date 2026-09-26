import XCTest
import AppKit
import SwiftUI
import Vision
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testImageCreationRollbackFailureRendersRecoveryBeforeAnyHelperStarts() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let (capture, _, _) = try await f.capture()
        defer { capture.cancel() }
        let owner = ImageTestAttachment()
        f.factory.rollbackFailure = owner
        f.model.loadPresentation()
        f.model.interfaceLanguage = "zh"
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        let png = try renderImageCapture(f, capture: capture, name: "image-create-cleanup-zh-dark",
                                         scheme: .dark, chinese: true, narrow: true, highResolution: true)
        let words = try imageWords(png, chinese: true).filter { !$0.isWhitespace }
        XCTAssertTrue(words.contains("重试清理图片"), words)
        XCTAssertTrue(words.contains("未发送任何内容"), words)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(owner.cleanupCalls, 0)
        f.resources.retryCleanup()
        try await CaptureProductFixture.waitFor { owner.removed }
    }

    @MainActor
    func testImageCaptureDisclosureAndSeparateActionsRenderWithEmptyOCRInBothLanguagesAndThemes() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let (capture, _, source) = try await f.capture()
        defer { capture.cancel() }
        for (name, scheme, chinese, narrow) in [
            ("image-preview-light", ColorScheme.light, false, false),
            ("image-preview-narrow-dark", .dark, false, true),
            ("image-preview-zh-dark", .dark, true, false),
            ("image-preview-zh-narrow-light", .light, true, true)
        ] {
            let png = try renderImageCapture(f, capture: capture, name: name, scheme: scheme,
                                             chinese: chinese, narrow: narrow)
            let words = try imageWords(png, chinese: chinese)
            if chinese {
                let compact = words.filter { !$0.isWhitespace }
                XCTAssertTrue(compact.contains("发送图片翻译"), words)
                XCTAssertTrue(compact.contains("翻译文字"), words)
                XCTAssertTrue(compact.contains("在本机完成"), words)
                XCTAssertTrue(compact.contains("你可以选择向codex发送文字或这张图片"), words)
                XCTAssertTrue(compact.contains("历史记录只保存译文"), words)
            } else {
                XCTAssertTrue(words.contains("send image for translation"), words)
                XCTAssertTrue(words.contains("translate text"), words)
                XCTAssertTrue(words.contains("text recognition stays on this mac"), words)
                XCTAssertTrue(words.contains("choose whether to send the text or this image to codex"), words)
                XCTAssertTrue(words.contains("temporary images are removed after use"), words)
            }
        }
        XCTAssertTrue(capture.canTranslateImage)
        XCTAssertFalse(capture.canTranslate)
        XCTAssertTrue(f.factory.images.isEmpty)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(source.permissionCalls, 1, "Only the synthetic explicit capture requests permission.")
    }

    @MainActor
    func testImageCapturePreparingStreamingAndCancellationRenderWithoutAutomaticResend() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture()
        defer { capture.cancel() }
        f.factory.hold = true
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        let preparing = try renderImageCapture(f, capture: capture, name: "image-preparing-light", scheme: .light)
        XCTAssertTrue(try imageWords(preparing).contains("preparing the selected image"))
        f.factory.finish()
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        let request = try XCTUnwrap(client.imageRequests.last)
        client.base.event("delta", id: request.id, payload: [
            "text": .string("A translated heading\n\nThis is a synthetic streamed image result."),
            "submitted": .bool(true)
        ])
        try await CaptureProductFixture.waitFor { !f.model.output.isEmpty }
        f.model.appearance = "dark"
        let streaming = try render(TranslationResultView(model: f.model, compact: true),
                                   named: "image-result-streaming-dark", size: NSSize(width: 540, height: 560),
                                   scheme: .dark)
        XCTAssertTrue(try imageWords(streaming).contains("synthetic streamed image result"))
        f.model.interfaceLanguage = "zh"
        capture.cancelCurrentAction()
        let cancelling = try renderImageCapture(f, capture: capture, name: "image-cancelling-zh-dark",
                                                scheme: .dark, chinese: true)
        XCTAssertTrue(try imageWords(cancelling, chinese: true).filter { !$0.isWhitespace }.contains("正在取消图片翻译"))
        client.base.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { !f.resources.working }
        let cancelled = try renderImageCapture(f, capture: capture, name: "image-cancelled-zh-light",
                                               scheme: .light, chinese: true)
        XCTAssertTrue(try imageWords(cancelled, chinese: true).filter { !$0.isWhitespace }.contains("图片可能已经发送"))
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertTrue(capture.canTranslateImage)
    }

    @MainActor
    func testImageFailurePartialOutputAndRecoverableCleanupRenderOnActualCaptureAndResultViews() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture(fails: true)
        defer { capture.cancel() }
        let failedOCR = try renderImageCapture(f, capture: capture, name: "image-local-ocr-failed-light", scheme: .light)
        XCTAssertTrue(try imageWords(failedOCR).contains("send image for translation"))
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        let request = try XCTUnwrap(client.imageRequests.last)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        attachment.failsCleanup = true
        client.base.event("delta", id: request.id, payload: [
            "text": .string("Partial image translation remains selectable."), "submitted": .bool(true)
        ])
        client.base.event("failed", id: request.id, payload: [
            "code": .string("provider_failed"), "submitted": .bool(true)
        ])
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        let failure = try renderImageCapture(f, capture: capture, name: "image-failure-cleanup-dark",
                                             scheme: .dark, narrow: true)
        let failureWords = try imageWords(failure)
        XCTAssertTrue(failureWords.contains("image translation failed"), failureWords)
        XCTAssertTrue(failureWords.contains("retry image cleanup"), failureWords)
        f.model.appearance = "light"
        let result = try render(TranslationResultView(model: f.model, compact: true),
                                named: "image-result-partial-light", size: NSSize(width: 560, height: 620),
                                scheme: .light, inspect: { host in
            let text = self.imageTextViews(host).first { $0.string.contains("Partial image translation") }
            XCTAssertEqual(text?.isEditable, false)
            XCTAssertEqual(text?.isSelectable, true)
        })
        XCTAssertTrue(try imageWords(result).contains("partial image translation remains selectable"))
        attachment.failsCleanup = false
        attachment.holdCleanup = true
        f.resources.retryCleanup()
        try await CaptureProductFixture.waitFor { attachment.cleanupContinuation != nil }
        let cleaning = try renderImageCapture(f, capture: capture, name: "image-cleaning-zh-light",
                                              scheme: .light, chinese: true)
        XCTAssertTrue(try imageWords(cleaning, chinese: true).filter { !$0.isWhitespace }.contains("正在删除临时图片"))
        attachment.finishCleanup()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertEqual(f.model.output, "Partial image translation remains selectable.")
    }

    @MainActor
    func testImageHistoryDetailRendersLiteralOutputAndNoFabricatedOriginalInBothLanguages() throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let row = ProbeModel.HistoryRow(id: "image-output-only", input: "",
                                       output: "Only translated text is retained in this synthetic history record.",
                                       kind: "ocr", hasOriginalInput: false)
        for (name, chinese, scheme) in [
            ("image-history-light", false, ColorScheme.light),
            ("image-history-zh-dark", true, .dark)
        ] {
            f.model.interfaceLanguage = chinese ? "zh" : "en"
            let png = try render(
                HistoryTranslationDetail(model: f.model, row: row, useEntry: {})
                    .background(Color(nsColor: .windowBackgroundColor)),
                named: name, size: NSSize(width: 660, height: 520), scheme: scheme, inspect: { host in
                    let rendered = self.imageTextViews(host).first { $0.string == row.output }
                    XCTAssertNotNil(rendered)
                    XCTAssertEqual(rendered?.isEditable, false)
                    XCTAssertEqual(rendered?.isSelectable, true)
                })
            let bitmap = try XCTUnwrap(NSBitmapImageRep(data: png))
            let background = try XCTUnwrap(bitmap.colorAt(x: 0, y: 0))
            XCTAssertEqual(background.alphaComponent, 1, accuracy: 0.001,
                           "Cache the history window's semantic background, not a transparent child in isolation.")
            let words = try imageWords(png, chinese: chinese)
            XCTAssertTrue(words.contains("only translated text"), words)
            if chinese {
                XCTAssertTrue(words.filter { !$0.isWhitespace }.contains("打开结果"), words)
            } else {
                XCTAssertTrue(words.contains("open result"), words)
                XCTAssertTrue(words.contains("not stored"), words)
            }
        }
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertTrue(f.factory.images.isEmpty)
    }

    @MainActor
    func testImageActionDoesNotTakeCommandReturnOrCommitNativeCaptureComposition() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture(text: "Editable local OCR text")
        defer { capture.cancel() }
        _ = try renderImageCapture(f, capture: capture, name: "image-capture-ime-light", scheme: .light,
                                   inspect: { host in
            guard let editor = self.imageTextViews(host).first(where: \.isEditable),
                  let window = host.window else {
                XCTFail("The real capture TextEditor must remain available.")
                return
            }
            XCTAssertTrue(window.makeFirstResponder(editor))
            editor.setMarkedText("拼", selectedRange: NSRange(location: 1, length: 0),
                                 replacementRange: NSRange(location: NSNotFound, length: 0))
            XCTAssertTrue(editor.hasMarkedText())
            guard let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command,
                                               timestamp: 0, windowNumber: window.windowNumber, context: nil,
                                               characters: "\r", charactersIgnoringModifiers: "\r",
                                               isARepeat: false, keyCode: 36) else {
                XCTFail("Could not construct a local, never-posted key event.")
                return
            }
            XCTAssertFalse(host.performKeyEquivalent(with: event))
            XCTAssertTrue(editor.hasMarkedText())
            XCTAssertTrue(client.imageRequests.isEmpty)
            XCTAssertTrue(client.base.translations.isEmpty)
            XCTAssertTrue(f.factory.images.isEmpty)
            editor.unmarkText()
        })
    }

    @MainActor
    private func renderImageCapture(_ f: ImageAppFixture, capture: CaptureModel, name: String,
                                    scheme: ColorScheme, chinese: Bool = false, narrow: Bool = false,
                                    highResolution: Bool = false,
                                    inspect: ((NSView) -> Void)? = nil) throws -> Data {
        f.model.interfaceLanguage = chinese ? "zh" : "en"
        f.model.appearance = scheme == .dark ? "dark" : "light"
        return try render(CaptureView(capture: capture, model: f.model,
                                      captureAgain: { capture.start() }, reselect: { capture.reselect() },
                                      close: { capture.cancel() }),
                          named: name, size: NSSize(width: narrow ? 620 : 860, height: narrow ? 600 : 780),
                          scheme: scheme, inspect: inspect, highResolution: highResolution)
    }

    @MainActor
    private func imageTextViews(_ view: NSView) -> [NSTextView] {
        (view as? NSTextView).map { [$0] } ?? view.subviews.flatMap { imageTextViews($0) }
    }

    @MainActor
    private func imageWords(_ png: Data, chinese: Bool = false) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.minimumTextHeight = 0
        request.usesLanguageCorrection = false
        request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
        try VNImageRequestHandler(cgImage: NativeRenderEvidence.recognitionImage(image)).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
            .joined(separator: " ").lowercased()
    }
}
