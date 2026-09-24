import XCTest
@testable import CCTranslateSupport

final class PrewarmProtocolTests: XCTestCase {
    private let operation: [String: JSONValue] = ["operation": .string("prewarm")]
    private var request: ClientMessage {
        ClientMessage(id: "warm", type: "request", payload: [
            "operation": .string("prewarm"), "app_language": .string("en_US")
        ])
    }

    private func event(_ id: String, _ seq: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object([
            "v": .integer(1), "id": .string(id), "seq": .integer(seq),
            "type": .string(type), "payload": .object(payload)
        ]).encoded()
    }

    private func ready(prewarm: Bool = true, provider: TranslationProvider = .codex) -> [String: JSONValue] {
        let required = ["config_load", "config_save", "history_load", "history_add", "history_clear",
                        "translate", "result_action", "translate_image", "model_catalog"] +
            DictionaryRequest.operations.sorted()
        return [
            "protocol": .integer(1), "max_frame_bytes": .integer(65_536),
            "fixture": .bool(false), "backend": .string(provider.backend),
            "capabilities": .array((required + (prewarm ? ["prewarm"] : [])).map(JSONValue.string))
        ]
    }

    private func connected(prewarm: Bool = true, provider: TranslationProvider = .codex) throws -> ProtocolState {
        var state = ProtocolState(mode: .translation, provider: provider)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", ready(prewarm: prewarm, provider: provider)))
        return state
    }

    private func pending(started: Bool = true) throws -> ProtocolState {
        var state = try connected()
        try state.register(request)
        _ = try state.receive(event("warm", 0, "accepted", operation))
        if started { _ = try state.receive(event("warm", 1, "started", operation)) }
        return state
    }

    func testOptionalCapabilityNegotiationPreservesOldHelpersButDoesNotPermitUnadvertisedOperation() throws {
        for provider in [TranslationProvider.codex, .claude] {
            for supported in [true, false] {
                var state = try connected(prewarm: supported, provider: provider)
                XCTAssertEqual(state.supportsPrewarm, supported)
                if supported { try state.register(request) }
                else { XCTAssertThrowsError(try state.register(request)) }
            }
        }
        for extra in [JSONValue.string("prewarm"), .string("unknown"), .bool(true)] {
            var state = ProtocolState(mode: .translation)
            try state.register(ClientMessage(id: "hello", type: "hello"))
            var invalid = ready()
            if case let .array(capabilities)? = invalid["capabilities"] {
                invalid["capabilities"] = .array(capabilities + [extra])
            }
            XCTAssertThrowsError(try state.receive(event("hello", 0, "ready", invalid)))
            XCTAssertFalse(state.supportsPrewarm)
        }
    }

    func testRequestContainsExactlyOperationAndSupportedLanguageWithoutUserText() throws {
        for language in ["zh_CN", "en_US"] {
            var state = try connected()
            var payload = request.payload
            payload["app_language"] = .string(language)
            let message = ClientMessage(id: "warm", type: "request", payload: payload)
            try state.register(message)
            let data = try message.encoded()
            XCTAssertEqual(try JSONValue.parse(Data(data.dropLast())).object?["payload"], .object(payload))
        }
        let variants: [[String: JSONValue]] = [
            ["text": .string("")], ["model": .string("private")], ["image_path": .string("private")],
            ["direction": .string("to_en")], ["app_language": .bool(true)], ["app_language": .null],
            ["app_language": .array([])], ["app_language": .string("en")]
        ]
        for variant in variants {
            var state = try connected()
            let message = ClientMessage(id: "warm", type: "request", payload:
                request.payload.merging(variant) { _, value in value })
            XCTAssertThrowsError(try state.register(message))
            XCTAssertThrowsError(try message.encoded())
        }
        for key in request.payload.keys {
            var payload = request.payload
            payload.removeValue(forKey: key)
            var state = try connected()
            XCTAssertThrowsError(try state.register(ClientMessage(id: "warm", type: "request", payload: payload)))
        }
    }

