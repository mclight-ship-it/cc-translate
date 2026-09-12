import AppKit
import Darwin
import CCProcessSupport

public struct CLICandidate: Identifiable {
    public var id: String { url.path }
    public let url: URL
    public let executable: Bool
}

public enum CLILocator {
    public static var searchPath: String {
        [NSHomeDirectory() + "/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
            .joined(separator: ":")
    }

    public static func candidates(name: String, userURL: URL? = nil) -> [CLICandidate] {
        guard ["codex", "claude"].contains(name) else { return [] }
        var urls = searchPath.split(separator: ":").map {
            URL(fileURLWithPath: String($0), isDirectory: true).appendingPathComponent(name)
        }
        if name == "codex" {
            urls.append(URL(fileURLWithPath: "/Applications/Codex.app/Contents/Resources/codex"))
        }
        if let userURL = userURL { urls.insert(userURL, at: 0) }
        var seen = Set<String>()
        return urls.filter { seen.insert($0.path).inserted }.map {
            var isDirectory: ObjCBool = false
            let exists = FileManager.default.fileExists(atPath: $0.path, isDirectory: &isDirectory)
            return CLICandidate(url: $0, executable: exists && !isDirectory.boolValue &&
                                FileManager.default.isExecutableFile(atPath: $0.path))
        }
    }

    @MainActor
    public static func chooseExecutable() -> URL? {
        let panel = NSOpenPanel()
        panel.title = "Choose CLI executable (will not execute yet)"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        return panel.runModal() == .OK ? panel.url : nil
    }
}

private final class CLIPipeReader {
    private let handle: FileHandle
    private let chunk: (Data) -> Void
    private let ended: (ProbeError?) -> Void
    private var source: DispatchSourceRead?
    private var failure: ProbeError?

    init(handle: FileHandle, chunk: @escaping (Data) -> Void, ended: @escaping (ProbeError?) -> Void) {
        self.handle = handle
        self.chunk = chunk
        self.ended = ended
    }

    func start(queue: DispatchQueue) throws {
        let descriptor = handle.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) != -1 else {
            throw ProbeError.cliFailed
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: queue)
        self.source = source
        source.setEventHandler { [weak self] in self?.readOnce() }
        source.setCancelHandler { [self] in
            do { try handle.close() }
            catch { failure = .cliFailed }
            ended(failure)
        }
        source.resume()
    }

    func stop() {
        source?.cancel()
        source = nil
    }

    private func readOnce() {
        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = bytes.withUnsafeMutableBytes {
            Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count)
        }
        if count > 0 {
            chunk(Data(bytes.prefix(count)))
        } else if count == 0 {
            stop()
        } else if errno != EINTR, errno != EAGAIN {
            failure = .cliFailed
            stop()
        }
    }
}

// Explicit --version only. No shell, login check, model call, or credential inspection.
// Owns an atomically created process group, not any pre-existing user CLI process.
public final class CLIVersionRun {
    private let queue = DispatchQueue(label: "dev.cc-translate.cli.version")
    private var processID: pid_t?
    private var terminationBegan: DispatchTime?
    private var killSent = false
    private let output = Pipe()
    private let errors = Pipe()
    private let completion: (Result<Void, ProbeError>) -> Void
    private var stdoutBytes = 0
    private var totalBytes = 0
    private var readEnds = 0
    private var exitCode: Int32?
    private var failure: ProbeError?
    private var finished = false
    private var started = false
    private var timeout: DispatchWorkItem?
    private var readers: [CLIPipeReader] = []

    public init(completion: @escaping (Result<Void, ProbeError>) -> Void) {
        self.completion = completion
    }

    public func start(executable: URL) {
        queue.async {
            guard !self.started, !self.finished else { return }
            self.started = true
            var pid: pid_t = 0
            let validPath = executable.isFileURL && !executable.path.utf8.contains(0)
            let error = validPath ? cc_spawn_cli_version(
                executable.path, NSHomeDirectory(), CLILocator.searchPath,
                self.output.fileHandleForWriting.fileDescriptor,
                self.errors.fileHandleForWriting.fileDescriptor, &pid
            ) : EINVAL
            guard error == 0 else {
                self.failure = .cliFailed
                self.readEnds = 2
                self.exitCode = -1
                self.closeAfterLaunchFailure()
                self.finish()
                return
            }
            self.processID = pid
            do {
                try self.output.fileHandleForWriting.close()
                try self.errors.fileHandleForWriting.close()
            } catch {
                self.abort(.cliFailed)
            }
            self.read(self.output.fileHandleForReading, stdout: true)
            self.read(self.errors.fileHandleForReading, stdout: false)
            let timeout = DispatchWorkItem { [weak self] in self?.abort(.cliTimeout) }
            self.timeout = timeout
            self.queue.asyncAfter(deadline: .now() + 5, execute: timeout)
            self.pollProcess()
        }
    }

