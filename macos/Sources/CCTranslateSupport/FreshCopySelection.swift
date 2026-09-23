import Foundation

struct PassiveCopySource: Equatable {
    let target: FocusTarget
    // Focus identity is optional: copying in browser/PDF views does not require AX text support.
    let focusIdentity: UUID?
    var launchDate: Date? = nil
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
    static let freshnessWindow: TimeInterval = 2
    static let copyWaitWindow: TimeInterval = 2
    static let promiseWaitWindow: TimeInterval = 5
    static let readerCleanupWindow: TimeInterval = 0.5
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
        let startedAt: TimeInterval
        var deadline: TimeInterval
        var baseline: Int?
        var waiting = false
        var reading = false
        var attemptedAXRevision: Int?
        var lastFailure: SelectionResult?
        var attemptedRevision: Int?
        var retryAfter: TimeInterval = 0
        let cancellation = PlainTextPasteCancellation()

        var expiredResult: SelectionResult {
            lastFailure ?? .unknown(attemptedRevision == nil ? .copyNotObserved : .clipboardUnavailable)
        }
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
        // The native copy gesture, pre-dispatch counter and foreground process
        // establish freshness. AX focus objects in PDF/web renderers are often
        // missing or recreated by Copy itself, so they cannot be a prerequisite.
        guard let source = environment.source(false) else {
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
            pairBaseline = revisionAtPress
            return
        }
        pairSource = nil
        pairTime = nil
        pairBaseline = nil
        let id = UUID()
        pending = Request(id: id, source: source, startedAt: time,
                          deadline: time + Self.copyWaitWindow, baseline: baseline)
        if fallbackEnabled, baseline != nil {
            // The user's fresh copy is sufficient; do not block on a browser's
            // missing/slow AXSelectedText when the copied text is already available.
            pending?.waiting = true
            poll()
            guard pending?.id == id else { return }
            if pending?.attemptedRevision != nil { return }
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
                   ? request.expiredResult : .unknown(failure))
            return
        }
        guard !request.reading else { return }
        let revision = clipboard.revision()
        guard pending?.id == request.id else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, failure == .copyNotObserved ? request.expiredResult : .unknown(failure))
            return
        }
        guard revision >= baseline else {
            finish(id: request.id, .unknown(.clipboardChanged))
            return
        }
        guard revision > baseline else { return }
        guard revision != request.attemptedRevision || environment.now() >= request.retryAfter else { return }
        // A published copy can contain promised data. Give that provider time to
        // respond without making an unchanged/no-selection gesture wait as long.
        pending?.deadline = request.startedAt + Self.promiseWaitWindow
        pending?.reading = true
        pending?.attemptedRevision = revision
        clipboard.read(revision: revision,
                       timeout: max(0.001, request.startedAt + Self.promiseWaitWindow - environment.now()),
                       cancellation: request.cancellation, whileValid: { [weak self] in
            guard let self, self.pending?.id == request.id else { return false }
            return self.invalidReason(id: request.id) == nil
        }, completion: { [weak self] result in
            self?.completeRead(id: request.id, revision: revision, result: result)
        })
    }

    private func completeRead(id: UUID, revision: Int, result: SelectionResult) {
        guard let request = pending, request.id == id else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, failure == .copyNotObserved ? request.expiredResult : .unknown(failure))
            return
        }
        let finalRevision = clipboard.revision()
        guard pending?.id == request.id else { return }
        guard finalRevision >= revision else {
            finish(id: id, .unknown(.clipboardChanged))
            return
        }
        var coherent = finalRevision == revision ? result : .unknown(.clipboardChanged)
        if request.attemptedAXRevision != revision,
           coherent == .unknown(.clipboardUnsupported) || coherent == .unknown(.clipboardUnavailable) {
            if let failure = invalidReason(id: id) {
                finish(id: id, failure == .copyNotObserved ? request.expiredResult : .unknown(failure))
                return
            }
            pending?.attemptedAXRevision = revision
            pending?.lastFailure = coherent
            // Keep this request busy while AX can reenter the run loop. A usable
            // native selection rescues unreadable copy formats, not a retired copy.
            let selection = environment.selection(request.source.target)
            guard pending?.id == id else { return }
            if let failure = invalidReason(id: id) {
                finish(id: id, failure == .copyNotObserved ? request.expiredResult : .unknown(failure))
                return
            }
            let currentRevision = clipboard.revision()
            guard pending?.id == id else { return }
            guard currentRevision >= revision else {
                finish(id: id, .unknown(.clipboardChanged))
                return
            }
            if currentRevision != revision {
                coherent = .unknown(.clipboardChanged)
            } else {
                switch selection {
                case .present, .unknown(.secureInput), .unknown(.accessibility), .unknown(.inputMonitoring),
                     .unknown(.focusChanged), .unknown(.tooLarge):
                    finish(id: id, selection)
                    return
                default:
                    break
                }
            }
        }
        switch coherent {
        case .absent, .unknown(.clipboardChanged), .unknown(.clipboardUnavailable), .unknown(.clipboardUnsupported):
            // clear/declare/provide and the two native copy commands are separate
            // publications. Discard partial reads and retry within the same deadline,
            // including promised data that becomes available without a new revision.
            pending?.reading = false
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
        guard environment.source(false) == request.source else { return .focusChanged }
        guard pending?.id == id else { return .clipboardChanged }
        if requireDeadline {
            let now = environment.now()
            // The isolated worker enforces its own read timeout. Its response is
            // delivered only after the exited process/group and pipes are drained.
            let deadline = request.deadline + (request.reading ? Self.readerCleanupWindow : 0)
            guard now.isFinite, now < deadline,
                  now >= request.startedAt else { return .copyNotObserved }
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
