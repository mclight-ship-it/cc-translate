import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class TranslationLatencyTests: XCTestCase {
    private func prewarms(_ helper: ProductTestHelper) -> [ClientMessage] {
        helper.messages.filter { $0.payload["operation"] == .string("prewarm") }
    }

    private func distributionSample(
        _ trace: inout TranslationLatency, delay: Double = 10, preparation: Double = 0,
        source: TranslationLatency.Source = .text, provider: TranslationProvider = .codex,
        info: JSONValue? = .object(["requested_model": .string("auto-fast")]),
        cached: Bool? = false, flags: [String: JSONValue] = [:],
        summary: Bool = true, dispatched: Bool = true, outcome: TranslationLatency.Outcome = .completed
    ) {
        let start = (trace.recent.last?.started ?? 0) + 1_000
        let dispatch = start + preparation / 1_000
        trace.begin(intent: UUID(), source: source, provider: provider, trigger: nil, now: start)
        if dispatched { trace.dispatch(id: "request", now: dispatch) }
        else { trace.bind(id: "request") }
        if let info { trace.providerModelInfo(info, id: "request") }
        if let cached { trace.completionCacheState(cached, id: "request") }
        trace.providerMetrics(flags, id: "request")
        trace.mark("first_meaningful_output_ms", now: dispatch + delay / 1_000)
        if summary { trace.mark("summary_completed_ms", now: dispatch + delay * 2 / 1_000) }
        trace.finish(outcome, now: dispatch + delay * 3 / 1_000)
    }

    func testSettingsAndOldHelpersDoNotPrewarmButTranslationIntentDoes() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(capabilities: ["prewarm"])
        XCTAssertTrue(prewarms(helper).isEmpty)
        fixture.model.prepareTranslation()
        let warm = try XCTUnwrap(prewarms(helper).last)
        XCTAssertEqual(warm.payload, ["operation": .string("prewarm"), "app_language": .string("en_US")])
        fixture.model.prepareTranslation()
        XCTAssertEqual(prewarms(helper).count, 1)
        helper.event("completed", id: warm.id, payload: ["warmed": .bool(true)])
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(fixture.model.active)

        let old = try ProductTestHarness()
        defer { old.cleanUp() }
        let legacy = try old.ready()
        old.model.prepareTranslation()
        XCTAssertTrue(prewarms(legacy).isEmpty)

        let unconfigured = try ProductTestHarness(savedCLI: false)
        defer { unconfigured.cleanUp() }
        unconfigured.model.prepareTranslation(onlyIfConfigured: true)
        XCTAssertTrue(unconfigured.helpers.isEmpty)
        XCTAssertEqual(unconfigured.locatorRequests, 0)
    }

    func testForegroundDoesNotWaitForPrewarmAndFirstDeltaIsImmediate() throws {
        var time = 100.0
        let fixture = try ProductTestHarness(latencyClock: { time })
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(capabilities: ["prewarm"])
        fixture.model.prepareTranslation()
        let warm = try XCTUnwrap(prewarms(helper).last)
        fixture.model.input = "Translate this complete sentence."
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        XCTAssertTrue(helper.messages.contains {
            $0.type == "cancel" && $0.payload["request_id"] == .string(warm.id)
        })
        XCTAssertTrue(helper.dictionaryRequests.filter { $0.request.operation == "dictionary_lookup" }.isEmpty)
        time += 0.012
        helper.event("delta", id: request.id, payload: ["text": .string("First")])
        XCTAssertEqual(fixture.model.output, "First", "Do not wait for the 40ms batching timer.")
        XCTAssertEqual(try XCTUnwrap(fixture.model.latency.current?.milliseconds["first_output_ms"]), 12, accuracy: 0.001)
        helper.event("delta", id: request.id, payload: ["text": .string(" second")])
        XCTAssertEqual(fixture.model.output, "First")
        helper.event("completed", id: warm.id, payload: ["warmed": .bool(true)])
        XCTAssertTrue(fixture.model.active)
        time += 0.020
        helper.event("completed", id: request.id, payload: [
            "text": .string("First second"), "cached": .bool(false), "kind": .string("text"),
            "history": .string("disabled"), "timings": .object([
                "version_check_ms": .integer(1), "warm_process_hit": .integer(1),
                "private_text": .string("must-not-appear")
            ]),
            "model_info": .object(["requested_model": .string("auto-fast"),
                                   "resolved_model": .string("model-snapshot"),
                                   "reasoning_effort": .string("low")])
        ])
        XCTAssertEqual(fixture.model.output, "First second")
        XCTAssertEqual(fixture.model.latency.recent.last?.outcome, .completed)
        XCTAssertEqual(fixture.model.latency.recent.last?.milliseconds["provider_version_check_ms"], 1)
        XCTAssertFalse(fixture.model.latency.report.contains("must-not-appear"))
        XCTAssertFalse(fixture.model.latency.report.contains(fixture.model.input))
        XCTAssertFalse(fixture.model.latency.report.contains(fixture.model.output))
        XCTAssertTrue(fixture.model.latency.report.contains("requested_model=auto-fast"))
        XCTAssertTrue(fixture.model.latency.report.contains("cli_resolved_model=model-snapshot"))
        XCTAssertEqual(prewarms(helper).count, 2, "Refill the single warm slot after a model request.")
    }

    func testWarmFailureDoesNotBecomeTranslationAndDoesNotLoop() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(capabilities: ["prewarm"])
        fixture.model.prepareTranslation()
        let warm = try XCTUnwrap(prewarms(helper).last)
        helper.event("failed", id: warm.id, payload: ["code": .string("provider_failed"), "submitted": .bool(false)])
        XCTAssertEqual(fixture.model.prewarmFailure, "provider_failed")
        XCTAssertEqual(fixture.model.productPhase, .idle)
        XCTAssertEqual(prewarms(helper).count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    func testIdleIntentRefreshAndSleepReleaseAreBounded() throws {
        var time = 100.0
        let fixture = try ProductTestHarness(latencyClock: { time })
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(capabilities: ["prewarm"])
        fixture.model.prepareTranslation()
        helper.event("completed", id: try XCTUnwrap(prewarms(helper).last).id, payload: ["warmed": .bool(true)])
        time += 539
        fixture.model.prepareTranslation()
        XCTAssertEqual(prewarms(helper).count, 1)
        time += 2
        fixture.model.prepareTranslation()
        XCTAssertEqual(prewarms(helper).count, 2)
        fixture.model.suspendTranslationPreparation()
        XCTAssertEqual(helper.stopCount, 1)
        helper.stopped()
        XCTAssertEqual(fixture.helpers.count, 1)
        XCTAssertFalse(fixture.model.connected)
    }

    func testLateCancelledRequestCannotFinishNewIntentTiming() throws {
        var time = 100.0
        let fixture = try ProductTestHarness(latencyClock: { time })
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "First sentence."
        fixture.model.translate()
        let first = try XCTUnwrap(helper.translations.last)
        time += 1
        fixture.model.input = "Second sentence."
        fixture.model.translate()
        let intent = fixture.model.translationIntentID
        helper.event("cancelled", id: first.id)
        XCTAssertEqual(fixture.model.latency.current?.intent, intent)
        XCTAssertEqual(fixture.model.latency.recent.map(\.outcome), [.superseded])
        XCTAssertEqual(helper.translations.count, 2)
    }

    func testDictionaryFastPathKeepsWordsAndShortMultiwordTerms() {
        for text in ["hello", "machine learning", "New York", "  hello  ", "\u{4f60}\u{597d}",
                     String(repeating: "a", count: 30) + "\u{001c}"] {
            XCTAssertTrue(LocalDictionaryPreflight.mayBeTerm(text), text)
        }
        for text in ["", "A sentence.", "word\nword", String(repeating: "a", count: 31),
                     "\u{8fd9}\u{662f}\u{4e00}\u{53e5}\u{8bdd}\u{3002}"] {
            XCTAssertFalse(LocalDictionaryPreflight.mayBeTerm(text), text)
        }
    }

    func testTraceIsBoundedNumericAndRequestCorrelated() throws {
        var trace = TranslationLatency()
        for index in 0..<25 {
            let time = Double(index + 10)
            trace.begin(intent: UUID(), source: .selection, provider: .codex, trigger: time - 0.25, now: time)
            trace.dispatch(id: "request", now: time)
            trace.providerMetrics(["total_ms": .integer(999)], id: "stale")
            XCTAssertNil(trace.current?.milliseconds["provider_total_ms"])
            trace.providerMetrics(["total_ms": .number(.infinity), "spawn_ms": .integer(-1),
                                   "version_check_ms": .integer(2), "secret": .string("private")], id: "request")
            trace.mark("first_output_ms", now: time + 0.01)
            trace.mark("first_output_ms", now: time + 1)
            trace.finish(.completed, now: time + 1)
        }
        XCTAssertEqual(trace.recent.count, 20)
        XCTAssertEqual(trace.report.split(separator: "\n").count, 20)
        let sample = try XCTUnwrap(trace.recent.last)
        XCTAssertEqual(sample.milliseconds["input_ready_ms"], 250)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["first_output_ms"]), 260, accuracy: 0.001)
        XCTAssertNil(sample.milliseconds["provider_total_ms"])
        XCTAssertNil(sample.milliseconds["provider_spawn_ms"])
        XCTAssertFalse(trace.report.contains("private"))
    }

    func testSelectionReadTimeIsSeparateFromTranslationDispatch() throws {
        var time = 100.0
        let monitor = SelectionMonitorFixture()
        let fixture = try ProductTestHarness(selectionMonitor: monitor, latencyClock: { time })
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        fixture.model.startMonitor()
        monitor.onTranslationGesture?(time)
        time += 0.35
        fixture.model.translateSelection(.present("Selected sentence."))
        let sample = try XCTUnwrap(fixture.model.latency.current)
        XCTAssertEqual(sample.source, .selection)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["input_ready_ms"]), 350, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["dispatch_ms"]), 350, accuracy: 0.001)
        fixture.model.cancel()
    }

    func testMeaningfulAndSummaryBoundaryStagesIgnoreTitlesAndMarkExactlyOnce() throws {
        var time = 100.0
        let fixture = try ProductTestHarness(latencyClock: { time })
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "A private source sentence."
        fixture.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        time = 100.1
        helper.event("delta", id: request.id, payload: ["text": .string("## Sum")])
        XCTAssertEqual(try XCTUnwrap(fixture.model.latency.current?.milliseconds["first_output_ms"]), 100, accuracy: 0.001)
        XCTAssertNil(fixture.model.latency.current?.milliseconds["first_meaningful_output_ms"])
        time = 100.2
        helper.event("delta", id: request.id, payload: ["text": .string("mary\r\n")])
        XCTAssertNil(fixture.model.latency.current?.milliseconds["first_meaningful_output_ms"])
        time = 100.3
        helper.event("delta", id: request.id, payload: ["text": .string("Private summary.\r\n## Trans")])
        XCTAssertEqual(try XCTUnwrap(fixture.model.latency.current?.milliseconds["first_meaningful_output_ms"]), 300, accuracy: 0.001)
        XCTAssertNil(fixture.model.latency.current?.milliseconds["summary_completed_ms"])
        time = 100.4
        helper.event("delta", id: request.id, payload: ["text": .string("lation")])
        XCTAssertNil(fixture.model.latency.current?.milliseconds["summary_completed_ms"])
        time = 100.5
        helper.event("delta", id: request.id, payload: ["text": .string("\r\nPrivate translation.")])
        XCTAssertEqual(try XCTUnwrap(fixture.model.latency.current?.milliseconds["summary_completed_ms"]), 500, accuracy: 0.001)
        time = 101
        helper.event("completed", id: request.id, payload: [
            "text": .string("## Summary\nPrivate summary.\n## Translation\nPrivate translation."),
            "cached": .bool(false), "kind": .string("text"), "history": .string("disabled")
        ])
        let sample = try XCTUnwrap(fixture.model.latency.recent.last)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["first_meaningful_output_ms"]), 300, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["summary_completed_ms"]), 500, accuracy: 0.001)
        XCTAssertFalse(fixture.model.latency.report.contains("Private"))
        XCTAssertFalse(fixture.model.latency.report.contains(fixture.model.input))
    }

    func testCachedNonstreamOutputMarksBoundaryAndActionDoesNotInspectOldPrefix() throws {
        var time = 100.0
        let fixture = try ProductTestHarness(latencyClock: { time })
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "A private source sentence."
        fixture.model.translate()
        time = 100.5
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id), payload: [
            "text": .string("## Summary\nPrivate summary.\n## Translation\nPrivate translation."),
            "cached": .bool(true), "kind": .string("text"), "history": .string("unchanged")
        ])
        let sample = try XCTUnwrap(fixture.model.latency.recent.last)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["first_meaningful_output_ms"]), 500, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["summary_completed_ms"]), 500, accuracy: 0.001)
        XCTAssertEqual(sample.cached, true)
        XCTAssertTrue(fixture.model.latency.distributionReport.contains("cache_hit=1 memory_cache_hit=unknown"))
        fixture.model.performResultAction(.summary)
        let action = try XCTUnwrap(helper.resultActions.last)
        time = 100.6
        helper.event("delta", id: action.id, payload: ["text": .string("## Summary\n")])
        XCTAssertNil(fixture.model.latency.current?.milliseconds["first_meaningful_output_ms"])
        XCTAssertNil(fixture.model.latency.current?.milliseconds["summary_completed_ms"])
        fixture.model.cancel()
        time = 101
        helper.event("delta", id: action.id, payload: ["text": .string("late content\n## Translation\n")])
        helper.event("completed", id: action.id, payload: ["text": .string("late content")])
        XCTAssertEqual(fixture.model.latency.recent.last?.outcome, .cancelled)
        XCTAssertNil(fixture.model.latency.recent.last?.milliseconds["first_meaningful_output_ms"])
        XCTAssertNil(fixture.model.latency.current)
    }

    func testImmutableCaptureSnapshotPreservesLongSelectionAndSeparateProcessing() throws {
        var capture = CaptureTimingSnapshot(now: 100)
        capture.framesReady(now: 100.5)
        capture.completeSelection(now: 120.5)
        capture.mark("selection_composed_ms", now: 120.6)
        capture.mark("ocr_started_ms", now: 120.7)
        capture.mark("ocr_finished_ms", now: 121)
        var trace = TranslationLatency()
        let intent = UUID()
        trace.begin(intent: intent, source: .ocr, provider: .codex, trigger: nil, now: 121, capture: capture)
        capture.beginSelection(now: 122)
        XCTAssertEqual(trace.current?.started, 100)
        XCTAssertEqual(trace.current?.milliseconds["user_selection_ms"], 20_000)
        XCTAssertEqual(try XCTUnwrap(trace.current?.milliseconds["ocr_processing_ms"]), 300, accuracy: 0.001)
        XCTAssertEqual(trace.current?.milliseconds["input_ready_ms"], 21_000)
        XCTAssertNotNil(trace.current?.milliseconds["ocr_finished_ms"])
        trace.recordCapture(capture, provider: .codex, outcome: .cancelled, now: 140)
        XCTAssertEqual(trace.current?.intent, intent)
        XCTAssertEqual(trace.recent.last?.source, .capture)
        XCTAssertEqual(trace.recent.last?.milliseconds["user_selection_ms"], 38_000)
    }

    func testReportShowsRequestedAndOnlyConfirmedModelWithoutInferringAliasesOrPrivateText() throws {
        var trace = TranslationLatency()
        trace.begin(intent: UUID(), source: .text, provider: .codex, trigger: nil, now: 100)
        trace.dispatch(id: "request", now: 100)
        trace.providerModelInfo(.object(["requested_model": .string("auto-fast")]), id: "request")
        XCTAssertTrue(trace.report.contains("requested_model=auto-fast"))
        XCTAssertTrue(trace.report.contains("cli_resolved_model=unknown"))
        XCTAssertTrue(trace.report.contains("confirmed_reasoning_effort=unknown"))
        trace.providerModelInfo(.object(["requested_model": .string("private input")]), id: "request")
        trace.providerModelInfo(.object(["requested_model": .string("auto"),
                                        "resolved_model": .string("stale-model")]), id: "stale")
        XCTAssertFalse(trace.report.contains("private"))
        XCTAssertFalse(trace.report.contains("stale-model"))
        trace.providerModelInfo(.object(["requested_model": .string("auto-fast"),
                                        "resolved_model": .string("model-snapshot"),
                                        "reasoning_effort": .string("low")]), id: "request")
        trace.observeOutput("Private result text", id: "request", now: 100.1)
        trace.finish(.completed, now: 101)
        XCTAssertTrue(trace.report.contains("cli_resolved_model=model-snapshot"))
        XCTAssertTrue(trace.report.contains("confirmed_reasoning_effort=low"))
        XCTAssertFalse(trace.report.contains("Private result"))
        trace.providerModelInfo(.object(["requested_model": .string("late-model")]), id: "request")
        XCTAssertFalse(trace.report.contains("late-model"))
    }

    func testMemoryCacheMetricRemainsNumericAndRequestCorrelated() {
        var trace = TranslationLatency()
        trace.begin(intent: UUID(), source: .text, provider: .codex, trigger: nil, now: 100)
        trace.dispatch(id: "current", now: 100)
        trace.providerMetrics(["memory_cache_hit": .integer(1)], id: "stale")
        XCTAssertNil(trace.current?.milliseconds["provider_memory_cache_hit"])
        trace.providerMetrics(["cache_hit": .integer(1), "memory_cache_hit": .integer(1)], id: "current")
        XCTAssertEqual(trace.current?.milliseconds["provider_memory_cache_hit"], 1)
        trace.finish(.completed, now: 100.1)
        XCTAssertTrue(trace.report.contains("provider_memory_cache_hit=1.00"))
        XCTAssertTrue(trace.report.contains("cli_resolved_model=unknown"))
    }

    func testCompletedDistributionsUseExactMedianNearestRankP95AndExcludePreparation() {
        var trace = TranslationLatency()
        for delay in [10.0, 20, 30] {
            distributionSample(&trace, delay: delay, preparation: 25_000, source: .ocr)
        }
        XCTAssertTrue(trace.distributionReport.contains(
            "dispatch_to_first_meaningful_output_ms: n=3 min=10.00 median=20.00 p95=30.00"))
        distributionSample(&trace, delay: 100, preparation: 25_000, source: .ocr)
        let report = trace.distributionReport
        XCTAssertTrue(report.contains("sample_n=4"))
        XCTAssertTrue(report.contains("dispatch_to_first_meaningful_output_ms: n=4 min=10.00 median=25.00 p95=100.00"))
        XCTAssertTrue(report.contains("dispatch_to_summary_completed_ms: n=4 min=20.00 median=50.00 p95=200.00"))
        XCTAssertTrue(report.contains("dispatch_to_finished_ms: n=4 min=30.00 median=75.00 p95=300.00"))
        XCTAssertTrue(report.contains("small samples, not benchmarks"))
        XCTAssertTrue(report.contains("capture/user selection excluded"))
        XCTAssertFalse(report.contains("25000"))
        XCTAssertTrue(report.contains("cli_resolved_model=unknown"))
        XCTAssertTrue(report.contains("warm_process_hit=unknown"))
    }

    func testDistributionGroupsSeparateSourcesProvidersModelsEffortAndAllCacheWarmFlags() {
        var trace = TranslationLatency()
        let flags: [String: JSONValue] = ["memory_cache_hit": .integer(0), "warm_process_hit": .integer(0),
                                          "version_cache_hit": .integer(0)]
        let info: [String: JSONValue] = ["requested_model": .string("auto-fast"),
                                        "resolved_model": .string("model-v1"), "reasoning_effort": .string("low")]
        distributionSample(&trace, info: .object(info), flags: flags)
        distributionSample(&trace, source: .ocr, info: .object(info), flags: flags)
        distributionSample(&trace, source: .image, info: .object(info), flags: flags)
        distributionSample(&trace, provider: .claude, info: .object(info), flags: flags)
        for (key, value) in [("requested_model", "other-profile"), ("resolved_model", "model-v2"),
                             ("reasoning_effort", "high")] {
            distributionSample(&trace, info: .object(info.merging([key: .string(value)]) { _, new in new }),
                               flags: flags)
        }
        let cachedInfo: JSONValue = .object(["requested_model": .string("auto-fast")])
        distributionSample(&trace, info: cachedInfo, cached: true, flags: flags)
        distributionSample(&trace, info: cachedInfo, cached: true,
                           flags: flags.merging(["memory_cache_hit": .integer(1)]) { _, new in new })
        for key in ["warm_process_hit", "version_cache_hit"] {
            distributionSample(&trace, info: .object(info),
                               flags: flags.merging([key: .integer(1)]) { _, new in new })
        }
        distributionSample(&trace, info: nil, flags: flags)
        distributionSample(&trace, info: .object(info))
        let groups = trace.distributionReport.components(separatedBy: "\n").filter { $0.hasPrefix("source=") }
        XCTAssertEqual(groups.count, 13)
        XCTAssertTrue(groups.allSatisfy { $0.contains("sample_n=1") })
        XCTAssertTrue(groups.contains { $0.contains("source=image") })
        XCTAssertTrue(groups.contains { $0.contains("cache_hit=1 memory_cache_hit=1") })
        XCTAssertTrue(groups.contains { $0.contains("warm_process_hit=1") })
        XCTAssertTrue(groups.contains { $0.contains("warm_process_hit=0") })
        XCTAssertTrue(groups.contains { $0.contains("requested_model=unknown") })
    }

    func testDistributionExcludesUnfinishedFailedCancelledUnknownCacheAndUnsentSamples() {
        var trace = TranslationLatency()
        let outcomes: [TranslationLatency.Outcome] = [.failed, .cancelled, .superseded]
        for outcome in outcomes {
            distributionSample(&trace, outcome: outcome)
        }
        distributionSample(&trace, cached: nil)
        distributionSample(&trace, dispatched: false)
        XCTAssertTrue(trace.distributionReport.isEmpty)
        distributionSample(&trace, summary: false)
        trace.begin(intent: UUID(), source: .text, provider: .codex, trigger: nil, now: 10_000)
        trace.dispatch(id: "running", now: 10_000)
        trace.completionCacheState(true, id: "stale")
        XCTAssertNil(trace.current?.cached)
        XCTAssertTrue(trace.distributionReport.contains("sample_n=1"))
        XCTAssertFalse(trace.distributionReport.contains("dispatch_to_summary_completed_ms"))
    }

    func testCacheAndRealCompletionStaySeparateEvenWithoutProviderTimingFlags() {
        var trace = TranslationLatency()
        distributionSample(&trace, cached: false)
        distributionSample(&trace, cached: true)
        let groups = trace.distributionReport.components(separatedBy: "\n").filter { $0.hasPrefix("source=") }
        XCTAssertEqual(groups.count, 2)
        XCTAssertTrue(groups.allSatisfy { $0.contains("sample_n=1") })
        XCTAssertTrue(groups.contains { $0.contains("cache_hit=0 memory_cache_hit=unknown") })
        XCTAssertTrue(groups.contains { $0.contains("cache_hit=1 memory_cache_hit=unknown") })
    }

    func testDistributionsRespectTwentySampleRetentionAndNeverIncludeOutputText() {
        var trace = TranslationLatency()
        for value in 1...25 { distributionSample(&trace, delay: Double(value)) }
        XCTAssertTrue(trace.distributionReport.contains("sample_n=20"))
        XCTAssertTrue(trace.distributionReport.contains(
            "dispatch_to_first_meaningful_output_ms: n=20 min=6.00 median=15.50 p95=24.00"))
        trace.begin(intent: UUID(), source: .text, provider: .codex, trigger: nil, now: 30_000)
        trace.dispatch(id: "last", now: 30_000)
        trace.completionCacheState(false, id: "last")
        trace.providerModelInfo(.object(["requested_model": .string("private source text")]), id: "last")
        trace.observeOutput("private result text", id: "last", now: 30_000.01)
        trace.finish(.completed, now: 30_000.1)
        XCTAssertFalse(trace.report.contains("private"))
        XCTAssertTrue(trace.report.contains("requested_model=unknown"))
    }
}
