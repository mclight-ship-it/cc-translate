import AppKit
import ApplicationServices
import Carbon
import Combine

public enum PlainTextPasteAdmission: Equatable {
    case accepted, busy, disabled, shutDown
}

public enum PlainTextPasteReason: Equatable, Sendable {
    case eventsSubmitted
    case noText, unsupportedRepresentation, unavailableData, invalidRichText
    case clipboardChanged, clipboardTimedOut, writeFailed
    case targetUnavailable, targetChanged, accessibilityUnavailable, secureInput
    case keysStillPressed, keyReleaseTimedOut, eventCreationFailed, eventPostingFailed
    case cancelled, disabled, shutDown
}

/// Effects describe what this service did, not the clipboard's current owner or the destination's contents.
public struct PlainTextPasteOutcome: Equatable, Sendable {
    public enum ClipboardEffect: Equatable, Sendable {
        case unchanged, mayHaveChanged, cleared, plainTextWritten
    }
    public enum EventEffect: Equatable, Sendable {
        case notPosted, mayHavePosted, submittedUnconfirmed
    }
    public let reason: PlainTextPasteReason
    public let clipboard: ClipboardEffect
    public let events: EventEffect
}

public enum PlainTextPasteStatus: Equatable {
    case idle, reading, waitingForKeys, writing, verifying
    case finished(PlainTextPasteOutcome)
}

@MainActor
public final class PlainTextPasteService: ObservableObject {
    @Published public private(set) var isEnabled: Bool
    @Published public private(set) var isBusy = false
    @Published public private(set) var status: PlainTextPasteStatus = .idle

    private let clipboard: any PlainTextPasteClipboard
    private let input: any PlainTextPasteInput
    private let scheduler: any PlainTextPasteScheduler
    private let clipboardTimeout: TimeInterval
    private let keyReleaseTimeout: TimeInterval
    private var operation: Operation?
    private var worker: Task<Void, Never>?
    private var timer: (any PlainTextPasteScheduled)?
    private var workerBusy = false
    private var postingBusy = false
    private var permanentlyStopped = false

    private struct Operation: Sendable {
        let id: UUID
        let target: PlainTextPasteTarget?
        let keys: [CGKeyCode]
        let cancellation: PlainTextPasteCancellation
        var terminated: PlainTextPasteReason?
    }

    /// No clipboard, permission, input, registration or background activity occurs during construction.
    public convenience init(enabled: Bool = false) {
        self.init(enabled: enabled, clipboard: SystemPlainTextPasteClipboard(),
                  input: SystemPlainTextPasteInput(), scheduler: SystemPlainTextPasteScheduler())
    }

    init(enabled: Bool = false, clipboard: any PlainTextPasteClipboard, input: any PlainTextPasteInput,
         scheduler: any PlainTextPasteScheduler, clipboardTimeout: TimeInterval = 30,
         keyReleaseTimeout: TimeInterval = 3) {
        self.isEnabled = enabled
        self.clipboard = clipboard
        self.input = input
        self.scheduler = scheduler
        self.clipboardTimeout = clipboardTimeout
        self.keyReleaseTimeout = keyReleaseTimeout
    }

    deinit {
        operation?.cancellation.cancel()
        timer?.cancel()
        worker?.cancel()
    }

    public func setEnabled(_ enabled: Bool) {
        guard !permanentlyStopped else { return }
        isEnabled = enabled
        if !enabled { terminate(.disabled) }
    }

    /// App owns shortcut registration. Supply its trigger key codes as well as releasing physical modifiers.
    @discardableResult
    public func requestPaste(releasing keyCodes: [CGKeyCode] = []) -> PlainTextPasteAdmission {
        guard !permanentlyStopped else { return .shutDown }
        guard isEnabled else { return .disabled }
        guard !isBusy else { return .busy }
        // Claim the request before any adapter can reenter through an AppKit/AX call.
        let id = UUID()
        let cancellation = PlainTextPasteCancellation()
        operation = Operation(id: id, target: nil,
                              keys: Array(Set(keyCodes)), cancellation: cancellation)
        isBusy = true
        guard current(id) else {
            isBusy = operation != nil
            return .accepted
        }
        let context = input.captureTarget()
        guard current(id) else { return .accepted }
        switch context {
        case .failure(let reason): finish(reason)
        case .success(let target):
            operation = Operation(id: id, target: target, keys: Array(Set(keyCodes)), cancellation: cancellation)
            status = .reading
            let clipboard = self.clipboard
            perform(id: id, work: { await clipboard.read(cancellation: cancellation) }) { [weak self] result in
                guard let self else { return }
                switch result {
                case .failure(let reason): finish(reason)
                case .text(let snapshot):
                    waitForKeys(id: id, snapshot: snapshot, writtenCount: nil,
                                deadline: scheduler.now + keyReleaseTimeout)
                }
            }
        }
        return .accepted
    }

