import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

extension ProductRenderingTests {
    @MainActor
    func testProductionShortcutsUseTypedPermissionsInsteadOfDiagnosticText() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let snapshot = PermissionSnapshot(accessibility: .granted, inputMonitoring: .notGranted,
                                          screenCapture: .notGranted, secureInput: false)
        let model = ProbeModel(preferences: fixture.preferences, readPermissions: { snapshot })
        defer { model.prepareToQuit() }
        model.refreshPermissions()
        XCTAssertTrue(model.permissions.contains("Accessibility: granted"),
                      "The diagnostics still retain the raw state; settings must not render it.")
        for language in ["en", "zh"] {
            model.interfaceLanguage = language
            let png = try render(
                TranslationSettingsView(model: model, showDiagnostics: {}, showAbout: {}, pane: .shortcuts),
                named: "settings-typed-permissions-\(language)", size: NSSize(width: 760, height: 1000),
                scheme: language == "zh" ? .dark : .light, highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
                .filter { !$0.isWhitespace }
            XCTAssertTrue(words.contains(language == "zh" ? "已允许" : "allowed"), words)
            XCTAssertTrue(words.contains(language == "zh" ? "未允许" : "notallowed"), words)
            XCTAssertFalse(words.contains("accessibility:granted"), words)
            XCTAssertFalse(words.contains("notgranted/notyetrequested"), words)
            if language == "zh" {
                for token in ["accessibility", "inputmonitoring", "granted", "secureinput"] {
                    XCTAssertFalse(words.contains(token), words)
                }
            }
        }
    }

    @MainActor
    func testPermissionSettingsRenderShortBilingualAllowedNotAllowedAndUncheckedStates() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let states: [(PermissionState?, String, String, String)] = [
            (nil, "unchecked", "Not checked", "未检查"),
            (.granted, "allowed", "Allowed", "已允许"),
            (.notGranted, "not-allowed", "Not allowed", "未允许")
        ]
        for language in ["en", "zh"] {
            fixture.model.interfaceLanguage = language
            let chinese = language == "zh"
            for (state, name, english, translated) in states {
                let png = try render(
                    ShortcutPermissionsSettingsView(
                        model: fixture.model, accessibility: state, inputMonitoring: state,
                        monitorState: state == nil ? .off : (state == .granted ? .active : .requiresPermissions),
                        allowAccessibility: { XCTFail("Rendering must not request access.") },
                        allowInputMonitoring: { XCTFail("Rendering must not request access.") },
                        refresh: { XCTFail("Rendering must not inspect system permissions.") })
                    .padding(20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .pearlSurface(),
                    named: "settings-permissions-\(name)-\(language)",
                    size: NSSize(width: 530, height: 280), scheme: chinese ? .dark : .light, highResolution: true)
                try assertPermissionSurfaceIsOpaque(png)
                let words = try NativeRenderEvidence.settingsWords(png, chinese: chinese).filter { !$0.isWhitespace }
                XCTAssertTrue(words.contains((chinese ? translated : english).lowercased().filter { !$0.isWhitespace }), words)
                XCTAssertTrue(words.contains(chinese ? "检查权限" : "checkpermissions"), words)
                for diagnostic in ["accessibilitygranted", "inputmonitoringgranted", "notgranted/notyetrequested",
                                   "screencapture", "secureinput:", "passivedouble", "辅助功能和输入监控权限已丢失"] {
                    XCTAssertFalse(words.contains(diagnostic), diagnostic + ": " + words)
                }
                if chinese {
                    for englishToken in ["accessibility", "inputmonitoring", "granted", "notchecked", "shortcut"] {
                        XCTAssertFalse(words.contains(englishToken), englishToken + ": " + words)
                    }
                }
                if state == .granted {
                    XCTAssertTrue(words.contains(chinese ? "快捷键已开启" : "shortcuton"), words)
                    XCTAssertFalse(words.contains(chinese ? "未允许" : "notallowed"), words)
                } else if state == .notGranted {
                    XCTAssertTrue(words.contains(chinese ? "等待授权" : "waitingforpermissions"), words)
                    XCTAssertFalse(words.contains(chinese ? "已拒绝" : "denied"),
                                   "macOS cannot distinguish an unrequested permission from a denial.")
                }
            }
        }
        XCTAssertNil(fixture.model.permissionSnapshot)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testGrantedPermissionsDoNotClaimShortcutIsRunningAndSecureInputIsLocalized() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        for language in ["en", "zh"] {
            fixture.model.interfaceLanguage = language
            let chinese = language == "zh"
            let states: [(SelectionMonitorState, String, String, String)] = [
                (.off, "off", "Shortcut off", "快捷键已关闭"),
                (.temporarilyUnavailable, "not-running", "Temporarily unavailable", "暂时不可用"),
                (.secureInput, "secure-input", "Paused during secure input", "安全输入期间已暂停"),
                (.diagnostic, "diagnostic", "Diagnostic mode", "诊断模式")
            ]
            for (state, name, english, translated) in states {
                let png = try render(
                    ShortcutPermissionsSettingsView(
                        model: fixture.model, accessibility: .granted, inputMonitoring: .granted,
                        monitorState: state,
                        allowAccessibility: {}, allowInputMonitoring: {}, refresh: {})
                    .padding(20)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .pearlSurface(),
                    named: "settings-permissions-\(name)-\(language)",
                    size: NSSize(width: 530, height: 240), scheme: chinese ? .dark : .light, highResolution: true)
                try assertPermissionSurfaceIsOpaque(png)
                let words = try NativeRenderEvidence.settingsWords(png, chinese: chinese).filter { !$0.isWhitespace }
                XCTAssertTrue(words.contains((chinese ? translated : english).lowercased().filter { !$0.isWhitespace }), words)
                XCTAssertFalse(words.contains(chinese ? "快捷键已开启" : "shortcuton"), words)
                if chinese { XCTAssertFalse(words.contains("secureinput"), words) }
            }
        }
    }

