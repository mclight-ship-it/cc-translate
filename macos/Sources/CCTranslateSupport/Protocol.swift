import Foundation

public enum ProbeError: String, Error, LocalizedError {
    case frameTooLarge, incompleteFrame, invalidJSON, invalidEnvelope
    case invalidID, reusedID, idLimit, invalidSequence, invalidTransition
    case invalidPayload, notReady, bundleMissing, launchFailed, readFailed, writeFailed
    case handshakeTimeout, requestTimeout, helperEOF, helperExited, helperProtocolError
    case stderrLimit, shuttingDown, cliTimeout, cliOutputLimit, cliFailed, cliCancelled
    case permissionDenied, secureInput, noDisplay, captureFailed, noImage, ocrFailed
    case configurationOutcomeUnknown, historyOutcomeUnknown, translationOutcomeUnknown, dictionaryOutcomeUnknown
    case translationUnavailable = "translation_unavailable"
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
        if DictionaryRequest.operations.contains(payload["operation"]?.string ?? "") {
            try DictionaryDocument.validateRequest(payload)
        }
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

public enum ResultAction: String, CaseIterable {
    case concise, formal, summary
    case explainCode = "explain_code"
    case asText = "as_text"
    case retranslate

    public var usesOriginalInput: Bool {
        switch self {
        case .explainCode, .asText, .retranslate: return true
        default: return false
        }
    }

    public func request(text: String, appLanguage: String, targetLanguage: String? = nil,
                        id: String = UUID().uuidString) -> ClientMessage {
        ClientMessage(id: id, type: "request", payload: [
            "operation": .string("result_action"), "action": .string(rawValue),
            "text": .string(text), "app_language": .string(appLanguage),
            "target_language": targetLanguage.map(JSONValue.string) ?? .null
        ])
    }

    public func acceptsInput(text: String, appLanguage: String, targetLanguage: String?) -> Bool {
        !text.trimmingCharacters(in: TranslationDocument.whitespace).isEmpty &&
            text.utf8.count <= TranslationDocument.maxTextBytes &&
            ["zh_CN", "en_US"].contains(appLanguage) &&
            (self == .retranslate ? TranslationDocument.targetLanguages.contains(targetLanguage ?? "") :
                targetLanguage == nil)
    }
}

enum TranslationDocument {
    static let modelOperations: Set<String> = ["translate", "result_action"]
    static let targetLanguages: Set<String> = ["zh", "en", "ja", "ko", "fr", "de", "es"]
    static let maxInputBytes = 8192
    static let maxDeltaBytes = 4096
    static let maxTextBytes = 24_000
    static let maxWireBytes = 1_048_576
    static let whitespace = CharacterSet.whitespacesAndNewlines.union(
        CharacterSet(charactersIn: "\u{001c}\u{001d}\u{001e}\u{001f}")
    )
    static let failureCodes: Set<String> = [
        "invalid_translation", "invalid_result_action", "translation_unavailable", "unsupported_provider",
        "invalid_translation_settings", "translation_timeout", "translation_output_limit",
        "provider_version_unsupported", "provider_version_unreadable", "provider_version_prerelease",
        "provider_cleanup_failed", "provider_protocol_error", "provider_failed"
    ]
    static let storageFailureCodes: Set<String> = [
        "config_in_use", "config_unavailable", "invalid_config", "config_io_failed",
        "history_in_use", "history_unavailable", "history_io_failed", "invalid_history",
        "history_too_large", "history_entry_too_large", "invalid_history_record",
        "invalid_history_cursor", "history_cursor_expired", "state_io_failed"
    ]

    static func validateRequest(_ payload: [String: JSONValue]) throws {
        if payload["operation"] == .string("result_action") {
            guard Set(payload.keys) == ["operation", "action", "text", "app_language", "target_language"],
                  let rawAction = payload["action"]?.string,
                  let action = ResultAction(rawValue: rawAction),
                  let text = payload["text"]?.string,
                  let language = payload["app_language"]?.string,
                  let target = payload["target_language"],
                  target == .null || target.string != nil,
                  action.acceptsInput(text: text, appLanguage: language, targetLanguage: target.string) else {
                throw ProbeError.invalidPayload
            }
            return
        }
        guard Set(payload.keys) == ["operation", "text", "app_language", "origin", "use_cache", "record_history"],
              payload["operation"] == .string("translate"),
              let text = payload["text"]?.string, !text.trimmingCharacters(in: whitespace).isEmpty,
              text.utf8.count <= maxInputBytes,
              let language = payload["app_language"]?.string, ["zh_CN", "en_US"].contains(language),
              let origin = payload["origin"]?.string, ["text", "selection"].contains(origin),
              payload["use_cache"]?.bool != nil, payload["record_history"]?.bool != nil else {
            throw ProbeError.invalidPayload
        }
    }

