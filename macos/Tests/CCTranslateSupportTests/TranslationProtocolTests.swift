import XCTest
@testable import CCTranslateSupport

final class TranslationProtocolTests: XCTestCase {
    private let storage = ["config_load", "config_save", "history_load", "history_add", "history_clear"] +
        DictionaryRequest.operations.sorted()
    private var ready: [String: JSONValue] {
        [
            "protocol": .integer(1),
            "capabilities": .array((storage + ["translate", "result_action"]).map(JSONValue.string)),
            "max_frame_bytes": .integer(65_536), "fixture": .bool(false), "backend": .string("native_appserver")
        ]
    }
    private var request: [String: JSONValue] {
        [
            "operation": .string("translate"), "text": .string("synthetic"), "app_language": .string("zh_CN"),
            "origin": .string("text"), "use_cache": .bool(true), "record_history": .bool(true)
        ]
    }
    private var completion: [String: JSONValue] {
        [
            "text": .string("final corrected translation"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("recorded"), "history_error": .null
        ]
    }
    private let operation: [String: JSONValue] = ["operation": .string("translate")]

    private func event(_ id: String, _ seq: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object([
            "v": .integer(1), "id": .string(id), "seq": .integer(seq), "type": .string(type),
            "payload": .object(payload)
        ]).encoded()
    }

    private func connected() throws -> ProtocolState {
        var state = ProtocolState(mode: .translation)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", ready))
        return state
    }

    private func pending(_ id: String = "t", started: Bool = true) throws -> ProtocolState {
        var state = try connected()
        try state.register(ClientMessage(id: id, type: "request", payload: request))
        _ = try state.receive(event(id, 0, "accepted", operation))
        if started { _ = try state.receive(event(id, 1, "started", operation)) }
        return state
    }

    private func assertInvalidCompletion(_ payload: [String: JSONValue],
                                         file: StaticString = #filePath, line: UInt = #line) throws {
        var state = try pending()
        XCTAssertThrowsError(try state.receive(event("t", 2, "completed", payload)), file: file, line: line)
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown, file: file, line: line)
        _ = try state.receive(event("t", 2, "completed", completion))
    }

    func testTranslationHandshakeRequiresExactModeBackendAndSevenCapabilities() throws {
        var state = ProtocolState(mode: .translation)
        XCTAssertTrue(state.isBusiness)
        XCTAssertThrowsError(try state.register(ClientMessage(id: "t", type: "request", payload: request)))
        XCTAssertThrowsError(try state.register(ClientMessage(id: "hello", type: "hello", payload: ["mode": .string("translation")])))
        try state.register(ClientMessage(id: "hello", type: "hello"))
        for key in ready.keys {
            var invalid = ready
            invalid.removeValue(forKey: key)
            XCTAssertThrowsError(try state.receive(event("hello", 0, "ready", invalid)))
        }
        let variants: [[String: JSONValue]] = [
            ["backend": .string("fixture")], ["backend": .bool(true)], ["fixture": .integer(0)],
            ["fixture": .bool(true)], ["protocol": .bool(true)], ["protocol": .integer(2)],
            ["max_frame_bytes": .integer(1_048_576)], ["extra": .null],
            ["capabilities": .array(storage.map(JSONValue.string))],
            ["capabilities": .array((storage + ["translate"]).map(JSONValue.string))],
            ["capabilities": .array((storage + ["config_load"]).map(JSONValue.string))],
            ["capabilities": .array((storage + ["fixture"]).map(JSONValue.string))],
            ["capabilities": .array((storage + ["translate", "translate"]).map(JSONValue.string))],
            ["capabilities": .array(storage.map(JSONValue.string) + [.bool(true)])]
        ]
        for variant in variants {
            let invalid = ready.merging(variant) { _, new in new }
            XCTAssertThrowsError(try state.receive(event("hello", 0, "ready", invalid)))
        }
        let valid = String(decoding: try event("hello", 0, "ready", ready), as: UTF8.self)
        for replacement in ["65536.0", "6.5536e4", "true"] {
            XCTAssertThrowsError(try state.receive(Data(valid.replacingOccurrences(
                of: "\"max_frame_bytes\":65536", with: "\"max_frame_bytes\":\(replacement)"
            ).utf8)))
        }
        _ = try state.receive(event("hello", 0, "ready", ready))
        XCTAssertTrue(state.ready)
        for mode in [ProtocolState.Mode.configuration, .diagnostic] {
            var other = ProtocolState(mode: mode)
            try other.register(ClientMessage(id: "hello", type: "hello"))
            XCTAssertThrowsError(try other.receive(event("hello", 0, "ready", ready)))
        }
    }

