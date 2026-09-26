import XCTest
@testable import CCTranslateSupport

final class ResultActionProtocolTests: XCTestCase {
    private let operation: [String: JSONValue] = ["operation": .string("result_action")]
    private var completion: [String: JSONValue] {
        ["text": .string("Action result"), "submitted": .bool(true), "cached": .bool(false),
         "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
         "history": .string("disabled"), "history_error": .null]
    }

    private func event(_ id: String, _ sequence: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object(["v": .integer(1), "id": .string(id), "seq": .integer(sequence),
                              "type": .string(type), "payload": .object(payload)]).encoded()
    }

    private func connected() throws -> ProtocolState {
        var state = ProtocolState(mode: .translation)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", [
            "protocol": .integer(1), "max_frame_bytes": .integer(65_536), "fixture": .bool(false),
            "backend": .string("native_appserver"),
            "capabilities": .array(["config_load", "config_save", "history_load", "history_add",
                                    "history_clear", "translate", "result_action", "translate_image", "model_catalog"].map(JSONValue.string) +
                DictionaryRequest.operations.sorted().map(JSONValue.string))
        ]))
        return state
    }

    private func pending(started: Bool = true) throws -> ProtocolState {
        var state = try connected()
        try state.register(ResultAction.summary.request(text: "Primary", appLanguage: "en_US", id: "action"))
        _ = try state.receive(event("action", 0, "accepted", operation))
        if started { _ = try state.receive(event("action", 1, "started", operation)) }
        return state
    }

    func testTypedActionRequestsAndEveryTargetHaveExactShapeAndFullResultInputBudget() throws {
        for action in ResultAction.allCases {
            let targets: [String?] = action == .retranslate ?
                TranslationDocument.targetLanguages.sorted().map(Optional.some) : [nil]
            for target in targets {
                let text = String(repeating: "\u{1f600}", count: 6000)
                let message = action.request(text: text, appLanguage: "zh_CN", targetLanguage: target)
                let data = try message.encoded()
                XCTAssertEqual(data.last, 10)
                XCTAssertEqual(try JSONValue.parse(Data(data.dropLast())).object?["payload"],
                               .object(["operation": .string("result_action"), "action": .string(action.rawValue),
                                        "text": .string(text), "app_language": .string("zh_CN"),
                                        "target_language": target.map(JSONValue.string) ?? .null]))
                var state = try connected()
                try state.register(message)
                XCTAssertTrue(state.hasPendingTranslation)
                XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
            }
        }
    }

    func testInvalidActionsTargetsFieldsAndUTF8LimitsAreRejectedBeforeRegistration() throws {
        let valid = ResultAction.summary.request(text: "Primary", appLanguage: "en_US", id: "action").payload
        var invalid: [[String: JSONValue]] = valid.keys.map { key in
            var missing = valid
            missing.removeValue(forKey: key)
            return missing
        }
        let changes: [[String: JSONValue]] = [
            ["action": .string("unknown")], ["action": .bool(true)], ["text": .string("")],
            ["text": .string(" \n\u{001c}")], ["text": .string(String(repeating: "a", count: 24_001))],
            ["text": .string(String(repeating: "\u{1f600}", count: 6000) + "a")],
            ["app_language": .string("en")], ["target_language": .string("en")],
            ["target_language": .bool(false)], ["action": .string("retranslate")],
            ["action": .string("retranslate"), "target_language": .string("unknown")],
            ["use_cache": .bool(true)], ["record_history": .bool(false)], ["unknown": .null]
        ]
        invalid += changes.map { valid.merging($0) { _, replacement in replacement } }
        for (index, payload) in invalid.enumerated() {
            var state = try connected()
            let request = ClientMessage(id: "invalid", type: "request", payload: payload)
            if payload["operation"] == .string("result_action") {
                XCTAssertThrowsError(try request.encoded(), "Action payload \(index)")
            } else {
                // Generic encoding also serves hello/diagnostics; registration requires an operation.
                XCTAssertNoThrow(try request.encoded())
            }
            XCTAssertThrowsError(try state.register(request), "Request registration \(index)")
            XCTAssertFalse(state.hasPendingTranslation)
        }
    }

    func testActionStreamCompletionCannotClaimCacheHistoryOrAutomaticSummary() throws {
        var state = try pending()
        XCTAssertThrowsError(try state.receive(event("action", 2, "delta", [
            "text": .string(String(repeating: "a", count: 4096)), "submitted": .bool(true)
        ])))
        _ = try state.receive(event("action", 2, "delta", ["text": .string("part"), "submitted": .bool(true)]))
        let variants: [[String: JSONValue]] = [
            ["cached": .bool(true)], ["history": .string("recorded")], ["summarize": .bool(true)],
            ["history": .string("failed"), "history_error": .string("history_io_failed")],
            ["submitted": .bool(false)]
        ]
        for changes in variants {
            XCTAssertThrowsError(try state.receive(event(
                "action", 3, "completed", completion.merging(changes) { _, new in new })))
        }
        _ = try state.receive(event("action", 3, "completed", completion))
        XCTAssertFalse(state.hasPendingTranslation)
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    func testStartedActionCancellationAllowsInFlightDeltasUntilOriginalTerminal() throws {
        var state = try pending()
        try state.register(ClientMessage(id: "cancel", type: "cancel",
                                         payload: ["request_id": .string("action")]))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertTrue(state.hasPendingTranslation)
        _ = try state.receive(event("action", 2, "delta", ["text": .string("in flight"), "submitted": .bool(true)]))
        _ = try state.receive(event("action", 3, "cancelled", ["submitted": .bool(true)]))
        XCTAssertFalse(state.hasPendingTranslation)
    }

    func testQueuedActionCancellationPreventsStartingAndWorkerFailureIsDeterminate() throws {
        var state = try pending(started: false)
        try state.register(ClientMessage(id: "cancel", type: "cancel",
                                         payload: ["request_id": .string("action")]))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("action", 1, "started", operation)))
        _ = try state.receive(event("action", 1, "cancelled"))
        XCTAssertNil(state.pendingOutcomeUnknown)
        var failed = try pending(started: false)
        _ = try failed.receive(event("action", 1, "failed", ["code": .string("worker_start_failed")]))
        XCTAssertNil(failed.pendingOutcomeUnknown)
    }

    func testActionsTakeUnknownPriorityAndShutdownWaitsForActionAndStorageTerminals() throws {
        var state = try pending()
        let storage: [String: JSONValue] = ["operation": .string("history_clear")]
        try state.register(ClientMessage(id: "history", type: "request", payload: storage))
        _ = try state.receive(event("history", 0, "accepted", storage))
        _ = try state.receive(event("history", 1, "started", storage))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        _ = try state.receive(event("action", 2, "completed", completion))
        XCTAssertEqual(state.pendingOutcomeUnknown, .historyOutcomeUnknown)
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        _ = try state.receive(event("history", 2, "completed", [
            "cleared": .bool(true), "revision": .string(String(repeating: "0", count: 64))
        ]))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertFalse(state.hasPendingResponses)
    }
}
