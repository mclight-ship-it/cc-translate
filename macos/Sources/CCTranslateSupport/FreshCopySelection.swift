import Foundation

struct PassiveCopySource: Equatable {
    let target: FocusTarget
    // Nil still permits the existing AX path, but cannot authorize clipboard fallback.
    let focusIdentity: UUID?
}

@MainActor
protocol FreshCopyClipboard {
    func revision() -> Int
    func read(revision: Int, whileValid: () -> Bool) -> SelectionResult
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
        pending = nil
        pair.reset()
        pairSource = nil
        pairTime = nil
        pairBaseline = nil
    }

    func observe(time: TimeInterval, isCopy: Bool, isRepeat: Bool) {
        let generation = UUID()
        self.generation = generation
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
        // Global key notifications can arrive after the application has copied.
        // Remember metadata at the first press; never read contents until the pair.
        var revisionAtPress: Int?
        if fallbackEnabled {
            let revision = clipboard.revision()
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
        pending = Request(id: id, source: source, deadline: time + Self.freshnessWindow, baseline: baseline)
        if fallbackEnabled, baseline != nil {
            // The user's fresh copy is sufficient; do not block on a browser's
            // missing/slow AXSelectedText when the copied text is already available.
            pending?.waiting = true
            poll()
            guard pending?.id == id else { return }
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
        guard let request = pending, request.waiting, !request.reading, let baseline = request.baseline else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, .unknown(failure))
            return
        }
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
        pending?.reading = true
        let result = clipboard.read(revision: revision) { [weak self] in
            guard let self, self.pending?.id == request.id else { return false }
            return self.invalidReason(id: request.id) == nil
        }
        guard pending?.id == request.id else { return }
        if let failure = invalidReason(id: request.id) {
            finish(id: request.id, .unknown(failure))
            return
        }
        let finalRevision = clipboard.revision()
        guard pending?.id == request.id else { return }
        finish(id: request.id, finalRevision == revision ? result : .unknown(.clipboardChanged))
    }

    private func invalidReason(id: UUID, requireDeadline: Bool = true) -> SelectionResult.Reason? {
        guard let request = pending, request.id == id else { return .clipboardChanged }
        if let failure = environment.securityFailure() { return failure }
        guard environment.source(fallbackEnabled) == request.source else { return .focusChanged }
        guard pending?.id == id else { return .clipboardChanged }
        if requireDeadline {
            let now = environment.now()
            guard now.isFinite, now < request.deadline,
                  now >= request.deadline - Self.freshnessWindow else { return .copyNotObserved }
        }
        return nil
    }

    private func finish(id: UUID, _ result: SelectionResult) {
        guard pending?.id == id else { return }
        pending = nil
        onSelection?(result)
    }
}
