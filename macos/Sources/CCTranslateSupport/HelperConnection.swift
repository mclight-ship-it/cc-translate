import Foundation
import Darwin

public struct BundleRuntime {
    public let executable: URL
    public let launcher: URL
    private let contents: URL

    public init(bundle: Bundle = .main) throws {
        guard let resources = bundle.resourceURL else { throw ProbeError.bundleMissing }
        try self.init(appURL: bundle.bundleURL, resourcesURL: resources)
    }

    public init(appURL: URL, resourcesURL: URL? = nil) throws {
        contents = appURL.appendingPathComponent("Contents", isDirectory: true)
        executable = contents.appendingPathComponent("Helpers/python/bin/python3")
        launcher = (resourcesURL ?? contents.appendingPathComponent("Resources", isDirectory: true))
            .appendingPathComponent("Core/launch.py")
        let runtimeRoot = contents.resolvingSymlinksInPath().appendingPathComponent("Helpers/python").path + "/"
        let coreRoot = contents.resolvingSymlinksInPath().appendingPathComponent("Resources/Core").path + "/"
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: executable.path, isDirectory: &isDirectory),
              !isDirectory.boolValue, FileManager.default.isExecutableFile(atPath: executable.path),
              executable.resolvingSymlinksInPath().path.hasPrefix(runtimeRoot),
              launcher.resolvingSymlinksInPath().path.hasPrefix(coreRoot),
              FileManager.default.isReadableFile(atPath: launcher.path) else {
            throw ProbeError.bundleMissing
        }
    }

    public func configurationApplicationIdentifier() throws -> String {
        do {
            let data = try Data(contentsOf: contents.appendingPathComponent("Info.plist"))
            guard let plist = try PropertyListSerialization.propertyList(from: data, format: nil)
                    as? [String: Any],
                  let identifier = plist["CFBundleIdentifier"] as? String,
                  identifier.range(of: #"\A[A-Za-z0-9][A-Za-z0-9.-]*\z"#,
                                   options: .regularExpression) != nil else {
                throw ProbeError.bundleMissing
            }
            return identifier
        } catch {
            throw ProbeError.bundleMissing
        }
    }
}

public enum HelperNotice {
    case event(ServerEvent)
    case failure(ProbeError)
    case stopped
}

// One connection is one process and one ID namespace. It is never restarted or replayed.
public final class HelperConnection {
    private let queue = DispatchQueue(label: "dev.cc-translate.helper.state")
    private let writer = DispatchQueue(label: "dev.cc-translate.helper.stdin")
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let errors = Pipe()
    private var state = ProtocolState()
    private var framer = LineFramer()
    private var stderrFramer = LineFramer()
    private var started = false
    private var stopping = false
    private var failed = false
    private var stdoutEnded = false
    private var stderrEnded = false
    private var exitStatus: Int32?
    private var didFinish = false
    private var stderrBytes = 0
    private var pendingWrites = 0
    private var inputClosed = false
    private var terminationScheduled = false
    private var terminationRequested = false
    private var deadlines: [String: DispatchWorkItem] = [:]
    private let notice: (HelperNotice) -> Void

    public init(notice: @escaping (HelperNotice) -> Void) { self.notice = notice }

    public func start(runtime: BundleRuntime) {
        start(runtime: runtime, configurationHome: nil)
    }

    public func startConfiguration(runtime: BundleRuntime, home: URL) {
        start(runtime: runtime, configurationHome: home)
    }

    public func startBusiness(runtime: BundleRuntime, home: URL) {
        startConfiguration(runtime: runtime, home: home)
    }

    public func startTranslation(runtime: BundleRuntime, home: URL, codexCommand: URL,
                                 environment: [String: String]) {
        start(runtime: runtime, configurationHome: home, codexCommand: codexCommand,
              codexEnvironment: environment)
    }

