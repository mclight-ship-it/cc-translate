import XCTest
@testable import CCTranslateSupport

final class ModelCatalogProtocolTests: XCTestCase {
    private let operation: [String: JSONValue] = ["operation": .string("model_catalog")]
    private let storage = ["config_load", "config_save", "history_load", "history_add", "history_clear"] +
        DictionaryRequest.operations.sorted()

    private func entry(_ id: String = "provider/Exact-ID", name: String = "Display",
                       description: String = "") -> JSONValue {
        .object(["id": .string(id), "name": .string(name), "description": .string(description)])
    }

    private var completion: [String: JSONValue] { ["models": .array([entry()])] }

    private func event(_ id: String, _ seq: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object(["v": .integer(1), "id": .string(id), "seq": .integer(seq),
                              "type": .string(type), "payload": .object(payload)]).encoded()
    }

    private func ready(_ mode: ProtocolState.Mode = .translation) -> [String: JSONValue] {
        let operations = mode == .diagnostic ? ["fixture", "runtime_probe"] :
            storage + (mode == .translation ? ["translate", "result_action", "translate_image", "model_catalog"] : [])
        var payload: [String: JSONValue] = [
            "protocol": .integer(1), "max_frame_bytes": .integer(65_536),
            "fixture": .bool(mode == .diagnostic), "capabilities": .array(operations.map(JSONValue.string))
        ]
        if mode == .translation { payload["backend"] = .string("native_appserver") }
        return payload
    }

    private func connected(_ mode: ProtocolState.Mode = .translation) throws -> ProtocolState {
        var state = ProtocolState(mode: mode)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", ready(mode)))
        return state
    }

    private func pending(started: Bool = true) throws -> ProtocolState {
        var state = try connected()
        try state.register(ClientMessage(id: "catalog", type: "request", payload: operation))
        _ = try state.receive(event("catalog", 0, "accepted", operation))
        if started { _ = try state.receive(event("catalog", 1, "started", operation)) }
        return state
    }

    func testCatalogRequiresExactNativeCapabilityAndOperationOnlyRequest() throws {
        for mode in [ProtocolState.Mode.diagnostic, .configuration] {
            var state = try connected(mode)
            XCTAssertThrowsError(try state.register(ClientMessage(id: "catalog", type: "request", payload: operation)))
            var hello = ProtocolState(mode: mode)
            try hello.register(ClientMessage(id: "hello", type: "hello"))
            var invalid = ready(mode)
            if case let .array(capabilities)? = invalid["capabilities"] {
                invalid["capabilities"] = .array(capabilities + [.string("model_catalog")])
            }
            XCTAssertThrowsError(try hello.receive(event("hello", 0, "ready", invalid)))
        }
        var hello = ProtocolState(mode: .translation)
        try hello.register(ClientMessage(id: "hello", type: "hello"))
        for capabilities in [
            storage + ["translate", "result_action", "translate_image"],
            storage + ["translate", "result_action", "translate_image", "model_catalog", "model_catalog"]
        ] {
            var invalid = ready()
            invalid["capabilities"] = .array(capabilities.map(JSONValue.string))
            XCTAssertThrowsError(try hello.receive(event("hello", 0, "ready", invalid)))
        }
        _ = try hello.receive(event("hello", 0, "ready", ready()))
        for extra in ["text", "model", "timeout", "provider", "config", "refresh"] {
            let invalid = ClientMessage(id: "invalid", type: "request", payload: operation.merging([extra: .null]) { _, new in new })
            XCTAssertThrowsError(try invalid.encoded())
            XCTAssertThrowsError(try hello.register(invalid))
        }
        let request = ClientMessage(id: "catalog", type: "request", payload: operation)
        try hello.register(request)
        XCTAssertEqual(try JSONValue.parse(Data(request.encoded().dropLast())).object?["payload"], .object(operation))
        XCTAssertTrue(hello.hasPendingModelCatalog)
        XCTAssertFalse(hello.hasPendingTranslation)
        XCTAssertNil(hello.pendingOutcomeUnknown)
    }

