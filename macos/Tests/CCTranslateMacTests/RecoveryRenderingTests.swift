import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class RecoveryRenderingTests: XCTestCase {
    func testCompletedTimingDetailsAreOptionalLocalizedAndResetForReplacement() async throws {
        for language in ["en", "zh"] {
            var time = 100.0
            let fixture = try ProductTestHarness(latencyClock: { time })
            defer { fixture.cleanUp() }
            let appLanguage = language == "zh" ? "zh_CN" : "en_US"
            let helper = try fixture.ready(configuration: ProductTestHarness.configuration(language: appLanguage))
            fixture.model.interfaceLanguage = language
            fixture.model.input = "First complete source sentence."
            fixture.model.translate()
            let first = try XCTUnwrap(helper.translations.last)
            XCTAssertEqual(first.language, appLanguage)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            time = 101
            helper.event("delta", id: first.id, payload: ["text": .string("## Summary\nA summary.")])
            time = 102
            helper.event("delta", id: first.id, payload: ["text": .string("\n## Translation\nA translation.")])
            time = 103
            helper.event("completed", id: first.id, payload: [
                "text": .string("## Summary\nA summary.\n## Translation\nA translation."),
                "cached": .bool(false), "kind": .string("text"), "history": .string("disabled")
            ])
            let originalOutput = fixture.model.output
            let surface = NativeSettingsTestHost(TranslationElapsedView(model: fixture.model).padding(),
                                                 size: NSSize(width: 360, height: 280))
            defer { surface.close() }
            let label = fixture.model.text("Timing details", "耗时详情")
            _ = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "translation-timing-details", label: label, kind: .button)
            let disclosure = try XCTUnwrap(InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: surface.host)
                .first { $0.identifier?.rawValue == "translation-timing-details" })
            XCTAssertFalse(disclosure.isAccessibilityExpanded())
            try await NativeSettingsTestControls.pressDisclosure(
                in: surface.host, identifier: "translation-timing-details", label: label)
            try await surface.waitFor { disclosure.isAccessibilityExpanded() }
            XCTAssertEqual(fixture.model.output, originalOutput)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertTrue(helper.configurationSaves.isEmpty)

            time = 110
            fixture.model.input = "Replacement source sentence."
            fixture.model.translate()
            let replacement = try XCTUnwrap(helper.translations.last)
            try await surface.waitFor {
                InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: surface.host).isEmpty
            }
            XCTAssertNil(fixture.model.completedTranslationTiming)
            time = 114
            helper.event("completed", id: replacement.id, payload: [
                "text": .string("A new translation."), "cached": .bool(false),
                "kind": .string("text"), "history": .string("disabled")
            ])
            try await surface.waitFor {
                InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: surface.host).contains {
                    $0.identifier?.rawValue == "translation-timing-details" && !$0.isAccessibilityExpanded()
                }
            }
            XCTAssertNil(fixture.model.completedTranslationTiming?.summaryComplete)
            XCTAssertEqual(fixture.model.completedTranslationTiming?.total, 4)
        }
    }

    func testCaptureSettingsRecoveryScrollsToModeControlFromAnotherSettingsPane() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.interfaceLanguage = language
            let navigation = SettingsNavigation(pane: .appearance)
            let surface = NativeSettingsTestHost(
                TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {},
                                        navigation: navigation),
                size: NSSize(width: 760, height: 500))
            defer { surface.close() }
            navigation.openCaptureSettings()
            XCTAssertEqual(try XCTUnwrap(navigation.request).destination, .capture)
            try await surface.waitFor(diagnostics: {
                "pane=\(navigation.pane), pending=\(String(describing: navigation.request))"
            }) {
                // Native controls can appear before the one-shot scroll task resumes after yielding.
                guard navigation.request == nil else { return false }
                return InputLimitNativeViews.views(NSSegmentedControl.self, in: surface.host).contains {
                    guard $0.segmentCount == 2,
                          $0.label(forSegment: 1) == fixture.model.text("Send image", "发送图片") else { return false }
                    let frame = RenderedGeometry.frame($0)
                    let visible = RenderedGeometry.visibleRect($0)
                    return frame.height > 0 && abs(frame.height - visible.height) <= 1
                }
            }
            XCTAssertEqual(navigation.pane, .shortcuts)
            XCTAssertNil(navigation.request)
            XCTAssertEqual(fixture.model.captureTranslationMode, .text)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    func testAutomaticOCRRecoveryOpensCaptureSettingsWithoutSendingOrChangingMode() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness(savedCLI: false)
            defer { fixture.cleanUp() }
            fixture.model.loadPresentation()
            fixture.model.interfaceLanguage = language
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let capture = CaptureModel(screen: ScreenProbe(
                source: source, makeOCRJob: { CaptureTestOCR(text: "") },
                notificationCenter: NotificationCenter()))
            defer { capture.cancel() }
            capture.startTranslation(using: fixture.model, mode: .text)
            try await CaptureProductFixture.waitFor { capture.phase == .selecting }
            capture.select(source.layout[0].frame)
            try await CaptureProductFixture.waitFor { capture.phase == .empty }
            let navigation = SettingsNavigation(pane: .more)
            var recoveryCalls = 0
            let surface = NativeSettingsTestHost(
                CaptureStatusView(capture: capture, model: fixture.model, captureAgain: {},
                                  showCaptureSettings: {
                    recoveryCalls += 1
                    navigation.openCaptureSettings()
                }, close: { capture.cancel() }),
                size: NSSize(width: 420, height: 170))
            defer { surface.close() }
            let button = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "automatic-capture-settings",
                label: fixture.model.text("Capture settings", "截图设置"), kind: .button,
                authoredCaption: true)
            try await button.press()
            XCTAssertEqual(recoveryCalls, 1)
            XCTAssertEqual(navigation.pane, .shortcuts)
            XCTAssertEqual(navigation.request?.destination, .capture)
            XCTAssertEqual(fixture.model.captureTranslationMode, .text)
            XCTAssertFalse(capture.submitted)
            XCTAssertTrue(fixture.helpers.isEmpty)
            XCTAssertEqual(source.requests.count, 1)
        }
    }

    func testMissingCLIBannerUsesDedicatedInstallationRouteAndExpandsExistingPath() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        _ = try fixture.localReady()
        let navigation = SettingsNavigation(pane: .more)
        var genericCalls = 0
        var installationCalls = 0
        let surface = NativeSettingsTestHost(
            TranslatorView(model: fixture.model, showHistory: {}, showSettings: { genericCalls += 1 },
                           showCapture: {}, showInstallationSettings: {
                installationCalls += 1
                navigation.openInstallationSettings()
            }),
            size: NSSize(width: 760, height: 600))
        defer { surface.close() }
        let button = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "missing-cli-installation-settings",
            label: "Installation settings", kind: .button, authoredCaption: true)
        try await button.press()
        XCTAssertEqual(installationCalls, 1)
        XCTAssertEqual(genericCalls, 0)
        XCTAssertEqual(navigation.pane, .translation)
        XCTAssertEqual(navigation.request?.destination, .installation)
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty && $0.configurationSaves.isEmpty })

        fixture.model.selectedCLI = fixture.executable.path
        let settings = NativeSettingsTestHost(
            TranslationSettingsView(model: fixture.model, showDiagnostics: {}, showAbout: {},
                                    navigation: navigation),
            size: NSSize(width: 760, height: 640))
        defer { settings.close() }
        try await settings.waitFor {
            InputLimitNativeViews.views(NativeSettingsDisclosureButton.self, in: settings.host).contains {
                $0.identifier?.rawValue == "provider-installation-details" && $0.isAccessibilityExpanded() &&
                    !$0.visibleRect.isEmpty
            }
        }
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty && $0.configurationSaves.isEmpty })
    }
}
