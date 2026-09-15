import AppKit
import Combine
import CCTranslateSupport

@MainActor
final class ProbeModel: ObservableObject {
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
    @Published var direction = "auto"
    @Published var modelProfile = "auto-fast"
    @Published var translatePassiveSelections = false
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
    struct HistoryRow: Identifiable {
        let id: String
        let input: String
        let output: String
    }
    private var savedConfiguration: [String: JSONValue]?
    private var configLoadID: String?
    private var configSaveID: String?
    private var historyID: String?
    private var historyClearID: String?
    private var historyCursor: JSONValue = .null
    private var connection: HelperConnection?
    private var connectionID = UUID()
    private var latest = LatestRequest()
    private var pending = Set<String>()
    private var error: ProbeError?
    private var stopping = false
    private var cliRun: CLIVersionRun?
    private var cliGeneration = UUID()
    private var userCLI: [String: URL] = [:]
    var hasProcesses: Bool { connection != nil || cliRun != nil }

    init() {
        monitor.onSelection = { [weak self] result in self?.onSelection?(result) }
        monitor.onStop = { [weak self] reason in self?.monitorStatus = reason }
    }

    func startHelper() {
        startConnection(native: false)
    }

    func startNativeTranslation() {
        guard cliName == "codex", candidates.contains(where: {
            $0.url.path == selectedCLI && $0.executable
        }) else {
            status = "Locate or choose a Codex executable in CLI locator first. No installation or login is automatic."
            return
        }
        startConnection(native: true)
    }

    private func startConnection(native: Bool) {
        guard connection == nil else { return }
        do {
            let runtime = try BundleRuntime()
            connectionID = UUID()
            let id = connectionID
            let connection = HelperConnection { [weak self] notice in
                MainActor.assumeIsolated {
                    guard let self = self, self.connectionID == id else { return }
                    self.receive(notice)
                }
            }
            self.connection = connection
            error = nil
            stopping = false
            connected = true
            nativeTranslation = native
            settingsReady = false
            output = ""
            latest.select(nil)
            status = "Starting bundled isolated Python; waiting for ready..."
            if native {
                let home = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
                var environment = ProcessInfo.processInfo.environment
                environment["HOME"] = home.path
                let inheritedPath = environment["PATH"].map { ":" + $0 } ?? ""
                environment["PATH"] = CLILocator.searchPath + inheritedPath
                connection.startTranslation(
                    runtime: runtime, home: home,
                    codexCommand: URL(fileURLWithPath: selectedCLI), environment: environment)
            } else {
                connection.start(runtime: runtime)
            }
        } catch let error as ProbeError {
            self.error = error
            status = "Cannot start: \(error.rawValue). No host Python fallback."
        } catch {
            self.error = .launchFailed
            status = "Cannot start bundled helper. No host Python fallback."
        }
    }

    func fixture() {
        guard !nativeTranslation else { return }
        guard input.utf8.count <= 8192 else {
            status = "Input exceeds 8192 UTF-8 bytes."
            return
        }
        request(["operation": .string("fixture"), "text": .string(input)])
    }

    func runtimeProbe(https: Bool) {
        guard !nativeTranslation else { return }
        request(["operation": .string("runtime_probe"), "https": .bool(https)])
    }

    func translate(origin: String = "text") {
        guard nativeTranslation, ready, settingsReady, !settingsBusy, let connection = connection else {
            status = "Enable native Codex and wait for its settings operation to finish first."
            return
        }
        guard !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, input.utf8.count <= 8192 else {
            status = "Enter non-empty text within 8192 UTF-8 bytes."
            return
        }
        if active { cancel() }
        let id = UUID().uuidString
        latest.select(id)
        pending.insert(id)
        active = true
        output = ""
        onTranslationStarted?()
        status = "Translation requested. Uses the selected native CLI; no automatic retry."
        connection.translate(text: input, appLanguage: "en_US", origin: origin, useCache: true,
                             recordHistory: true, id: id)
    }

