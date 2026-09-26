import XCTest
import Combine
import SwiftUI
@testable import CCTranslateMac
@testable import CCTranslateSupport

// These clients only record protocol operations. Even their executable fixtures are never run.
final class ProductTestHelper: AppHelperClient {
    struct Translation {
        let id: String
        let text: String
        let language: String
        let origin: String
        let useCache: Bool
        let recordHistory: Bool
    }
    struct Action {
        let id: String
        let action: ResultAction
        let text: String
        let language: String
        let targetLanguage: String?
    }
    struct Save {
        let id: String
        let config: [String: JSONValue]
    }
    struct History {
        let id: String
        let pageSize: Int
        let cursor: JSONValue
        let query: String
        let kind: String
    }

    private let notice: (HelperNotice) -> Void
    private var sequences: [String: Int64] = [:]
    private(set) var operations: [String] = []
    private(set) var messages: [ClientMessage] = []
    private(set) var translations: [Translation] = []
    private(set) var resultActions: [Action] = []
    private(set) var dictionaryRequests: [(request: DictionaryRequest, id: String)] = []
    var automaticDictionaryReplies = true
    private(set) var configurationLoads: [String] = []
    private(set) var configurationSaves: [Save] = []
    private(set) var historyLoads: [History] = []
    private(set) var historyClears: [String] = []
    private(set) var selectedExecutable: URL?
    private(set) var selectedProvider: TranslationProvider?
    private(set) var stopCount = 0

    init(notice: @escaping (HelperNotice) -> Void) { self.notice = notice }

    func start(runtime: BundleRuntime) { operations.append("start.diagnostic") }
    func startConfiguration(runtime: BundleRuntime, home: URL) {
        operations.append("start.configuration")
    }
    func startTranslation(runtime: BundleRuntime, home: URL, provider: TranslationProvider,
                          command: URL, environment: [String: String]) {
        selectedExecutable = command
        selectedProvider = provider
        operations.append("start.translation")
    }
    func send(_ message: ClientMessage, timeout: TimeInterval) {
        messages.append(message)
        operations.append(message.type)
    }
    func translate(text: String, appLanguage: String, origin: String, useCache: Bool,
                   recordHistory: Bool, id: String, timeout: TimeInterval) -> String {
        translations.append(Translation(id: id, text: text, language: appLanguage, origin: origin,
                                        useCache: useCache, recordHistory: recordHistory))
        operations.append("translate")
        return id
    }
    func resultAction(_ action: ResultAction, text: String, appLanguage: String,
                      targetLanguage: String?, id: String, timeout: TimeInterval) -> String {
        resultActions.append(Action(id: id, action: action, text: text, language: appLanguage,
                                    targetLanguage: targetLanguage))
        operations.append("result_action")
        return id
    }
    func dictionary(_ request: DictionaryRequest, id: String, timeout: TimeInterval) -> String {
        dictionaryRequests.append((request, id))
        operations.append(request.operation)
        if automaticDictionaryReplies {
            MainActor.assumeIsolated {
                switch request {
                case .lookup:
                    event("completed", id: id, payload: ["status": .string("disabled"), "result": .null])
                case .status:
                    event("completed", id: id, payload: Self.dictionaryStatus())
                default: break
                }
            }
        }
        return id
    }

    static func dictionaryStatus(installed: Bool = false, enabled: Bool = false) -> [String: JSONValue] {
        ["state": .string(installed ? "ready" : "not_installed"), "enabled": .bool(enabled),
         "size": .integer(4), "sha256": .string(String(repeating: "a", count: 64)),
         "data_version": .string("fixture-v1"), "download_url": .string("https://example.invalid/dictionary"),
         "entry_count": .integer(installed ? 1 : 0)]
    }
    func loadConfiguration(id: String, timeout: TimeInterval) -> String {
        configurationLoads.append(id)
        operations.append("config.load")
        return id
    }
    func saveConfiguration(_ config: [String: JSONValue], id: String,
                           timeout: TimeInterval) -> String {
        configurationSaves.append(Save(id: id, config: config))
        operations.append("config.save")
        return id
    }
    func loadHistory(pageSize: Int, cursor: JSONValue, query: String, kind: String, id: String,
                     timeout: TimeInterval) -> String {
        historyLoads.append(History(id: id, pageSize: pageSize, cursor: cursor, query: query, kind: kind))
        operations.append("history.load")
        return id
    }
    func clearHistory(id: String, timeout: TimeInterval) -> String {
        historyClears.append(id)
        operations.append("history.clear")
        return id
    }
    func stop() {
        stopCount += 1
        operations.append("stop")
    }

    @MainActor
    func event(_ type: String, id: String = "ready", payload: [String: JSONValue] = [:]) {
        let sequence = (sequences[id] ?? -1) + 1
        sequences[id] = sequence
        notice(.event(ServerEvent(id: id, sequence: sequence, type: type, payload: payload)))
    }

    @MainActor
    func failure(_ error: ProbeError) { notice(.failure(error)) }

    @MainActor
    func stopped() { notice(.stopped) }
}

@MainActor
final class ProductTestHarness {
    private final class Calls {
        var helpers: [ProductTestHelper] = []
        var runtimeRequests = 0
        var locatorRequests = 0
        var canLocateCLI = false
        var copiedText: [String] = []
    }

