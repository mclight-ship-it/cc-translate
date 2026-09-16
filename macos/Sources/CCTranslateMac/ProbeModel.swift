import AppKit
import Combine
import SwiftUI
import CCTranslateSupport

enum TranslationPhase: Equatable {
    case idle, preparing, translating, completed, cancelled, failed
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
    @Published private(set) var historyEnabled = true
    @Published var direction = "auto" {
        didSet { if !loadingConfiguration { directionEdited = true } }
    }
    @Published var modelProfile = "auto-fast" {
        didSet { if !loadingConfiguration { modelEdited = true } }
    }
    @Published var translatePassiveSelections = false
    @Published var interfaceLanguage = "system"
    @Published var appearance = "system"
    @Published var historySearch = ""
    @Published var historyFilter = "all"
    @Published private(set) var productPhase: TranslationPhase = .idle
    @Published private(set) var productMessage = ""
    @Published private(set) var needsCLI = false
    @Published private(set) var monitorEnabled = false
    @Published private(set) var resultKind = "text"
    @Published private(set) var resultInput = ""
    private(set) var translationOrigin = "text"
    @Published private(set) var historyPage: [HistoryRow] = []
    @Published private(set) var historyStatus = "History has not been read."
    @Published private(set) var historyBusy = false
    @Published private(set) var hasNextHistoryPage = false
    @Published private(set) var permissions = "Not checked."
    @Published private(set) var monitorStatus = "Passive double Cmd+C monitor is stopped."
    @Published var cliName = "codex"
    @Published private(set) var candidates: [CLICandidate] = []
    @Published var selectedCLI = "" {
        didSet {
            if selectedCLI != oldValue {
                cliStatus = "Selection changed; not executed. Run --version explicitly. Authentication: unknown."
                if connected && connectionMode != .diagnostic {
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
    @Published private(set) var cliStatus = "Not located. Authentication: unknown."
    @Published private(set) var cliBusy = false
    let screen = ScreenProbe()
    let monitor = PassiveCopyMonitor()
    var onSelection: ((SelectionResult) -> Void)?
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
        var configurationSaved = false
    }
    private var draft: Draft?
    private var presentationLoaded = false
    private var loadingConfiguration = false
    private var directionEdited = false
    private var modelEdited = false
    private var openAfterStop = false
    private var historyRequested = false
    private var historyAppending = false
    private var hideCurrentOutput = false
    private var bufferedDelta = ""
    private var renderUpdate: DispatchWorkItem?
    private let preferences: UserDefaults?
    private let persistsPreferences: Bool
    private let makeConnection: (@escaping (HelperNotice) -> Void) -> AppHelperClient
    private let runtimeProvider: () throws -> BundleRuntime
    private let locateCandidates: (String, URL?) -> [CLICandidate]
    private var savedConfiguration: [String: JSONValue]?
    private var configLoadID: String?
    private var configSaveID: String?
    private var historyID: String?
    private var historyClearID: String?
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
    var hasProcesses: Bool { connection != nil || cliRun != nil }
    var preparing: Bool { productPhase == .preparing }
    var preferredColorScheme: ColorScheme? {
        appearance == "dark" ? .dark : appearance == "light" ? .light : nil
    }
    var filteredHistory: [HistoryRow] {
        historyPage.filter {
            (historyFilter == "all" || $0.kind == historyFilter) &&
            (historySearch.isEmpty || $0.input.localizedCaseInsensitiveContains(historySearch) ||
             $0.output.localizedCaseInsensitiveContains(historySearch))
        }
    }
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
         }) {
        self.preferences = preferences
        self.persistsPreferences = persistsPreferences
        self.makeConnection = makeConnection
        self.runtimeProvider = runtimeProvider
        self.locateCandidates = locateCandidates
        monitor.onSelection = { [weak self] result in self?.onSelection?(result) }
        monitor.onStop = { [weak self] reason in
            self?.monitorStatus = reason
            self?.monitorEnabled = false
        }
    }

    func persistPresentation() {
        guard persistsPreferences else { return }
        let defaults = preferences ?? .standard
        defaults.set(interfaceLanguage, forKey: "interfaceLanguage")
        defaults.set(appearance, forKey: "appearance")
        if cliName == "codex", !selectedCLI.isEmpty {
            defaults.set(selectedCLI, forKey: "selectedCodexPath")
        }
        onPresentationChanged?()
    }

    func loadPresentation() {
        if !presentationLoaded {
            if persistsPreferences {
                let defaults = preferences ?? .standard
                interfaceLanguage = defaults.string(forKey: "interfaceLanguage") ?? "system"
                appearance = defaults.string(forKey: "appearance") ?? "system"
                if let saved = defaults.string(forKey: "selectedCodexPath"), !saved.isEmpty {
                    userCLI["codex"] = URL(fileURLWithPath: saved)
                }
            }
            presentationLoaded = true
            onPresentationChanged?()
        }
    }

    func openProduct() {
        loadPresentation()
        if connected {
            if connectionMode == .configuration && selectedCLI.isEmpty { return }
            if connectionMode != .translation {
                openAfterStop = true
                stopHelper()
            }
            return
        }
        cliName = "codex"
        if !candidates.contains(where: { $0.url.path == selectedCLI && $0.executable }) {
            locateCLI()
        }
        guard !selectedCLI.isEmpty else {
            needsCLI = true
            productMessage = text("Choose your Codex executable in Settings to get started.",
                                  "在设置中选择 Codex，即可开始翻译。")
            startConnection(mode: .configuration)
            return
        }
        needsCLI = false
        persistPresentation()
        startNativeTranslation()
    }

    func startHelper() {
        startConnection(mode: .diagnostic)
    }

    func startNativeTranslation() {
        guard cliName == "codex", candidates.contains(where: {
            $0.url.path == selectedCLI && $0.executable
        }) else {
            status = "Locate or choose a Codex executable in CLI locator first. No installation or login is automatic."
            return
        }
        startConnection(mode: .translation)
    }

    private func startConnection(mode: ConnectionMode) {
        guard connection == nil else { return }
        do {
            let runtime = try runtimeProvider()
            connectionID = UUID()
            let id = connectionID
            let connection = makeConnection { [weak self] notice in
                MainActor.assumeIsolated {
                    guard let self = self, self.connectionID == id else { return }
                    self.receive(notice)
                }
            }
            self.connection = connection
            error = nil
            stopping = false
            connected = true
            connectionMode = mode
            nativeTranslation = mode == .translation
            settingsReady = false
            output = ""
            latest.select(nil)
            status = "Starting bundled isolated Python; waiting for ready..."
            if mode == .translation {
                let home = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
                var environment = ProcessInfo.processInfo.environment
                environment["HOME"] = home.path
                let inheritedPath = environment["PATH"].map { ":" + $0 } ?? ""
                environment["PATH"] = CLILocator.searchPath + inheritedPath
                connection.startTranslation(
                    runtime: runtime, home: home,
                    codexCommand: URL(fileURLWithPath: selectedCLI), environment: environment)
            } else if mode == .configuration {
                connection.startConfiguration(runtime: runtime,
                                              home: URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true))
            } else {
                connection.start(runtime: runtime)
            }
        } catch let error as ProbeError {
            self.error = error
            status = "Cannot start: \(error.rawValue). No host Python fallback."
            failPreparation(status)
        } catch {
            self.error = .launchFailed
            status = "Cannot start bundled helper. No host Python fallback."
            failPreparation(status)
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
        loadPresentation()
        guard !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, input.utf8.count <= 8192 else {
            failPreparation(text("Enter some text (up to 8192 UTF-8 bytes).",
                                 "请输入要翻译的文字（最多 8192 UTF-8 字节）。"))
            return
        }
        draft = Draft(text: input, origin: origin, useCache: useCache,
                      direction: direction, model: modelProfile, language: usesChinese ? "zh_CN" : "en_US",
                      useSavedDirection: !settingsReady && !directionEdited,
                      useSavedModel: !settingsReady && !modelEdited)
        translationOrigin = origin
        productPhase = .preparing
        productMessage = text("Preparing translation…", "正在准备翻译…")
        openProduct()
        if needsCLI {
            failPreparation(productMessage)
            onConfigurationRequired?()
            return
        }
        if active { requestCancellation() }
        resumeTranslation()
    }

    private func resumeTranslation() {
        guard var requested = draft, nativeTranslation, ready, settingsReady, !settingsBusy,
              !active, let connection = connection else { return }
        let savedLanguage = savedConfiguration?["language"]?.string
        if savedConfiguration?["direction"] != .string(requested.direction) ||
            savedConfiguration?["codex_model"] != .string(requested.model) ||
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
        draft = nil
        let id = UUID().uuidString
        latest.select(id)
        pending.insert(id)
        active = true
        output = ""
        discardBufferedDelta()
        hideCurrentOutput = false
        resultInput = requested.text
        resultKind = "text"
        productPhase = .translating
        productMessage = text("Translating…", "正在翻译…")
        onTranslationStarted?()
        status = "Translation requested. Uses the selected native CLI; no automatic retry."
        _ = connection.translate(text: requested.text, appLanguage: requested.language,
                                 origin: requested.origin, useCache: requested.useCache,
                                 recordHistory: true, id: id, timeout: 110)
    }

    private func failPreparation(_ message: String) {
        draft = nil
        productPhase = .failed
        productMessage = message
    }

    func translateSelection(_ selection: SelectionResult) {
        translationOrigin = "selection"
        switch selection {
        case .present(let text):
            input = text
            translate(origin: "selection")
        case .absent:
            status = "No selected text. Nothing submitted."
            failPreparation(text("No text selected.", "没有选中文字。"))
        case .unknown(let reason):
            status = "Selection unavailable (\(reason.rawValue)). No clipboard fallback or submission."
            failPreparation(text("Could not read the selection. Open Translate to type or paste instead.",
                                 "无法读取选中文字，请打开翻译窗口输入或粘贴。"))
        }
        onTranslationResult?(status + (output.isEmpty ? "" : "\n\n" + output))
    }

    func loadSettings() {
        guard connectionMode != .diagnostic, ready, !settingsBusy, let connection = connection else { return }
        settingsBusy = true
        settingsReady = false
        let id = UUID().uuidString
        configLoadID = id
        connection.loadConfiguration(id: id)
    }

    func saveSettings(history: Bool? = nil) {
        saveConfiguration(history: history ?? historyEnabled, direction: direction, model: modelProfile)
    }

    private func saveConfiguration(history enabled: Bool, direction: String, model: String,
                                   language: String? = nil) {
        guard connectionMode != .diagnostic, ready, !settingsBusy, var config = savedConfiguration,
              let connection = connection else { return }
        if !enabled, active { cancel() }
        config["history_enabled"] = .bool(enabled)
        config["direction"] = .string(direction)
        config["codex_model"] = .string(model)
        config["model_provider"] = .string("codex_cli")
        config["language"] = .string(language ?? (usesChinese ? "zh_CN" : "en_US"))
        settingsBusy = true
        let id = UUID().uuidString
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func loadHistory(next: Bool = false) {
        if connectionMode == .diagnostic || !ready || !settingsReady {
            historyRequested = true
            openProduct()
            return
        }
        guard connectionMode != .diagnostic, ready, !historyBusy, let connection = connection else { return }
        historyBusy = true
        historyAppending = next
        let id = UUID().uuidString
        historyID = id
        connection.loadHistory(pageSize: 20, cursor: next ? historyCursor : .null, id: id)
    }

    func clearHistory() {
        guard connectionMode != .diagnostic, ready, !historyBusy, let connection = connection else { return }
        if active { cancel() }
        historyBusy = true
        let id = UUID().uuidString
        historyClearID = id
        connection.clearHistory(id: id)
    }

    func copyResult() {
        copyText(output)
    }

    func copyBilingual() {
        guard !output.isEmpty else { return }
        copyText(resultInput + "\n\n" + output)
    }

    func reuseHistory(_ row: HistoryRow) {
        cancel()
        translationOrigin = "text"
        discardBufferedDelta()
        input = row.input
        resultInput = row.input
        output = row.output
        resultKind = row.kind
        hideCurrentOutput = true
        productPhase = .completed
        productMessage = text("From history", "来自历史记录")
    }

    func clearTranslation() {
        cancel()
        translationOrigin = "text"
        discardBufferedDelta()
        input = ""
        output = ""
        resultInput = ""
        hideCurrentOutput = true
        productPhase = .idle
        productMessage = ""
    }

    func copyText(_ text: String) {
        guard !text.isEmpty else { return }
        NSPasteboard.general.clearContents()
        if !NSPasteboard.general.setString(text, forType: .string) {
            status = "Could not copy the result."
            historyStatus = status
        }
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
        if draft != nil {
            draft = nil
            productPhase = .cancelled
            productMessage = text("Cancelled", "已取消")
        }
        requestCancellation()
    }

    private func requestCancellation() {
        guard let id = latest.id, pending.contains(id), let connection = connection else { return }
        status = "Cancellation requested; waiting for the original request's terminal event."
        connection.send(ClientMessage(
            id: UUID().uuidString, type: "cancel", payload: ["request_id": .string(id)]
        ))
    }

    func stopHelper() {
        stopping = true
        ready = false
        if error == nil { status = "Stopping helper..." }
        connection?.stop()
    }

    private func receive(_ notice: HelperNotice) {
        switch notice {
        case .event(let event):
            guard error == nil, !stopping else { return }
            if event.type == "ready" {
                ready = true
                status = nativeTranslation
                    ? "Native connection ready. CLI/account/model availability is not yet verified."
                    : connectionMode == .configuration ? "Settings and history ready; translation needs a Codex installation."
                    : "Ready: fixture + runtime_probe. Fixture is NOT translation."
                if connectionMode != .diagnostic { loadSettings() }
                return
            }
            if handleBusinessEvent(event) { return }
            if event.isTerminal { pending.remove(event.id) }
            // The transport validated seq/terminal rules even for events hidden here.
            guard latest.accepts(event) else { return }
            active = pending.contains(event.id)
            switch event.type {
            case "accepted": status = nativeTranslation ? "Translation accepted." : "Accepted (P0 probe)."
            case "started": status = "Native request started; submission status is not yet known."
            case "delta":
                if !hideCurrentOutput { bufferedDelta += event.payload["text"]?.string ?? "" }
                if output.utf8.count + bufferedDelta.utf8.count > 65_536 {
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
                    if !hideCurrentOutput { output = text }
                    if nativeTranslation {
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
                    productPhase = .cancelled
                    productMessage = text("Cancelled", "已取消")
                }
            case "failed":
                flushBufferedDelta()
                status = event.safeFailureMessage
                if !hideCurrentOutput {
                    productPhase = .failed
                    productMessage = event.safeFailureMessage
                }
            default: break
            }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
            if event.isTerminal { resumeTranslation() }
        case .failure(let error):
            flushBufferedDelta()
            self.error = error
            ready = false
            active = false
            status = "Helper failure: \(error.rawValue). Restart explicitly; requests are not replayed."
            if error == .translationOutcomeUnknown {
                status = "Translation outcome unknown. The CLI may have received the request and history may have changed. Not retried."
            }
            failPreparation(status)
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
        case .stopped:
            discardBufferedDelta()
            let reopen = openAfterStop
            openAfterStop = false
            stopping = false
            connected = false
            ready = false
            active = false
            pending.removeAll()
            savedConfiguration = nil
            settingsReady = false
            settingsBusy = false
            historyBusy = false
            configLoadID = nil
            configSaveID = nil
            historyID = nil
            historyClearID = nil
            if !reopen {
                stopMonitor()
                translatePassiveSelections = false
            }
            historyPage = []
            historyCursor = .null
            hasNextHistoryPage = false
            connection = nil
            if !reopen, draft != nil {
                failPreparation(text("Connection closed. Translate again when you are ready.",
                                     "连接已关闭，准备好后可重新翻译。"))
            }
            if error == nil { status = "Helper stopped." }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
            notifyStoppedIfIdle()
            if reopen { openProduct() }
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

    private func handleBusinessEvent(_ event: ServerEvent) -> Bool {
        if event.id == configLoadID || event.id == configSaveID {
            guard event.isTerminal else { return true }
            settingsBusy = false
            if event.type == "completed" {
                if event.id == configSaveID {
                    configSaveID = nil
                    status = "Settings saved. Reloading their normalized view; no write replay."
                    loadSettings()
                    return true
                }
                guard let config = event.payload["config"]?.object,
                      case let .bool(enabled)? = config["history_enabled"],
                      let savedDirection = config["direction"]?.string,
                      let profile = config["codex_model"]?.string else {
                    error = .invalidTransition
                    settingsReady = false
                    status = "Invalid normalized settings response; stopping the connection."
                    stopHelper()
                    return true
                }
                savedConfiguration = config
                historyEnabled = enabled
                if draft?.useSavedDirection == true {
                    draft?.direction = savedDirection
                    draft?.useSavedDirection = false
                }
                if draft?.useSavedModel == true {
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
                if modelProfile == profile && (draft == nil || draft?.model == modelProfile) {
                    modelEdited = false
                }
                settingsReady = true
                status = "Native settings loaded. Account and model access require an explicit translation."
                if !needsCLI && !preparing && !active && output.isEmpty {
                    productMessage = ""
                    productPhase = .idle
                }
            } else {
                status = "Settings operation failed: \(event.safeFailureCode). No automatic retry."
                failPreparation(status)
            }
            configLoadID = nil
            configSaveID = nil
            resumeTranslation()
            if historyRequested && settingsReady {
                historyRequested = false
                loadHistory()
            }
            return true
        }
        if event.id == historyID || event.id == historyClearID {
            guard event.isTerminal else { return true }
            historyBusy = false
            if event.type == "completed", event.id == historyClearID {
                historyPage = []
                historyCursor = .null
                hasNextHistoryPage = false
                historyStatus = "History cleared. Later translations may still be recorded if history is enabled."
            } else if event.type == "completed", case let .array(entries)? = event.payload["entries"] {
                let revision = event.payload["revision"]?.string ?? ""
                let offset = historyAppending ? historyPage.count : 0
                let rows = entries.enumerated().map { index, value in
                    HistoryRow(id: "\(revision)-\(offset + index)", input: value.object?["input"]?.string ?? "",
                               output: value.object?["output"]?.string ?? "",
                               kind: value.object?["kind"]?.string ?? "text",
                               timestamp: value.object?["ts"]?.string ?? "")
                }
                historyPage = historyAppending ? historyPage + rows : rows
                historyCursor = event.payload["next_cursor"] ?? .null
                hasNextHistoryPage = historyCursor != .null
                historyStatus = text("\(historyPage.count) entries loaded", "已加载 \(historyPage.count) 条记录")
            } else {
                historyStatus = "History operation failed: \(event.safeFailureCode). Reload explicitly if history changed."
            }
            historyID = nil
            historyClearID = nil
            return true
        }
        return false
    }

    func refreshPermissions() {
        let snapshot = Permissions.snapshot()
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
        monitorStatus = granted ? "Input Monitoring granted; start monitoring explicitly."
            : "Input Monitoring not granted. System Settings/restart may be required."
        refreshPermissions()
    }

    func startMonitor() {
        do {
            try monitor.start()
            monitorEnabled = true
            monitorStatus = "Observing double Cmd+C only; AX selectedText only; no clipboard fallback."
        } catch let error as ProbeError {
            monitorStatus = "Monitor not started: \(error.rawValue)."
        } catch {
            monitorStatus = "Monitor not started."
        }
        refreshPermissions()
    }

    func stopMonitor() {
        monitor.stop()
        monitorEnabled = false
        monitorStatus = "Passive monitor stopped."
    }

    func locateCLI() {
        candidates = locateCandidates(cliName, userCLI[cliName])
        selectedCLI = candidates.first(where: \.executable)?.url.path ?? ""
        cliStatus = "Paths checked only; not executed. Authentication: unknown."
        needsCLI = cliName == "codex" && selectedCLI.isEmpty
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
        discardBufferedDelta()
        draft = nil
        productPhase = .idle
        productMessage = ""
        resultInput = ""
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
        stopMonitor()
        closePanel()
    }

    private func notifyStoppedIfIdle() {
        if !hasProcesses { onStopped?() }
    }
}
