import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum CopyIntervalFixture {
    static func save(_ f: ProductTestHarness, _ value: String) {
        f.model.editCopyInterval(value)
        f.model.saveCopyInterval()
    }

    static func finish(_ f: ProductTestHarness, _ helper: ProductTestHelper) throws {
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.copyInterval.phase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: save.config)
    }
}

final class CopyIntervalPreferenceTests: XCTestCase {
    @MainActor
    func testDefaultAndUnavailableActionsNeverStartServicesOrEnableMonitoring() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        XCTAssertEqual(f.model.activeCopyInterval, .standard)
        XCTAssertNil(f.model.copyInterval.saved)
        XCTAssertFalse(f.model.canEditCopyInterval)
        CopyIntervalFixture.save(f, "0.75")
        f.model.reloadCopyInterval()
        XCTAssertEqual(f.model.copyInterval.phase, .failed("settings_unavailable"))
        XCTAssertEqual(f.model.copyInterval.draft, "0.75")
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertFalse(monitor.fallback)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testNumericReadbackAcceptsDecimalsAndWholeSecondsWithoutStartingMonitor() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for value in [JSONValue.number(0.1), .number(0.75), .integer(1), .number(2.25)] {
            var config = ProductTestHarness.configuration()
            config["double_press_window"] = value
            f.model.loadSettings()
            try f.finishConfiguration(on: helper, configuration: config)
            XCTAssertEqual(f.model.copyInterval.saved, value.number)
            XCTAssertEqual(monitor.copyInterval.seconds, value.number)
            XCTAssertEqual(f.model.copyInterval.draft, String(try XCTUnwrap(value.number)))
        }
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertFalse(monitor.fallback)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
    }

    @MainActor
    func testApplyChangesOneSavedKeyAndTimingOnlyAfterSuccessfulReadback() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        var config = ProductTestHarness.configuration(history: false)
        config["future_setting"] = .array([.string("Preserve"), .integer(42)])
        let helper = try f.ready(configuration: config)
        f.model.direction = "to_en"
        f.model.modelProfile = "auto"
        f.model.input = "Unsubmitted input"
        monitor.pending = true
        let operations = helper.operations
        f.model.editCopyInterval("0.75")
        XCTAssertEqual(helper.operations, operations)
        f.model.saveCopyInterval()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        config["double_press_window"] = .number(0.75)
        XCTAssertEqual(save.config, config)
        XCTAssertEqual(monitor.copyInterval, .standard)
        XCTAssertTrue(monitor.pending)
        helper.event("accepted", id: save.id)
        XCTAssertEqual(helper.configurationLoads.count, 1)
        helper.event("completed", id: save.id)
        XCTAssertEqual(monitor.copyInterval, .standard)
        XCTAssertTrue(monitor.pending)
        try f.finishConfiguration(on: helper, configuration: config)
        XCTAssertEqual(f.model.copyInterval.phase, .saved)
        XCTAssertEqual(monitor.copyInterval.seconds, 0.75)
        XCTAssertFalse(monitor.pending)
        XCTAssertEqual(f.preferences.string(forKey: ProbeModel.copyIntervalHintKey), "0.75")
        XCTAssertEqual(f.model.input, "Unsubmitted input")
        XCTAssertEqual(f.model.direction, "to_en")
        XCTAssertEqual(f.model.modelProfile, "auto")
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertFalse(monitor.fallback)
        XCTAssertTrue(helper.messages.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testActiveMonitorKeepsRegistrationAndOptInWhenConfirmedTimingChanges() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.model.stopMonitor(); f.cleanUp() }
        f.model.translatePassiveSelections = true
        f.model.startMonitor()
        XCTAssertTrue(monitor.running)
        XCTAssertTrue(f.helpers.isEmpty)
        let helper = try f.ready()
        monitor.pending = true
        CopyIntervalFixture.save(f, "0.75")
        XCTAssertTrue(monitor.pending)
        try CopyIntervalFixture.finish(f, helper)
        XCTAssertTrue(monitor.running)
        XCTAssertTrue(f.model.monitorEnabled)
        XCTAssertTrue(f.model.translatePassiveSelections)
        XCTAssertTrue(monitor.fallback)
        XCTAssertEqual(monitor.starts, 1)
        XCTAssertEqual(monitor.stops, 0)
        XCTAssertFalse(monitor.pending)
        XCTAssertEqual(monitor.copyInterval.seconds, 0.75)
    }

    @MainActor
    func testInvalidDraftsDoNotWriteAndPositiveValuesAreNotCappedAtSuggestedSpeed() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for value in ["", "0", "-1", "nan", "inf", "-inf", "0,75", "abc", "9007199254740992"] {
            CopyIntervalFixture.save(f, value)
            XCTAssertEqual(f.model.copyInterval.phase, .invalidInput)
            XCTAssertEqual(f.model.copyInterval.draft, value)
            XCTAssertEqual(f.model.activeCopyInterval, .standard)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
        for value in [0.1, 0.75, 2.25, 60] {
            CopyIntervalFixture.save(f, String(value))
            try CopyIntervalFixture.finish(f, helper)
            XCTAssertEqual(f.model.copyInterval.saved, value)
            XCTAssertEqual(f.model.activeCopyInterval.seconds, value)
        }
    }

    @MainActor
    func testSaveAndReadbackFailuresKeepActiveTimingUntilExplicitReadOnlyRecovery() throws {
        for failReadback in [false, true] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            CopyIntervalFixture.save(f, "0.75")
            var id = try XCTUnwrap(helper.configurationSaves.last?.id)
            if failReadback {
                helper.event("completed", id: id)
                id = try XCTUnwrap(helper.configurationLoads.last)
            }
            helper.event("failed", id: id, payload: ["code": .string("config_io_failed")])
            XCTAssertEqual(f.model.copyInterval.phase, .failed("config_io_failed"))
            XCTAssertNil(f.model.copyInterval.saved)
            XCTAssertEqual(f.model.activeCopyInterval, .standard)
            XCTAssertEqual(f.preferences.string(forKey: ProbeModel.copyIntervalHintKey), "0.5")
            let reads = helper.configurationLoads.count
            f.model.reloadCopyInterval()
            XCTAssertEqual(helper.configurationLoads.count, reads + 1)
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(copyInterval: 0.75))
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.75)
            XCTAssertEqual(f.model.copyInterval.draft, "0.75")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testMalformedOrNonpositiveReadbackKeepsActiveTimingAndNeverRepairsConfigSilently() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(copyInterval: 0.75))
        for value in [JSONValue.string("1"), .bool(true), .null, .number(0), .number(-7)] {
            var config = ProductTestHarness.configuration()
            if value == .null { config.removeValue(forKey: "double_press_window") }
            else { config["double_press_window"] = value }
            f.model.loadSettings()
            try f.finishConfiguration(on: helper, configuration: config)
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.75)
            XCTAssertEqual(f.model.copyInterval.saved, value.number)
            if value.number == nil {
                XCTAssertEqual(f.model.copyInterval.phase, .failed("invalid_copy_interval"))
            } else {
                XCTAssertTrue(f.model.canEditCopyInterval)
            }
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
        CopyIntervalFixture.save(f, "1")
        try CopyIntervalFixture.finish(f, helper)
        XCTAssertEqual(f.model.activeCopyInterval.seconds, 1)
        XCTAssertEqual(f.model.copyInterval.phase, .saved)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testMismatchLaterDraftAndRetiredConnectionNeverOverwriteCurrentTiming() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        CopyIntervalFixture.save(f, "0.75")
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        let retiredRead = try XCTUnwrap(helper.configurationLoads.last)
        f.model.editCopyInterval("0.9")
        var config = ProductTestHarness.configuration()
        config["double_press_window"] = .integer(1)
        try f.finishConfiguration(on: helper, configuration: config)
        XCTAssertEqual(f.model.copyInterval.phase, .differentReadback)
        XCTAssertEqual(f.model.activeCopyInterval.seconds, 1)
        XCTAssertEqual(f.model.copyInterval.draft, "0.9")
        f.model.stopHelper()
        helper.stopped()
        XCTAssertEqual(f.model.activeCopyInterval.seconds, 1)
        f.model.openProduct()
        let reopened = try f.ready(configuration: ProductTestHarness.configuration(copyInterval: 0.8))
        helper.event("completed", id: retiredRead, payload: ["config": .object(config)])
        helper.failure(.invalidTransition)
        XCTAssertTrue(f.model.ready)
        XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.8)
        XCTAssertEqual(f.model.copyInterval.draft, "0.9")
        XCTAssertTrue(reopened.configurationSaves.isEmpty)
    }

    @MainActor
    func testConfirmedHintRestoresNativeTimingWithoutBusinessIOAndConfigReadWins() throws {
        for raw in ["0.75", "0", "-1", "nan", "inf", "9007199254740992", "invalid"] {
            let monitor = SelectionMonitorFixture()
            let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
            defer { f.cleanUp() }
            f.preferences.set(raw, forKey: ProbeModel.copyIntervalHintKey)
            f.model.loadPresentation()
            XCTAssertEqual(f.model.activeCopyInterval.seconds, raw == "0.75" ? 0.75 : 0.5)
            XCTAssertEqual(monitor.copyInterval, f.model.activeCopyInterval)
            XCTAssertNil(f.model.copyInterval.saved, "A local hint is not a confirmed helper read.")
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.runtimeRequests, 0)
            XCTAssertEqual(f.locatorRequests, 0)
            XCTAssertEqual(monitor.starts, 0)
            XCTAssertEqual(f.model.permissions, "Not checked.")
            let helper = try f.ready(configuration: ProductTestHarness.configuration(copyInterval: 1.25))
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 1.25)
            XCTAssertEqual(monitor.copyInterval.seconds, 1.25)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
    }

    @MainActor
    func testUnchangedReadbackPreservesPendingCopyAndNativePersistenceOptOut() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        let helper = try f.ready()
        monitor.pending = true
        f.model.loadSettings()
        try f.finishConfiguration(on: helper)
        XCTAssertTrue(monitor.pending)
        f.preferences.set("0.75", forKey: ProbeModel.copyIntervalHintKey)
        let diagnostic = ProbeModel(preferences: f.preferences, persistsPreferences: false)
        diagnostic.loadPresentation()
        diagnostic.persistPresentation()
        XCTAssertEqual(diagnostic.activeCopyInterval, .standard)
        XCTAssertEqual(f.preferences.string(forKey: ProbeModel.copyIntervalHintKey), "0.75")
    }

    @MainActor
    func testIntervalSaveOrFailureDoesNotCancelSubmittedTranslationOrChangeHistory() throws {
        for failSave in [false, true] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(history: false))
            f.model.loadHistory()
            helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                         payload: ProductTestHarness.historyPage(entries: [
                            ProductTestHarness.historyEntry(input: "Saved input", output: "Saved output")
                         ], total: 1))
            let row = try XCTUnwrap(f.model.historyPage.first)
            f.model.input = "In-progress translation"
            f.model.translate(useCache: false)
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("accepted", id: request.id)
            let intent = f.model.translationIntentID
            CopyIntervalFixture.save(f, "0.75")
            if failSave {
                helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                             payload: ["code": .string("config_io_failed")])
            } else {
                try CopyIntervalFixture.finish(f, helper)
            }
            XCTAssertTrue(f.model.active)
            XCTAssertEqual(f.model.translationIntentID, intent)
            XCTAssertEqual(f.model.productPhase, .translating)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertTrue(helper.messages.isEmpty)
            helper.event("completed", id: request.id, payload: SummaryPreferenceFixture.completed("Original response"))
            XCTAssertEqual(f.model.output, "Original response")
            XCTAssertEqual(f.model.historyPage.first?.id, row.id)
            XCTAssertEqual(f.model.historyPage.first?.output, "Saved output")
            XCTAssertEqual(helper.historyLoads.count, 1)
            XCTAssertTrue(helper.historyClears.isEmpty)
        }
    }
}
