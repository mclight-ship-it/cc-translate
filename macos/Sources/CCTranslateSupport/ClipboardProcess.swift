import Foundation
import Darwin
import CCProcessSupport

struct ClipboardProcessResult: Sendable {
    let pid: pid_t?
    let exitCode: Int32
    let failure: String?
}

// Unlike Foundation.Process, this owner does not reap behind a pending signal.
// Reuse the CLI's WNOWAIT/group-signal/reap primitives to keep the PID pinned.
final class ClipboardProcess: @unchecked Sendable {
    private let queue = DispatchQueue(label: "dev.cc-translate.clipboard.process")
    private let stderrQueue = DispatchQueue(label: "dev.cc-translate.clipboard.stderr")
    private let inputQueue = DispatchQueue(label: "dev.cc-translate.clipboard.stdin")
    private let input = Pipe()
    private let output = Pipe()
    private let errors = Pipe()
    private let receive: (Data) throws -> Void
    private let completion: (ClipboardProcessResult) -> Void
    private let trace: @Sendable (String) -> Void
    private let cancelled: @Sendable () -> Bool
    private let timeout: TimeInterval
    private var pid: pid_t?
    private var originalPID: pid_t?
    private var exitCode: Int32?
    private var failure: String?
    private var readEnds = 0
    private var pendingStderr = 0
    private var readers: [ClipboardPipeReader] = []
    private var pendingWrites = 0
    private var inputClosed = false
    private var inputDrained = false
    private var started = false
    private var finished = false
    private var stopTime: DispatchTime?
    private var killSent = false
    private var reportedPending = false
    private var deadline = DispatchTime.distantFuture

    init(timeout: TimeInterval = 35, cancelled: @escaping @Sendable () -> Bool = { false },
         trace: @escaping @Sendable (String) -> Void = { _ in },
         receive: @escaping (Data) throws -> Void,
         completion: @escaping (ClipboardProcessResult) -> Void) {
        self.timeout = timeout
        self.cancelled = cancelled
        self.trace = trace
        self.receive = receive
        self.completion = completion
    }

    func start(executable: URL, arguments: [String], input initialInput: Data = Data(),
               keepInputOpen: Bool = false) {
        queue.async {
            guard !self.started else { self.fail("duplicate_start"); return }
            self.started = true
            do {
                guard !self.cancelled() else { throw ClipboardProcessError.cancelled }
                guard executable.isFileURL, executable.path.hasPrefix("/"),
                      !executable.path.contains("\0"),
                      arguments.allSatisfy({ !$0.contains("\0") }) else {
                    throw ClipboardProcessError.launch
                }
                let fd = self.input.fileHandleForWriting.fileDescriptor
                guard fcntl(fd, F_SETNOSIGPIPE, 1) != -1 else { throw ClipboardProcessError.launch }
                let child = try self.spawn(executable: executable.path, arguments: arguments)
                self.pid = child
                self.originalPID = child
                self.trace("clipboard process started pid \(child)")
            } catch {
                self.failure = self.cancelled() ? "cancelled" : "launch_failed"
                self.closeLaunchHandles()
                self.finished = true
                self.completion(ClipboardProcessResult(pid: nil, exitCode: -1, failure: self.failure))
                return
            }
            self.close(self.input.fileHandleForReading)
            self.close(self.output.fileHandleForWriting)
            self.close(self.errors.fileHandleForWriting)
            self.read(self.output.fileHandleForReading, stdout: true)
            self.read(self.errors.fileHandleForReading, stdout: false)
            if !initialInput.isEmpty { self.writeInput(initialInput) }
            if !keepInputOpen { self.closeInput() }
            self.deadline = .now() + self.timeout
            self.poll()
        }
    }

    func send(_ data: Data) {
        queue.async {
            guard self.started, !self.finished, self.stopTime == nil, !self.inputClosed else {
                self.fail("stdin_closed")
                return
            }
            self.writeInput(data)
        }
    }

    func stop() { queue.async { self.fail("cancelled") } }