    func testWarmIsNotTranslationOrStorageAndDoesNotSerializeTheirStartedEvents() throws {
        var state = try pending()
        XCTAssertTrue(state.hasPendingPrewarm)
        XCTAssertFalse(state.hasPendingTranslation)
        XCTAssertNil(state.pendingOutcomeUnknown)
        let config: [String: JSONValue] = ["operation": .string("config_load")]
        try state.register(ClientMessage(id: "config", type: "request", payload: config))
        _ = try state.receive(event("config", 0, "accepted", config))
        _ = try state.receive(event("config", 1, "started", config))
        _ = try state.receive(event("warm", 2, "completed", ["warmed": .bool(true)]))
        XCTAssertFalse(state.hasPendingPrewarm)
        XCTAssertEqual(state.pendingOutcomeUnknown, .configurationOutcomeUnknown)
    }

    func testWarmCanStartWhileAnOlderConfigurationRequestIsStillPending() throws {
        var state = try connected()
        let config: [String: JSONValue] = ["operation": .string("config_load")]
        try state.register(ClientMessage(id: "config", type: "request", payload: config))
        _ = try state.receive(event("config", 0, "accepted", config))
        try state.register(request)
        _ = try state.receive(event("warm", 0, "accepted", operation))
        _ = try state.receive(event("warm", 1, "started", operation))
        _ = try state.receive(event("warm", 2, "completed", ["warmed": .bool(true)]))
    }

    func testWarmCompletionIsExactNoTextDeltaOrSubmissionAndOnlyOneTerminal() throws {
        var state = try pending()
        let invalid: [[String: JSONValue]] = [
            [:], ["warmed": .integer(1)], ["warmed": .bool(false)],
            ["warmed": .bool(true), "submitted": .bool(false)],
            ["warmed": .bool(true), "text": .string("")],
            ["warmed": .bool(true), "timings": .object([:])]
        ]
        for payload in invalid {
            XCTAssertThrowsError(try state.receive(event("warm", 2, "completed", payload)))
        }
        XCTAssertThrowsError(try state.receive(event("warm", 2, "delta", [
            "text": .string("not allowed"), "submitted": .bool(true)])))
        _ = try state.receive(event("warm", 2, "completed", ["warmed": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("warm", 3, "completed", ["warmed": .bool(true)])))
        XCTAssertFalse(state.hasPendingPrewarm)
        XCTAssertFalse(state.hasPendingResponses)
    }

    func testQueuedAndStartedWarmCancellationAllowNormalCancelAcknowledgement() throws {
        for started in [false, true] {
            var state = try pending(started: started)
            try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("warm")]))
            if started {
                _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
                XCTAssertThrowsError(try state.receive(event("warm", 2, "cancelled", ["submitted": .bool(false)])))
                _ = try state.receive(event("warm", 2, "cancelled"))
            } else {
                _ = try state.receive(event("warm", 1, "cancelled"))
                _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
            }
            XCTAssertFalse(state.hasPendingPrewarm)
        }
    }

    func testWarmFailureRequiresFixedCodeAndSubmittedFalseEvenForCleanupFailure() throws {
        for code in PrewarmDocument.failureCodes {
            var state = try pending()
            XCTAssertThrowsError(try state.receive(event("warm", 2, "failed", [
                "code": .string(code), "submitted": .bool(true)])))
            let result = try state.receive(event("warm", 2, "failed", [
                "code": .string(code), "submitted": .bool(false)]))
            XCTAssertEqual(result.safeFailureCode, code)
            XCTAssertFalse(state.hasPendingPrewarm)
        }
        let invalid: [[String: JSONValue]] = [
            ["code": .string("prewarm_failed")],
            ["code": .string("private path"), "submitted": .bool(false)],
            ["code": .string("provider_failed"), "submitted": .bool(false)],
            ["code": .string("prewarm_failed"), "submitted": .integer(0)],
            ["code": .string("prewarm_failed"), "submitted": .bool(false), "text": .string("private")]
        ]
        for payload in invalid {
            var state = try pending()
            XCTAssertThrowsError(try state.receive(event("warm", 2, "failed", payload)))
        }
    }

    func testShutdownWaitsForPrewarmTerminalWithoutUnknownTranslationOutcome() throws {
        var state = try pending()
        try state.register(ClientMessage(id: "stop", type: "shutdown"))
        XCTAssertNil(state.pendingOutcomeUnknown)
        XCTAssertThrowsError(try state.receive(event("stop", 0, "completed")))
        _ = try state.receive(event("warm", 2, "cancelled"))
        _ = try state.receive(event("stop", 0, "completed"))
    }
}

