import XCTest
@testable import CCTranslateSupport

final class ImageTranslationProtocolTests: XCTestCase {
    private let operation: [String: JSONValue] = ["operation": .string("translate_image")]
    private let storage = ["config_load", "config_save", "history_load", "history_add", "history_clear"] +
        DictionaryRequest.operations.sorted()
    private var request: [String: JSONValue] {
        ["operation": .string("translate_image"), "image_path": .string("/synthetic/owned/region.png"),
         "image_bytes": .integer(80), "image_sha256": .string(String(repeating: "a", count: 64)),
         "app_language": .string("en_US"), "record_history": .bool(true)]
    }
    private var completion: [String: JSONValue] {
        ["text": .string("Synthetic image translation"), "submitted": .bool(true), "cached": .bool(false),
         "kind": .string("ocr"), "target_lang": .null, "summarize": .bool(false),
         "history": .string("recorded"), "history_error": .null]
    }

    private func event(_ id: String, _ sequence: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object(["v": .integer(1), "id": .string(id), "seq": .integer(sequence),
                              "type": .string(type), "payload": .object(payload)]).encoded()
    }

    private func connected(_ mode: ProtocolState.Mode = .translation) throws -> ProtocolState {
        var state = ProtocolState(mode: mode)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        let operations = mode == .diagnostic ? ["fixture", "runtime_probe"] :
            storage + (mode == .translation ? ["translate", "result_action", "model_catalog", "translate_image"] : [])
        var ready: [String: JSONValue] = [
            "protocol": .integer(1), "capabilities": .array(operations.map(JSONValue.string)),
            "max_frame_bytes": .integer(65_536), "fixture": .bool(mode == .diagnostic)
        ]
        if mode == .translation { ready["backend"] = .string("native_appserver") }
        _ = try state.receive(event("hello", 0, "ready", ready))
        return state
    }

    private func pending(started: Bool = true) throws -> ProtocolState {
        var state = try connected()
        try state.register(ClientMessage(id: "image", type: "request", payload: request))
        _ = try state.receive(event("image", 0, "accepted", operation))
        if started { _ = try state.receive(event("image", 1, "started", operation)) }
        return state
    }

    func testImageRequestIsNativeOnlyAndDoesNotOpenTheReferencedPath() throws {
        for mode in [ProtocolState.Mode.diagnostic, .configuration] {
            var state = try connected(mode)
            XCTAssertThrowsError(try state.register(ClientMessage(id: "image", type: "request", payload: request)))
            XCTAssertFalse(state.hasPendingTranslation)
        }
        var state = try connected()
        try state.register(ClientMessage(id: "image", type: "request", payload: request))
        XCTAssertTrue(state.hasPendingTranslation)
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        XCTAssertFalse(state.hasPendingHistory)
        XCTAssertFalse(state.hasPendingConfiguration)
        XCTAssertFalse(state.hasPendingModelCatalog)
    }

    func testImageRequestExactShapeAndScalarTypesRejectInlineImageBytes() throws {
        var state = try connected()
        var variants: [(label: String, payload: [String: JSONValue])] = []
        for key in request.keys.sorted() {
            var missing = request
            missing.removeValue(forKey: key)
            variants.append(("missing \(key)", missing))
        }
        for change: [String: JSONValue] in [
            ["image_path": .null], ["image_path": .string("relative.png")], ["image_path": .string("file:///tmp/image.png")],
            ["image_path": .string("/bad\0path")], ["image_path": .integer(1)], ["image_path": .string("")],
            ["image_bytes": .integer(0)], ["image_bytes": .integer(-1)], ["image_bytes": .bool(true)],
            ["image_bytes": .number(80)], ["image_bytes": .string("80")], ["image_bytes": .integer(83_886_081)],
            ["image_bytes": .number(.nan)], ["image_bytes": .number(.infinity)], ["image_bytes": .number(-.infinity)],
            ["image_sha256": .string(String(repeating: "A", count: 64))],
            ["image_sha256": .string(String(repeating: "a", count: 63))],
            ["image_sha256": .string(String(repeating: "g", count: 64))], ["image_sha256": .null],
            ["app_language": .string("zh")], ["app_language": .bool(false)], ["record_history": .integer(1)],
            ["record_history": .null], ["text": .string("not OCR substitution")],
            ["image": .string("base64")], ["image_data": .array([.integer(1)])],
            ["use_cache": .bool(false)], ["model": .string("not wire settings")],
            ["origin": .string("ocr")], ["config": .object([:])]
        ] {
            variants.append(("invalid \(String(reflecting: change))", request.merging(change) { _, value in value }))
        }

        let integralDouble = JSONValue.number(80)
        XCTAssertEqual(try JSONValue.parse(integralDouble.encoded()), .integer(80),
                       "Foundation serialization can erase a Double's nominal type; validate before encoding")
        for token in ["80.0", "8e1", "8E+1", "80.5", "true", "false", "null", "[]", "{}"] {
            let value = try JSONValue.parse(Data(token.utf8))
            if ["80.0", "8e1", "8E+1"].contains(token) { XCTAssertEqual(value, integralDouble) }
            var payload = request
            payload["image_bytes"] = value
            variants.append(("image_bytes wire token \(token)", payload))
        }
        for (index, variant) in variants.enumerated() {
            let (label, payload) = variant
            XCTAssertThrowsError(try ImageTranslationDocument.validateRequest(payload), label) { error in
                XCTAssertEqual(error as? ProbeError, .invalidPayload, label)
            }
            let message = ClientMessage(id: "invalid\(index)", type: "request", payload: payload)
            if payload["operation"] == .string("translate_image") {
                XCTAssertThrowsError(try message.encoded(), label) { error in
                    XCTAssertEqual(error as? ProbeError, .invalidPayload, label)
                }
            } else {
                // The generic encoder cannot dispatch an absent operation; registration must reject it before writing.
                XCTAssertNil(payload["operation"], label)
                XCTAssertNoThrow(try message.encoded(), label)
            }
            XCTAssertThrowsError(try state.register(message), label) { error in
                XCTAssertEqual(error as? ProbeError, .invalidPayload, label)
            }
        }
        XCTAssertFalse(state.hasPendingTranslation)
    }