    static func translationEnvironment(home: URL, codexCommand: URL,
                                       environment: [String: String]) throws -> [String: String] {
        guard home.isFileURL, home.path.hasPrefix("/"), !home.path.contains("\0"),
              codexCommand.isFileURL, codexCommand.path.hasPrefix("/"),
              !codexCommand.path.contains("\0"),
              environment["HOME"] == home.path, environment["PATH"] != nil,
              environment.allSatisfy({
                  !$0.key.isEmpty && !$0.key.contains("=") && !$0.key.contains("\0") && !$0.value.contains("\0")
              }) else { throw ProbeError.translationUnavailable }
        let encoded = try JSONValue.object(environment.mapValues(JSONValue.string)).encoded()
        guard encoded.count <= 32_768 else { throw ProbeError.translationUnavailable }
        return [
            "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": home.path,
            "CC_TRANSLATE_CODEX_ENV": String(decoding: encoded, as: UTF8.self)
        ]
    }

    private func start(runtime: BundleRuntime, configurationHome: URL?, codexCommand: URL? = nil,
                       codexEnvironment: [String: String] = [:]) {
        queue.async {
            guard !self.started, !self.stopping else { return }
            self.started = true
            self.state = ProtocolState(mode: codexCommand != nil ? .translation :
                                        (configurationHome == nil ? .diagnostic : .configuration))
            self.process.executableURL = runtime.executable
            self.process.arguments = ["-I", "-B", runtime.launcher.path]
            do {
                self.process.environment = [
                    "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                    "HOME": configurationHome?.path ?? NSHomeDirectory()
                ]
                if let home = configurationHome {
                    guard home.isFileURL, home.path.hasPrefix("/"),
                          !home.pathComponents.contains(".."),
                          !home.pathComponents.contains(where: { $0.lowercased().hasSuffix(".app") }) else {
                        throw ProbeError.configUnavailable
                    }
                    let identifier = try runtime.configurationApplicationIdentifier()
                    self.process.arguments = ["-I", "-B", runtime.launcher.path,
                                              "--config-home", home.path, "--application-id", identifier]
                    if let command = codexCommand {
                        self.process.environment = try Self.translationEnvironment(
                            home: home, codexCommand: command, environment: codexEnvironment
                        )
                        self.process.arguments?.append(contentsOf: ["--codex-command", command.path])
                    }
                }
            } catch {
                self.fail((error as? ProbeError) ?? .bundleMissing)
                self.finishWithoutLaunch()
                return
            }
            self.process.currentDirectoryURL = runtime.launcher.deletingLastPathComponent()
            self.process.standardInput = self.input.fileHandleForReading
            self.process.standardOutput = self.output.fileHandleForWriting
            self.process.standardError = self.errors.fileHandleForWriting
            self.process.terminationHandler = { [weak self] process in
                guard let self = self else { return }
                self.queue.async {
                    self.exitStatus = process.terminationStatus
                    self.finishIfDrained()
                }
            }
            guard fcntl(self.input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
                self.fail(.writeFailed)
                self.finishWithoutLaunch()
                return
            }
            do {
                try self.process.run()
            } catch {
                self.fail(.launchFailed)
                self.finishWithoutLaunch()
                return
            }
            self.closeParentChildEnds()
            self.read(self.output.fileHandleForReading, stdout: true)
            self.read(self.errors.fileHandleForReading, stdout: false)
            self.enqueue(ClientMessage(id: "hello", type: "hello"), timeout: 5)
        }
    }

    public func send(_ message: ClientMessage, timeout: TimeInterval = 20) {
        queue.async {
            guard self.started, !self.stopping, !self.failed else {
                self.emit(.failure(.notReady))
                return
            }
            self.enqueue(message, timeout: timeout)
            if self.state.isBusiness, self.state.closing {
                self.stopping = true
                self.cancelDeadlines()
                self.closeInput()
            }
        }
    }

