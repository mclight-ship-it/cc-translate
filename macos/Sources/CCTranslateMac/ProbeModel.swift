import AppKit
import Combine
import SwiftUI
import CCTranslateSupport

enum TranslationPhase: Equatable {
    case idle, preparing, translating, completed, cancelled, failed
}

enum HistoryPhase: Equatable {
    case idle, waiting, loading, loaded, clearing, failed, stale
}

enum SummaryPreferencePhase: Equatable {
    case idle, saving, readingBack, saved, differentReadback
    case failed(String)
}

enum SelectionMonitorState: Equatable {
    case off, active, diagnostic, requiresPermissions, secureInput, temporarilyUnavailable
}

@MainActor
final class ProbeModel: ObservableObject {
    private enum ConnectionMode { case diagnostic, configuration, translation }
    @Published var input = ""
    @Published private(set) var output = ""
    @Published private(set) var status = "Not connected. Diagnostics and native translation require separate explicit connections."
    @Published private(set) var ready = false
    @Published private(set) var connected = false
    @Published private(set) var active = false
    @Published private(set) var nativeTranslation = false
    @Published private(set) var settingsReady = false
    @Published private(set) var settingsBusy = false
    @Published private(set) var defaultsPhase: SettingsDefaultsPhase = .idle
    private var settingsDefaults: SettingsDefaults?
    private var defaultsRequestID: String?
    private var defaultsDrafts: [String: String] = [:]
    private var defaultsProviderDrafts: [TranslationProvider: ProviderDraft] = [:]
    @Published private(set) var historyEnabled = true
    @Published private(set) var historyLimit = HistoryLimitPreference()
    @Published private(set) var inputLimit = IntegerPreference(
        supported: TextInputPreflight.characterRange, invalidReadbackCode: "invalid_max_chars")
    @Published private(set) var copyInterval = NumericPreference<Double>(
        supported: Double.leastNonzeroMagnitude...Double(ConfigurationDocument.maxNumber),
        invalidReadbackCode: "invalid_copy_interval")
    @Published private(set) var activeCopyInterval = DoubleCopyInterval.standard
    static let copyIntervalHintKey = "lastConfirmedCopyIntervalSeconds"
    @Published private(set) var summaryEnabled: Bool?
    @Published private(set) var summaryPreferencePhase: SummaryPreferencePhase = .idle
    private var summarySaveRequest: (id: String, value: Bool)?
    private var summaryReadRequest: (id: String, expected: Bool?)?
    @Published var direction = "auto" {
        didSet { if !loadingConfiguration { directionEdited = true } }
    }

    func refreshDictionary() {
        let reconcile = dictionary.phase == .unknown
        dictionary.requestStatusWhenReady()
        openProduct()
        if reconcile && ready && !settingsBusy { loadSettings() }
        else if ready && settingsReady && !settingsBusy { dictionary.connectionReady() }
    }

    func setDictionaryEnabled(_ enabled: Bool) {
        guard ready, settingsReady, !settingsBusy, !dictionary.busy, dictionary.phase != .unknown,
              var config = savedConfiguration, let connection else {
            dictionary.report("Wait for the current settings or dictionary operation to finish.",
                              "请等待当前设置或词典操作完成。")
            return
        }
        config["local_dictionary_enabled"] = .bool(enabled)
        settingsBusy = true
        let id = UUID().uuidString
        configSaveID = id
        connection.saveConfiguration(config, id: id)
        dictionary.requestStatusWhenReady()
    }
    @Published var modelProfile = "auto-fast" {
        didSet { if !loadingConfiguration { modelEdited = true } }
    }
    @Published private(set) var modelSettings = CodexModelSettings()
    @Published private(set) var modelCatalog = ModelCatalogState()
    @Published private(set) var translationProvider: TranslationProvider = .codex
    @Published private(set) var pendingProvider: TranslationProvider?
    @Published private(set) var providerMessage = ""
    private var providerChangeID: String?
    private struct ProviderDraft {
        var settings: CodexModelSettings
        var profile: String
        var edited: Bool
    }
    private var providerDrafts: [TranslationProvider: ProviderDraft] = [:]
    @Published var translatePassiveSelections = false {
        didSet {
            monitor.setClipboardFallbackEnabled(translatePassiveSelections && !monitorAXOnly)
            if monitorEnabled { updateMonitorStatus() }
        }
    }
    @Published var interfaceLanguage = "system"
    @Published var appearance = "system"
    @Published var nativeTextScale: NativeTextScale = .standard
    @Published var resultPlacement: NativeResultPlacement = .remembered
    @Published var resultPinned = true
    @Published private(set) var captureTranslationMode: CaptureTranslationMode = .text
    private(set) var rememberedResultFrame: NSRect?
    @Published var historySearch = "" {
        didSet { if historySearch != oldValue { queueHistorySearch(debounce: true) } }
    }
    @Published var historyFilter = "all" {
        didSet { if historyFilter != oldValue { queueHistorySearch(debounce: false) } }
    }
    @Published private(set) var productPhase: TranslationPhase = .idle
    @Published private(set) var productMessage = ""
    @Published private(set) var needsCLI = false
    @Published private(set) var monitorEnabled = false
    @Published private(set) var monitorRequestedEnabled = false
    static let selectionMonitorPreferenceKey = "nativeSelectionShortcutEnabled"
    @Published private(set) var resultKind = "text"
    @Published private(set) var isLocalDictionaryResult = false
    @Published private(set) var resultSources: [DictionarySource] = []
    @Published private(set) var resultInput = ""
    @Published private(set) var resultHasOriginalInput = true
    @Published private(set) var primaryResult = ""
    private(set) var translationOrigin = "text"
    @Published private(set) var historyPage: [HistoryRow] = []
    @Published private(set) var historyStatus = "History has not been read."
    @Published private(set) var historyBusy = false
    @Published private(set) var historyPhase: HistoryPhase = .idle
    @Published private(set) var historyTotal: Int? = nil
    @Published private(set) var hasNextHistoryPage = false
    @Published private(set) var permissions = "Not checked."
    @Published private(set) var permissionSnapshot: PermissionSnapshot?
    @Published private(set) var monitorStatus = "Passive double Cmd+C monitor is stopped."
    @Published var cliName = "codex" {
        didSet { if cliName != oldValue { invalidateModelCatalogScope() } }
    }
    @Published private(set) var candidates: [CLICandidate] = []
    @Published private(set) var cliChangeDeferred = false
    @Published var selectedCLI = "" {
        didSet {
            if !CodexModelSettings.sameID(selectedCLI, oldValue) {
                if !modelCatalog.matches(scope: selectedCLI) { invalidateModelCatalogScope() }
                cliStatus = "Selection changed; not executed. Run --version explicitly. Authentication: unknown."
                if connected && connectionMode != .diagnostic && !locatingForUpgrade {
                    if dictionary.ownsInstallation {
                        cliChangeDeferred = true
                    } else {
                        draft = nil
                        if preparing || active {
                            productPhase = .cancelled
                            productMessage = text("Connection changed; translation cancelled.",
                                                  "连接已更改，翻译已取消。")
                        }
                        openAfterStop = true
                        stopHelper()
                    }
                }
            }
        }
    }
    @Published private(set) var cliStatus = "Not located. Authentication: unknown."
    @Published private(set) var cliBusy = false
    let dictionary: DictionaryModel
    let dictionarySearch = DictionarySearchModel()
    let plainPaste: PlainPasteModel
    let captureShortcut: CaptureShortcutModel
    private var captureShortcutObservation: AnyCancellable?
    let imageTranslation: ImageTranslationState
    private var imageObservation: AnyCancellable?
    private var imageTranslationSupported = false
    private var publishingImageRequest = false
    let screen = ScreenProbe()
    let monitor: any PassiveSelectionMonitoring
    private var monitorAXOnly = false
    private var monitorRecoveryTimer: Timer?
    var onSelection: ((SelectionResult) -> Void)?
    var onQuickInputRequested: (() -> Void)?
    var onStopped: (() -> Void)?
    var onTranslationResult: ((String) -> Void)?
    var onTranslationStarted: (() -> Void)?
    var onConfigurationRequired: (() -> Void)?
    var onPresentationChanged: (() -> Void)?
    struct HistoryRow: Identifiable {
        let id: String
        let input: String
        let output: String
        var kind = "text"
        var timestamp = ""
        var signature = ""
        var hasOriginalInput = true
        var isLocalDictionary: Bool { signature.hasPrefix("local-dictionary|") }

        @MainActor
        static func decode(_ value: JSONValue, id: String) throws -> HistoryRow {
            try HistoryDocument.validateEntry(value)
            guard let entry = value.object else { throw ProbeError.invalidPayload }
            let rawKind = entry["kind"]?.string ?? ""
            let kind = ProbeModel.historyKinds.contains(rawKind) ? rawKind :
                entry["is_code"] == .bool(true) ? "code" : entry["is_dict"] == .bool(true) ? "dict" : "text"
            return HistoryRow(id: id, input: entry["input"]?.string ?? "", output: entry["output"]?.string ?? "",
                              kind: kind, timestamp: entry["ts"]?.string ?? "", signature: entry["sig"]?.string ?? "",
                              hasOriginalInput: entry["input"]?.string != nil)
        }
    }
    private struct HistoryCriteria: Equatable {
        let query: String
        let kind: String
    }
    private struct HistoryRead {
        let id: String
        let generation: UUID
        let criteria: HistoryCriteria
        let cursor: JSONValue
        let offset: Int
        let appending: Bool
    }
    private struct Draft {
        let text: String
        let origin: String
        let useCache: Bool
        var direction: String
        var model: String
        let language: String
        var useSavedDirection: Bool
        var useSavedModel: Bool
        var provider: TranslationProvider = .codex
        var useSavedProvider = false
        var configurationSaved = false
        var lookupFinished = false
        var action: ActionDraft?
        var imageIntent: UUID?
    }
    private struct ActionDraft {
        let action: ResultAction
        let targetLanguage: String?
        let generation: UUID
        let prefix: String
        let title: String
    }
    private var draft: Draft? {
        didSet {
            if let old = oldValue?.imageIntent, old != draft?.imageIntent {
                imageTranslation.discardPending(old)
            }
        }
    }
    private var dictionaryLookup: (id: String, draft: Draft, cancelled: Bool)?
    private var upgradingForDraft = false
    private var locatingForUpgrade = false
    private var activeAction: (id: String, content: ActionDraft)?
    private var resultGeneration = UUID()
    private(set) var translationIntentID = UUID()
    private var cancelledPreparation: (intent: UUID, connection: UUID)?
    private var presentationLoaded = false
    private var loadingConfiguration = false
    private var directionEdited = false
    private var modelEdited = false
    private var openAfterStop = false
    private var historyRequested = false
    private var historySearchActive = false
    private var historyGeneration = UUID()
    private var historyDebounce: DispatchWorkItem?
    private var queuedHistory: HistoryCriteria?
    private var loadedHistory: HistoryCriteria?
    private var historyRead: HistoryRead?
    private var hideCurrentOutput = false
    private var bufferedDelta = ""
    private var renderUpdate: DispatchWorkItem?
    private var dictionaryObservation: AnyCancellable?
    private var plainPasteObservation: AnyCancellable?
    private var plainPasteRestoreStarted = false
    private var plainPasteConfigAfterStop = false
    private var plainPasteReconcileOnLoad = false
    private var catalogReconnectIntent: UUID?
    private var advancingCatalog = false
    private var catalogSupported = false
    private var catalogNeedsReconnect = false
    private var catalogWasLastRequest = false
    private var catalogPreservesPreparation = false
    private(set) var catalogShutDown = false
    private var connectedCLI = ""
    private var connectedProvider: TranslationProvider?
    private let preferences: UserDefaults?
    private var persistsPreferences: Bool
    private let readPermissions: @MainActor () -> PermissionSnapshot
    private let makeConnection: (@escaping (HelperNotice) -> Void) -> AppHelperClient
    private let runtimeProvider: () throws -> BundleRuntime
    private let locateCandidates: (String, URL?) -> [CLICandidate]
    private let writeClipboard: (String) -> Bool
    private let homeDirectory: URL?
    private var savedConfiguration: [String: JSONValue]?
    private var configLoadID: String?
    private var configSaveID: String?
    private var historyClearID: String?
    private var historyClearGeneration: UUID?
    private var historyCursor: JSONValue = .null
    private var connection: AppHelperClient?
    private var connectionMode: ConnectionMode = .diagnostic
    private var connectionID = UUID()
    private var latest = LatestRequest()
    private var pending = Set<String>()
    private var error: ProbeError?
    private var stopping = false
    private var cliRun: CLIVersionRun?
    private var cliGeneration = UUID()
    private var userCLI: [String: URL] = [:]
    var hasProcesses: Bool {
        connection != nil || cliRun != nil || dictionary.busy || plainPaste.serviceState.busy ||
            imageTranslation.working
    }
    var preparing: Bool { productPhase == .preparing }
    var translatingImage: Bool { draft?.imageIntent != nil || imageTranslation.owns(requestID: latest.id) }
    var canRunResultAction: Bool {
        !primaryResult.isEmpty && !active && !preparing && !stopping
    }
    static let targetLanguages = [
        ("zh", "Simplified Chinese", "简体中文"), ("en", "English", "英语"),
        ("ja", "Japanese", "日语"), ("ko", "Korean", "韩语"),
        ("fr", "French", "法语"), ("de", "German", "德语"), ("es", "Spanish", "西班牙语")
    ]
    func resultActionTitle(_ action: ResultAction, targetLanguage: String? = nil) -> String {
        switch action {
        case .concise: return text("Make concise", "精简表达")
        case .formal: return text("Make formal", "正式表达")
        case .summary: return text("Summarize", "生成摘要")
        case .explainCode: return text("Explain code", "解释代码")
        case .asText: return text("Translate as text", "按普通文本翻译")
        case .retranslate:
            if let language = Self.targetLanguages.first(where: { $0.0 == targetLanguage }) {
                let name = text(language.1, language.2)
                return text("Translate to \(name)", "翻译为\(name)")
            }
            return text("Translate to…", "翻译为…")
        }
    }
    var preferredColorScheme: ColorScheme? {
        appearance == "dark" ? .dark : appearance == "light" ? .light : nil
    }
    static let historyKinds = ["text", "dict", "code", "ocr"]
    var filteredHistory: [HistoryRow] { historyPage }
    var usesChinese: Bool {
        interfaceLanguage == "zh" ||
        (interfaceLanguage == "system" && (Locale.preferredLanguages.first?.hasPrefix("zh") ?? false))
    }
    func text(_ english: String, _ chinese: String) -> String { usesChinese ? chinese : english }

