import XCTest
@testable import CCTranslateSupport

final class ProtocolTests: XCTestCase {
    private let ready: [String: JSONValue] = [
        "protocol": .integer(1), "capabilities": .array([.string("fixture"), .string("runtime_probe")]),
        "max_frame_bytes": .integer(65_536), "fixture": .bool(true)
    ]

    private func event(_ id: String, _ seq: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object([
            "v": .integer(1), "id": .string(id), "seq": .integer(seq),
            "type": .string(type), "payload": .object(payload)
        ]).encoded()
    }

    private func connected() throws -> ProtocolState {
        var state = ProtocolState()
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", ready))
        return state
    }

    private func request(_ id: String, text: String = "synthetic") -> ClientMessage {
        ClientMessage(id: id, type: "request", payload: [
            "operation": .string("fixture"), "text": .string(text)
        ])
    }

    private func runtimeReport(https: Bool = false) -> [String: JSONValue] {
        [
            "python": .object([
                "version": .string("3.13.3"), "platform": .string("darwin"), "machine": .string("arm64"),
                "isolated": .bool(true), "bytecode_disabled": .bool(true), "bundle_runtime": .bool(true)
            ]),
            "sqlite": .object([
                "status": .string("passed"), "read_write": .bool(true), "version": .string("3.0")
            ]),
            "ssl": .object([
                "status": .string("passed"), "version": .string("OpenSSL synthetic"),
                "certificate_validation": .bool(true), "ca_source": .string("bundle")
            ]),
            "https": https ? .object([
                "status": .string("passed"), "host": .string("www.python.org"),
                "certificate_verified": .bool(true)
            ]) : .object(["status": .string("not_run")])
        ]
    }

    func testFramingAcrossEveryByteIncludingUTF8() throws {
        let wire = Data("{\"text\":\"\\u4e2d\\u6587\"}\n{}\n".utf8)
        var framer = LineFramer()
        var frames: [Data] = []
        for byte in wire { frames += try framer.append(Data([byte])) }
        try framer.finish()
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(try JSONValue.parse(frames[0]), .object(["text": .string("\u{4e2d}\u{6587}")]))
        var utf8Framer = LineFramer()
        let utf8Wire = Data("{\"text\":\"\u{4e2d}\"}\n".utf8)
        var utf8Frames: [Data] = []
        for byte in utf8Wire { utf8Frames += try utf8Framer.append(Data([byte])) }
        XCTAssertEqual(utf8Frames.count, 1)
        XCTAssertNoThrow(try JSONValue.parse(utf8Frames[0]))
    }

    func testFrameLimitIncludesLF() throws {
        var framer = LineFramer()
        XCTAssertTrue(try framer.append(Data(repeating: 32, count: 65_535)).isEmpty)
        XCTAssertEqual(try framer.append(Data([10])).first?.count, 65_535)
        try framer.finish()
        var oversized = LineFramer()
        XCTAssertThrowsError(try oversized.append(Data(repeating: 32, count: 65_536)))
        var incomplete = LineFramer()
        _ = try incomplete.append(Data("{}".utf8))
        XCTAssertThrowsError(try incomplete.finish())
    }

    func testRejectsAmbiguousJSON() {
        let bad = [
            "", " ", "\u{feff}{}", "{\"x\":1,\"x\":2}", "{\"x\":1,\"\\u0078\":2}",
            "{\"a\":{\"b\":1,\"b\":2}}", "[NaN]", "[Infinity]", "[1e400]",
            "[01]", "[1.]", "[+1]", "[1,]", "{\"a\":1,}", "{}{}",
            "\"unescaped\nnewline\"", "\"\\ud800\"", "\"\\udc00\"",
            "\"\\ud800x\"", "\"\\ud800\\u0041\"", "\"\\udc00\\ud800\"",
            "\"\\uzzzz\"", "\"\\u123\"", "\"\\x41\"",
            String(repeating: "[", count: 16) + "0" + String(repeating: "]", count: 16)
        ]
        for value in bad {
            XCTAssertThrowsError(try JSONValue.parse(Data(value.utf8)), value)
        }
        XCTAssertThrowsError(try JSONValue.parse(Data([0xff])))
        XCTAssertNoThrow(try JSONValue.parse(Data("{\"ok\":[true,false,null,-2,1.25,1e2]}".utf8)))
        XCTAssertNoThrow(try JSONValue.parse(Data(
            (String(repeating: "[", count: 15) + "0" + String(repeating: "]", count: 15)).utf8
        )))
    }

    func testUnicodeEscapesAreValidatedBeforeFoundationDecoding() throws {
        XCTAssertEqual(try JSONValue.parse(Data("\"\\uD83D\\uDE00\"".utf8)), .string("\u{1f600}"))
        XCTAssertEqual(try JSONValue.parse(Data("\"\\u0000\"".utf8)), .string("\u{0}"))
        XCTAssertEqual(try JSONValue.parse(Data("\"\\\"\\\\\\/\\b\\f\\n\\r\\t\"".utf8)),
                       .string("\"\\/\u{8}\u{c}\n\r\t"))
        let duplicate = "{\"\u{1f600}\":1,\"\\uD83D\\uDE00\":2}"
        XCTAssertThrowsError(try JSONValue.parse(Data(duplicate.utf8)))
        XCTAssertThrowsError(try JSONValue.parse(Data([34, 0xed, 0xa0, 0x80, 34])))
    }

