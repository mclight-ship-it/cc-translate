import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct DefaultsSettingsSurface: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Form {
            Section {
                SettingsDefaultsView(model: model)
            } header: {
                Text(model.text("Restore settings", "恢复设置"))
            }
        }
        .formStyle(.grouped)
        .disabled(model.defaultsPhase.busy)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

final class SettingsDefaultsInteractionTests: XCTestCase {
    @MainActor
    func testNativeBilingualPreviewCancelConfirmAndReadbackWithoutCLIOrHistoryDeletion() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(DefaultsSettingsSurface(model: f.model),
                                                 size: NSSize(width: 760, height: 620))
            defer { surface.close() }
            func button(_ id: String, _ english: String, _ chinese: String,
                        kind: NativeRenderedControlKind = .button) async throws -> NativeSettingsTestControl {
                try await NativeSettingsTestControls.resolveWhenReady(
                    in: surface.host, identifier: id, label: f.model.text(english, chinese), kind: kind)
            }
            for cancel in [true, false] {
                let prepare = try await NativeSettingsTestControls.remainingActionWhenReady(
                    in: surface.host, identifier: "restore-default-settings",
                    label: f.model.text("Restore default settings…", "恢复默认设置…"))
                XCTAssertTrue(prepare.isEnabled)
                try await prepare.press()
                try await surface.waitFor { f.model.defaultsPhase == .loading }
                XCTAssertTrue(helper.configurationSaves.isEmpty)
                let request = try XCTUnwrap(helper.messages.last)
                XCTAssertEqual(request.payload, ["operation": .string("config_load"), "defaults": .bool(true)])
                helper.event("completed", id: request.id,
                             payload: ["config": .object(SettingsDefaultsFixture.canonical())])
                try await surface.waitFor { f.model.defaultsPhase == .confirming }
                if cancel {
                    let cancelButton = try await button("cancel-default-settings", "Cancel", "取消")
                    XCTAssertFalse(cancelButton.hasDestructiveAction)
                    try await cancelButton.press()
                    try await surface.waitFor { f.model.defaultsPhase == .idle }
                    XCTAssertTrue(helper.configurationSaves.isEmpty)
                } else {
                    let confirm = try await button("confirm-default-settings", "Restore defaults", "恢复默认",
                                                   kind: .destructiveButton)
                    XCTAssertTrue(confirm.hasDestructiveAction)
                    try await confirm.press()
                    try await surface.waitFor { f.model.defaultsPhase == .saving }
                    XCTAssertEqual(helper.configurationSaves.count, 1)
                    try SettingsDefaultsFixture.finish(f, helper: helper)
                    try await surface.waitFor { f.model.defaultsPhase == .restored && !f.model.settingsBusy }
                }
            }
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertEqual(helper.messages.count, 2, "Only the two explicit read-only previews are sent.")
            XCTAssertEqual(f.model.permissions, "Not checked.")
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testDefaultsSettingsRenderEnglishPreviewChineseConfirmationAndHonestFailure() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        let surface = DefaultsSettingsSurface(model: f.model)
        _ = try render(surface, named: "settings-defaults-en-light",
                       size: NSSize(width: 760, height: 320), scheme: .light)
        f.model.interfaceLanguage = "zh"
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        XCTAssertTrue(f.model.defaultsConfirmationMessage.contains("100"))
        _ = try render(surface, named: "settings-defaults-confirm-zh-dark",
                       size: NSSize(width: 760, height: 620), scheme: .dark)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        f.model.confirmDefaultsRestore()
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        XCTAssertEqual(f.model.defaultsPhase, .failed("config_io_failed"))
        XCTAssertTrue(f.model.defaultsMessage.contains("config_io_failed"))
        _ = try render(surface, named: "settings-defaults-failed-zh-light",
                       size: NSSize(width: 760, height: 420), scheme: .light)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }
}
