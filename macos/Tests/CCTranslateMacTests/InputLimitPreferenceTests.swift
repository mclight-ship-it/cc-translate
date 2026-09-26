import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum InputLimitFixture {
    static func save(_ f: ProductTestHarness, _ value: String) {
        f.model.editInputLimit(value)
        f.model.applyInputLimit()
    }

    static func finish(_ f: ProductTestHarness, _ helper: ProductTestHelper) throws {
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.inputLimit.phase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: save.config)
    }
}

final class InputLimitPreferenceTests: XCTestCase {
    @MainActor
    func testConstructionAndUnavailableActionsDoNotStartServicesOrInventLimit() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        XCTAssertNil(f.model.inputLimit.saved)
        XCTAssertEqual(f.model.inputLimit.draft, "")
        XCTAssertFalse(f.model.canEditInputLimit)
        XCTAssertFalse(f.model.canReloadInputLimit)
        InputLimitFixture.save(f, "20001")
        f.model.reloadInputLimit()
        XCTAssertEqual(f.model.inputLimit.phase, .failed("settings_unavailable"))
        XCTAssertEqual(f.model.inputLimit.draft, "20001")
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testLoadedDefaultLegacyAndInvalidValuesStayExactAndExplicitlyCorrectable() throws {
        for value in [Int64(5000), 1, 20_000, 20_001, ConfigurationDocument.maxNumber, 0, -7] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: value))
            XCTAssertEqual(f.model.inputLimit.saved, value)
            XCTAssertEqual(f.model.inputLimit.draft, String(value))
            XCTAssertTrue(f.model.canEditInputLimit)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            if value <= 0 {
                XCTAssertEqual(f.model.inputIssue(for: "a"), .invalidLimit(value))
                InputLimitFixture.save(f, "1")
                try InputLimitFixture.finish(f, helper)
                XCTAssertEqual(f.model.inputLimit.saved, 1)
                XCTAssertNil(f.model.inputIssue(for: "a"))
            }
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testInvalidDraftsNeverSaveAndOnlyWireSafePositiveRangeIsUsed() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for value in ["", "0", "-1", "1.5", "abc", "9007199254740992", "99999999999999999999999999"] {
            InputLimitFixture.save(f, value)
            XCTAssertEqual(f.model.inputLimit.phase, .invalidInput)
            XCTAssertEqual(f.model.inputLimit.draft, value)
            XCTAssertEqual(f.model.inputLimit.saved, 5000)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
        for value in [Int64(1), 20_000, 20_001, ConfigurationDocument.maxNumber] {
            InputLimitFixture.save(f, String(value))
            try InputLimitFixture.finish(f, helper)
            XCTAssertEqual(f.model.inputLimit.saved, value)
            XCTAssertEqual(f.model.inputLimit.draft, String(value))
        }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
    }

    @MainActor
    func testOfflineApplyWritesOnlyMaxCharsNeedsReadbackAndSurvivesReopen() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        var config = ProductTestHarness.configuration(history: false)
        config["font_size"] = .integer(12)
        config["future_setting"] = .string("Preserve")
        let helper = try f.ready(configuration: config)
        f.model.direction = "to_en"
        f.model.modelProfile = "auto"
        f.model.input = "Unsubmitted input"
        let initialOperations = helper.operations
        f.model.editInputLimit("20001")
        XCTAssertEqual(helper.operations, initialOperations)
        f.model.applyInputLimit()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        config["max_chars"] = .integer(20_001)
        XCTAssertEqual(save.config, config)
        XCTAssertEqual(f.model.inputLimit.saved, 5000)
        helper.event("accepted", id: save.id)
        XCTAssertEqual(helper.configurationLoads.count, 1)
        helper.event("completed", id: save.id)
        XCTAssertEqual(f.model.inputLimit.saved, 5000)
        XCTAssertEqual(f.model.inputLimit.phase, .readingBack)
        try f.finishConfiguration(on: helper, configuration: config)
        XCTAssertEqual(f.model.inputLimit.saved, 20_001)
        XCTAssertEqual(f.model.inputLimit.phase, .saved)
        XCTAssertEqual(f.model.input, "Unsubmitted input")
        XCTAssertEqual(f.model.direction, "to_en")
        XCTAssertEqual(f.model.modelProfile, "auto")
        XCTAssertTrue(f.model.needsCLI)
        XCTAssertFalse(f.model.nativeTranslation)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.messages.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)

        f.model.stopHelper()
        helper.stopped()
        XCTAssertNil(f.model.inputLimit.saved)
        f.model.openProduct()
        let reopened = try f.ready(configuration: config)
        XCTAssertEqual(f.model.inputLimit.saved, 20_001)
        XCTAssertEqual(f.model.inputLimit.draft, "20001")
        XCTAssertTrue(reopened.configurationSaves.isEmpty)
        XCTAssertTrue(reopened.translations.isEmpty)
    }

    @MainActor
    func testFailedSaveAndReadbackRequireExplicitReadOnlyRecoveryWithoutReplay() throws {
        for failReadback in [false, true] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            InputLimitFixture.save(f, "17")
            var failedID = try XCTUnwrap(helper.configurationSaves.last?.id)
            if failReadback {
                helper.event("completed", id: failedID)
                failedID = try XCTUnwrap(helper.configurationLoads.last)
            }
            helper.event("failed", id: failedID, payload: ["code": .string("config_io_failed")])
            XCTAssertNil(f.model.inputLimit.saved)
            XCTAssertEqual(f.model.inputLimit.draft, "17")
            XCTAssertEqual(f.model.inputLimit.phase, .failed("config_io_failed"))
            XCTAssertFalse(f.model.canEditInputLimit)
            let reads = helper.configurationLoads.count
            f.model.reloadInputLimit()
            XCTAssertEqual(helper.configurationLoads.count, reads + 1)
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(maxChars: 600))
            XCTAssertEqual(f.model.inputLimit.saved, 600)
            XCTAssertEqual(f.model.inputLimit.draft, "17")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testWrongTypedOrMissingLimitIsUnknownAndMismatchDoesNotPretendSuccess() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for field in [JSONValue.string("5000"), .null] {
            var config = ProductTestHarness.configuration()
            if field == .null { config.removeValue(forKey: "max_chars") }
            else { config["max_chars"] = field }
            f.model.loadSettings()
            try f.finishConfiguration(on: helper, configuration: config)
            XCTAssertNil(f.model.inputLimit.saved)
            XCTAssertEqual(f.model.inputLimit.phase, .failed("invalid_max_chars"))
            XCTAssertEqual(f.model.inputIssue(for: "a"), .unconfirmedLimit)
            f.model.input = "a"
            f.model.translate()
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        }
        f.model.reloadInputLimit()
        try f.finishConfiguration(on: helper)
        InputLimitFixture.save(f, "17")
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(maxChars: 19))
        XCTAssertEqual(f.model.inputLimit.saved, 19)
        XCTAssertEqual(f.model.inputLimit.draft, "17")
        XCTAssertEqual(f.model.inputLimit.phase, .differentReadback)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testLaterDraftAndRetiredConnectionEventsCannotOverwriteConfirmedReopen() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        InputLimitFixture.save(f, "17")
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        let oldRead = try XCTUnwrap(helper.configurationLoads.last)
        f.model.editInputLimit("23")
        try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(maxChars: 17))
        XCTAssertEqual(f.model.inputLimit.draft, "23")
        XCTAssertEqual(f.model.inputLimit.saved, 17)
        f.model.stopHelper()
        helper.stopped()
        f.model.openProduct()
        let reopened = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 20_001))
        helper.event("completed", id: oldRead, payload: [
            "config": .object(ProductTestHarness.configuration(maxChars: 1))
        ])
        helper.failure(.invalidTransition)
        XCTAssertEqual(f.model.inputLimit.saved, 20_001)
        XCTAssertEqual(f.model.inputLimit.draft, "23")
        XCTAssertTrue(f.model.ready)
        XCTAssertTrue(reopened.configurationSaves.isEmpty)
    }

    @MainActor
    func testSettingDuringAcceptedRequestNeverCancelsReplaysOrRewritesResultOrHistory() throws {
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
            f.model.input = "Request submitted under the previous preference"
            f.model.translate(useCache: false)
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("accepted", id: request.id)
            let intent = f.model.translationIntentID
            InputLimitFixture.save(f, "1")
            if failSave {
                helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                             payload: ["code": .string("config_io_failed")])
            } else {
                try InputLimitFixture.finish(f, helper)
            }
            XCTAssertTrue(f.model.active)
            XCTAssertEqual(f.model.translationIntentID, intent)
            XCTAssertEqual(f.model.productPhase, .translating)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertTrue(helper.messages.isEmpty, "No cancellation; accepted is not proof of a backend config capture.")
            helper.event("completed", id: request.id,
                         payload: SummaryPreferenceFixture.completed("Original response"))
            XCTAssertEqual(f.model.output, "Original response")
            XCTAssertEqual(f.model.resultInput, request.text)
            XCTAssertEqual(f.model.historyPage.first?.id, row.id)
            XCTAssertEqual(f.model.historyPage.first?.output, "Saved output")
            XCTAssertEqual(helper.historyLoads.count, 1)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertEqual(helper.translations.count, 1)
        }
    }
}