    public func cancel() { terminate(.cancelled) }

    /// Permanent stop. A new service is required to enable the feature again after shutdown.
    public func shutdown() {
        permanentlyStopped = true
        isEnabled = false
        terminate(.shutDown)
    }

    private func current(_ id: UUID) -> Bool {
        operation?.id == id && operation?.terminated == nil && isEnabled && !permanentlyStopped
    }

    private func perform<Value: Sendable>(
        id: UUID, work: @escaping @Sendable () async -> Value,
        completion: @escaping @MainActor (Value) -> Void
    ) {
        guard current(id) else { return }
        workerBusy = true
        timer = scheduler.schedule(after: clipboardTimeout) { [weak self] in
            guard let self, current(id) else { return }
            terminate(.clipboardTimedOut)
        }
        worker = Task { [weak self] in
            let result = await work()
            guard let self, operation?.id == id else { return }
            timer?.cancel()
            timer = nil
            worker = nil
            workerBusy = false
            if let reason = operation?.terminated {
                finish(reason)
            } else if current(id) {
                completion(result)
            }
        }
    }

    private func waitForKeys(id: UUID, snapshot: PlainTextPasteSnapshot,
                             writtenCount: Int?, deadline: TimeInterval) {
        guard current(id), let operation, let target = operation.target else { return }
        let problem = input.validate(target)
        guard current(id) else { return }
        if let problem { finish(problem); return }
        let released = input.keysReleased(operation.keys)
        guard current(id) else { return }
        guard released else {
            guard scheduler.now < deadline else { finish(.keyReleaseTimedOut); return }
            status = .waitingForKeys
            guard current(id) else { return }
            timer = scheduler.schedule(after: 0.02) { [weak self] in
                self?.waitForKeys(id: id, snapshot: snapshot, writtenCount: writtenCount, deadline: deadline)
            }
            return
        }
        timer?.cancel()
        timer = nil
        let clipboard = self.clipboard
        if let writtenCount {
            status = .verifying
            perform(id: id, work: { await clipboard.stillOwns(writtenCount) }) { [weak self] owned in
                guard let self else { return }
                guard owned else { finish(.clipboardChanged); return }
                let problem = input.validate(target)
                guard current(id) else { return }
                if let problem { finish(problem); return }
                let released = input.keysReleased(operation.keys)
                guard current(id) else { return }
                guard released else {
                    guard scheduler.now < deadline else { finish(.keyReleaseTimedOut); return }
                    waitForKeys(id: id, snapshot: snapshot, writtenCount: writtenCount, deadline: deadline)
                    return
                }
                // postToPid has no delivery acknowledgement. Never claim that a paste reached an editor.
                postingBusy = true
                operation.cancellation.record(events: .mayHavePosted)
                let posted = input.postPaste(to: target, releasing: operation.keys, cancellation: operation.cancellation)
                operation.cancellation.record(events: posted.events)
                postingBusy = false
                guard current(id) else {
                    if let reason = self.operation?.terminated { finish(reason) }
                    return
                }
                finish(posted.reason)
            }
        } else {
            status = .writing
            perform(id: id, work: {
                await clipboard.replace(snapshot, cancellation: operation.cancellation)
            }) { [weak self] result in
                guard let self else { return }
                switch result {
                case .failure(let reason): finish(reason)
                case .written(let count):
                    waitForKeys(id: id, snapshot: snapshot, writtenCount: count, deadline: deadline)
                }
            }
        }
    }

