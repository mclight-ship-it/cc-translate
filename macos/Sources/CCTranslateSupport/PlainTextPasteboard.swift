import AppKit

// Only local lease state is queue-confined. Blocking reads run on a separate process's
// physical main thread; revision checks and eager writes stay on the host MainActor.
final class SystemPlainTextPasteClipboard: PlainTextPasteClipboard, @unchecked Sendable {
    private static let queue = DispatchQueue(label: "CCTranslate.plain-text-paste.clipboard", qos: .userInitiated)
    private let name: NSPasteboard.Name
    private let identity = UUID()
    private let trace: (@Sendable (String) -> Void)?
    private let readerExecutable: URL?
    private let workerTimeout: TimeInterval
    private var nextToken = 0
    private var readable: Lease?
    private var written: Lease?

    private struct Lease: Sendable {
        let token: Int
        let changeCount: Int
    }

    init(name: NSPasteboard.Name = .general, readerExecutable: URL? = nil,
         workerTimeout: TimeInterval = 35, trace: (@Sendable (String) -> Void)? = nil) {
        self.name = name
        self.readerExecutable = readerExecutable
        self.workerTimeout = workerTimeout
        self.trace = trace
    }

    func read(cancellation: PlainTextPasteCancellation) async -> PlainTextPasteRead {
        let token = await onQueue {
            self.readable = nil
            self.written = nil
            self.nextToken += 1
            return self.nextToken
        }
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard let executable = readerExecutable ?? Bundle.main.executableURL,
              readerExecutable != nil || executable.lastPathComponent == "CCTranslateMac" else {
            trace?("clipboard reader executable unavailable")
            return .failure(.unavailableData)
        }
        let initialCount = await currentChangeCount()
        let run = ClipboardReadRun(request: UUID(), revision: initialCount, trace: trace ?? { _ in })
        let read = await run.read(executable: executable, name: name.rawValue,
                                  cancellation: cancellation, timeout: workerTimeout)
        let result: PlainTextPasteRead
        switch read {
        case .success(let text):
            result = .text(PlainTextPasteSnapshot(changeCount: token, text: text, sourceIdentity: identity))
        case .failure(let failure): result = .failure(failure.reason)
        }
        let count = await currentChangeCount()
        return await onQueue {
            self.trace?("read finish token \(token)/\(self.nextToken), change count \(initialCount)/\(count), cancelled \(cancellation.isCancelled)")
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            guard self.nextToken == token, count == initialCount else { return .failure(.clipboardChanged) }
            if case .text = result { self.readable = Lease(token: token, changeCount: count) }
            return result
        }
    }

    func replace(_ snapshot: PlainTextPasteSnapshot, cancellation: PlainTextPasteCancellation) async -> PlainTextPasteWrite {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let prepared: (Lease, Data)? = await onQueue {
            guard snapshot.sourceIdentity == self.identity, let lease = self.readable,
                  lease.token == snapshot.changeCount else { return nil }
            self.readable = nil
            self.written = nil
            return (lease, Data(snapshot.text.utf8))
        }
        guard let (lease, data) = prepared else { return .failure(.clipboardChanged) }
        let result = await write(data, replacing: lease.changeCount, cancellation: cancellation)
        return await onQueue {
            switch result {
            case .failure: return result
            case .written(let changeCount):
                guard self.nextToken == lease.token else { return .failure(.clipboardChanged) }
                self.nextToken += 1
                self.written = Lease(token: self.nextToken, changeCount: changeCount)
                return .written(self.nextToken)
            }
        }
    }

    func stillOwns(_ token: Int) async -> Bool {
        let lease = await onQueue { self.written }
        guard let lease, lease.token == token else { return false }
        let count = await currentChangeCount()
        return await onQueue {
            guard self.written?.token == token else { return false }
            guard count == lease.changeCount else {
                self.written = nil
                return false
            }
            return true
        }
    }

    @MainActor
    private func currentChangeCount() -> Int { NSPasteboard(name: name).changeCount }

    @MainActor
    private func write(_ data: Data, replacing expected: Int,
                       cancellation: PlainTextPasteCancellation) -> PlainTextPasteWrite {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let board = NSPasteboard(name: name)
        guard board.changeCount == expected else { return .failure(.clipboardChanged) }
        cancellation.record(clipboard: .mayHaveChanged)
        // Check/clear is not an atomic CAS against other applications. Never restore or retry.
        let count = board.clearContents()
        cancellation.record(clipboard: .cleared)
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard board.changeCount == count else { return .failure(.clipboardChanged) }
        guard board.setData(data, forType: .string) else { return .failure(.writeFailed) }
        cancellation.record(clipboard: .plainTextWritten)
        guard board.changeCount == count else { return .failure(.clipboardChanged) }
        return .written(count)
    }

    private func onQueue<Value: Sendable>(_ work: @escaping @Sendable () -> Value) async -> Value {
        await withCheckedContinuation { continuation in
            Self.queue.async {
                let value = autoreleasepool(invoking: work)
                continuation.resume(returning: value)
            }
        }
    }
}