    @discardableResult
    public func translate(text: String, appLanguage: String, origin: String = "text",
                          useCache: Bool = true, recordHistory: Bool = true,
                          id: String = UUID().uuidString, timeout: TimeInterval = 110) -> String {
        send(ClientMessage(id: id, type: "request", payload: [
            "operation": .string("translate"), "text": .string(text),
            "app_language": .string(appLanguage), "origin": .string(origin),
            "use_cache": .bool(useCache), "record_history": .bool(recordHistory)
        ]), timeout: timeout)
        return id
    }

    @discardableResult
    public func translateImage(imagePath: String, imageBytes: Int, imageSHA256: String, appLanguage: String,
                               recordHistory: Bool = true, id: String = UUID().uuidString,
                               timeout: TimeInterval = 110) -> String {
        send(ClientMessage(id: id, type: "request", payload: [
            "operation": .string(ImageTranslationDocument.operation), "image_path": .string(imagePath),
            "image_bytes": .integer(Int64(imageBytes)), "image_sha256": .string(imageSHA256),
            "app_language": .string(appLanguage), "record_history": .bool(recordHistory)
        ]), timeout: timeout)
        return id
    }

    @discardableResult
    public func resultAction(_ action: ResultAction, text: String, appLanguage: String,
                             targetLanguage: String? = nil, id: String = UUID().uuidString,
                             timeout: TimeInterval = 110) -> String {
        send(action.request(text: text, appLanguage: appLanguage, targetLanguage: targetLanguage, id: id),
             timeout: timeout)
        return id
    }

    @discardableResult
    public func modelCatalog(id: String = UUID().uuidString, timeout: TimeInterval = 40) -> String {
        send(ClientMessage(id: id, type: "request", payload: ["operation": .string(ModelCatalogDocument.operation)]),
             timeout: timeout)
        return id
    }

    @discardableResult
    public func dictionary(_ request: DictionaryRequest, id: String = UUID().uuidString,
                           timeout: TimeInterval = 30) -> String {
        send(ClientMessage(id: id, type: "request", payload: request.payload), timeout: timeout)
        return id
    }

    @discardableResult
    public func loadConfiguration(id: String = UUID().uuidString, timeout: TimeInterval = 20) -> String {
        send(ClientMessage(id: id, type: "request", payload: ["operation": .string("config_load")]),
             timeout: timeout)
        return id
    }

    @discardableResult
    public func saveConfiguration(_ config: [String: JSONValue], id: String = UUID().uuidString,
                                  timeout: TimeInterval = 20) -> String {
        send(ClientMessage(id: id, type: "request", payload: [
            "operation": .string("config_save"), "config": .object(config)
        ]), timeout: timeout)
        return id
    }

    @discardableResult
    public func loadHistory(pageSize: Int = 100, cursor: JSONValue = .null,
                            query: String = "", kind: String = "all",
                            id: String = UUID().uuidString, timeout: TimeInterval = 20) -> String {
        var payload: [String: JSONValue] = [
            "operation": .string("history_load"), "page_size": .integer(Int64(pageSize)), "cursor": cursor
        ]
        if !query.isEmpty { payload["query"] = .string(query) }
        if kind != "all" { payload["kind"] = .string(kind) }
        send(ClientMessage(id: id, type: "request", payload: payload), timeout: timeout)
        return id
    }

    @discardableResult
    public func addHistory(input: String, output: String, isDict: Bool, isCode: Bool,
                           kind: String, sig: String, limit: Int,
                           id: String = UUID().uuidString, timeout: TimeInterval = 20) -> String {
        send(ClientMessage(id: id, type: "request", payload: [
            "operation": .string("history_add"), "input": .string(input), "output": .string(output),
            "is_dict": .bool(isDict), "is_code": .bool(isCode), "kind": .string(kind),
            "sig": .string(sig), "limit": .integer(Int64(limit))
        ]), timeout: timeout)
        return id
    }

    @discardableResult
    public func clearHistory(id: String = UUID().uuidString, timeout: TimeInterval = 20) -> String {
        send(ClientMessage(id: id, type: "request", payload: ["operation": .string("history_clear")]),
             timeout: timeout)
        return id
    }

