import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum SettingsDefaultsFixture {
    nonisolated static func canonical() -> [String: JSONValue] {
        var config = ProductTestHarness.configuration()
        config["codex_model_default_migrated"] = .bool(true)
        config["local_dictionary_enabled"] = .bool(false)
        config["plain_text_paste_enabled"] = .bool(false)
        return config
    }

    static func prepare(_ model: ProbeModel, helper: ProductTestHelper,
                        defaults: [String: JSONValue] = SettingsDefaultsFixture.canonical()) throws {
        model.prepareDefaultsRestore()
        XCTAssertEqual(model.defaultsPhase, .loading)
        let request = try XCTUnwrap(helper.messages.last)
        XCTAssertEqual(request.type, "request")
        XCTAssertEqual(request.payload, ["operation": .string("config_load"), "defaults": .bool(true)])
        helper.event("accepted", id: request.id, payload: ["operation": .string("config_load")])
        helper.event("started", id: request.id, payload: ["operation": .string("config_load")])
        XCTAssertEqual(model.defaultsPhase, .loading)
        helper.event("completed", id: request.id, payload: ["config": .object(defaults)])
        XCTAssertEqual(model.defaultsPhase, .confirming)
        XCTAssertFalse(model.settingsBusy)
    }

    static func finish(_ f: ProductTestHarness, helper: ProductTestHelper,
                       readback: [String: JSONValue]? = nil) throws {
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.defaultsPhase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: readback ?? save.config)
    }
}

final class SettingsDefaultsTests: XCTestCase {
    func testCanonicalMergeOnlyChangesSupportedKeysAndRequiresCompleteTypedDefaults() throws {
        let canonical = SettingsDefaultsFixture.canonical()
        let defaults = try XCTUnwrap(SettingsDefaults(canonical))
        let current: [String: JSONValue] = [
            "max_chars": .integer(9999), "theme": .string("dark"), "font_size": .integer(17),
            "language": .string("fr_FR"), "ocr_hotkey_enabled": .bool(true),
            "future": .object(["nested": .array([.null, .bool(false)])])
        ]
        let merged = defaults.merging(into: current)
        XCTAssertTrue(defaults.matches(merged))
        for key in ["theme", "font_size", "language", "ocr_hotkey_enabled", "future"] {
            XCTAssertEqual(merged[key], current[key])
        }
        XCTAssertEqual(merged["max_chars"], canonical["max_chars"])
        for key in defaults.values.keys {
            var missing = canonical
            missing.removeValue(forKey: key)
            XCTAssertNil(SettingsDefaults(missing), key)
            var invalid = canonical
            invalid[key] = .array([])
            XCTAssertNil(SettingsDefaults(invalid), key)
            var different = merged
            different[key] = .null
            XCTAssertFalse(defaults.matches(different), key)
        }
        var numeric = canonical
        numeric["double_press_window"] = .number(1)
        let numericDefaults = try XCTUnwrap(SettingsDefaults(numeric))
        numeric["double_press_window"] = .integer(1)
        XCTAssertTrue(numericDefaults.matches(numeric))
        var unicode = canonical
        unicode["codex_model"] = .string("fixture/e\u{301}")
        let exact = try XCTUnwrap(SettingsDefaults(unicode))
        unicode["codex_model"] = .string("fixture/\u{e9}")
        XCTAssertFalse(exact.matches(unicode))
    }

