import Foundation

public enum ProbeError: String, Error, LocalizedError {
    case frameTooLarge, incompleteFrame, invalidJSON, invalidEnvelope
    case invalidID, reusedID, idLimit, invalidSequence, invalidTransition
    case invalidPayload, notReady, bundleMissing, launchFailed, readFailed, writeFailed
    case handshakeTimeout, requestTimeout, helperEOF, helperExited, helperProtocolError
    case stderrLimit, shuttingDown, cliTimeout, cliOutputLimit, cliFailed, cliCancelled
    case permissionDenied, secureInput, noDisplay, captureFailed, noImage, ocrFailed
    case configurationOutcomeUnknown, historyOutcomeUnknown
    case configInUse = "config_in_use"
    case configUnavailable = "config_unavailable"
    case historyInUse = "history_in_use"
    case historyUnavailable = "history_unavailable"
    case stateIOFailed = "state_io_failed"

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
        let value = foundation
        // Foundation can raise an Objective-C exception for NaN instead of a Swift error.
        guard JSONSerialization.isValidJSONObject([value]) else { throw ProbeError.invalidJSON }
        return try JSONSerialization.data(withJSONObject: value,
                                          options: [.sortedKeys, .fragmentsAllowed, .withoutEscapingSlashes])
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
                guard depth < 16 else { throw ProbeError.invalidJSON }
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

public enum ConfigurationDocument {
    public static let maxBytes = 16_384
    public static let maxDepth = 10
    public static let maxNumber: Int64 = 9_007_199_254_740_991

    public static func validate(_ value: JSONValue) throws {
        guard value.object != nil else { throw ProbeError.invalidPayload }
        try validateJSONTree(value, maxDepth: maxDepth)
        let data = try value.encoded()
        guard data.count <= maxBytes else { throw ProbeError.invalidPayload }
        _ = try JSONValue.parse(data)
    }

    public static func parse(_ data: Data) throws -> [String: JSONValue] {
        let value = try JSONValue.parse(data)
        try validate(value)
        guard let object = value.object else { throw ProbeError.invalidPayload }
        return object
    }
}

private func validateJSONTree(_ value: JSONValue, maxDepth: Int, depth: Int = 1) throws {
    guard depth <= maxDepth else { throw ProbeError.invalidPayload }
    switch value {
    case .object(let object):
        for (key, child) in object {
            try validateJSONTree(.string(key), maxDepth: maxDepth, depth: depth + 1)
            try validateJSONTree(child, maxDepth: maxDepth, depth: depth + 1)
        }
    case .array(let array):
        for child in array { try validateJSONTree(child, maxDepth: maxDepth, depth: depth + 1) }
    case .integer(let integer):
        guard (-ConfigurationDocument.maxNumber...ConfigurationDocument.maxNumber).contains(integer) else {
            throw ProbeError.invalidPayload
        }
    case .number(let number):
        guard number.isFinite, abs(number) <= Double(ConfigurationDocument.maxNumber) else {
            throw ProbeError.invalidPayload
        }
    case .bool, .string, .null: break
    }
}

enum HistoryDocument {
    static func validRevision(_ value: JSONValue?) -> Bool {
        guard let revision = value?.string else { return false }
        let bytes = Array(revision.utf8)
        return bytes.count == 64 && bytes.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    static func validateEntry(_ value: JSONValue) throws {
        guard let entry = value.object else { throw ProbeError.invalidPayload }
        try validateJSONTree(value, maxDepth: 13)
        for key in ["ts", "input", "output", "kind", "sig"] {
            if let field = entry[key], field != .null, field.string == nil {
                throw ProbeError.invalidPayload
            }
        }
        for key in ["is_dict", "is_code"] {
            if let field = entry[key], field.bool == nil { throw ProbeError.invalidPayload }
        }
    }

    static func validateRequest(_ payload: [String: JSONValue]) throws {
        switch payload["operation"]?.string {
        case "history_load":
            _ = try HistoryPageRequest(payload)
        case "history_add":
            guard Set(payload.keys) == ["operation", "input", "output", "is_dict", "is_code", "kind", "sig", "limit"],
                  let input = payload["input"]?.string, input.utf8.count <= 24_000,
                  let output = payload["output"]?.string, output.utf8.count <= 24_000,
                  payload["is_dict"]?.bool != nil, payload["is_code"]?.bool != nil,
                  let kind = payload["kind"]?.string, ["text", "dict", "code", "ocr"].contains(kind),
                  let sig = payload["sig"]?.string, sig.utf8.count <= 4096,
                  let limit = payload["limit"]?.integer, (1...10_000).contains(limit) else {
                throw ProbeError.invalidPayload
            }
        case "history_clear":
            guard Set(payload.keys) == ["operation"] else { throw ProbeError.invalidPayload }
        default: throw ProbeError.invalidPayload
        }
    }
}

private struct HistoryPageRequest {
    let pageSize: Int64
    let offset: Int64
    let revision: String?

