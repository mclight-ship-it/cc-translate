import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum SummaryPreferenceFixture {
    static func completeSave(_ f: ProductTestHarness, helper: ProductTestHelper) throws {
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.summaryPreferencePhase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: save.config)
    }

    static func completed(_ text: String, summarized: Bool = false, cached: Bool = false) -> [String: JSONValue] {
        ["text": .string(text), "kind": .string("text"), "target_lang": .string("en"),
         "summarize": .bool(summarized), "cached": .bool(cached), "submitted": .bool(!cached),
         "history": .string("disabled"), "history_error": .null]
    }
}

final class SummaryPreferenceTests: XCTestCase {
    @MainActor
    func testConstructionAndUnavailableSettingDoNotStartServicesOrInventConfirmedDefault() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        XCTAssertNil(f.model.summaryEnabled)
        XCTAssertFalse(f.model.canSaveSummaryPreference)
        XCTAssertFalse(f.model.canReloadSummaryPreference)
        f.model.saveSummaryPreference(false)
        XCTAssertEqual(f.model.summaryPreferencePhase, .failed("settings_unavailable"))
        f.model.reloadSummaryPreference()
        XCTAssertNil(f.model.summaryEnabled)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testNoCLISaveReadbackAndConnectionReopenUseSavedBooleanWithoutModelOrVersionCalls() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        XCTAssertTrue(helper.operations.contains("start.configuration"))
        XCTAssertTrue(f.model.needsCLI)
        XCTAssertFalse(f.model.nativeTranslation)
        XCTAssertEqual(f.model.summaryEnabled, true)
        XCTAssertTrue(f.model.canSaveSummaryPreference)
        f.model.saveSummaryPreference(false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["summary_enabled"], .bool(false))
        XCTAssertEqual(f.model.summaryEnabled, true, "A submitted save is not confirmed persistence.")
        XCTAssertEqual(f.model.summaryPreferencePhase, .saving)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.summaryEnabled, true, "The save acknowledgement still requires readback.")
        XCTAssertEqual(f.model.summaryPreferencePhase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.summaryEnabled, false)
        XCTAssertEqual(f.model.summaryPreferencePhase, .saved)
        XCTAssertFalse(f.model.settingsBusy)

        f.model.stopHelper()
        helper.stopped()
        XCTAssertNil(f.model.summaryEnabled)
        f.model.openProduct()
        let reopened = try f.ready(configuration: save.config)
        XCTAssertFalse(reopened === helper)
        XCTAssertEqual(f.model.summaryEnabled, false)
        f.model.saveSummaryPreference(true)
        try SummaryPreferenceFixture.completeSave(f, helper: reopened)
        XCTAssertEqual(f.model.summaryEnabled, true)
        for client in f.helpers {
            XCTAssertTrue(client.translations.isEmpty)
            XCTAssertTrue(client.resultActions.isEmpty)
            XCTAssertTrue(client.messages.isEmpty)
            XCTAssertEqual(client.configurationSaves.count, 1)
        }
        XCTAssertFalse(f.model.cliBusy)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testSummarySaveChangesExactlyOneKeyPreservingOptOutDraftsOpaqueFieldsAndSavedHistory() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        var config = ProductTestHarness.configuration(direction: "to_ja", model: "provider/saved", history: false)
        config["history_limit"] = .integer(100)
        config["font_size"] = .integer(12)
        config["max_chars"] = .integer(5000)
        config["future"] = .object(["data": .array([.string("opaque"), .bool(true)])])
        let helper = try f.ready(configuration: config)
        f.model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        ProductTestHarness.historyEntry(input: "Saved original", output: "Saved output")
                     ], total: 1))
        let row = try XCTUnwrap(f.model.historyPage.first)
        f.model.reuseHistory(row)
        f.model.direction = "to_ko"
        f.model.modelProfile = "provider/unsaved"
        f.model.editCustomModelID("provider/custom-draft")
        let originalMessage = f.model.productMessage
        let phase = f.model.productPhase
        f.model.saveSummaryPreference(false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        var expected = config
        expected["summary_enabled"] = .bool(false)
        XCTAssertEqual(save.config, expected)
        try SummaryPreferenceFixture.completeSave(f, helper: helper)
        XCTAssertEqual(f.model.direction, "to_ko")
        XCTAssertEqual(f.model.modelProfile, "provider/unsaved")
        XCTAssertEqual(f.model.modelSettings.draft, "provider/custom-draft")
        XCTAssertFalse(f.model.historyEnabled)
        XCTAssertEqual(f.model.input, row.input)
        XCTAssertEqual(f.model.output, row.output)
        XCTAssertEqual(f.model.historyPage.first?.id, row.id)
        XCTAssertEqual(f.model.historyPage.first?.input, row.input)
        XCTAssertEqual(f.model.historyPage.first?.output, row.output)
        XCTAssertEqual(f.model.productMessage, originalMessage)
        XCTAssertEqual(f.model.productPhase, phase)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertTrue(helper.messages.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertEqual(helper.historyLoads.count, 1)
        f.model.saveSummaryPreference(false)
        XCTAssertEqual(helper.configurationSaves.count, 1, "An unchanged value does not write again.")
    }

    @MainActor
    func testSaveFailureLeavesValueUnconfirmedUntilExplicitReloadAndNeverReplaysWrite() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.reuseHistory(.init(id: "retained", input: "Original", output: "Retained result"))
        let phase = f.model.productPhase
        let message = f.model.productMessage
        f.model.saveSummaryPreference(false)
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        XCTAssertNil(f.model.summaryEnabled, "A failed write must not claim either disk value.")
        XCTAssertEqual(f.model.summaryPreferencePhase, .failed("config_io_failed"))
        XCTAssertFalse(f.model.canSaveSummaryPreference)
        XCTAssertTrue(f.model.canReloadSummaryPreference)
        XCTAssertEqual(f.model.output, "Retained result")
        XCTAssertEqual(f.model.productPhase, phase)
        XCTAssertEqual(f.model.productMessage, message)
        XCTAssertEqual(helper.configurationLoads.count, 1, "No automatic recovery read or write.")
        f.model.reloadSummaryPreference()
        try f.finishConfiguration(on: helper)
        XCTAssertEqual(f.model.summaryEnabled, true)
        XCTAssertEqual(f.model.summaryPreferencePhase, .idle)
        XCTAssertTrue(f.model.canSaveSummaryPreference)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.stopCount, 0)
    }

    @MainActor
    func testReadbackFailureRecoversActualCommittedValueByReadOnlyReloadWithoutResaving() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.saveSummaryPreference(false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("config_io_failed")])
        XCTAssertNil(f.model.summaryEnabled)
        XCTAssertFalse(f.model.settingsReady)
        XCTAssertEqual(f.model.summaryPreferencePhase, .failed("config_io_failed"))
        XCTAssertTrue(f.model.canReloadSummaryPreference)
        f.model.reloadSummaryPreference()
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.summaryEnabled, false)
        XCTAssertEqual(f.model.summaryPreferencePhase, .idle)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 3)
        XCTAssertTrue(helper.messages.isEmpty)
    }

    @MainActor
    func testMismatchedReadbackShowsActualValueAndMissingOrInvalidFieldDoesNotInventDefault() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.saveSummaryPreference(false)
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        try f.finishConfiguration(on: helper)
        XCTAssertEqual(f.model.summaryEnabled, true)
        XCTAssertEqual(f.model.summaryPreferencePhase, .differentReadback)
        XCTAssertFalse(f.model.summaryPreferenceMessage.contains("saved and read back"))
        XCTAssertEqual(helper.configurationSaves.count, 1)
        let invalidValues: [JSONValue?] = [nil, .string("false"), .integer(0), .null]
        for value in invalidValues {
            f.model.reloadSummaryPreference()
            var invalid = ProductTestHarness.configuration()
            invalid["summary_enabled"] = value
            try f.finishConfiguration(on: helper, configuration: invalid)
            XCTAssertNil(f.model.summaryEnabled)
            XCTAssertEqual(f.model.summaryPreferencePhase, .failed("invalid_summary_preference"))
            XCTAssertTrue(f.model.settingsReady, "This field alone must not take down other product operations.")
            XCTAssertFalse(f.model.canSaveSummaryPreference)
            XCTAssertTrue(f.model.canReloadSummaryPreference)
        }
        f.model.reloadSummaryPreference()
        try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(summary: false))
        XCTAssertEqual(f.model.summaryEnabled, false)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.stopCount, 0)
    }

    @MainActor
    func testActiveOptedOutTranslationContinuesThroughSummarySaveAndReadbackWithoutCancelOrReplay() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(history: false))
        let original = String(repeating: "Long synthetic prose with complete sentences. ", count: 12)
        f.model.input = original
        f.model.translate(useCache: false)
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("delta", id: request.id, payload: ["text": .string("In-progress output"), "submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { f.model.output == "In-progress output" }
        let phase = f.model.productPhase
        let message = f.model.productMessage
        XCTAssertTrue(f.model.canSaveSummaryPreference)
        f.model.saveSummaryPreference(false)
        try SummaryPreferenceFixture.completeSave(f, helper: helper)
        XCTAssertTrue(f.model.active)
        XCTAssertEqual(f.model.productPhase, phase)
        XCTAssertEqual(f.model.productMessage, message)
        XCTAssertEqual(f.model.input, original)
        XCTAssertEqual(f.model.output, "In-progress output")
        XCTAssertFalse(f.model.historyEnabled)
        XCTAssertTrue(helper.messages.isEmpty, "Even with history already off, a summary-only save must not cancel.")
        XCTAssertEqual(helper.stopCount, 0)
        XCTAssertEqual(helper.translations.count, 1)
        helper.event("completed", id: request.id,
                     payload: SummaryPreferenceFixture.completed("Original snapshot summary and translation", summarized: true))
        XCTAssertEqual(f.model.output, "Original snapshot summary and translation")
        XCTAssertEqual(f.model.productPhase, .completed)
        XCTAssertFalse(f.model.active)
        XCTAssertEqual(f.model.summaryEnabled, false)
    }

    @MainActor
    func testActiveTranslationSurvivesBothSaveAndReadbackFailuresAndKeepsItsTerminalResult() async throws {
        for failReadback in [false, true] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.input = "Synthetic active request must not be invalidated by a future preference."
            f.model.translate(useCache: false)
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("delta", id: request.id, payload: ["text": .string("Partial"), "submitted": .bool(true)])
            try await CaptureProductFixture.waitFor { f.model.output == "Partial" }
            let message = f.model.productMessage
            f.model.saveSummaryPreference(false)
            var id = try XCTUnwrap(helper.configurationSaves.last?.id)
            if failReadback {
                helper.event("completed", id: id)
                id = try XCTUnwrap(helper.configurationLoads.last)
            }
            helper.event("failed", id: id, payload: ["code": .string("config_io_failed")])
            XCTAssertEqual(f.model.summaryPreferencePhase, .failed("config_io_failed"))
            XCTAssertEqual(f.model.productPhase, .translating)
            XCTAssertEqual(f.model.productMessage, message)
            XCTAssertEqual(f.model.output, "Partial")
            XCTAssertTrue(f.model.active)
            XCTAssertTrue(helper.messages.isEmpty)
            XCTAssertEqual(helper.stopCount, 0)
            XCTAssertEqual(helper.translations.count, 1)
            helper.event("completed", id: request.id, payload: SummaryPreferenceFixture.completed("Finished"))
            XCTAssertEqual(f.model.productPhase, .completed)
            XCTAssertEqual(f.model.output, "Finished")
            XCTAssertEqual(helper.configurationSaves.count, 1)
        }
    }

    @MainActor
    func testFutureCachedRequestUsesSavedConfigAndManualSummaryRemainsExplicitWhenAutomaticIsOff() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.saveSummaryPreference(false)
        try SummaryPreferenceFixture.completeSave(f, helper: helper)
        let input = "Synthetic future text translation with the usual cache behavior."
        f.model.input = input
        f.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        XCTAssertTrue(request.useCache, "The shared summary-aware signature owns cache separation, not a UI bypass.")
        XCTAssertEqual(request.text, input)
        XCTAssertEqual(request.origin, "text")
        XCTAssertEqual(helper.configurationSaves.count, 1, "Translate must preserve the read-back summary opt-out.")
        helper.event("completed", id: request.id,
                     payload: SummaryPreferenceFixture.completed("Cached translation for this config", cached: true))
        XCTAssertEqual(f.model.output, "Cached translation for this config")
        f.model.performResultAction(.summary)
        let action = try XCTUnwrap(helper.resultActions.last)
        XCTAssertEqual(action.action, .summary)
        XCTAssertEqual(action.text, "Cached translation for this config")
        helper.event("completed", id: action.id, payload: SummaryPreferenceFixture.completed("Explicit summary"))
        XCTAssertTrue(f.model.output.hasSuffix("Explicit summary"))
        XCTAssertEqual(f.model.summaryEnabled, false)
        XCTAssertEqual(helper.resultActions.count, 1)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testLateSummaryReadbackPreservesCompletedOrExplicitlyCancelledTranslationWithoutResubmission() async throws {
        for cancelled in [false, true] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.input = "Synthetic text with a late preference readback."
            f.model.translate(useCache: false)
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("delta", id: request.id,
                         payload: ["text": .string("Retained output"), "submitted": .bool(true)])
            try await CaptureProductFixture.waitFor { f.model.output == "Retained output" }
            f.model.saveSummaryPreference(false)
            let save = try XCTUnwrap(helper.configurationSaves.last)
            helper.event("completed", id: save.id)
            if cancelled {
                f.model.cancel()
                helper.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
            } else {
                helper.event("completed", id: request.id,
                             payload: SummaryPreferenceFixture.completed("Retained output"))
            }
            let phase = f.model.productPhase
            let message = f.model.productMessage
            let output = f.model.output
            XCTAssertEqual(phase, cancelled ? .cancelled : .completed)
            try f.finishConfiguration(on: helper, configuration: save.config)
            XCTAssertEqual(f.model.summaryEnabled, false)
            XCTAssertEqual(f.model.summaryPreferencePhase, .saved)
            XCTAssertEqual(f.model.productPhase, phase)
            XCTAssertEqual(f.model.productMessage, message)
            XCTAssertEqual(f.model.output, output)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertEqual(helper.messages.count, cancelled ? 1 : 0)
            XCTAssertEqual(helper.configurationSaves.count, 1)
        }
    }

    @MainActor
    func testDisconnectDuringSaveOrReadbackDropsPendingChoiceAndReopenDoesNotReplayIt() throws {
        for afterSave in [false, true] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.saveSummaryPreference(false)
            if afterSave { helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id)) }
            f.model.stopHelper()
            helper.stopped()
            XCTAssertNil(f.model.summaryEnabled)
            XCTAssertEqual(f.model.summaryPreferencePhase, .failed("connection_closed"))
            f.model.openProduct()
            let reopened = try f.ready(configuration: ProductTestHarness.configuration(summary: !afterSave))
            XCTAssertEqual(f.model.summaryEnabled, !afterSave)
            XCTAssertEqual(f.model.summaryPreferencePhase, .idle)
            XCTAssertTrue(reopened.configurationSaves.isEmpty)
            XCTAssertTrue(reopened.translations.isEmpty)
            XCTAssertTrue(reopened.messages.isEmpty)
        }
    }
}
