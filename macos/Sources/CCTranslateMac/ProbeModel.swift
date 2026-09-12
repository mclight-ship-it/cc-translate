import AppKit
import Combine
import CCTranslateSupport

@MainActor
final class ProbeModel: ObservableObject {
    @Published var input = "P0 synthetic fixture"
    @Published private(set) var output = ""
    @Published private(set) var status = "Not connected. No translation/model provider in P0."
    @Published private(set) var ready = false
    @Published private(set) var connected = false
    @Published private(set) var active = false
    @Published private(set) var permissions = "Not checked."
    @Published private(set) var monitorStatus = "Passive double Cmd+C monitor is stopped."
    @Published var cliName = "codex"
    @Published private(set) var candidates: [CLICandidate] = []
    @Published var selectedCLI = ""
    @Published private(set) var cliStatus = "Not located. Authentication: unknown."
    @Published private(set) var cliBusy = false
    let screen = ScreenProbe()
    let monitor = PassiveCopyMonitor()
    var onSelection: ((SelectionResult) -> Void)?
    var onStopped: (() -> Void)?
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
            status = "Starting bundled isolated Python; waiting for ready..."
            connection.start(runtime: runtime)
        } catch let error as ProbeError {
            self.error = error
            status = "Cannot start: \(error.rawValue). No host Python fallback."
        } catch {
            self.error = .launchFailed
            status = "Cannot start bundled helper. No host Python fallback."
        }
    }

    func fixture() {
        guard input.utf8.count <= 8192 else {
            status = "Input exceeds 8192 UTF-8 bytes."
            return
        }
        request(["operation": .string("fixture"), "text": .string(input)])
    }

    func runtimeProbe(https: Bool) {
        request(["operation": .string("runtime_probe"), "https": .bool(https)])
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
                status = "Ready: fixture + runtime_probe. Fixture is NOT translation."
                return
            }
            if event.isTerminal { pending.remove(event.id) }
            // The transport validated seq/terminal rules even for events hidden here.
            guard latest.accepts(event) else { return }
            active = pending.contains(event.id)
            switch event.type {
            case "accepted": status = "Accepted (P0 probe)."
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
                    status = "Completed SYNTHETIC FIXTURE - NOT translation."
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
            case "cancelled": status = "Cancelled."
            case "failed": status = "Helper request failed: \(event.safeFailureCode). No fallback or automatic retry."
            default: break
            }
        case .failure(let error):
            self.error = error
            ready = false
            active = false
            status = "Helper failure: \(error.rawValue). Restart explicitly; requests are not replayed."
        case .stopped:
            stopping = false
            connected = false
            ready = false
            active = false
            pending.removeAll()
            connection = nil
            if error == nil { status = "Helper stopped." }
            notifyStoppedIfIdle()
        }
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
        let run = CLIVersionRun { [weak self] result in
            MainActor.assumeIsolated {
                guard let self = self else { return }
                self.cliBusy = false
                self.cliRun = nil
                if self.cliGeneration == generation {
                    switch result {
                    case .success:
                        self.cliStatus = "Version command exited successfully. All CLI output discarded.\nAuthentication: unknown. No model call."
                    case .failure(let error):
                        self.cliStatus = "Version probe: \(error.rawValue). Authentication: unknown."
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
        cliStatus = "Cancelling the selected CLI process (direct child only)..."
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