    func testTranslationRequestExactFieldsTypesAndUTF8Boundaries() throws {
        var state = try connected()
        for key in request.keys {
            var invalid = request
            invalid.removeValue(forKey: key)
            XCTAssertThrowsError(try state.register(ClientMessage(id: "invalid", type: "request", payload: invalid)))
        }
        let variants: [[String: JSONValue]] = [
            ["operation": .integer(1)], ["text": .null], ["text": .integer(1)], ["text": .string("")],
            ["text": .string(" \t\r\n\u{2003}")], ["text": .string("\u{001c}\u{001d}\u{001e}\u{001f}")],
            ["text": .string(String(repeating: "a", count: 8193))],
            ["text": .string(String(repeating: "\u{1f600}", count: 2048) + "a")],
            ["app_language": .string("zh")], ["app_language": .string("en_us")], ["app_language": .bool(true)],
            ["origin": .string("ocr")], ["origin": .string("clipboard")], ["origin": .null],
            ["use_cache": .integer(1)], ["use_cache": .number(1)], ["use_cache": .string("true")],
            ["record_history": .integer(0)], ["record_history": .null], ["record_history": .array([])],
            ["timeout": .integer(90)], ["provider": .string("codex")], ["fixture": .bool(false)],
            ["environment": .object([:])], ["unknown": .null]
        ]
        for variant in variants {
            let invalid = request.merging(variant) { _, new in new }
            XCTAssertThrowsError(try state.register(ClientMessage(id: "invalid", type: "request", payload: invalid)))
        }
        var index = 0
        for language in ["zh_CN", "en_US"] {
            for origin in ["text", "selection"] {
                for enabled in [true, false] {
                    var valid = request
                    valid["text"] = .string(String(repeating: "\u{1f600}", count: 2048))
                    valid["app_language"] = .string(language)
                    valid["origin"] = .string(origin)
                    valid["use_cache"] = .bool(enabled)
                    valid["record_history"] = .bool(enabled)
                    let message = ClientMessage(id: "valid_\(index)", type: "request", payload: valid)
                    try state.register(message)
                    let bytes = try message.encoded()
                    XCTAssertEqual(bytes.last, 10)
                    XCTAssertEqual(try JSONValue.parse(Data(bytes.dropLast())).object?["payload"], .object(valid))
                    index += 1
                }
            }
        }
        XCTAssertTrue(state.hasPendingTranslation)
        XCTAssertFalse(state.hasPendingConfiguration)
        XCTAssertFalse(state.hasPendingHistory)
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
    }

    func testTranslationRequestsRemainExclusiveToExplicitTranslationMode() throws {
        for mode in [ProtocolState.Mode.configuration, .diagnostic] {
            var state = ProtocolState(mode: mode)
            try state.register(ClientMessage(id: "hello", type: "hello"))
            let capabilities = mode == .diagnostic ? ["fixture", "runtime_probe"] : storage
            _ = try state.receive(event("hello", 0, "ready", [
                "protocol": .integer(1), "capabilities": .array(capabilities.map(JSONValue.string)),
                "max_frame_bytes": .integer(65_536), "fixture": .bool(mode == .diagnostic)
            ]))
            XCTAssertThrowsError(try state.register(ClientMessage(id: "t", type: "request", payload: request)))
            XCTAssertFalse(state.hasPendingTranslation)
        }
        var translation = try connected()
        for payload: [String: JSONValue] in [
            ["operation": .string("fixture"), "text": .string("synthetic")],
            ["operation": .string("runtime_probe")],
            ["operation": .string("config_load"), "backend": .string("native_appserver")]
        ] {
            XCTAssertThrowsError(try translation.register(ClientMessage(id: "invalid", type: "request", payload: payload)))
        }
    }