    @MainActor
    func testUnavailableActionAndConstructionDoNotStartServices() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        XCTAssertEqual(f.model.defaultsPhase, .idle)
        XCTAssertFalse(f.model.canRestoreDefaults)
        f.model.prepareDefaultsRestore()
        XCTAssertEqual(f.model.defaultsPhase, .failed("settings_unavailable"))
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testReadOnlyPreviewAndCancelDoNotChangePreferencesOrWriteAnything() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        f.model.appearance = "dark"
        f.model.persistPresentation()
        let preferences = f.preferences.dictionaryRepresentation()
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        XCTAssertTrue(f.model.defaultsConfirmationMessage.contains("100"))
        XCTAssertTrue(f.model.defaultsConfirmationMessage.contains("History saving will be turned on"))
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        f.model.cancelDefaultsRestore()
        XCTAssertEqual(f.model.defaultsPhase, .idle)
        XCTAssertEqual(f.model.appearance, "dark")
        XCTAssertEqual(f.preferences.dictionaryRepresentation() as NSDictionary, preferences as NSDictionary)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertEqual(helper.configurationLoads.count, 1)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testOneConfirmedWriteUsesCanonicalValuesAndReadbackBeforeApplyingLocalDefaults() throws {
        let registrar = PasteTestRegistrar()
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor, captureRegistrar: registrar)
        defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
        var original = SettingsDefaultsFixture.canonical()
        original["history_enabled"] = .bool(false)
        original["history_limit"] = .integer(900)
        original["future"] = .object(["preserved": .bool(true)])
        original["ocr_hotkey_enabled"] = .bool(true)
        let helper = try f.ready(configuration: original)
        f.model.reuseHistory(.init(id: "saved-row", input: "Existing original", output: "Existing result"))
        f.model.appearance = "dark"
        f.model.interfaceLanguage = "zh"
        f.model.nativeTextScale = .largest
        f.model.resultPlacement = .pointer
        f.model.captureShortcut.choose(true)
        f.model.translatePassiveSelections = true
        f.model.editHistoryLimit("700")
        f.model.editInputLimit("8000")
        f.model.editCopyInterval("0.9")
        f.model.modelProfile = "fixture/unsaved"
        f.model.editCustomModelID("fixture/custom-draft")
        var canonical = SettingsDefaultsFixture.canonical()
        canonical["history_limit"] = .integer(37)
        canonical["max_chars"] = .integer(3217)
        canonical["double_press_window"] = .number(0.75)
        canonical["summary_enabled"] = .bool(false)
        try SettingsDefaultsFixture.prepare(f.model, helper: helper, defaults: canonical)
        XCTAssertTrue(f.model.defaultsConfirmationMessage.contains("37"))
        f.model.confirmDefaultsRestore()
        XCTAssertEqual(f.model.defaultsPhase, .saving)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config, try XCTUnwrap(SettingsDefaults(canonical)).merging(into: original))
        XCTAssertEqual(f.model.appearance, "dark")
        XCTAssertTrue(f.model.captureShortcut.enabled)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.defaultsPhase, .readingBack)
        XCTAssertEqual(f.model.appearance, "dark", "A save acknowledgement is not verified readback.")
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.defaultsPhase, .restored)
        XCTAssertEqual(f.model.appearance, "system")
        XCTAssertEqual(f.model.interfaceLanguage, "system")
        XCTAssertEqual(f.model.nativeTextScale, .standard)
        XCTAssertEqual(f.model.resultPlacement, .remembered)
        XCTAssertFalse(f.model.captureShortcut.enabled)
        XCTAssertFalse(f.model.translatePassiveSelections)
        XCTAssertFalse(monitor.fallback)
        XCTAssertEqual(f.model.historyLimit.draft, "37")
        XCTAssertEqual(f.model.inputLimit.draft, "3217")
        XCTAssertEqual(f.model.copyInterval.draft, "0.75")
        XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.75)
        XCTAssertEqual(f.model.modelProfile, "auto-fast")
        XCTAssertEqual(f.model.modelSettings.draft, "")
        XCTAssertEqual(f.model.summaryEnabled, false)
        XCTAssertTrue(f.model.historyEnabled)
        XCTAssertEqual(f.model.input, "Existing original")
        XCTAssertEqual(f.model.output, "Existing result")
        XCTAssertEqual(f.preferences.string(forKey: "appearance"), "system")
        XCTAssertFalse(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.allSatisfy { $0.request.operation == DictionaryRequest.status.operation })
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testMismatchAndWriteFailureNeverApplyLocalDefaultsOrReplayWrite() throws {
        for mismatch in [true, false] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
            f.model.appearance = "dark"
            try SettingsDefaultsFixture.prepare(f.model, helper: helper)
            f.model.confirmDefaultsRestore()
            let save = try XCTUnwrap(helper.configurationSaves.last)
            if mismatch {
                var wrong = save.config
                wrong["max_chars"] = .integer(5001)
                try SettingsDefaultsFixture.finish(f, helper: helper, readback: wrong)
                XCTAssertEqual(f.model.defaultsPhase, .differentReadback)
            } else {
                helper.event("failed", id: save.id, payload: ["code": .string("config_io_failed")])
                XCTAssertEqual(f.model.defaultsPhase, .failed("config_io_failed"))
                XCTAssertEqual(helper.configurationLoads.count, 1)
            }
            XCTAssertEqual(f.model.appearance, "dark")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertFalse(f.model.settingsBusy)
        }
    }

    @MainActor
    func testReadFailureInvalidDefaultsAndDuplicatePrepareDoNotWriteOrInventValues() throws {
        for failure in [true, false] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
            f.model.prepareDefaultsRestore()
            let request = try XCTUnwrap(helper.messages.last)
            f.model.prepareDefaultsRestore()
            XCTAssertEqual(helper.messages.count, 1)
            if failure {
                helper.event("failed", id: request.id, payload: ["code": .string("config_unavailable")])
                XCTAssertEqual(f.model.defaultsPhase, .failed("config_unavailable"))
            } else {
                helper.event("completed", id: request.id, payload: ["config": .object([:])])
                XCTAssertEqual(f.model.defaultsPhase, .failed("invalid_defaults"))
            }
            XCTAssertFalse(f.model.settingsBusy)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testDisconnectAtEveryPhaseRetiresLateRepliesWithoutApplyingLocalDefaults() throws {
        for stage in 0...3 {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
            f.model.appearance = "dark"
            f.model.prepareDefaultsRestore()
            var id = try XCTUnwrap(helper.messages.last?.id)
            if stage > 0 {
                helper.event("completed", id: id, payload: ["config": .object(SettingsDefaultsFixture.canonical())])
            }
            if stage > 1 {
                f.model.confirmDefaultsRestore()
                id = try XCTUnwrap(helper.configurationSaves.last?.id)
            }
            if stage > 2 {
                helper.event("completed", id: id)
                id = try XCTUnwrap(helper.configurationLoads.last)
            }
            f.model.stopHelper()
            helper.stopped()
            XCTAssertEqual(f.model.defaultsPhase, .failed("connection_closed"))
            helper.event("completed", id: id, payload: ["config": .object(SettingsDefaultsFixture.canonical())])
            XCTAssertEqual(f.model.appearance, "dark")
            XCTAssertEqual(f.model.defaultsPhase, .failed("connection_closed"))
            XCTAssertFalse(f.model.settingsBusy)
            XCTAssertEqual(helper.configurationSaves.count, stage > 1 ? 1 : 0)
            if stage == 3 {
                XCTAssertTrue(f.model.canReloadDefaults)
                f.model.reloadDefaultsSettings()
                let reopened = try f.ready(configuration: SettingsDefaultsFixture.canonical())
                XCTAssertFalse(reopened === helper)
                XCTAssertTrue(reopened.configurationSaves.isEmpty)
                XCTAssertTrue(reopened.messages.isEmpty, "Reload must not replay the defaults request.")
                XCTAssertEqual(f.model.appearance, "dark")
            }
        }
    }

    @MainActor
    func testLaterEditorDraftsAndSelectedCLIPathArePreserved() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        let cli = f.model.selectedCLI
        let savedCLI = f.preferences.string(forKey: "selectedCodexPath")
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        f.model.direction = "to_ja"
        f.model.modelProfile = "fixture/later"
        f.model.editCustomModelID("fixture/later-draft")
        f.model.editHistoryLimit("731")
        f.model.editInputLimit("9876")
        f.model.editCopyInterval("0.83")
        try SettingsDefaultsFixture.finish(f, helper: helper)
        XCTAssertEqual(f.model.defaultsPhase, .restored)
        XCTAssertEqual(f.model.direction, "to_ja")
        XCTAssertEqual(f.model.modelProfile, "fixture/later")
        XCTAssertEqual(f.model.modelSettings.draft, "fixture/later-draft")
        XCTAssertEqual(f.model.historyLimit.draft, "731")
        XCTAssertEqual(f.model.inputLimit.draft, "9876")
        XCTAssertEqual(f.model.copyInterval.draft, "0.83")
        XCTAssertEqual(f.model.selectedCLI, cli)
        XCTAssertEqual(f.preferences.string(forKey: "selectedCodexPath"), savedCLI)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testFailedShortcutReleaseRemainsVisibleInsteadOfClaimingFullSuccess() throws {
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
        defer {
            registrar.leases.last?.releaseError = nil
            f.model.captureShortcut.shutdown()
            f.cleanUp()
        }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        f.model.captureShortcut.choose(true)
        try XCTUnwrap(registrar.leases.last).releaseError = .releaseFailed(-50)
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        try SettingsDefaultsFixture.finish(f, helper: helper)
        XCTAssertEqual(f.model.defaultsPhase, .shortcutCleanupRequired)
        XCTAssertFalse(f.model.captureShortcut.enabled)
        XCTAssertEqual(f.model.captureShortcut.registration, .failed(.releaseFailed(-50)))
        XCTAssertTrue(f.model.captureShortcut.canRetry)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testRestoreUsesExistingPasteIntentSaveReadbackAndReleasesItsLease() throws {
        let f = try PasteAppFixture()
        defer { f.cleanUp() }
        let helper = try f.ready(true)
        XCTAssertTrue(f.service.state.enabled)
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        XCTAssertFalse(f.service.state.enabled, "Explicit disabling stops external actions while saving.")
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        try f.finishRead(configuration: save.config)
        XCTAssertEqual(f.model.defaultsPhase, .restored)
        XCTAssertEqual(f.paste.preference.phase, .confirmed)
        XCTAssertFalse(f.paste.preference.toggleValue)
        XCTAssertFalse(f.paste.preference.authorized)
        XCTAssertEqual(f.paste.registration, .off)
        XCTAssertTrue(f.service.requests.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }
}