    private func terminate(_ reason: PlainTextPasteReason) {
        timer?.cancel()
        timer = nil
        guard var active = operation else {
            // Cancellation cannot retroactively undo a completed write or submitted event pair.
            if case .finished = status { return }
            status = .finished(PlainTextPasteOutcome(reason: reason, clipboard: .unchanged, events: .notPosted))
            return
        }
        active.terminated = reason
        operation = active
        active.cancellation.cancel()
        worker?.cancel()
        status = .finished(active.cancellation.outcome(reason))
        // A promised read or system write cannot be interrupted safely. Reject new requests until it returns.
        if !workerBusy, !postingBusy { finish(reason) }
    }

    private func finish(_ reason: PlainTextPasteReason) {
        guard let operation else { return }
        let outcome = operation.cancellation.outcome(reason)
        timer?.cancel()
        timer = nil
        self.operation = nil
        isBusy = false
        status = .finished(outcome)
    }
}

struct PlainTextPasteTarget: Equatable, Sendable {
    let pid: pid_t
    let identity: UUID

    init(application: FocusTarget, identity: UUID = UUID()) {
        pid = application.pid
        self.identity = identity
    }
}

enum PlainTextPasteTargetResult {
    case success(PlainTextPasteTarget), failure(PlainTextPasteReason)
}

struct PlainTextPasteSnapshot: Sendable {
    // An adapter-local synchronization token, not an NSPasteboard absolute changeCount.
    let changeCount: Int
    let text: String
    let sourceIdentity: UUID?

    init(changeCount: Int, text: String, sourceIdentity: UUID? = nil) {
        self.changeCount = changeCount
        self.text = text
        self.sourceIdentity = sourceIdentity
    }
}

enum PlainTextPasteRead: Sendable {
    case text(PlainTextPasteSnapshot), failure(PlainTextPasteReason)
}

enum PlainTextPasteWrite: Sendable {
    case written(Int), failure(PlainTextPasteReason)
}

struct PlainTextPastePost {
    let reason: PlainTextPasteReason
    let events: PlainTextPasteOutcome.EventEffect
}

protocol PlainTextPasteClipboard: Sendable {
    func read(cancellation: PlainTextPasteCancellation) async -> PlainTextPasteRead
    func replace(_ snapshot: PlainTextPasteSnapshot, cancellation: PlainTextPasteCancellation) async -> PlainTextPasteWrite
    func stillOwns(_ count: Int) async -> Bool
}

@MainActor
protocol PlainTextPasteInput {
    func captureTarget() -> PlainTextPasteTargetResult
    func validate(_ target: PlainTextPasteTarget) -> PlainTextPasteReason?
    func keysReleased(_ keys: [CGKeyCode]) -> Bool
    func postPaste(to target: PlainTextPasteTarget, releasing keys: [CGKeyCode],
                   cancellation: PlainTextPasteCancellation) -> PlainTextPastePost
}

@MainActor
protocol PlainTextPasteScheduler {
    var now: TimeInterval { get }
    func schedule(after delay: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any PlainTextPasteScheduled
}

protocol PlainTextPasteScheduled: Sendable { func cancel() }

// This lock protects only local flags/effect receipts, never a pasteboard call or arbitrary callback.
final class PlainTextPasteCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var clipboard: PlainTextPasteOutcome.ClipboardEffect = .unchanged
    private var events: PlainTextPasteOutcome.EventEffect = .notPosted
    var isCancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopped
    }
    func cancel() { lock.lock(); stopped = true; lock.unlock() }
    func record(clipboard effect: PlainTextPasteOutcome.ClipboardEffect) {
        lock.lock(); clipboard = effect; lock.unlock()
    }
    func record(events effect: PlainTextPasteOutcome.EventEffect) {
        lock.lock(); events = effect; lock.unlock()
    }
    func outcome(_ reason: PlainTextPasteReason) -> PlainTextPasteOutcome {
        lock.lock()
        defer { lock.unlock() }
        return PlainTextPasteOutcome(reason: reason, clipboard: clipboard, events: events)
    }
}

