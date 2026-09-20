import XCTest
import AppKit
import SwiftUI
import Vision
@testable import CCTranslateMac

extension ProductRenderingTests {
    @MainActor
    func testAboutSupportEntryRendersAtMinimumWidthInEnglishAndChinese() async throws {
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let about = AboutModel(resources: bundle.resources)
        about.openResources()
        await about.loadTask?.value
        defer { about.close() }
        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            fixture.model.interfaceLanguage = language
            fixture.model.appearance = scheme == .light ? "light" : "dark"
            _ = try render(AboutView(model: about, presentation: fixture.model, close: {}),
                                 named: "about-support-entry-\(language)-\(scheme == .light ? "light" : "dark")",
                                 size: NSSize(width: 660, height: 520), scheme: scheme, inspect: { host in
                let button = try NativeSettingsTestControls.resolve(in: host, identifier: "about-support-author",
                    label: fixture.model.text("Buy the author a coffee", "请作者喝杯咖啡"),
                    kind: .button, authoredCaption: true)
                XCTAssertTrue(button.isEnabled)
                XCTAssertEqual(button.visibleRect.height, button.frame.height, accuracy: 1)
                XCTAssertEqual(button.visibleRect.width, button.frame.width, accuracy: 1)
            }, highResolution: true)
            XCTAssertFalse(about.showingSupport)
            XCTAssertNil(about.supportImage)
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
    }

    @MainActor
    func testSupportSheetRendersBothOriginalScannableQRCodesInEnglishLightAndChineseDark() async throws {
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let about = AboutModel(resources: bundle.resources)
        about.openResources()
        await about.loadTask?.value
        about.showSupport()
        await about.supportTask?.value
        defer { about.close() }
        let original = try supportQRPayloads(AboutBundleFixture.originalSupportImage())
        XCTAssertEqual(original.count, 2, "Both original payment QR codes must remain decodable.")
        for (language, scheme) in [("en", ColorScheme.light), ("zh", .dark)] {
            fixture.model.interfaceLanguage = language
            fixture.model.appearance = scheme == .light ? "light" : "dark"
            let png = try render(AboutView.AboutSupportView(model: about, presentation: fixture.model),
                                 named: "about-support-sheet-\(language)-\(scheme == .light ? "light" : "dark")",
                                 size: NSSize(width: 620, height: 500), scheme: scheme, highResolution: true)
            XCTAssertEqual(try supportQRPayloads(png), original, "Display original destinations, never reconstructed codes.")
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            XCTAssertTrue(words.contains(language == "zh" ? "完成" : "done"), words)
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertTrue(fixture.copiedText.isEmpty)
    }

    @MainActor
    func testSupportSheetMissingResourceRendersRetryWithoutPlaceholderPaymentCode() async throws {
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        try FileManager.default.removeItem(at: bundle.url("Contents/" + AboutBundleResources.supportImagePath))
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        fixture.model.interfaceLanguage = "en"
        fixture.model.appearance = "light"
        let about = AboutModel(resources: bundle.resources)
        about.openResources()
        await about.loadTask?.value
        about.showSupport()
        await about.supportTask?.value
        defer { about.close() }
        let png = try render(AboutView.AboutSupportView(model: about, presentation: fixture.model),
                             named: "about-support-missing-light", size: NSSize(width: 620, height: 500),
                             scheme: .light, highResolution: true)
        let words = try NativeRenderEvidence.settingsWords(png)
        XCTAssertTrue(words.contains("missing"), words)
        XCTAssertTrue(words.contains("retry reading image"), words)
        XCTAssertTrue(try supportQRPayloads(png).isEmpty)
        XCTAssertNil(about.supportImage)
        XCTAssertEqual(about.supportError, .missing)
        XCTAssertTrue(fixture.helpers.isEmpty)
    }

    private func supportQRPayloads(_ png: Data) throws -> Set<String> {
        let request = VNDetectBarcodesRequest()
        request.symbologies = [.qr]
        try VNImageRequestHandler(data: png).perform([request])
        return Set(try XCTUnwrap(request.results).compactMap(\.payloadStringValue))
    }
}