final class TranslationTimingProtocolTests: XCTestCase {
    private var completion: [String: JSONValue] {
        [
            "text": .string("synthetic output"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("disabled"), "history_error": .null
        ]
    }

    func testLegacyAndBoundedNumericTimingsAreAcceptedForTranslationActionsAndImages() throws {
        try TranslationDocument.validateCompletion(completion, streamed: false)
        var payload = completion
        var timings = Dictionary(uniqueKeysWithValues: TranslationDocument.timingFields.map { ($0, JSONValue.number(12.5)) })
        for flag in TranslationDocument.timingFlags { timings[flag] = .integer(0) }
        payload["timings"] = .object(timings)
        try TranslationDocument.validateCompletion(payload, streamed: true)
        try TranslationDocument.validateCompletion(payload, streamed: true, resultAction: true)
        payload["kind"] = .string("ocr")
        try TranslationDocument.validateCompletion(payload, streamed: true)
        try ImageTranslationDocument.validateCompletion(payload)
        for value in [JSONValue.integer(0), .number(TranslationDocument.maxTimingMilliseconds)] {
            payload["timings"] = .object(["total_ms": value])
            try TranslationDocument.validateCompletion(payload, streamed: false)
        }
        payload = completion
        payload["cached"] = .bool(true)
        payload["submitted"] = .bool(false)
        payload["history"] = .string("unchanged")
        payload["timings"] = .object(["cache_hit": .integer(1), "helper_elapsed_ms": .integer(0)])
        try TranslationDocument.validateCompletion(payload, streamed: false)
    }

    func testTimingsRejectEveryNonNumericPrivateUnknownNegativeNonfiniteAndUnboundedValue() throws {
        let invalid: [JSONValue] = [.string("private"), .bool(true), .bool(false), .null, .array([]), .object([:]),
                                    .integer(-1), .number(.nan), .number(.infinity), .number(-.infinity),
                                    .number(TranslationDocument.maxTimingMilliseconds + 1)]
        for value in invalid {
            var payload = completion
            payload["timings"] = .object(["total_ms": value])
            XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
        }
        for value in [JSONValue.string("private"), .null, .array([]), .bool(false), .integer(0)] {
            var payload = completion
            payload["timings"] = value
            XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
        }
        for key in ["model", "path", "account", "config", "text", "turn_submitted", "unbounded_new_metric"] {
            var payload = completion
            payload["timings"] = .object([key: .integer(1)])
            XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
        }
        for value in [JSONValue.bool(true), .number(1), .integer(2), .integer(-1), .string("1")] {
            var payload = completion
            payload["timings"] = .object(["warm_process_hit": value])
            XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
        }
        var payload = completion
        payload["timings"] = .object(["cache_hit": .integer(1)])
        XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
    }

    func testTimingsDoNotWeakenRequiredCompletionFieldsOrPermitOtherAdditions() throws {
        for key in completion.keys {
            var payload = completion
            payload["timings"] = .object([:])
            payload.removeValue(forKey: key)
            XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
        }
        var payload = completion
        payload["timings"] = .object([:])
        payload["model"] = .string("private")
        XCTAssertThrowsError(try TranslationDocument.validateCompletion(payload, streamed: false))
    }
}
