import Foundation

public enum ProbeError: String, Error, LocalizedError {
    case frameTooLarge, incompleteFrame, invalidJSON, invalidEnvelope
    case invalidID, reusedID, idLimit, invalidSequence, invalidTransition
    case invalidPayload, notReady, bundleMissing, launchFailed, readFailed, writeFailed
    case handshakeTimeout, requestTimeout, helperEOF, helperExited, helperProtocolError
    case stderrLimit, shuttingDown, cliTimeout, cliOutputLimit, cliFailed, cliCancelled
    case permissionDenied, secureInput, noDisplay, captureFailed, noImage, ocrFailed

    public var errorDescription: String? { rawValue }
}

public struct LineFramer {
    public static let maxFrameBytes = 65_536
    private var buffer = Data()

    public init() {}

    public mutating func append(_ bytes: Data) throws -> [Data] {
        var frames: [Data] = []
        for byte in bytes {
            guard buffer.count < Self.maxFrameBytes else { throw ProbeError.frameTooLarge }
            buffer.append(byte)
            if byte == 10 {
                frames.append(buffer.dropLast())
                buffer.removeAll(keepingCapacity: true)
            } else if buffer.count == Self.maxFrameBytes {
                throw ProbeError.frameTooLarge
            }
        }
        return frames
    }

    public func finish() throws {
        guard buffer.isEmpty else { throw ProbeError.incompleteFrame }
    }
}

public enum JSONValue: Equatable {
    case object([String: JSONValue]), array([JSONValue]), string(String)
    case integer(Int64), number(Double), bool(Bool), null

    public var object: [String: JSONValue]? {
        if case let .object(value) = self { return value }
        return nil
    }
    public var string: String? {
        if case let .string(value) = self { return value }
        return nil
    }
    public var integer: Int64? {
        if case let .integer(value) = self { return value }
        return nil
    }
    public var bool: Bool? {
        if case let .bool(value) = self { return value }
        return nil
    }

    fileprivate var foundation: Any {
        switch self {
        case .object(let value): return value.mapValues(\.foundation)
        case .array(let value): return value.map(\.foundation)
        case .string(let value): return value
        case .integer(let value): return NSNumber(value: value)
        case .number(let value): return NSNumber(value: value)
        case .bool(let value): return NSNumber(value: value)
        case .null: return NSNull()
        }
    }

    public func encoded() throws -> Data {
        try JSONSerialization.data(withJSONObject: foundation, options: [.sortedKeys, .fragmentsAllowed])
    }

    public static func parse(_ data: Data) throws -> JSONValue {
        var parser = StrictJSON(data)
        return try parser.parse()
    }
}

// Validate before Foundation can discard duplicate object keys or coerce numbers.
private struct StrictJSON {
    let bytes: [UInt8]
    var cursor = 0
    init(_ data: Data) { bytes = Array(data) }

    mutating func parse() throws -> JSONValue {
        guard !bytes.isEmpty, bytes.count < LineFramer.maxFrameBytes,
              String(bytes: bytes, encoding: .utf8) != nil else { throw ProbeError.invalidJSON }
        let result = try value(depth: 1)
        whitespace()
        guard cursor == bytes.count else { throw ProbeError.invalidJSON }
        return result
    }

    mutating func whitespace() {
        while cursor < bytes.count, [9, 10, 13, 32].contains(bytes[cursor]) { cursor += 1 }
    }

    mutating func consume(_ byte: UInt8) -> Bool {
        whitespace()
        guard cursor < bytes.count, bytes[cursor] == byte else { return false }
        cursor += 1
        return true
    }

    mutating func value(depth: Int) throws -> JSONValue {
        whitespace()
        guard depth <= 16, cursor < bytes.count else { throw ProbeError.invalidJSON }
        switch bytes[cursor] {
        case 123:
            cursor += 1
            var result: [String: JSONValue] = [:]
            if consume(125) { return .object(result) }
            repeat {
                whitespace()
                let key = try string()
                guard result[key] == nil, consume(58) else { throw ProbeError.invalidJSON }
                result[key] = try value(depth: depth + 1)
                if consume(125) { return .object(result) }
            } while consume(44)
            throw ProbeError.invalidJSON
        case 91:
            cursor += 1
            var result: [JSONValue] = []
            if consume(93) { return .array(result) }
            repeat {
                result.append(try value(depth: depth + 1))
                if consume(93) { return .array(result) }
            } while consume(44)
            throw ProbeError.invalidJSON
        case 34: return .string(try string())
        case 116: try literal("true"); return .bool(true)
        case 102: try literal("false"); return .bool(false)
        case 110: try literal("null"); return .null
        default: return try number()
        }
    }

