import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class HistorySearchModelTests: XCTestCase {
    private let revision = String(repeating: "a", count: 64)
    private var entry: JSONValue {
        ProductTestHarness.historyEntry(input: "Previously unloaded source", output: "Remote matching result")
    }
    private var cleared: [String: JSONValue] {
        ["cleared": .bool(true), "revision": .string(String(repeating: "b", count: 64))]
    }

    @MainActor
    private func complete(_ helper: ProductTestHelper, entries: [JSONValue], total: Int,
                          nextOffset: Int? = nil) throws {
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: entries, total: total, nextOffset: nextOffset))
    }

    @MainActor
    func testInitialHistoryBindingsDoNotLocateOrStartHelperUntilExplicitSubmission() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        let query = "  中文\tStraße  2026-01-01\n"
        model.historySearch = "initial seed"
        model.historyFilter = "ocr"
        model.historySearch = query
        model.historyFilter = "dict"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeRequests, 0)
        XCTAssertEqual(fixture.locatorRequests, 0)
        XCTAssertFalse(model.connected)
        XCTAssertFalse(model.historyBusy)
        XCTAssertEqual(model.historyPhase, .idle)
        XCTAssertNil(model.historyTotal)
        model.submitHistorySearch()
        let helper = try fixture.ready()
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(helper.historyLoads.last?.query, query)
        XCTAssertEqual(helper.historyLoads.last?.kind, "dict")
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testSeedingHistoryOnAnOpenHelperPerformsNoBusinessIOUntilExplicitOpen() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        let operations = helper.operations
        model.historySearch = "rendering seed"
        model.historyFilter = "dict"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.operations, operations)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertEqual(model.historyPhase, .idle)
        model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(helper.historyLoads.last?.query, "rendering seed")
        XCTAssertEqual(helper.historyLoads.last?.kind, "dict")
    }

    @MainActor
    func testClosedHistoryIgnoresLateBindingUpdatesUntilExplicitReopen() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        try complete(helper, entries: [entry], total: 2, nextOffset: 1)
        model.closeHistorySearch()
        model.historySearch = "late binding value"
        model.historyFilter = "ocr"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(model.historyPhase, .idle)
        XCTAssertFalse(model.historyBusy)
        XCTAssertFalse(model.hasNextHistoryPage)
        model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "late binding value")
        XCTAssertEqual(helper.historyLoads.last?.kind, "ocr")
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertTrue(model.historyPage.isEmpty)
    }

    @MainActor
    func testTypingDebouncesAndReturnFlushesOnlyLatestIntent() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        try complete(helper, entries: [], total: 0)
        model.historySearch = "r"
        model.historySearch = "remote"
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(model.historyPhase, .waiting)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "remote")
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.count, 2, "Return during this same read is coalesced.")
        try complete(helper, entries: [entry], total: 1)
        model.historySearch = "instant"
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.count, 3)
        XCTAssertEqual(helper.historyLoads.last?.query, "instant")
        try complete(helper, entries: [], total: 0)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 3, "The flushed timer must not submit a duplicate.")
        XCTAssertFalse(model.nativeTranslation)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testQueryAndTypeChangesDuringReadIgnoreOldResultsAndSendLatestOnce() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let old = try XCTUnwrap(helper.historyLoads.last)
        model.historySearch = "earlier"
        model.historySearch = "latest"
        model.historyFilter = "dict"
        model.historyFilter = "ocr"
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.count, 1)
        helper.event("accepted", id: old.id, payload: ["operation": .string("history_load")])
        helper.event("started", id: old.id, payload: ["operation": .string("history_load")])
        XCTAssertEqual(helper.historyLoads.count, 1, "Only a terminal can release the serialization barrier.")
        helper.event("completed", id: old.id, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
        XCTAssertEqual(helper.historyLoads.count, 2)
        let latest = try XCTUnwrap(helper.historyLoads.last)
        XCTAssertEqual(latest.query, "latest")
        XCTAssertEqual(latest.kind, "ocr")
        XCTAssertEqual(latest.cursor, .null)
        XCTAssertTrue(model.historyPage.isEmpty)
        XCTAssertNil(model.historyTotal)
        XCTAssertTrue(model.historyBusy)
        XCTAssertEqual(model.historyPhase, .loading)
        try complete(helper, entries: [
            ProductTestHarness.historyEntry(input: "Only latest intent", output: "Returned by helper", kind: "ocr")
        ], total: 1)
        helper.event("completed", id: old.id, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
        XCTAssertEqual(model.historyPage.map(\.input), ["Only latest intent"])
        XCTAssertEqual(helper.historyLoads.count, 2)
    }

    @MainActor
    func testOldTerminalDoesNotBypassPendingTextDebounce() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        model.historySearch = "still typing"
        try complete(helper, entries: [entry], total: 1)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertFalse(model.historyBusy)
        XCTAssertEqual(model.historyPhase, .waiting)
        XCTAssertTrue(model.historyPage.isEmpty)
        model.historySearch = "finished typing"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "finished typing")
    }

    @MainActor
    func testObsoleteFailureOrCancellationCannotOverwriteNewExplicitSearch() throws {
        for terminal in ["failed", "cancelled"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            let model = try XCTUnwrap(fixture.model)
            model.loadHistory()
            let old = try XCTUnwrap(helper.historyLoads.last?.id)
            model.historySearch = "new intent"
            model.submitHistorySearch()
            helper.event(terminal, id: old, payload: terminal == "failed" ? ["code": .string("history_io_failed")] : [:])
            XCTAssertEqual(helper.historyLoads.count, 2)
            XCTAssertEqual(helper.historyLoads.last?.query, "new intent")
            XCTAssertEqual(model.historyPhase, .loading)
            XCTAssertFalse(model.historyStatus.contains("failed"))
            try complete(helper, entries: [entry], total: 1)
            XCTAssertEqual(model.historyTotal, 1)
            XCTAssertEqual(model.historyPhase, .loaded)
        }
    }

    @MainActor
    func testLoadMoreKeepsCriteriaAndStableIDsButNewSearchDropsCursor() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.historySearch = "full library"
        model.historyFilter = "text"
        model.submitHistorySearch()
        try complete(helper, entries: [entry], total: 3, nextOffset: 1)
        let firstID = try XCTUnwrap(model.historyPage.first?.id)
        model.loadHistory(next: true)
        let page = try XCTUnwrap(helper.historyLoads.last)
        XCTAssertEqual(page.query, "full library")
        XCTAssertEqual(page.kind, "text")
        XCTAssertEqual(page.cursor, .object(["revision": .string(revision), "offset": .integer(1)]))
        try complete(helper, entries: [entry, entry], total: 3)
        XCTAssertEqual(model.historyPage.first?.id, firstID)
        XCTAssertEqual(Set(model.historyPage.map(\.id)).count, 3)
        XCTAssertEqual(model.historyTotal, 3)
        XCTAssertFalse(model.hasNextHistoryPage)
        model.loadHistory()
        XCTAssertEqual(model.historyPage.first?.id, firstID, "Same-condition refresh retains the selectable snapshot.")
        try complete(helper, entries: [entry], total: 3, nextOffset: 1)
        XCTAssertEqual(model.historyPage.first?.id, firstID)
        model.historySearch = "different"
        XCTAssertFalse(model.hasNextHistoryPage)
        model.loadHistory(next: true)
        XCTAssertEqual(helper.historyLoads.count, 3)
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertEqual(helper.historyLoads.last?.query, "different")
        XCTAssertTrue(model.historyPage.isEmpty)
    }

    @MainActor
    func testExpiredCursorRequiresExplicitFirstPageRefreshWithoutRetry() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.historySearch = "saved"
        model.submitHistorySearch()
        try complete(helper, entries: [entry], total: 4, nextOffset: 1)
        model.loadHistory(next: true)
        helper.event("failed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ["code": .string("history_cursor_expired")])
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertNil(model.historyTotal)
        XCTAssertFalse(model.hasNextHistoryPage)
        XCTAssertEqual(model.historyPage.count, 1, "The previous selectable snapshot remains available.")
        XCTAssertTrue(model.historyStatus.contains("expired"))
        try await Task.sleep(nanoseconds: 400_000_000)
        model.loadHistory(next: true)
        XCTAssertEqual(helper.historyLoads.count, 2)
        model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 3)
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertEqual(helper.historyLoads.last?.query, "saved")
    }

    @MainActor
    func testReturnDuringPaginationQueuesFreshFirstPageInsteadOfReusingCursor() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        try complete(helper, entries: [entry], total: 2, nextOffset: 1)
        model.loadHistory(next: true)
        let oldPage = try XCTUnwrap(helper.historyLoads.last?.id)
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.count, 2)
        helper.event("completed", id: oldPage,
                     payload: ProductTestHarness.historyPage(entries: [entry], total: 2))
        XCTAssertEqual(helper.historyLoads.count, 3)
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertEqual(model.historyPage.count, 1, "The superseded page is not appended.")
        try complete(helper, entries: [], total: 0)
        XCTAssertTrue(model.historyPage.isEmpty)
        XCTAssertEqual(model.historyTotal, 0)
    }

    @MainActor
    func testRawChineseUnicodeDateQueryAndFilteredTotalAreOwnedByServer() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.interfaceLanguage = "zh"
        let query = "  中文\tStraße  2026-01-01\n"
        model.historySearch = query
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.last?.query, query)
        try complete(helper, entries: [
            ProductTestHarness.historyEntry(input: "中文 strasse 2026-01-01", output: "Previously unloaded Unicode match")
        ], total: 83, nextOffset: 1)
        XCTAssertEqual(model.filteredHistory.count, 1, "Never second-guess server Unicode normalization with a local substring filter.")
        XCTAssertEqual(model.historyTotal, 83)
        XCTAssertTrue(model.historyStatus.contains("83"))
        XCTAssertTrue(model.historyStatus.contains("匹配记录"))
        XCTAssertTrue(model.hasNextHistoryPage)
        model.historySearch = "2026-01-01"
        model.submitHistorySearch()
        try complete(helper, entries: [entry], total: 1)
        XCTAssertEqual(model.filteredHistory.count, 1, "A timestamp match need not appear in the input or output.")
        XCTAssertEqual(helper.historyLoads.last?.query, "2026-01-01")
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testQueryUTF8LimitAndUnsupportedTypeAreRejectedWithoutSubmission() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.historySearch = String(repeating: "字", count: 8_001)
        model.submitHistorySearch()
        XCTAssertEqual(model.historyPhase, .failed)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        model.historySearch = String(repeating: "字", count: 8_000)
        model.submitHistorySearch()
        XCTAssertEqual(helper.historyLoads.last?.query.utf8.count, 24_000)
        try complete(helper, entries: [], total: 0)
        model.historyFilter = "mixed"
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(ProbeModel.historyKinds, ["text", "dict", "code", "ocr"])
    }

    @MainActor
    func testMalformedPageRejectsWholeResponseWithoutSuccessDefaults() throws {
        var missingTotal = ProductTestHarness.historyPage(entries: [entry], total: 1)
        missingTotal.removeValue(forKey: "total")
        var invalidRevision = ProductTestHarness.historyPage(entries: [entry], total: 1)
        invalidRevision["revision"] = .string("legacy-fixture")
        var invalidCursor = ProductTestHarness.historyPage(entries: [entry], total: 3, nextOffset: 1)
        invalidCursor["next_cursor"] = .object(["offset": .integer(1)])
        let invalidPages = [
            missingTotal, invalidRevision, invalidCursor,
            ProductTestHarness.historyPage(entries: [entry], total: 2),
            ProductTestHarness.historyPage(entries: [entry, .object(["input": .integer(1)])], total: 2)
        ]
        for page in invalidPages {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.loadHistory()
            helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id), payload: page)
            XCTAssertEqual(fixture.model.historyPhase, .failed)
            XCTAssertTrue(fixture.model.historyPage.isEmpty)
            XCTAssertNil(fixture.model.historyTotal)
            XCTAssertEqual(helper.historyLoads.count, 1)
        }
    }

    @MainActor
    func testAppendCannotAcceptDifferentRevisionOrPartiallyReplaceSnapshot() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.loadHistory()
        try complete(helper, entries: [entry], total: 2, nextOffset: 1)
        let id = fixture.model.historyPage.first?.id
        fixture.model.loadHistory(next: true)
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [entry], total: 2,
                                                              revision: String(repeating: "b", count: 64)))
        XCTAssertEqual(fixture.model.historyPhase, .failed)
        XCTAssertEqual(fixture.model.historyPage.map(\.id), [try XCTUnwrap(id)])
        XCTAssertFalse(fixture.model.hasNextHistoryPage)
        XCTAssertEqual(helper.historyLoads.count, 2)
    }

    @MainActor
    func testClearCancelsDelayAndLatestQueryWaitsForClearTerminal() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        try complete(helper, entries: [], total: 0)
        model.historySearch = "obsolete debounce"
        model.clearHistory()
        let clearID = try XCTUnwrap(helper.historyClears.last)
        XCTAssertEqual(model.historyPhase, .clearing)
        XCTAssertEqual(helper.historyLoads.count, 1)
        model.historySearch = "newer"
        model.historyFilter = "ocr"
        helper.event("accepted", id: clearID, payload: ["operation": .string("history_clear")])
        helper.event("started", id: clearID, payload: ["operation": .string("history_clear")])
        XCTAssertEqual(helper.historyLoads.count, 1)
        helper.event("completed", id: clearID, payload: cleared)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "newer")
        XCTAssertEqual(helper.historyLoads.last?.kind, "ocr")
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertNil(model.historyTotal, "The old clear must not publish a zero count for the new read.")
        try complete(helper, entries: [
            ProductTestHarness.historyEntry(input: "newer record", output: "OCR result", kind: "ocr")
        ], total: 7, nextOffset: 1)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyClears.count, 1)
        XCTAssertEqual(model.historyTotal, 7)
    }

    @MainActor
    func testFailedOrMalformedClearDoesNotReplayWriteOrQueuedSearch() async throws {
        for malformed in [false, true] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            let model = try XCTUnwrap(fixture.model)
            model.clearHistory()
            model.historySearch = "pending"
            helper.event(malformed ? "completed" : "failed", id: try XCTUnwrap(helper.historyClears.last),
                         payload: malformed ? [:] : ["code": .string("history_io_failed")])
            try await Task.sleep(nanoseconds: 400_000_000)
            XCTAssertEqual(model.historyPhase, .failed)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertEqual(helper.historyClears.count, 1)
            XCTAssertNil(model.historyTotal)
            model.loadHistory()
            XCTAssertEqual(helper.historyLoads.count, 1)
            XCTAssertEqual(helper.historyLoads.last?.query, "pending")
        }
    }

    @MainActor
    func testCloseIsolatesOldResponseAndReopeningWaitsWithoutParallelRead() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let old = try XCTUnwrap(helper.historyLoads.last?.id)
        model.historySearch = "latest before close"
        model.closeHistorySearch()
        XCTAssertTrue(model.historyBusy, "Closing is not a terminal acknowledgment from the helper.")
        model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 1)
        helper.event("completed", id: old, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
        XCTAssertTrue(model.historyPage.isEmpty)
        XCTAssertEqual(helper.historyLoads.count, 2)
        XCTAssertEqual(helper.historyLoads.last?.query, "latest before close")
        try complete(helper, entries: [], total: 0)
        XCTAssertEqual(model.historyPhase, .loaded)
    }

    @MainActor
    func testCloseQuitAndStopDiscardDebounceAndLateResults() async throws {
        for action in ["window", "panel", "quit", "stop"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            let model = try XCTUnwrap(fixture.model)
            model.loadHistory()
            let old = try XCTUnwrap(helper.historyLoads.last?.id)
            model.historySearch = "never send"
            switch action {
            case "window": model.closeHistorySearch()
            case "panel": model.closePanel()
            case "quit": model.prepareToQuit()
            default: model.stopHelper()
            }
            helper.event("completed", id: old, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
            try await Task.sleep(nanoseconds: 400_000_000)
            XCTAssertEqual(helper.historyLoads.count, 1)
            XCTAssertTrue(model.historyPage.isEmpty)
            XCTAssertNil(model.historyTotal)
            XCTAssertFalse(model.historyBusy)
        }
    }

    @MainActor
    func testConnectionFailureDropsDelayedIntentAndRequiresExplicitRefreshAfterReconnect() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let old = try XCTUnwrap(helper.historyLoads.last?.id)
        model.historySearch = "not replayed"
        helper.failure(.helperEOF)
        helper.stopped()
        helper.event("completed", id: old, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertTrue(model.historyPage.isEmpty)
        model.historySearch = "new seed after disconnect"
        model.historyFilter = "dict"
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertEqual(helper.historyLoads.count, 1)
        model.openProduct()
        let replacement = try XCTUnwrap(fixture.helpers.last)
        XCTAssertFalse(replacement === helper)
        replacement.event("ready")
        try fixture.finishConfiguration(on: replacement)
        XCTAssertTrue(replacement.historyLoads.isEmpty)
        model.loadHistory()
        XCTAssertEqual(replacement.historyLoads.last?.query, "new seed after disconnect")
        XCTAssertEqual(replacement.historyLoads.last?.kind, "dict")
        XCTAssertEqual(replacement.historyLoads.last?.cursor, .null)
    }

    @MainActor
    func testUnknownClearOutcomeNeverReplaysClearOrQueuedRead() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.clearHistory()
        fixture.model.historySearch = "queued"
        helper.failure(.historyOutcomeUnknown)
        helper.stopped()
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(fixture.model.historyPhase, .failed)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertEqual(helper.historyClears.count, 1)
        XCTAssertNil(fixture.model.historyTotal)
    }

    @MainActor
    func testUnexpectedStopRetiresPendingReadAndDelayEvenWithoutFailureNotice() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let old = try XCTUnwrap(helper.historyLoads.last?.id)
        model.historySearch = "not replayed"
        helper.stopped()
        helper.event("completed", id: old, payload: ProductTestHarness.historyPage(entries: [entry], total: 1))
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertFalse(model.historyBusy)
        XCTAssertTrue(model.historyPage.isEmpty)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertTrue(model.historyStatus.contains("closed"))
    }

    @MainActor
    func testConfigurationFailureCancelsBootstrapSearchWithoutLaterReplay() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        fixture.model.loadHistory()
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("invalid_config")])
        XCTAssertEqual(fixture.model.historyPhase, .failed)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        fixture.model.loadSettings()
        try fixture.finishConfiguration(on: helper)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        fixture.model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 1)
    }

    @MainActor
    func testExplicitHistoryRefreshRetriesFailedConfigurationReadOnlyOnDemand() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.historySearch = "saved"
        model.submitHistorySearch()
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("invalid_config")])
        XCTAssertEqual(helper.configurationLoads.count, 1)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertEqual(model.historyPhase, .failed)
        model.loadHistory()
        XCTAssertEqual(helper.configurationLoads.count, 2)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        model.submitHistorySearch()
        XCTAssertEqual(helper.configurationLoads.count, 2)
        try fixture.finishConfiguration(on: helper)
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertEqual(helper.historyLoads.last?.query, "saved")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testSearchBootstrapFromDiagnosticConnectionIsPreservedOnlyUntilFirstSubmission() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.startHelper()
        let diagnostic = try XCTUnwrap(fixture.helpers.last)
        diagnostic.event("ready")
        model.historySearch = "explicit search"
        model.submitHistorySearch()
        XCTAssertEqual(diagnostic.stopCount, 1)
        diagnostic.stopped()
        let product = try XCTUnwrap(fixture.helpers.last)
        XCTAssertFalse(product === diagnostic)
        product.event("ready")
        try fixture.finishConfiguration(on: product)
        XCTAssertEqual(product.historyLoads.count, 1)
        XCTAssertEqual(product.historyLoads.last?.query, "explicit search")
        XCTAssertEqual(product.historyLoads.last?.kind, "all")
        XCTAssertTrue(diagnostic.historyLoads.isEmpty)
    }

    @MainActor
    func testLaunchFailureEndsPendingHistoryInsteadOfLeavingSpinnerOrRetry() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = ProbeModel(preferences: fixture.preferences,
                               runtimeProvider: { throw ProbeError.launchFailed },
                               locateCandidates: { _, _ in [] })
        defer { model.closePanel() }
        model.historySearch = "query"
        model.submitHistorySearch()
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertFalse(model.historyBusy)
        XCTAssertFalse(model.connected)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(model.historyPhase, .failed)
        XCTAssertNil(model.historyTotal)
    }

    @MainActor
    func testLegacyKindsAndSourceSignaturesRemainConsistentForCopyReuseAndActions() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        let local = "**literal sense**\nSource: WordNet"
        let ai = "**Model meaning**"
        let entries: [JSONValue] = [
            .object(["kind": .string("ocr"), "is_code": .bool(true)]),
            .object(["kind": .string("unknown"), "is_code": .bool(true), "is_dict": .bool(true)]),
            .object(["kind": .null, "is_dict": .bool(true)]),
            .object(["kind": .string("mixed")]),
            .object(["kind": .string("text"), "is_dict": .bool(true)]),
            .object(["input": .string("local word"), "output": .string(local), "is_dict": .bool(true),
                     "sig": .string("local-dictionary|pinned-fixture|native-plain-v1:en_US")]),
            .object(["input": .string("AI word"), "output": .string(ai), "kind": .string("dict"),
                     "sig": .string("model-prompt-signature")])
        ]
        model.loadHistory()
        try complete(helper, entries: entries, total: entries.count)
        XCTAssertEqual(model.historyPage.map(\.kind), ["ocr", "code", "dict", "text", "text", "dict", "dict"])
        let localRow = model.historyPage[5]
        XCTAssertTrue(localRow.isLocalDictionary)
        model.copyText(localRow.output)
        model.copyText(localRow.input + "\n\n" + localRow.output)
        XCTAssertEqual(Array(fixture.copiedText.suffix(2)), [local, "local word\n\n" + local])
        model.reuseHistory(localRow)
        model.input = "Unrelated live editor text"
        model.copyResult()
        XCTAssertEqual(fixture.copiedText.last, local)
        XCTAssertTrue(model.isLocalDictionaryResult)
        model.performResultAction(.summary)
        XCTAssertEqual(helper.resultActions.last?.text, local)
        helper.event("completed", id: try XCTUnwrap(helper.resultActions.last?.id), payload: [
            "text": .string("Explicit AI section"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("disabled"), "history_error": .null
        ])
        XCTAssertEqual(model.primaryResult, local)
        XCTAssertEqual(model.resultInput, "local word")
        let aiRow = model.historyPage[6]
        XCTAssertFalse(aiRow.isLocalDictionary)
        model.reuseHistory(aiRow)
        XCTAssertFalse(model.isLocalDictionaryResult)
        XCTAssertEqual(model.primaryResult, ai)
        model.input = "Another live edit"
        model.performResultAction(.retranslate, targetLanguage: "ja")
        XCTAssertEqual(helper.resultActions.last?.text, "AI word")
        XCTAssertEqual(helper.resultActions.last?.targetLanguage, "ja")
        XCTAssertEqual(model.input, "Another live edit")
        XCTAssertTrue(helper.dictionaryRequests.isEmpty, "Explicit result actions bypass local lookup.")
        XCTAssertTrue(helper.translations.isEmpty)
    }
}