    func testTranslationStrictJSONEnvelopeUnicodeDepthNumbersAndDuplicateKeys() throws {
        var state = try pending()
        let good = String(decoding: try event("t", 2, "delta", [
            "text": .string("x"), "submitted": .bool(true)
        ]), as: UTF8.self)
        let invalid = [
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":2.0"),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":2e0"),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":true"),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":-1"),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":9007199254740992"),
            good.replacingOccurrences(of: "\"v\":1", with: "\"v\":1.0"),
            good.replacingOccurrences(of: "\"v\":1", with: "\"v\":true"),
            good.replacingOccurrences(of: "\"id\":\"t\"", with: "\"id\":\"missing\""),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":2,\"seq\":2"),
            good.replacingOccurrences(of: "\"submitted\":true", with: "\"submitted\":true,\"\\u0073ubmitted\":true"),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":\"\\ud800\""),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":\"\\udc00\""),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":NaN"),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":1e400"),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":\(String(repeating: "[", count: 17))0\(String(repeating: "]", count: 17))"),
            good.replacingOccurrences(of: "\"text\":\"x\"", with: "\"text\":\"x\",\"unknown\":null"),
            good.replacingOccurrences(of: "\"seq\":2", with: "\"seq\":2,\"unknown\":null")
        ]
        for wire in invalid { XCTAssertThrowsError(try state.receive(Data(wire.utf8))) }
        XCTAssertThrowsError(try state.receive(Data([0xff])))
        var framed = LineFramer()
        XCTAssertThrowsError(try framed.append(Data(String(repeating: " ", count: 65_536).utf8)))
        XCTAssertThrowsError(try state.receive(Data((good + String(repeating: " ", count: 65_536)).utf8)))
        _ = try state.receive(Data(good.replacingOccurrences(of: "\"text\":\"x\"",
                                                            with: "\"text\":\"\\uD83D\\uDE00\"").utf8))
        _ = try state.receive(event("t", 3, "completed", completion))
    }

    func testTranslationAcceptedStartedDeltaAndTerminalSequencesStayStrict() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "t", type: "request", payload: request))
        XCTAssertThrowsError(try state.receive(event("t", 0, "started", operation)))
        XCTAssertThrowsError(try state.receive(event("t", 0, "completed", completion)))
        XCTAssertThrowsError(try state.receive(event("t", 0, "accepted", ["operation": .string("fixture")])))
        XCTAssertThrowsError(try state.receive(event("t", 0, "accepted", operation.merging(["extra": .null]) { _, b in b })))
        _ = try state.receive(event("t", 0, "accepted", operation))
        XCTAssertThrowsError(try state.receive(event("t", 1, "delta", ["text": .string("early"), "submitted": .bool(true)])))
        XCTAssertThrowsError(try state.receive(event("t", 1, "completed", completion)))
        XCTAssertThrowsError(try state.receive(event("t", 1, "accepted", operation)))
        XCTAssertThrowsError(try state.receive(event("t", 1, "started", operation.merging(["extra": .null]) { _, b in b })))
        _ = try state.receive(event("t", 1, "started", operation))
        XCTAssertThrowsError(try state.receive(event("t", 2, "started", operation)))
        XCTAssertThrowsError(try state.receive(event("t", 3, "delta", ["text": .string("gap"), "submitted": .bool(true)])))
        _ = try state.receive(event("t", 2, "delta", ["text": .string("draft"), "submitted": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("t", 2, "delta", ["text": .string("duplicate"), "submitted": .bool(true)])))
        let result = try state.receive(event("t", 3, "completed", completion))
        XCTAssertEqual(result.payload["text"], completion["text"], "The native backend may correct streamed text")
        XCTAssertFalse(state.hasPendingTranslation)
        XCTAssertNil(state.pendingOutcomeUnknown)
        for type in ["delta", "completed", "cancelled", "failed"] {
            XCTAssertThrowsError(try state.receive(event("t", 4, type, completion)))
            XCTAssertThrowsError(try state.receive(event("t", 3, type, completion)))
        }
        XCTAssertThrowsError(try state.register(ClientMessage(id: "t", type: "request", payload: request)))
    }

    func testTranslationDeltaRequiresStrictSubmittedAndCompactJSONByteLimit() throws {
        XCTAssertEqual(try JSONValue.string("/").encoded(), Data("\"/\"".utf8))
        var state = try pending()
        let invalid: [[String: JSONValue]] = [
            [:], ["text": .string("x")], ["submitted": .bool(true)],
            ["text": .string(""), "submitted": .bool(true)], ["text": .integer(1), "submitted": .bool(true)],
            ["text": .string("x"), "submitted": .bool(false)], ["text": .string("x"), "submitted": .integer(1)],
            ["text": .string("x"), "submitted": .bool(true), "fixture": .bool(false)]
        ]
        for payload in invalid { XCTAssertThrowsError(try state.receive(event("t", 2, "delta", payload))) }
        for text in [
            String(repeating: "a", count: 4094),
            String(repeating: "/", count: 4094),
            String(repeating: "\"", count: 2047),
            String(repeating: "\\", count: 2047),
            String(repeating: "\u{1f600}", count: 1023) + "aa",
            String(repeating: "\0", count: 682) + "aa"
        ] {
            var boundary = try pending()
            XCTAssertEqual(try JSONValue.string(text).encoded().count, 4096)
            XCTAssertThrowsError(try boundary.receive(event("t", 2, "delta", [
                "text": .string(text + "a"), "submitted": .bool(true)
            ])))
            _ = try boundary.receive(event("t", 2, "delta", ["text": .string(text), "submitted": .bool(true)]))
        }
        _ = try state.receive(event("t", 2, "delta", ["text": .string(" \n"), "submitted": .bool(true)]))
    }

    func testTranslationDeltaCumulativeBudgetUsesCombinedEscapedText() throws {
        var state = try pending()
        for seq in 2...6 {
            _ = try state.receive(event("t", Int64(seq), "delta", [
                "text": .string(String(repeating: "\"", count: 2047)), "submitted": .bool(true)
            ]))
        }
        _ = try state.receive(event("t", 7, "delta", [
            "text": .string(String(repeating: "\"", count: 1764)), "submitted": .bool(true)
        ]))
        XCTAssertEqual(try JSONValue.string(String(repeating: "\"", count: 11999)).encoded().count, 24_000)
        XCTAssertThrowsError(try state.receive(event("t", 8, "delta", ["text": .string("a"), "submitted": .bool(true)])))
        _ = try state.receive(event("t", 8, "completed", completion))
    }

    func testTranslationWireBudgetCountsActualWhitespaceEnvelopeSequenceAndLF() throws {
        let id = String(repeating: "t", count: 64)
        var state = try pending(id)
        var used = try event(id, 0, "accepted", operation).count + 1
        used += try event(id, 1, "started", operation).count + 1
        for seq in 2...16 {
            var frame = try event(id, Int64(seq), "delta", ["text": .string("x"), "submitted": .bool(true)])
            frame.append(Data(repeating: 32, count: 65_535 - frame.count))
            _ = try state.receive(frame)
            used += frame.count + 1
        }
        var terminal = try event(id, 17, "completed", completion)
        let padding = 1_048_576 - used - terminal.count - 1
        XCTAssertGreaterThan(padding, 0)
        terminal.append(Data(repeating: 32, count: padding))
        XCTAssertLessThan(terminal.count, 65_535)
        var tooLarge = terminal
        tooLarge.append(32)
        XCTAssertThrowsError(try state.receive(tooLarge))
        _ = try state.receive(terminal)
        XCTAssertFalse(state.hasPendingTranslation)
    }

    func testTranslationCompletionExactFieldsEnumsBooleansAndHistoryErrors() throws {
        for key in completion.keys {
            var invalid = completion
            invalid.removeValue(forKey: key)
            try assertInvalidCompletion(invalid)
        }
        let variants: [[String: JSONValue]] = [
            ["text": .string("")], ["text": .string(" \r\n\u{2003}")],
            ["text": .string("\u{001c}\u{001d}\u{001e}\u{001f}")], ["text": .bool(true)],
            ["submitted": .integer(1)], ["submitted": .string("true")],
            ["cached": .integer(0)], ["cached": .null], ["summarize": .integer(0)], ["summarize": .null],
            ["kind": .string("ocr")], ["kind": .string("dictionary")], ["kind": .bool(false)],
            ["target_lang": .string("zh_CN")], ["target_lang": .string("")], ["target_lang": .bool(false)],
            ["history": .string("pending")], ["history": .bool(true)],
            ["history_error": .string("history_io_failed")],
            ["history": .string("failed"), "history_error": .string("provider_failed")],
            ["history": .string("failed"), "history_error": .string("unknown_error")],
            ["history": .string("failed"), "history_error": .integer(1)],
            ["cached": .bool(true)],
            ["cached": .bool(true), "submitted": .bool(false), "history": .string("disabled")],
            ["cached": .bool(true), "history": .string("unchanged")],
            ["fixture": .bool(false)], ["usage": .object([:])]
        ]
        for variant in variants { try assertInvalidCompletion(completion.merging(variant) { _, new in new }) }
        for kind in ["text", "dict", "code"] {
            for language: JSONValue in [.null] + ["zh", "en", "ja", "ko", "fr", "de", "es"].map(JSONValue.string) {
                var state = try pending()
                var payload = completion
                payload["kind"] = .string(kind)
                payload["target_lang"] = language
                payload["summarize"] = .bool(true)
                _ = try state.receive(event("t", 2, "completed", payload))
            }
        }
        for history in ["recorded", "disabled", "unchanged"] {
            var state = try pending()
            var payload = completion
            payload["history"] = .string(history)
            payload["submitted"] = .bool(false)
            payload["cached"] = .bool(history == "unchanged")
            _ = try state.receive(event("t", 2, "completed", payload))
        }
        for code in TranslationDocument.storageFailureCodes {
            var state = try pending()
            var payload = completion
            payload["history"] = .string("failed")
            payload["history_error"] = .string(code)
            _ = try state.receive(event("t", 2, "completed", payload))
        }
        var missingHistoryError = try pending()
        var historyFailed = completion
        historyFailed["history"] = .string("failed")
        XCTAssertEqual(historyFailed["history_error"], .null)
        XCTAssertThrowsError(try missingHistoryError.receive(event("t", 2, "completed", historyFailed))) {
            XCTAssertEqual($0 as? ProbeError, .invalidPayload)
        }
        XCTAssertEqual(missingHistoryError.pendingOutcomeUnknown, .translationOutcomeUnknown)
    }

    func testTranslationCompletionTextUsesCompactJSONBytesNotCharacterCount() throws {
        for text in [
            String(repeating: "a", count: 23_998),
            String(repeating: "/", count: 23_998),
            String(repeating: "\"", count: 11_999),
            String(repeating: "\u{4e2d}", count: 7999) + "a",
            String(repeating: "\u{1f600}", count: 5999) + "aa"
        ] {
            XCTAssertEqual(try JSONValue.string(text).encoded().count, 24_000)
            var valid = completion
            valid["text"] = .string(text)
            var invalid = valid
            invalid["text"] = .string(text + "a")
            try assertInvalidCompletion(invalid)
            var state = try pending()
            _ = try state.receive(event("t", 2, "completed", valid))
        }
    }

    func testVersionAndProtocolFailuresHaveFixedActionableDiagnostics() throws {
        let actions = [
            "provider_version_unsupported": "too old",
            "provider_version_unreadable": "could not be recognized",
            "provider_version_prerelease": "prereleases are not supported",
            "provider_protocol_error": "failed strict protocol validation"
        ]
        for (code, action) in actions {
            XCTAssertTrue(TranslationDocument.failureCodes.contains(code))
            var state = try pending()
            let result = try state.receive(event("t", 2, "failed", [
                "code": .string(code), "submitted": .bool(false)
            ]))
            XCTAssertEqual(result.safeFailureCode, code)
            XCTAssertTrue(result.safeFailureMessage.contains(action))
            XCTAssertTrue(result.safeFailureMessage.contains("No fallback or automatic retry."))
            if code != "provider_protocol_error" {
                XCTAssertTrue(result.safeFailureMessage.contains("0.146.0"))
            }
            let extra = ServerEvent(id: "t", sequence: 2, type: "failed", payload: [
                "submitted": .bool(false),
                "code": .string(code), "detail": .string("/SYNTHETIC-PRIVATE/path token=SYNTHETIC-PRIVATE"),
                "stdout": .string("SYNTHETIC-PRIVATE"), "stderr": .string("SYNTHETIC-PRIVATE")
            ])
            XCTAssertEqual(extra.safeFailureMessage, result.safeFailureMessage)
            XCTAssertFalse(result.safeFailureMessage.contains("SYNTHETIC-PRIVATE"))
        }
        let unknown = ServerEvent(id: "t", sequence: 2, type: "failed", payload: [
            "code": .string("/SYNTHETIC-PRIVATE/path token=SYNTHETIC-PRIVATE")
        ])
        XCTAssertEqual(unknown.safeFailureMessage,
                       "Helper request failed: helper_failed. No fallback or automatic retry.")
    }

    func testProtocolFailureAfterPossibleSubmissionDoesNotSuggestReplay() {
        for submitted: JSONValue? in [.bool(true), nil] {
            var payload: [String: JSONValue] = ["code": .string("provider_protocol_error")]
            if let submitted = submitted { payload["submitted"] = submitted }
            let failed = ServerEvent(id: "t", sequence: 2, type: "failed", payload: payload)
            XCTAssertTrue(failed.safeFailureMessage.contains("Do not replay"))
            XCTAssertTrue(failed.safeFailureMessage.contains("outcome is unknown"))
            XCTAssertTrue(failed.safeFailureMessage.contains("provider_protocol_error"))
            XCTAssertFalse(failed.safeFailureMessage.contains("before model submission"))
        }
    }

    func testTranslationFailureWhitelistSubmissionAndDeterminateWorkerStartFailure() throws {
        for code in TranslationDocument.failureCodes.union(TranslationDocument.storageFailureCodes) {
            for submitted in [false, true] {
                var state = try pending()
                let result = try state.receive(event("t", 2, "failed", [
                    "code": .string(code), "submitted": .bool(submitted)
                ]))
                XCTAssertEqual(result.safeFailureCode, code)
                XCTAssertNil(state.pendingOutcomeUnknown)
            }
        }
        var started = try pending()
        for payload: [String: JSONValue] in [
            ["code": .string("provider_failed")],
            ["code": .string("provider_failed"), "submitted": .integer(1)],
            ["code": .string("provider_failed"), "submitted": .bool(true), "detail": .string("private")],
            ["code": .string("unknown_error"), "submitted": .bool(false)],
            ["code": .string("worker_start_failed"), "submitted": .bool(false)],
            ["code": .string("busy"), "submitted": .bool(false)]
        ] { XCTAssertThrowsError(try started.receive(event("t", 2, "failed", payload))) }
        for operation in ["translate"] + storage {
            var state = try connected()
            let payload: [String: JSONValue]
            switch operation {
            case "translate": payload = request
            case "config_save": payload = ["operation": .string(operation), "config": .object([:])]
            case "history_load": payload = ["operation": .string(operation), "page_size": .integer(1), "cursor": .null]
            case "history_add":
                payload = ["operation": .string(operation), "input": .string("x"), "output": .string("y"),
                           "is_dict": .bool(false), "is_code": .bool(false), "kind": .string("text"),
                           "sig": .string(""), "limit": .integer(1)]
            default: payload = ["operation": .string(operation)]
            }
            try state.register(ClientMessage(id: "work", type: "request", payload: payload))
            let failure: [String: JSONValue] = ["code": .string("worker_start_failed")]
            XCTAssertThrowsError(try state.receive(event("work", 0, "failed", failure)))
            _ = try state.receive(event("work", 0, "accepted", ["operation": .string(operation)]))
            XCTAssertThrowsError(try state.receive(event("work", 1, "failed",
                                                         failure.merging(["submitted": .bool(false)]) { _, b in b })))
            let result = try state.receive(event("work", 1, "failed", failure))
            XCTAssertEqual(result.safeFailureCode, "worker_start_failed")
            XCTAssertFalse(state.hasPendingResponses)
            XCTAssertNil(state.pendingOutcomeUnknown)
            XCTAssertThrowsError(try state.receive(event("work", 2, "started", ["operation": .string(operation)])))
        }
        var queued = try pending(started: false)
        XCTAssertThrowsError(try queued.receive(event("t", 1, "failed", ["code": .string("provider_failed")])))
        var validating = try connected()
        try validating.register(ClientMessage(id: "t", type: "request", payload: request))
        XCTAssertThrowsError(try validating.receive(event("t", 0, "failed", [
            "code": .string("invalid_translation"), "submitted": .bool(false)
        ])))
        _ = try validating.receive(event("t", 0, "failed", ["code": .string("invalid_translation")]))
        XCTAssertNil(validating.pendingOutcomeUnknown)
    }

    func testTranslationDeltaRequiresSubmittedTrueForEveryTerminalKind() throws {
        for type in ["completed", "failed", "cancelled"] {
            var state = try pending()
            _ = try state.receive(event("t", 2, "delta", ["text": .string("x"), "submitted": .bool(true)]))
            var payload: [String: JSONValue]
            switch type {
            case "completed": payload = completion
            case "failed": payload = ["code": .string("provider_failed")]
            default: payload = [:]
            }
            payload["submitted"] = .bool(false)
            XCTAssertThrowsError(try state.receive(event("t", 3, type, payload)))
            payload["submitted"] = .bool(true)
            _ = try state.receive(event("t", 3, type, payload))
        }
    }

    func testTranslationCancellationAcknowledgementAllowsInFlightDeltaAndTerminalRaces() throws {
        for type in ["completed", "failed", "cancelled"] {
            for acknowledgementFirst in [true, false] {
                var state = try pending()
                try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("t")]))
                let ack = try event("cancel", 0, "completed", ["cancel_requested": .bool(true)])
                if acknowledgementFirst {
                    _ = try state.receive(ack)
                    XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
                }
                _ = try state.receive(event("t", 2, "delta", ["text": .string("in flight"), "submitted": .bool(true)]))
                let payload: [String: JSONValue] = type == "completed" ? completion :
                    (type == "failed" ? ["code": .string("provider_failed"), "submitted": .bool(true)] :
                        ["submitted": .bool(true)])
                _ = try state.receive(event("t", 3, type, payload))
                if !acknowledgementFirst { _ = try state.receive(ack) }
                XCTAssertFalse(state.hasPendingResponses)
                XCTAssertNil(state.pendingOutcomeUnknown)
                try state.register(ClientMessage(id: "late", type: "cancel", payload: ["request_id": .string("t")]))
                XCTAssertThrowsError(try state.receive(event("late", 0, "completed", ["cancel_requested": .bool(true)])))
                _ = try state.receive(event("late", 0, "completed", ["cancel_requested": .bool(false)]))
            }
        }
        var committing = try pending()
        try committing.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("t")]))
        _ = try committing.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(false)]))
        _ = try committing.receive(event("t", 2, "completed", completion))
    }

    func testTranslationQueuedAndStartedCancellationHaveDistinctStrictPayloads() throws {
        var queued = try pending(started: false)
        try queued.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("t")]))
        for payload: [String: JSONValue] in [
            [:], ["cancel_requested": .integer(1)], ["cancel_requested": .bool(true), "extra": .null]
        ] { XCTAssertThrowsError(try queued.receive(event("cancel", 0, "completed", payload))) }
        _ = try queued.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertThrowsError(try queued.receive(event("t", 1, "started", operation)))
        XCTAssertThrowsError(try queued.receive(event("t", 1, "cancelled", ["submitted": .bool(false)])))
        _ = try queued.receive(event("t", 1, "cancelled"))
        XCTAssertNil(queued.pendingOutcomeUnknown)
        for submitted in [true, false] {
            var started = try pending()
            for payload: [String: JSONValue] in [
                [:], ["submitted": .integer(1)], ["submitted": .bool(true), "extra": .null]
            ] { XCTAssertThrowsError(try started.receive(event("t", 2, "cancelled", payload))) }
            _ = try started.receive(event("t", 2, "cancelled", ["submitted": .bool(submitted)]))
            XCTAssertNil(started.pendingOutcomeUnknown)
        }
        var unknown = try connected()
        try unknown.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("unknown")]))
        XCTAssertThrowsError(try unknown.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)])))
        _ = try unknown.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(false)]))
        var starting = try pending(started: false)
        try starting.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("t")]))
        _ = try starting.receive(event("t", 1, "started", operation))
        _ = try starting.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        _ = try starting.receive(event("t", 2, "cancelled", ["submitted": .bool(false)]))
    }

    func testTranslationStorageRemainsFIFOWhileTranslationsStartIndependently() throws {
        var state = try connected()
        let requests: [(String, [String: JSONValue])] = [
            ("t1", request), ("config", ["operation": .string("config_load")]),
            ("t2", request), ("history", ["operation": .string("history_clear")]),
            ("save", ["operation": .string("config_save"), "config": .object([:])])
        ]
        for (id, payload) in requests {
            try state.register(ClientMessage(id: id, type: "request", payload: payload))
            _ = try state.receive(event(id, 0, "accepted", ["operation": payload["operation"]!]))
        }
        _ = try state.receive(event("t2", 1, "started", operation))
        _ = try state.receive(event("t1", 1, "started", operation))
        XCTAssertThrowsError(try state.receive(event("history", 1, "started", ["operation": .string("history_clear")])))
        _ = try state.receive(event("config", 1, "started", ["operation": .string("config_load")]))
        XCTAssertThrowsError(try state.receive(event("save", 1, "started", ["operation": .string("config_save")])))
        _ = try state.receive(event("config", 2, "completed", ["config": .object([:])]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        _ = try state.receive(event("history", 1, "started", ["operation": .string("history_clear")]))
        try state.register(ClientMessage(id: "cancel_storage", type: "cancel", payload: ["request_id": .string("history")]))
        XCTAssertThrowsError(try state.receive(event("cancel_storage", 0, "completed", ["cancel_requested": .bool(true)])))
        _ = try state.receive(event("cancel_storage", 0, "completed", ["cancel_requested": .bool(false)]))
        _ = try state.receive(event("history", 2, "completed", [
            "cleared": .bool(true), "revision": .string(String(repeating: "a", count: 64))
        ]))
        _ = try state.receive(event("save", 1, "started", ["operation": .string("config_save")]))
        _ = try state.receive(event("save", 2, "completed", ["saved": .bool(true)]))
        _ = try state.receive(event("t2", 2, "completed", completion))
        _ = try state.receive(event("t1", 2, "completed", completion))
        XCTAssertFalse(state.hasPendingResponses)
    }

    func testTranslationShutdownFailureIsExactAndModeSpecific() throws {
        for mode in [ProtocolState.Mode.configuration, .translation] {
            for code in ["state_io_failed", "provider_cleanup_failed"] {
                var state = ProtocolState(mode: mode)
                try state.register(ClientMessage(id: "hello", type: "hello"))
                var handshake = ready
                if mode == .configuration {
                    handshake.removeValue(forKey: "backend")
                    handshake["capabilities"] = .array(storage.map(JSONValue.string))
                }
                _ = try state.receive(event("hello", 0, "ready", handshake))
                try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
                let payload: [String: JSONValue] = ["code": .string(code)]
                for invalid: [String: JSONValue] in [
                    [:], ["code": .integer(1)],
                    ["code": .string("provider_failed")], ["code": .string("internal_error")],
                    ["code": .string("unknown_error")], ["code": .string("config_io_failed")],
                    ["code": .string("worker_start_failed")],
                    payload.merging(["submitted": .bool(false)]) { _, b in b },
                    payload.merging(["detail": .string("private")]) { _, b in b }
                ] {
                    XCTAssertThrowsError(try state.receive(event("shutdown", 0, "failed", invalid)))
                }
                XCTAssertThrowsError(try state.receive(event("shutdown", 1, "failed", payload)))
                let wire = String(decoding: try event("shutdown", 0, "failed", payload), as: UTF8.self)
                XCTAssertThrowsError(try state.receive(Data(wire.replacingOccurrences(
                    of: "\"seq\":0", with: "\"seq\":0.0"
                ).utf8)))
                XCTAssertThrowsError(try state.receive(Data(wire.replacingOccurrences(
                    of: "\"code\":\"\(code)\"", with: "\"code\":\"\(code)\",\"code\":\"\(code)\""
                ).utf8)))
                if mode == .configuration && code == "provider_cleanup_failed" {
                    XCTAssertThrowsError(try state.receive(event("shutdown", 0, "failed", payload)))
                    XCTAssertTrue(state.hasPendingResponses)
                } else {
                    let result = try state.receive(event("shutdown", 0, "failed", payload))
                    XCTAssertTrue(result.isTerminal)
                    XCTAssertEqual(result.safeFailureCode, code)
                    XCTAssertFalse(state.hasPendingResponses)
                    XCTAssertNil(state.pendingOutcomeUnknown)
                    XCTAssertThrowsError(try state.receive(event("shutdown", 1, "completed")))
                }
            }
        }
    }

    func testTranslationReservedInternalErrorNeverInventsRequestSubmissionOrTerminal() throws {
        var state = try pending()
        try state.register(ClientMessage(id: "config", type: "request", payload: ["operation": .string("config_load")]))
        for invalid: [String: JSONValue] in [
            ["code": .string("internal_error"), "submitted": .bool(false)],
            ["code": .string("internal_error"), "detail": .string("private")],
            ["code": .integer(1)], ["code": .string("unknown_error")]
        ] {
            XCTAssertThrowsError(try state.receive(event("protocol", 0, "failed", invalid))) {
                XCTAssertEqual($0 as? ProbeError, .invalidEnvelope)
            }
        }
        XCTAssertThrowsError(try state.receive(event("protocol", 1, "failed", ["code": .string("internal_error")]))) {
            XCTAssertEqual($0 as? ProbeError, .invalidEnvelope)
        }
        XCTAssertThrowsError(try state.receive(event("protocol", 0, "failed", ["code": .string("internal_error")]))) {
            XCTAssertEqual($0 as? ProbeError, .helperProtocolError)
        }
        XCTAssertTrue(state.hasPendingTranslation)
        XCTAssertTrue(state.hasPendingConfiguration)
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
    }

    func testTranslationBootstrapFailuresUnknownPriorityAndShutdownDrain() throws {
        for code in ["translation_unavailable", "config_in_use", "history_in_use", "state_io_failed"] {
            var state = ProtocolState(mode: .translation)
            try state.register(ClientMessage(id: "hello", type: "hello"))
            let failure = try state.receive(event("hello", 0, "failed", ["code": .string(code)]))
            XCTAssertEqual(failure.safeFailureCode, code)
            XCTAssertFalse(state.ready)
        }
        var invalidBootstrap = ProtocolState(mode: .translation)
        try invalidBootstrap.register(ClientMessage(id: "hello", type: "hello"))
        for code in ["provider_failed", "unknown_error", "worker_start_failed"] {
            XCTAssertThrowsError(try invalidBootstrap.receive(event("hello", 0, "failed", ["code": .string(code)])))
        }
        var state = try pending()
        try state.register(ClientMessage(id: "config", type: "request", payload: ["operation": .string("config_load")]))
        try state.register(ClientMessage(id: "history", type: "request", payload: ["operation": .string("history_clear")]))
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        _ = try state.receive(event("t", 2, "cancelled", ["submitted": .bool(true)]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .configurationOutcomeUnknown)
        _ = try state.receive(event("config", 0, "failed", ["code": .string("busy")]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .historyOutcomeUnknown)
        _ = try state.receive(event("history", 0, "failed", ["code": .string("busy")]))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertNil(state.pendingOutcomeUnknown)
        XCTAssertFalse(state.hasPendingResponses)
        XCTAssertThrowsError(try state.register(ClientMessage(id: "new", type: "request", payload: request)))
    }
}