    let root: URL
    let executable: URL
    let alternateExecutable: URL
    let suiteName: String
    let preferences: UserDefaults
    let runtime: BundleRuntime
    private let calls = Calls()
    var helpers: [ProductTestHelper] { calls.helpers }
    var runtimeRequests: Int { calls.runtimeRequests }
    var locatorRequests: Int { calls.locatorRequests }
    var copiedText: [String] { calls.copiedText }
    var canLocateCLI: Bool {
        get { calls.canLocateCLI }
        set { calls.canLocateCLI = newValue }
    }
    var model: ProbeModel!

    init(savedCLI: Bool = true, autodetectFixture: Bool = false,
         dictionaryDownloader: DictionaryDownloading? = nil,
         selectionMonitor: (any PassiveSelectionMonitoring)? = nil,
         captureRegistrar: (any NativeShortcutRegistering)? = nil,
         readPermissions: @escaping @MainActor () -> PermissionSnapshot = { Permissions.snapshot() },
         latencyClock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) throws {
        let identifier = UUID().uuidString
        root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent(".fixtures-\(identifier)", isDirectory: true)
        executable = root.appendingPathComponent("codex")
        alternateExecutable = root.appendingPathComponent("codex-alternate")
        suiteName = "dev.cc-translate.product-tests.\(identifier)"
        preferences = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        preferences.removePersistentDomain(forName: suiteName)
        preferences.set("en", forKey: "interfaceLanguage")
        let app = root.appendingPathComponent("Fixture.app", isDirectory: true)
        let python = app.appendingPathComponent("Contents/Resources/python/bin/python3")
        let launcher = app.appendingPathComponent("Contents/Resources/Core/launch.py")
        do {
            for file in [python, launcher, executable, alternateExecutable] {
                try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                        withIntermediateDirectories: true)
                try Data("Inert product test fixture; never execute.\n".utf8).write(to: file)
            }
            for file in [python, executable, alternateExecutable] {
                try FileManager.default.setAttributes([.posixPermissions: 0o755],
                                                       ofItemAtPath: file.path)
            }
            runtime = try BundleRuntime(appURL: app)
        } catch {
            preferences.removePersistentDomain(forName: suiteName)
            if FileManager.default.fileExists(atPath: root.path) {
                XCTAssertNoThrow(try FileManager.default.removeItem(at: root))
            }
            throw error
        }
        if savedCLI { preferences.set(executable.path, forKey: "selectedCodexPath") }
        let calls = self.calls
        calls.canLocateCLI = savedCLI || autodetectFixture
        let runtime = self.runtime
        let executable = self.executable
        let alternateExecutable = self.alternateExecutable
        model = ProbeModel(
            preferences: preferences,
            makeConnection: { notice in
                let helper = ProductTestHelper(notice: notice)
                calls.helpers.append(helper)
                return helper
            },
            runtimeProvider: {
                calls.runtimeRequests += 1
                return runtime
            },
            locateCandidates: { name, userURL in
                calls.locatorRequests += 1
                guard calls.canLocateCLI else { return [] }
                // Exercise CLILocator's real filesystem validation, without executing a CLI.
                let candidates = CLILocator.candidates(
                    name: name, userURL: autodetectFixture ? executable : (userURL ?? executable))
                let alternate = CLILocator.candidates(name: name, userURL: alternateExecutable)
                    .filter { $0.url == alternateExecutable }
                return candidates + alternate
            }, dictionaryDownloader: dictionaryDownloader,
            writeClipboard: { calls.copiedText.append($0); return true },
            selectionMonitor: selectionMonitor,
            captureShortcut: captureRegistrar.map { CaptureShortcutModel(preferences: preferences, registrar: $0) },
            readPermissions: readPermissions, latencyClock: latencyClock)
    }

    func cleanUp() {
        model.closePanel()
        helpers.last?.stopped()
        preferences.removePersistentDomain(forName: suiteName)
        XCTAssertNoThrow(try FileManager.default.removeItem(at: root))
    }

    nonisolated static func configuration(direction: String = "auto", model: String = "auto-fast",
                                         language: String = "en_US", history: Bool = true,
                                         summary: Bool = true, historyLimit: Int64 = 100,
                                         maxChars: Int64 = 5000, copyInterval: Double = 0.5) -> [String: JSONValue] {
        ["direction": .string(direction), "codex_model": .string(model), "claude_model": .string("haiku"),
         "language": .string(language), "history_enabled": .bool(history),
         "model_provider": .string("codex_cli"), "summary_enabled": .bool(summary),
         "labs_defaults_migrated": .bool(true), "history_limit": .integer(historyLimit),
         "max_chars": .integer(maxChars), "double_press_window": .number(copyInterval)]
    }

    @discardableResult
    func ready(configuration: [String: JSONValue] = ProductTestHarness.configuration(),
               capabilities: [String] = []) throws
        -> ProductTestHelper {
        if helpers.isEmpty { model.openProduct() }
        let helper = try XCTUnwrap(helpers.last)
        helper.event("ready", payload: ["capabilities": .array(capabilities.map(JSONValue.string))])
        try finishConfiguration(on: helper, configuration: configuration)
        if model.preparing && helper.stopCount > 0 {
            helper.stopped()
            let upgraded = try XCTUnwrap(helpers.last)
            XCTAssertFalse(upgraded === helper)
            upgraded.event("ready")
            try finishConfiguration(on: upgraded, configuration: configuration)
            return upgraded
        }
        return helper
    }

    @discardableResult
    func localReady(configuration: [String: JSONValue] = ProductTestHarness.configuration(),
                    automaticReplies: Bool = false) throws -> ProductTestHelper {
        // Start without a discoverable CLI; tests can expose the fixture CLI for a later fallback.
        let discoverable = canLocateCLI
        canLocateCLI = false
        model.openProduct()
        canLocateCLI = discoverable
        let helper = try XCTUnwrap(helpers.last)
        helper.automaticDictionaryReplies = automaticReplies
        helper.event("ready")
        try finishConfiguration(on: helper, configuration: configuration)
        return helper
    }

    func finishConfiguration(on helper: ProductTestHelper,
                             configuration: [String: JSONValue] = ProductTestHarness.configuration()) throws {
        let id = try XCTUnwrap(helper.configurationLoads.last)
        helper.event("completed", id: id, payload: ["config": .object(configuration)])
    }

    nonisolated static func historyEntry(input: String, output: String, kind: String = "text") -> JSONValue {
        .object(["input": .string(input), "output": .string(output), "kind": .string(kind),
                 "ts": .string("2026-01-01T12:00:00Z")])
    }

    nonisolated static func historyPage(entries: [JSONValue], total: Int,
                                       revision: String = String(repeating: "a", count: 64),
                                       nextOffset: Int? = nil) -> [String: JSONValue] {
        ["entries": .array(entries), "revision": .string(revision), "total": .integer(Int64(total)),
         "next_cursor": nextOffset.map {
             .object(["offset": .integer(Int64($0)), "revision": .string(revision)])
         } ?? .null]
    }
}

