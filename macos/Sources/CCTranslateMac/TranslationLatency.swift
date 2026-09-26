import Foundation
import CCTranslateSupport

/// Value-only handoff. Capturing never starts or replaces a translation intent.
struct CaptureTimingSnapshot {
    let id = UUID()
    let started: TimeInterval
    private(set) var stages: [String: TimeInterval] = [:]
    private(set) var selectionWait: TimeInterval = 0
    private var selectingSince: TimeInterval?

    init(now: TimeInterval) {
        started = now
        mark("capture_start_ms", now: now)
    }

    mutating func mark(_ stage: String, now: TimeInterval) {
        guard now.isFinite, now >= started, stages[stage] == nil else { return }
        stages[stage] = now
    }

    mutating func framesReady(now: TimeInterval) {
        mark("frames_ready_ms", now: now)
        beginSelection(now: now)
    }

    mutating func beginSelection(now: TimeInterval) {
        guard selectingSince == nil, now.isFinite, now >= started else { return }
        for stage in ["selection_ready_ms", "selection_completed_ms", "selection_composed_ms",
                      "ocr_started_ms", "ocr_finished_ms"] {
            stages.removeValue(forKey: stage)
        }
        mark("selection_ready_ms", now: now)
        selectingSince = now
    }

    mutating func completeSelection(now: TimeInterval) {
        guard let start = selectingSince, now.isFinite, now >= start else { return }
        selectionWait += now - start
        selectingSince = nil
        mark("selection_completed_ms", now: now)
    }

    mutating func restartOCR() {
        stages.removeValue(forKey: "ocr_started_ms")
        stages.removeValue(forKey: "ocr_finished_ms")
    }

    func milliseconds(until now: TimeInterval) -> [String: Double] {
        guard started.isFinite, now.isFinite, now >= started else { return [:] }
        var values = stages.filter { $0.value <= now }.mapValues { ($0 - started) * 1_000 }
        values["user_selection_ms"] = (selectionWait + (selectingSince.map { max(0, now - $0) } ?? 0)) * 1_000
        if let start = stages["ocr_started_ms"], let end = stages["ocr_finished_ms"], end >= start {
            values["ocr_processing_ms"] = (end - start) * 1_000
        }
        return values
    }
}

struct TranslationLatency {
    enum Source: String { case text, selection, ocr, image, action, capture }
    enum Outcome: String { case completed, cancelled, failed, superseded }
    struct Sample {
        let intent: UUID
        let source: Source
        var provider: TranslationProvider
        let started: TimeInterval
        var requestID: String?
        var milliseconds: [String: Double] = [:]
        var modelInfo: TranslationModelInfo?
        var cached: Bool?
        var outcome: Outcome?
    }
    struct Completion: Equatable {
        let intent: UUID
        let firstReadableText: TimeInterval?
        let summaryComplete: TimeInterval?
        let total: TimeInterval
    }

    private(set) var current: Sample?
    private(set) var recent: [Sample] = []
    static let capacity = 20
    static let metricNames: Set<String> = [
        "helper_elapsed_ms", "total_ms", "spawn_ms", "initialize_ms",
        "hook_preflight_ms", "thread_start_ms", "turn_start_ms", "first_result_ms",
        "turn_first_result_ms", "turn_total_ms", "version_check_ms", "version_cache_hit",
        "warm_process_hit", "cache_hit", "memory_cache_hit"
    ]

    func elapsedSeconds(for intent: UUID, now: TimeInterval) -> Int? {
        guard let sample = current, sample.intent == intent, sample.outcome == nil else { return nil }
        // The capture trace may start long before submission while the user selects a region.
        let start = sample.started + (sample.milliseconds["input_ready_ms"] ?? 0) / 1_000
        let elapsed = now - start
        guard elapsed.isFinite, elapsed >= 0, elapsed < Double(Int.max) else { return nil }
        return Int(elapsed.rounded(.down))
    }