    func translateSelection(_ selection: SelectionResult) {
        onTranslationStarted?()
        switch selection {
        case .present(let text):
            input = text
            translate(origin: "selection")
        case .absent:
            status = "No selected text. Nothing submitted."
        case .unknown(let reason):
            status = "Selection unavailable (\(reason.rawValue)). No clipboard fallback or submission."
        }
        onTranslationResult?(status + (output.isEmpty ? "" : "\n\n" + output))
    }

    func loadSettings() {
        guard nativeTranslation, ready, !settingsBusy, let connection = connection else { return }
        settingsBusy = true
        settingsReady = false
        let id = UUID().uuidString
        configLoadID = id
        connection.loadConfiguration(id: id)
    }

    func saveSettings(history: Bool? = nil) {
        guard nativeTranslation, ready, !settingsBusy, var config = savedConfiguration,
              let connection = connection else { return }
        let enabled = history ?? historyEnabled
        if !enabled, active { cancel() }
        config["history_enabled"] = .bool(enabled)
        config["direction"] = .string(direction)
        config["codex_model"] = .string(modelProfile)
        config["model_provider"] = .string("codex_cli")
        settingsBusy = true
        let id = UUID().uuidString
        configSaveID = id
        connection.saveConfiguration(config, id: id)
    }

    func loadHistory(next: Bool = false) {
        guard nativeTranslation, ready, !historyBusy, let connection = connection else { return }
        historyBusy = true
        let id = UUID().uuidString
        historyID = id
        connection.loadHistory(pageSize: 20, cursor: next ? historyCursor : .null, id: id)
    }

    func clearHistory() {
        guard nativeTranslation, ready, !historyBusy, let connection = connection else { return }
        if active { cancel() }
        historyBusy = true
        let id = UUID().uuidString
        historyClearID = id
        connection.clearHistory(id: id)
    }

    func copyResult() {
        copyText(output)
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
                    : "Ready: fixture + runtime_probe. Fixture is NOT translation."
                if nativeTranslation { loadSettings() }
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
                output += event.payload["text"]?.string ?? ""
                if output.utf8.count > 65_536 {
                    error = .frameTooLarge
                    status = "Probe output limit exceeded; stopping helper."
                    stopHelper()
                }
            case "completed":
                if let text = event.payload["text"]?.string {
                    output = text
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
                status = event.payload["submitted"] == .bool(true)
                    ? "Cancelled after possible CLI submission. Submission cannot be rolled back."
                    : "Cancelled before native submission."
            case "failed": status = event.safeFailureMessage
            default: break
            }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
        case .failure(let error):
            self.error = error
            ready = false
            active = false
            status = "Helper failure: \(error.rawValue). Restart explicitly; requests are not replayed."
            if error == .translationOutcomeUnknown {
                status = "Translation outcome unknown. The CLI may have received the request and history may have changed. Not retried."
            }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
        case .stopped:
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
            translatePassiveSelections = false
            historyPage = []
            historyCursor = .null
            hasNextHistoryPage = false
            connection = nil
            if error == nil { status = "Helper stopped." }
            if nativeTranslation { onTranslationResult?(status + "\n\n" + output) }
            notifyStoppedIfIdle()
        }
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
                direction = savedDirection
                modelProfile = profile
                settingsReady = true
                status = "Native settings loaded. Account and model access require an explicit translation."
            } else {
                status = "Settings operation failed: \(event.safeFailureCode). No automatic retry."
            }
            configLoadID = nil
            configSaveID = nil
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
                historyPage = entries.enumerated().map { index, value in
                    HistoryRow(id: "\(revision)-\(index)", input: value.object?["input"]?.string ?? "",
                               output: value.object?["output"]?.string ?? "")
                }
                historyCursor = event.payload["next_cursor"] ?? .null
                hasNextHistoryPage = historyCursor != .null
                historyStatus = "Showing \(entries.count) entries. \(hasNextHistoryPage ? "Another page is available." : "End of history.")"
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
        monitorStatus = "Passive monitor stopped."
    }

    func locateCLI() {
        candidates = CLILocator.candidates(name: cliName, userURL: userCLI[cliName])
        selectedCLI = candidates.first(where: \.executable)?.url.path ?? ""
        cliStatus = "Paths checked only; not executed. Authentication: unknown."
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
