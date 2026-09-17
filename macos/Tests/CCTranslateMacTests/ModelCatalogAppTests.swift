import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CatalogTestClient: AppHelperClient {
    let base: ProductTestHelper
    private(set) var catalogRequests: [String] = []
    private(set) var catalogTimeouts: [TimeInterval] = []
    var onCatalog: (@MainActor (String) -> Void)?
    var onStart: (@MainActor () -> Void)?
    var onStop: (@MainActor () -> Void)?
    init(notice: @escaping (HelperNotice) -> Void) { base = ProductTestHelper(notice: notice) }
    func start(runtime: BundleRuntime) { base.start(runtime: runtime) }
    func startConfiguration(runtime: BundleRuntime, home: URL) {
        base.startConfiguration(runtime: runtime, home: home)
        MainActor.assumeIsolated { onStart?() }
    }
    func startTranslation(runtime: BundleRuntime, home: URL, codexCommand: URL, environment: [String: String]) {
        base.startTranslation(runtime: runtime, home: home, codexCommand: codexCommand, environment: environment)
        MainActor.assumeIsolated { onStart?() }
    }
    func modelCatalog(id: String, timeout: TimeInterval) -> String {
        catalogRequests.append(id)
        catalogTimeouts.append(timeout)
        MainActor.assumeIsolated { onCatalog?(id) }
        return id
    }
    func send(_ message: ClientMessage, timeout: TimeInterval) { base.send(message, timeout: timeout) }
    func translate(text: String, appLanguage: String, origin: String, useCache: Bool,
                   recordHistory: Bool, id: String, timeout: TimeInterval) -> String {
        base.translate(text: text, appLanguage: appLanguage, origin: origin, useCache: useCache,
                       recordHistory: recordHistory, id: id, timeout: timeout)
    }
    func resultAction(_ action: ResultAction, text: String, appLanguage: String,
                      targetLanguage: String?, id: String, timeout: TimeInterval) -> String {
        base.resultAction(action, text: text, appLanguage: appLanguage, targetLanguage: targetLanguage,
                          id: id, timeout: timeout)
    }
    func dictionary(_ request: DictionaryRequest, id: String, timeout: TimeInterval) -> String {
        base.dictionary(request, id: id, timeout: timeout)
    }
    func loadConfiguration(id: String, timeout: TimeInterval) -> String { base.loadConfiguration(id: id, timeout: timeout) }
    func saveConfiguration(_ config: [String: JSONValue], id: String, timeout: TimeInterval) -> String {
        base.saveConfiguration(config, id: id, timeout: timeout)
    }
    func loadHistory(pageSize: Int, cursor: JSONValue, query: String, kind: String,
                     id: String, timeout: TimeInterval) -> String {
        base.loadHistory(pageSize: pageSize, cursor: cursor, query: query, kind: kind, id: id, timeout: timeout)
    }
    func clearHistory(id: String, timeout: TimeInterval) -> String { base.clearHistory(id: id, timeout: timeout) }
    func stop() { base.stop(); MainActor.assumeIsolated { onStop?() } }
}

