import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class RecordingDictionaryDownloader: DictionaryDownloading {
    private(set) var tickets: [DictionaryInstallTicket] = []
    private(set) var cancellations = 0
    private var progress: ((Int64) -> Void)?
    private var completion: ((Result<Void, DictionaryDownloadError>) -> Void)?
    func start(_ ticket: DictionaryInstallTicket, progress: @escaping (Int64) -> Void,
               completion: @escaping (Result<Void, DictionaryDownloadError>) -> Void) {
        tickets.append(ticket)
        self.progress = progress
        self.completion = completion
    }
    func cancel() { cancellations += 1 }
    func advance(_ bytes: Int64) { progress?(bytes) }
    func finish(_ result: Result<Void, DictionaryDownloadError>) {
        let callback = completion
        completion = nil
        callback?(result)
    }
}

final class DictionaryModelTests: XCTestCase {
    static let senses = """
    example /ig'zampel/
    noun
    1. A representative instance.
    2. A model to imitate.
    Sources: Princeton WordNet; Chinese Open Wordnet.
    """

    static func hit(_ text: String = DictionaryModelTests.senses, history: String = "recorded") -> [String: JSONValue] {
        ["status": .string("hit"), "result": .object([
            "text": .string(text), "submitted": .bool(false), "cached": .bool(false),
            "kind": .string("dict"), "target_lang": .null, "summarize": .bool(false),
            "history": .string(history), "history_error": history == "failed" ? .string("history_io_failed") : .null
        ])]
    }

    static func ticket(path: URL) -> [String: JSONValue] {
        ["ticket": .string(String(repeating: "a", count: 32)), "path": .string(path.path),
         "url": .string("https://example.invalid/dictionary"), "size": .integer(4),
         "sha256": .string(String(repeating: "a", count: 64)), "data_version": .string("fixture-v1")]
    }