    private func spawn(executable: String, arguments: [String]) throws -> pid_t {
        var attributes: posix_spawnattr_t?
        var actions: posix_spawn_file_actions_t?
        guard posix_spawnattr_init(&attributes) == 0 else { throw ClipboardProcessError.launch }
        defer { posix_spawnattr_destroy(&attributes) }
        guard posix_spawn_file_actions_init(&actions) == 0 else { throw ClipboardProcessError.launch }
        defer { posix_spawn_file_actions_destroy(&actions) }
        var empty = sigset_t()
        var defaults = sigset_t()
        sigemptyset(&empty)
        sigemptyset(&defaults)
        for signal in [SIGTERM, SIGINT, SIGPIPE] { sigaddset(&defaults, signal) }
        let flags = Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK |
                          POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_CLOEXEC_DEFAULT)
        guard posix_spawnattr_setpgroup(&attributes, 0) == 0,
              posix_spawnattr_setsigmask(&attributes, &empty) == 0,
              posix_spawnattr_setsigdefault(&attributes, &defaults) == 0,
              posix_spawnattr_setflags(&attributes, flags) == 0 else { throw ClipboardProcessError.launch }
        for (source, destination) in [
            (input.fileHandleForReading.fileDescriptor, STDIN_FILENO),
            (output.fileHandleForWriting.fileDescriptor, STDOUT_FILENO),
            (errors.fileHandleForWriting.fileDescriptor, STDERR_FILENO)
        ] {
            guard posix_spawn_file_actions_adddup2(&actions, source, destination) == 0 else {
                throw ClipboardProcessError.launch
            }
        }
        let strings = [executable] + arguments
        let argv = strings.map { strdup($0) }
        defer { argv.forEach { free($0) } }
        // In particular, an XCTest DYLD path must not replace an audited app's libraries.
        let environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("DYLD_") }
        let envp = environment.sorted(by: { $0.key < $1.key }).map { strdup("\($0.key)=\($0.value)") }
        defer { envp.forEach { free($0) } }
        guard argv.allSatisfy({ $0 != nil }), envp.allSatisfy({ $0 != nil }) else {
            throw ClipboardProcessError.launch
        }
        var terminatedArguments = argv + [nil]
        var terminatedEnvironment = envp + [nil]
        var child: pid_t = 0
        let status = terminatedArguments.withUnsafeMutableBufferPointer { argv in
            terminatedEnvironment.withUnsafeMutableBufferPointer { envp in
                posix_spawn(&child, executable, &actions, &attributes, argv.baseAddress!, envp.baseAddress!)
            }
        }
        guard status == 0, child > 1 else { throw ClipboardProcessError.launch }
        return child
    }

    private func read(_ handle: FileHandle, stdout: Bool) {
        let reader = ClipboardPipeReader(handle: handle, chunk: { [self] data in
            if stdout {
                guard failure == nil else { return }
                do { try receive(data) }
                catch { fail("invalid_worker_output") }
            } else {
                pendingStderr += 1
                stderrQueue.async {
                    var failed = false
                    do { try FileHandle.standardError.write(contentsOf: data) }
                    catch { failed = true }
                    let writeFailed = failed
                    self.queue.async {
                        if writeFailed { self.fail("stderr_forward_failed") }
                        self.pendingStderr -= 1
                        self.finish()
                    }
                }
            }
        }, ended: { [self] failed in
            if failed { fail("pipe_read_failed") }
            readEnds += 1
            finish()
        })
        readers.append(reader)
        do { try reader.start(queue: queue) }
        catch {
            fail("pipe_setup_failed")
            close(handle)
            readEnds += 1
        }
    }

    private func writeInput(_ data: Data) {
        pendingWrites += 1
        inputQueue.async {
            var failed = false
            do { try self.input.fileHandleForWriting.write(contentsOf: data) }
            catch { failed = true }
            let writeFailed = failed
            self.queue.async {
                if writeFailed { self.fail("stdin_write_failed") }
                self.pendingWrites -= 1
                self.finish()
            }
        }
    }

    private func closeInput() {
        guard !inputClosed else { return }
        inputClosed = true
        inputQueue.async {
            var failed = false
            do { try self.input.fileHandleForWriting.close() }
            catch { failed = true }
            let closeFailed = failed
            self.queue.async {
                if closeFailed { self.fail("stdin_close_failed") }
                self.inputDrained = true
                self.finish()
            }
        }
    }

    private func fail(_ code: String) {
        guard !finished else { return }
        if failure == nil { failure = code; trace("clipboard process failure: \(code)") }
        beginStop()
    }

    private func beginStop() {
        guard let pid, stopTime == nil else { return }
        stopTime = .now()
        closeInput()
        if cc_cli_signal_group(pid, SIGTERM) != 0 {
            failure = "signal_failed"
            trace("clipboard process termination signal failed")
        }
    }

    private func poll() {
        guard let pid, !finished else { return }
        var exited: Int32 = 0
        guard cc_cli_has_exited(pid, &exited) == 0 else {
            // No further signals/reap are safe if someone else consumed our wait status.
            // Do not report a drained operation or release its lease.
            trace("clipboard process ownership lost; cleanup unconfirmed")
            return
        }
        if cancelled() { fail("cancelled") }
        // The budget applies to the live reader, not to the mandatory group
        // cleanup/reap after it has already exited with a complete response.
        if exited == 0, stopTime == nil, DispatchTime.now() >= deadline { fail("timed_out") }
        if exited != 0 { beginStop() }
        if let began = stopTime, DispatchTime.now() >= began + .milliseconds(200) {
            if !killSent {
                guard cc_cli_signal_group(pid, SIGKILL) == 0 else {
                    trace("clipboard process cleanup signal failed; ownership retained")
                    queue.asyncAfter(deadline: .now() + .milliseconds(20)) { self.poll() }
                    return
                }
                killSent = true
            }
            if exited != 0 {
                var code: Int32 = -1
                guard cc_cli_reap(pid, &code) == 0 else {
                    trace("clipboard process reap failed; cleanup unconfirmed")
                    return
                }
                self.pid = nil
                exitCode = code
                trace("clipboard process reaped pid \(pid)")
                if readEnds != 2 { trace("clipboard process awaiting pipe EOF; lease retained") }
                finish()
                return
            }
            if !reportedPending, DispatchTime.now() >= began + .seconds(2) {
                reportedPending = true
                trace("clipboard process cleanup pending; lease retained")
            }
        }
        queue.asyncAfter(deadline: .now() + .milliseconds(20)) { self.poll() }
    }

    private func finish() {
        guard !finished, let exitCode, readEnds == 2, pendingStderr == 0,
              inputDrained, pendingWrites == 0 else { return }
        finished = true
        readers.removeAll()
        closeInput()
        trace("clipboard process pipes drained")
        if exitCode != 0 { trace("clipboard process exit status \(exitCode)") }
        completion(ClipboardProcessResult(pid: originalPID, exitCode: exitCode, failure: failure))
    }

    private func close(_ handle: FileHandle) {
        do { try handle.close() }
        catch { failure = failure ?? "pipe_close_failed"; trace("clipboard pipe close failed") }
    }

    private func closeLaunchHandles() {
        for handle in [input.fileHandleForReading, input.fileHandleForWriting,
                       output.fileHandleForReading, output.fileHandleForWriting,
                       errors.fileHandleForReading, errors.fileHandleForWriting] { close(handle) }
    }
}

