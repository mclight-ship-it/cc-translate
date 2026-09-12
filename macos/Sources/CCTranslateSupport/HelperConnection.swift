import Foundation
import Darwin

public struct BundleRuntime {
    public let executable: URL
    public let launcher: URL

    public init(bundle: Bundle = .main) throws {
        guard let resources = bundle.resourceURL else { throw ProbeError.bundleMissing }
        try self.init(appURL: bundle.bundleURL, resourcesURL: resources)
    }

    public init(appURL: URL, resourcesURL: URL? = nil) throws {
        let contents = appURL.appendingPathComponent("Contents", isDirectory: true)
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
    private var deadlines: [String: DispatchWorkItem] = [:]
    private let notice: (HelperNotice) -> Void

    public init(notice: @escaping (HelperNotice) -> Void) { self.notice = notice }

    public func start(runtime: BundleRuntime) {
        queue.async {
            guard !self.started, !self.stopping else { return }
            self.started = true
            self.process.executableURL = runtime.executable
            self.process.arguments = ["-I", "-B", runtime.launcher.path]
            self.process.currentDirectoryURL = runtime.launcher.deletingLastPathComponent()
            self.process.environment = [
                "PATH": "/usr/bin:/bin",
                "LANG": "en_US.UTF-8",
                "HOME": NSHomeDirectory()
            ]
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
        }
    }

    public func stop() {
        queue.async {
            guard !self.stopping else { return }
            self.stopping = true
            if self.started, self.process.isRunning, self.state.ready, !self.failed {
                self.enqueue(ClientMessage(id: UUID().uuidString, type: "shutdown"), timeout: 3)
            }
            self.closeInput()
            self.scheduleTermination()
            if !self.started { self.emit(.stopped) }
        }
    }

    private func emit(_ value: HelperNotice) {
        // No public method waits on queue; synchronous delivery bounds queued UI events.
        DispatchQueue.main.sync { self.notice(value) }
    }

    private func enqueue(_ message: ClientMessage, timeout: TimeInterval) {
        do {
            guard pendingWrites < 8 else { throw ProbeError.writeFailed }
            let bytes = try message.encoded()
            try state.register(message)
            pendingWrites += 1
            let deadline = DispatchWorkItem { [weak self] in
                self?.fail(message.type == "hello" ? .handshakeTimeout : .requestTimeout)
            }
            deadlines[message.id] = deadline
            queue.asyncAfter(deadline: .now() + max(0.1, timeout), execute: deadline)
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
                if !stopping || !deadlines.isEmpty { fail(.helperEOF) }
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
        emit(.failure(error))
        deadlines.values.forEach { $0.cancel() }
        deadlines.removeAll()
        stopping = true
        closeInput()
        scheduleTermination()
    }

    private func closeInput() {
        guard !inputClosed else { return }
        inputClosed = true
        writer.async {
            do { try self.input.fileHandleForWriting.close() }
            catch { self.queue.async { self.emit(.failure(.writeFailed)) } }
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

    private func scheduleTermination() {
        guard !terminationScheduled else { return }
        terminationScheduled = true
        // Leave time for the core's two-second bounded worker cleanup and pipe drain.
        queue.asyncAfter(deadline: .now() + 3) {
            guard self.process.isRunning else { return }
            self.emit(.failure(.requestTimeout))
            self.process.terminate()
            self.queue.asyncAfter(deadline: .now() + 1) {
                guard self.process.isRunning else { return }
                // Only the still-owned helper PID; P0 helper never creates CLI children.
                if Darwin.kill(self.process.processIdentifier, SIGKILL) != 0, errno != ESRCH {
                    self.emit(.failure(.helperExited))
                }
            }
        }
    }

    private func finishIfDrained() {
        guard !didFinish, stdoutEnded, stderrEnded, let status = exitStatus else { return }
        didFinish = true
        deadlines.values.forEach { $0.cancel() }
        deadlines.removeAll()
        if status != 0, !failed { emit(.failure(.helperExited)) }
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