    func testCatalogDecoderPreservesOrderExactIDsAndNormalPresentationEquality() throws {
        let composed = "model-\u{00e9}", decomposed = "model-e\u{0301}"
        let models = try CodexModelEntry.decode(payload: ["models": .array([
            entry(composed, name: "Caf\u{00e9}", description: " \tfirst\r\n"),
            entry(decomposed, name: "Cafe\u{0301}", description: " \tfirst\r\n"),
            entry("  provider/Exact-ID:2026  ", name: "third")
        ])])
        XCTAssertEqual(models.count, 3)
        XCTAssertTrue(models[0].matchesID(composed))
        XCTAssertFalse(models[0].matchesID(decomposed))
        XCTAssertEqual(Array(models[1].id.utf8), Array(decomposed.utf8))
        XCTAssertEqual(Array(models[2].id.utf8), Array("  provider/Exact-ID:2026  ".utf8))
        XCTAssertNotEqual(models[0], models[1])
        XCTAssertEqual(Set(models).count, 3)
        let equivalentPresentation = try CodexModelEntry(payload: [
            "id": .string(composed), "name": .string("Cafe\u{0301}"), "description": .string(" \tfirst\r\n")
        ])
        XCTAssertEqual(models[0], equivalentPresentation)
        XCTAssertEqual(Set([models[0], equivalentPresentation]).count, 1)
        XCTAssertTrue(try CodexModelEntry.decode(payload: ["models": .array([])]).isEmpty)
    }

    func testCatalogDecoderRejectsMalformedControlledShapeAndExactDuplicateIDs() throws {
        let valid = try XCTUnwrap(entry().object)
        var invalidEntries: [JSONValue] = [.null, .string("id"), .array([])]
        for key in ["id", "name", "description"] {
            var missing = valid
            missing.removeValue(forKey: key)
            invalidEntries.append(.object(missing))
            for value: JSONValue in [.null, .bool(false), .integer(1), .array([]), .object([:])] {
                invalidEntries.append(.object(valid.merging([key: value]) { _, new in new }))
            }
        }
        invalidEntries.append(.object(valid.merging(["capabilities": .array([])]) { _, new in new }))
        for id in ["", " \t\n\u{2003}", "\u{001c}\u{001d}", "bad\0id"] {
            invalidEntries.append(entry(id))
        }
        invalidEntries.append(entry(name: ""))
        for value in invalidEntries {
            XCTAssertThrowsError(try CodexModelEntry.decode(payload: ["models": .array([value])]))
        }
        for payload: [String: JSONValue] in [
            [:], ["models": .null], ["models": .object([:])], ["models": .array([]), "extra": .null],
            ["models": .array([entry(), entry(name: "Different display")])]
        ] {
            XCTAssertThrowsError(try CodexModelEntry.decode(payload: payload))
        }
    }

    func testCatalogUsesExistingFrameBudgetWithoutModelCountOrSavedIDLengthCap() throws {
        var state = try pending()
        let models = (0..<600).map { entry("provider-\($0)") }
        let payload: [String: JSONValue] = ["models": .array(models)]
        let frame = try event("catalog", 2, "completed", payload)
        XCTAssertLessThan(frame.count, LineFramer.maxFrameBytes)
        _ = try state.receive(frame)
        XCTAssertEqual(try CodexModelEntry.decode(payload: payload).count, 600)
        XCTAssertEqual(try CodexModelEntry(payload: XCTUnwrap(entry(String(repeating: "m", count: 257)).object)).id.utf8.count, 257)
        let oversized: [String: JSONValue] = ["models": .array([entry(description: String(repeating: "x", count: 65_536))])]
        XCTAssertThrowsError(try CodexModelEntry.decode(payload: oversized))
        var second = try pending()
        XCTAssertThrowsError(try second.receive(event("catalog", 2, "completed", oversized)))
        XCTAssertNil(second.pendingOutcomeUnknown)
    }

    func testCatalogUnsolicitedPrematureDuplicateAndLateResultsAreRejected() throws {
        var state = try connected()
        XCTAssertThrowsError(try state.receive(event("unsolicited", 0, "completed", completion)))
        try state.register(ClientMessage(id: "catalog", type: "request", payload: operation))
        XCTAssertThrowsError(try state.receive(event("catalog", 0, "completed", completion)))
        _ = try state.receive(event("catalog", 0, "accepted", operation))
        XCTAssertThrowsError(try state.receive(event("catalog", 1, "completed", completion)))
        _ = try state.receive(event("catalog", 1, "started", operation))
        _ = try state.receive(event("catalog", 2, "completed", completion))
        XCTAssertFalse(state.hasPendingModelCatalog)
        XCTAssertThrowsError(try state.receive(event("catalog", 3, "completed", completion)))
        XCTAssertThrowsError(try state.receive(event("catalog", 2, "completed", completion)))
        XCTAssertThrowsError(try state.register(ClientMessage(id: "catalog", type: "request", payload: operation)))
    }

    func testCatalogNeverAcceptsTranslationDeltaOrSubmittedFields() throws {
        var state = try pending()
        for (type, payload) in [
            ("delta", ["text": JSONValue.string("not a translation"), "submitted": .bool(true)]),
            ("cancelled", ["submitted": JSONValue.bool(false)]),
            ("failed", ["code": JSONValue.string("model_catalog_failed"), "submitted": .bool(false)]),
            ("completed", completion.merging(["submitted": .bool(true)]) { _, new in new }),
            ("completed", ["models": JSONValue.array([.object(["id": .string("x")])])])
        ] {
            XCTAssertThrowsError(try state.receive(event("catalog", 2, type, payload)))
            XCTAssertTrue(state.hasPendingModelCatalog)
            XCTAssertFalse(state.hasPendingTranslation)
            XCTAssertNil(state.pendingOutcomeUnknown)
        }
        _ = try state.receive(event("catalog", 2, "completed", completion))
    }