private enum ClipboardProcessError: Error { case launch, cancelled }

// This follows CLIPipeReader's nonblocking-source pattern without its CLI output limit.
private final class ClipboardPipeReader {
    private let handle: FileHandle
    private let chunk: (Data) -> Void
    private let ended: (Bool) -> Void
    private var source: DispatchSourceRead?
    private var failed = false

    init(handle: FileHandle, chunk: @escaping (Data) -> Void, ended: @escaping (Bool) -> Void) {
        self.handle = handle
        self.chunk = chunk
        self.ended = ended
    }

    func start(queue: DispatchQueue) throws {
        let fd = handle.fileDescriptor
        let flags = fcntl(fd, F_GETFL)
        guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) != -1 else {
            throw ClipboardProcessError.launch
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        self.source = source
        source.setEventHandler { [weak self] in self?.read() }
        source.setCancelHandler { [self] in
            do { try handle.close() }
            catch { failed = true }
            ended(failed)
        }
        source.resume()
    }

    private func read() {
        var bytes = [UInt8](repeating: 0, count: 32_768)
        let count = bytes.withUnsafeMutableBytes { Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count) }
        if count > 0 { chunk(Data(bytes.prefix(count))) }
        else if count == 0 { stop() }
        else if errno != EINTR, errno != EAGAIN { failed = true; stop() }
    }

    private func stop() {
        source?.cancel()
        source = nil
    }
}
