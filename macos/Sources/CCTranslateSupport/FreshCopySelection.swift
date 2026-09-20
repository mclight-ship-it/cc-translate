import Foundation

struct PassiveCopySource: Equatable {
    let target: FocusTarget
    // Nil still permits the existing AX path, but cannot authorize clipboard fallback.
    let focusIdentity: UUID?
}

@MainActor
protocol FreshCopyClipboard {
    func revision() -> Int
    func read(revision: Int, timeout: TimeInterval, cancellation: PlainTextPasteCancellation,
              whileValid: @escaping @MainActor () -> Bool,
              completion: @escaping @MainActor (SelectionResult) -> Void)
}

@MainActor
struct PassiveCopyEnvironment {
    let now: () -> TimeInterval
    let source: (_ requireFocusIdentity: Bool) -> PassiveCopySource?
    let securityFailure: () -> SelectionResult.Reason?
    let selection: (FocusTarget) -> SelectionResult
}

@MainActor
final class FreshCopySelection {
    static let freshnessWindow: TimeInterval = 0.5
    static let copyWaitWindow: TimeInterval = 2
    private let environment: PassiveCopyEnvironment
    private let clipboard: any FreshCopyClipboard
    private var pair = DoubleCopyState()
    private var pairSource: PassiveCopySource?
    private var pairTime: TimeInterval?
    private var pairBaseline: Int?
    private var pending: Request?
    private var generation = UUID()
    private var acceptEventsAfter: TimeInterval?
    private(set) var fallbackEnabled = false
    var interval: DoubleCopyInterval { pair.interval }
    var onSelection: ((SelectionResult) -> Void)?

    private struct Request {
        let id: UUID
        let source: PassiveCopySource
        let deadline: TimeInterval
        var baseline: Int?
        var waiting = false
        var reading = false
        var lastFailure: SelectionResult?
        var attemptedRevision: Int?
        var retryAfter: TimeInterval = 0
        let cancellation = PlainTextPasteCancellation()
    }

    init(environment: PassiveCopyEnvironment, clipboard: any FreshCopyClipboard) {
        self.environment = environment
        self.clipboard = clipboard
    }

    func setFallbackEnabled(_ enabled: Bool) {
        guard fallbackEnabled != enabled else { return }
        cancel()
        fallbackEnabled = enabled
    }

    func setInterval(_ interval: DoubleCopyInterval) {
        guard pair.interval != interval else { return }
        cancel()
        pair = DoubleCopyState(interval: interval)
    }

    func cancel() {
        generation = UUID()
        acceptEventsAfter = environment.now()
        pending?.cancellation.cancel()
        pending = nil
        pair.reset()
        pairSource = nil
        pairTime = nil
        pairBaseline = nil
    }

    func observe(time: TimeInterval, isCopy: Bool, isRepeat: Bool, revisionBeforeCopy: Int? = nil) {
        let generation = UUID()
        self.generation = generation
        pending?.cancellation.cancel()
        pending = nil
        let now = environment.now()
        guard isCopy, !isRepeat, time.isFinite, now.isFinite, now >= time,
              acceptEventsAfter.map({ time >= $0 }) ?? true,
              now - time <= Self.freshnessWindow, environment.securityFailure() == nil else {
            cancel()
            return
        }
        guard self.generation == generation else { return }
        if let pairTime, time <= pairTime {
            cancel()
            return
        }
        // The forwarding event tap snapshots only changeCount before delivering C
        // to the source. A late global notification cannot establish that baseline.
        // Never inspect clipboard contents until the second press.
        var revisionAtPress: Int?
        if fallbackEnabled {
            let revision = revisionBeforeCopy ?? clipboard.revision()
            guard self.generation == generation else { return }
            if revision >= 0 { revisionAtPress = revision }
        }
        guard let source = environment.source(fallbackEnabled) else {
            cancel()
            return
        }
        guard self.generation == generation else { return }
        if pairSource != source { pair.reset(); pairBaseline = nil }
        let baseline = pairBaseline
        guard pair.observe(time: time, pid: source.target.pid, isCopy: true, isRepeat: false,
                           secureInput: false) else {
            pairSource = source
            pairTime = time
            pairBaseline = source.focusIdentity == nil ? nil : revisionAtPress
            return
        }
        pairSource = nil
        pairTime = nil
        pairBaseline = nil
        let id = UUID()
        pending = Request(id: id, source: source, deadline: time + Self.copyWaitWindow, baseline: baseline)
        if fallbackEnabled, baseline != nil {
            // The user's fresh copy is sufficient; do not block on a browser's
            // missing/slow AXSelectedText when the copied text is already available.
            pending?.waiting = true
            poll()
            guard pending?.id == id else { return }
            if pending?.reading == true { return }
            pending?.waiting = false
        }
        let selection = environment.selection(source.target)
        guard pending?.id == id else { return }
        if let failure = invalidReason(id: id, requireDeadline: false) {
            finish(id: id, .unknown(failure))
            return
        }
        let needsCopy = selection == .absent || selection == .unknown(.unsupported) ||
            selection == .unknown(.unavailable)
        guard needsCopy, fallbackEnabled, pending?.baseline != nil else {
            finish(id: id, selection)
            return
        }
        pending?.waiting = true
        poll()
    }