    func testImageRequestAllowsFullByteBudgetButKeepsIPCFrameSmallAndUnchanged() throws {
        var state = try connected()
        for (index, language) in ["en_US", "zh_CN"].enumerated() {
            for bytes: Int64 in [1, 80, 83_886_080] {
                var payload = request
                payload["image_path"] = .string("/synthetic/\u{4e2d} space/region.png")
                payload["image_bytes"] = .integer(bytes)
                payload["record_history"] = .bool(index == 0)
                payload["app_language"] = .string(language)
                let message = ClientMessage(id: "image\(index)_\(bytes)", type: "request", payload: payload)
                let encoded = try message.encoded()
                XCTAssertLessThan(encoded.count, 1024, "The 80MiB image must never be embedded in JSONL")
                XCTAssertEqual(try JSONValue.parse(Data(encoded.dropLast())).object?["payload"], .object(payload))
                try state.register(message)
            }
        }
        var oversized = request
        oversized["image_path"] = .string("/" + String(repeating: "a", count: 65_536))
        XCTAssertThrowsError(try ClientMessage(id: "oversized", type: "request", payload: oversized).encoded())
    }

    func testImageNativeHandshakeRequiresTheNewCapabilityExactlyOnce() throws {
        var state = ProtocolState(mode: .translation)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        let base = storage + ["translate", "result_action", "model_catalog"]
        for capabilities in [base, base + ["translate_image", "translate_image"]] {
            XCTAssertThrowsError(try state.receive(event("hello", 0, "ready", [
                "protocol": .integer(1), "capabilities": .array(capabilities.map(JSONValue.string)),
                "max_frame_bytes": .integer(65_536), "fixture": .bool(false), "backend": .string("native_appserver")
            ])))
        }
    }

    func testImageStreamingCompletionIsSubmittedOCRWithoutCacheOrSummary() throws {
        var state = try pending()
        _ = try state.receive(event("image", 2, "delta", ["text": .string("provisional"), "submitted": .bool(true)]))
        _ = try state.receive(event("image", 3, "completed", completion))
        XCTAssertFalse(state.hasPendingTranslation)
        XCTAssertNil(state.pendingOutcomeUnknown)
        for change: [String: JSONValue] in [
            ["kind": .string("text")], ["kind": .string("dict")], ["kind": .string("code")],
            ["cached": .bool(true)], ["summarize": .bool(true)], ["submitted": .bool(false)],
            ["history": .string("unchanged")], ["image_path": .string("/not-returned")],
            ["image_sha256": request["image_sha256"]!], ["image": .string("no inline bytes")]
        ] {
            var invalid = try pending()
            XCTAssertThrowsError(try invalid.receive(event("image", 2, "completed", completion.merging(change) { _, value in value })))
            XCTAssertEqual(invalid.pendingOutcomeUnknown, .translationOutcomeUnknown)
        }
    }

    func testImageCompletionAllowsHistoryOptoutAndExplicitHistoryFailure() throws {
        for history in ["recorded", "disabled", "failed"] {
            var state = try pending()
            var payload = completion
            payload["history"] = .string(history)
            payload["history_error"] = history == "failed" ? .string("history_io_failed") : .null
            _ = try state.receive(event("image", 2, "completed", payload))
            XCTAssertNil(state.pendingOutcomeUnknown)
        }
    }