final class ProductModelTests: XCTestCase {
    @MainActor
    func testConstructionDoesNotStartHelperLocateCLIOrCheckPermissions() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)

        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeRequests, 0)
        XCTAssertEqual(fixture.locatorRequests, 0)
        XCTAssertFalse(model.hasProcesses)
        XCTAssertFalse(model.connected)
        XCTAssertFalse(model.cliBusy)
        XCTAssertFalse(model.monitorEnabled)
        XCTAssertFalse(model.screen.busy)
        XCTAssertEqual(model.permissions, "Not checked.")
        XCTAssertEqual(model.productPhase, .idle)
    }

    @MainActor
    func testOpenProductRemembersExecutableWithoutVersionOrModelCall() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.openProduct()
        let helper = try XCTUnwrap(fixture.helpers.first)

        XCTAssertEqual(model.selectedCLI, fixture.executable.path)
        XCTAssertEqual(helper.selectedExecutable, fixture.executable)
        XCTAssertEqual(helper.operations, ["start.translation"])
        XCTAssertEqual(fixture.locatorRequests, 1)
        XCTAssertTrue(model.candidates.contains { $0.url == fixture.executable && $0.executable })
        XCTAssertEqual(fixture.preferences.string(forKey: "selectedCodexPath"), fixture.executable.path)
        XCTAssertFalse(model.cliBusy)
        XCTAssertEqual(model.permissions, "Not checked.")
        XCTAssertFalse(model.monitorEnabled)
        _ = try fixture.ready()
        model.openProduct()
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertEqual(helper.operations, ["start.translation", "config.load"])
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.messages.isEmpty)
    }

    @MainActor
    func testOpenProductAutodetectsAndPersistsExecutableWithoutSubmission() throws {
        let fixture = try ProductTestHarness(savedCLI: false, autodetectFixture: true)
        defer { fixture.cleanUp() }
        XCTAssertNil(fixture.preferences.string(forKey: "selectedCodexPath"))
        fixture.model.openProduct()

        XCTAssertEqual(fixture.model.selectedCLI, fixture.executable.path)
        XCTAssertEqual(fixture.preferences.string(forKey: "selectedCodexPath"), fixture.executable.path)
        XCTAssertEqual(fixture.helpers.first?.operations, ["start.translation"])
        XCTAssertFalse(fixture.model.cliBusy)
        XCTAssertFalse(fixture.model.needsCLI)
    }

    @MainActor
    func testMissingCLIAllowsSettingsButDoesNotTranslateOrInstall() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        var configurationRequests = 0
        model.onConfigurationRequired = { configurationRequests += 1 }
        model.input = "Synthetic source"
        model.translate()
        let helper = try fixture.ready()

        XCTAssertTrue(model.needsCLI)
        XCTAssertTrue(model.settingsReady)
        XCTAssertFalse(model.nativeTranslation)
        XCTAssertEqual(configurationRequests, 1)
        XCTAssertEqual(helper.operations, ["start.configuration", "config.load", "dictionary_lookup"])
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertFalse(model.cliBusy)
    }

    @MainActor
    func testHistoryAndSettingsRemainAvailableWithoutCLI() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let helper = try fixture.ready()
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertEqual(helper.operations, ["start.configuration", "config.load", "history.load"])
        XCTAssertTrue(model.needsCLI)
        XCTAssertTrue(model.settingsReady)
        XCTAssertFalse(model.nativeTranslation)
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [ProductTestHarness.historyEntry(
                        input: "Previously saved synthetic source", output: "Saved synthetic translation")], total: 1))
        let row = try XCTUnwrap(model.historyPage.first)
        model.reuseHistory(row)
        XCTAssertEqual(model.output, row.output)
        model.direction = "to_en"
        model.saveSettings(history: false)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["direction"], .string("to_en"))
        XCTAssertEqual(save.config["history_enabled"], .bool(false))
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)

        XCTAssertTrue(model.settingsReady)
        XCTAssertFalse(model.historyEnabled)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.messages.isEmpty)
        XCTAssertNil(helper.selectedExecutable)
        XCTAssertFalse(model.cliBusy)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testChoosingCLIUpgradesConnectionAfterDrainWithoutModelInvocationOrReplay() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.input = "Synthetic source attempted without a CLI"
        model.translate()
        let configuration = try fixture.ready()
        XCTAssertEqual(configuration.operations, ["start.configuration", "config.load"])
        XCTAssertTrue(configuration.translations.isEmpty)

        fixture.canLocateCLI = true
        model.locateCLI()
        XCTAssertEqual(model.selectedCLI, fixture.executable.path)
        XCTAssertEqual(configuration.stopCount, 1)
        XCTAssertEqual(fixture.helpers.count, 1)
        configuration.stopped()
        let translation = try fixture.ready()

        XCTAssertEqual(fixture.helpers.count, 2)
        XCTAssertEqual(translation.operations, ["start.translation", "config.load", "dictionary_status"])
        XCTAssertEqual(translation.selectedExecutable, fixture.executable)
        XCTAssertTrue(model.nativeTranslation)
        XCTAssertFalse(model.needsCLI)
        XCTAssertTrue(model.settingsReady)
        XCTAssertTrue(translation.translations.isEmpty)
        XCTAssertFalse(model.preparing)
        XCTAssertFalse(model.cliBusy)
        XCTAssertEqual(model.permissions, "Not checked.")
    }

    @MainActor
    func testOneClickWaitsForReadyAndNormalizedSettingsBeforeOneSend() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.input = "Synthetic clicked source"
        model.translate()
        let helper = try XCTUnwrap(fixture.helpers.first)
        XCTAssertEqual(model.productPhase, .preparing)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationLoads.isEmpty)

        helper.event("ready")
        XCTAssertTrue(model.settingsBusy)
        XCTAssertTrue(helper.translations.isEmpty)
        model.input = "Edited after clicking"
        try fixture.finishConfiguration(on: helper)
        XCTAssertEqual(helper.stopCount, 0)
        let provider = helper
        let request = try XCTUnwrap(provider.translations.first)

        XCTAssertEqual(provider.operations, ["start.translation", "config.load", "dictionary_lookup", "translate"])
        XCTAssertEqual(request.text, "Synthetic clicked source")
        XCTAssertEqual(request.language, "en_US")
        XCTAssertEqual(request.origin, "text")
        XCTAssertTrue(request.useCache)
        XCTAssertTrue(request.recordHistory)
        XCTAssertEqual(model.resultInput, request.text)
        XCTAssertEqual(model.input, "Edited after clicking")
        XCTAssertEqual(model.productPhase, .translating)
        XCTAssertTrue(model.active)
        provider.event("accepted", id: request.id)
        provider.event("started", id: request.id)
        XCTAssertEqual(provider.translations.count, 1)
    }

    @MainActor
    func testClickedSettingsStayImmutableAcrossOneSaveAndNormalizedReload() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        let helper = try fixture.ready()
        model.interfaceLanguage = "zh"
        model.direction = "to_en"
        model.modelProfile = "auto"
        model.input = "Synthetic immutable source"
        var starts = 0
        model.onTranslationStarted = { starts += 1 }
        model.translate(origin: "selection", useCache: false)
        model.input = "New unsent source"
        model.interfaceLanguage = "en"
        model.direction = "auto"
        model.modelProfile = "auto-fast"
        let save = try XCTUnwrap(helper.configurationSaves.first)
        XCTAssertEqual(save.config["direction"], .string("to_en"))
        XCTAssertEqual(save.config["codex_model"], .string("auto"))
        XCTAssertEqual(save.config["language"], .string("zh_CN"))
        XCTAssertEqual(save.config["model_provider"], .string("codex_cli"))
        XCTAssertTrue(helper.translations.isEmpty)
        helper.event("completed", id: save.id)
        XCTAssertEqual(helper.configurationLoads.count, 2)
        XCTAssertTrue(helper.translations.isEmpty)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        let request = try XCTUnwrap(helper.translations.first)

        XCTAssertEqual(helper.operations,
                       ["start.translation", "config.load", "config.save", "config.load", "translate"])
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(request.text, "Synthetic immutable source")
        XCTAssertEqual(request.language, "zh_CN")
        XCTAssertEqual(request.origin, "selection")
        XCTAssertFalse(request.useCache)
        XCTAssertEqual(starts, 1)
        XCTAssertEqual(model.direction, "auto", "Normalized loading must not clobber subsequent UI edits.")
        XCTAssertEqual(model.modelProfile, "auto-fast")
        XCTAssertEqual(model.interfaceLanguage, "en")
        helper.event("completed", id: request.id,
                     payload: ["text": .string("Synthetic result"), "kind": .string("text")])
        XCTAssertEqual(model.output, "Synthetic result")
        XCTAssertEqual(model.productPhase, .completed)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testFirstClickUsesSavedDefaultsWhenNoSettingsWereEdited() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(
            direction: "to_en", model: "auto"))

        XCTAssertEqual(fixture.model.direction, "to_en")
        XCTAssertEqual(fixture.model.modelProfile, "auto")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testFirstClickLoadsPresentationAndSavesOnlyMismatchedLanguageOnce() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.preferences.set("zh", forKey: "interfaceLanguage")
        let model = try XCTUnwrap(fixture.model)
        model.input = "Synthetic language-only settings change"
        model.translate()
        XCTAssertEqual(model.interfaceLanguage, "zh")
        model.interfaceLanguage = "en"
        let helper = try fixture.ready()
        let save = try XCTUnwrap(helper.configurationSaves.first)

        XCTAssertEqual(save.config["direction"], .string("auto"))
        XCTAssertEqual(save.config["codex_model"], .string("auto-fast"))
        XCTAssertEqual(save.config["language"], .string("zh_CN"))
        XCTAssertTrue(helper.translations.isEmpty)
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 2)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.first?.language, "zh_CN")
        XCTAssertEqual(model.interfaceLanguage, "en")
    }

    @MainActor
    func testStreamingDeltasRenderOnMainQueueAndFinalPayloadReplacesPartial() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.input = "Synthetic source"
        model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        let rendered = expectation(description: "Buffered deltas rendered on the main queue")
        let observation = model.$output
            .filter { $0 == "Synthetic streamed text" }
            .prefix(1)
            .sink { _ in rendered.fulfill() }
        helper.event("delta", id: request.id, payload: ["text": .string("Synthetic ")])
        helper.event("delta", id: request.id, payload: ["text": .string("streamed text")])
        await fulfillment(of: [rendered], timeout: 2)
        XCTAssertEqual(model.output, "Synthetic streamed text")

        helper.event("delta", id: request.id, payload: ["text": .string(" Pending obsolete suffix")])
        helper.event("completed", id: request.id, payload: ["text": .string("Authoritative synthetic result")])
        await drainRenderingQueue()
        XCTAssertEqual(model.output, "Authoritative synthetic result")
        XCTAssertEqual(model.productPhase, .completed)
        XCTAssertEqual(helper.translations.count, 1)
        withExtendedLifetime(observation) {}
    }

    @MainActor
    func testCancellationTerminalFlushesBufferedPartialWithoutAnotherSend() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("delta", id: request.id, payload: ["text": .string("Synthetic partial")])
        fixture.model.cancel()
        helper.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])

        XCTAssertEqual(fixture.model.output, "Synthetic partial")
        XCTAssertEqual(fixture.model.productPhase, .cancelled)
        XCTAssertFalse(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testFailureFlushesBufferedPartialWithoutReplaying() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("delta", id: request.id, payload: ["text": .string("Synthetic partial")])
        helper.failure(.translationOutcomeUnknown)

        XCTAssertEqual(fixture.model.output, "Synthetic partial")
        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertFalse(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testCancelBeforeReadyDiscardsQueuedTranslation() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.input = "Synthetic cancelled source"
        fixture.model.translate()
        fixture.model.cancel()
        XCTAssertEqual(fixture.model.productPhase, .cancelled)
        let helper = try fixture.ready()

        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.messages.isEmpty)
        XCTAssertFalse(fixture.model.active)
        fixture.model.openProduct()
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testCancelDuringSettingsSaveDoesNotSubmitAfterReload() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.direction = "to_en"
        fixture.model.input = "Synthetic cancelled source"
        fixture.model.translate()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        fixture.model.cancel()
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)

        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(fixture.model.active)
        XCTAssertFalse(fixture.model.preparing)
    }

    @MainActor
    func testReplacementWaitsForOriginalTerminalNotCancellationAcknowledgement() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "First synthetic source"
        fixture.model.translate()
        let original = try XCTUnwrap(helper.translations.first)
        fixture.model.input = "Replacement synthetic source"
        fixture.model.translate(useCache: false)
        let cancellation = try XCTUnwrap(helper.messages.last)

        XCTAssertEqual(cancellation.type, "cancel")
        XCTAssertEqual(cancellation.payload["request_id"], .string(original.id))
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(fixture.model.active)
        helper.event("completed", id: cancellation.id)
        helper.event("delta", id: original.id, payload: ["text": .string("Old partial")])
        XCTAssertEqual(helper.translations.count, 1)
        helper.event("cancelled", id: original.id, payload: ["submitted": .bool(true)])
        XCTAssertEqual(helper.translations.count, 2)
        let replacement = try XCTUnwrap(helper.translations.last)
        XCTAssertNotEqual(original.id, replacement.id)
        XCTAssertEqual(replacement.text, "Replacement synthetic source")
        XCTAssertFalse(replacement.useCache)
        XCTAssertEqual(fixture.model.output, "")
        XCTAssertEqual(fixture.model.resultInput, replacement.text)
        XCTAssertEqual(fixture.model.productPhase, .translating)
    }

    @MainActor
    func testRepeatedReplacementClicksKeepOnlyLatestDraft() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Original source"
        fixture.model.translate()
        let original = try XCTUnwrap(helper.translations.first)
        fixture.model.input = "Superseded draft"
        fixture.model.translate()
        fixture.model.input = "Latest explicit draft"
        fixture.model.translate()
        XCTAssertEqual(helper.translations.count, 1)
        helper.event("completed", id: original.id, payload: ["text": .string("Original result")])

        XCTAssertEqual(helper.translations.map(\.text), ["Original source", "Latest explicit draft"])
        XCTAssertEqual(fixture.model.resultInput, "Latest explicit draft")
    }

    @MainActor
    func testFailedTranslationIsNeverAutomaticallyReplayed() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Synthetic failure source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("failed", id: request.id, payload: ["code": .string("provider_failed")])
        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertFalse(fixture.model.active)
        fixture.model.openProduct()
        fixture.model.loadSettings()
        try fixture.finishConfiguration(on: helper)

        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testUnknownOutcomeDoesNotReplayOnExplicitConnectionRestart() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let original = try fixture.ready()
        fixture.model.input = "Possibly submitted synthetic source"
        fixture.model.translate()
        original.failure(.translationOutcomeUnknown)
        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertTrue(fixture.model.status.contains("unknown"))
        original.stopped()
        fixture.model.openProduct()
        let restarted = try fixture.ready()

        XCTAssertEqual(fixture.helpers.count, 2)
        XCTAssertEqual(original.translations.count, 1)
        XCTAssertTrue(restarted.translations.isEmpty)
        XCTAssertTrue(restarted.configurationSaves.isEmpty)
    }

    @MainActor
    func testCloseWhilePreparingPreventsLateReadyAndReloadFromSending() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.input = "Queued synthetic source"
        fixture.model.translate()
        let old = try XCTUnwrap(fixture.helpers.first)
        old.event("ready")
        let load = try XCTUnwrap(old.configurationLoads.last)
        fixture.model.closePanel()
        old.event("completed", id: load,
                  payload: ["config": .object(ProductTestHarness.configuration())])
        old.event("ready")
        XCTAssertEqual(old.stopCount, 1)
        XCTAssertTrue(old.translations.isEmpty)
        old.stopped()
        fixture.model.openProduct()
        let reopened = try fixture.ready()

        XCTAssertTrue(reopened.translations.isEmpty)
        XCTAssertEqual(fixture.model.output, "")
        XCTAssertEqual(fixture.model.productPhase, .idle,
                       "Reopening must not leave a discarded draft permanently preparing.")
    }

    @MainActor
    func testCloseDuringTranslationIgnoresLateOutput() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("delta", id: request.id, payload: ["text": .string("Buffered result")])
        fixture.model.closePanel()
        helper.event("completed", id: request.id, payload: ["text": .string("Late result")])
        helper.stopped()

        XCTAssertEqual(fixture.model.output, "")
        XCTAssertFalse(fixture.model.hasProcesses)
        XCTAssertFalse(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testClearTranslationDiscardsBufferedAndLateTerminalOutput() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("delta", id: request.id, payload: ["text": .string("Buffered old result")])
        fixture.model.clearTranslation()
        helper.event("delta", id: request.id, payload: ["text": .string("Late delta")])
        helper.event("completed", id: request.id, payload: ["text": .string("Late terminal result")])
        await drainRenderingQueue()

        XCTAssertEqual(fixture.model.input, "")
        XCTAssertEqual(fixture.model.resultInput, "")
        XCTAssertEqual(fixture.model.output, "")
        XCTAssertEqual(fixture.model.productPhase, .idle)
        XCTAssertFalse(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testHistoryReuseDoesNotTranslateAndIgnoresLateActiveResult() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Live synthetic source"
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.first)
        helper.event("delta", id: request.id, payload: ["text": .string("Buffered old result")])
        let history = ProbeModel.HistoryRow(id: "synthetic-history", input: "Saved source",
                                           output: "Saved translation", kind: "dict")
        fixture.model.reuseHistory(history)
        helper.event("delta", id: request.id, payload: ["text": .string("Late delta")])
        helper.event("completed", id: request.id, payload: ["text": .string("Late replacement")])
        await drainRenderingQueue()

        XCTAssertEqual(fixture.model.input, history.input)
        XCTAssertEqual(fixture.model.resultInput, history.input)
        XCTAssertEqual(fixture.model.output, history.output)
        XCTAssertEqual(fixture.model.resultKind, "dict")
        XCTAssertEqual(fixture.model.productPhase, .completed)
        XCTAssertFalse(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testSettingsLoadFailureDropsQueuedTranslationWithoutSaving() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let helper = try XCTUnwrap(fixture.helpers.first)
        helper.event("ready")
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("config_unavailable")])

        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertFalse(fixture.model.settingsBusy)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        fixture.model.loadSettings()
        try fixture.finishConfiguration(on: helper)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testSettingsSaveFailureDropsQueuedTranslationWithoutRetry() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.direction = "to_en"
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("failed", id: save.id, payload: ["code": .string("state_io_failed")])

        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertFalse(fixture.model.settingsBusy)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        fixture.model.loadSettings()
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testSettingsReloadFailureDoesNotSendOrRepeatSuccessfulWrite() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.direction = "to_en"
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("completed", id: save.id)
        helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["code": .string("config_unavailable")])

        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 2)
    }

    @MainActor
    func testNormalizedSettingsMismatchFailsRatherThanSavingOrSendingAgain() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.direction = "to_en"
        fixture.model.input = "Synthetic source"
        fixture.model.translate()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper)

        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 2)
    }

    @MainActor
    func testDiagnosticsToProductWaitsForOldConnectionToDrain() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.startHelper()
        let old = try XCTUnwrap(fixture.helpers.first)
        old.event("ready")
        fixture.model.input = "Explicit synthetic product request"
        fixture.model.translate()

        XCTAssertEqual(old.operations, ["start.diagnostic", "stop"])
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertTrue(old.translations.isEmpty)
        old.event("ready")
        XCTAssertEqual(fixture.helpers.count, 1)
        old.stopped()
        let replacement = try fixture.ready()

        XCTAssertEqual(fixture.helpers.count, 2)
        XCTAssertEqual(replacement.operations, ["start.translation", "config.load", "dictionary_status", "translate"])
        XCTAssertEqual(replacement.translations.first?.text, "Explicit synthetic product request")
    }

    @MainActor
    func testStaleConnectionCallbacksCannotChangeNewConnectionOrReplayDraft() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let old = try fixture.ready()
        fixture.model.input = "Old synthetic source"
        fixture.model.translate()
        let oldTranslation = try XCTUnwrap(old.translations.first)
        let oldLoad = try XCTUnwrap(old.configurationLoads.first)
        fixture.model.closePanel()
        old.stopped()
        fixture.model.input = "New explicitly clicked source"
        fixture.model.translate()
        let current = try XCTUnwrap(fixture.helpers.last)

        old.event("ready")
        old.event("completed", id: oldLoad,
                  payload: ["config": .object(ProductTestHarness.configuration(direction: "to_en"))])
        old.event("completed", id: oldTranslation.id, payload: ["text": .string("Stale output")])
        old.failure(.translationOutcomeUnknown)
        old.stopped()
        XCTAssertFalse(fixture.model.ready)
        XCTAssertTrue(fixture.model.connected)
        XCTAssertEqual(fixture.model.productPhase, .preparing)
        XCTAssertEqual(fixture.model.output, "")
        XCTAssertEqual(fixture.helpers.count, 2)
        XCTAssertEqual(current.operations, ["start.translation"])
        current.event("ready")
        try fixture.finishConfiguration(on: current)
        let provider = current
        XCTAssertEqual(provider.translations.count, 1)
        XCTAssertEqual(provider.translations.first?.text, "New explicitly clicked source")
        XCTAssertEqual(old.translations.count, 1)
    }

    @MainActor
    func testChangingCLIWhilePreparingDrainsOldConnectionWithoutReplayingDraft() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.input = "Synthetic draft for the original CLI"
        model.translate()
        let old = try XCTUnwrap(fixture.helpers.first)
        old.event("ready")
        let oldLoad = try XCTUnwrap(old.configurationLoads.last)
        model.selectedCLI = fixture.alternateExecutable.path

        XCTAssertEqual(old.stopCount, 1)
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertTrue(old.translations.isEmpty)
        old.event("completed", id: oldLoad,
                  payload: ["config": .object(ProductTestHarness.configuration())])
        old.stopped()
        let replacement = try fixture.ready()
        XCTAssertEqual(fixture.helpers.count, 2)
        XCTAssertEqual(replacement.selectedExecutable, fixture.alternateExecutable)
        XCTAssertTrue(model.nativeTranslation)
        XCTAssertEqual(fixture.preferences.string(forKey: "selectedCodexPath"), fixture.alternateExecutable.path)
        XCTAssertTrue(replacement.translations.isEmpty)
        XCTAssertEqual(model.productPhase, .idle,
                       "Changing CLI must not leave a discarded draft permanently preparing.")
        old.failure(.translationOutcomeUnknown)
        old.stopped()
        XCTAssertTrue(model.ready)
        XCTAssertTrue(model.connected)
        XCTAssertTrue(replacement.translations.isEmpty)
    }

    @MainActor
    func testHistoryRequestedBeforeReadyLoadsOnlyAfterSettingsWithoutModelCall() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.loadHistory()
        let helper = try XCTUnwrap(fixture.helpers.first)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        helper.event("ready")
        XCTAssertTrue(helper.historyLoads.isEmpty)
        try fixture.finishConfiguration(on: helper)

        XCTAssertEqual(helper.operations, ["start.translation", "config.load", "history.load"])
        XCTAssertEqual(helper.historyLoads.first?.pageSize, 20)
        XCTAssertEqual(helper.historyLoads.first?.cursor, .null)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testHistoryPagesAppendWithStableConditionsAndSearchUsesServerResults() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.loadHistory()
        let first = try XCTUnwrap(helper.historyLoads.last)
        let cursor: JSONValue = .object(["offset": .integer(2), "revision": .string(String(repeating: "a", count: 64))])
        let repeated = ProductTestHarness.historyEntry(input: "Repeated source", output: "Repeated result")
        helper.event("completed", id: first.id,
                     payload: ProductTestHarness.historyPage(entries: [repeated, repeated], total: 4, nextOffset: 2))
        XCTAssertEqual(model.historyPage.count, 2)
        XCTAssertTrue(model.hasNextHistoryPage)
        model.loadHistory(next: true)
        let second = try XCTUnwrap(helper.historyLoads.last)
        XCTAssertEqual(second.cursor, cursor)
        XCTAssertEqual(second.query, "")
        XCTAssertEqual(second.kind, "all")
        XCTAssertNotEqual(first.id, second.id)
        helper.event("completed", id: second.id, payload: ProductTestHarness.historyPage(entries: [
                ProductTestHarness.historyEntry(input: "Synthetic word", output: "Fixture meaning", kind: "dict"),
                repeated
            ], total: 4))

        XCTAssertEqual(model.historyPage.count, 4)
        XCTAssertEqual(Set(model.historyPage.map(\.id)).count, 4)
        XCTAssertFalse(model.hasNextHistoryPage)
        XCTAssertFalse(model.historyBusy)
        XCTAssertEqual(model.historyTotal, 4)
        model.historySearch = "FIXTURE MEANING"
        XCTAssertTrue(model.filteredHistory.isEmpty, "Do not locally filter a previously loaded page.")
        model.submitHistorySearch()
        let query = try XCTUnwrap(helper.historyLoads.last)
        XCTAssertEqual(query.query, "FIXTURE MEANING")
        XCTAssertEqual(query.cursor, .null)
        helper.event("completed", id: query.id, payload: ProductTestHarness.historyPage(entries: [
            ProductTestHarness.historyEntry(input: "Previously unloaded match", output: "Fixture meaning", kind: "dict")
        ], total: 1))
        XCTAssertEqual(model.filteredHistory.map(\.input), ["Previously unloaded match"])
        model.historyFilter = "text"
        XCTAssertTrue(model.filteredHistory.isEmpty)
        XCTAssertEqual(helper.historyLoads.last?.kind, "text")
        XCTAssertEqual(helper.historyLoads.last?.query, "FIXTURE MEANING")
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [], total: 0))
        XCTAssertEqual(model.historyTotal, 0)
        XCTAssertEqual(helper.historyLoads.count, 4)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testClearHistoryResetsPaginationAndNextExplicitReloadStartsAtBeginning() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.loadHistory()
        helper.event("completed", id: try XCTUnwrap(helper.historyLoads.last?.id),
                     payload: ProductTestHarness.historyPage(entries: [
                        ProductTestHarness.historyEntry(input: "Synthetic", output: "Saved")
                     ], total: 2, nextOffset: 1))
        fixture.model.clearHistory()
        XCTAssertTrue(fixture.model.historyBusy)
        helper.event("completed", id: try XCTUnwrap(helper.historyClears.last),
                     payload: ["cleared": .bool(true), "revision": .string(String(repeating: "b", count: 64))])

        XCTAssertTrue(fixture.model.historyPage.isEmpty)
        XCTAssertFalse(fixture.model.hasNextHistoryPage)
        XCTAssertFalse(fixture.model.historyBusy)
        XCTAssertEqual(fixture.model.historyTotal, 0)
        fixture.model.loadHistory()
        XCTAssertEqual(helper.historyLoads.last?.cursor, .null)
        XCTAssertEqual(helper.historyClears.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testLanguageAndThemePersistWithoutStartingAnything() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        model.interfaceLanguage = "zh"
        model.appearance = "dark"
        model.persistPresentation()
        let restored = ProbeModel(
            preferences: fixture.preferences,
            makeConnection: { notice in
                XCTFail("Loading presentation must not construct a connection.")
                return ProductTestHelper(notice: notice)
            },
            runtimeProvider: {
                XCTFail("Loading presentation must not request a runtime.")
                throw ProbeError.bundleMissing
            })
        restored.loadPresentation()

        XCTAssertEqual(restored.interfaceLanguage, "zh")
        XCTAssertTrue(restored.usesChinese)
        XCTAssertEqual(restored.text("English", "中文"), "中文")
        XCTAssertEqual(restored.preferredColorScheme, .dark)
        restored.interfaceLanguage = "en"
        restored.appearance = "light"
        restored.persistPresentation()
        XCTAssertFalse(restored.usesChinese)
        XCTAssertEqual(restored.preferredColorScheme, .light)
        XCTAssertEqual(fixture.preferences.string(forKey: "interfaceLanguage"), "en")
        XCTAssertEqual(fixture.preferences.string(forKey: "appearance"), "light")
        restored.appearance = "system"
        XCTAssertNil(restored.preferredColorScheme)
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeRequests, 0)
        XCTAssertEqual(fixture.locatorRequests, 0)
        XCTAssertEqual(restored.permissions, "Not checked.")
    }

    @MainActor
    func testDiagnosticPreferencesAreNeitherRestoredNorOverwrittenByCLIScan() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.preferences.set("zh", forKey: "interfaceLanguage")
        fixture.preferences.set("dark", forKey: "appearance")
        var locatorCalls = 0
        let diagnostic = ProbeModel(
            preferences: fixture.preferences, persistsPreferences: false,
            makeConnection: { notice in
                XCTFail("Diagnostic presentation and path inspection must not construct a helper.")
                return ProductTestHelper(notice: notice)
            },
            runtimeProvider: {
                XCTFail("Diagnostic presentation and path inspection must not request a runtime.")
                throw ProbeError.bundleMissing
            },
            locateCandidates: { name, userURL in
                locatorCalls += 1
                XCTAssertEqual(name, "codex")
                XCTAssertNil(userURL, "Diagnostics must not restore the product's saved executable.")
                return [CLICandidate(url: fixture.alternateExecutable, executable: true)]
            })
        diagnostic.loadPresentation()
        XCTAssertEqual(diagnostic.interfaceLanguage, "system")
        XCTAssertEqual(diagnostic.appearance, "system")
        XCTAssertEqual(diagnostic.selectedCLI, "")
        XCTAssertEqual(locatorCalls, 0)

        diagnostic.locateCLI()
        diagnostic.interfaceLanguage = "en"
        diagnostic.appearance = "light"
        diagnostic.persistPresentation()
        XCTAssertEqual(locatorCalls, 1)
        XCTAssertEqual(diagnostic.selectedCLI, fixture.alternateExecutable.path)
        XCTAssertEqual(fixture.preferences.string(forKey: "interfaceLanguage"), "zh")
        XCTAssertEqual(fixture.preferences.string(forKey: "appearance"), "dark")
        XCTAssertEqual(fixture.preferences.string(forKey: "selectedCodexPath"), fixture.executable.path)
        XCTAssertFalse(diagnostic.hasProcesses)
        XCTAssertFalse(diagnostic.cliBusy)
        XCTAssertEqual(diagnostic.permissions, "Not checked.")

        fixture.model.openProduct()
        XCTAssertEqual(fixture.model.interfaceLanguage, "zh")
        XCTAssertEqual(fixture.model.preferredColorScheme, .dark)
        XCTAssertEqual(fixture.model.selectedCLI, fixture.executable.path)
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty })
    }

    @MainActor
    func testEmptyWhitespaceAndOversizedUTF8InputNeverStartAnything() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        for invalid in ["", " \n\t ", String(repeating: "界", count: 2731)] {
            fixture.model.input = invalid
            fixture.model.translate()
            XCTAssertEqual(fixture.model.productPhase, .failed)
        }
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeRequests, 0)
        XCTAssertEqual(fixture.locatorRequests, 0)
        XCTAssertFalse(fixture.model.hasProcesses)
    }

    @MainActor
    private func drainRenderingQueue() async {
        let drained = expectation(description: "Main queue passed the buffered-render deadline")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { drained.fulfill() }
        await fulfillment(of: [drained], timeout: 2)
    }
}