    init(_ payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["operation", "page_size", "cursor"],
              let pageSize = payload["page_size"]?.integer, (1...100).contains(pageSize),
              let cursor = payload["cursor"] else { throw ProbeError.invalidPayload }
        self.pageSize = pageSize
        if cursor == .null {
            offset = 0
            revision = nil
        } else {
            let parsed = try Self.parseCursor(cursor)
            offset = parsed.offset
            revision = parsed.revision
        }
    }

    private static func parseCursor(_ value: JSONValue) throws -> (revision: String, offset: Int64) {
        guard let cursor = value.object, Set(cursor.keys) == ["revision", "offset"],
              HistoryDocument.validRevision(cursor["revision"]), let revision = cursor["revision"]?.string,
              let offset = cursor["offset"]?.integer, (1...10_000).contains(offset) else {
            throw ProbeError.invalidPayload
        }
        return (revision, offset)
    }

    func validatePage(_ payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["entries", "revision", "total", "next_cursor"],
              case let .array(entries)? = payload["entries"], Int64(entries.count) <= pageSize,
              HistoryDocument.validRevision(payload["revision"]),
              let returnedRevision = payload["revision"]?.string,
              revision == nil || revision == returnedRevision,
              let total = payload["total"]?.integer, (0...10_000).contains(total),
              let next = payload["next_cursor"] else { throw ProbeError.invalidPayload }
        for entry in entries { try HistoryDocument.validateEntry(entry) }
        let end = offset + Int64(entries.count)
        if next == .null {
            guard end == total else { throw ProbeError.invalidPayload }
        } else {
            let cursor = try Self.parseCursor(next)
            guard !entries.isEmpty, cursor.revision == returnedRevision,
                  cursor.offset == end, end < total else { throw ProbeError.invalidPayload }
        }
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
        if payload["operation"] == .string("config_save"), let config = payload["config"] {
            try ConfigurationDocument.validate(config)
        }
        if let operation = payload["operation"]?.string,
           ["history_load", "history_add", "history_clear"].contains(operation) {
            try HistoryDocument.validateRequest(payload)
        }
        var data = try JSONValue.object([
            "v": .integer(1), "id": .string(id), "type": .string(type), "payload": .object(payload)
        ]).encoded()
        guard data.count < LineFramer.maxFrameBytes else { throw ProbeError.frameTooLarge }
        // Validate the actual bytes, not just the caller's in-memory value tree.
        _ = try JSONValue.parse(data)
        data.append(10)
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
    case catalogFixtureFailed = "catalog_fixture_failed"
    case bundleCAMissing = "bundle_ca_missing"
    case sslContextFailed = "ssl_context_failed"
    case sslValidationDisabled = "ssl_validation_disabled"
    case httpsStatusFailed = "https_status_failed"
    case httpsCertificateFailed = "https_certificate_failed"
    case httpsProbeFailed = "https_probe_failed"
    case configInUse = "config_in_use"
    case configUnavailable = "config_unavailable"
    case invalidConfig = "invalid_config"
    case configIOFailed = "config_io_failed"
    case historyInUse = "history_in_use"
    case historyUnavailable = "history_unavailable"
    case historyIOFailed = "history_io_failed"
    case invalidHistory = "invalid_history"
    case historyTooLarge = "history_too_large"
    case historyEntryTooLarge = "history_entry_too_large"
    case invalidHistoryRecord = "invalid_history_record"
    case invalidHistoryCursor = "invalid_history_cursor"
    case historyCursorExpired = "history_cursor_expired"
    case stateIOFailed = "state_io_failed"
}

public struct ProtocolState {
    public enum Mode { case diagnostic, configuration }