    func completedTiming(for intent: UUID) -> Completion? {
        guard current == nil, let sample = recent.last(where: { $0.intent == intent }),
              sample.outcome == .completed, sample.source != .capture,
              let start = sample.milliseconds["input_ready_ms"], start.isFinite, start >= 0,
              let finish = sample.milliseconds["finished_ms"], finish.isFinite, finish >= start else { return nil }
        func seconds(_ stage: String) -> TimeInterval? {
            guard let value = sample.milliseconds[stage], value.isFinite,
                  value >= start, value <= finish else { return nil }
            return (value - start) / 1_000
        }
        return Completion(intent: intent,
                          firstReadableText: seconds("first_meaningful_output_ms"),
                          summaryComplete: seconds("summary_completed_ms"),
                          total: (finish - start) / 1_000)
    }

    mutating func begin(intent: UUID, source: Source, provider: TranslationProvider,
                        trigger: TimeInterval?, now: TimeInterval, capture: CaptureTimingSnapshot? = nil) {
        finish(.superseded, now: now)
        guard now.isFinite else { return }
        let capture = capture.flatMap { $0.started.isFinite && $0.started <= now ? $0 : nil }
        let start = capture?.started ??
            trigger.flatMap { $0.isFinite && $0 <= now && now - $0 <= 10 ? $0 : nil } ?? now
        current = Sample(intent: intent, source: source, provider: provider, started: start)
        if let capture { current?.milliseconds = capture.milliseconds(until: now) }
        mark("input_ready_ms", now: now)
    }

    mutating func recordCapture(_ capture: CaptureTimingSnapshot, provider: TranslationProvider,
                                outcome: Outcome, now: TimeInterval) {
        guard capture.started.isFinite, now.isFinite, now >= capture.started else { return }
        var sample = Sample(intent: capture.id, source: .capture, provider: provider, started: capture.started)
        sample.milliseconds = capture.milliseconds(until: now)
        sample.milliseconds["finished_ms"] = (now - capture.started) * 1_000
        sample.outcome = outcome
        append(sample)
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
        let elapsed = (now - sample.started) * 1_000
        guard elapsed.isFinite else { return }
        current?.milliseconds[stage] = elapsed
        if stage == "image_attachment_ready_ms", let start = sample.milliseconds["image_attachment_started_ms"],
           elapsed >= start {
            current?.milliseconds["image_attachment_processing_ms"] = elapsed - start
        }
    }

    mutating func providerMetrics(_ values: [String: JSONValue], id: String) {
        guard current?.requestID == id else { return }
        for name in Self.metricNames {
            guard let value = values[name]?.number, value.isFinite, (0...3_600_000).contains(value) else { continue }
            current?.milliseconds["provider_" + name] = value
        }
    }

    mutating func observeOutput(_ text: String, id: String, isFinal: Bool = false, now: TimeInterval) {
        guard current?.requestID == id else { return }
        let progress = TranslationOutputProgress.inspect(text, isFinal: isFinal)
        if progress.hasMeaningfulContent { mark("first_meaningful_output_ms", now: now) }
        if progress.summaryComplete { mark("summary_completed_ms", now: now) }
    }

    mutating func providerModelInfo(_ value: JSONValue, id: String) {
        guard current?.requestID == id, let info = try? TranslationModelInfo(value) else { return }
        current?.modelInfo = info
    }

    mutating func completionCacheState(_ cached: Bool, id: String) {
        guard current?.requestID == id else { return }
        current?.cached = cached
    }

    mutating func finish(_ outcome: Outcome, now: TimeInterval) {
        guard current != nil else { return }
        mark("finished_ms", now: now)
        current?.outcome = outcome
        if let sample = current {
            append(sample)
        }
        current = nil
    }

    private mutating func append(_ sample: Sample) {
        recent.append(sample)
        if recent.count > Self.capacity { recent.removeFirst(recent.count - Self.capacity) }
    }

    private struct DistributionGroup: Hashable {
        let source: String
        let provider: String
        let requested: String?
        let resolved: String?
        let effort: String?
        let cache: Bool?
        let memory: Bool?
        let warm: Bool?
        let versionCache: Bool?