    @MainActor
    func testNoCLIHitReturnsCompleteLiteralSensesBeforeSetupAndSupportsCopyAndHistory() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        var setup = 0
        var presented = 0
        model.onConfigurationRequired = { setup += 1 }
        model.onTranslationResult = { _ in presented += 1 }
        model.input = "example"
        model.translate(origin: "selection")
        let request = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(request.request, .lookup(text: "example", appLanguage: "en_US", origin: "selection",
                                               useCache: true, recordHistory: true))
        model.input = "unsent editor change"
        helper.event("completed", id: request.id, payload: Self.hit())
        XCTAssertEqual(model.output, Self.senses)
        XCTAssertEqual(model.primaryResult, Self.senses)
        XCTAssertEqual(model.resultInput, "example")
        XCTAssertEqual(model.resultKind, "dict")
        XCTAssertTrue(model.isLocalDictionaryResult)
        XCTAssertEqual(model.input, "unsent editor change")
        XCTAssertEqual(model.productPhase, .completed)
        XCTAssertEqual(setup, 0)
        XCTAssertEqual(presented, 1)
        XCTAssertNil(helper.selectedExecutable)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertEqual(fixture.helpers.count, 1)
        model.copyResult()
        model.copyBilingual()
        XCTAssertEqual(fixture.copiedText, [Self.senses, "example\n\n" + Self.senses])
        model.loadHistory()
        var entry = try XCTUnwrap(ProductTestHarness.historyEntry(input: "example", output: Self.senses, kind: "dict").object)
        entry["sig"] = .string("local-dictionary|fixture|native-plain-v1:en_US")
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id), payload: [
            "entries": .array([.object(entry)]),
            "revision": .string("local"), "next_cursor": .null
        ])
        XCTAssertEqual(model.historyPage.first?.output, Self.senses)
        XCTAssertEqual(model.historyPage.first?.kind, "dict")
        model.reuseHistory(try XCTUnwrap(model.historyPage.first))
        XCTAssertTrue(model.isLocalDictionaryResult)
        model.reuseHistory(.init(id: "ai", input: "example", output: "**AI definition**", kind: "dict"))
        XCTAssertFalse(model.isLocalDictionaryResult, "AI dictionary formatting must not become verbatim.")
        model.clearTranslation()
        XCTAssertFalse(model.isLocalDictionaryResult)
    }

    @MainActor
    func testEachKnownMissUpgradesOnceAndPreservesInputAndClickedSettings() throws {
        for miss in ["miss", "disabled", "unavailable", "ineligible"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let local = try fixture.localReady()
            let model = try XCTUnwrap(fixture.model)
            model.direction = "to_ja"
            model.modelProfile = "auto"
            model.input = "clicked original"
            model.translate()
            let lookup = try XCTUnwrap(local.dictionaryRequests.last)
            model.input = "later edit"
            model.direction = "to_en"
            model.modelProfile = "auto-fast"
            helperMiss(local, lookup.id, miss)
            XCTAssertEqual(local.stopCount, 1)
            XCTAssertTrue(local.translations.isEmpty)
            XCTAssertTrue(local.configurationSaves.isEmpty, "Offline lookup must precede all-settings saves.")
            local.stopped()
            let provider = try fixture.ready()
            let save = try XCTUnwrap(provider.configurationSaves.last)
            XCTAssertEqual(save.config["direction"], .string("to_ja"))
            XCTAssertEqual(save.config["codex_model"], .string("auto"))
            provider.event("completed", id: save.id)
            try fixture.finishConfiguration(on: provider, configuration: save.config)
            XCTAssertEqual(provider.translations.count, 1)
            XCTAssertEqual(provider.translations.first?.text, "clicked original")
            XCTAssertEqual(model.direction, "to_en")
            XCTAssertEqual(model.modelProfile, "auto-fast")
            XCTAssertEqual(model.input, "later edit")
            XCTAssertEqual(fixture.helpers.count, 2)
            XCTAssertEqual(local.dictionaryRequests.filter { $0.request.operation == "dictionary_lookup" }.count, 1)
            XCTAssertFalse(provider.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
        }
    }

    @MainActor
    private func helperMiss(_ helper: ProductTestHelper, _ id: String, _ value: String = "miss") {
        helper.event("completed", id: id, payload: ["status": .string(value), "result": .null])
    }

    @MainActor
    func testNoCLISetupOnlyFollowsKnownMissAndNeverReplaysWhenCLIIsChosen() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        var setup = 0
        model.onConfigurationRequired = { setup += 1 }
        model.input = "example"
        model.translate()
        XCTAssertEqual(setup, 0)
        helperMiss(helper, try XCTUnwrap(helper.dictionaryRequests.last?.id))
        XCTAssertEqual(setup, 1)
        XCTAssertEqual(model.productPhase, .failed)
        fixture.canLocateCLI = true
        model.locateCLI()
        helper.stopped()
        let reopened = try fixture.ready()
        XCTAssertTrue(model.nativeTranslation)
        XCTAssertTrue(reopened.translations.isEmpty)
        XCTAssertFalse(model.preparing)
    }

    @MainActor
    func testCancellationWaitsForLookupTerminalAndNeverUpgradesEvenForLateMissOrHit() throws {
        for completion in [Self.hit(), ["status": .string("miss"), "result": .null]] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.localReady()
            fixture.model.input = "example"
            fixture.model.translate()
            let lookup = try XCTUnwrap(helper.dictionaryRequests.last)
            helper.event("started", id: lookup.id, payload: ["operation": .string("dictionary_lookup")])
            fixture.model.cancel()
            XCTAssertTrue(fixture.model.active)
            let cancellation = try XCTUnwrap(helper.messages.last)
            helper.event("completed", id: cancellation.id)
            XCTAssertTrue(fixture.model.active)
            helper.event("completed", id: lookup.id, payload: completion)
            XCTAssertFalse(fixture.model.active)
            XCTAssertEqual(fixture.model.productPhase, .cancelled)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertEqual(helper.stopCount, 0)
            XCTAssertEqual(fixture.helpers.count, 1)
        }
    }

    @MainActor
    func testFailedCancelledInvalidAndUnknownLookupAreNotMisses() throws {
        for kind in ["failed", "cancelled", "invalid", "unknown"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.localReady()
            fixture.model.input = "example"
            fixture.model.translate()
            let lookup = try XCTUnwrap(helper.dictionaryRequests.last)
            switch kind {
            case "unknown": helper.failure(.dictionaryOutcomeUnknown)
            case "invalid": helper.event("completed", id: lookup.id, payload: [:])
            default: helper.event(kind, id: lookup.id, payload: ["code": .string("dictionary_io_failed")])
            }
            XCTAssertEqual(fixture.model.productPhase, kind == "cancelled" ? .cancelled : .failed)
            XCTAssertEqual(fixture.helpers.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertFalse(fixture.model.preparing)
            if kind == "unknown" {
                XCTAssertTrue(fixture.model.productMessage.contains("Local dictionary outcome unknown"))
                XCTAssertTrue(fixture.model.productMessage.contains("No model fallback"))
                XCTAssertFalse(fixture.model.productMessage.contains("CLI may have received"))
            }
        }
    }

    @MainActor
    func testCancellationDuringUpgradePreventsSubmissionAndRetainsLaterEdits() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.input = "example"
        fixture.model.translate()
        helperMiss(helper, try XCTUnwrap(helper.dictionaryRequests.last?.id))
        fixture.model.cancel()
        fixture.model.input = "new draft"
        helper.stopped()
        let reopened = try fixture.ready()
        XCTAssertTrue(fixture.model.nativeTranslation)
        XCTAssertTrue(reopened.translations.isEmpty)
        XCTAssertEqual(fixture.model.input, "new draft")
    }

    @MainActor
    func testExplicitNoCacheAndActionsSkipLookupAndUseImmutableDictionarySource() throws {
        for action in [ResultAction.concise, .asText, .summary] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.localReady()
            fixture.model.input = "example"
            fixture.model.translate()
            helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id), payload: Self.hit())
            fixture.model.input = "unsent editor"
            fixture.model.performResultAction(action)
            XCTAssertEqual(helper.stopCount, 1)
            helper.stopped()
            let provider = try fixture.ready()
            XCTAssertEqual(provider.resultActions.count, 1)
            XCTAssertEqual(provider.resultActions.first?.text, action.usesOriginalInput ? "example" : Self.senses)
            XCTAssertEqual(fixture.model.primaryResult, Self.senses)
            XCTAssertTrue(fixture.model.output.hasPrefix(Self.senses))
            XCTAssertEqual(fixture.model.input, "unsent editor")
            XCTAssertTrue(provider.translations.isEmpty)
            XCTAssertFalse(provider.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
        }
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.input = "force model"
        fixture.model.translate(useCache: false)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        helper.stopped()
        let provider = try fixture.ready()
        XCTAssertEqual(provider.translations.count, 1)
        XCTAssertEqual(provider.translations.first?.useCache, false)
    }

    @MainActor
    func testHistoryWriteFailureStillShowsCompleteLocalHitWithoutAI() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.input = "example"
        fixture.model.translate()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: Self.hit(history: "failed"))
        XCTAssertEqual(fixture.model.output, Self.senses)
        XCTAssertTrue(fixture.model.productMessage.contains("History was not saved"))
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testDownloadIsExplicitConfigOnlyAndInstallsAfterSettledTransferWithoutSavingAllSettings() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(savedCLI: false, dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.refreshDictionary()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ProductTestHelper.dictionaryStatus())
        XCTAssertTrue(downloader.tickets.isEmpty)
        model.dictionary.download()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        downloader.advance(2)
        XCTAssertEqual(model.dictionary.received, 2)
        XCTAssertEqual(model.dictionary.phase, .downloading)
        model.loadHistory()
        XCTAssertEqual(helper.historyLoads.count, 1)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        downloader.finish(.success(()))
        let install = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(install.request, .install(ticket: String(repeating: "a", count: 32)))
        XCTAssertEqual(model.dictionary.phase, .installing)
        model.dictionary.cancel()
        XCTAssertEqual(model.dictionary.phase, .installing, "Commit cancellation cannot promise rollback.")
        helper.event("completed", id: install.id, payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        XCTAssertEqual(model.dictionary.status?.state, .ready)
        XCTAssertEqual(model.dictionary.status?.enabled, true)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertNil(helper.selectedExecutable)
    }

    @MainActor
    func testLateDownloadCompletionAfterCancelCannotInstallAndShutdownWaitsForDiscard() throws {
        for close in [false, true] {
            let downloader = RecordingDictionaryDownloader()
            let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
            defer { fixture.cleanUp() }
            let helper = try fixture.localReady()
            fixture.model.dictionary.download()
            helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                         payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
            if close { fixture.model.closePanel() } else { fixture.model.dictionary.cancel() }
            XCTAssertEqual(downloader.cancellations, 1)
            XCTAssertEqual(helper.stopCount, 0)
            XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_discard_install" })
            downloader.finish(.success(()))
            let discard = try XCTUnwrap(helper.dictionaryRequests.last)
            XCTAssertEqual(discard.request.operation, "dictionary_discard_install")
            XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
            helper.event("completed", id: discard.id, payload: ["discarded": .bool(true)])
            XCTAssertEqual(helper.stopCount, close ? 1 : 0)
        }
    }

    @MainActor
    func testUnknownInstallIsQueriedAfterReconnectAndNeverReplayed() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.dictionary.download()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        downloader.finish(.success(()))
        helper.failure(.dictionaryOutcomeUnknown)
        XCTAssertEqual(fixture.model.dictionary.phase, .unknown)
        XCTAssertTrue(fixture.model.productMessage.contains("Dictionary operation outcome unknown"))
        XCTAssertFalse(fixture.model.productMessage.contains("CLI may have received"))
        helper.stopped()
        fixture.model.refreshDictionary()
        let reopened = try fixture.ready()
        XCTAssertTrue(reopened.dictionaryRequests.contains { $0.request == .status })
        XCTAssertFalse(reopened.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
        XCTAssertTrue(reopened.translations.isEmpty)
    }

    @MainActor
    func testDictionaryToggleSavesOnlyDictionaryPreferenceAndPreservesLiveEdits() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.refreshDictionary()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        model.direction = "to_ja"
        model.modelProfile = "auto"
        model.setDictionaryEnabled(false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["local_dictionary_enabled"], .bool(false))
        XCTAssertEqual(save.config["direction"], .string("auto"))
        XCTAssertEqual(save.config["codex_model"], .string("auto-fast"))
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(model.direction, "to_ja")
        XCTAssertEqual(model.modelProfile, "auto")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request == .delete })
    }

    @MainActor
    func testDeleteIsAnExplicitSeparateOperationAndReloadsNormalizedSettings() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.refreshDictionary()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: false))
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request == .delete })
        fixture.model.dictionary.delete()
        let request = try XCTUnwrap(helper.dictionaryRequests.last)
        XCTAssertEqual(request.request, .delete)
        helper.event("completed", id: request.id, payload: ["deleted": .bool(true), "enabled": .bool(false)])
        XCTAssertEqual(helper.configurationLoads.count, 2)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testPreparingDownloadCancellationDiscardsTicketWithoutStartingTransfer() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        fixture.model.dictionary.download()
        let prepare = try XCTUnwrap(helper.dictionaryRequests.last)
        fixture.model.dictionary.cancel()
        XCTAssertTrue(fixture.model.dictionary.busy)
        helper.event("completed", id: prepare.id, payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        XCTAssertTrue(downloader.tickets.isEmpty)
        XCTAssertEqual(helper.dictionaryRequests.last?.request.operation, "dictionary_discard_install")
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id), payload: ["discarded": .bool(true)])
        XCTAssertFalse(fixture.model.dictionary.busy)
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
    }

    @MainActor
    func testInstallWaitsForSettingsWriteAndDoesNotOverwriteNewPreferences() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let helper = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.dictionary.download()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        model.direction = "to_ja"
        model.saveSettings()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        downloader.finish(.success(()))
        XCTAssertEqual(model.dictionary.phase, .waitingToInstall)
        XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(model.dictionary.phase, .installing)
        XCTAssertEqual(helper.dictionaryRequests.last?.request.operation, "dictionary_install")
        model.saveSettings(history: false)
        XCTAssertEqual(helper.configurationSaves.count, 1, "A stale settings snapshot must not race the core commit.")
    }

    @MainActor
    func testTransferCompletionAfterTransportLossCannotCommitOnReplacementConnection() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let old = try fixture.localReady()
        fixture.model.dictionary.download()
        old.event("completed", id: try XCTUnwrap(old.dictionaryRequests.last?.id),
                  payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        old.failure(.dictionaryOutcomeUnknown)
        XCTAssertEqual(downloader.cancellations, 1)
        old.stopped()
        fixture.model.openProduct()
        let replacement = try fixture.ready()
        downloader.finish(.success(()))
        XCTAssertFalse(replacement.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
        XCTAssertFalse(old.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
        XCTAssertEqual(fixture.model.dictionary.phase, .unknown)
    }

    @MainActor
    func testExistingTranslationConnectionCanReturnLocalHitWithoutProviderInvocation() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        helper.automaticDictionaryReplies = false
        fixture.model.input = "example"
        fixture.model.translate()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id), payload: Self.hit())
        XCTAssertEqual(fixture.model.output, Self.senses)
        XCTAssertTrue(fixture.model.nativeTranslation)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertEqual(helper.stopCount, 0)
    }

    @MainActor
    func testDictionaryUnknownDoesNotConcealASeparatePendingModelRequest() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        helper.automaticDictionaryReplies = false
        fixture.model.input = "separate explicit model request"
        fixture.model.translate(useCache: false)
        fixture.model.refreshDictionary()
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.dictionaryRequests.last?.request, .status)
        helper.failure(.dictionaryOutcomeUnknown)
        XCTAssertTrue(fixture.model.productMessage.contains("Dictionary operation outcome unknown"))
        XCTAssertTrue(fixture.model.productMessage.contains("separate model request may also have been submitted"))
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(fixture.model.productPhase, .failed)
    }

    @MainActor
    func testChoosingCLIDuringDownloadWaitsForInstallOrExplicitCancelAndDiscard() throws {
        for cancel in [false, true] {
            let downloader = RecordingDictionaryDownloader()
            let fixture = try ProductTestHarness(savedCLI: false, dictionaryDownloader: downloader)
            defer { fixture.cleanUp() }
            let old = try fixture.localReady()
            let model = try XCTUnwrap(fixture.model)
            model.dictionary.download()
            old.event("completed", id: try XCTUnwrap(old.dictionaryRequests.last?.id),
                      payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
            fixture.canLocateCLI = true
            model.locateCLI()
            XCTAssertTrue(model.cliChangeDeferred)
            XCTAssertEqual(old.stopCount, 0)
            XCTAssertEqual(downloader.cancellations, 0)
            XCTAssertFalse(model.nativeTranslation)

            if cancel {
                model.dictionary.cancel()
                XCTAssertEqual(downloader.cancellations, 1)
                downloader.finish(.failure(.cancelled))
                XCTAssertEqual(old.stopCount, 0)
                let discard = try XCTUnwrap(old.dictionaryRequests.last)
                XCTAssertEqual(discard.request.operation, "dictionary_discard_install")
                old.event("completed", id: discard.id, payload: ["discarded": .bool(true)])
            } else {
                downloader.finish(.success(()))
                XCTAssertEqual(downloader.cancellations, 0)
                XCTAssertEqual(old.stopCount, 0)
                let install = try XCTUnwrap(old.dictionaryRequests.last)
                XCTAssertEqual(install.request.operation, "dictionary_install")
                old.event("completed", id: install.id,
                          payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
                XCTAssertEqual(old.stopCount, 0)
                var config = ProductTestHarness.configuration()
                config["local_dictionary_enabled"] = .bool(true)
                try fixture.finishConfiguration(on: old, configuration: config)
            }
            XCTAssertEqual(old.stopCount, 1)
            XCTAssertFalse(model.cliChangeDeferred)
            old.stopped()
            let replacement = try fixture.ready()
            XCTAssertEqual(replacement.selectedExecutable, fixture.executable)
            XCTAssertTrue(model.nativeTranslation)
            XCTAssertTrue(replacement.translations.isEmpty)
            XCTAssertFalse(replacement.dictionaryRequests.contains { $0.request.operation == "dictionary_install" })
        }
    }

    @MainActor
    func testKnownMissWaitsForOwnedDownloadInsteadOfCancellingItToUpgrade() throws {
        let downloader = RecordingDictionaryDownloader()
        let fixture = try ProductTestHarness(dictionaryDownloader: downloader)
        defer { fixture.cleanUp() }
        let old = try fixture.localReady()
        let model = try XCTUnwrap(fixture.model)
        model.dictionary.download()
        old.event("completed", id: try XCTUnwrap(old.dictionaryRequests.last?.id),
                  payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
        model.input = "clicked source"
        model.translate()
        helperMiss(old, try XCTUnwrap(old.dictionaryRequests.last?.id))
        XCTAssertEqual(old.stopCount, 0)
        XCTAssertEqual(downloader.cancellations, 0)
        XCTAssertTrue(model.preparing)
        model.input = "later unsent edit"
        model.direction = "to_ja"
        downloader.finish(.success(()))
        old.event("completed", id: try XCTUnwrap(old.dictionaryRequests.last?.id),
                  payload: ProductTestHelper.dictionaryStatus(installed: true, enabled: true))
        var config = ProductTestHarness.configuration()
        config["local_dictionary_enabled"] = .bool(true)
        try fixture.finishConfiguration(on: old, configuration: config)
        XCTAssertEqual(old.stopCount, 1)
        XCTAssertEqual(downloader.cancellations, 0)
        old.stopped()
        let provider = try fixture.ready(configuration: config)
        XCTAssertEqual(provider.translations.count, 1)
        XCTAssertEqual(provider.translations.first?.text, "clicked source")
        XCTAssertEqual(model.input, "later unsent edit")
        XCTAssertEqual(model.direction, "to_ja")
        XCTAssertFalse(provider.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
    }

    @MainActor
    func testKnownInstallFailureAndCancellationRefreshWithoutDiscardingConsumedTicket() throws {
        for terminal in ["failed", "cancelled"] {
            let downloader = RecordingDictionaryDownloader()
            let fixture = try ProductTestHarness(savedCLI: false, dictionaryDownloader: downloader)
            defer { fixture.cleanUp() }
            let helper = try fixture.localReady()
            fixture.model.dictionary.download()
            helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                         payload: Self.ticket(path: fixture.root.appendingPathComponent("staged")))
            downloader.finish(.success(()))
            let install = try XCTUnwrap(helper.dictionaryRequests.last)
            helper.event("started", id: install.id, payload: ["operation": .string("dictionary_install")])
            helper.event(terminal, id: install.id,
                         payload: terminal == "failed" ? ["code": .string("dictionary_install_failed")] : [:])
            XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_discard_install" })
            XCTAssertEqual(helper.configurationLoads.count, 2)
            let message = fixture.model.dictionary.messageEnglish
            try fixture.finishConfiguration(on: helper)
            let status = try XCTUnwrap(helper.dictionaryRequests.last)
            XCTAssertEqual(status.request, .status)
            helper.event("completed", id: status.id, payload: ProductTestHelper.dictionaryStatus())
            XCTAssertEqual(fixture.model.dictionary.messageEnglish, message, "Refreshing state must not hide the terminal failure.")
            XCTAssertEqual(fixture.model.dictionary.status?.enabled, false)
            XCTAssertFalse(fixture.model.dictionary.busy)
            XCTAssertEqual(helper.dictionaryRequests.filter { $0.request.operation == "dictionary_install" }.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }
}