    func testImageErrorsUseHonestSubmissionStateWithoutBroadeningTextErrors() throws {
        for code in ImageTranslationDocument.failureCodes.sorted() {
            for submitted in [false, true] {
                var state = try pending()
                let terminal = try state.receive(event("image", 2, "failed", [
                    "code": .string(code), "submitted": .bool(submitted)
                ]))
                XCTAssertEqual(terminal.safeFailureCode, code)
                XCTAssertNil(state.pendingOutcomeUnknown)
            }
            var text = try connected()
            let textOperation: [String: JSONValue] = ["operation": .string("translate")]
            try text.register(ClientMessage(id: "text", type: "request", payload: [
                "operation": .string("translate"), "text": .string("synthetic"), "app_language": .string("en_US"),
                "origin": .string("text"), "use_cache": .bool(false), "record_history": .bool(false)
            ]))
            XCTAssertThrowsError(try text.receive(event("text", 0, "failed", ["code": .string(code)])))
            _ = try text.receive(event("text", 0, "accepted", textOperation))
            _ = try text.receive(event("text", 1, "started", textOperation))
            XCTAssertThrowsError(try text.receive(event("text", 2, "failed", ["code": .string(code), "submitted": .bool(false)])))
            var configuration = try connected(.configuration)
            try configuration.register(ClientMessage(id: "load", type: "request", payload: ["operation": .string("config_load")]))
            XCTAssertThrowsError(try configuration.receive(event("load", 0, "failed", ["code": .string(code)])))
        }
    }

    func testImageAdmissionWorkerFailureAndStartedFailureHaveDistinctShapes() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "image", type: "request", payload: request))
        _ = try state.receive(event("image", 0, "failed", ["code": .string("invalid_image_translation")]))
        XCTAssertNil(state.pendingOutcomeUnknown)
        state = try pending(started: false)
        _ = try state.receive(event("image", 1, "failed", ["code": .string("worker_start_failed")]))
        XCTAssertNil(state.pendingOutcomeUnknown)
        state = try pending()
        XCTAssertThrowsError(try state.receive(event("image", 2, "failed", ["code": .string("image_unavailable")])))
        _ = try state.receive(event("image", 2, "failed", ["code": .string("image_unavailable"), "submitted": .bool(false)]))
    }

    func testImageCancellationBeforeAndAfterStartDrainsExactlyOnce() throws {
        for started in [false, true] {
            var state = try pending(started: started)
            try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("image")]))
            _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
            XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
            let seq: Int64 = started ? 2 : 1
            _ = try state.receive(event("image", seq, "cancelled", started ? ["submitted": .bool(false)] : [:]))
            XCTAssertNil(state.pendingOutcomeUnknown)
            XCTAssertThrowsError(try state.receive(event("image", seq + 1, "completed", completion)))
        }
    }

    func testImageStreamCannotLaterDenySubmissionAndShutdownWaitsForDrain() throws {
        var state = try pending()
        _ = try state.receive(event("image", 2, "delta", ["text": .string("started"), "submitted": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("image", 3, "cancelled", ["submitted": .bool(false)])))
        XCTAssertThrowsError(try state.receive(event("image", 3, "failed", ["code": .string("image_cleanup_failed"), "submitted": .bool(false)])))
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        _ = try state.receive(event("image", 3, "failed", ["code": .string("image_cleanup_failed"), "submitted": .bool(true)]))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertFalse(state.hasPendingResponses)
    }

    func testImageUnsolicitedPrematureAndDuplicateTerminalsAreRejected() throws {
        var state = try connected()
        XCTAssertThrowsError(try state.receive(event("image", 0, "completed", completion)))
        try state.register(ClientMessage(id: "image", type: "request", payload: request))
        XCTAssertThrowsError(try state.receive(event("image", 0, "completed", completion)))
        _ = try state.receive(event("image", 0, "accepted", operation))
        XCTAssertThrowsError(try state.receive(event("image", 1, "completed", completion)))
        _ = try state.receive(event("image", 1, "started", operation))
        _ = try state.receive(event("image", 2, "completed", completion))
        XCTAssertThrowsError(try state.receive(event("image", 3, "completed", completion)))
        XCTAssertThrowsError(try state.register(ClientMessage(id: "image", type: "request", payload: request)))
    }

    func testImageShutdownCleanupFailureIsExplicitNativeControlErrorOnly() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "failed", ["code": .string("image_unavailable")])))
        let failure = try state.receive(event("shutdown", 0, "failed", ["code": .string("image_cleanup_failed")]))
        XCTAssertEqual(failure.safeFailureCode, "image_cleanup_failed")
        XCTAssertNil(state.pendingOutcomeUnknown)
        var configuration = try connected(.configuration)
        try configuration.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try configuration.receive(event("shutdown", 0, "failed", ["code": .string("image_cleanup_failed")])))
    }
}
