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

    private var configurationReady: [String: JSONValue] {
        [
            "protocol": .integer(1),
            "capabilities": .array([.string("config_load"), .string("config_save")]),
            "max_frame_bytes": .integer(65_536), "fixture": .bool(false)
        ]
    }

    private func configurationConnected() throws -> ProtocolState {
        var state = ProtocolState(mode: .configuration)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        _ = try state.receive(event("hello", 0, "ready", configurationReady))
        return state
    }

    private func configurationRequest(_ id: String, config: JSONValue? = nil) -> ClientMessage {
        if let config = config {
            return ClientMessage(id: id, type: "request", payload: [
                "operation": .string("config_save"), "config": config
            ])
        }
        return ClientMessage(id: id, type: "request", payload: ["operation": .string("config_load")])
    }

    func testConfigurationHandshakeModesAndEmptyHello() throws {
        for mode in [ProtocolState.Mode.diagnostic, .configuration] {
            var state = ProtocolState(mode: mode)
            let invalidHello: [[String: JSONValue]] = [
                ["mode": .string("configuration")], ["fixture": .bool(false)],
                ["config_home": .string("synthetic")], ["options": .object([:])]
            ]
            for payload in invalidHello {
                XCTAssertThrowsError(try state.register(ClientMessage(id: "hello", type: "hello", payload: payload)))
            }
            let hello = ClientMessage(id: "hello", type: "hello")
            let bytes = try hello.encoded()
            XCTAssertEqual(try JSONValue.parse(Data(bytes.dropLast())).object?["payload"], .object([:]))
            try state.register(hello)
            XCTAssertThrowsError(try state.receive(event("hello", 0, "ready",
                                                         mode == .diagnostic ? configurationReady : ready)))
            let valid = mode == .diagnostic ? ready : configurationReady
            for invalid in [
                ["fixture": JSONValue.integer(mode == .diagnostic ? 1 : 0)],
                ["protocol": .bool(true)], ["max_frame_bytes": .number(65_536)],
                ["capabilities": .array([.string("config_load"), .string("config_load")])],
                ["extra": .null]
            ] {
                var payload = valid
                payload.merge(invalid) { _, new in new }
                // Use raw decimal syntax because Foundation may serialize a whole Double as an integer.
                let wire: Data
                if invalid["max_frame_bytes"] == nil {
                    wire = try event("hello", 0, "ready", payload)
                } else {
                    wire = Data(String(decoding: try event("hello", 0, "ready", valid), as: UTF8.self)
                        .replacingOccurrences(of: "\"max_frame_bytes\":65536",
                                              with: "\"max_frame_bytes\":65536.0").utf8)
                }
                XCTAssertThrowsError(try state.receive(wire))
            }
            _ = try state.receive(event("hello", 0, "ready", valid))
            XCTAssertTrue(state.ready)
        }
    }

    func testConfigurationRequestsAreStrictAndModeExclusive() throws {
        var business = try configurationConnected()
        var diagnostic = try connected()
        XCTAssertThrowsError(try diagnostic.register(configurationRequest("load")))
        XCTAssertThrowsError(try diagnostic.register(configurationRequest("save", config: .object([:]))))
        try diagnostic.register(request("diagnostic"))
        _ = try diagnostic.receive(event("diagnostic", 0, "accepted", ["operation": .string("fixture")]))
        XCTAssertThrowsError(try diagnostic.receive(event("diagnostic", 1, "started", ["operation": .string("fixture")])))
        _ = try diagnostic.receive(event("diagnostic", 1, "completed", [
            "text": .string("synthetic"), "fixture": .bool(true)
        ]))
        XCTAssertThrowsError(try business.register(request("fixture")))
        XCTAssertThrowsError(try business.register(ClientMessage(id: "runtime", type: "request",
                                                                payload: ["operation": .string("runtime_probe")])))
        let invalidRequests: [[String: JSONValue]] = [
            ["operation": .string("config_load"), "config": .object([:])],
            ["operation": .string("config_load"), "fixture": .bool(false)],
            ["operation": .string("config_load"), "https": .bool(false)],
            ["operation": .string("config_save")],
            ["operation": .string("config_save"), "config": .array([])],
            ["operation": .string("config_save"), "config": .bool(true)],
            ["operation": .string("config_save"), "config": .object([:]), "extra": .null],
            ["operation": .integer(1)]
        ]
        for payload in invalidRequests {
            XCTAssertThrowsError(try business.register(ClientMessage(id: "invalid", type: "request", payload: payload)))
        }
        try business.register(configurationRequest("load"))
        try business.register(configurationRequest("save", config: .object(["unknown": .array([.bool(true), .null])])))
        XCTAssertTrue(business.hasPendingConfiguration)
    }

    func testConfigurationDocumentRejectsDuplicateKeysAndIllegalUnicode() throws {
        for json in [
            "[]", "null", "true", "1", "\"object required\"", "{\"x\":1,\"x\":2}",
            "{\"x\":1,\"\\u0078\":2}", "{\"x\":{\"a\":1,\"a\":2}}",
            "{\"x\":\"\\ud800\"}", "{\"\\udc00\":true}", "{\"x\":NaN}", "{\"x\":1e400}"
        ] { XCTAssertThrowsError(try ConfigurationDocument.parse(Data(json.utf8))) }
        XCTAssertThrowsError(try ConfigurationDocument.parse(Data([123, 34, 0xff, 34, 58, 48, 125])))
        let valid = try ConfigurationDocument.parse(Data(
            #"{"unknown":{"text":"\uD83D\uDE00","values":[true,false,null,0,1.5]}}"#.utf8
        ))
        XCTAssertEqual(valid["unknown"]?.object?["text"], .string("\u{1f600}"))
        XCTAssertEqual(valid["unknown"]?.object?["values"],
                       .array([.bool(true), .bool(false), .null, .integer(0), .number(1.5)]))
    }

    func testConfigurationDocumentUTF8CompactByteBoundary() throws {
        let overhead = try JSONValue.object(["x": .string("")]).encoded().count
        let exact = JSONValue.object(["x": .string(String(repeating: "a", count: 16_384 - overhead))])
        XCTAssertEqual(try exact.encoded().count, ConfigurationDocument.maxBytes)
        XCTAssertNoThrow(try ConfigurationDocument.validate(exact))
        XCTAssertThrowsError(try ConfigurationDocument.validate(.object([
            "x": .string(String(repeating: "a", count: 16_385 - overhead))
        ])))
        let unicode = JSONValue.object(["x": .string(String(repeating: "\u{00e9}", count: (16_384 - overhead) / 2))])
        XCTAssertEqual(try unicode.encoded().count, ConfigurationDocument.maxBytes)
        XCTAssertNoThrow(try ConfigurationDocument.validate(unicode))
        XCTAssertThrowsError(try ConfigurationDocument.validate(.object([
            "x": .string(String(repeating: "\u{00e9}", count: (16_384 - overhead) / 2 + 1))
        ])))
        XCTAssertNoThrow(try ConfigurationDocument.parse(Data(
            (" \n" + String(decoding: try exact.encoded(), as: UTF8.self) + "\n ").utf8
        )))
        XCTAssertNoThrow(try ConfigurationDocument.validate(.object(["url": .string("https://synthetic.invalid/")])))
        for unit in ["/", "\u{1f600}", "\u{4e2d}/"] {
            let text = String(repeating: unit, count: (16_384 - overhead) / unit.utf8.count)
            let config = JSONValue.object(["x": .string(text)])
            let compact = Data("{\"x\":\"\(text)\"}".utf8)
            XCTAssertEqual(compact.count, ConfigurationDocument.maxBytes)
            XCTAssertTrue(try config.encoded() == compact, "Unicode and slashes must remain unescaped")
            XCTAssertNoThrow(try ConfigurationDocument.validate(config))
            XCTAssertNoThrow(try ConfigurationDocument.parse(compact))
            XCTAssertNoThrow(try configurationRequest("boundary", config: config).encoded())
            let oversized = JSONValue.object(["x": .string(text + "a")])
            XCTAssertEqual(try oversized.encoded().count, ConfigurationDocument.maxBytes + 1)
            XCTAssertThrowsError(try ConfigurationDocument.validate(oversized))
            XCTAssertThrowsError(try ConfigurationDocument.parse(oversized.encoded()))
            XCTAssertThrowsError(try configurationRequest("oversized", config: oversized).encoded())
        }
    }

    func testConfigurationDocumentDepthCountsKeysAndValues() throws {
        var value: JSONValue = .integer(1)
        for _ in 0..<8 { value = .array([value]) }
        XCTAssertNoThrow(try ConfigurationDocument.validate(.object(["x": value])))
        XCTAssertThrowsError(try ConfigurationDocument.validate(.object(["x": .array([value])])))
        var object: JSONValue = .object([:])
        for _ in 0..<9 { object = .object(["x": object]) }
        XCTAssertNoThrow(try ConfigurationDocument.validate(object))
        XCTAssertThrowsError(try ConfigurationDocument.validate(.object(["x": object])))
        let keyAtSeventeen = String(repeating: "{\"x\":", count: 16) + "{}" + String(repeating: "}", count: 16)
        XCTAssertThrowsError(try JSONValue.parse(Data(keyAtSeventeen.utf8)))
    }

    func testConfigurationDocumentNumericBoundsKeepBooleansSeparate() throws {
        for value in [
            JSONValue.integer(9_007_199_254_740_991), .integer(-9_007_199_254_740_991),
            .number(9_007_199_254_740_991), .number(-9_007_199_254_740_991),
            .number(0.125), .bool(true), .bool(false)
        ] { XCTAssertNoThrow(try ConfigurationDocument.validate(.object(["x": value]))) }
        for value in [
            JSONValue.integer(9_007_199_254_740_992), .integer(-9_007_199_254_740_992),
            .integer(Int64.min), .integer(Int64.max), .number(9_007_199_254_740_992),
            .number(-9_007_199_254_740_992), .number(.infinity), .number(-.infinity), .number(.nan)
        ] {
            XCTAssertThrowsError(try ConfigurationDocument.validate(.object(["x": value])))
            XCTAssertThrowsError(try configurationRequest("save", config: .object(["x": value])).encoded())
        }
        for json in ["{\"x\":9007199254740992}", "{\"x\":-9007199254740992}", "{\"x\":1e16}"] {
            XCTAssertThrowsError(try ConfigurationDocument.parse(Data(json.utf8)))
        }
        XCTAssertNil(JSONValue.bool(true).integer)
        XCTAssertNil(JSONValue.integer(1).bool)
    }

    func testEncodedClientFramesCannotBypassStrictParsing() throws {
        var nested: JSONValue = .null
        for _ in 0..<13 { nested = .array([nested]) }
        let valid = ClientMessage(id: "r", type: "request", payload: ["value": nested])
        XCTAssertNoThrow(try valid.encoded())
        XCTAssertThrowsError(try ClientMessage(id: "r", type: "request",
                                              payload: ["value": .array([nested])]).encoded())
        for invalid in [Double.nan, .infinity, -.infinity] {
            XCTAssertThrowsError(try ClientMessage(id: "r", type: "request",
                                                  payload: ["value": .number(invalid)]).encoded())
            XCTAssertThrowsError(try JSONValue.array([.number(invalid)]).encoded())
            XCTAssertThrowsError(try JSONValue.number(invalid).encoded())
        }
        XCTAssertNoThrow(try JSONValue.number(0.125).encoded())
        XCTAssertNoThrow(try JSONValue.bool(true).encoded())
        let empty = ClientMessage(id: "r", type: "request", payload: ["value": .string("")])
        let overhead = try empty.encoded().count
        XCTAssertEqual(try ClientMessage(id: "r", type: "request", payload: [
            "value": .string(String(repeating: "a", count: 65_536 - overhead))
        ]).encoded().count, 65_536)
        XCTAssertThrowsError(try ClientMessage(id: "r", type: "request", payload: [
            "value": .string(String(repeating: "a", count: 65_537 - overhead))
        ]).encoded())
    }

    func testConfigurationLoadRequiresStartedAndValidatesReceivedDocument() throws {
        var state = try configurationConnected()
        try state.register(configurationRequest("load"))
        let operation: [String: JSONValue] = ["operation": .string("config_load")]
        XCTAssertThrowsError(try state.receive(event("load", 0, "cancelled")))
        XCTAssertThrowsError(try state.receive(event("load", 0, "started", operation)))
        _ = try state.receive(event("load", 0, "accepted", operation))
        XCTAssertThrowsError(try state.receive(event("load", 1, "completed", ["config": .object([:])])))
        XCTAssertThrowsError(try state.receive(event("load", 1, "delta", ["text": .string("synthetic"), "fixture": .bool(true)])))
        XCTAssertThrowsError(try state.receive(event("load", 1, "started", ["operation": .string("config_save")])))
        XCTAssertThrowsError(try state.receive(event("load", 1, "started", [
            "operation": .string("config_load"), "fixture": .bool(false)
        ])))
        _ = try state.receive(event("load", 1, "started", operation))
        XCTAssertThrowsError(try state.receive(event("load", 2, "started", operation)))
        for config in [JSONValue.array([]), .object(["x": .integer(9_007_199_254_740_992)]),
                       .object(["x": .string(String(repeating: "a", count: 16_384))])] {
            XCTAssertThrowsError(try state.receive(event("load", 2, "completed", ["config": config])))
        }
        var deep: JSONValue = .null
        for _ in 0..<9 { deep = .array([deep]) }
        XCTAssertThrowsError(try state.receive(event("load", 2, "completed", ["config": .object(["x": deep])])))
        XCTAssertThrowsError(try state.receive(event("load", 2, "completed", [
            "config": .object([:]), "fixture": .bool(false)
        ])))
        let duplicate = #"{"v":1,"id":"load","seq":2,"type":"completed","payload":{"config":{"x":1,"x":2}}}"#
        XCTAssertThrowsError(try state.receive(Data(duplicate.utf8)))
        _ = try state.receive(event("load", 2, "completed", ["config": .object(["unknown": .bool(true)])]))
        XCTAssertFalse(state.hasPendingConfiguration)
        XCTAssertFalse(state.hasPendingResponses)
        XCTAssertThrowsError(try state.receive(event("load", 3, "completed", ["config": .object([:])])))
        XCTAssertThrowsError(try state.receive(event("load", 3, "failed", ["code": .string("config_io_failed")])))
        XCTAssertThrowsError(try state.register(configurationRequest("load")))
    }

    func testConfigurationSaveCompletionRequiresStrictTrueAndNoFixtureFields() throws {
        var state = try configurationConnected()
        try state.register(configurationRequest("save", config: .object([:])))
        let operation: [String: JSONValue] = ["operation": .string("config_save")]
        _ = try state.receive(event("save", 0, "accepted", operation))
        XCTAssertThrowsError(try state.receive(event("save", 1, "completed", ["saved": .bool(true)])))
        _ = try state.receive(event("save", 1, "started", operation))
        for value in [JSONValue.bool(false), .integer(1), .string("true"), .null] {
            XCTAssertThrowsError(try state.receive(event("save", 2, "completed", ["saved": value])))
        }
        XCTAssertThrowsError(try state.receive(event("save", 2, "completed", [
            "saved": .bool(true), "fixture": .bool(false)
        ])))
        _ = try state.receive(event("save", 2, "completed", ["saved": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("save", 2, "completed", ["saved": .bool(true)])))
    }

    func testConfigurationQueuedAndStartedCancellationHaveDifferentTerminals() throws {
        var state = try configurationConnected()
        try state.register(configurationRequest("queued"))
        _ = try state.receive(event("queued", 0, "accepted", ["operation": .string("config_load")]))
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("queued")]))
        _ = try state.receive(event("queued", 1, "cancelled"))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("queued", 2, "started", ["operation": .string("config_load")])))
        try state.register(ClientMessage(id: "late", type: "cancel", payload: ["request_id": .string("queued")]))
        XCTAssertThrowsError(try state.receive(event("late", 0, "completed", ["cancel_requested": .bool(true)])))
        _ = try state.receive(event("late", 0, "completed", ["cancel_requested": .bool(false)]))
        try state.register(configurationRequest("started", config: .object([:])))
        _ = try state.receive(event("started", 0, "accepted", ["operation": .string("config_save")]))
        _ = try state.receive(event("started", 1, "started", ["operation": .string("config_save")]))
        try state.register(ClientMessage(id: "too_late", type: "cancel", payload: ["request_id": .string("started")]))
        XCTAssertThrowsError(try state.receive(event("too_late", 0, "completed", ["cancel_requested": .integer(0)])))
        XCTAssertThrowsError(try state.receive(event("too_late", 0, "completed", ["cancel_requested": .bool(true)])))
        _ = try state.receive(event("too_late", 0, "completed", ["cancel_requested": .bool(false)]))
        XCTAssertThrowsError(try state.receive(event("started", 2, "cancelled")))
        _ = try state.receive(event("started", 2, "completed", ["saved": .bool(true)]))
        try state.register(configurationRequest("ack_first"))
        _ = try state.receive(event("ack_first", 0, "accepted", ["operation": .string("config_load")]))
        try state.register(ClientMessage(id: "control_first", type: "cancel", payload: ["request_id": .string("ack_first")]))
        _ = try state.receive(event("control_first", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("ack_first", 1, "started", ["operation": .string("config_load")])))
        _ = try state.receive(event("ack_first", 1, "cancelled"))
        XCTAssertFalse(state.hasPendingConfiguration)
    }

    func testConfigurationFailureCodesBootstrapAndShutdownDrain() throws {
        for code in ["config_in_use", "config_unavailable"] {
            var bootstrap = ProtocolState(mode: .configuration)
            try bootstrap.register(ClientMessage(id: "hello", type: "hello"))
            let failure = try bootstrap.receive(event("hello", 0, "failed", ["code": .string(code)]))
            XCTAssertEqual(failure.safeFailureCode, code)
            XCTAssertFalse(bootstrap.ready)
            XCTAssertThrowsError(try bootstrap.register(configurationRequest("load")))
        }
        var state = try configurationConnected()
        for code in ["config_in_use", "config_unavailable", "invalid_config", "config_io_failed", "busy"] {
            try state.register(configurationRequest(code))
            let failure = try state.receive(event(code, 0, "failed", ["code": .string(code)]))
            XCTAssertEqual(failure.safeFailureCode, code)
        }
        try state.register(configurationRequest("load"))
        _ = try state.receive(event("load", 0, "accepted", ["operation": .string("config_load")]))
        _ = try state.receive(event("load", 1, "started", ["operation": .string("config_load")]))
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        XCTAssertThrowsError(try state.receive(event("load", 2, "cancelled")))
        _ = try state.receive(event("load", 2, "failed", ["code": .string("config_io_failed")]))
        XCTAssertThrowsError(try state.receive(event("load", 3, "failed", ["code": .string("config_io_failed")])))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertFalse(state.hasPendingResponses)
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
            "dictionary": .object([
                "status": .string("passed"), "read_only": .bool(true),
                "sources_preserved": .bool(true), "reopened": .bool(true)
            ]),
            "codex_config_fixture": .object([
                "status": .string("passed"), "fixture": .bool(true),
                "methods_verified": .bool(true), "routing_preserved": .bool(true)
            ]),
            "catalog_storage_fixture": .object([
                "status": .string("passed"), "cli_simulated": .bool(true),
                "cache_verified": .bool(true), "reopen_verified": .bool(true)
            ]),
            "catalog_process_fixture": .object([
                "status": .string("passed"), "fixture": .bool(true), "process_verified": .bool(true),
                "cache_verified": .bool(true), "reopen_verified": .bool(true)
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
        report["codex_config_fixture"] = .object(["status": .string("not_run")])
        report["catalog_process_fixture"] = .object(["status": .string("not_run")])
        let result = try state.receive(event("runtime", 1, "completed", report))
        XCTAssertEqual(result.payload["python"]?.object?["bundle_runtime"], .bool(false))
        XCTAssertEqual(result.payload["https"]?.object?["status"], .string("not_run"))
    }

    func testBundledConfigFixtureCannotBeSkippedOrForged() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "config", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        _ = try state.receive(event("config", 0, "accepted", ["operation": .string("runtime_probe")]))
        let fixture = try XCTUnwrap(runtimeReport()["codex_config_fixture"]?.object)
        var report = runtimeReport()
        report.removeValue(forKey: "codex_config_fixture")
        XCTAssertThrowsError(try state.receive(event("config", 1, "completed", report)))
        report["codex_config_fixture"] = .object(["status": .string("not_run")])
        XCTAssertThrowsError(try state.receive(event("config", 1, "completed", report)))
        for key in ["fixture", "methods_verified", "routing_preserved"] {
            for value in [JSONValue.bool(false), .integer(1), .string("true")] {
                var invalid = fixture
                invalid[key] = value
                report["codex_config_fixture"] = .object(invalid)
                XCTAssertThrowsError(try state.receive(event("config", 1, "completed", report)))
            }
        }
        var extra = fixture
        extra["path"] = .string("synthetic forbidden path")
        report["codex_config_fixture"] = .object(extra)
        XCTAssertThrowsError(try state.receive(event("config", 1, "completed", report)))
        _ = try state.receive(event("config", 1, "completed", runtimeReport()))
    }

    func testDictionaryEvidenceAndFailureCodeAreStrict() throws {
        var state = try connected()
        let message = ClientMessage(id: "runtime", type: "request", payload: [
            "operation": .string("runtime_probe")
        ])
        try state.register(message)
        _ = try state.receive(event("runtime", 0, "accepted", ["operation": .string("runtime_probe")]))
        let dictionary = try XCTUnwrap(runtimeReport()["dictionary"]?.object)
        for key in dictionary.keys {
            var report = runtimeReport()
            var incomplete = dictionary
            incomplete.removeValue(forKey: key)
            report["dictionary"] = .object(incomplete)
            XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", report)))
        }

        for key in ["read_only", "sources_preserved", "reopened"] {
            for value in [JSONValue.bool(false), .integer(1), .string("true")] {
                var report = runtimeReport()
                var invalid = dictionary
                invalid[key] = value
                report["dictionary"] = .object(invalid)
                XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", report)))
            }
        }
        var extra = runtimeReport()
        var privateField = dictionary
        privateField["path"] = .string("synthetic forbidden path")
        extra["dictionary"] = .object(privateField)
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", extra)))
        extra.removeValue(forKey: "dictionary")
        XCTAssertThrowsError(try state.receive(event("runtime", 1, "completed", extra)))
        _ = try state.receive(event("runtime", 1, "completed", runtimeReport()))
        try state.register(ClientMessage(id: "dictionary_failed", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        let failure = try state.receive(event("dictionary_failed", 0, "failed", [
            "code": .string("dictionary_probe_failed")
        ]))
        XCTAssertEqual(failure.safeFailureCode, "dictionary_probe_failed")
    }

    func testCatalogFixtureCannotOmitSimulationOrForgeStorageEvidence() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "catalog", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        _ = try state.receive(event("catalog", 0, "accepted", ["operation": .string("runtime_probe")]))
        let fixture = try XCTUnwrap(runtimeReport()["catalog_storage_fixture"]?.object)
        var report = runtimeReport()
        report.removeValue(forKey: "catalog_storage_fixture")
        XCTAssertThrowsError(try state.receive(event("catalog", 1, "completed", report)))
        for key in fixture.keys {
            var incomplete = fixture
            incomplete.removeValue(forKey: key)
            report["catalog_storage_fixture"] = .object(incomplete)
            XCTAssertThrowsError(try state.receive(event("catalog", 1, "completed", report)))
        }
        for key in ["cli_simulated", "cache_verified", "reopen_verified"] {
            for value in [JSONValue.bool(false), .integer(1), .string("true")] {
                var invalid = fixture
                invalid[key] = value
                report["catalog_storage_fixture"] = .object(invalid)
                XCTAssertThrowsError(try state.receive(event("catalog", 1, "completed", report)))
            }
        }
        var extra = fixture
        extra["path"] = .string("synthetic forbidden path")
        report["catalog_storage_fixture"] = .object(extra)
        XCTAssertThrowsError(try state.receive(event("catalog", 1, "completed", report)))
        _ = try state.receive(event("catalog", 1, "completed", runtimeReport()))
        try state.register(ClientMessage(id: "catalog_failed", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        let failure = try state.receive(event("catalog_failed", 0, "failed", [
            "code": .string("catalog_fixture_failed")
        ]))
        XCTAssertEqual(failure.safeFailureCode, "catalog_fixture_failed")
    }

    func testCatalogProcessRequiresCompleteSyntheticProcessEvidence() throws {
        var state = try connected()
        try state.register(ClientMessage(id: "catalog_process", type: "request", payload: [
            "operation": .string("runtime_probe")
        ]))
        _ = try state.receive(event("catalog_process", 0, "accepted", ["operation": .string("runtime_probe")]))
        let fixture = try XCTUnwrap(runtimeReport()["catalog_process_fixture"]?.object)
        var report = runtimeReport()
        report.removeValue(forKey: "catalog_process_fixture")
        XCTAssertThrowsError(try state.receive(event("catalog_process", 1, "completed", report)))
        report["catalog_process_fixture"] = .object(["status": .string("not_run")])
        XCTAssertThrowsError(try state.receive(event("catalog_process", 1, "completed", report)))
        for key in fixture.keys {
            var incomplete = fixture
            incomplete.removeValue(forKey: key)
            report["catalog_process_fixture"] = .object(incomplete)
            XCTAssertThrowsError(try state.receive(event("catalog_process", 1, "completed", report)))
        }
        for key in ["fixture", "process_verified", "cache_verified", "reopen_verified"] {
            for value in [JSONValue.bool(false), .integer(1), .string("true")] {
                var invalid = fixture
                invalid[key] = value
                report["catalog_process_fixture"] = .object(invalid)
                XCTAssertThrowsError(try state.receive(event("catalog_process", 1, "completed", report)))
            }
        }
        var extra = fixture
        extra["path"] = .string("synthetic forbidden path")
        report["catalog_process_fixture"] = .object(extra)
        XCTAssertThrowsError(try state.receive(event("catalog_process", 1, "completed", report)))
        _ = try state.receive(event("catalog_process", 1, "completed", runtimeReport()))
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