        init(_ sample: Sample) {
            source = sample.source.rawValue
            provider = sample.provider.rawValue
            requested = sample.modelInfo?.requestedModel
            resolved = sample.modelInfo?.resolvedModel
            effort = sample.modelInfo?.reasoningEffort
            cache = sample.cached ?? Self.flag("cache_hit", sample)
            memory = Self.flag("memory_cache_hit", sample)
            warm = Self.flag("warm_process_hit", sample)
            versionCache = Self.flag("version_cache_hit", sample)
        }

        private static func flag(_ name: String, _ sample: Sample) -> Bool? {
            switch sample.milliseconds["provider_" + name] {
            case .some(0): return false
            case .some(1): return true
            default: return nil
            }
        }

        var label: String {
            func flag(_ value: Bool?) -> String { value.map { $0 ? "1" : "0" } ?? "unknown" }
            return "source=\(source) provider=\(provider) requested_model=\(requested ?? "unknown") " +
                "cli_resolved_model=\(resolved ?? "unknown") confirmed_reasoning_effort=\(effort ?? "unknown") " +
                "cache_hit=\(flag(cache)) memory_cache_hit=\(flag(memory)) " +
                "warm_process_hit=\(flag(warm)) " +
                "version_cache_hit=\(flag(versionCache))"
        }
    }

    var distributionReport: String {
        let samples = recent.filter { sample in
            guard sample.outcome == .completed, let dispatch = sample.milliseconds["dispatch_ms"],
                  let finish = sample.milliseconds["finished_ms"], dispatch.isFinite, finish.isFinite,
                  finish >= dispatch, DistributionGroup(sample).cache != nil else { return false }
            return true
        }
        guard !samples.isEmpty else { return "" }
        let groups = Dictionary(grouping: samples, by: DistributionGroup.init)
        let sections = groups.map { entry -> (String, String) in
            let (group, samples) = entry
            let metrics = ["first_meaningful_output_ms", "summary_completed_ms", "finished_ms"].compactMap { stage -> String? in
                let values = samples.compactMap { sample -> Double? in
                    guard let end = sample.milliseconds[stage], let start = sample.milliseconds["dispatch_ms"],
                          end.isFinite, start.isFinite, end >= start else { return nil }
                    return end - start
                }.sorted()
                guard !values.isEmpty else { return nil }
                let count = values.count
                let median = count % 2 == 0 ? values[count / 2 - 1] / 2 + values[count / 2] / 2 : values[count / 2]
                let p95 = values[Int(ceil(Double(count) * 0.95)) - 1]
                func number(_ value: Double) -> String {
                    String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), value)
                }
                return "  dispatch_to_\(stage): n=\(count) min=\(number(values[0])) " +
                    "median=\(number(median)) p95=\(number(p95))"
            }
            return (group.label, "\(group.label) sample_n=\(samples.count)\n" + metrics.joined(separator: "\n"))
        }.sorted { $0.0 < $1.0 }.map(\.1)
        return "Completed sample distributions (retained <=\(Self.capacity); small samples, not benchmarks; " +
            "P95 nearest-rank). Dispatch-relative wall-clock, not model compute; capture/user selection excluded. " +
            "Unsent and unknown-cache samples excluded.\n" + sections.joined(separator: "\n")
    }

    var report: String {
        let samples = (recent + (current.map { [$0] } ?? [])).suffix(Self.capacity).enumerated().map { index, sample in
            let values = sample.milliseconds.sorted { $0.key < $1.key }.map {
                "\($0.key)=\(String(format: "%.2f", $0.value))"
            }.joined(separator: " ")
            return "\(index + 1) \(sample.source.rawValue) \(sample.provider.rawValue) " +
                "\(sample.outcome?.rawValue ?? "running") " +
                "requested_model=\(sample.modelInfo?.requestedModel ?? "unknown") " +
                "cli_resolved_model=\(sample.modelInfo?.resolvedModel ?? "unknown") " +
                "confirmed_reasoning_effort=\(sample.modelInfo?.reasoningEffort ?? "unknown") \(values)"
        }.joined(separator: "\n")
        let distributions = distributionReport
        return distributions.isEmpty ? samples : samples + "\n\n" + distributions
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