    private struct Entry {
        let order: Int
        let type: String
        let operation: String?
        let https: Bool
        let cancellationTarget: String?
        let cancellationEligible: Bool
        let historyPage: HistoryPageRequest?
        var sequence: Int64 = 0
        var accepted = false
        var started = false
        var cancellationConfirmed = false
        var cancelled = false
        var terminal = false
    }
    private var entries: [String: Entry] = [:]
    public private(set) var ready = false
    public private(set) var closing = false
    public var registeredCount: Int { entries.count }
    public let mode: Mode
    public var hasPendingResponses: Bool { entries.values.contains { !$0.terminal } }
    public var hasPendingConfiguration: Bool {
        entries.values.contains {
            !$0.terminal && ["config_load", "config_save"].contains($0.operation ?? "")
        }
    }
    public var hasPendingHistory: Bool {
        entries.values.contains {
            !$0.terminal && ["history_load", "history_add", "history_clear"].contains($0.operation ?? "")
        }
    }
    var pendingOutcomeUnknown: ProbeError? {
        if hasPendingConfiguration { return .configurationOutcomeUnknown }
        if hasPendingHistory { return .historyOutcomeUnknown }
        return nil
    }
    private static let businessOperations: Set<String> = [
        "config_load", "config_save", "history_load", "history_add", "history_clear"
    ]
    public init(mode: Mode = .diagnostic) { self.mode = mode }

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
            let operations: Set<String> = mode == .configuration ?
                Self.businessOperations : ["fixture", "runtime_probe"]
            guard let operation = operation, operations.contains(operation) else {
                throw ProbeError.invalidPayload
            }
            switch operation {
            case "config_load":
                guard Set(payload.keys) == ["operation"] else { throw ProbeError.invalidPayload }
            case "config_save":
                guard Set(payload.keys) == ["operation", "config"], let config = payload["config"] else {
                    throw ProbeError.invalidPayload
                }
                try ConfigurationDocument.validate(config)
            case "history_load", "history_add", "history_clear":
                _ = try message.encoded()
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
        let target = payload["request_id"]?.string
        let targetEntry = target.flatMap { entries[$0] }
        let historyPage: HistoryPageRequest?
        if operation == "history_load" { historyPage = try HistoryPageRequest(payload) }
        else { historyPage = nil }
        entries[message.id] = Entry(order: entries.count, type: message.type, operation: operation,
                                   https: payload["https"]?.bool ?? false,
                                   cancellationTarget: target,
                                   cancellationEligible: targetEntry.map {
                                       $0.type == "request" && !$0.started && !$0.terminal
                                   } ?? false, historyPage: historyPage)
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
                  payload["max_frame_bytes"] == .integer(65_536),
                  payload["fixture"] == .bool(mode == .diagnostic),
                  case let .array(capabilities)? = payload["capabilities"],
                  capabilities.count == (mode == .configuration ? Self.businessOperations.count : 2),
                  Set(capabilities.compactMap(\.string)) == (mode == .configuration ?
                      Self.businessOperations : ["fixture", "runtime_probe"]) else {
                throw ProbeError.invalidPayload
            }
            ready = true
        case "accepted":
            guard entry.type == "request", !entry.accepted, seq == 0,
                  Set(payload.keys) == ["operation"],
                  payload["operation"]?.string == entry.operation else { throw ProbeError.invalidTransition }
            entry.accepted = true
        case "started":
            guard mode == .configuration, entry.type == "request", entry.accepted,
                  !entry.started, !entry.cancellationConfirmed, seq == 1, Set(payload.keys) == ["operation"],
                  payload["operation"]?.string == entry.operation else {
                throw ProbeError.invalidTransition
            }
            guard !entries.values.contains(where: {
                $0.type == "request" && !$0.terminal && $0.order < entry.order
            }) else { throw ProbeError.invalidTransition }
            entry.started = true
        case "delta":
            guard entry.accepted, entry.operation == "fixture", fixturePayload(payload) else {
                throw ProbeError.invalidPayload
            }
        case "completed":
            switch entry.type {
            case "request":
                guard entry.accepted else { throw ProbeError.invalidTransition }
                if mode == .configuration {
                    guard entry.started, seq == 2 else { throw ProbeError.invalidTransition }
                    switch entry.operation {
                    case "config_load":
                        guard Set(payload.keys) == ["config"], let config = payload["config"] else {
                            throw ProbeError.invalidPayload
                        }
                        try ConfigurationDocument.validate(config)
                    case "config_save":
                        guard Set(payload.keys) == ["saved"], payload["saved"] == .bool(true) else {
                            throw ProbeError.invalidPayload
                        }
                    case "history_load":
                        guard let page = entry.historyPage else { throw ProbeError.invalidPayload }
                        try page.validatePage(payload)
                    case "history_add", "history_clear":
                        let field = entry.operation == "history_add" ? "recorded" : "cleared"
                        guard Set(payload.keys) == [field, "revision"], payload[field] == .bool(true),
                              HistoryDocument.validRevision(payload["revision"]) else {
                            throw ProbeError.invalidPayload
                        }
                    default: throw ProbeError.invalidPayload
                    }
                } else if entry.operation == "fixture" {
                    guard fixturePayload(payload) else { throw ProbeError.invalidPayload }
                } else {
                    guard runtimePayload(payload, https: entry.https) else {
                        throw ProbeError.invalidPayload
                    }
                }
            case "cancel":
                guard seq == 0, Set(payload.keys) == ["cancel_requested"],
                      payload["cancel_requested"]?.bool != nil else { throw ProbeError.invalidPayload }
                if mode == .configuration, payload["cancel_requested"] == .bool(true) {
                    guard entry.cancellationEligible,
                          let target = entry.cancellationTarget, let request = entries[target],
                          request.type == "request", request.accepted, !request.started,
                          !request.terminal || request.cancelled else {
                        throw ProbeError.invalidTransition
                    }
                    entries[target]?.cancellationConfirmed = true
                }
            case "shutdown":
                guard seq == 0, payload.isEmpty else { throw ProbeError.invalidPayload }
                if mode == .configuration, hasPendingConfiguration || hasPendingHistory {
                    throw ProbeError.invalidTransition
                }
            default: throw ProbeError.invalidTransition
            }
        case "cancelled":
            guard entry.type == "request", entry.accepted, !entry.started, payload.isEmpty else {
                throw ProbeError.invalidTransition
            }
        case "failed":
            guard validFailure(payload) else { throw ProbeError.invalidPayload }
            if mode == .configuration, entry.type == "hello" {
                guard seq == 0, ["config_in_use", "config_unavailable", "history_in_use",
                                 "history_unavailable", "state_io_failed"].contains(payload["code"]?.string ?? "") else {
                    throw ProbeError.invalidPayload
                }
            }
            if mode == .configuration, entry.type == "request", entry.accepted {
                guard entry.started, seq == 2 else { throw ProbeError.invalidTransition }
            }
        default: throw ProbeError.invalidEnvelope
        }
        entry.sequence += 1
        entry.terminal = event.isTerminal
        entry.cancelled = type == "cancelled"
        entries[id] = entry
        return event
    }