    @MainActor
    private func assertPermissionSurfaceIsOpaque(_ png: Data) throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(data: png))
        let background = try XCTUnwrap(bitmap.colorAt(x: 0, y: 0))
        XCTAssertGreaterThan(background.alphaComponent, 0.99,
                             "Permission screenshots must render the product surface, not transparent text.")
    }
}

final class ShortcutPermissionsSettingsInteractionTests: XCTestCase {
    @MainActor
    func testNativePermissionActionsAreExplicitAndEachRequestsOnlyItsOwnPermission() async throws {
        for language in ["en", "zh"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            _ = try fixture.ready()
            fixture.model.interfaceLanguage = language
            var accessibilityRequests = 0
            var monitoringRequests = 0
            var refreshes = 0
            let surface = NativeSettingsTestHost(
                ShortcutPermissionsSettingsView(
                    model: fixture.model, accessibility: .notGranted, inputMonitoring: .notGranted,
                    monitorState: .requiresPermissions,
                    allowAccessibility: { accessibilityRequests += 1 },
                    allowInputMonitoring: { monitoringRequests += 1 },
                    refresh: { refreshes += 1 })
                .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .pearlSurface(),
                size: NSSize(width: 530, height: 280))
            defer { surface.close() }
            XCTAssertEqual(accessibilityRequests + monitoringRequests + refreshes, 0)
            let accessibility = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "selection-accessibility-allow",
                label: fixture.model.text("Allow Accessibility…", "允许辅助功能…"), kind: .button)
            try await accessibility.press()
            try await surface.waitFor { accessibilityRequests == 1 }
            XCTAssertEqual(monitoringRequests + refreshes, 0)
            let monitoring = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "selection-input-monitoring-allow",
                label: fixture.model.text("Allow Input Monitoring…", "允许输入监控…"), kind: .button)
            try await monitoring.press()
            try await surface.waitFor { monitoringRequests == 1 }
            XCTAssertEqual(accessibilityRequests, 1)
            let refresh = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "check-selection-permissions",
                label: fixture.model.text("Check permissions", "检查权限"), kind: .button)
            try await refresh.press()
            try await surface.waitFor { refreshes == 1 }
            XCTAssertEqual(accessibilityRequests + monitoringRequests, 2)
            XCTAssertNil(fixture.model.permissionSnapshot)
        }
    }
}