    func testHandshakeRequiredAndStrictEnvelope() throws {
        var state = ProtocolState()
        XCTAssertThrowsError(try state.register(request("before_hello")))
        try state.register(ClientMessage(id: "hello", type: "hello"))
        XCTAssertThrowsError(try state.register(request("before_ready")))
        for json in [
            #"{"v":true,"id":"hello","seq":0,"type":"ready","payload":{}}"#,
            #"{"v":1.0,"id":"hello","seq":0,"type":"ready","payload":{}}"#,
            #"{"v":1,"id":"hello","seq":false,"type":"ready","payload":{}}"#,
            #"{"v":1,"id":"hello","seq":0,"type":"ready","payload":{},"extra":1}"#
        ] { XCTAssertThrowsError(try state.receive(Data(json.utf8))) }
        _ = try state.receive(event("hello", 0, "ready", ready))
        XCTAssertTrue(state.ready)
        XCTAssertThrowsError(try state.register(ClientMessage(id: "another", type: "hello")))
    }

    func testFixtureAcceptedDeltaTerminalAndNoReuse() throws {
        var state = try connected()
        try state.register(request("r1"))
        _ = try state.receive(event("r1", 0, "accepted", ["operation": .string("fixture")]))
        let payload: [String: JSONValue] = ["text": .string("SYNTHETIC fixture"), "fixture": .bool(true)]
        _ = try state.receive(event("r1", 1, "delta", payload))
        let terminal = try state.receive(event("r1", 2, "completed", payload))
        XCTAssertTrue(terminal.isTerminal)
        XCTAssertThrowsError(try state.receive(event("r1", 3, "completed", payload)))
        XCTAssertThrowsError(try state.register(request("r1")))
        XCTAssertThrowsError(try state.receive(event("unknown", 0, "cancelled")))
    }

    func testRejectsWrongOrderAndMissingFixtureLabel() throws {
        var state = try connected()
        try state.register(request("r"))
        let payload: [String: JSONValue] = ["text": .string("test"), "fixture": .bool(true)]
        XCTAssertThrowsError(try state.receive(event("r", 0, "completed", payload)))
        XCTAssertThrowsError(try state.receive(event("r", 1, "accepted", ["operation": .string("fixture")])))
        _ = try state.receive(event("r", 0, "accepted", ["operation": .string("fixture")]))
        XCTAssertThrowsError(try state.receive(event("r", 1, "delta", ["text": .string("not labelled")])))
        XCTAssertThrowsError(try state.receive(event("r", 1, "accepted", ["operation": .string("fixture")])))
    }

    func testOldEventsStillRequireCorrectSequence() throws {
        var state = try connected()
        var latest = LatestRequest()
        try state.register(request("old"))
        try state.register(request("new"))
        latest.select("new")
        let old = try state.receive(event("old", 0, "accepted", ["operation": .string("fixture")]))
        XCTAssertFalse(latest.accepts(old))
        XCTAssertThrowsError(try state.receive(event("old", 2, "cancelled")))
        let current = try state.receive(event("new", 0, "accepted", ["operation": .string("fixture")]))
        XCTAssertTrue(latest.accepts(current))
        latest.select(nil)
        XCTAssertFalse(latest.accepts(current))
    }

