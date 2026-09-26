import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testAutomaticCaptureProgressAndEmptyRecoveryFitCompactWindowsWithoutReviewEditor() async throws {
        for (language, scheme) in [("en", ColorScheme.light), ("zh", ColorScheme.dark)] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            f.model.loadPresentation()
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .light ? "light" : "dark"
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let ocr = CaptureTestOCR(text: "", blocked: true)
            let capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { ocr },
                                                           notificationCenter: NotificationCenter()))
            defer { ocr.gate?.signal(); capture.cancel() }
            capture.startTranslation(using: f.model, mode: .text)
            try await CaptureProductFixture.waitFor { capture.phase == .selecting }
            capture.select(source.layout[0].frame)
            try await CaptureProductFixture.waitFor { capture.phase == .recognizing && ocr.image != nil }
            let content = CaptureStatusView(capture: capture, model: f.model, captureAgain: {},
                                            showCaptureSettings: {}, close: { capture.cancel() })
            _ = try render(content, named: "automatic-capture-progress-\(language)",
                           size: NSSize(width: 520, height: 190), scheme: scheme, inspect: { host in
                XCTAssertTrue(InputLimitNativeViews.views(NSTextView.self, in: host).allSatisfy { !$0.isEditable })
                let cancel = try NativeSettingsTestControls.resolve(in: host, identifier: "automatic-capture-close",
                    label: f.model.text("Cancel", "取消"), kind: .button, authoredCaption: true)
                XCTAssertEqual(cancel.visibleRect.height, cancel.frame.height, accuracy: 1)
            })
            XCTAssertTrue(f.helpers.isEmpty)
            ocr.gate?.signal()
            try await CaptureProductFixture.waitFor { capture.phase == .empty }
            _ = try render(content, named: "automatic-capture-empty-\(language)",
                           size: NSSize(width: 420, height: 170), scheme: scheme, inspect: { host in
                XCTAssertTrue(InputLimitNativeViews.views(NSTextView.self, in: host).allSatisfy { !$0.isEditable })
                for (identifier, label) in [
                    ("automatic-capture-settings", f.model.text("Capture settings", "截图设置")),
                    ("automatic-capture-retry", f.model.text("Capture again", "重新截图")),
                    ("automatic-capture-close", f.model.text("Close", "关闭"))
                ] {
                    let button = try NativeSettingsTestControls.resolve(in: host, identifier: identifier,
                        label: label, kind: .button, authoredCaption: true)
                    XCTAssertEqual(button.visibleRect.height, button.frame.height, accuracy: 1)
                }
            })
            XCTAssertTrue(f.helpers.isEmpty)
        }
    }

    @MainActor
    func testScreenshotImageModeSettingsRenderInBothLanguages() throws {
        for (language, scheme) in [("en", ColorScheme.light), ("zh", ColorScheme.dark)] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            f.model.loadPresentation()
            f.model.interfaceLanguage = language
            f.model.chooseCaptureTranslationMode(.image)
            _ = try render(Form {
                CaptureShortcutSettingsSection(model: f.model, shortcut: f.model.captureShortcut)
            }.formStyle(.grouped).background(Color(nsColor: .windowBackgroundColor)),
                named: "screenshot-image-mode-\(language)", size: NSSize(width: 760, height: 540),
                scheme: scheme, inspect: { host in
                    let title = f.model.text("Send image", "发送图片")
                    let picker = try XCTUnwrap(InputLimitNativeViews.views(NSSegmentedControl.self, in: host).first {
                        $0.segmentCount == 2 && $0.label(forSegment: 1) == title
                    })
                    XCTAssertEqual(picker.selectedSegment, 1)
                    let frame = RenderedGeometry.frame(picker)
                    let visible = RenderedGeometry.visibleRect(picker)
                    XCTAssertGreaterThan(visible.height, 0)
                    XCTAssertEqual(visible.height, frame.height, accuracy: 1)
                    XCTAssertEqual(visible.width, frame.width, accuracy: 1)
                })
            XCTAssertTrue(f.helpers.isEmpty)
        }
    }
}
