import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testPearlAboutBuildDisclosureKeepsRealMetadataReachable() async throws {
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let about = AboutModel(resources: bundle.resources)
        about.openResources()
        await about.loadTask?.value
        defer { about.close() }
        let focus = NativeTestWindowFocus()
        let host = NSHostingView(rootView: AboutView(model: about, presentation: fixture.model, close: {}))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 1100),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .aqua)
        window.contentView = host
        defer { focus.close(window) }
        window.makeKeyAndOrderFront(nil)
        let expand = try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: "about-build-details",
            label: "Package information & recorded build source", kind: .button)
        try expand.focus(in: window)
        XCTAssertTrue(expand.isFocused)
        try await expand.press()
        let labels = ["info.plist", "source commit", "signing declaration", "build toolchain", "not a live audit"]
        let deadline = Date().addingTimeInterval(3)
        var png = Data()
        var words = ""
        // Wait for the content update after pressing the real native control.
        repeat {
            try await Task.sleep(nanoseconds: 50_000_000)
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            let bitmap = try NativeRenderEvidence.doubleResolutionBitmap(size: host.bounds.size)
            host.effectiveAppearance.performAsCurrentDrawingAppearance {
                host.cacheDisplay(in: host.bounds, to: bitmap)
            }
            png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            words = try NativeRenderEvidence.settingsWords(png)
        } while !labels.allSatisfy({ words.contains($0) }) && Date() < deadline
        try NativeRenderEvidence.retainPNG(png, named: "pearl-about-expanded-build-details")
        for label in labels {
            XCTAssertTrue(words.contains(label), words)
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
    }

    @MainActor
    func testPearlAboutLandingKeepsVersionSupportAndResourceActionsWithoutPromotionalCopy() async throws {
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let about = AboutModel(resources: bundle.resources)
        about.openResources()
        await about.loadTask?.value
        defer { about.close() }

        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .light ? "light" : "dark"
            let png = try render(
                AboutView(model: about, presentation: f.model, close: {}),
                named: "pearl-about-landing-\(language)", size: NSSize(width: 660, height: 520),
                scheme: scheme, inspect: { host in
                    let sponsor = try NativeSettingsTestControls.resolve(
                        in: host, identifier: "about-support-author",
                        label: f.model.text("Buy the author a coffee", "请作者喝杯咖啡"),
                        kind: .button, authoredCaption: true)
                    XCTAssertTrue(sponsor.isEnabled)
                    XCTAssertEqual(sponsor.visibleRect.width, sponsor.frame.width, accuracy: 1)
                    XCTAssertEqual(sponsor.visibleRect.height, sponsor.frame.height, accuracy: 1)
                }, highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            for label in ["9.8.7", "42", f.model.text("Reload resources", "重新读取资源"),
                          f.model.text("Copy app information", "复制应用信息")] {
                XCTAssertTrue(words.contains(label.lowercased().filter { !$0.isWhitespace }), words)
            }
            XCTAssertFalse(words.contains("nativemacosedition"))
            XCTAssertFalse(words.contains("原生macos版本"))
        }
        XCTAssertNil(about.supportImage)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testPearlManualRecoveryKeepsEditableScaledTextAndSeparateNativeSubmissionActions() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        f.model.nativeTextScale = .largest
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(
            source: source, makeOCRJob: { CaptureTestOCR(fails: true) },
            notificationCenter: NotificationCenter()))
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        capture.text = "Retained recovery text"

        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .light ? "light" : "dark"
            let png = try render(
                CaptureView(capture: capture, model: f.model, captureAgain: {}, reselect: {}, close: {}),
                named: "pearl-capture-recovery-\(language)", size: NSSize(width: 620, height: 600),
                scheme: scheme, inspect: { host in
                    let editor = try XCTUnwrap(InputLimitNativeViews.views(NSTextView.self, in: host)
                        .first { $0.string == capture.text })
                    XCTAssertTrue(editor.isEditable)
                    XCTAssertTrue(editor.isSelectable)
                    XCTAssertEqual(editor.font?.pointSize ?? 0,
                                   f.model.nativeTextScale.points(15),
                                   accuracy: 0.1)
                    let submit = try NativeSettingsTestControls.resolve(
                        in: host, identifier: "translate-capture-text",
                        label: f.model.text("Translate text", "翻译文字"), kind: .button, authoredCaption: true)
                    XCTAssertEqual(submit.visibleRect.height, submit.frame.height, accuracy: 1)
                }, highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            XCTAssertTrue(words.contains(f.model.text("Send image for translation", "发送图片翻译")
                .lowercased().filter { !$0.isWhitespace }), words)
            XCTAssertTrue(words.contains(f.model.text("Retry local OCR", "重试本地识别")
                .lowercased().filter { !$0.isWhitespace }), words)
        }
        XCTAssertEqual(capture.text, "Retained recovery text")
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testPearlDictionaryCardPreservesLocalStatusAndExplicitManagementInBothLanguages() throws {
        let downloader = RecordingDictionaryDownloader()
        let f = try ProductTestHarness(savedCLI: false, dictionaryDownloader: downloader)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        f.model.refreshDictionary()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .light ? "light" : "dark"
            let png = try render(
                Form { DictionarySettingsSection(model: f.model, dictionary: f.model.dictionary) }
                    .formStyle(.grouped).pearlSurface(),
                named: "pearl-dictionary-card-\(language)", size: NSSize(width: 620, height: 600),
                scheme: scheme, highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            for label in [f.model.text("Enabled", "已启用"),
                          f.model.text("Refresh status", "刷新状态"),
                          f.model.text("Dictionary information", "词库信息")] {
                XCTAssertTrue(words.contains(label.lowercased().filter { !$0.isWhitespace }), words)
            }
            XCTAssertFalse(words.contains("instantly"))
        }
        XCTAssertTrue(downloader.tickets.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request == .delete || $0.request == .prepareInstall })
    }
}