    public func stop() {
        queue.async {
            guard !self.stopping else { return }
            self.stopping = true
            let business = self.state.isBusiness
            if business { self.cancelDeadlines() }
            if self.started, self.process.isRunning, self.state.ready, !self.failed,
               !business || self.state.registeredCount < 4096 {
                self.enqueue(ClientMessage(id: UUID().uuidString, type: "shutdown"),
                             timeout: business ? nil : 3)
            }
            self.closeInput()
            if !business { self.scheduleTermination() }
            if !self.started { self.emit(.stopped) }
        }
    }

    public func forceStop() {
        queue.async {
            guard !self.didFinish else { return }
            if let unknown = self.state.pendingOutcomeUnknown { self.fail(unknown) }
            self.stopping = true
            self.cancelDeadlines()
            self.closeInput()
            self.terminateOwnedProcess()
            if !self.started { self.emit(.stopped) }
        }
    }

    private func emit(_ value: HelperNotice) {
        // No public method waits on queue; synchronous delivery bounds queued UI events.
        DispatchQueue.main.sync { self.notice(value) }
    }

    private func enqueue(_ message: ClientMessage, timeout: TimeInterval?) {
        do {
            let drainingBusiness = state.isBusiness && message.type == "shutdown"
            guard pendingWrites < 8 || drainingBusiness else { throw ProbeError.writeFailed }
            let bytes = try message.encoded()
            try state.register(message)
            pendingWrites += 1
            if let timeout = timeout {
                let deadline = DispatchWorkItem { [weak self] in
                    self?.fail(message.type == "hello" ? .handshakeTimeout : .requestTimeout)
                }
                deadlines[message.id] = deadline
                queue.asyncAfter(deadline: .now() + max(0.1, timeout), execute: deadline)
            }
            writer.async {
                do {
                    try self.input.fileHandleForWriting.write(contentsOf: bytes)
                    self.queue.async { self.pendingWrites -= 1 }
                } catch {
                    self.queue.async {
                        self.pendingWrites -= 1
                        self.fail(.writeFailed)
                    }
                }
            }
        } catch let error as ProbeError {
            fail(error)
        } catch {
            fail(.invalidJSON)
        }
    }

