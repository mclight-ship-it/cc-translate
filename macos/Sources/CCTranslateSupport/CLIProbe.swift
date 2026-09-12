import AppKit
import Darwin

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
// P0 supervises the direct child only; wrappers leaving descendants are unsupported.
// Owned CLI process-group supervision is a separate P1 requirement.
public final class CLIVersionRun {
    private let queue = DispatchQueue(label: "dev.cc-translate.cli.version")
    private let process = Process()
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
            self.process.executableURL = executable
            self.process.arguments = ["--version"]
            self.process.environment = [
                "PATH": CLILocator.searchPath, "HOME": NSHomeDirectory(), "LANG": "en_US.UTF-8"
            ]
            self.process.currentDirectoryURL = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            self.process.standardInput = FileHandle.nullDevice
            self.process.standardOutput = self.output.fileHandleForWriting
            self.process.standardError = self.errors.fileHandleForWriting
            self.process.terminationHandler = { [weak self] process in
                guard let self = self else { return }
                self.queue.async {
                    self.exitCode = process.terminationStatus
                    self.finish()
                }
            }
            do {
                try self.process.run()
            } catch {
                self.failure = .cliFailed
                self.readEnds = 2
                self.exitCode = -1
                self.closeAfterLaunchFailure()
                self.finish()
                return
            }
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
        if process.isRunning { process.terminate() }
        queue.asyncAfter(deadline: .now() + 1) {
            if self.process.isRunning {
                if Darwin.kill(self.process.processIdentifier, SIGKILL) != 0, errno != ESRCH {
                    self.failure = .cliFailed
                }
            }
            // Also release pipes inherited by an unsupported wrapper's background children.
            // Never search for or kill unrelated user CLI processes.
            self.readers.forEach { $0.stop() }
        }
        finish()
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