    public func cancel() {
        queue.async {
            guard !self.finished else { return }
            if !self.started {
                self.failure = .cliCancelled
                self.readEnds = 2
                self.exitCode = -1
                self.closeAfterLaunchFailure()
                self.finish()
                return
            }
            self.abort(.cliCancelled)
        }
    }

    private func read(_ handle: FileHandle, stdout: Bool) {
        let reader = CLIPipeReader(handle: handle, chunk: { [self] chunk in
            guard failure == nil else { return }
            totalBytes += chunk.count
            guard totalBytes <= 32_768 else {
                abort(.cliOutputLimit)
                return
            }
            if stdout { stdoutBytes += chunk.count }
        }, ended: { [self] error in
            if let error = error { abort(error) }
            readEnds += 1
            finish()
        })
        readers.append(reader)
        do {
            try reader.start(queue: queue)
        } catch {
            abort(.cliFailed)
            do { try handle.close() }
            catch { failure = .cliFailed }
            readEnds += 1
            finish()
        }
    }

    private func abort(_ error: ProbeError) {
        guard !finished, failure == nil else { return }
        failure = error
        beginTermination()
        finish()
    }

    private func signalGroup(_ signal: Int32) {
        guard let pid = processID else { return }
        if Darwin.kill(-pid, signal) != 0, errno != ESRCH { failure = .cliFailed }
    }

    private func beginTermination() {
        guard processID != nil, terminationBegan == nil else { return }
        terminationBegan = .now()
        signalGroup(SIGTERM)
    }

    private func pollProcess() {
        guard let pid = processID else { return }
        var exited: Int32 = 0
        guard cc_cli_has_exited(pid, &exited) == 0 else {
            // Lost child ownership: never signal or wait on a possibly recycled PID.
            processID = nil
            failure = .cliFailed
            exitCode = -1
            readers.forEach { $0.stop() }
            finish()
            return
        }
        if exited != 0 { beginTermination() }
        if let began = terminationBegan, DispatchTime.now() >= began + .milliseconds(200) {
            if !killSent {
                signalGroup(SIGKILL)
                killSent = true
                queue.asyncAfter(deadline: .now() + .milliseconds(200)) {
                    if self.readEnds != 2 {
                        // A descendant that deliberately left our group is unsupported.
                        self.failure = self.failure ?? .cliFailed
                        self.readers.forEach { $0.stop() }
                    }
                }
            }
            if exited != 0 {
                var code: Int32 = -1
                if cc_cli_reap(pid, &code) != 0 { failure = .cliFailed }
                processID = nil
                exitCode = code
                finish()
                return
            }
            if DispatchTime.now() >= began + .seconds(2), !finished {
                // Surface OS cleanup failure without dropping ownership of a live child.
                failure = .cliFailed
                exitCode = -1
                readers.forEach { $0.stop() }
                finish()
            }
        }
        queue.asyncAfter(deadline: .now() + .milliseconds(20)) { self.pollProcess() }
    }

    private func finish() {
        guard !finished, readEnds == 2, let code = exitCode else { return }
        finished = true
        timeout?.cancel()
        readers.removeAll()
        let result: Result<Void, ProbeError>
        if let failure = failure {
            result = .failure(failure)
        } else if code != 0 {
            result = .failure(.cliFailed)
        } else if stdoutBytes > 0 {
            result = .success(())
        } else {
            result = .failure(.cliFailed)
        }
        DispatchQueue.main.async { self.completion(result) }
    }

    private func closeAfterLaunchFailure() {
        do {
            try output.fileHandleForReading.close()
            try output.fileHandleForWriting.close()
            try errors.fileHandleForReading.close()
            try errors.fileHandleForWriting.close()
        } catch {
            failure = .cliFailed
        }
    }
}