    private func fixturePayload(_ payload: [String: JSONValue]) -> Bool {
        Set(payload.keys) == ["text", "fixture"] &&
            payload["text"]?.string != nil && payload["fixture"] == .bool(true)
    }

    private func runtimePayload(_ payload: [String: JSONValue], https: Bool) -> Bool {
        guard Set(payload.keys) == ["python", "sqlite", "dictionary", "codex_config_fixture",
                                   "catalog_storage_fixture", "catalog_process_fixture", "ssl", "https"],
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
              payload["catalog_storage_fixture"] == .object([
                  "status": .string("passed"), "cli_simulated": .bool(true),
                  "cache_verified": .bool(true), "reopen_verified": .bool(true)
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
        let catalogExpected: JSONValue = platform == "darwin" && python["bundle_runtime"] == .bool(true) ?
            .object(["status": .string("passed"), "fixture": .bool(true), "process_verified": .bool(true),
                     "cache_verified": .bool(true), "reopen_verified": .bool(true)]) :
            .object(["status": .string("not_run")])
        guard payload["catalog_process_fixture"] == catalogExpected else { return false }
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
        if mode == .configuration { return HelperFailureCode(rawValue: code) != nil }
        return Self.validID(code)
    }
}

public struct LatestRequest {
    public private(set) var id: String?
    public init() {}
    public mutating func select(_ id: String?) { self.id = id }
    public func accepts(_ event: ServerEvent) -> Bool { id == event.id }
}