    mutating func literal(_ text: String) throws {
        let expected = Array(text.utf8)
        guard cursor + expected.count <= bytes.count,
              Array(bytes[cursor..<(cursor + expected.count)]) == expected else {
            throw ProbeError.invalidJSON
        }
        cursor += expected.count
    }

    mutating func string() throws -> String {
        guard cursor < bytes.count, bytes[cursor] == 34 else { throw ProbeError.invalidJSON }
        let start = cursor
        cursor += 1
        while cursor < bytes.count {
            let byte = bytes[cursor]
            cursor += 1
            if byte == 34 {
                do {
                    return try JSONDecoder().decode(String.self, from: Data(bytes[start..<cursor]))
                } catch {
                    throw ProbeError.invalidJSON
                }
            }
            guard byte >= 32 else { throw ProbeError.invalidJSON }
            if byte == 92 {
                guard cursor < bytes.count else { throw ProbeError.invalidJSON }
                let escape = bytes[cursor]
                cursor += 1
                switch escape {
                case 34, 47, 92, 98, 102, 110, 114, 116: break
                case 117:
                    let scalar = try hexQuad()
                    if (0xD800...0xDBFF).contains(scalar) {
                        guard cursor + 2 <= bytes.count,
                              bytes[cursor] == 92, bytes[cursor + 1] == 117 else {
                            throw ProbeError.invalidJSON
                        }
                        cursor += 2
                        guard (0xDC00...0xDFFF).contains(try hexQuad()) else {
                            throw ProbeError.invalidJSON
                        }
                    } else if (0xDC00...0xDFFF).contains(scalar) {
                        throw ProbeError.invalidJSON
                    }
                default: throw ProbeError.invalidJSON
                }
            }
        }
        throw ProbeError.invalidJSON
    }

    mutating func hexQuad() throws -> UInt16 {
        guard cursor + 4 <= bytes.count else { throw ProbeError.invalidJSON }
        var value: UInt16 = 0
        for _ in 0..<4 {
            let digit: UInt16
            switch bytes[cursor] {
            case 48...57: digit = UInt16(bytes[cursor] - 48)
            case 65...70: digit = UInt16(bytes[cursor] - 65 + 10)
            case 97...102: digit = UInt16(bytes[cursor] - 97 + 10)
            default: throw ProbeError.invalidJSON
            }
            value = value * 16 + digit
            cursor += 1
        }
        return value
    }

    mutating func number() throws -> JSONValue {
        let start = cursor
        if cursor < bytes.count, bytes[cursor] == 45 { cursor += 1 }
        guard cursor < bytes.count else { throw ProbeError.invalidJSON }
        if bytes[cursor] == 48 {
            cursor += 1
        } else {
            guard (49...57).contains(bytes[cursor]) else { throw ProbeError.invalidJSON }
            digits()
        }
        var integral = true
        if cursor < bytes.count, bytes[cursor] == 46 {
            integral = false
            cursor += 1
            let before = cursor
            digits()
            guard cursor > before else { throw ProbeError.invalidJSON }
        }
        if cursor < bytes.count, [69, 101].contains(bytes[cursor]) {
            integral = false
            cursor += 1
            if cursor < bytes.count, [43, 45].contains(bytes[cursor]) { cursor += 1 }
            let before = cursor
            digits()
            guard cursor > before else { throw ProbeError.invalidJSON }
        }
        let text = String(decoding: bytes[start..<cursor], as: UTF8.self)
        if integral, let value = Int64(text) { return .integer(value) }
        guard let value = Double(text), value.isFinite else { throw ProbeError.invalidJSON }
        return .number(value)
    }

    mutating func digits() {
        while cursor < bytes.count, (48...57).contains(bytes[cursor]) { cursor += 1 }
    }
}

public struct ClientMessage {
    public let id: String
    public let type: String
    public let payload: [String: JSONValue]

    public init(id: String, type: String, payload: [String: JSONValue] = [:]) {
        self.id = id
        self.type = type
        self.payload = payload
    }

    public func encoded() throws -> Data {
        var data = try JSONValue.object([
            "v": .integer(1), "id": .string(id), "type": .string(type), "payload": .object(payload)
        ]).encoded()
        data.append(10)
        guard data.count <= LineFramer.maxFrameBytes else { throw ProbeError.frameTooLarge }
        return data
    }
}

public struct ServerEvent: Equatable {
    public let id: String
    public let sequence: Int64
    public let type: String
    public let payload: [String: JSONValue]
    public var isTerminal: Bool { ["ready", "completed", "cancelled", "failed"].contains(type) }

    public var safeFailureCode: String {
        HelperFailureCode(rawValue: payload["code"]?.string ?? "")?.rawValue ?? "helper_failed"
    }
}