    static func validateCompletion(_ payload: [String: JSONValue], streamed: Bool,
                                   resultAction: Bool = false) throws {
        guard Set(payload.keys) == ["text", "submitted", "cached", "kind", "target_lang",
                                    "summarize", "history", "history_error"],
              let text = payload["text"]?.string, !text.trimmingCharacters(in: whitespace).isEmpty,
              let submitted = payload["submitted"]?.bool, !streamed || submitted,
              let cached = payload["cached"]?.bool,
              let kind = payload["kind"]?.string, ["text", "dict", "code"].contains(kind),
              payload["target_lang"] == .null ||
                targetLanguages.contains(payload["target_lang"]?.string ?? ""),
              payload["summarize"]?.bool != nil,
              let history = payload["history"]?.string,
              ["recorded", "disabled", "unchanged", "failed"].contains(history),
              !cached || (!submitted && history == "unchanged"),
              try JSONValue.string(text).encoded().count <= maxTextBytes else {
            throw ProbeError.invalidPayload
        }
        if history == "failed" {
            guard let code = payload["history_error"]?.string, storageFailureCodes.contains(code) else {
                throw ProbeError.invalidPayload
            }
        } else {
            guard payload["history_error"] == .null else { throw ProbeError.invalidPayload }
        }
        if resultAction {
            guard payload["cached"] == .bool(false), payload["summarize"] == .bool(false),
                  payload["history"] == .string("disabled"), payload["history_error"] == .null else {
                throw ProbeError.invalidPayload
            }
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
        if TranslationDocument.modelOperations.contains(payload["operation"]?.string ?? "") {
            try TranslationDocument.validateRequest(payload)
        }
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

    public var safeFailureMessage: String {
        let action: String
        switch HelperFailureCode(rawValue: safeFailureCode) {
        case .providerVersionUnsupported:
            action = "The selected Codex CLI is too old. Select stable Codex \(CodexVersion.minimum) or newer and run the explicit --version probe."
        case .providerVersionUnreadable:
            action = "The Codex version could not be recognized. Choose a Codex executable and run the explicit --version probe; stable \(CodexVersion.minimum) or newer is required."
        case .providerVersionPrerelease:
            action = "Codex prereleases are not supported. Select stable Codex \(CodexVersion.minimum) or newer and run the explicit --version probe."
        case .providerProtocolError:
            action = payload["submitted"] == .bool(false)
                ? "The Codex app-server response failed strict protocol validation before model submission. Check the selected Codex installation. Meeting the version minimum does not prove protocol compatibility."
                : "The Codex app-server response failed strict protocol validation after possible model submission. Do not replay this request; its remote outcome is unknown."
        default:
            return "Helper request failed: \(safeFailureCode). No fallback or automatic retry."
        }
        return "Helper request failed: \(safeFailureCode). \(action) No fallback or automatic retry."
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
    case invalidTranslation = "invalid_translation"
    case invalidResultAction = "invalid_result_action"
    case translationUnavailable = "translation_unavailable"
    case unsupportedProvider = "unsupported_provider"
    case invalidTranslationSettings = "invalid_translation_settings"
    case translationTimeout = "translation_timeout"
    case translationOutputLimit = "translation_output_limit"
    case providerVersionUnsupported = "provider_version_unsupported"
    case providerVersionUnreadable = "provider_version_unreadable"
    case providerVersionPrerelease = "provider_version_prerelease"
    case providerCleanupFailed = "provider_cleanup_failed"
    case providerProtocolError = "provider_protocol_error"
    case providerFailed = "provider_failed"
    case invalidDictionary = "invalid_dictionary"
    case dictionaryUnavailable = "dictionary_unavailable"
    case dictionaryIOFailed = "dictionary_io_failed"
    case dictionaryBusy = "dictionary_busy"
    case invalidDictionaryTicket = "invalid_dictionary_ticket"
    case dictionaryInstallFailed = "dictionary_install_failed"
    case dictionaryOutputLimit = "dictionary_output_limit"
    case dictionaryCleanupFailed = "dictionary_cleanup_failed"
}

public struct ProtocolState {
    public enum Mode { case diagnostic, configuration, translation }

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
        var wireBytes = 0
        var deltaText = ""
        var isModelRequest: Bool { TranslationDocument.modelOperations.contains(operation ?? "") }
        var isDictionaryRequest: Bool { DictionaryRequest.operations.contains(operation ?? "") }
        var cancellableAfterStart: Bool { isModelRequest || isDictionaryRequest }
    }
    private var entries: [String: Entry] = [:]
    public private(set) var ready = false
    public private(set) var closing = false
    public var registeredCount: Int { entries.count }
    public let mode: Mode
    public var isBusiness: Bool { mode != .diagnostic }
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
    public var hasPendingTranslation: Bool {
        entries.values.contains { !$0.terminal && $0.isModelRequest }
    }
    public var hasPendingDictionary: Bool {
        entries.values.contains { !$0.terminal && $0.isDictionaryRequest }
    }
    var pendingOutcomeUnknown: ProbeError? {
        if hasPendingTranslation { return .translationOutcomeUnknown }
        if hasPendingDictionary { return .dictionaryOutcomeUnknown }
        if hasPendingConfiguration { return .configurationOutcomeUnknown }
        if hasPendingHistory { return .historyOutcomeUnknown }
        return nil
    }
    private static let businessOperations: Set<String> = [
        "config_load", "config_save", "history_load", "history_add", "history_clear"
    ]
    private var operations: Set<String> {
        switch mode {
        case .diagnostic: return ["fixture", "runtime_probe"]
        case .configuration: return Self.businessOperations.union(DictionaryRequest.operations)
        case .translation:
            return Self.businessOperations.union(DictionaryRequest.operations).union(TranslationDocument.modelOperations)
        }
    }
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
            guard let operation = operation, operations.contains(operation) else {
                throw ProbeError.invalidPayload
            }
            switch operation {
            case "translate", "result_action":
                _ = try message.encoded()
            case let operation where DictionaryRequest.operations.contains(operation):
                try DictionaryDocument.validateRequest(payload)
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
                                       $0.type == "request" && !$0.terminal &&
                                           (!$0.started || $0.cancellableAfterStart)
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
        if entry.isModelRequest {
            guard frame.count + 1 <= TranslationDocument.maxWireBytes - entry.wireBytes else {
                throw ProbeError.invalidPayload
            }
            entry.wireBytes += frame.count + 1
        }
        let event = ServerEvent(id: id, sequence: seq, type: type, payload: payload)
        switch type {
        case "ready":
            let fields: Set<String> = mode == .translation ?
                ["protocol", "capabilities", "max_frame_bytes", "fixture", "backend"] :
                ["protocol", "capabilities", "max_frame_bytes", "fixture"]
            guard entry.type == "hello", seq == 0,
                  Set(payload.keys) == fields,
                  payload["protocol"] == .integer(1),
                  payload["max_frame_bytes"] == .integer(65_536),
                  payload["fixture"] == .bool(mode == .diagnostic),
                  mode != .translation || payload["backend"] == .string("native_appserver"),
                  case let .array(capabilities)? = payload["capabilities"],
                  capabilities.count == operations.count,
                  Set(capabilities.compactMap(\.string)) == operations else {
                throw ProbeError.invalidPayload
            }
            ready = true
        case "accepted":
            guard entry.type == "request", !entry.accepted, seq == 0,
                  Set(payload.keys) == ["operation"],
                  payload["operation"]?.string == entry.operation else { throw ProbeError.invalidTransition }
            entry.accepted = true
        case "started":
            guard isBusiness, entry.type == "request", entry.accepted,
                  !entry.started, !entry.cancellationConfirmed, seq == 1, Set(payload.keys) == ["operation"],
                  payload["operation"]?.string == entry.operation else {
                throw ProbeError.invalidTransition
            }
            if !entry.isModelRequest {
                guard !entries.values.contains(where: {
                    $0.type == "request" && !$0.terminal && $0.order < entry.order &&
                        !$0.isModelRequest && $0.isDictionaryRequest == entry.isDictionaryRequest
                }) else { throw ProbeError.invalidTransition }
            }
            entry.started = true
        case "delta":
            if entry.isModelRequest {
                guard entry.started, seq >= 2, Set(payload.keys) == ["text", "submitted"],
                      let text = payload["text"]?.string, !text.isEmpty,
                      payload["submitted"] == .bool(true),
                      try JSONValue.string(text).encoded().count <= TranslationDocument.maxDeltaBytes else {
                    throw ProbeError.invalidPayload
                }
                let combined = entry.deltaText + text
                guard try JSONValue.string(combined).encoded().count <= TranslationDocument.maxTextBytes else {
                    throw ProbeError.invalidPayload
                }
                entry.deltaText = combined
            } else {
                guard entry.accepted, entry.operation == "fixture", fixturePayload(payload) else {
                    throw ProbeError.invalidPayload
                }
            }
        case "completed":
            switch entry.type {
            case "request":
                guard entry.accepted else { throw ProbeError.invalidTransition }
                if entry.isModelRequest {
                    guard entry.started, seq >= 2 else { throw ProbeError.invalidTransition }
                    try TranslationDocument.validateCompletion(
                        payload, streamed: !entry.deltaText.isEmpty,
                        resultAction: entry.operation == "result_action")
                } else if entry.isDictionaryRequest {
                    guard entry.started, seq == 2 else { throw ProbeError.invalidTransition }
                    try DictionaryDocument.validateCompletion(payload, operation: entry.operation)
                } else if isBusiness {
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
                if isBusiness, payload["cancel_requested"] == .bool(true) {
                    guard entry.cancellationEligible,
                          let target = entry.cancellationTarget, let request = entries[target],
                          request.type == "request", request.accepted else {
                        throw ProbeError.invalidTransition
                    }
                    // Independently running jobs may finish before their cancel acknowledgement arrives.
                    if !request.cancellableAfterStart || !request.started {
                        guard !request.started, !request.terminal || request.cancelled else {
                            throw ProbeError.invalidTransition
                        }
                    }
                    entries[target]?.cancellationConfirmed = true
                }
            case "shutdown":
                guard seq == 0, payload.isEmpty else { throw ProbeError.invalidPayload }
                if isBusiness, hasPendingConfiguration || hasPendingHistory ||
                    hasPendingTranslation || hasPendingDictionary {
                    throw ProbeError.invalidTransition
                }
            default: throw ProbeError.invalidTransition
            }
        case "cancelled":
            guard entry.type == "request", entry.accepted else {
                throw ProbeError.invalidTransition
            }
            if entry.isModelRequest, entry.started {
                guard seq >= 2, Set(payload.keys) == ["submitted"],
                      let submitted = payload["submitted"]?.bool,
                      entry.deltaText.isEmpty || submitted else { throw ProbeError.invalidPayload }
            } else if entry.isDictionaryRequest, entry.started {
                guard seq == 2, payload.isEmpty else { throw ProbeError.invalidTransition }
            } else {
                guard !entry.started, payload.isEmpty else { throw ProbeError.invalidTransition }
            }
        case "failed":
            if entry.isModelRequest, entry.started {
                guard seq >= 2, Set(payload.keys) == ["code", "submitted"],
                      let code = payload["code"]?.string,
                      TranslationDocument.failureCodes.union(TranslationDocument.storageFailureCodes).contains(code),
                      let submitted = payload["submitted"]?.bool,
                      entry.deltaText.isEmpty || submitted else { throw ProbeError.invalidPayload }
            } else {
                guard validFailure(payload) else { throw ProbeError.invalidPayload }
            }
            if isBusiness, entry.type == "shutdown" {
                guard seq == 0,
                      payload["code"] == .string("state_io_failed") ||
                        payload["code"] == .string("dictionary_cleanup_failed") ||
                        (mode == .translation && payload["code"] == .string("provider_cleanup_failed")) else {
                    throw ProbeError.invalidPayload
                }
            }
            if isBusiness, entry.type == "hello" {
                var startupCodes: Set<String> = [
                    "config_in_use", "config_unavailable", "history_in_use", "history_unavailable", "state_io_failed"
                ]
                if mode == .translation { startupCodes.insert("translation_unavailable") }
                guard seq == 0, startupCodes.contains(payload["code"]?.string ?? "") else {
                    throw ProbeError.invalidPayload
                }
            }
            if isBusiness, entry.type == "request" {
                if payload["code"] == .string("worker_start_failed") ||
                    (entry.isDictionaryRequest && !entry.started &&
                     payload["code"] == .string("dictionary_cleanup_failed")) {
                    guard entry.accepted, !entry.started, seq == 1 else {
                        throw ProbeError.invalidTransition
                    }
                } else if entry.accepted {
                    guard entry.started, entry.isModelRequest || seq == 2 else {
                        throw ProbeError.invalidTransition
                    }
                }
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
        if isBusiness {
            return HelperFailureCode(rawValue: code) != nil &&
                (mode == .translation || !TranslationDocument.failureCodes.contains(code))
        }
        return Self.validID(code)
    }
}

public struct LatestRequest {
    public private(set) var id: String?
    public init() {}
    public mutating func select(_ id: String?) { self.id = id }
    public func accepts(_ event: ServerEvent) -> Bool { id == event.id }
}
