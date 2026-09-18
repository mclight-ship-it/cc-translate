import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum HistoryLimitFixture {
    static func save(_ f: ProductTestHarness, value: String) {
        f.model.editHistoryLimit(value)
        f.model.applyHistoryLimit()
        if f.model.historyLimit.confirmation != nil { f.model.confirmHistoryLimitReduction() }
    }

    static func finish(_ f: ProductTestHarness, helper: ProductTestHelper) throws {
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.historyLimit.phase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: save.config)
    }

    static func completion(history: String = "recorded", kind: String = "text") -> [String: JSONValue] {
        ["text": .string("Completed translation"), "kind": .string(kind), "target_lang": .string("en"),
         "summarize": .bool(false), "cached": .bool(history == "unchanged"),
         "submitted": .bool(history != "unchanged"), "history": .string(history),
         "history_error": history == "failed" ? .string("history_io_failed") : .null]
    }
}

final class HistoryLimitPreferenceTests: XCTestCase {
    @MainActor
    func testConstructionAndUnavailableActionsDoNotStartServicesOrInventSavedLimit() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        XCTAssertNil(f.model.historyLimit.saved)
        XCTAssertEqual(f.model.historyLimit.draft, "")
        XCTAssertFalse(f.model.canEditHistoryLimit)
        XCTAssertFalse(f.model.canReloadHistoryLimit)
        f.model.editHistoryLimit("17")
        f.model.applyHistoryLimit()
        f.model.reloadHistoryLimit()
        XCTAssertNil(f.model.historyLimit.saved)
        XCTAssertEqual(f.model.historyLimit.draft, "17")
        XCTAssertEqual(f.model.historyLimit.phase, .failed("settings_unavailable"))
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testNormalizedDefaultAndLegacyValuesRemainExactWithoutAutomaticWritesOrClamping() throws {
        for value in [Int64(100), 17, 600, 1, 10_000, 0, -5, 10_001] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(historyLimit: value))
            XCTAssertEqual(f.model.historyLimit.saved, value)
            XCTAssertEqual(f.model.historyLimit.draft, String(value))
            XCTAssertTrue(f.model.canEditHistoryLimit, "Unsupported stored ranges remain explicitly correctable.")
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            if !HistoryLimitPreference.supported.contains(value) {
                HistoryLimitFixture.save(f, value: "100")
                try HistoryLimitFixture.finish(f, helper: helper)
                XCTAssertEqual(f.model.historyLimit.saved, 100)
            }
        }
    }

    @MainActor
    func testInvalidDraftsNeverCoerceClampSaveOrReadHistoryAndFullNativeRangeIsAccepted() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for draft in ["", "0", "-1", "10001", "1.5", "abc", "9999999999999999999999999999"] {
            f.model.editHistoryLimit(draft)
            f.model.applyHistoryLimit()
            XCTAssertEqual(f.model.historyLimit.draft, draft)
            XCTAssertEqual(f.model.historyLimit.saved, 100)
            XCTAssertEqual(f.model.historyLimit.phase, .invalidInput)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
        for draft in ["1", "17", "600", "10000"] {
            HistoryLimitFixture.save(f, value: draft)
            try HistoryLimitFixture.finish(f, helper: helper)
            XCTAssertEqual(f.model.historyLimit.saved, Int64(draft))
        }
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testReductionRequiresExactOldAndNewConfirmationAndCancelHasNoIO() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(historyLimit: 600))
        f.model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        ProductTestHarness.historyEntry(input: "Saved original", output: "Saved output")
                     ], total: 400, nextOffset: 1))
        let row = try XCTUnwrap(f.model.historyPage.first)
        let operations = helper.operations
        f.model.editHistoryLimit("17")
        XCTAssertEqual(helper.operations, operations, "Typing must not submit anything.")
        f.model.applyHistoryLimit()
        XCTAssertEqual(f.model.historyLimit.confirmation, HistoryLimitPreference.Reduction(from: 600, to: 17))
        XCTAssertEqual(helper.operations, operations)
        f.model.cancelHistoryLimitReduction()
        XCTAssertNil(f.model.historyLimit.confirmation)
        XCTAssertEqual(helper.operations, operations)
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        XCTAssertEqual(f.model.historyLimit.draft, "17")
        XCTAssertEqual(f.model.historyPage.first?.id, row.id)
        XCTAssertEqual(f.model.historyTotal, 400)
        XCTAssertTrue(f.model.hasNextHistoryPage)

        f.model.applyHistoryLimit()
        f.model.confirmHistoryLimitReduction()
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationSaves.last?.config["history_limit"], .integer(17))
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        try HistoryLimitFixture.finish(f, helper: helper)
        XCTAssertEqual(f.model.historyLimit.saved, 17)
        XCTAssertEqual(f.model.historyTotal, 400, "Saving does not pretend that retention has already run.")
        XCTAssertEqual(f.model.historyPage.first?.id, row.id)
        XCTAssertTrue(f.model.hasNextHistoryPage)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertTrue(helper.historyClears.isEmpty)
        f.model.loadHistory(next: true)
        XCTAssertNotEqual(helper.historyLoads.last?.cursor, .null, "Config-only changes do not invalidate history cursors.")
    }

    @MainActor
    func testReadbackChangingSavedValueInvalidatesAnOlderReductionConfirmation() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(historyLimit: 600))
        f.model.editHistoryLimit("17")
        f.model.applyHistoryLimit()
        f.model.loadSettings()
        try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(historyLimit: 400))
        XCTAssertNil(f.model.historyLimit.confirmation)
        XCTAssertEqual(f.model.historyLimit.draft, "17")
        f.model.confirmHistoryLimitReduction()
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        f.model.applyHistoryLimit()
        XCTAssertEqual(f.model.historyLimit.confirmation, HistoryLimitPreference.Reduction(from: 400, to: 17))
        f.model.editHistoryLimit("20")
        XCTAssertNil(f.model.historyLimit.confirmation)
        f.model.confirmHistoryLimitReduction()
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
    }

    @MainActor
    func testConfigOnlySingleKeySaveReadbackAndReopenPreserveOptOutOpaqueFieldsAndEditorDrafts() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        var config = ProductTestHarness.configuration(history: false)
        config["future"] = .object(["opaque": .array([.string("keep"), .bool(true)])])
        config["font_size"] = .integer(12)
        let helper = try f.ready(configuration: config)
        XCTAssertTrue(f.model.needsCLI)
        XCTAssertFalse(f.model.nativeTranslation)
        f.model.reuseHistory(.init(id: "shown", input: "Original", output: "Result"))
        f.model.direction = "to_ja"
        f.model.modelProfile = "provider/unsaved"
        f.model.editCustomModelID("provider/custom-draft")
        let phase = f.model.productPhase
        let message = f.model.productMessage
        HistoryLimitFixture.save(f, value: "600")
        let save = try XCTUnwrap(helper.configurationSaves.last)
        var expected = config
        expected["history_limit"] = .integer(600)
        XCTAssertEqual(save.config, expected)
        XCTAssertEqual(f.model.historyLimit.phase, .saving)
        XCTAssertEqual(f.model.historyLimit.saved, 100)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.historyLimit.saved, 100, "An acknowledgement is not readback.")
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.historyLimit.phase, .saved)
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        XCTAssertEqual(f.model.direction, "to_ja")
        XCTAssertEqual(f.model.modelProfile, "provider/unsaved")
        XCTAssertEqual(f.model.modelSettings.draft, "provider/custom-draft")
        XCTAssertEqual(f.model.input, "Original")
        XCTAssertEqual(f.model.output, "Result")
        XCTAssertEqual(f.model.productPhase, phase)
        XCTAssertEqual(f.model.productMessage, message)
        XCTAssertFalse(f.model.historyEnabled)
        f.model.applyHistoryLimit()
        XCTAssertEqual(helper.configurationSaves.count, 1)

        f.model.stopHelper()
        helper.stopped()
        XCTAssertNil(f.model.historyLimit.saved)
        f.model.openProduct()
        let reopened = try f.ready(configuration: save.config)
        XCTAssertFalse(reopened === helper)
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        XCTAssertEqual(f.model.historyLimit.draft, "600")
        XCTAssertTrue(reopened.configurationSaves.isEmpty)
        for client in f.helpers {
            XCTAssertTrue(client.translations.isEmpty)
            XCTAssertTrue(client.resultActions.isEmpty)
            XCTAssertTrue(client.messages.isEmpty)
            XCTAssertTrue(client.historyLoads.isEmpty)
            XCTAssertTrue(client.historyClears.isEmpty)
        }
    }

    @MainActor
    func testSaveAndReadbackFailureKeepDraftUnknownUntilExplicitReloadWithoutWriteReplay() throws {
        for failReadback in [false, true] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.reuseHistory(.init(id: "shown", input: "Original", output: "Result"))
            HistoryLimitFixture.save(f, value: "600")
            let save = try XCTUnwrap(helper.configurationSaves.last)
            if failReadback { helper.event("completed", id: save.id) }
            let failedID = failReadback ? helper.configurationLoads.last : save.id
            helper.event("failed", id: try XCTUnwrap(failedID),
                         payload: ["code": .string("config_io_failed")])
            XCTAssertNil(f.model.historyLimit.saved)
            XCTAssertEqual(f.model.historyLimit.draft, "600")
            XCTAssertEqual(f.model.historyLimit.phase, .failed("config_io_failed"))
            XCTAssertFalse(f.model.canEditHistoryLimit)
            XCTAssertTrue(f.model.canReloadHistoryLimit)
            XCTAssertEqual(f.model.output, "Result")
            XCTAssertEqual(f.model.productPhase, .completed)
            XCTAssertEqual(helper.configurationLoads.count, failReadback ? 2 : 1)
            f.model.reloadHistoryLimit()
            try f.finishConfiguration(on: helper, configuration: failReadback ? save.config : ProductTestHarness.configuration())
            XCTAssertEqual(f.model.historyLimit.saved, failReadback ? 600 : 100)
            XCTAssertEqual(f.model.historyLimit.draft, "600")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)
        }
    }

    @MainActor
    func testMismatchOrMissingOrWrongTypedReadbackNeverClaimsSavedOrInventsDefault() throws {
        let fields: [JSONValue?] = [.integer(17), nil, .string("600"), .bool(true)]
        for field in fields {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            HistoryLimitFixture.save(f, value: "600")
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            var config = ProductTestHarness.configuration()
            config["history_limit"] = field
            try f.finishConfiguration(on: helper, configuration: config)
            XCTAssertEqual(f.model.historyLimit.draft, "600")
            if field == .integer(17) {
                XCTAssertEqual(f.model.historyLimit.saved, 17)
                XCTAssertEqual(f.model.historyLimit.phase, .differentReadback)
            } else {
                XCTAssertNil(f.model.historyLimit.saved)
                XCTAssertEqual(f.model.historyLimit.phase, .failed("invalid_history_limit"))
                XCTAssertFalse(f.model.canEditHistoryLimit)
            }
            XCTAssertTrue(f.model.settingsReady, "A bad optional field must not fake another value or disable unrelated settings.")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertEqual(helper.configurationLoads.count, 2)
        }

        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        HistoryLimitFixture.save(f, value: "600")
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        f.model.editHistoryLimit("700")
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.historyLimit.phase, .saved)
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        XCTAssertEqual(f.model.historyLimit.draft, "700", "A late editor update is not overwritten by readback of the earlier choice.")
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testActiveOptedOutTranslationSurvivesLimitSaveAndFailuresWithoutCancelOrReplay() throws {
        for failure in ["none", "save", "readback"] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let config = ProductTestHarness.configuration(history: false)
            let helper = try f.ready(configuration: config)
            f.model.input = "This translation is already running."
            f.model.translate()
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("delta", id: request.id, payload: ["text": .string("partial"), "submitted": .bool(true)])
            HistoryLimitFixture.save(f, value: "600")
            let save = try XCTUnwrap(helper.configurationSaves.last)
            if failure == "save" {
                helper.event("failed", id: save.id, payload: ["code": .string("config_io_failed")])
            } else {
                helper.event("completed", id: save.id)
                if failure == "readback" {
                    helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                                 payload: ["code": .string("config_io_failed")])
                } else {
                    try f.finishConfiguration(on: helper, configuration: save.config)
                }
            }
            XCTAssertTrue(f.model.active)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertTrue(helper.messages.isEmpty, "Limit changes must not use the generic history-off cancellation path.")
            helper.event("completed", id: request.id, payload: HistoryLimitFixture.completion(history: "disabled"))
            XCTAssertEqual(f.model.output, "Completed translation")
            XCTAssertEqual(f.model.productPhase, .completed)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertTrue(helper.historyLoads.isEmpty)
        }
    }

    @MainActor
    func testDisconnectRetiresPendingLimitSaveAndNeverReplaysItAfterReopen() throws {
        for duringReadback in [false, true] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            HistoryLimitFixture.save(f, value: "600")
            if duringReadback { helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id)) }
            f.model.stopHelper()
            helper.stopped()
            XCTAssertNil(f.model.historyLimit.saved)
            XCTAssertEqual(f.model.historyLimit.draft, "600")
            f.model.openProduct()
            let reopened = try f.ready()
            XCTAssertEqual(f.model.historyLimit.saved, 100)
            XCTAssertEqual(f.model.historyLimit.draft, "600")
            XCTAssertTrue(reopened.configurationSaves.isEmpty)
            XCTAssertTrue(reopened.translations.isEmpty)
            XCTAssertTrue(reopened.historyLoads.isEmpty)
        }
    }

    @MainActor
    func testConfigReadbackPreservesCursorOnlyWithinSessionAndReopenRequiresFreshRevision() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        let entry = ProductTestHarness.historyEntry(input: "Saved original", output: "Saved result")
        let firstPage = ProductTestHarness.historyPage(entries: [entry], total: 2, nextOffset: 1)
        let oldCursor = try XCTUnwrap(firstPage["next_cursor"])
        f.model.historySearch = "Saved"
        f.model.historyFilter = "text"
        f.model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id), payload: firstPage)
        let oldIDs = f.model.historyPage.map(\.id)

        HistoryLimitFixture.save(f, value: "600")
        try HistoryLimitFixture.finish(f, helper: helper)
        let savedConfig = try XCTUnwrap(helper.configurationSaves.last?.config)
        XCTAssertEqual(f.model.historyPage.map(\.id), oldIDs)
        XCTAssertEqual(f.model.historyTotal, 2)
        XCTAssertEqual(helper.historyLoads.count, 1)
        f.model.loadHistory(next: true)
        let oldRead = try XCTUnwrap(helper.historyLoads.last)
        XCTAssertEqual(oldRead.cursor, oldCursor, "A config save/readback does not invalidate the same session's history cursor.")

        f.model.stopHelper()
        helper.stopped()
        f.model.openProduct()
        let reopened = try f.ready(configuration: savedConfig)
        XCTAssertFalse(reopened === helper)
        XCTAssertEqual(f.model.historyLimit.saved, 600)
        XCTAssertNil(f.model.historyTotal)
        XCTAssertFalse(f.model.hasNextHistoryPage)
        XCTAssertTrue(f.model.historyPage.isEmpty)
        XCTAssertTrue(reopened.historyLoads.isEmpty)
        XCTAssertEqual(f.model.historySearch, "Saved")
        XCTAssertEqual(f.model.historyFilter, "text")
        f.model.loadHistory(next: true)
        XCTAssertTrue(reopened.historyLoads.isEmpty, "Never submit a previous helper's cursor.")
        f.model.loadHistory()
        let newRead = try XCTUnwrap(reopened.historyLoads.last)
        XCTAssertEqual(newRead.cursor, .null)
        XCTAssertEqual(newRead.query, "Saved")
        XCTAssertEqual(newRead.kind, "text")

        helper.event("completed", id: oldRead.id,
                     payload: ProductTestHarness.historyPage(entries: [entry], total: 2))
        helper.failure(.invalidTransition)
        XCTAssertTrue(f.model.ready, "Retired-connection notices cannot fail the new connection.")
        XCTAssertTrue(f.model.historyBusy)
        XCTAssertTrue(f.model.historyPage.isEmpty)
        XCTAssertNil(f.model.historyTotal)

        // The fake models a new session revision with unchanged records; Python owns the disk-byte proof.
        let newRevision = String(repeating: "b", count: 64)
        reopened.event("completed", id: newRead.id,
                       payload: ProductTestHarness.historyPage(entries: [entry], total: 2,
                                                              revision: newRevision, nextOffset: 1))
        XCTAssertEqual(f.model.historyPage.first?.input, "Saved original")
        XCTAssertEqual(f.model.historyPage.first?.output, "Saved result")
        XCTAssertNotEqual(f.model.historyPage.map(\.id), oldIDs)
        f.model.loadHistory(next: true)
        let newCursor = try XCTUnwrap(reopened.historyLoads.last?.cursor)
        XCTAssertEqual(newCursor.object?["revision"], .string(newRevision))
        XCTAssertNotEqual(newCursor, oldCursor)
        XCTAssertTrue(reopened.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(reopened.historyClears.isEmpty)
    }

    @MainActor
    func testConfigReadErrorCannotBeBypassedByApplyingOrConfirmingALimit() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.openProduct()
        let helper = try XCTUnwrap(f.helpers.last)
        helper.event("ready")
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("invalid_config")])
        f.model.editHistoryLimit("100")
        f.model.applyHistoryLimit()
        f.model.confirmHistoryLimitReduction()
        XCTAssertNil(f.model.historyLimit.saved)
        XCTAssertFalse(f.model.settingsReady)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationLoads.count, 1)
    }
}
