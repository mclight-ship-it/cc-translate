import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class DictionarySearchTests: XCTestCase {
    @MainActor
    func testStandaloneLookupUsesLocalConnectionWithoutChangingTranslationOrHistory() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.input = "Keep this unsent translation draft."
        let search = model.dictionarySearch
        search.query = "  example  "
        search.search(language: "en_US")
        let request = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(request.request, .lookup(text: "example", appLanguage: "en_US", origin: "text",
                                               useCache: true, recordHistory: false))
        helper.event("completed", id: request.id, payload: try DictionarySourcesFixture.hit())
        XCTAssertEqual(search.phase, .hit)
        XCTAssertEqual(search.output, DictionaryModelTests.senses)
        XCTAssertEqual(search.sources.count, 2)
        XCTAssertFalse(search.busy)
        XCTAssertEqual(model.input, "Keep this unsent translation draft.")
        XCTAssertEqual(model.output, "")
        XCTAssertEqual(model.productPhase, .idle)
        XCTAssertEqual(helper.operations.filter { $0 == "start.configuration" }.count, 1)
        XCTAssertFalse(helper.operations.contains("start.translation"))
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(model.copyText(search.output))
        XCTAssertEqual(fixture.copiedText, [DictionaryModelTests.senses])
    }

    @MainActor
    func testAllLocalMissStatesNeverUpgradeToAI() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let search = fixture.model.dictionarySearch
        let cases: [(String, DictionarySearchModel.Phase)] = [
            ("miss", .miss), ("disabled", .disabled), ("unavailable", .unavailable), ("ineligible", .ineligible)
        ]
        for (status, expected) in cases {
            search.query = "example"
            search.search(language: "zh_CN")
            helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                         payload: ["status": .string(status), "result": .null])
            XCTAssertEqual(search.phase, expected)
            XCTAssertFalse(search.busy)
            XCTAssertEqual(search.output, "")
            XCTAssertTrue(search.sources.isEmpty)
        }
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(helper.operations.contains("start.translation"))
    }

    @MainActor
    func testMalformedAndFailedTerminalsAreVisibleAndDoNotRequestAI() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let search = fixture.model.dictionarySearch
        search.query = "example"
        search.search(language: "en_US")
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ["status": .string("hit"), "result": .null])
        XCTAssertEqual(search.phase, .failed("invalid_dictionary_response"))
        search.search(language: "en_US")
        helper.event("failed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ["code": .string("dictionary_io_failed")])
        if case .failed = search.phase {} else { XCTFail("A failed lookup must not look like a miss.") }
        XCTAssertFalse(search.busy)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testConnectionLossRejectsLateTerminalAndAllowsExplicitRetry() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let search = fixture.model.dictionarySearch
        search.query = "example"
        search.search(language: "en_US")
        let oldID = try XCTUnwrap(helper.dictionaryRequests.last?.id)
        helper.stopped()
        XCTAssertEqual(search.phase, .failed("connection_lost"))
        XCTAssertFalse(search.busy)
        helper.event("completed", id: oldID, payload: DictionaryModelTests.hit())
        XCTAssertEqual(search.phase, .failed("connection_lost"))
        XCTAssertEqual(search.output, "")
        let retryHelper = try fixture.localReady()
        search.search(language: "en_US")
        retryHelper.event("completed", id: try XCTUnwrap(retryHelper.dictionaryRequests.last?.id),
                          payload: DictionaryModelTests.hit(history: "disabled"))
        XCTAssertEqual(search.phase, .hit)
        XCTAssertTrue(retryHelper.translations.isEmpty)
    }

    @MainActor
    func testBlankInputAndDisconnectedSearchShowActionableState() {
        let search = DictionarySearchModel()
        search.query = " \n "
        search.search(language: "en_US")
        XCTAssertEqual(search.phase, .ineligible)
        XCTAssertFalse(search.busy)
        search.query = "example"
        search.search(language: "en_US")
        XCTAssertEqual(search.phase, .failed("not_connected"))
        XCTAssertFalse(search.busy)
    }

    @MainActor
    func testOversizedLookupStaysLocalAndAcceptsTheExactUTF8TransportBoundary() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.reuseHistory(.init(id: "retained", input: "Retained draft.", output: "Retained result."))
        let search = model.dictionarySearch
        let limit = TranslationDocument.maxInputBytes
        let originalRequestCount = helper.dictionaryRequests.count
        let originalStopCount = helper.stopCount
        for query in [String(repeating: "a", count: limit + 1),
                      String(repeating: "你", count: limit / 3 + 1)] {
            search.query = query
            search.search(language: "en_US")
            XCTAssertEqual(search.phase, .ineligible)
            XCTAssertEqual(search.query, query, "Do not truncate the user's query.")
            XCTAssertFalse(search.busy)
            XCTAssertEqual(helper.dictionaryRequests.count, originalRequestCount)
        }
        for query in [String(repeating: "a", count: limit),
                      String(repeating: "你", count: limit / 3) + "aa"] {
            search.query = "  \(query) \n"
            search.search(language: "zh_CN")
            let request = try XCTUnwrap(helper.dictionaryRequests.last)
            XCTAssertEqual(request.request, .lookup(text: query, appLanguage: "zh_CN", origin: "text",
                                                   useCache: true, recordHistory: false))
            helper.event("completed", id: request.id, payload: ["status": .string("ineligible"), "result": .null])
            XCTAssertFalse(search.busy)
        }
        XCTAssertEqual(helper.stopCount, originalStopCount)
        XCTAssertEqual(model.input, "Retained draft.")
        XCTAssertEqual(model.output, "Retained result.")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(model.ready)
        XCTAssertTrue(model.settingsReady)
    }

    @MainActor
    func testSynchronousReplyAndBusyEditsKeepSubmittedQueryImmutable() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let search = fixture.model.dictionarySearch
        search.query = "example"
        search.search(language: "en_US")
        let request = try XCTUnwrap(helper.dictionaryRequests.last)
        search.query = "another"
        search.search(language: "en_US")
        XCTAssertEqual(helper.dictionaryRequests.last?.id, request.id)
        XCTAssertEqual(search.submittedQuery, "example")
        helper.event("completed", id: request.id, payload: ["status": .string("miss"), "result": .null])
        XCTAssertEqual(search.phase, .miss)
        helper.automaticDictionaryReplies = true
        search.search(language: "en_US")
        XCTAssertEqual(search.submittedQuery, "another")
        XCTAssertEqual(search.phase, .disabled)
        XCTAssertFalse(search.busy)
    }
}