    func testCatalogFailureCodesAreExactAndDoNotLoosenOtherOperations() throws {
        for code in ["model_catalog_failed", "model_catalog_too_large", "provider_cleanup_failed"] {
            var state = try pending()
            let terminal = try state.receive(event("catalog", 2, "failed", ["code": .string(code)]))
            XCTAssertEqual(terminal.safeFailureCode, code)
            XCTAssertFalse(terminal.safeFailureMessage.contains("outcome is unknown"))
            XCTAssertNil(state.pendingOutcomeUnknown)
            XCTAssertFalse(state.hasPendingModelCatalog)
            try state.register(ClientMessage(id: "refresh", type: "request", payload: operation))
        }
        for code in ["busy", "worker_start_failed", "provider_failed", "translation_timeout", "invalid_config", "unknown"] {
            var state = try pending()
            XCTAssertThrowsError(try state.receive(event("catalog", 2, "failed", ["code": .string(code)])))
        }
        for mode in [ProtocolState.Mode.configuration, .translation] {
            for code in ["model_catalog_failed", "model_catalog_too_large"] {
                var state = try connected(mode)
                try state.register(ClientMessage(id: "config", type: "request", payload: ["operation": .string("config_load")]))
                _ = try state.receive(event("config", 0, "accepted", ["operation": .string("config_load")]))
                _ = try state.receive(event("config", 1, "started", ["operation": .string("config_load")]))
                XCTAssertThrowsError(try state.receive(event("config", 2, "failed", ["code": .string(code)])))
                XCTAssertThrowsError(try state.receive(event("protocol", 0, "failed", ["code": .string(code)])))
            }
        }
    }

    func testCatalogAdmissionAndWorkerFailureRemainDeterminate() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "catalog", type: "request", payload: operation))
        _ = try state.receive(event("catalog", 0, "failed", ["code": .string("busy")]))
        XCTAssertNil(state.pendingOutcomeUnknown)
        state = try pending(started: false)
        _ = try state.receive(event("catalog", 1, "failed", ["code": .string("worker_start_failed")]))
        XCTAssertFalse(state.hasPendingModelCatalog)
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    func testCatalogCancellationBeforeAndAfterStartKeepsOneEmptyTerminal() throws {
        for started in [false, true] {
            var state = try pending(started: started)
            try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("catalog")]))
            _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
            XCTAssertTrue(state.hasPendingModelCatalog, "Acknowledgement is not worker drainage")
            XCTAssertNil(state.pendingOutcomeUnknown)
            let sequence: Int64 = started ? 2 : 1
            if !started { XCTAssertThrowsError(try state.receive(event("catalog", 1, "started", operation))) }
            _ = try state.receive(event("catalog", sequence, "cancelled"))
            XCTAssertFalse(state.hasPendingModelCatalog)
            XCTAssertThrowsError(try state.receive(event("catalog", sequence + 1, "completed", completion)))
        }
    }

    func testCatalogCancelAcknowledgementMayRaceAlreadyEmittedCompletion() throws {
        var state = try pending()
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("catalog")]))
        _ = try state.receive(event("catalog", 2, "completed", completion))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertFalse(state.hasPendingResponses)
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    func testCatalogDoesNotBlockStorageLaneOrHidePendingTranslationUnknown() throws {
        var state = try pending()
        let load: [String: JSONValue] = ["operation": .string("config_load")]
        try state.register(ClientMessage(id: "load", type: "request", payload: load))
        _ = try state.receive(event("load", 0, "accepted", load))
        _ = try state.receive(event("load", 1, "started", load))
        _ = try state.receive(event("load", 2, "completed", ["config": .object([:])]))
        try state.register(ClientMessage(id: "translation", type: "request", payload: [
            "operation": .string("translate"), "text": .string("synthetic"), "app_language": .string("en_US"),
            "origin": .string("text"), "use_cache": .bool(false), "record_history": .bool(false)
        ]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        _ = try state.receive(event("catalog", 2, "completed", completion))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
    }

    func testCatalogShutdownWaitsForReadOnlyWorkerTerminal() throws {
        var state = try pending()
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        XCTAssertNil(state.pendingOutcomeUnknown)
        _ = try state.receive(event("catalog", 2, "cancelled"))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertFalse(state.hasPendingResponses)
    }
}