@MainActor
final class CatalogAppFixture {
    let base: ProductTestHarness
    private(set) var clients: [CatalogTestClient] = []
    private(set) var runtimeCalls = 0
    private(set) var locateCalls = 0
    var cliAvailable = true
    var onLocate: (() -> Void)?
    var onRuntime: (() -> Void)?
    var onMake: ((CatalogTestClient) -> Void)?
    var model: ProbeModel { base.model }
    nonisolated static var config: [String: JSONValue] {
        var config = ProductTestHarness.configuration()
        config["plain_text_paste_enabled"] = .bool(false)
        return config
    }
    nonisolated static func payload(_ ids: [String] = ["fixture/model-a", "fixture/model-b"]) -> [String: JSONValue] {
        ["models": .array(ids.enumerated().map { index, id in
            .object(["id": .string(id), "name": .string("Fixture Model \(index + 1)"),
                     "description": .string("Provider description for \(id). Not an account entitlement.")])
        })]
    }
    init(plainPaste: PlainPasteModel? = nil) throws {
        base = try ProductTestHarness(savedCLI: false)
        base.model = ProbeModel(preferences: base.preferences, makeConnection: { [weak self] notice in
            let client = CatalogTestClient(notice: notice)
            self?.clients.append(client)
            self?.onMake?(client)
            return client
        }, runtimeProvider: { [weak self] in
            guard let self else { throw ProbeError.launchFailed }
            self.runtimeCalls += 1
            self.onRuntime?()
            return self.base.runtime
        }, locateCandidates: { [weak self] _, _ in
            guard let self else { return [] }
            self.locateCalls += 1
            self.onLocate?()
            return self.cliAvailable ? [
                CLICandidate(url: self.base.executable, executable: true),
                CLICandidate(url: self.base.alternateExecutable, executable: true)
            ] : []
        }, writeClipboard: { _ in XCTFail("Catalog tests must not use the clipboard."); return false },
           homeDirectory: base.root, plainPaste: plainPaste)
    }
    @discardableResult
    func ready(native: Bool = true, supported: Bool = true) throws -> CatalogTestClient {
        if !model.connected {
            cliAvailable = native
            model.openProduct()
        }
        let client = try XCTUnwrap(clients.last)
        try ready(client, supported: supported)
        return client
    }
    func ready(_ client: CatalogTestClient, supported: Bool = true,
               config: [String: JSONValue] = CatalogAppFixture.config) throws {
        client.base.event("ready", payload: ["capabilities": .array(supported ? [.string("model_catalog")] : [])])
        try finishSettings(client, config: config)
    }
    func finishSettings(_ client: CatalogTestClient, config: [String: JSONValue] = CatalogAppFixture.config) throws {
        client.base.event("completed", id: try XCTUnwrap(client.base.configurationLoads.last),
                          payload: ["config": .object(config)])
    }
    func complete(_ client: CatalogTestClient, payload: [String: JSONValue] = CatalogAppFixture.payload()) throws {
        client.base.event("completed", id: try XCTUnwrap(client.catalogRequests.last), payload: payload)
    }
    func finishTranslation(_ client: CatalogTestClient, text: String = "Preserved translation") throws {
        client.base.event("completed", id: try XCTUnwrap(client.base.translations.last?.id), payload: [
            "text": .string(text), "kind": .string("text"), "cached": .bool(false),
            "submitted": .bool(true), "history": .string("disabled"), "history_error": .null,
            "summarize": .bool(false), "target_lang": .string("zh")
        ])
    }
    func cleanUp() {
        onLocate = nil
        onRuntime = nil
        onMake = nil
        for client in clients { client.onStart = nil; client.onStop = nil; client.onCatalog = nil }
        model.prepareToQuit()
        clients.last?.base.stopped()
        base.cleanUp()
    }
}

