import Foundation
import CCTranslateSupport

struct TranslationLatency {
    enum Source: String { case text, selection, ocr, action }
    enum Outcome: String { case completed, cancelled, failed, superseded }
    struct Sample {
        let intent: UUID
        let source: Source
        var provider: TranslationProvider
        let started: TimeInterval
        var requestID: String?
        var milliseconds: [String: Double] = [:]
        var outcome: Outcome?
    }

    private(set) var current: Sample?
    private(set) var recent: [Sample] = []
    static let capacity = 20
    static let metricNames: Set<String> = [
        "helper_elapsed_ms", "total_ms", "spawn_ms", "initialize_ms",
        "hook_preflight_ms", "thread_start_ms", "turn_start_ms", "first_result_ms",
        "turn_first_result_ms", "turn_total_ms", "version_check_ms", "version_cache_hit",
        "warm_process_hit", "cache_hit"
    ]

    mutating func begin(intent: UUID, source: Source, provider: TranslationProvider,
                        trigger: TimeInterval?, now: TimeInterval) {
        finish(.superseded, now: now)
        guard now.isFinite else { return }
        let start = trigger.flatMap { $0.isFinite && $0 <= now && now - $0 <= 10 ? $0 : nil } ?? now
        current = Sample(intent: intent, source: source, provider: provider, started: start)
        mark("input_ready_ms", now: now)
    }

    mutating func dispatch(id: String, provider: TranslationProvider? = nil, now: TimeInterval) {
        current?.requestID = id
        if let provider { current?.provider = provider }
        mark("dispatch_ms", now: now)
    }

    mutating func bind(id: String) {
        current?.requestID = id
    }

    mutating func mark(_ stage: String, now: TimeInterval) {
        guard let sample = current, sample.milliseconds[stage] == nil,
              now.isFinite, now >= sample.started else { return }
        current?.milliseconds[stage] = (now - sample.started) * 1_000
    }

    mutating func providerMetrics(_ values: [String: JSONValue], id: String) {
        guard current?.requestID == id else { return }
        for name in Self.metricNames {
            guard let value = values[name]?.number, value.isFinite, (0...3_600_000).contains(value) else { continue }
            current?.milliseconds["provider_" + name] = value
        }
    }

    mutating func finish(_ outcome: Outcome, now: TimeInterval) {
        guard current != nil else { return }
        mark("finished_ms", now: now)
        current?.outcome = outcome
        if let sample = current {
            recent.append(sample)
            if recent.count > Self.capacity { recent.removeFirst(recent.count - Self.capacity) }
        }
        current = nil
    }

    var report: String {
        (recent + (current.map { [$0] } ?? [])).suffix(Self.capacity).enumerated().map { index, sample in
            let values = sample.milliseconds.sorted { $0.key < $1.key }.map {
                "\($0.key)=\(String(format: "%.2f", $0.value))"
            }.joined(separator: " ")
            return "\(index + 1) \(sample.source.rawValue) \(sample.provider.rawValue) " +
                "\(sample.outcome?.rawValue ?? "running") \(values)"
        }.joined(separator: "\n")
    }
}

enum LocalDictionaryPreflight {
    static func mayBeTerm(_ text: String) -> Bool {
        let whitespace = CharacterSet.whitespacesAndNewlines.union(
            CharacterSet(charactersIn: "\u{001c}\u{001d}\u{001e}\u{001f}"))
        let trimmed = text.trimmingCharacters(in: whitespace)
        guard let last = trimmed.last else { return false }
        // Only reject cases that cc_classify.is_single_word always rejects.
        // Short multi-word terms still need the shared dictionary classifier.
        return trimmed.unicodeScalars.count <= 30 &&
            !trimmed.contains("\n") &&
            !".!?\u{2026}\u{3002}\u{ff01}\u{ff1f}\u{ff0c},;\u{ff1b}:\u{ff1a}".contains(last)
    }
}