private struct SystemPlainTextPasteScheduler: PlainTextPasteScheduler {
    var now: TimeInterval { ProcessInfo.processInfo.systemUptime }
    func schedule(after delay: TimeInterval, _ action: @escaping @MainActor () -> Void) -> any PlainTextPasteScheduled {
        let item = DispatchWorkItem { MainActor.assumeIsolated { action() } }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: item)
        return Scheduled(item: item)
    }
    // DispatchWorkItem cancellation is thread-safe; the wrapper never mutates the work item reference.
    private final class Scheduled: PlainTextPasteScheduled, @unchecked Sendable {
        let item: DispatchWorkItem
        init(item: DispatchWorkItem) { self.item = item }
        func cancel() { item.cancel() }
    }
}

@MainActor
private final class SystemPlainTextPasteInput: PlainTextPasteInput {
    private struct Captured {
        let target: PlainTextPasteTarget
        let app: NSRunningApplication
        let focused: AXUIElement
    }
    private var captured: Captured?

    func captureTarget() -> PlainTextPasteTargetResult {
        captured = nil
        if let problem = safetyProblem() { return .failure(problem) }
        guard let application = SelectionProbe.currentTarget(),
              let app = NSRunningApplication(processIdentifier: application.pid), !app.isTerminated,
              let focused = focusedElement(application.pid) else { return .failure(.targetUnavailable) }
        let target = PlainTextPasteTarget(application: application)
        captured = Captured(target: target, app: app, focused: focused)
        if let problem = validate(target) { return .failure(problem) }
        return .success(target)
    }

    func validate(_ target: PlainTextPasteTarget) -> PlainTextPasteReason? {
        if let problem = safetyProblem() { return problem }
        guard let captured, captured.target == target, !captured.app.isTerminated,
              SelectionProbe.currentTarget()?.pid == target.pid,
              let running = NSRunningApplication(processIdentifier: target.pid), !running.isTerminated,
              running.launchDate == captured.app.launchDate,
              let focused = focusedElement(target.pid), CFEqual(focused, captured.focused) else {
            return .targetChanged
        }
        return nil
    }

    func keysReleased(_ keys: [CGKeyCode]) -> Bool {
        let modifiers: CGEventFlags = [.maskCommand, .maskShift, .maskControl, .maskAlternate, .maskSecondaryFn]
        return CGEventSource.flagsState(.hidSystemState).intersection(modifiers).isEmpty &&
            keys.allSatisfy { !CGEventSource.keyState(.hidSystemState, key: $0) }
    }

    func postPaste(to target: PlainTextPasteTarget, releasing keys: [CGKeyCode],
                   cancellation: PlainTextPasteCancellation) -> PlainTextPastePost {
        guard !cancellation.isCancelled else { return PlainTextPastePost(reason: .cancelled, events: .notPosted) }
        if let problem = validate(target) { return PlainTextPastePost(reason: problem, events: .notPosted) }
        guard keysReleased(keys) else { return PlainTextPastePost(reason: .keysStillPressed, events: .notPosted) }
        guard let source = CGEventSource(stateID: .privateState),
              let down = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: false) else {
            return PlainTextPastePost(reason: .eventCreationFailed, events: .notPosted)
        }
        down.flags = .maskCommand
        up.flags = .maskCommand
        guard !cancellation.isCancelled else { return PlainTextPastePost(reason: .cancelled, events: .notPosted) }
        down.postToPid(target.pid)
        up.postToPid(target.pid)
        return PlainTextPastePost(reason: .eventsSubmitted, events: .submittedUnconfirmed)
    }

    private func safetyProblem() -> PlainTextPasteReason? {
        if IsSecureEventInputEnabled() { return .secureInput }
        if !AXIsProcessTrusted() || !CGPreflightPostEventAccess() { return .accessibilityUnavailable }
        return nil
    }

    private func focusedElement(_ pid: pid_t) -> AXUIElement? {
        let application = AXUIElementCreateApplication(pid)
        guard AXUIElementSetMessagingTimeout(application, 0.2) == .success else { return nil }
        var element: CFTypeRef?
        guard AXUIElementCopyAttributeValue(application, kAXFocusedUIElementAttribute as CFString, &element) == .success,
              let element, CFGetTypeID(element) == AXUIElementGetTypeID() else { return nil }
        return (element as! AXUIElement)
    }
}