    private func read(_ handle: FileHandle, stdout: Bool) {
        DispatchQueue.global(qos: .utility).async {
            defer {
                do { try handle.close() }
                catch { self.queue.async { self.fail(.readFailed) } }
            }
            var bytes = [UInt8](repeating: 0, count: 4096)
            while true {
                let count = bytes.withUnsafeMutableBytes {
                    Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count)
                }
                if count < 0 {
                    if errno == EINTR { continue }
                    self.queue.sync {
                        self.fail(.readFailed)
                        self.ended(stdout: stdout)
                    }
                    return
                }
                if count == 0 {
                    self.queue.sync { self.ended(stdout: stdout) }
                    return
                }
                // A single POSIX read returns available pipe bytes without waiting to fill 4 KiB.
                let chunk = Data(bytes.prefix(count))
                self.queue.sync { self.received(chunk, stdout: stdout) }
            }
        }
    }

    private func received(_ data: Data, stdout: Bool) {
        guard !failed else { return }
        if !stdout {
            stderrBytes += data.count
            // stderr is intentionally never displayed or logged (it may contain source text).
            if stderrBytes > 65_536 {
                fail(.stderrLimit)
                return
            }
            do { _ = try stderrFramer.append(data) }
            catch { fail(.stderrLimit) }
            return
        }
        do {
            for frame in try framer.append(data) {
                let event = try state.receive(frame)
                if event.isTerminal { deadlines.removeValue(forKey: event.id)?.cancel() }
                if !state.ready, event.isTerminal {
                    if state.isBusiness, event.type == "failed" {
                        emit(.event(event))
                        fail(ProbeError(rawValue: event.safeFailureCode) ?? .helperProtocolError)
                        return
                    }
                    throw ProbeError.helperProtocolError
                }
                emit(.event(event))
            }
        } catch let error as ProbeError {
            fail(error)
        } catch {
            fail(.invalidJSON)
        }
    }

    private func ended(stdout: Bool) {
        if stdout {
            stdoutEnded = true
            if !failed {
                do { try framer.finish() }
                catch let error as ProbeError { fail(error) }
                catch { fail(.incompleteFrame) }
                let pending = state.isBusiness ? state.hasPendingResponses : !deadlines.isEmpty
                if !stopping || pending { fail(.helperEOF) }
            }
        } else {
            stderrEnded = true
            if !failed {
                do { try stderrFramer.finish() }
                catch { fail(.readFailed) }
            }
        }
        finishIfDrained()
    }

    private func fail(_ error: ProbeError) {
        guard !failed, !didFinish else { return }
        failed = true
        emit(.failure(state.pendingOutcomeUnknown ?? error))
        cancelDeadlines()
        stopping = true
        closeInput()
        scheduleTermination()
    }

    private func cancelDeadlines() {
        deadlines.values.forEach { $0.cancel() }
        deadlines.removeAll()
    }

    private func closeInput() {
        guard !inputClosed else { return }
        inputClosed = true
        writer.async {
            do { try self.input.fileHandleForWriting.close() }
            catch {
                self.queue.async {
                    if self.state.isBusiness { self.fail(.writeFailed) }
                    else { self.emit(.failure(.writeFailed)) }
                }
            }
        }
    }

    private func closeParentChildEnds() {
        do {
            try input.fileHandleForReading.close()
            try output.fileHandleForWriting.close()
            try errors.fileHandleForWriting.close()
        } catch {
            fail(.readFailed)
        }
    }

    static func terminationGrace(for mode: ProtocolState.Mode) -> (eof: TimeInterval, term: TimeInterval) {
        // Business helpers must drain owned CLI groups and dictionary validation/commit
        // before escalation, even when no model connection was opened.
        mode == .diagnostic ? (3, 1) : (30, 30)
    }

    private func scheduleTermination() {
        guard !terminationScheduled else { return }
        terminationScheduled = true
        queue.asyncAfter(deadline: .now() + Self.terminationGrace(for: state.mode).eof) {
            guard self.process.isRunning else { return }
            if self.state.mode == .diagnostic {
                self.emit(.failure(.requestTimeout))
            } else if !self.failed {
                self.fail(.requestTimeout)
            }
            self.terminateOwnedProcess()
        }
    }

    private func terminateOwnedProcess() {
        guard process.isRunning, !terminationRequested else { return }
        terminationRequested = true
        process.terminate()
        queue.asyncAfter(deadline: .now() + Self.terminationGrace(for: state.mode).term) {
            guard self.process.isRunning else { return }
            // Last resort for only the still-owned helper PID. This cannot prove
            // descendant cleanup or rollback; pending work remains OutcomeUnknown.
            if Darwin.kill(self.process.processIdentifier, SIGKILL) != 0, errno != ESRCH {
                self.emit(.failure(self.state.pendingOutcomeUnknown ?? .helperExited))
            }
        }
    }

    private func finishIfDrained() {
        guard !didFinish, stdoutEnded, stderrEnded, let status = exitStatus else { return }
        if !failed, let unknown = state.pendingOutcomeUnknown { fail(unknown) }
        if status != 0, !failed { emit(.failure(.helperExited)) }
        didFinish = true
        cancelDeadlines()
        emit(.stopped)
    }

    private func finishWithoutLaunch() {
        do {
            try input.fileHandleForReading.close()
            try output.fileHandleForReading.close()
            try output.fileHandleForWriting.close()
            try errors.fileHandleForReading.close()
            try errors.fileHandleForWriting.close()
        } catch {
            emit(.failure(.readFailed))
        }
        didFinish = true
        emit(.stopped)
    }
}
