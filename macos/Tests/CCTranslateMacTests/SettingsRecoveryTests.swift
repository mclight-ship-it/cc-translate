import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class SettingsRecoveryTests: XCTestCase {
    func testSettingsLoadFailuresAreLocalizedActionableAndNeverReplayTranslation() throws {
        for language in ["en", "zh"] {
            for code in ["config_in_use", "config_unavailable", "invalid_config", "config_io_failed", "helper_failed"] {
                let fixture = try ProductTestHarness()
                defer { fixture.cleanUp() }
                fixture.model.loadPresentation()
                fixture.model.interfaceLanguage = language
                fixture.model.input = "A queued translation."
                fixture.model.translate()
                let helper = try XCTUnwrap(fixture.helpers.first)
                helper.event("ready")
                helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                             payload: ["code": .string(code)])

                XCTAssertEqual(fixture.model.productPhase, .failed)
                XCTAssertEqual(fixture.model.productMessage, fixture.model.settingsFailureMessage(code: code))
                XCTAssertFalse(fixture.model.productMessage.contains(code))
                XCTAssertTrue(fixture.model.status.contains(code), "The exact code remains available in diagnostics.")
                XCTAssertTrue(fixture.model.productMessage.contains(language == "zh" ? "请重新加载设置" : "Reload settings"))
                XCTAssertFalse(fixture.model.productMessage.contains("Settings operation failed:"))
                XCTAssertFalse(fixture.model.settingsBusy)
                XCTAssertFalse(fixture.model.preparing)
                XCTAssertNil(fixture.model.translationElapsedSeconds)
                XCTAssertTrue(helper.translations.isEmpty)
                XCTAssertTrue(helper.configurationSaves.isEmpty)
                XCTAssertEqual(helper.configurationLoads.count, 1)

                fixture.model.loadSettings()
                try fixture.finishConfiguration(on: helper)
                XCTAssertTrue(helper.translations.isEmpty, "Explicitly reloading settings must not replay a dropped request.")
            }
        }
    }

    func testSettingsSaveFailurePreservesLocalizedMessageWithoutRepeatingWrite() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.interfaceLanguage = "zh"
        fixture.model.direction = "to_en"
        fixture.model.input = "A queued translation."
        fixture.model.translate()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("failed", id: save.id, payload: ["code": .string("config_io_failed")])

        XCTAssertEqual(fixture.model.productMessage, fixture.model.settingsFailureMessage(code: "config_io_failed"))
        XCTAssertTrue(fixture.model.productMessage.contains("磁盘空间"))
        XCTAssertFalse(fixture.model.productMessage.contains("config_io_failed"))
        XCTAssertTrue(fixture.model.status.contains("config_io_failed"))
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        fixture.model.loadSettings()
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    func testRecoveryRoutesReplaceStalePaneAndCanBeRequestedAgain() throws {
        let navigation = SettingsNavigation(pane: .more)
        navigation.openInstallationSettings()
        XCTAssertEqual(navigation.pane, .translation)
        XCTAssertEqual(navigation.request?.destination, .installation)
        let first = try XCTUnwrap(navigation.request)
        navigation.openInstallationSettings()
        XCTAssertNotEqual(navigation.request?.id, first.id, "Repeated recovery must scroll even on a retained settings page.")
        navigation.finish(first)
        XCTAssertNotNil(navigation.request, "An older scroll must not consume a newer route.")
        navigation.pane = .appearance
        navigation.openCaptureSettings()
        XCTAssertEqual(navigation.pane, .shortcuts)
        XCTAssertEqual(navigation.request?.destination, .capture)
        navigation.finish(try XCTUnwrap(navigation.request))
        XCTAssertNil(navigation.request, "Normal settings visits must not repeat a previous recovery jump.")
    }

    func testMissingCLIMessageDistinguishesSelectedServiceFromExecutable() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        fixture.model.loadPresentation()
        for language in ["en", "zh"] {
            fixture.model.interfaceLanguage = language
            XCTAssertTrue(fixture.model.missingCLIMessage.contains("Codex"))
            XCTAssertTrue(fixture.model.missingCLIMessage.contains(language == "zh" ? "可执行文件缺失或尚未配置" : "executable is missing or not configured"))
            XCTAssertFalse(fixture.model.missingCLIMessage.contains("Choose Codex"))
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
    }
}