final class ModelCatalogAppTests: XCTestCase {
    @MainActor
    func testCatalogConvenienceDispatchesThroughTheClientRequirement() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let transport: AppHelperClient = client
        XCTAssertEqual(transport.modelCatalog(id: "default-timeout"), "default-timeout")
        XCTAssertEqual(transport.modelCatalog(id: "explicit-timeout", timeout: 7), "explicit-timeout")
        XCTAssertEqual(client.catalogRequests, ["default-timeout", "explicit-timeout"])
        XCTAssertEqual(client.catalogTimeouts, [40, 7])
        XCTAssertTrue(client.base.messages.isEmpty)
    }

    @MainActor
    func testConstructionAndOpeningSettingsDoNotDiscoverModels() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        XCTAssertEqual(f.runtimeCalls, 0)
        XCTAssertEqual(f.locateCalls, 0)
        XCTAssertTrue(f.clients.isEmpty)
        let client = try f.ready()
        f.model.refreshDictionary()
        XCTAssertTrue(client.catalogRequests.isEmpty)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertEqual(f.model.modelCatalog.phase, .idle)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testExplicitNativeRefreshKeepsOutputDraftSelectionAndSettingsUntouched() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.reuseHistory(.init(id: "fixture", input: "Original", output: "Existing result"))
        f.model.modelProfile = "fixture/unsaved-selection"
        f.model.editCustomModelID("fixture/unapplied-draft")
        f.model.setCustomModelEditing(true)
        f.model.refreshModels()
        f.model.refreshModels()
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertEqual(f.model.modelCatalog.phase, .loading)
        try f.complete(client)
        XCTAssertEqual(f.model.modelCatalog.phase, .loaded)
        XCTAssertEqual(f.model.output, "Existing result")
        XCTAssertEqual(f.model.input, "Original")
        XCTAssertEqual(f.model.modelProfile, "fixture/unsaved-selection")
        XCTAssertEqual(f.model.modelSettings.draft, "fixture/unapplied-draft")
        XCTAssertTrue(f.model.modelSettings.editing)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertEqual(client.base.stopCount, 0)
    }

    @MainActor
    func testConfigOnlyRefreshUpgradesSelectedHelperWithoutSavingOrRequestingTranslation() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready(native: false)
        f.model.reuseHistory(.init(id: "fixture", input: "Original", output: "Kept while connecting"))
        f.cliAvailable = true
        f.model.refreshModels()
        XCTAssertEqual(old.base.stopCount, 1)
        XCTAssertTrue(old.catalogRequests.isEmpty)
        XCTAssertEqual(f.model.modelCatalog.phase, .connecting)
        old.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        XCTAssertFalse(current === old)
        XCTAssertEqual(current.base.operations, ["start.translation"])
        XCTAssertEqual(current.base.selectedExecutable, f.base.executable)
        try f.ready(current)
        XCTAssertEqual(current.catalogRequests.count, 1)
        XCTAssertTrue(f.clients.allSatisfy { $0.base.configurationSaves.isEmpty && $0.base.translations.isEmpty })
        XCTAssertEqual(f.model.output, "Kept while connecting")
        try f.complete(current)
        XCTAssertEqual(f.model.modelCatalog.models.count, 2)
    }

    @MainActor
    func testMissingCLIAndUnsupportedConnectionKeepManualModelsUsable() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let local = try f.ready(native: false)
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.missingCLI))
        XCTAssertEqual(f.clients.count, 1)
        XCTAssertEqual(local.base.stopCount, 0)
        f.model.editCustomModelID("fixture/manual-id")
        f.model.applyCustomModelID()
        let save = try XCTUnwrap(local.base.configurationSaves.last)
        local.base.event("completed", id: save.id)
        try f.finishSettings(local, config: save.config)
        XCTAssertEqual(f.model.modelSettings.phase, .applied("fixture/manual-id"))
        f.cliAvailable = true
        f.model.refreshModels()
        local.base.stopped()
        let native = try XCTUnwrap(f.clients.last)
        try f.ready(native, supported: false, config: save.config)
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.unavailable))
        XCTAssertEqual(f.model.modelProfile, "fixture/manual-id")
        XCTAssertTrue(native.catalogRequests.isEmpty)
        XCTAssertTrue(f.model.modelChoices(selection: "auto").contains("auto-fast"))
        XCTAssertTrue(f.model.canApplyModelSetting)
    }

    @MainActor
    func testDiscoveredSelectionUsesExistingApplyAndReadbackWithByteDistinctIDs() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let ids = ["fixture/\u{00e9}", "fixture/e\u{0301}", "fixture/CASE"]
        f.model.refreshModels()
        try f.complete(client, payload: CatalogAppFixture.payload(ids))
        let choices = f.model.modelChoices(selection: "auto").map { CodexModelSettings.ChoiceID(value: $0) }
        XCTAssertEqual(Set(choices).count, choices.count)
        for id in ids { XCTAssertTrue(choices.contains(.init(value: id))) }
        XCTAssertEqual(f.model.discoveredModel(ids[1])?.id.value.utf8.map { $0 }, Array(ids[1].utf8))
        f.model.applyModelProfile(ids[1])
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        XCTAssertTrue(CodexModelSettings.sameID(save.config["codex_model"]?.string, ids[1]))
        client.base.event("completed", id: save.id)
        try f.finishSettings(client, config: save.config)
        XCTAssertTrue(CodexModelSettings.sameID(f.model.modelSettings.savedProfile, ids[1]))
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertEqual(client.catalogRequests.count, 1)
    }

    @MainActor
    func testDiscoveryFailureRetriesOnlyExplicitlyAndLateOldSuccessCannotReplaceNewList() throws {
        for code in ["model_catalog_failed", "model_catalog_too_large", "provider_cleanup_failed"] {
            let f = try CatalogAppFixture()
            defer { f.cleanUp() }
            let client = try f.ready()
            f.model.refreshModels()
            let old = try XCTUnwrap(client.catalogRequests.last)
            client.base.event("failed", id: old, payload: ["code": .string(code)])
            let failure: ModelCatalogState.Failure = code == "model_catalog_too_large" ? .tooLarge :
                code == "provider_cleanup_failed" ? .cleanup : .discovery
            XCTAssertEqual(f.model.modelCatalog.phase, .failed(failure))
            XCTAssertEqual(client.catalogRequests.count, 1)
            XCTAssertEqual(client.base.stopCount, 0)
            f.model.refreshModels()
            let current: CatalogTestClient
            if code == "provider_cleanup_failed" {
                XCTAssertEqual(client.base.stopCount, 1)
                XCTAssertEqual(client.catalogRequests.count, 1)
                XCTAssertEqual(f.clients.count, 1, "The poisoned helper must drain before a replacement starts.")
                client.base.stopped()
                current = try XCTUnwrap(f.clients.last)
                XCTAssertFalse(current === client)
                try f.ready(current)
                XCTAssertEqual(current.catalogRequests.count, 1)
            } else {
                current = client
                XCTAssertEqual(client.catalogRequests.count, 2)
                XCTAssertEqual(f.clients.count, 1)
            }
            client.base.event("completed", id: old, payload: CatalogAppFixture.payload(["fixture/stale"]))
            try f.complete(current, payload: CatalogAppFixture.payload(["fixture/fresh"]))
            XCTAssertEqual(f.model.modelCatalog.models.map(\.id.value), ["fixture/fresh"])
        }
    }

    @MainActor
    func testCancelAcknowledgementDoesNotFinishTargetAndLateSuccessCannotPublishChoices() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        let target = try XCTUnwrap(client.catalogRequests.last)
        f.model.cancelModelCatalog()
        f.model.cancelModelCatalog()
        let cancel = try XCTUnwrap(client.base.messages.last)
        XCTAssertEqual(cancel.type, "cancel")
        XCTAssertEqual(cancel.payload["request_id"], .string(target))
        XCTAssertEqual(client.base.messages.filter { $0.type == "cancel" }.count, 1)
        client.base.event("completed", id: cancel.id, payload: ["cancelled": .string(target)])
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelling)
        f.model.refreshModels()
        XCTAssertEqual(client.catalogRequests.count, 1)
        try f.complete(client)
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelled)
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        f.model.refreshModels()
        XCTAssertEqual(client.catalogRequests.count, 2)
    }

    @MainActor
    func testRefreshWaitsForActiveTranslationWithoutCancellingItOrChangingSnapshot() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.input = "The explicitly submitted original."
        f.model.translate(useCache: false)
        let request = try XCTUnwrap(client.base.translations.last)
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .waiting)
        XCTAssertTrue(client.catalogRequests.isEmpty)
        XCTAssertFalse(client.base.messages.contains { $0.type == "cancel" })
        f.model.input = "Later editor text."
        try f.finishTranslation(client)
        XCTAssertEqual(client.catalogRequests.count, 1)
        try f.complete(client)
        XCTAssertEqual(client.base.translations.count, 1)
        XCTAssertEqual(request.text, "The explicitly submitted original.")
        XCTAssertEqual(f.model.input, "Later editor text.")
        XCTAssertEqual(f.model.output, "Preserved translation")
    }

    @MainActor
    func testCancellingWaitingRefreshDoesNotCancelActiveTranslation() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.input = "Keep this translation running."
        f.model.translate(useCache: false)
        f.model.refreshModels()
        f.model.cancelModelCatalog()
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelled)
        XCTAssertTrue(f.model.active)
        XCTAssertTrue(client.base.messages.filter { $0.type == "cancel" }.isEmpty)
        try f.finishTranslation(client)
        XCTAssertTrue(client.catalogRequests.isEmpty)
    }

    @MainActor
    func testRefreshWaitsForModelSaveAndReadbackWithoutRepeatingTheWrite() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.applyModelProfile("fixture/new-selection")
        f.model.refreshModels()
        XCTAssertTrue(client.catalogRequests.isEmpty)
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        client.base.event("completed", id: save.id)
        XCTAssertTrue(client.catalogRequests.isEmpty)
        try f.finishSettings(client, config: save.config)
        XCTAssertEqual(client.catalogRequests.count, 1)
        try f.complete(client)
        XCTAssertEqual(client.base.configurationSaves.count, 1)
        XCTAssertEqual(f.model.modelProfile, "fixture/new-selection")
    }

    @MainActor
    func testCLIChangeInvalidatesResultsAndDoesNotAutomaticallyQueryReplacement() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready()
        f.model.refreshModels()
        let oldRequest = try XCTUnwrap(old.catalogRequests.last)
        f.model.selectedCLI = f.base.alternateExecutable.path
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        old.base.event("completed", id: oldRequest, payload: CatalogAppFixture.payload(["fixture/stale"]))
        old.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        try f.ready(current)
        XCTAssertTrue(current.catalogRequests.isEmpty)
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        f.model.refreshModels()
        XCTAssertEqual(current.base.selectedExecutable, f.base.alternateExecutable)
        try f.complete(current)
        old.base.event("completed", id: oldRequest, payload: CatalogAppFixture.payload(["fixture/stale-again"]))
        XCTAssertEqual(f.model.modelCatalog.models.map(\.id.value), ["fixture/model-a", "fixture/model-b"])
    }

    @MainActor
    func testCancelDuringConfigUpgradeDoesNotRestartHelperOrRunCatalog() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready(native: false)
        f.cliAvailable = true
        f.model.refreshModels()
        f.model.cancelModelCatalog()
        old.base.stopped()
        XCTAssertEqual(f.clients.count, 1)
        XCTAssertTrue(old.catalogRequests.isEmpty)
        XCTAssertFalse(f.model.connected)
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelled)
    }

    @MainActor
    func testHelperFailureDuringPreparationCanBeRetriedWithoutDiagnostics() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        f.model.refreshModels()
        let old = try XCTUnwrap(f.clients.last)
        old.base.event("ready", payload: ["capabilities": .array([.string("model_catalog")])])
        XCTAssertTrue(f.model.settingsBusy)
        old.base.failure(.launchFailed)
        f.model.refreshModels()
        XCTAssertEqual(old.base.stopCount, 1)
        old.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        try f.ready(current)
        XCTAssertEqual(current.catalogRequests.count, 1)
        XCTAssertTrue(current.base.translations.isEmpty)
    }

    @MainActor
    func testMalformedResponseAndFailedConfigurationNeverPublishPartialModels() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        try f.complete(client, payload: ["models": .array([.object(["id": .string("fixture/missing-fields")])])])
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.discovery))
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        f.model.loadSettings()
        f.model.refreshModels()
        client.base.event("failed", id: try XCTUnwrap(client.base.configurationLoads.last),
                          payload: ["code": .string("config_io_failed")])
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.connection))
        XCTAssertEqual(client.catalogRequests.count, 1)
        let reads = client.base.configurationLoads.count
        f.model.refreshModels()
        XCTAssertEqual(client.base.configurationLoads.count, reads + 1)
        try f.finishSettings(client)
        XCTAssertEqual(client.catalogRequests.count, 2)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
    }

    @MainActor
    func testSynchronousCatalogCompletionCannotBeLostOrCauseASecondRequest() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        client.onCatalog = { [weak client] id in
            client?.base.event("accepted", id: id, payload: ["operation": .string("model_catalog")])
            client?.base.event("started", id: id, payload: ["operation": .string("model_catalog")])
            client?.base.event("completed", id: id, payload: CatalogAppFixture.payload())
        }
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .loaded)
        XCTAssertNil(f.model.modelCatalog.requestID)
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertFalse(f.model.active)
        XCTAssertTrue(f.model.output.isEmpty)
    }

    @MainActor
    func testSynchronousConfigUpgradeCompletesOneQueryWithoutRecursion() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready(native: false)
        old.onStop = { [weak old] in old?.base.stopped() }
        f.onMake = { [weak f] client in
            client.onStart = { [weak f, weak client] in
                guard let f, let client else { return }
                do {
                    try f.ready(client)
                } catch {
                    XCTFail("Synchronous native readiness must succeed: \(error)")
                }
            }
            client.onCatalog = { [weak client] id in
                client?.base.event("completed", id: id, payload: CatalogAppFixture.payload())
            }
        }
        f.cliAvailable = true
        f.model.refreshModels()
        XCTAssertEqual(f.clients.count, 2)
        XCTAssertEqual(f.clients.last?.catalogRequests.count, 1)
        XCTAssertEqual(f.model.modelCatalog.phase, .loaded)
    }

    @MainActor
    func testReentrantCancellationDuringRuntimePreparationDoesNotStartAHelper() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        f.onRuntime = { [weak f] in f?.model.cancelModelCatalog() }
        f.model.refreshModels()
        XCTAssertEqual(f.runtimeCalls, 1)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelled)
    }

    @MainActor
    func testReentrantCLISelectionDuringLocateCannotBeOverwrittenByOldDiscovery() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        f.onLocate = { [weak f] in
            guard let f else { return }
            f.model.selectedCLI = f.base.alternateExecutable.path
        }
        f.model.refreshModels()
        XCTAssertEqual(f.model.selectedCLI, f.base.alternateExecutable.path)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.cliChanged))
    }

    @MainActor
    func testDisconnectAndShutdownDiscardCatalogAndRejectLateCompletion() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        let id = try XCTUnwrap(client.catalogRequests.last)
        f.model.prepareToQuit()
        client.base.event("completed", id: id, payload: CatalogAppFixture.payload())
        client.base.stopped()
        f.model.refreshModels()
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertEqual(f.clients.count, 1)
        XCTAssertTrue(f.model.catalogShutDown)
        XCTAssertFalse(f.model.hasProcesses)
    }

    @MainActor
    func testDiscoveryAfterDisconnectedFailedTranslationKeepsItsPartialText() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready()
        f.model.input = "A synthetic translation."
        f.model.translate(useCache: false)
        let id = try XCTUnwrap(old.base.translations.last?.id)
        old.base.event("delta", id: id, payload: ["text": .string("Partial result"), "submitted": .bool(true)])
        old.base.event("failed", id: id, payload: ["code": .string("provider_failed"), "submitted": .bool(true)])
        XCTAssertEqual(f.model.output, "Partial result")
        f.model.stopHelper()
        old.base.stopped()
        f.model.refreshModels()
        let current = try XCTUnwrap(f.clients.last)
        try f.ready(current)
        try f.complete(current)
        XCTAssertEqual(f.model.output, "Partial result")
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertTrue(current.base.translations.isEmpty)
    }

    @MainActor
    func testExplicitRefreshWaitsForPlainPasteDrainWithoutCancellingIt() async throws {
        let service = PasteTestService()
        let paste = PlainPasteModel(service: service, registrar: PasteTestRegistrar(), routing: PasteTestRouting())
        let f = try CatalogAppFixture(plainPaste: paste)
        defer { f.cleanUp() }
        let client = try f.ready()
        service.progress(.waitingForKeys)
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .waiting)
        XCTAssertTrue(client.catalogRequests.isEmpty)
        XCTAssertEqual(service.cancellations, 0)
        let submitted = expectation(description: "Only the explicitly waiting refresh resumes after drain")
        client.onCatalog = { _ in submitted.fulfill() }
        service.finish(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed)
        await fulfillment(of: [submitted], timeout: 2)
        XCTAssertEqual(client.catalogRequests.count, 1)
    }

    @MainActor
    func testLocalCaptureRemainsIndependentOfCatalogCompletion() async throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let screen = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { capture.cancel() }
        capture.start()
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        let task = try XCTUnwrap(screen.captureTask)
        f.model.refreshModels()
        try f.complete(client)
        XCTAssertEqual(screen.phase, .capturing)
        source.finishCapture()
        await task.value
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertNil(screen.ocrTask)
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    func testCatalogCompletionDuringANewerTranslationCannotChangeItsRequestOrResult() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        f.model.input = "An explicit translation after starting discovery."
        f.model.translate(useCache: false)
        let request = try XCTUnwrap(client.base.translations.last)
        f.model.input = "A later unsent editor change."
        f.model.editCustomModelID("fixture/next-draft")
        try f.complete(client)
        XCTAssertTrue(f.model.active)
        XCTAssertEqual(f.model.productPhase, .translating)
        XCTAssertEqual(client.base.translations.count, 1)
        XCTAssertEqual(client.base.translations.last?.id, request.id)
        XCTAssertEqual(request.text, "An explicit translation after starting discovery.")
        XCTAssertEqual(f.model.modelSettings.draft, "fixture/next-draft")
        try f.finishTranslation(client)
        XCTAssertEqual(f.model.output, "Preserved translation")
        XCTAssertEqual(f.model.input, "A later unsent editor change.")
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
    }

    @MainActor
    func testUnusableProviderIDIsRetainedExactlyAndCannotSilentlyApplyAPreset() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let longID = String(repeating: "x", count: 257)
        f.model.refreshModels()
        try f.complete(client, payload: CatalogAppFixture.payload([longID]))
        XCTAssertEqual(f.model.modelCatalog.models.first?.id.value, longID)
        XCTAssertTrue(f.model.modelChoices(selection: "auto-fast").contains(longID))
        f.model.applyModelProfile(longID)
        XCTAssertEqual(f.model.modelSettings.phase, .failed(.invalidID(.tooLong)))
        XCTAssertEqual(f.model.modelProfile, "auto-fast")
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    func testFatalCleanupAfterCancellationIsNotReportedAsSuccessfulCancellation() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        f.model.cancelModelCatalog()
        client.base.event("failed", id: try XCTUnwrap(client.catalogRequests.last),
                          payload: ["code": .string("provider_cleanup_failed")])
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.cleanup))
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        f.model.refreshModels()
        XCTAssertEqual(client.base.stopCount, 1)
        XCTAssertEqual(client.catalogRequests.count, 1)
        client.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        XCTAssertFalse(current === client)
        try f.ready(current)
        try f.complete(current)
        XCTAssertEqual(f.model.modelCatalog.phase, .loaded)
    }

    @MainActor
    func testExplicitTranslationAfterFatalCatalogCleanupUsesANewHelperWithoutReplayingDiscovery() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        client.base.event("failed", id: try XCTUnwrap(client.catalogRequests.last),
                          payload: ["code": .string("provider_cleanup_failed")])
        XCTAssertEqual(client.base.stopCount, 0)
        f.model.input = "A new explicit translation."
        f.model.translate(useCache: false)
        XCTAssertEqual(client.base.stopCount, 1)
        XCTAssertTrue(client.base.translations.isEmpty)
        client.base.stopped()
        let current = try XCTUnwrap(f.clients.last)
        XCTAssertFalse(current === client)
        try f.ready(current)
        if let save = current.base.configurationSaves.last {
            current.base.event("completed", id: save.id)
            try f.finishSettings(current, config: save.config)
        }
        XCTAssertEqual(current.base.translations.count, 1)
        XCTAssertTrue(current.catalogRequests.isEmpty)
        try f.finishTranslation(current)
        XCTAssertEqual(f.model.primaryResult, "Preserved translation")
    }

    @MainActor
    func testExplicitRefreshResumesAfterHistoryReadWithoutChangingTheHistoryQuery() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.loadHistory()
        let read = try XCTUnwrap(client.base.historyLoads.last)
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .waiting)
        XCTAssertTrue(client.catalogRequests.isEmpty)
        client.base.event("completed", id: read.id,
                          payload: ProductTestHarness.historyPage(entries: [], total: 0))
        XCTAssertEqual(client.base.historyLoads.count, 1)
        XCTAssertEqual(client.catalogRequests.count, 1)
        XCTAssertTrue(client.base.historyClears.isEmpty)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
    }

    @MainActor
    func testCLIKindChangeCancelsOldCatalogAndIgnoresItsLateResult() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        let request = try XCTUnwrap(client.catalogRequests.last)
        f.model.cliName = "claude"
        XCTAssertEqual(client.base.messages.last?.type, "cancel")
        XCTAssertEqual(client.base.messages.last?.payload["request_id"], .string(request))
        try f.complete(client)
        XCTAssertTrue(f.model.modelCatalog.models.isEmpty)
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.cliChanged))
        XCTAssertTrue(client.base.translations.isEmpty)
    }

    @MainActor
    func testCancellingBootstrapReadDoesNotClearAnEarlierTranslationValidationMessage() throws {
        let f = try CatalogAppFixture()
        defer { f.cleanUp() }
        f.model.translate(useCache: false)
        XCTAssertEqual(f.model.productPhase, .failed)
        let message = f.model.productMessage
        f.model.refreshModels()
        let client = try XCTUnwrap(f.clients.last)
        f.model.cancelModelCatalog()
        try f.ready(client)
        XCTAssertEqual(f.model.modelCatalog.phase, .cancelled)
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertEqual(f.model.productMessage, message)
        XCTAssertTrue(client.catalogRequests.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
    }
}