    init(preferences: UserDefaults? = nil, persistsPreferences: Bool = true,
         makeConnection: @escaping (@escaping (HelperNotice) -> Void) -> AppHelperClient = {
             HelperConnection(notice: $0)
         }, runtimeProvider: @escaping () throws -> BundleRuntime = { try BundleRuntime() },
         locateCandidates: @escaping (String, URL?) -> [CLICandidate] = {
             CLILocator.candidates(name: $0, userURL: $1)
         }, dictionaryDownloader: DictionaryDownloading? = nil,
         writeClipboard: ((String) -> Bool)? = nil, homeDirectory: URL? = nil,
         plainPaste: PlainPasteModel? = nil, imageTranslation: ImageTranslationState? = nil,
         selectionMonitor: (any PassiveSelectionMonitoring)? = nil,
         captureShortcut: CaptureShortcutModel? = nil,
         readPermissions: @escaping @MainActor () -> PermissionSnapshot = { Permissions.snapshot() }) {
        dictionary = DictionaryModel(downloader: dictionaryDownloader ?? DictionaryDownloader())
        self.plainPaste = plainPaste ?? PlainPasteModel()
        self.captureShortcut = captureShortcut ?? CaptureShortcutModel(
            preferences: preferences, persistsPreferences: persistsPreferences)
        self.imageTranslation = imageTranslation ?? ImageTranslationState(factory: NativeImageAttachment.make)
        monitor = selectionMonitor ?? PassiveCopyMonitor()
        self.homeDirectory = homeDirectory
        self.writeClipboard = writeClipboard ?? {
            NSPasteboard.general.clearContents()
            return NSPasteboard.general.setString($0, forType: .string)
        }
        self.preferences = preferences
        self.persistsPreferences = persistsPreferences
        self.readPermissions = readPermissions
        self.makeConnection = makeConnection
        self.runtimeProvider = runtimeProvider
        self.locateCandidates = locateCandidates
        monitor.onSelection = { [weak self] result in
            guard let self, self.monitorEnabled, self.monitor.running else { return }
            self.onSelection?(result)
        }
        monitor.onStop = { [weak self] reason in
            self?.monitorStatus = reason
            self?.monitorEnabled = false
        }
        dictionary.send = { [weak self] request, id in
            guard let self, let connection = self.connection, self.error == nil,
                  self.connectionMode != .diagnostic else { return false }
            _ = connection.dictionary(request, id: id, timeout: 60)
            return true
        }
        dictionary.onConfigurationChanged = { [weak self] in self?.loadSettings() }
        dictionarySearch.send = { [weak self] request, id in
            guard let self, let connection = self.connection, self.error == nil,
                  self.ready, self.settingsReady, !self.settingsBusy, !self.stopping,
                  !self.dictionary.committing, self.connectionMode != .diagnostic else { return false }
            _ = connection.dictionary(request, id: id, timeout: 25)
            return true
        }
        dictionary.canInstall = { [weak self] in
            guard let self else { return false }
            return self.ready && self.settingsReady && !self.settingsBusy && !self.stopping
        }
        dictionaryObservation = dictionary.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }
        plainPasteObservation = self.plainPaste.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }
        captureShortcutObservation = self.captureShortcut.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }
        imageObservation = self.imageTranslation.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }
        self.imageTranslation.onPrepared = { [weak self] intent in
            guard let self, self.draft?.imageIntent == intent, self.translationIntentID == intent else { return }
            self.openProduct()
            self.resumeTranslation()
        }
        self.imageTranslation.onPreparationFailed = { [weak self] intent in
            guard let self, self.draft?.imageIntent == intent else { return }
            self.failPreparation(self.text("The selected image could not be prepared. Nothing was sent. Try a smaller region.",
                                          "无法准备所选图片，未发送任何内容。请尝试较小选区。"))
        }
        self.imageTranslation.onSettled = { [weak self] in self?.notifyStoppedIfIdle() }
        self.plainPaste.onDrained = { [weak self] in
            DispatchQueue.main.async { [weak self] in
                self?.resumeModelCatalog()
                self?.notifyStoppedIfIdle()
            }
        }
        dictionary.onSettled = { [weak self] in
            self?.flushPlainPastePreference()
            self?.resumeTranslation()
            self?.resumeDeferredCLIConnection()
            self?.notifyStoppedIfIdle()
        }
    }

    func persistPresentation() {
        guard persistsPreferences else { return }
        let defaults = preferences ?? .standard
        defaults.set(interfaceLanguage, forKey: "interfaceLanguage")
        defaults.set(appearance, forKey: "appearance")
        defaults.set(nativeTextScale.rawValue, forKey: NativeTextScale.preferenceKey)
        defaults.set(resultPlacement.rawValue, forKey: NativeResultPlacement.preferenceKey)
        defaults.set(captureTranslationMode.rawValue, forKey: CaptureTranslationMode.preferenceKey)
        if let provider = TranslationProvider.allCases.first(where: { $0.cliName == cliName }),
           !selectedCLI.isEmpty {
            defaults.set(selectedCLI, forKey: provider.pathPreferenceKey)
        }
        onPresentationChanged?()
    }

    func loadPresentation() {
        if !presentationLoaded {
            if persistsPreferences {
                let defaults = preferences ?? .standard
                monitorRequestedEnabled = defaults.bool(forKey: Self.selectionMonitorPreferenceKey)
                if monitorRequestedEnabled { translatePassiveSelections = true }
                interfaceLanguage = defaults.string(forKey: "interfaceLanguage") ?? "system"
                appearance = defaults.string(forKey: "appearance") ?? "system"
                nativeTextScale = NativeTextScale(
                    rawValue: defaults.string(forKey: NativeTextScale.preferenceKey) ?? ""
                ) ?? .standard
                resultPlacement = NativeResultPlacement(
                    rawValue: defaults.string(forKey: NativeResultPlacement.preferenceKey) ?? ""
                ) ?? .remembered
                captureTranslationMode = CaptureTranslationMode(
                    rawValue: defaults.string(forKey: CaptureTranslationMode.preferenceKey) ?? ""
                ) ?? .text
                rememberedResultFrame = NativeResultPlacement.restoredFrame(
                    defaults.string(forKey: NativeResultPlacement.frameKey))
                // The confirmed local hint restores native timing without starting a helper.
                // The next configuration read remains authoritative.
                if let raw = defaults.object(forKey: Self.copyIntervalHintKey) as? String,
                   let seconds = Double(raw), copyInterval.supported.contains(seconds),
                   let interval = DoubleCopyInterval(seconds: seconds) {
                    applyCopyInterval(interval, persistHint: false)
                }
                for provider in TranslationProvider.allCases {
                    if let custom = defaults.string(forKey: provider.customModelPreferenceKey) {
                        if provider == translationProvider { modelSettings.restoreCustom(custom) }
                        else {
                            var settings = CodexModelSettings(provider: provider)
                            settings.restoreCustom(custom)
                            providerDrafts[provider] = ProviderDraft(
                                settings: settings, profile: provider.defaultModel, edited: false)
                        }
                    }
                    if let saved = defaults.string(forKey: provider.pathPreferenceKey), !saved.isEmpty {
                        userCLI[provider.cliName] = URL(fileURLWithPath: saved)
                    }
                }
                if let raw = defaults.string(forKey: "lastConfirmedTranslationProvider"),
                   let provider = TranslationProvider(rawValue: raw), provider != translationProvider {
                    adoptProvider(provider, profile: provider.defaultModel, locate: false)
                }
            }
            presentationLoaded = true
            captureShortcut.restore()
            if monitorRequestedEnabled { restoreSelectionMonitorIfNeeded() }
            onPresentationChanged?()
        }
    }

    func chooseCaptureTranslationMode(_ mode: CaptureTranslationMode) {
        loadPresentation()
        captureTranslationMode = mode
        persistPresentation()
    }

    func openProduct() {
        loadPresentation()
        if connected {
            if connectionMode == .diagnostic || error != nil ||
                (catalogNeedsReconnect && !dictionary.ownsInstallation) {
                openAfterStop = true
                stopHelper(preservePendingHistory: true)
            }
            return
        }
        if cliName != translationProvider.cliName {
            candidates = []
            selectedCLI = ""
        }
        cliName = translationProvider.cliName
        if !candidates.contains(where: { $0.url.path == selectedCLI && $0.executable }) {
            locateCLI()
        }
        needsCLI = selectedCLI.isEmpty
        persistPresentation()
        if needsCLI { startConnection(mode: .configuration) }
        else { startNativeTranslation() }
    }

    var canChangeProvider: Bool { canApplyModelSetting && !modelCatalog.busy && !cliBusy }

    func selectTranslationProvider(_ provider: TranslationProvider) {
        guard provider != translationProvider else { return }
        guard canChangeProvider, var config = savedConfiguration, let connection else {
            providerMessage = text("Finish the current operation before changing services.",
                                   "请先完成当前操作，再切换服务。")
            return
        }
        config["model_provider"] = .string(provider.rawValue)
        let id = UUID().uuidString
        pendingProvider = provider
        providerChangeID = id
        providerMessage = text("Saving service selection…", "正在保存服务选择…")
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    private func adoptProvider(_ provider: TranslationProvider, profile: String, locate: Bool = true) {
        guard provider != translationProvider else { return }
        providerDrafts[translationProvider] = ProviderDraft(
            settings: modelSettings, profile: modelProfile, edited: modelEdited)
        if !selectedCLI.isEmpty { userCLI[cliName] = URL(fileURLWithPath: selectedCLI) }
        let restored = providerDrafts[provider] ?? ProviderDraft(
            settings: CodexModelSettings(provider: provider), profile: profile, edited: false)
        let wasLoading = loadingConfiguration
        let wasLocating = locatingForUpgrade
        loadingConfiguration = true
        locatingForUpgrade = true
        defer {
            loadingConfiguration = wasLoading
            locatingForUpgrade = wasLocating
        }
        translationProvider = provider
        modelSettings = restored.settings
        modelProfile = restored.edited ? restored.profile : profile
        modelEdited = restored.edited
        cliName = provider.cliName
        modelCatalog = ModelCatalogState()
        catalogWasLastRequest = false
        catalogReconnectIntent = nil
        if locate {
            candidates = locateCandidates(cliName, userCLI[cliName])
            selectedCLI = candidates.first(where: \.executable)?.url.path ?? ""
        } else {
            candidates = []
            selectedCLI = ""
        }
        needsCLI = selectedCLI.isEmpty
        if connected, connectionMode == .translation, dictionary.ownsInstallation {
            cliChangeDeferred = true
        }
    }

    private func failProviderChange(_ code: String) {
        guard pendingProvider != nil else { return }
        pendingProvider = nil
        providerChangeID = nil
        providerMessage = text("Service change was not confirmed (\(code)). Reload settings to check.",
                               "服务切换尚未确认（\(code)）。请重新读取设置进行确认。")
    }

    func rememberResultFrame(_ frame: NSRect) {
        rememberedResultFrame = frame
        guard persistsPreferences else { return }
        (preferences ?? .standard).set(NSStringFromRect(frame), forKey: NativeResultPlacement.frameKey)
    }

    func startHelper() {
        startConnection(mode: .diagnostic)
    }

    func restorePlainPastePreferenceIfNeeded() {
        guard !plainPasteRestoreStarted, !plainPaste.isShutDown, persistsPreferences else { return }
        plainPasteRestoreStarted = true
        guard (preferences ?? .standard).bool(forKey: "plainPasteOptInHint") else { return }
        plainPaste.beginRestore()
        loadPlainPasteConfiguration()
    }

    func setPlainPasteEnabled(_ enabled: Bool) {
        guard !plainPaste.isShutDown else { return }
        plainPaste.choose(enabled)
        persistPlainPasteHint(false)
        if !connected || error != nil || stopping || connectionMode == .diagnostic { loadPlainPasteConfiguration() }
        else if ready && !settingsBusy && !settingsReady { loadSettings() }
        flushPlainPastePreference()
    }

    func reloadPlainPastePreference() {
        guard !plainPaste.isShutDown else { return }
        loadPlainPasteConfiguration(reconcile: true)
    }

    private func loadPlainPasteConfiguration(reconcile: Bool = false) {
        guard !plainPaste.isShutDown else { return }
        if !connected {
            // The mirror permits only a config read, never CLI discovery or registration.
            plainPasteReconcileOnLoad = reconcile
            startConnection(mode: .configuration)
        } else if error != nil || stopping || connectionMode == .diagnostic {
            plainPasteConfigAfterStop = true
            stopHelper()
        } else if ready && !settingsBusy && !stopping && connectionMode != .diagnostic {
            loadSettings(plainReconcile: reconcile)
        }
    }

    private func persistPlainPasteHint(_ enabled: Bool) {
        guard persistsPreferences else { return }
        (preferences ?? .standard).set(enabled, forKey: "plainPasteOptInHint")
    }

    private func flushPlainPastePreference() {
        guard plainPaste.preference.canWrite, !plainPaste.isShutDown,
              ready, settingsReady, !settingsBusy, !stopping, !dictionary.committing,
              connectionMode != .diagnostic, var config = savedConfiguration, let connection else { return }
        let id = UUID().uuidString
        guard let enabled = plainPaste.beginSave(id: id) else { return }
        config["plain_text_paste_enabled"] = .bool(enabled)
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func startNativeTranslation() {
        guard cliName == translationProvider.cliName, candidates.contains(where: {
            $0.url.path == selectedCLI && $0.executable
        }) else {
            status = text("Choose a \(translationProvider.displayName) executable in Settings.",
                          "请在设置中选择 \(translationProvider.displayName) 可执行文件。")
            return
        }
        startConnection(mode: .translation)
    }

    private func startConnection(mode: ConnectionMode, preserveProduct: Bool = false) {
        guard connection == nil else { return }
        let imageIntent = draft?.imageIntent
        do {
            let runtime = try runtimeProvider()
            if let imageIntent, draft?.imageIntent != imageIntent || imageTranslation.isShutDown { return }
            if preserveProduct {
                guard modelCatalog.pending, modelCatalog.matches(scope: selectedCLI),
                      !catalogShutDown else { return }
            }
            let command = selectedCLI
            connectionID = UUID()
            let id = connectionID
            let connection = makeConnection { [weak self] notice in
                MainActor.assumeIsolated {
                    guard let self = self, self.connectionID == id else { return }
                    self.receive(notice)
                }
            }
            if let imageIntent, draft?.imageIntent != imageIntent || imageTranslation.isShutDown { return }
            if preserveProduct {
                guard modelCatalog.pending, modelCatalog.matches(scope: selectedCLI),
                      CodexModelSettings.sameID(command, selectedCLI), !catalogShutDown else { return }
            }
            self.connection = connection
            error = nil
            stopping = false
            connected = true
            connectionMode = mode
            nativeTranslation = mode == .translation
            connectedCLI = mode == .translation ? command : ""
            connectedProvider = mode == .translation ? translationProvider : nil
            catalogSupported = false
            imageTranslationSupported = false
            catalogNeedsReconnect = false
            catalogPreservesPreparation = preserveProduct
            settingsReady = false
            if !preserveProduct && (mode == .diagnostic || primaryResult.isEmpty) { output = "" }
            activeAction = nil
            latest.select(nil)
            status = "Starting bundled isolated Python; waiting for ready..."
            if mode == .translation {
                let home = homeDirectory ?? URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
                var environment = ProcessInfo.processInfo.environment
                environment["HOME"] = home.path
                let inheritedPath = environment["PATH"].map { ":" + $0 } ?? ""
                environment["PATH"] = CLILocator.searchPath + inheritedPath
                connection.startTranslation(
                    runtime: runtime, home: home,
                    provider: translationProvider, command: URL(fileURLWithPath: command), environment: environment)
            } else if mode == .configuration {
                connection.startConfiguration(runtime: runtime,
                                              home: homeDirectory ?? URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true))
            } else {
                connection.start(runtime: runtime)
            }
        } catch let error as ProbeError {
            if modelCatalog.pending { modelCatalog.fail(.connection) }
            plainPaste.connectionLost()
            self.error = error
            status = "Cannot start: \(error.rawValue). No host Python fallback."
            if !preserveProduct { failPreparation(status) }
            if queuedHistory != nil {
                failHistory(text("History helper could not start (\(error.rawValue)). Refresh explicitly.",
                                 "历史记录助手无法启动（\(error.rawValue)），请手动刷新。"))
            }
        } catch {
            if modelCatalog.pending { modelCatalog.fail(.connection) }
            plainPaste.connectionLost()
            self.error = .launchFailed
            status = "Cannot start bundled helper. No host Python fallback."
            if !preserveProduct { failPreparation(status) }
            if queuedHistory != nil {
                failHistory(text("History helper could not start. Refresh explicitly.",
                                 "历史记录助手无法启动，请手动刷新。"))
            }
        }
    }

    func fixture() {
        guard connectionMode == .diagnostic else { return }
        guard input.utf8.count <= 8192 else {
            status = "Input exceeds 8192 UTF-8 bytes."
            return
        }
        request(["operation": .string("fixture"), "text": .string(input)])
    }

    func runtimeProbe(https: Bool) {
        guard connectionMode == .diagnostic else { return }
        request(["operation": .string("runtime_probe"), "https": .bool(https)])
    }

    func translate(origin: String = "text", useCache: Bool = true) {
        monitor.cancelPendingSelection()
        catalogWasLastRequest = false
        loadPresentation()
        if let issue = inputIssue(for: input) {
            failPreparation(issue.message(using: self))
            return
        }
        translationIntentID = UUID()
        draft = Draft(text: input, origin: origin, useCache: origin == "ocr" ? false : useCache,
                      direction: direction, model: modelProfile, language: usesChinese ? "zh_CN" : "en_US",
                      useSavedDirection: origin != "ocr" && !settingsReady && !directionEdited,
                      useSavedModel: origin != "ocr" && !settingsReady && !modelEdited,
                      provider: translationProvider, useSavedProvider: !settingsReady)
        translationOrigin = origin
        productPhase = .preparing
        productMessage = text("Preparing translation…", "正在准备翻译…")
        openProduct()
        if active { requestCancellation() }
        resumeTranslation()
    }

    @discardableResult
    func translateImage(_ image: CGImage) -> UUID? {
        monitor.cancelPendingSelection()
        guard !active, !preparing, !stopping, !imageTranslation.isShutDown else {
            productMessage = text("Finish or cancel the current request first.", "请先完成或取消当前请求。")
            return nil
        }
        catalogWasLastRequest = false
        loadPresentation()
        let intent = UUID()
        translationIntentID = intent
        draft = Draft(text: "", origin: "ocr", useCache: false, direction: direction, model: modelProfile,
                      language: usesChinese ? "zh_CN" : "en_US", useSavedDirection: false,
                      useSavedModel: false, provider: translationProvider, useSavedProvider: !settingsReady,
                      imageIntent: intent)
        translationOrigin = "ocr"
        productPhase = .preparing
        productMessage = text("Preparing the selected image…", "正在准备所选图片…")
        guard draft?.imageIntent == intent, translationIntentID == intent else { return nil }
        imageTranslation.prepare(image, intent: intent)
        return intent
    }

    func performResultAction(_ action: ResultAction, targetLanguage: String? = nil) {
        monitor.cancelPendingSelection()
        catalogWasLastRequest = false
        guard !active, !preparing else {
            productMessage = text("Finish or cancel the current request first.", "请先完成或取消当前请求。")
            return
        }
        guard !primaryResult.isEmpty else {
            failPreparation(text("Complete a translation or choose a history result first.",
                                 "请先完成翻译或选择一条历史结果。"))
            return
        }
        guard !action.usesOriginalInput || resultHasOriginalInput else {
            productMessage = text("This result has no original text.", "此结果没有原文。")
            if resultKind == "ocr" {
                productMessage += text(" Use the capture preview to send the image again.",
                                       " 如需再次发送图片，请使用截图预览。")
            }
            return
        }
        let source = action.usesOriginalInput ? resultInput : primaryResult
        let language = usesChinese ? "zh_CN" : "en_US"
        guard action.acceptsInput(text: source, appLanguage: language, targetLanguage: targetLanguage) else {
            failPreparation(text("This result action needs valid text and a supported target language.",
                                 "此操作需要有效文本及支持的目标语言。"))
            return
        }
        let title = resultActionTitle(action, targetLanguage: targetLanguage)
        translationIntentID = UUID()
        draft = Draft(text: source, origin: translationOrigin, useCache: false,
                      direction: direction, model: modelProfile, language: language,
                      useSavedDirection: !settingsReady && !directionEdited,
                      useSavedModel: !settingsReady && !modelEdited,
                      provider: translationProvider, useSavedProvider: !settingsReady,
                      action: ActionDraft(action: action, targetLanguage: targetLanguage,
                                          generation: resultGeneration,
                                          prefix: output + "\n\n---\n\n### " + title + "\n\n",
                                          title: title))
        productPhase = .preparing
        productMessage = text("Preparing result action…", "正在准备结果操作…")
        openProduct()
        resumeTranslation()
    }

    private func resumeTranslation() {
        guard var requested = draft, connectionMode != .diagnostic, ready, settingsReady, !settingsBusy,
              !active, !stopping, !publishingImageRequest, !dictionary.committing,
              let connection = connection else { return }
        if let intent = requested.imageIntent, imageTranslation.attachment(for: intent) == nil { return }
        guard requested.provider == translationProvider else {
            failPreparation(text("The translation service changed. Translate again to use the current service.",
                                 "翻译服务已更改，请再次翻译以使用当前服务。"))
            return
        }
        if requested.action == nil && requested.imageIntent == nil,
           let issue = TextInputPreflight.check(requested.text, limit: inputLimit.saved, requireLimit: true) {
            failPreparation(issue.message(using: self))
            onTranslationResult?(productMessage)
            return
        }
        if requested.action == nil, requested.origin != "ocr", requested.useCache, !requested.lookupFinished {
            let id = UUID().uuidString
            draft = nil
            dictionaryLookup = (id, requested, false)
            latest.select(id)
            pending.insert(id)
            active = true
            hideCurrentOutput = false
            productPhase = .preparing
            productMessage = text("Checking the local dictionary…", "正在查询本地词典…")
            onTranslationStarted?()
            _ = connection.dictionary(.lookup(text: requested.text, appLanguage: requested.language,
                                               origin: requested.origin, useCache: true, recordHistory: true),
                                      id: id, timeout: 25)
            return
        }
        if cliChangeDeferred || catalogNeedsReconnect ||
            (nativeTranslation && connectedProvider != requested.provider) {
            if dictionary.ownsInstallation {
                productMessage = text("Waiting for the dictionary operation before using \(translationProvider.displayName). You can cancel the download.",
                                      "等待词典操作完成后使用 \(translationProvider.displayName)。你可以取消下载。")
            } else {
                cliChangeDeferred = false
                openAfterStop = true
                stopHelper()
            }
            return
        }
        if !nativeTranslation {
            // A known local miss (or an explicit model action) may upgrade this one immutable intent.
            locatingForUpgrade = true
            if !candidates.contains(where: { $0.executable && $0.url.path == selectedCLI }) { locateCLI() }
            locatingForUpgrade = false
            if let intent = requested.imageIntent, draft?.imageIntent != intent { return }
            guard candidates.contains(where: { $0.executable && $0.url.path == selectedCLI }) else {
                needsCLI = true
                failPreparation(requested.lookupFinished
                    ? text("No local dictionary result. Choose \(translationProvider.displayName) in Settings to use model translation.",
                           "本地词典没有结果。请在设置中选择 \(translationProvider.displayName) 以使用模型翻译。")
                    : text("This request needs \(translationProvider.displayName). Choose an installation in Settings.",
                           "此请求需要 \(translationProvider.displayName)，请在设置中选择安装路径。"))
                onConfigurationRequired?()
                return
            }
            needsCLI = false
            if dictionary.ownsInstallation {
                productMessage = text("Waiting for the dictionary operation before model translation. You can cancel the download.",
                                      "等待词典操作完成后进行模型翻译。你可以取消下载。")
                return
            }
            upgradingForDraft = true
            openAfterStop = true
            stopHelper()
            return
        }
        if requested.imageIntent != nil && !imageTranslationSupported {
            failPreparation(text("Image translation is unavailable in this connection. Translate text is still available.",
                                 "当前连接不支持图片翻译，仍可使用“翻译文字”。"))
            return
        }
        let savedLanguage = savedConfiguration?["language"]?.string
        if savedConfiguration?["direction"] != .string(requested.direction) ||
            savedConfiguration?["model_provider"] != .string(requested.provider.rawValue) ||
            !CodexModelSettings.sameID(savedConfiguration?[requested.provider.modelKey]?.string, requested.model) ||
            (savedLanguage != nil && savedLanguage != "" && savedLanguage != requested.language) {
            guard !requested.configurationSaved else {
                failPreparation(text("Settings could not be applied. Check Settings before translating again.",
                                     "设置未能应用，请检查设置后再翻译。"))
                return
            }
            requested.configurationSaved = true
            draft = requested
            productPhase = .preparing
            productMessage = text("Applying translation settings…", "正在应用翻译设置…")
            saveConfiguration(history: historyEnabled, direction: requested.direction, model: requested.model,
                              language: requested.language)
            return
        }
        if let action = requested.action, action.generation != resultGeneration {
            failPreparation(text("The displayed result changed. Choose an action on the current result.",
                                 "显示的结果已更改，请对当前结果选择操作。"))
            return
        }
        let id = UUID().uuidString
        if let intent = requested.imageIntent {
            guard draft?.imageIntent == intent, translationIntentID == intent else { return }
            guard imageTranslation.reserve(intent: intent, requestID: id, connectionID: connectionID) else {
                failPreparation(text("The selected image is no longer available. Send it again from the capture preview.",
                                     "所选图片已不可用，请从截图预览重新发送。"))
                return
            }
            publishingImageRequest = true
        }
        defer {
            if requested.imageIntent != nil {
                publishingImageRequest = false
                if draft != nil && !active { resumeTranslation() }
            }
        }
        draft = nil
        latest.select(id)
        pending.insert(id)
        active = true
        discardBufferedDelta()
        hideCurrentOutput = false
        productPhase = .translating
        if let action = requested.action {
            activeAction = (id, action)
            output = action.prefix
            productMessage = action.title + "…"
        } else {
            activeAction = nil
            resultGeneration = UUID()
            primaryResult = ""
            output = ""
            resultInput = requested.text
            resultHasOriginalInput = requested.imageIntent == nil
            resultKind = requested.origin == "ocr" ? "ocr" : "text"
            isLocalDictionaryResult = false
            resultSources = []
            productMessage = requested.imageIntent == nil ? text("Translating…", "正在翻译…") :
                text("Translating the selected image…", "正在翻译所选图片…")
        }
        if let intent = requested.imageIntent {
            status = "Image translation requested. No automatic retry."
            guard translationIntentID == intent, latest.id == id, !stopping,
                  let attachment = imageTranslation.attachment(for: intent),
                  imageTranslation.markSent(intent: intent) else {
                _ = imageTranslation.cancelUnsent(requestID: id)
                pending.remove(id)
                if latest.id == id { active = false }
                if translationIntentID == intent && draft == nil && !stopping {
                    productPhase = .cancelled
                    productMessage = text("Cancelled", "已取消")
                }
                return
            }
            _ = connection.translateImage(imagePath: attachment.imagePath, imageBytes: attachment.imageBytes,
                                          imageSHA256: attachment.imageSHA256, appLanguage: requested.language,
                                          recordHistory: historyEnabled, id: id, timeout: 110)
            onTranslationStarted?()
            return
        }
        onTranslationStarted?()
        status = "Translation requested. Uses the selected native CLI; no automatic retry."
        if let action = requested.action {
            _ = connection.resultAction(action.action, text: requested.text, appLanguage: requested.language,
                                        targetLanguage: action.targetLanguage, id: id, timeout: 110)
        } else {
            _ = connection.translate(text: requested.text, appLanguage: requested.language,
                                     origin: requested.origin, useCache: requested.useCache,
                                     recordHistory: true, id: id, timeout: 110)
        }
    }

    private func failPreparation(_ message: String) {
        draft = nil
        productPhase = .failed
        productMessage = message
    }

    private func resumeDeferredCLIConnection() {
        if !cliChangeDeferred { resumeModelCatalog() }
        guard cliChangeDeferred, !dictionary.busy, !dictionary.ownsInstallation,
              dictionary.phase != .unknown, !active, !preparing, draft == nil,
              ready, !settingsBusy, !stopping else { return }
        cliChangeDeferred = false
        openAfterStop = true
        stopHelper()
    }

    func translateSelection(_ selection: SelectionResult) {
        switch selection {
        case .present(let text):
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                requestQuickInputForEmptySelection()
                return
            }
            translationOrigin = "selection"
            input = text
            translate(origin: "selection")
        case .absent, .unknown(.copyNotObserved):
            requestQuickInputForEmptySelection()
            return
        case .unknown(let reason):
            status = "Selection unavailable (\(reason.rawValue)). Nothing submitted."
            failPreparation(text("Could not read the selection. Open Translate to type or paste instead.",
                                 "无法读取选中文字，请打开翻译窗口输入或粘贴。"))
        }
        onTranslationResult?((productPhase == .failed ? productMessage : status) +
                             (output.isEmpty ? "" : "\n\n" + output))
    }

    private func requestQuickInputForEmptySelection() {
        status = "No selected text. Nothing submitted."
        onQuickInputRequested?()
    }

    func loadSettings(modelRead: Bool = false, afterModelSave: Bool = false, plainReconcile: Bool = false,
                      summaryExpected: Bool? = nil, summaryReconcile: Bool = false,
                      historyLimitRead: Bool = false, afterHistoryLimitSave: Bool = false,
                      inputLimitRead: Bool = false, afterInputLimitSave: Bool = false,
                      copyIntervalRead: Bool = false, afterCopyIntervalSave: Bool = false) {
        guard connectionMode != .diagnostic, ready, !settingsBusy, let connection = connection else { return }
        settingsBusy = true
        settingsReady = false
        let id = UUID().uuidString
        configLoadID = id
        if pendingProvider != nil {
            providerChangeID = id
            providerMessage = text("Checking the saved service…", "正在确认已保存的服务…")
        }
        if defaultsPhase == .readingBack { defaultsRequestID = id }
        if historyLimitRead { historyLimit.beginRead(id: id, afterSave: afterHistoryLimitSave) }
        if inputLimitRead { inputLimit.beginRead(id: id, afterSave: afterInputLimitSave) }
        if copyIntervalRead { copyInterval.beginRead(id: id, afterSave: afterCopyIntervalSave) }
        if summaryExpected != nil || summaryReconcile {
            summaryReadRequest = (id, summaryExpected)
            summaryPreferencePhase = .readingBack
        }
        if modelRead { modelSettings.beginRead(id: id, afterSave: afterModelSave) }
        plainPaste.beginRead(id: id, reconcile: plainReconcile || plainPasteReconcileOnLoad)
        plainPasteReconcileOnLoad = false
        connection.loadConfiguration(id: id)
    }

    var canRestoreDefaults: Bool {
        defaultsServicesIdle && !defaultsPhase.busy && defaultsPhase != .confirming
    }

    private var defaultsServicesIdle: Bool {
        canApplyModelSetting && !dictionary.busy && !modelCatalog.busy && !catalogShutDown &&
            !plainPaste.serviceState.busy && !plainPaste.stoppingAction &&
            !plainPaste.isShutDown && !captureShortcut.isShutDown
    }

    func prepareDefaultsRestore() {
        guard canRestoreDefaults, let connection else {
            if !defaultsPhase.busy && defaultsPhase != .confirming { defaultsPhase = .failed("settings_unavailable") }
            return
        }
        settingsDefaults = nil
        defaultsPhase = .loading
        settingsBusy = true
        let id = UUID().uuidString
        defaultsRequestID = id
        connection.send(ClientMessage(id: id, type: "request",
                                      payload: ["operation": .string("config_load"), "defaults": .bool(true)]))
    }

    func cancelDefaultsRestore() {
        guard defaultsPhase == .confirming else { return }
        defaultsPhase = .idle
        settingsDefaults = nil
    }

    func confirmDefaultsRestore() {
        guard defaultsPhase == .confirming else { return }
        guard canConfirmDefaultsRestore,
              let settingsDefaults, let config = savedConfiguration, let connection else {
            failDefaultsRestore("settings_unavailable")
            return
        }
        defaultsDrafts = ["direction": direction, "history": historyLimit.draft,
                          "input": inputLimit.draft, "interval": copyInterval.draft]
        defaultsProviderDrafts = providerDrafts
        defaultsProviderDrafts[translationProvider] = ProviderDraft(
            settings: modelSettings, profile: modelProfile, edited: modelEdited)
        let id = UUID().uuidString
        defaultsRequestID = id
        defaultsPhase = .saving
        settingsBusy = true
        configSaveID = id
        if let enabled = settingsDefaults.values["plain_text_paste_enabled"]?.bool {
            plainPaste.choose(enabled)
            guard plainPaste.beginSave(id: id) == enabled else {
                configSaveID = nil
                failDefaultsRestore("paste_settings_unavailable")
                stopHelper()
                return
            }
        }
        dictionary.requestStatusWhenReady()
        connection.saveConfiguration(settingsDefaults.merging(into: config), id: id)
    }

    var canConfirmDefaultsRestore: Bool {
        defaultsPhase == .confirming && defaultsServicesIdle
    }

    var canReloadDefaults: Bool {
        !defaultsPhase.busy && !settingsBusy && !stopping && !dictionary.committing &&
            !catalogShutDown && connectionMode != .diagnostic
    }

    func reloadDefaultsSettings() {
        guard canReloadDefaults else { return }
        openProduct()
        if ready && !settingsBusy { loadSettings() }
    }

    var defaultsConfirmationMessage: String {
        let history = settingsDefaults?.values["history_enabled"] == .bool(true)
            ? text("History saving will be turned on. ", "将开启保存翻译历史记录。")
            : text("History saving will be turned off. ", "将关闭保存翻译历史记录。")
        let dictionary = settingsDefaults?.values["local_dictionary_enabled"] == .bool(true)
            ? text("An installed dictionary will be enabled. ", "将启用已安装的词典。")
            : text("An installed dictionary will be kept but disabled. ", "保留已安装词典，但会停用。")
        let paste = settingsDefaults?.values["plain_text_paste_enabled"] == .bool(true)
            ? text("Plain-text paste will be enabled. ", "将开启纯文本粘贴。")
            : text("Plain-text paste will be disabled. ", "将关闭纯文本粘贴。")
        let limit = settingsDefaults?.values["history_limit"]?.integer.map(String.init) ?? ""
        return history + text(
            "The next new history record will keep only the newest \(limit) records. No records are deleted now. ",
            "下次新增历史记录时将仅保留最新 \(limit) 条；现在不删除记录。") + dictionary + paste + text(
            "Translation settings, appearance, language, text size and window placement return to defaults. Selection and screenshot shortcuts are turned off. Your CLI paths, accounts and system permissions are kept.",
            "翻译设置、外观、语言、字号和窗口位置策略恢复默认。划词和截图快捷键关闭。保留 CLI 路径、账号及系统权限。")
    }

    var defaultsMessage: String {
        switch defaultsPhase {
        case .idle, .confirming: return ""
        case .loading: return text("Reading default settings…", "正在读取默认设置…")
        case .saving: return text("Saving default settings…", "正在保存默认设置…")
        case .readingBack: return text("Checking the saved settings…", "正在确认已保存的设置…")
        case .restored: return text("Default settings restored.", "已恢复默认设置。")
        case .shortcutCleanupRequired:
            return text("Default settings saved. A shortcut could not be released; check the shortcut settings.",
                        "默认设置已保存，但有快捷键未能释放，请查看快捷键设置。")
        case .differentReadback:
            return text("The saved settings differ from the defaults. Local appearance and selection/screenshot shortcuts were not reset. Reload settings before trying again.",
                        "读回的设置与默认值不同，未重置本机外观及划词、截图快捷键。请重新读取设置后再试。")
        case .failed(let code):
            return text("Restore did not finish (\(code)). Saved settings may have changed; appearance and selection/screenshot shortcuts were not reset. Reload settings before trying again.",
                        "恢复未完成（\(code)）。已保存的设置可能发生了变化；外观及划词、截图快捷键未重置。请重新读取设置后再试。")
        }
    }

    private func resumeAfterSettingsOperation() {
        if settingsReady { dictionary.connectionReady() }
        flushPlainPastePreference()
        resumeTranslation()
        resumeDeferredCLIConnection()
        if settingsReady { submitPendingHistory() }
    }

    private func failDefaultsRestore(_ code: String) {
        defaultsPhase = .failed(code)
        defaultsRequestID = nil
        settingsDefaults = nil
        defaultsDrafts.removeAll()
        defaultsProviderDrafts.removeAll()
    }

    private func finishDefaultsRestore(config: [String: JSONValue], id: String) {
        guard defaultsRequestID == id, defaultsPhase == .readingBack else { return }
        defaultsRequestID = nil
        guard settingsDefaults?.matches(config) == true else {
            defaultsPhase = .differentReadback
            settingsDefaults = nil
            defaultsDrafts.removeAll()
            defaultsProviderDrafts.removeAll()
            return
        }
        if defaultsDrafts["history"] == historyLimit.draft { historyLimit.resetDraft() }
        if defaultsDrafts["input"] == inputLimit.draft { inputLimit.resetDraft() }
        if defaultsDrafts["interval"] == copyInterval.draft { copyInterval.resetDraft() }
        loadingConfiguration = true
        for provider in TranslationProvider.allCases {
            guard let profile = config[provider.modelKey]?.string else { continue }
            var current = provider == translationProvider
                ? ProviderDraft(settings: modelSettings, profile: modelProfile, edited: modelEdited)
                : providerDrafts[provider] ?? ProviderDraft(
                    settings: CodexModelSettings(provider: provider), profile: profile, edited: false)
            let previous = defaultsProviderDrafts[provider]
            if previous == nil || CodexModelSettings.sameID(previous?.settings.draft, current.settings.draft) {
                current.settings = CodexModelSettings(provider: provider)
                current.settings.loaded(profile: profile, id: id)
                if persistsPreferences {
                    (preferences ?? .standard).removeObject(forKey: provider.customModelPreferenceKey)
                }
            }
            if previous == nil || CodexModelSettings.sameID(previous?.profile, current.profile) {
                current.profile = profile
                current.edited = false
            }
            providerDrafts[provider] = current
            if provider == translationProvider {
                modelSettings = current.settings
                modelProfile = current.profile
                modelEdited = current.edited
            }
        }
        defaultsProviderDrafts.removeAll()
        if defaultsDrafts["direction"] == direction, let value = config["direction"]?.string {
            direction = value
            directionEdited = false
        }
        loadingConfiguration = false
        interfaceLanguage = "system"
        appearance = "system"
        nativeTextScale = .standard
        resultPlacement = .remembered
        captureTranslationMode = .text
        translatePassiveSelections = false
        stopMonitor()
        captureShortcut.choose(false)
        persistPresentation()
        defaultsPhase = .restored
        if case .failed = captureShortcut.registration { defaultsPhase = .shortcutCleanupRequired }
        if case .failed = plainPaste.registration { defaultsPhase = .shortcutCleanupRequired }
        settingsDefaults = nil
        defaultsDrafts.removeAll()
    }

    var canApplyModelSetting: Bool {
        ready && settingsReady && !settingsBusy && !stopping && !active && !preparing &&
            !dictionary.committing && connectionMode != .diagnostic && savedConfiguration != nil
    }

    func editCustomModelID(_ value: String) { modelSettings.edit(value) }

    func setCustomModelEditing(_ editing: Bool) { modelSettings.setEditing(editing) }

    func resetCustomModelDraft() { modelSettings.resetDraft(selection: modelProfile) }

    func modelChoices(selection: String) -> [String] {
        guard translationProvider == .codex else { return modelSettings.choices(selection: selection) }
        return modelCatalog.choices(addingTo: modelSettings.choices(selection: selection), scope: selectedCLI)
    }

    func discoveredModel(_ id: String) -> DiscoveredCodexModel? {
        guard translationProvider == .codex, modelCatalog.matches(scope: selectedCLI) else { return nil }
        return modelCatalog.models.first { CodexModelSettings.sameID($0.id.value, id) }
    }

    func refreshModels() {
        guard !modelCatalog.busy, !catalogShutDown else { return }
        loadPresentation()
        modelCatalog.begin(scope: selectedCLI)
        guard translationProvider == .codex else { modelCatalog.fail(.unavailable); return }
        resumeModelCatalog()
    }

    func cancelModelCatalog() {
        guard modelCatalog.busy else { return }
        let request = modelCatalog.cancel()
        catalogReconnectIntent = nil
        if let request, let connection, !stopping {
            connection.send(ClientMessage(id: UUID().uuidString, type: "cancel",
                                          payload: ["request_id": .string(request)]))
        }
    }

    private func invalidateModelCatalogScope() {
        catalogReconnectIntent = nil
        guard modelCatalog.scope != nil || modelCatalog.busy else { return }
        let request = modelCatalog.phase == .failed(.cliChanged) ? nil : modelCatalog.requestID
        modelCatalog.fail(.cliChanged)
        if let request, let connection, !stopping {
            connection.send(ClientMessage(id: UUID().uuidString, type: "cancel",
                                          payload: ["request_id": .string(request)]))
        }
    }

    private func resumeModelCatalog() {
        guard modelCatalog.pending, !catalogShutDown, !advancingCatalog else { return }
        if error == nil && !stopping {
            guard !active, !preparing, !settingsBusy, !historyBusy, !dictionary.busy,
                  !dictionary.ownsInstallation, !cliBusy, !plainPaste.serviceState.busy else { return }
        }
        advancingCatalog = true
        defer { advancingCatalog = false }
        let intent = modelCatalog.intent
        guard translationProvider == .codex, cliName == "codex" else {
            modelCatalog.fail(translationProvider == .claude ? .unavailable : .missingCLI)
            return
        }
        if stopping {
            catalogReconnectIntent = intent
            modelCatalog.connecting()
            return
        }
        if selectedCLI.isEmpty {
            let located = locateCandidates("codex", userCLI["codex"])
            guard modelCatalog.intent == intent, modelCatalog.pending, selectedCLI.isEmpty,
                  cliName == "codex", !catalogShutDown else { return }
            guard let candidate = located.first(where: \.executable) else {
                modelCatalog.fail(.missingCLI)
                return
            }
            modelCatalog.bind(scope: candidate.url.path)
            candidates = located
            locatingForUpgrade = true
            selectedCLI = candidate.url.path
            locatingForUpgrade = false
            needsCLI = false
        }
        guard modelCatalog.intent == intent, modelCatalog.pending,
              modelCatalog.matches(scope: selectedCLI) else { return }
        guard candidates.contains(where: { $0.executable && CodexModelSettings.sameID($0.url.path, selectedCLI) }) else {
            modelCatalog.fail(.missingCLI)
            return
        }
        if connected && (connectionMode != .translation || error != nil || catalogNeedsReconnect ||
                         !CodexModelSettings.sameID(connectedCLI, selectedCLI)) {
            catalogReconnectIntent = intent
            modelCatalog.connecting()
            stopHelper()
        }
        if !connected, modelCatalog.intent == intent, modelCatalog.pending {
            modelCatalog.connecting()
            startConnection(mode: .translation, preserveProduct: true)
        }
        if modelCatalog.intent == intent, modelCatalog.pending, ready, !settingsReady,
           !settingsBusy, !stopping, error == nil, connectionMode == .translation {
            modelCatalog.connecting()
            catalogPreservesPreparation = true
            loadSettings()
        }
        guard modelCatalog.intent == intent, modelCatalog.pending, ready, settingsReady, !settingsBusy,
              !stopping, error == nil, connectionMode == .translation,
              modelCatalog.matches(scope: connectedCLI) else { return }
        guard catalogSupported else { modelCatalog.fail(.unavailable); return }
        guard let connection else { modelCatalog.fail(.connection); return }
        let id = UUID().uuidString
        modelCatalog.submit(id: id)
        catalogWasLastRequest = true
        connection.modelCatalog(id: id)
    }

    private func handleModelCatalog(_ event: ServerEvent) -> Bool {
        guard modelCatalog.requestID == event.id else { return false }
        guard event.isTerminal else { return true }
        switch event.type {
        case "completed":
            do {
                let rows = try CodexModelEntry.decode(payload: event.payload).map {
                    DiscoveredCodexModel(id: .init(value: $0.id), name: $0.name, description: $0.description)
                }
                modelCatalog.finish(id: event.id, models: rows)
            } catch {
                modelCatalog.finish(id: event.id, models: nil, failure: .discovery)
            }
        case "cancelled": modelCatalog.finish(id: event.id, models: nil, cancelled: true)
        default:
            let failure: ModelCatalogState.Failure
            switch event.safeFailureCode {
            case "model_catalog_unavailable": failure = .unavailable
            case "model_catalog_too_large": failure = .tooLarge
            case "provider_cleanup_failed":
                catalogNeedsReconnect = true
                failure = .cleanup
            default: failure = .discovery
            }
            modelCatalog.finish(id: event.id, models: nil, failure: failure)
        }
        resumeTranslation()
        resumeDeferredCLIConnection()
        return true
    }

    func applyCustomModelID() {
        if let validation = modelSettings.validateCustom(modelSettings.draft) {
            modelSettings.reject(.invalidID(validation))
            return
        }
        applyModelProfile(modelSettings.draft)
    }

    func applyModelProfile(_ profile: String) {
        if !modelSettings.isPreset(profile), let validation = modelSettings.validateCustom(profile) {
            modelSettings.reject(.invalidID(validation))
            return
        }
        guard canApplyModelSetting else {
            modelSettings.reject(ready && settingsReady ? .busy : .unavailable)
            return
        }
        modelProfile = profile
        saveConfiguration(history: historyEnabled, direction: direction, model: profile, modelEdit: true)
    }

    func reloadModelSetting() {
        guard ready, !settingsBusy, !stopping, !active, !preparing, !dictionary.committing,
              connectionMode != .diagnostic else {
            modelSettings.reject(ready ? .busy : .unavailable)
            return
        }
        loadSettings(modelRead: true)
    }

    func saveSettings(history: Bool? = nil) {
        guard !dictionary.committing else {
            dictionary.report("Wait for the dictionary commit to finish before changing settings.",
                              "请等待词典提交完成，再更改设置。")
            return
        }
        saveConfiguration(history: history ?? historyEnabled, direction: direction, model: modelProfile)
    }

    var canEditHistoryLimit: Bool {
        ready && settingsReady && !settingsBusy && !stopping && !dictionary.committing &&
            connectionMode != .diagnostic && savedConfiguration != nil && historyLimit.saved != nil
    }

    var canReloadHistoryLimit: Bool {
        ready && !settingsBusy && !stopping && !dictionary.committing && connectionMode != .diagnostic
    }

    func editHistoryLimit(_ value: String) { historyLimit.edit(value) }

    func applyHistoryLimit() {
        guard canEditHistoryLimit else { historyLimit.rejectUnavailable(); return }
        if let value = historyLimit.propose() { saveHistoryLimit(value) }
    }

    func confirmHistoryLimitReduction() {
        guard canEditHistoryLimit else { historyLimit.rejectUnavailable(); return }
        if let value = historyLimit.confirm() { saveHistoryLimit(value) }
    }

    func cancelHistoryLimitReduction() { historyLimit.cancelConfirmation() }

    private func saveHistoryLimit(_ value: Int64) {
        guard var config = savedConfiguration, let connection else {
            historyLimit.rejectUnavailable()
            return
        }
        config["history_limit"] = .integer(value)
        let id = UUID().uuidString
        historyLimit.beginSave(id: id, value: value)
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func reloadHistoryLimit() {
        guard canReloadHistoryLimit else { historyLimit.rejectUnavailable(); return }
        loadSettings(historyLimitRead: true)
    }

    func inputIssue(for text: String) -> TextInputPreflight.Issue? {
        TextInputPreflight.check(text, limit: settingsReady ? inputLimit.saved : nil, requireLimit: settingsReady)
    }

    var canEditInputLimit: Bool {
        ready && settingsReady && !settingsBusy && !stopping && !dictionary.committing &&
            connectionMode != .diagnostic && savedConfiguration != nil && inputLimit.saved != nil
    }

    var canReloadInputLimit: Bool {
        ready && !settingsBusy && !stopping && !dictionary.committing && connectionMode != .diagnostic
    }

    func editInputLimit(_ value: String) { inputLimit.edit(value) }

    func applyInputLimit() {
        guard canEditInputLimit, var config = savedConfiguration, let connection else {
            inputLimit.reject("settings_unavailable")
            return
        }
        guard let value = inputLimit.propose() else { return }
        config["max_chars"] = .integer(value)
        let id = UUID().uuidString
        inputLimit.beginSave(id: id, value: value)
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func reloadInputLimit() {
        guard canReloadInputLimit else { inputLimit.reject("settings_unavailable"); return }
        loadSettings(inputLimitRead: true)
    }

    var canEditCopyInterval: Bool {
        ready && settingsReady && !settingsBusy && !stopping && !dictionary.committing &&
            connectionMode != .diagnostic && savedConfiguration != nil && copyInterval.saved != nil
    }

    var canReloadCopyInterval: Bool {
        ready && !settingsBusy && !stopping && !dictionary.committing && connectionMode != .diagnostic
    }

    func editCopyInterval(_ value: String) { copyInterval.edit(value) }

    func saveCopyInterval() {
        guard canEditCopyInterval, var config = savedConfiguration, let connection else {
            copyInterval.reject("settings_unavailable")
            return
        }
        guard let value = copyInterval.propose() else { return }
        config["double_press_window"] = .number(value)
        let id = UUID().uuidString
        copyInterval.beginSave(id: id, value: value)
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func reloadCopyInterval() {
        guard canReloadCopyInterval else { copyInterval.reject("settings_unavailable"); return }
        loadSettings(copyIntervalRead: true)
    }

    private func applyCopyInterval(_ interval: DoubleCopyInterval, persistHint: Bool = true) {
        activeCopyInterval = interval
        monitor.setCopyInterval(interval)
        if persistHint && persistsPreferences {
            (preferences ?? .standard).set(String(interval.seconds), forKey: Self.copyIntervalHintKey)
        }
    }

    var canSaveSummaryPreference: Bool {
        ready && settingsReady && !settingsBusy && !stopping && !dictionary.committing &&
            connectionMode != .diagnostic && savedConfiguration != nil && summaryEnabled != nil
    }

    var canReloadSummaryPreference: Bool {
        ready && !settingsBusy && !stopping && !dictionary.committing && connectionMode != .diagnostic
    }

    var summaryPreferenceMessage: String {
        switch summaryPreferencePhase {
        case .idle:
            return summaryEnabled == nil
                ? text("The saved summary preference is not loaded yet.", "尚未读取已保存的摘要偏好。") : ""
        case .saving:
            return text("Saving summary setting…", "正在保存摘要设置…")
        case .readingBack:
            return text("Confirming summary setting…", "正在确认摘要设置…")
        case .saved:
            return text("Summary setting saved.", "摘要设置已保存。")
        case .differentReadback:
            return text("The saved value differs from your choice. The switch shows the value read back; no write was retried.",
                        "已保存的值与你的选择不同。开关显示回读值，未重试写入。")
        case .failed(let code):
            return text("Summary preference could not be confirmed (\(code)). Reload the saved setting; no write was retried.",
                        "无法确认摘要偏好（\(code)）。请重新读取已保存设置，未重试写入。")
        }
    }

    func saveSummaryPreference(_ enabled: Bool) {
        guard canSaveSummaryPreference, var config = savedConfiguration, let connection else {
            summaryPreferencePhase = .failed("settings_unavailable")
            return
        }
        guard enabled != summaryEnabled else { return }
        // Preserve every other saved field, including history opt-out and unsaved editor drafts.
        config["summary_enabled"] = .bool(enabled)
        let id = UUID().uuidString
        summarySaveRequest = (id, enabled)
        summaryPreferencePhase = .saving
        settingsBusy = true
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func reloadSummaryPreference() {
        guard canReloadSummaryPreference else {
            summaryPreferencePhase = .failed("settings_unavailable")
            return
        }
        loadSettings(summaryReconcile: true)
    }

    private func summaryPreferenceConnectionLost() {
        if summaryEnabled != nil || summarySaveRequest != nil || summaryReadRequest != nil {
            summaryPreferencePhase = .failed("connection_closed")
        }
        summaryEnabled = nil
        summarySaveRequest = nil
        summaryReadRequest = nil
    }

    private func saveConfiguration(history enabled: Bool, direction: String, model: String,
                                   language: String? = nil, modelEdit: Bool = false) {
        guard connectionMode != .diagnostic, ready, !settingsBusy, var config = savedConfiguration,
              let connection = connection else {
            if modelEdit { modelSettings.reject(.unavailable) }
            return
        }
        if !enabled, active { cancel() }
        config["history_enabled"] = .bool(enabled)
        config["direction"] = .string(direction)
        config[translationProvider.modelKey] = .string(model)
        config["model_provider"] = .string(translationProvider.rawValue)
        config["language"] = .string(language ?? (usesChinese ? "zh_CN" : "en_US"))
        settingsBusy = true
        let id = UUID().uuidString
        configSaveID = id
        if modelEdit { modelSettings.beginSave(id: id, profile: model) }
        connection.saveConfiguration(config, id: id)
    }

    func loadHistory(next: Bool = false) {
        if !next {
            submitHistorySearch()
            return
        }
        let criteria = HistoryCriteria(query: historySearch, kind: historyFilter)
        guard historySearchActive, ready, settingsReady, !settingsBusy, !stopping, !historyBusy,
              historyDebounce == nil, queuedHistory == nil, historyPhase == .loaded,
              loadedHistory == criteria, historyCursor != .null else {
            historyStatus = text("Finish the current search or refresh history before loading more.",
                                 "请先完成当前搜索或刷新历史记录，再加载更多。")
            return
        }
        sendHistory(criteria, cursor: historyCursor, appending: true)
    }

    func submitHistorySearch() {
        historySearchActive = true
        queueHistorySearch(debounce: false)
    }

    private func queueHistorySearch(debounce: Bool) {
        // Seeded bindings stay inert until history is explicitly opened or submitted.
        guard historySearchActive else { return }
        let criteria = HistoryCriteria(query: historySearch, kind: historyFilter)
        if let read = historyRead, read.generation == historyGeneration,
           !read.appending, read.criteria == criteria, queuedHistory == nil { return }
        historyDebounce?.cancel()
        historyDebounce = nil
        // New edits own the UI immediately; the old read keeps its transport slot until terminal.
        historyGeneration = UUID()
        historyCursor = .null
        hasNextHistoryPage = false
        historyTotal = nil
        if loadedHistory != criteria {
            historyPage = []
            loadedHistory = nil
        }
        guard criteria.query.utf8.count <= 24_000,
              criteria.kind == "all" || Self.historyKinds.contains(criteria.kind) else {
            queuedHistory = nil
            historyRequested = false
            historyPhase = .failed
            historyStatus = text("Search text must fit 24,000 UTF-8 bytes and use a supported type.",
                                 "搜索文字不能超过 24,000 UTF-8 字节，且必须选择支持的类型。")
            return
        }
        queuedHistory = criteria
        historyPhase = .waiting
        historyStatus = text("Searching all saved history…", "正在搜索所有已保存的历史记录…")
        if debounce {
            let generation = historyGeneration
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.historyGeneration == generation else { return }
                self.historyDebounce = nil
                self.submitPendingHistory()
            }
            historyDebounce = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
        } else {
            submitPendingHistory()
        }
    }

    private func submitPendingHistory() {
        guard let criteria = queuedHistory, historyDebounce == nil,
              !historyBusy, !stopping else { return }
        guard connectionMode != .diagnostic, ready, settingsReady, !settingsBusy else {
            historyRequested = true
            openProduct()
            if connectionMode != .diagnostic, ready, !settingsReady, !settingsBusy { loadSettings() }
            return
        }
        historyRequested = false
        queuedHistory = nil
        sendHistory(criteria, cursor: .null, appending: false)
    }

    private func sendHistory(_ criteria: HistoryCriteria, cursor: JSONValue, appending: Bool) {
        guard let connection else {
            failHistory(text("History connection is unavailable. Refresh to try again.",
                             "历史记录连接不可用，请刷新重试。"))
            return
        }
        historyBusy = true
        historyPhase = .loading
        historyStatus = text(appending ? "Loading more matching records…" : "Searching all saved history…",
                             appending ? "正在加载更多匹配记录…" : "正在搜索所有已保存的历史记录…")
        let id = UUID().uuidString
        historyRead = HistoryRead(id: id, generation: historyGeneration, criteria: criteria, cursor: cursor,
                                  offset: appending ? historyPage.count : 0, appending: appending)
        connection.loadHistory(pageSize: 20, cursor: cursor, query: criteria.query, kind: criteria.kind, id: id)
    }

    func closeHistorySearch() {
        // Retain an in-flight read as a barrier if the user reopens the window before it finishes.
        historySearchActive = false
        invalidateHistory(retireActive: false)
        historyPhase = .idle
    }

    private func invalidateHistory(retireActive: Bool, preservePending: Bool = false) {
        historyDebounce?.cancel()
        historyDebounce = nil
        historyCursor = .null
        hasNextHistoryPage = false
        if !preservePending {
            historyGeneration = UUID()
            queuedHistory = nil
            historyRequested = false
        }
        if retireActive {
            historyRead = nil
            historyClearID = nil
            historyClearGeneration = nil
            historyBusy = false
        }
    }

    private func failHistory(_ message: String) {
        invalidateHistory(retireActive: true)
        historyTotal = nil
        historyPhase = .failed
        historyStatus = message
    }

    private func markHistorySnapshotStale() {
        guard historySearchActive || historyRead != nil || historyClearID != nil ||
                !historyPage.isEmpty || historyTotal != nil else { return }
        // Keep the selectable snapshot, but never append a late page from before the write.
        invalidateHistory(retireActive: false)
        historyTotal = nil
        historyPhase = .stale
        historyStatus = text("Saved history changed. These are previously loaded records. Refresh to read the current history.",
                             "已保存的历史记录已更改。当前显示此前加载的记录，请刷新读取最新历史。")
    }

    func clearHistory() {
        guard connectionMode != .diagnostic, ready, settingsReady, !settingsBusy, !stopping,
              !historyBusy, let connection else {
            historyStatus = text("Wait for the current history operation before clearing all records.",
                                 "请等待当前历史记录操作完成，再清除所有记录。")
            return
        }
        invalidateHistory(retireActive: false)
        historySearchActive = true
        if active { cancel() }
        historyPage = []
        historyTotal = nil
        loadedHistory = nil
        historyBusy = true
        historyPhase = .clearing
        historyStatus = text("Clearing all saved history…", "正在清除所有已保存的历史记录…")
        let id = UUID().uuidString
        historyClearID = id
        historyClearGeneration = historyGeneration
        connection.clearHistory(id: id)
    }

    func copyResult() {
        copyText(output)
    }

    func copyBilingual() {
        guard resultHasOriginalInput else {
            productMessage = text("No original text is available for this result. Use Copy for the translated text.",
                                  "此结果没有可用原文。请使用“复制”复制翻译文字。")
            return
        }
        guard !output.isEmpty else { return }
        copyText(resultInput + "\n\n" + output)
    }

    func reuseHistory(_ row: HistoryRow) {
        translationIntentID = UUID()
        cancel()
        translationOrigin = "text"
        discardBufferedDelta()
        if row.hasOriginalInput { input = row.input }
        resultInput = row.input
        resultHasOriginalInput = row.hasOriginalInput
        output = row.output
        primaryResult = row.output
        resultGeneration = UUID()
        resultKind = row.kind
        isLocalDictionaryResult = row.isLocalDictionary
        resultSources = []
        hideCurrentOutput = true
        productPhase = .completed
        productMessage = text("From history", "来自历史记录")
    }

    func clearTranslation() {
        translationIntentID = UUID()
        cancel()
        translationOrigin = "text"
        discardBufferedDelta()
        input = ""
        output = ""
        primaryResult = ""
        isLocalDictionaryResult = false
        resultSources = []
        resultGeneration = UUID()
        resultInput = ""
        resultHasOriginalInput = true
        hideCurrentOutput = true
        productPhase = .idle
        productMessage = ""
    }

    @discardableResult
    func copyText(_ text: String) -> Bool {
        guard !text.isEmpty else { return false }
        if !writeClipboard(text) {
            status = "Could not copy the result."
            historyStatus = status
            return false
        }
        return true
    }

    private func request(_ payload: [String: JSONValue]) {
        guard ready, let connection = connection else { return }
        let id = UUID().uuidString
        latest.select(id)
        pending.insert(id)
        active = true
        output = ""
        status = "Requested \(payload["operation"]?.string ?? "probe"). No automatic retries."
        connection.send(ClientMessage(id: id, type: "request", payload: payload), timeout: 25)
    }

    func cancel() {
        monitor.cancelPendingSelection()
        if draft != nil {
            cancelledPreparation = (translationIntentID, connectionID)
            draft = nil
            productPhase = .cancelled
            productMessage = text("Cancelled", "已取消")
        }
        requestCancellation()
        resumeDeferredCLIConnection()
    }

    private func requestCancellation() {
        if dictionaryLookup != nil { dictionaryLookup?.cancelled = true }
        guard let id = latest.id, pending.contains(id), let connection = connection else { return }
        if imageTranslation.cancelUnsent(requestID: id) {
            pending.remove(id)
            active = !pending.isEmpty
            productPhase = .cancelled
            productMessage = text("Cancelled", "已取消")
            return
        }
        status = "Cancellation requested; waiting for the original request's terminal event."
        if imageTranslation.owns(requestID: id) {
            productMessage = text("Cancelling image translation… Waiting for the request to finish.",
                                  "正在取消图片翻译… 等待请求结束。")
        }
        connection.send(ClientMessage(
            id: UUID().uuidString, type: "cancel", payload: ["request_id": .string(id)]
        ))
    }

    func stopHelper(preservePendingHistory: Bool = false) {
        guard !stopping else { return }
        if catalogReconnectIntent != modelCatalog.intent { modelCatalog.disconnect() }
        plainPaste.connectionLost(preservingQueuedChoice: plainPasteConfigAfterStop)
        let keepHistory = preservePendingHistory && historyRead == nil && historyClearID == nil && queuedHistory != nil
        let interruptedHistory = historyBusy || queuedHistory != nil || historyDebounce != nil
        if !keepHistory { historySearchActive = false }
        invalidateHistory(retireActive: true, preservePending: keepHistory)
        if interruptedHistory && !keepHistory {
            historyTotal = nil
            historyPhase = .failed
            historyStatus = text("History connection closed. Refresh explicitly to reload.",
                                 "历史记录连接已关闭，请手动刷新以重新加载。")
        }
        stopping = true
        ready = false
        if error == nil { status = "Stopping helper..." }
        let stoppingConnection = connection
        dictionary.prepareToStop { stoppingConnection?.stop() }
    }

    private func imageFailureMessage(_ event: ServerEvent) -> String {
        switch event.payload["code"]?.string {
        case "image_too_large":
            return text("The image is too large. Select a smaller region and send it again.",
                        "图片过大。请选取较小区域后重新发送。")
        case "image_cleanup_failed":
            return text("The helper could not remove a temporary image. It may remain on this Mac.",
                        "助手无法删除临时图片，它可能仍保留在此 Mac 上。")
        case "image_changed", "image_unavailable", "invalid_image_translation":
            return text("The image could not be used. Send it again from the capture preview, or translate the text.",
                        "无法使用此图片。请从截图预览重新发送，或翻译文字。")
        default:
            return event.payload["submitted"] == .bool(true)
                ? text("Image translation failed after possible submission. Nothing was retried. Try another model or translate the text.",
                       "图片可能已提交，但翻译失败。未重试。可尝试其他模型或翻译文字。")
                : text("Image translation failed. Retry explicitly or translate the text.",
                       "图片翻译失败。请手动重试，或翻译文字。")
        }
    }

    private func receive(_ notice: HelperNotice) {
        defer { resumeModelCatalog() }
        switch notice {
        case .event(let event):
            let imageRequest = imageTranslation.owns(requestID: event.id)
            // Target terminals release resources even when their output is hidden or the helper is stopping.
            if event.isTerminal { imageTranslation.terminal(requestID: event.id) }
            if dictionary.handle(event) { return }
            if dictionarySearch.handle(event) { return }
            guard error == nil, !stopping else { return }
            if event.type == "ready" {
                if case .array(let capabilities)? = event.payload["capabilities"] {
                    catalogSupported = nativeTranslation && capabilities.contains(.string("model_catalog"))
                    imageTranslationSupported = nativeTranslation && capabilities.contains(.string("translate_image"))
                } else {
                    catalogSupported = false
                    imageTranslationSupported = false
                }
                ready = true
                status = nativeTranslation
                    ? "Native connection ready. CLI/account/model availability is not yet verified."
                    : connectionMode == .configuration ? "Local dictionary, settings, and history connection ready."
                    : "Ready: fixture + runtime_probe. Fixture is NOT translation."
                if connectionMode != .diagnostic { loadSettings() }
                return
            }
            if handleModelCatalog(event) { return }
            if handleBusinessEvent(event) { return }
            if handleDictionaryLookup(event) { return }
            if nativeTranslation, pending.contains(event.id), event.type == "completed",
               event.payload["history"] == .string("recorded"), activeAction?.id != event.id {
                markHistorySnapshotStale()
            }
            if event.isTerminal { pending.remove(event.id) }
            // The transport validated seq/terminal rules even for events hidden here.
            guard latest.accepts(event) else { return }
            active = pending.contains(event.id)
            switch event.type {
            case "accepted": status = nativeTranslation ? "Translation accepted." : "Accepted (P0 probe)."
            case "started": status = "Native request started; submission status is not yet known."
            case "delta":
                if !hideCurrentOutput { bufferedDelta += event.payload["text"]?.string ?? "" }
                let prefixBytes = activeAction?.content.prefix.utf8.count ?? 0
                if output.utf8.count - prefixBytes + bufferedDelta.utf8.count > 65_536 {
                    error = .frameTooLarge
                    status = "Probe output limit exceeded; stopping helper."
                    stopHelper()
                }
                if renderUpdate == nil && !hideCurrentOutput {
                    let requestID = event.id
                    let update = DispatchWorkItem { [weak self] in
                        guard let self, self.latest.id == requestID else { return }
                        self.flushBufferedDelta()
                    }
                    renderUpdate = update
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.04, execute: update)
                }
            case "completed":
                discardBufferedDelta()
                if let text = event.payload["text"]?.string {
                    if let action = activeAction {
                        if !hideCurrentOutput, action.id == event.id,
                           action.content.generation == resultGeneration {
                            output = action.content.prefix + text
                            productPhase = .completed
                            productMessage = self.text("Result action complete · Original result kept",
                                                       "结果操作完成 · 已保留原结果")
                        }
                        status = "Result action completed. No cache read or history write."
                    } else if nativeTranslation {
                        if !hideCurrentOutput {
                            output = text
                            primaryResult = text
                        }
                        let cached = event.payload["cached"] == .bool(true)
                        let history = event.payload["history"]?.string ?? ""
                        status = cached ? "Loaded matching cached translation." : "Native translation completed."
                        if history == "failed" {
                            status += " History was not saved."
                            if let code = event.payload["history_error"]?.string { status += " \(code)." }
                        } else {
                            status += " History: \(history)."
                        }
                        if !hideCurrentOutput {
                            resultKind = event.payload["kind"]?.string ?? "text"
                            productPhase = .completed
                            productMessage = self.text(cached ? "From history" : "Translation complete",
                                                       cached ? "来自历史记录" : "翻译完成")
                            if history == "failed" {
                                productMessage += self.text(" · History was not saved", " · 历史记录未保存")
                            }
                        }
                    } else {
                        if !hideCurrentOutput { output = text }
                        status = "Completed SYNTHETIC FIXTURE - NOT translation."
                    }
                } else {
                    do {
                        output = String(decoding: try JSONValue.object(event.payload).encoded(), as: UTF8.self)
                        let https = event.payload["https"]?.object?["status"] == .string("passed")
                            ? "passed" : "not_run"
                        status = "Runtime probe completed. HTTPS: \(https). Python metadata is descriptive, not proof of HTTPS or an overall pass."
                    } catch {
                        status = "Runtime result could not be rendered."
                    }
                }
            case "cancelled":
                flushBufferedDelta()
                status = event.payload["submitted"] == .bool(true)
                    ? "Cancelled after possible CLI submission. Submission cannot be rolled back."
                    : "Cancelled before native submission."
                if !hideCurrentOutput {
                    if activeAction != nil {
                        output += "\n\n[" + text("Result action cancelled", "结果操作已取消") + "]"
                    }
                    productPhase = .cancelled
                    productMessage = imageRequest && event.payload["submitted"] == .bool(true)
                        ? text("Cancelled · The image may already have been sent.", "已取消 · 图片可能已经发送。")
                        : text("Cancelled", "已取消")
                }
            case "failed":
                flushBufferedDelta()
                status = event.safeFailureMessage(provider: connectedProvider ?? translationProvider)
                if !hideCurrentOutput {
                    if activeAction != nil {
                        output += "\n\n[" + text("Result action failed", "结果操作失败") + "]"
                    }
                    productPhase = .failed
                    productMessage = imageRequest ? imageFailureMessage(event) : status
                }
            default: break
            }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
            if event.isTerminal {
                activeAction = nil
                resumeTranslation()
                resumeDeferredCLIConnection()
            }
        case .failure(let error):
            let isolatedCatalog = (modelCatalog.busy || catalogWasLastRequest) && !active && draft == nil
            let hiddenImageResult = imageTranslation.owns(requestID: latest.id) && hideCurrentOutput && draft == nil
            modelCatalog.disconnect()
            catalogReconnectIntent = nil
            modelSettings.connectionLost()
            failProviderChange("connection_closed")
            summaryPreferenceConnectionLost()
            historyLimit.connectionLost()
            inputLimit.connectionLost()
            copyInterval.connectionLost()
            if defaultsPhase.busy || defaultsPhase == .confirming { failDefaultsRestore("connection_closed") }
            plainPaste.connectionLost()
            historySearchActive = false
            let hadLocalLookup = dictionaryLookup != nil
            let hadSeparateModelRequest = active && !hadLocalLookup && nativeTranslation
            upgradingForDraft = false
            cliChangeDeferred = false
            openAfterStop = false
            dictionaryLookup = nil
            flushBufferedDelta()
            self.error = error
            ready = false
            active = false
            if historyBusy || queuedHistory != nil || historyDebounce != nil ||
                !historyPage.isEmpty || historyTotal != nil {
                failHistory(text("History connection interrupted (\(error.rawValue)). Refresh explicitly; no automatic retry.",
                                 "历史记录连接中断（\(error.rawValue)）。请手动刷新，不会自动重试。"))
            } else {
                invalidateHistory(retireActive: true)
            }
            dictionary.connectionLost()
            dictionarySearch.connectionLost()
            status = "Helper failure: \(error.rawValue). Restart explicitly; requests are not replayed."
            if error == .translationOutcomeUnknown {
                status = imageTranslation.owns(requestID: latest.id)
                    ? text("Image translation outcome unknown. The image may have been sent and history may have changed. Not retried.",
                           "图片翻译结果未知。图片可能已经发送，历史记录可能已更改。未重试。")
                    : activeAction == nil
                    ? "Translation outcome unknown. The CLI may have received the request and history may have changed. Not retried."
                    : "Result action outcome unknown. The CLI may have received the request. Original result retained; no automatic retry."
            } else if error == .dictionaryOutcomeUnknown {
                status = hadLocalLookup
                    ? text("Local dictionary outcome unknown. History may have changed. No model fallback or automatic retry.",
                           "本地词典查询结果未知，历史记录可能已更改。不会回退到模型或自动重试。")
                    : text("Dictionary operation outcome unknown. Refresh dictionary status; installation will not be replayed.",
                           "词典操作结果未知。请刷新词典状态，不会重试安装。")
                if hadSeparateModelRequest {
                    status += text(" A separate model request may also have been submitted; it will not be retried.",
                                   " 独立的模型请求也可能已提交，不会重试。")
                }
            }
            if activeAction != nil, !hideCurrentOutput {
                output += "\n\n[" + text("Result action interrupted", "结果操作已中断") + "]"
            }
            if !isolatedCatalog && !hiddenImageResult { failPreparation(status) }
            if !isolatedCatalog && !hiddenImageResult && (nativeTranslation || hadLocalLookup) {
                onTranslationResult?(status + "\n\n" + output)
            }
        case .stopped:
            imageTranslation.drained(connectionID: connectionID)
            let restartCatalog = catalogReconnectIntent == modelCatalog.intent && modelCatalog.pending &&
                (modelCatalog.matches(scope: selectedCLI) ||
                 (modelCatalog.scope == nil && selectedCLI.isEmpty)) && !catalogShutDown
            catalogReconnectIntent = nil
            if !restartCatalog { modelCatalog.disconnect() }
            discardBufferedDelta()
            let reopen = openAfterStop
            let keepHistory = reopen && historyRequested && queuedHistory != nil
            let interruptedHistory = historyBusy || queuedHistory != nil || historyDebounce != nil
            historySearchActive = keepHistory
            openAfterStop = false
            invalidateHistory(retireActive: true, preservePending: keepHistory)
            stopping = false
            cliChangeDeferred = false
            connected = false
            ready = false
            active = false
            activeAction = nil
            pending.removeAll()
            savedConfiguration = nil
            modelSettings.connectionLost()
            failProviderChange("connection_closed")
            summaryPreferenceConnectionLost()
            historyLimit.connectionLost()
            inputLimit.connectionLost()
            copyInterval.connectionLost()
            if defaultsPhase.busy || defaultsPhase == .confirming { failDefaultsRestore("connection_closed") }
            plainPaste.connectionLost(preservingQueuedChoice: plainPasteConfigAfterStop)
            settingsReady = false
            settingsBusy = false
            historyBusy = false
            configLoadID = nil
            configSaveID = nil
            historyClearID = nil
            historyPage = []
            historyTotal = nil
            loadedHistory = nil
            if keepHistory { historyPhase = .waiting }
            else if historyPhase != .failed {
                historyPhase = interruptedHistory ? .failed : .idle
                historyStatus = text("History connection closed. Refresh explicitly to reload.",
                                     "历史记录连接已关闭，请手动刷新以重新加载。")
            }
            historyCursor = .null
            hasNextHistoryPage = false
            connection = nil
            connectedCLI = ""
            connectedProvider = nil
            catalogSupported = false
            imageTranslationSupported = false
            catalogPreservesPreparation = false
            dictionaryLookup = nil
            dictionary.connectionLost()
            dictionarySearch.connectionLost()
            if !reopen, draft != nil {
                failPreparation(text("Connection closed. Translate again when you are ready.",
                                     "连接已关闭，准备好后可重新翻译。"))
            }
            if error == nil { status = "Helper stopped." }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
            notifyStoppedIfIdle()
            let upgrade = reopen && upgradingForDraft && draft != nil && error == nil
            upgradingForDraft = false
            let restartPlainPaste = plainPasteConfigAfterStop
            plainPasteConfigAfterStop = false
            if restartPlainPaste { plainPasteReconcileOnLoad = true }
            if upgrade { startNativeTranslation() }
            else if restartCatalog {
                if draft != nil { openProduct(); resumeTranslation() }
                else { resumeModelCatalog() }
            }
            else if reopen { openProduct() }
            else if restartPlainPaste { loadPlainPasteConfiguration(reconcile: true) }
        }
    }

    private func flushBufferedDelta() {
        renderUpdate?.cancel()
        renderUpdate = nil
        if !hideCurrentOutput { output += bufferedDelta }
        bufferedDelta = ""
    }

    private func discardBufferedDelta() {
        renderUpdate?.cancel()
        renderUpdate = nil
        bufferedDelta = ""
    }

    private func handleDictionaryLookup(_ event: ServerEvent) -> Bool {
        guard let lookup = dictionaryLookup, event.id == lookup.id else { return false }
        guard event.isTerminal else { return true }
        dictionaryLookup = nil
        pending.remove(event.id)
        if latest.id == event.id { latest.select(nil) }
        active = false
        if event.type == "completed", event.payload["result"]?.object?["history"] == .string("recorded") {
            markHistorySnapshotStale()
        }
        if lookup.cancelled || hideCurrentOutput {
            if draft == nil && !hideCurrentOutput {
                productPhase = .cancelled
                productMessage = text("Local lookup cancelled. No model request was sent.",
                                      "本地查询已取消，未发送模型请求。")
            }
            resumeTranslation()
            resumeDeferredCLIConnection()
            return true
        }
        guard event.type == "completed" else {
            draft = nil
            productPhase = event.type == "cancelled" ? .cancelled : .failed
            productMessage = event.type == "cancelled"
                ? text("Local lookup cancelled. No model request was sent.", "本地查询已取消，未发送模型请求。")
                : text("Local lookup failed: \(event.safeFailureCode). Not sent to a model.",
                       "本地查询失败：\(event.safeFailureCode)。未发送给模型。")
            onTranslationResult?(productMessage)
            resumeDeferredCLIConnection()
            return true
        }
        do {
            let result = try DictionaryLookupResult(payload: event.payload)
            if let value = result.result, let resultText = value["text"]?.string {
                discardBufferedDelta()
                resultInput = lookup.draft.text
                resultHasOriginalInput = true
                resultKind = "dict"
                isLocalDictionaryResult = true
                resultSources = result.sources
                resultGeneration = UUID()
                output = resultText
                primaryResult = resultText
                activeAction = nil
                productPhase = .completed
                productMessage = text("Local dictionary · No model request", "本地词典 · 未请求模型")
                if value["history"] == .string("failed") {
                    productMessage += text(" · History was not saved", " · 历史记录未保存")
                }
                status = productMessage
                onTranslationResult?(productMessage + "\n\n" + output)
            } else {
                var requested = lookup.draft
                requested.lookupFinished = true
                draft = requested
                resumeTranslation()
            }
        } catch {
            failPreparation(text("Invalid local dictionary response. Nothing was sent to a model.",
                                 "本地词典响应无效，未发送给模型。"))
        }
        resumeDeferredCLIConnection()
        return true
    }

    private func handleBusinessEvent(_ event: ServerEvent) -> Bool {
        if defaultsPhase == .loading, event.id == defaultsRequestID {
            guard event.isTerminal else { return true }
            defaultsRequestID = nil
            settingsBusy = false
            if event.type == "completed", let config = event.payload["config"]?.object,
               let defaults = SettingsDefaults(config) {
                settingsDefaults = defaults
                defaultsPhase = .confirming
            } else {
                failDefaultsRestore(event.type == "completed" ? "invalid_defaults" : event.safeFailureCode)
            }
            resumeAfterSettingsOperation()
            return true
        }
        if event.id == configLoadID || event.id == configSaveID {
            guard event.isTerminal else { return true }
            let pasteSave = plainPaste.preference.ownsSave(event.id)
            let summaryOperation = summarySaveRequest?.id == event.id || summaryReadRequest?.id == event.id
            let historyLimitOperation = historyLimit.owns(event.id)
            let inputLimitOperation = inputLimit.owns(event.id)
            let copyIntervalOperation = copyInterval.owns(event.id)
            if event.type == "completed" {
                if event.id == configSaveID {
                    if defaultsRequestID == event.id { defaultsPhase = .readingBack }
                    let modelSave = modelSettings.requestID == event.id
                    let summaryExpected = summarySaveRequest?.id == event.id ? summarySaveRequest?.value : nil
                    summarySaveRequest = nil
                    plainPaste.saved(id: event.id)
                    configSaveID = nil
                    settingsBusy = false
                    status = "Settings saved. Reloading their normalized view; no write replay."
                    loadSettings(modelRead: modelSave, afterModelSave: modelSave, summaryExpected: summaryExpected,
                                 historyLimitRead: historyLimitOperation, afterHistoryLimitSave: historyLimitOperation,
                                 inputLimitRead: inputLimitOperation, afterInputLimitSave: inputLimitOperation,
                                 copyIntervalRead: copyIntervalOperation, afterCopyIntervalSave: copyIntervalOperation)
                    return true
                }
                guard let config = event.payload["config"]?.object,
                      case let .bool(enabled)? = config["history_enabled"],
                      let savedDirection = config["direction"]?.string,
                      let providerID = config["model_provider"]?.string,
                      let savedProvider = TranslationProvider(rawValue: providerID),
                      let profile = config[savedProvider.modelKey]?.string else {
                    summaryEnabled = nil
                    summaryPreferencePhase = .failed("invalid_config")
                    summarySaveRequest = nil
                    summaryReadRequest = nil
                    historyLimit.fail("invalid_config")
                    inputLimit.fail("invalid_config")
                    copyInterval.fail("invalid_config")
                    modelSettings.fail(id: event.id, failure: .invalidReadback)
                    failProviderChange("invalid_config")
                    plainPaste.failed(id: event.id, code: "invalid_config")
                    error = .invalidTransition
                    settingsReady = false
                    if defaultsRequestID == event.id { failDefaultsRestore("invalid_config") }
                    status = "Invalid normalized settings response; stopping the connection."
                    stopHelper()
                    return true
                }
                savedConfiguration = config
                adoptProvider(savedProvider, profile: profile)
                if persistsPreferences {
                    (preferences ?? .standard).set(savedProvider.rawValue, forKey: "lastConfirmedTranslationProvider")
                }
                if providerChangeID == event.id, let expected = pendingProvider {
                    providerMessage = expected == savedProvider
                        ? text("Using \(savedProvider.displayName).", "正在使用 \(savedProvider.displayName)。")
                        : text("The saved service differs from your selection. The saved service is shown.",
                               "保存的服务与所选项不同，当前显示已保存的服务。")
                    pendingProvider = nil
                    providerChangeID = nil
                }
                historyLimit.loaded(config["history_limit"]?.integer, id: event.id)
                inputLimit.loaded(config["max_chars"]?.integer, id: event.id)
                copyInterval.loaded(config["double_press_window"]?.number, id: event.id)
                if let seconds = copyInterval.saved, copyInterval.supported.contains(seconds),
                   let interval = DoubleCopyInterval(seconds: seconds) {
                    applyCopyInterval(interval)
                }
                if case let .bool(value)? = config["summary_enabled"] {
                    summaryEnabled = value
                    if let read = summaryReadRequest, read.id == event.id {
                        summaryPreferencePhase = read.expected.map { $0 == value ? .saved : .differentReadback } ?? .idle
                    } else {
                        summaryPreferencePhase = .idle
                    }
                } else {
                    summaryEnabled = nil
                    summaryPreferencePhase = .failed("invalid_summary_preference")
                }
                summaryReadRequest = nil
                let pasteEnabled: Bool?
                if case .bool(let enabled)? = config["plain_text_paste_enabled"] { pasteEnabled = enabled }
                else { pasteEnabled = nil }
                plainPaste.loaded(id: event.id, enabled: pasteEnabled)
                if plainPaste.preference.authorized { persistPlainPasteHint(true) }
                else if pasteEnabled == false { persistPlainPasteHint(false) }
                let modelReadbackFailure = modelSettings.loaded(profile: profile, id: event.id)
                if persistsPreferences, let custom = modelSettings.rememberedCustom,
                   modelSettings.validateCustom(custom) == nil {
                    (preferences ?? .standard).set(custom, forKey: translationProvider.customModelPreferenceKey)
                }
                historyEnabled = enabled
                if draft?.useSavedDirection == true {
                    draft?.direction = savedDirection
                    draft?.useSavedDirection = false
                }
                if draft?.useSavedProvider == true {
                    let changedProvider = draft?.provider != savedProvider
                    draft?.provider = savedProvider
                    draft?.useSavedProvider = false
                    if draft?.useSavedModel == true {
                        draft?.model = profile
                    } else if changedProvider {
                        draft?.model = modelEdited ? modelProfile : profile
                    }
                    draft?.useSavedModel = false
                } else if draft?.useSavedModel == true {
                    draft?.model = profile
                    draft?.useSavedModel = false
                }
                loadingConfiguration = true
                if !directionEdited { direction = savedDirection }
                if !modelEdited { modelProfile = profile }
                loadingConfiguration = false
                if direction == savedDirection && (draft == nil || draft?.direction == direction) {
                    directionEdited = false
                }
                if CodexModelSettings.sameID(modelProfile, profile) &&
                    (draft == nil || CodexModelSettings.sameID(draft?.model, modelProfile)) {
                    modelEdited = false
                }
                settingsReady = true
                finishDefaultsRestore(config: config, id: event.id)
                status = "Native settings loaded. Account and model access require an explicit translation."
                let preservesCancellation = productPhase == .cancelled &&
                    cancelledPreparation?.intent == translationIntentID &&
                    cancelledPreparation?.connection == connectionID
                if !needsCLI && !preparing && !active && output.isEmpty &&
                    !modelCatalog.pending && !catalogPreservesPreparation && !preservesCancellation {
                    productMessage = ""
                    productPhase = .idle
                }
                if let modelReadbackFailure,
                   case .differentReadback(let expected, _) = modelReadbackFailure,
                   CodexModelSettings.sameID(draft?.model, expected) {
                    failPreparation(text("The saved model differs from the requested ID. Review model settings; nothing was sent.",
                                         "保存的模型与请求的 ID 不同。请检查模型设置，未发送模型请求。"))
                }
            } else {
                if providerChangeID == event.id { failProviderChange(event.safeFailureCode) }
                if defaultsRequestID == event.id { failDefaultsRestore(event.safeFailureCode) }
                let catalogPreparation = modelCatalog.pending && !active && draft == nil
                if historyLimitOperation || event.id == configLoadID { historyLimit.fail(event.safeFailureCode) }
                if inputLimitOperation || event.id == configLoadID { inputLimit.fail(event.safeFailureCode) }
                if copyIntervalOperation || event.id == configLoadID { copyInterval.fail(event.safeFailureCode) }
                if summaryOperation || event.id == configLoadID {
                    summaryEnabled = nil
                    summaryPreferencePhase = .failed(event.safeFailureCode)
                    summarySaveRequest = nil
                    summaryReadRequest = nil
                }
                modelSettings.fail(id: event.id, failure: .operation(event.safeFailureCode))
                plainPaste.failed(id: event.id, code: event.safeFailureCode)
                status = "Settings operation failed: \(event.safeFailureCode). No automatic retry."
                if catalogPreparation { modelCatalog.fail(.connection) }
                if (!pasteSave || !active) && !catalogPreparation &&
                    (!(summaryOperation || historyLimitOperation || inputLimitOperation || copyIntervalOperation) ||
                     (!active && draft != nil)) { failPreparation(status) }
                if historyRead == nil && historyClearID == nil && queuedHistory != nil {
                    failHistory(text("History settings could not be loaded: \(event.safeFailureCode). Refresh to try again.",
                                     "无法加载历史记录设置：\(event.safeFailureCode)。请刷新重试。"))
                }
            }
            configLoadID = nil
            configSaveID = nil
            catalogPreservesPreparation = false
            settingsBusy = false
            if settingsReady, nativeTranslation, connectedProvider != translationProvider,
               !dictionary.ownsInstallation {
                openAfterStop = true
                upgradingForDraft = draft != nil && !needsCLI
                stopHelper(preservePendingHistory: true)
                return true
            }
            resumeAfterSettingsOperation()
            return true
        }
        if event.id == historyRead?.id {
            guard event.isTerminal else { return true }
            guard let read = historyRead else { return true }
            historyRead = nil
            historyBusy = false
            guard read.generation == historyGeneration else {
                submitPendingHistory()
                return true
            }
            guard event.type == "completed" else {
                if event.safeFailureCode == "history_cursor_expired" {
                    failHistory(text("History or search conditions changed; the page cursor expired. Refresh to search again.",
                                     "历史记录或搜索条件已更改，分页游标已过期。请刷新后重新搜索。"))
                } else {
                    failHistory(text("History read ended: \(event.safeFailureCode). Refresh explicitly; no automatic retry.",
                                     "历史记录读取已结束：\(event.safeFailureCode)。请手动刷新，不会自动重试。"))
                }
                return true
            }
            do {
                let page = try decodeHistoryPage(event.payload, request: read)
                historyPage = read.appending ? historyPage + page.rows : page.rows
                historyTotal = page.total
                loadedHistory = read.criteria
                historyCursor = page.cursor
                hasNextHistoryPage = historyCursor != .null
                historyPhase = .loaded
                historyStatus = text("\(historyPage.count) of \(page.total) matching records loaded.",
                                     "已加载 \(historyPage.count) 条，共 \(page.total) 条匹配记录。")
            } catch {
                failHistory(text("Invalid history response. Refresh explicitly; no partial results were accepted.",
                                 "历史记录响应无效。请手动刷新，未接受任何部分结果。"))
            }
            return true
        }
        if event.id == historyClearID {
            guard event.isTerminal else { return true }
            let current = historyClearGeneration == historyGeneration
            historyClearID = nil
            historyClearGeneration = nil
            historyBusy = false
            let completed = event.type == "completed" && Set(event.payload.keys) == ["cleared", "revision"] &&
                event.payload["cleared"] == .bool(true) && HistoryDocument.validRevision(event.payload["revision"])
            if !current && queuedHistory == nil { return true }
            guard completed else {
                failHistory(text("History clear did not complete: \(event.safeFailureCode). Refresh to check the state; the clear will not be replayed.",
                                 "清除历史记录未完成：\(event.safeFailureCode)。请刷新以检查状态，不会重试清除。"))
                return true
            }
            if current {
                historyPage = []
                historyTotal = 0
                loadedHistory = HistoryCriteria(query: historySearch, kind: historyFilter)
                historyPhase = .loaded
                historyStatus = text("All saved history cleared. Future translations may still be recorded.",
                                     "已清除所有已保存的历史记录。未来的翻译仍可能被保存。")
            }
            submitPendingHistory()
            return true
        }
        return false
    }

    private func decodeHistoryPage(_ payload: [String: JSONValue], request: HistoryRead) throws
        -> (rows: [HistoryRow], total: Int, cursor: JSONValue) {
        try HistoryDocument.validatePage(payload, pageSize: 20, cursor: request.cursor)
        guard case let .array(entries)? = payload["entries"],
              let revision = payload["revision"]?.string,
              let total = payload["total"]?.integer, let cursor = payload["next_cursor"] else {
            throw ProbeError.invalidPayload
        }
        let rows = try entries.enumerated().map { index, value in
            try HistoryRow.decode(value, id: "\(revision)-\(request.offset + index)")
        }
        return (rows, Int(total), cursor)
    }

    func refreshPermissions() {
        updatePermissionSnapshot()
        restoreSelectionMonitorIfNeeded(refreshSnapshot: false)
    }

    private func updatePermissionSnapshot() {
        let snapshot = readPermissions()
        permissionSnapshot = snapshot
        permissions = """
        Accessibility: \(snapshot.accessibility.rawValue)
        Input Monitoring: \(snapshot.inputMonitoring.rawValue)
        Screen Capture: \(snapshot.screenCapture.rawValue)
        Secure Input: \(snapshot.secureInput ? "enabled (selection/monitoring disabled)" : "not enabled")
        """
    }

    func requestAX() {
        Permissions.requestAccessibility()
        refreshPermissions()
    }

    func requestInputMonitoring() {
        let granted = Permissions.requestInputMonitoring()
        monitorStatus = granted ? "Input Monitoring granted."
            : "Input Monitoring not granted. System Settings/restart may be required."
        refreshPermissions()
    }

    func startMonitor(accessibilityOnly: Bool = false) {
        loadPresentation()
        guard !catalogShutDown else { return }
        if !accessibilityOnly {
            monitorRequestedEnabled = true
            translatePassiveSelections = true
            persistMonitorPreference()
            scheduleMonitorRecovery()
        }
        updatePermissionSnapshot()
        attemptMonitorStart(accessibilityOnly: accessibilityOnly)
    }

    private func attemptMonitorStart(accessibilityOnly: Bool) {
        do {
            monitor.setCopyInterval(activeCopyInterval)
            monitorAXOnly = accessibilityOnly
            monitor.setClipboardFallbackEnabled(translatePassiveSelections && !monitorAXOnly)
            try monitor.start()
            monitorEnabled = monitor.running
            updateMonitorStatus()
        } catch let error as ProbeError {
            monitorEnabled = monitor.running
            monitorStatus = "Monitor not started: \(error.rawValue)."
        } catch {
            monitorEnabled = monitor.running
            monitorStatus = "Monitor not started."
        }
    }

    func stopMonitor() {
        monitorRequestedEnabled = false
        persistMonitorPreference()
        translatePassiveSelections = false
        suspendMonitor()
    }

    func suspendMonitor() {
        monitorRecoveryTimer?.invalidate()
        monitorRecoveryTimer = nil
        monitor.stop()
        monitorEnabled = false
        monitorStatus = text("Passive monitor stopped.", "已停止选区快捷键监听。")
    }

    var selectionMonitorState: SelectionMonitorState {
        if monitorEnabled, monitor.running {
            return monitorAXOnly || !translatePassiveSelections ? .diagnostic : .active
        }
        guard monitorRequestedEnabled else { return .off }
        guard let snapshot = permissionSnapshot else { return .requiresPermissions }
        if snapshot.secureInput { return .secureInput }
        if snapshot.accessibility != .granted || snapshot.inputMonitoring != .granted {
            return .requiresPermissions
        }
        return .temporarilyUnavailable
    }

    var selectionShortcutActive: Bool { selectionMonitorState == .active }

    var selectionMonitorStateMessage: String {
        switch selectionMonitorState {
        case .off:
            return text("Double Cmd+C is off.", "双击 Cmd+C 已关闭。")
        case .active:
            return text("Double Cmd+C is ready. With no text selected, it opens quick input.",
                        "双击 Cmd+C 已就绪。未选中文字时会打开快速输入。")
        case .diagnostic:
            return text("AX-only diagnostics are running. Clipboard reading is off.",
                        "仅辅助功能选区诊断正在运行，剪贴板读取已关闭。")
        case .requiresPermissions:
            return text("Double Cmd+C is enabled and will start when Accessibility and Input Monitoring are allowed.",
                        "双击 Cmd+C 已启用，允许辅助功能和输入监控后会自动开始监听。")
        case .secureInput:
            return text("Double Cmd+C is paused while Secure Input is active. It will resume automatically.",
                        "安全输入开启期间，双击 Cmd+C 暂停监听，之后会自动恢复。")
        case .temporarilyUnavailable:
            return text("Double Cmd+C is temporarily unavailable. Your choice is saved and it will retry automatically.",
                        "双击 Cmd+C 暂时不可用。已保存您的选择，将自动重试。")
        }
    }

    func restoreSelectionMonitorIfNeeded(refreshSnapshot: Bool = true) {
        loadPresentation()
        guard monitorRequestedEnabled, !catalogShutDown, !monitorAXOnly else { return }
        scheduleMonitorRecovery()
        if refreshSnapshot { updatePermissionSnapshot() }
        guard !monitor.running, let snapshot = permissionSnapshot,
              snapshot.accessibility == .granted, snapshot.inputMonitoring == .granted,
              !snapshot.secureInput else { return }
        translatePassiveSelections = true
        attemptMonitorStart(accessibilityOnly: false)
    }

    private func persistMonitorPreference() {
        guard persistsPreferences else { return }
        (preferences ?? .standard).set(monitorRequestedEnabled, forKey: Self.selectionMonitorPreferenceKey)
    }

    private func scheduleMonitorRecovery() {
        guard monitorRecoveryTimer == nil, monitorRequestedEnabled, !catalogShutDown else { return }
        monitorRecoveryTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] timer in
            MainActor.assumeIsolated {
                guard let self else { timer.invalidate(); return }
                self.refreshPermissions()
            }
        }
    }

    private func updateMonitorStatus() {
        monitorStatus = translatePassiveSelections && !monitorAXOnly
            ? text("Double Cmd+C: translate newly copied text or the accessible selection. Your clipboard stays unchanged.",
                   "选中文字并双击 Cmd+C，即可翻译。优先读取本次复制的文字，不改写剪贴板。")
            : text("AX-only diagnostic monitoring. Clipboard fallback is off.",
                   "仅监听辅助功能选区的诊断模式。剪贴板回退已关闭。")
    }

    func locateCLI() {
        candidates = locateCandidates(cliName, userCLI[cliName])
        selectedCLI = candidates.first(where: \.executable)?.url.path ?? ""
        cliStatus = "Paths checked only; not executed. Authentication: unknown."
        needsCLI = cliName == translationProvider.cliName && selectedCLI.isEmpty
        if !selectedCLI.isEmpty { persistPresentation() }
    }

    func chooseCLI() {
        guard let url = CLILocator.chooseExecutable() else { return }
        userCLI[cliName] = url
        locateCLI()
    }

    func versionCLI() {
        guard !cliBusy else { return }
        guard let candidate = candidates.first(where: {
            $0.url.path == selectedCLI && $0.executable
        }) else {
            cliStatus = "No executable selected. Locate or choose a path first. Authentication: unknown."
            return
        }
        cliBusy = true
        cliStatus = "Running selected executable with --version only..."
        cliGeneration = UUID()
        let generation = cliGeneration
        let name = cliName
        let selectedPath = selectedCLI
        let run = CLIVersionRun { [weak self] result in
            MainActor.assumeIsolated {
                guard let self = self else { return }
                self.cliBusy = false
                self.cliRun = nil
                if self.cliGeneration == generation, self.cliName == name, self.selectedCLI == selectedPath {
                    switch result {
                    case .success(let version):
                        self.cliStatus = name == "codex" ? version.codexStatus
                            : "Version command exited successfully. Raw CLI output discarded.\nAuthentication: unknown. No model call."
                    case .failure(let error):
                        self.cliStatus = "Version probe: \(error.rawValue). Authentication: unknown."
                        if name == "codex" {
                            self.cliStatus += "\n" + CLIVersionResult(codexVersion: nil).codexStatus
                        }
                    }
                } else {
                    self.cliStatus = "Version probe closed. Authentication: unknown."
                }
                self.notifyStoppedIfIdle()
                self.resumeModelCatalog()
            }
        }
        cliRun = run
        run.start(executable: candidate.url)
    }

    func cancelCLI() {
        guard let run = cliRun else { return }
        cliStatus = "Cancelling the selected CLI and its owned process group..."
        run.cancel()
    }

    func closePanel() {
        monitor.cancelPendingSelection()
        cancelModelCatalog()
        modelCatalog.disconnect()
        translationIntentID = UUID()
        discardBufferedDelta()
        historySearchActive = false
        invalidateHistory(retireActive: true)
        historyPage = []
        historyTotal = nil
        loadedHistory = nil
        historyPhase = .idle
        historyStatus = text("History connection closed. Refresh explicitly to reload.",
                             "历史记录连接已关闭，请手动刷新以重新加载。")
        draft = nil
        cliChangeDeferred = false
        isLocalDictionaryResult = false
        resultSources = []
        productPhase = .idle
        productMessage = ""
        resultInput = ""
        resultHasOriginalInput = true
        primaryResult = ""
        resultGeneration = UUID()
        activeAction = nil
        translationOrigin = "text"
        hideCurrentOutput = true
        openAfterStop = false
        historyRequested = false
        latest.select(nil)
        output = ""
        screen.clear()
        cliGeneration = UUID()
        cancelCLI()
        if connection != nil { stopHelper() }
    }

    func prepareToQuit() {
        catalogShutDown = true
        imageTranslation.shutdown()
        plainPasteConfigAfterStop = false
        plainPaste.shutdown()
        suspendMonitor()
        closePanel()
    }

    func stopPersistingPreferencesForUninstall() {
        persistsPreferences = false
    }

    private func notifyStoppedIfIdle() {
        if !hasProcesses { onStopped?() }
    }
}