    func testCancelControlIndependentFromOriginal() throws {
        var state = try connected()
        try state.register(request("r"))
        _ = try state.receive(event("r", 0, "accepted", ["operation": .string("fixture")]))
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("r")]))
        let cancelled = try state.receive(event("r", 1, "cancelled"))
        XCTAssertEqual(cancelled.type, "cancelled")
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        try state.register(ClientMessage(id: "late_cancel", type: "cancel", payload: ["request_id": .string("r")]))
        _ = try state.receive(event("late_cancel", 0, "completed", ["cancel_requested": .bool(false)]))
    }

    func testRuntimeBusyShutdownAndConnectionFailure() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "runtime", type: "request", payload: [
            "operation": .string("runtime_probe"), "https": .bool(false)
        ]))
        _ = try state.receive(event("runtime", 0, "accepted", ["operation": .string("runtime_probe")]))
        _ = try state.receive(event("runtime", 1, "completed", runtimeReport()))
        try state.register(request("busy"))
        _ = try state.receive(event("busy", 0, "failed", ["code": .string("busy")]))
        XCTAssertThrowsError(try state.receive(event("protocol", 0, "failed", ["code": .string("invalid_frame")])))
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertThrowsError(try state.register(request("after_shutdown")))
    }

    func testIDAndPayloadBounds() throws {
        var state = try connected()
        for id in ["", "protocol", "space here", "\u{4e2d}", String(repeating: "x", count: 65)] {
            XCTAssertThrowsError(try state.register(request(id)))
        }
        try state.register(request("utf8limit", text: String(repeating: "\u{00e9}", count: 4096)))
        XCTAssertThrowsError(try state.register(request("too_big", text: String(repeating: "\u{00e9}", count: 4097))))
        XCTAssertThrowsError(try state.register(ClientMessage(id: "bool_delay", type: "request", payload: [
            "operation": .string("fixture"), "text": .string("test"), "delay_ms": .bool(true)
        ])))
        XCTAssertThrowsError(try state.register(ClientMessage(id: "bad_https", type: "request", payload: [
            "operation": .string("runtime_probe"), "https": .integer(1)
        ])))
        var full = try connected()
        for index in 0..<4095 { try full.register(request("r\(index)")) }
        XCTAssertEqual(full.registeredCount, 4096)
        XCTAssertThrowsError(try full.register(request("overflow"))) {
            XCTAssertEqual($0 as? ProbeError, .idLimit)
        }
    }

    func testRuntimeRejectsUnknownFieldsAndUnrequestedNetwork() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "runtime", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        _ = try state.receive(event("runtime", 0, "accepted", ["operation": .string("runtime_probe")]))
        var extra = runtimeReport()
        extra["unexpected"] = .object([:])
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", extra)))
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", runtimeReport(https: true))))
        var empty = runtimeReport()
        empty["sqlite"] = .object([:])
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", empty)))
        _ = try state.receive(event("runtime", 1, "completed", runtimeReport()))
    }

    func testPythonMetadataIsTypedEvidenceNotAnHTTPSVerdict() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "runtime", type: "request", payload: [
            "operation": .string("runtime_probe"), "https": .bool(false)
        ]))
        _ = try state.receive(event("runtime", 0, "accepted", ["operation": .string("runtime_probe")]))
        var report = runtimeReport()
        report["python"] = .object([
            "version": .string("3.13.3"), "platform": .string("darwin"), "machine": .string("arm64"),
            "isolated": .integer(1), "bytecode_disabled": .bool(true), "bundle_runtime": .bool(true)
        ])
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", report)))
        report["python"] = .object([
            "version": .string("3.13.3"), "platform": .string("darwin"), "machine": .string("arm64"),
            "isolated": .bool(false), "bytecode_disabled": .bool(false), "bundle_runtime": .bool(false)
        ])
        let result = try state.receive(event("runtime", 1, "completed", report))
        XCTAssertEqual(result.payload["python"]?.object?["bundle_runtime"], .bool(false))
        XCTAssertEqual(result.payload["https"]?.object?["status"], .string("not_run"))
    }

    func testHTTPSRequiresBundledCAAndFixedHost() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "https", type: "request", payload: [
            "operation": .string("runtime_probe"), "https": .bool(true)
        ]))
        _ = try state.receive(event("https", 0, "accepted", ["operation": .string("runtime_probe")]))
        XCTAssertThrowsError(try state.receive(event("https", 1, "completed", runtimeReport())))
        var wrongHost = runtimeReport(https: true)
        wrongHost["https"] = .object([
            "status": .string("passed"), "host": .string("not-the-fixed-endpoint.example"),
            "certificate_verified": .bool(true)
        ])
        XCTAssertThrowsError(try state.receive(event("https", 1, "completed", wrongHost)))
        var systemCA = runtimeReport(https: true)
        systemCA["ssl"] = .object([
            "status": .string("passed"), "version": .string("OpenSSL synthetic"),
            "certificate_validation": .bool(true), "ca_source": .string("system")
        ])
        XCTAssertThrowsError(try state.receive(event("https", 1, "completed", systemCA)))
        _ = try state.receive(event("https", 1, "completed", runtimeReport(https: true)))
    }

    func testFailureDiagnosticsAreLocalConstantsAndBusyAfterCancelIsValid() throws {
        var state = try connected()
        try state.register(request("old"))
        _ = try state.receive(event("old", 0, "accepted", ["operation": .string("fixture")]))
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("old")]))
        _ = try state.receive(event("old", 1, "cancelled"))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        try state.register(request("next"))
        let busy = try state.receive(event("next", 0, "failed", ["code": .string("busy")]))
        XCTAssertEqual(busy.safeFailureCode, "busy")
        try state.register(request("unknown_error"))
        let hidden = try state.receive(event("unknown_error", 0, "failed", ["code": .string("private_user_value")]))
        XCTAssertEqual(hidden.safeFailureCode, "helper_failed")
        try state.register(ClientMessage(id: "https", type: "request", payload: [
            "operation": .string("runtime_probe"), "https": .bool(true)
        ]))
        _ = try state.receive(event("https", 0, "accepted", ["operation": .string("runtime_probe")]))
        let missingCA = try state.receive(event("https", 1, "failed", ["code": .string("bundle_ca_missing")]))
        XCTAssertEqual(missingCA.safeFailureCode, "bundle_ca_missing")
    }
}