private enum HelperFailureCode: String {
    case busy
    case invalidPayload = "invalid_payload"
    case invalidText = "invalid_text"
    case invalidDelay = "invalid_delay"
    case unsupportedOperation = "unsupported_operation"
    case internalError = "internal_error"
    case workerStartFailed = "worker_start_failed"
    case sqliteReadbackFailed = "sqlite_readback_failed"
    case sqliteProbeFailed = "sqlite_probe_failed"
    case dictionaryProbeFailed = "dictionary_probe_failed"
    case configFixtureFailed = "config_fixture_failed"
    case bundleCAMissing = "bundle_ca_missing"
    case sslContextFailed = "ssl_context_failed"
    case sslValidationDisabled = "ssl_validation_disabled"
    case httpsStatusFailed = "https_status_failed"
    case httpsCertificateFailed = "https_certificate_failed"
    case httpsProbeFailed = "https_probe_failed"
}

public struct ProtocolState {
    private struct Entry {
        let type: String
        let operation: String?
        let https: Bool
        var sequence: Int64 = 0
        var accepted = false
        var terminal = false
    }
    private var entries: [String: Entry] = [:]
    public private(set) var ready = false
    public private(set) var closing = false
    public var registeredCount: Int { entries.count }
    public init() {}

    public static func validID(_ id: String) -> Bool {
        let bytes = Array(id.utf8)
        return (1...64).contains(bytes.count) && bytes.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) ||
                (48...57).contains($0) || $0 == 95 || $0 == 45
        }
    }

    public mutating func register(_ message: ClientMessage) throws {
        guard Self.validID(message.id), message.id != "protocol" else { throw ProbeError.invalidID }
        guard entries[message.id] == nil else { throw ProbeError.reusedID }
        guard entries.count < 4096 else { throw ProbeError.idLimit }
        guard !closing else { throw ProbeError.shuttingDown }
        if entries.isEmpty {
            guard message.type == "hello", message.payload.isEmpty else { throw ProbeError.notReady }
        } else {
            guard ready, message.type != "hello" else { throw ProbeError.notReady }
        }
        let payload = message.payload
        let operation = payload["operation"]?.string
        switch message.type {
        case "hello", "shutdown":
            guard payload.isEmpty else { throw ProbeError.invalidPayload }
        case "request":
            switch operation {
            case "fixture":
                guard Set(payload.keys).isSubset(of: ["operation", "text", "delay_ms"]),
                      let text = payload["text"]?.string, text.utf8.count <= 8192 else {
                    throw ProbeError.invalidPayload
                }
                if let delay = payload["delay_ms"] {
                    guard let delay = delay.integer, (0...2000).contains(delay) else {
                        throw ProbeError.invalidPayload
                    }
                }
            case "runtime_probe":
                guard Set(payload.keys).isSubset(of: ["operation", "https"]),
                      payload["https"] == nil || payload["https"]?.bool != nil else {
                    throw ProbeError.invalidPayload
                }
            default: throw ProbeError.invalidPayload
            }
        case "cancel":
            guard Set(payload.keys) == ["request_id"],
                  let id = payload["request_id"]?.string, Self.validID(id), id != "protocol" else {
                throw ProbeError.invalidPayload
            }
        default: throw ProbeError.invalidEnvelope
        }
        entries[message.id] = Entry(type: message.type, operation: operation, https: payload["https"]?.bool ?? false)
        if message.type == "shutdown" { closing = true }
    }

    public mutating func receive(_ frame: Data) throws -> ServerEvent {
        guard let envelope = try JSONValue.parse(frame).object,
              Set(envelope.keys) == ["v", "id", "seq", "type", "payload"],
              envelope["v"] == .integer(1),
              let id = envelope["id"]?.string, Self.validID(id),
              let seq = envelope["seq"]?.integer, seq >= 0,
              let type = envelope["type"]?.string,
              let payload = envelope["payload"]?.object else { throw ProbeError.invalidEnvelope }
        if id == "protocol" {
            guard seq == 0, type == "failed", validFailure(payload) else {
                throw ProbeError.invalidEnvelope
            }
            throw ProbeError.helperProtocolError
        }
        guard var entry = entries[id] else { throw ProbeError.invalidID }
        guard seq == entry.sequence else { throw ProbeError.invalidSequence }
        guard !entry.terminal else { throw ProbeError.invalidTransition }
        let event = ServerEvent(id: id, sequence: seq, type: type, payload: payload)
        switch type {
        case "ready":
            guard entry.type == "hello", seq == 0,
                  Set(payload.keys) == ["protocol", "capabilities", "max_frame_bytes", "fixture"],
                  payload["protocol"] == .integer(1),
                  payload["max_frame_bytes"] == .integer(65_536), payload["fixture"] == .bool(true),
                  case let .array(capabilities)? = payload["capabilities"],
                  capabilities.count == 2,
                  Set(capabilities.compactMap(\.string)) == ["fixture", "runtime_probe"] else {
                throw ProbeError.invalidPayload
            }
            ready = true
        case "accepted":
            guard entry.type == "request", !entry.accepted, seq == 0,
                  Set(payload.keys) == ["operation"],
                  payload["operation"]?.string == entry.operation else { throw ProbeError.invalidTransition }
            entry.accepted = true
        case "delta":
            guard entry.accepted, entry.operation == "fixture", fixturePayload(payload) else {
                throw ProbeError.invalidPayload
            }
        case "completed":
            switch entry.type {
            case "request":
                guard entry.accepted else { throw ProbeError.invalidTransition }
                if entry.operation == "fixture" {
                    guard fixturePayload(payload) else { throw ProbeError.invalidPayload }
                } else {
                    guard runtimePayload(payload, https: entry.https) else {
                        throw ProbeError.invalidPayload
                    }
                }
            case "cancel":
                guard seq == 0, Set(payload.keys) == ["cancel_requested"],
                      payload["cancel_requested"]?.bool != nil else { throw ProbeError.invalidPayload }
            case "shutdown":
                guard seq == 0, payload.isEmpty else { throw ProbeError.invalidPayload }
            default: throw ProbeError.invalidTransition
            }
        case "cancelled":
            guard entry.type == "request", entry.accepted, payload.isEmpty else {
                throw ProbeError.invalidTransition
            }
        case "failed":
            guard validFailure(payload) else { throw ProbeError.invalidPayload }
        default: throw ProbeError.invalidEnvelope
        }
        entry.sequence += 1
        entry.terminal = event.isTerminal
        entries[id] = entry
        return event
    }

    private func fixturePayload(_ payload: [String: JSONValue]) -> Bool {
        Set(payload.keys) == ["text", "fixture"] &&
            payload["text"]?.string != nil && payload["fixture"] == .bool(true)
    }

    private func runtimePayload(_ payload: [String: JSONValue], https: Bool) -> Bool {
        guard Set(payload.keys) == ["python", "sqlite", "dictionary", "codex_config_fixture", "ssl", "https"],
              let python = payload["python"]?.object,
              Set(python.keys) == ["version", "platform", "machine", "isolated", "bytecode_disabled", "bundle_runtime"],
              let pythonVersion = python["version"]?.string, !pythonVersion.isEmpty,
              let platform = python["platform"]?.string, !platform.isEmpty,
              let machine = python["machine"]?.string, !machine.isEmpty,
              python["isolated"]?.bool != nil, python["bytecode_disabled"]?.bool != nil,
              python["bundle_runtime"]?.bool != nil,
              let sqlite = payload["sqlite"]?.object, let ssl = payload["ssl"]?.object,
              let network = payload["https"]?.object,
              Set(sqlite.keys) == ["status", "read_write", "version"],
              sqlite["status"] == .string("passed"), sqlite["read_write"] == .bool(true),
              let sqliteVersion = sqlite["version"]?.string, !sqliteVersion.isEmpty,
              payload["dictionary"] == .object([
                  "status": .string("passed"), "read_only": .bool(true),
                  "sources_preserved": .bool(true), "reopened": .bool(true)
              ]),
              Set(ssl.keys) == ["status", "version", "certificate_validation", "ca_source"],
              ssl["status"] == .string("passed"), ssl["certificate_validation"] == .bool(true),
              let sslVersion = ssl["version"]?.string, !sslVersion.isEmpty,
              ssl["ca_source"] == .string("bundle") || ssl["ca_source"] == .string("system") else {
            return false
        }
        let fixtureExpected: JSONValue = platform == "darwin" && python["bundle_runtime"] == .bool(true) ?
            .object(["status": .string("passed"), "fixture": .bool(true),
                     "methods_verified": .bool(true), "routing_preserved": .bool(true)]) :
            .object(["status": .string("not_run")])
        guard payload["codex_config_fixture"] == fixtureExpected else { return false }
        if https {
            return ssl["ca_source"] == .string("bundle") &&
                Set(network.keys) == ["status", "host", "certificate_verified"] &&
                network["status"] == .string("passed") &&
                network["host"] == .string("www.python.org") &&
                network["certificate_verified"] == .bool(true)
        }
        return Set(network.keys) == ["status"] && network["status"] == .string("not_run")
    }

    private func validFailure(_ payload: [String: JSONValue]) -> Bool {
        guard Set(payload.keys) == ["code"], let code = payload["code"]?.string else { return false }
        return Self.validID(code)
    }
}

public struct LatestRequest {
    public private(set) var id: String?
    public init() {}
    public mutating func select(_ id: String?) { self.id = id }
    public func accepts(_ event: ServerEvent) -> Bool { id == event.id }
}
