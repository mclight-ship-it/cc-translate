import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class TranslationLatencyTests: XCTestCase {
    private func prewarms(_ helper: ProductTestHelper) -> [ClientMessage] {
        helper.messages.filter { $0.payload["operation"] == .string("prewarm") }
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
            ])
        ])
        XCTAssertEqual(fixture.model.output, "First second")
        XCTAssertEqual(fixture.model.latency.recent.last?.outcome, .completed)
        XCTAssertEqual(fixture.model.latency.recent.last?.milliseconds["provider_version_check_ms"], 1)
        XCTAssertFalse(fixture.model.latency.report.contains("must-not-appear"))
        XCTAssertFalse(fixture.model.latency.report.contains(fixture.model.input))
        XCTAssertFalse(fixture.model.latency.report.contains(fixture.model.output))
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
}