    func poll() {
        guard let request = pending, request.waiting, let baseline = request.baseline else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, failure == .copyNotObserved
                   ? request.lastFailure ?? .unknown(failure) : .unknown(failure))
            return
        }
        guard !request.reading else { return }
        let revision = clipboard.revision()
        guard pending?.id == request.id else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, .unknown(failure))
            return
        }
        guard revision >= baseline else {
            finish(id: request.id, .unknown(.clipboardChanged))
            return
        }
        guard revision > baseline else { return }
        guard revision != request.attemptedRevision || environment.now() >= request.retryAfter else { return }
        pending?.reading = true
        pending?.attemptedRevision = revision
        clipboard.read(revision: revision, timeout: max(0.001, request.deadline - environment.now()),
                       cancellation: request.cancellation, whileValid: { [weak self] in
            guard let self, self.pending?.id == request.id else { return false }
            return self.invalidReason(id: request.id) == nil
        }, completion: { [weak self] result in
            self?.completeRead(id: request.id, revision: revision, result: result)
        })
    }

    private func completeRead(id: UUID, revision: Int, result: SelectionResult) {
        guard let request = pending, request.id == id else { return }
        pending?.reading = false
        guard pending?.id == request.id else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, .unknown(failure))
            return
        }
        let finalRevision = clipboard.revision()
        guard pending?.id == request.id else { return }
        guard finalRevision >= revision else {
            finish(id: id, .unknown(.clipboardChanged))
            return
        }
        let coherent = finalRevision == revision ? result : .unknown(.clipboardChanged)
        switch coherent {
        case .unknown(.clipboardChanged), .unknown(.clipboardUnavailable), .unknown(.clipboardUnsupported):
            // clear/declare/provide and the two native copy commands are separate
            // publications. Discard partial reads and retry within the same deadline,
            // including promised data that becomes available without a new revision.
            pending?.lastFailure = coherent
            pending?.waiting = true
            pending?.retryAfter = environment.now() + 0.05
        default:
            finish(id: id, coherent)
        }
    }

    private func invalidReason(id: UUID, requireDeadline: Bool = true) -> SelectionResult.Reason? {
        guard let request = pending, request.id == id else { return .clipboardChanged }
        if let failure = environment.securityFailure() { return failure }
        guard environment.source(fallbackEnabled) == request.source else { return .focusChanged }
        guard pending?.id == id else { return .clipboardChanged }
        if requireDeadline {
            let now = environment.now()
            guard now.isFinite, now < request.deadline,
                  now >= request.deadline - Self.copyWaitWindow else { return .copyNotObserved }
        }
        return nil
    }

    private func finish(id: UUID, _ result: SelectionResult) {
        guard pending?.id == id else { return }
        pending?.cancellation.cancel()
        pending = nil
        onSelection?(result)
    }
}
