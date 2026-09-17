import Foundation

public enum DictionaryRequest: Equatable {
    case status
    case lookup(text: String, appLanguage: String, origin: String, useCache: Bool, recordHistory: Bool)
    case prepareInstall
    case install(ticket: String)
    case discardInstall(ticket: String)
    case delete

    public static let operations: Set<String> = [
        "dictionary_status", "dictionary_lookup", "dictionary_prepare_install",
        "dictionary_install", "dictionary_discard_install", "dictionary_delete"
    ]

    public var operation: String {
        switch self {
        case .status: return "dictionary_status"
        case .lookup: return "dictionary_lookup"
        case .prepareInstall: return "dictionary_prepare_install"
        case .install: return "dictionary_install"
        case .discardInstall: return "dictionary_discard_install"
        case .delete: return "dictionary_delete"
        }
    }

    public var payload: [String: JSONValue] {
        var result: [String: JSONValue] = ["operation": .string(operation)]
        switch self {
        case let .lookup(text, appLanguage, origin, useCache, recordHistory):
            result["text"] = .string(text)
            result["app_language"] = .string(appLanguage)
            result["origin"] = .string(origin)
            result["use_cache"] = .bool(useCache)
            result["record_history"] = .bool(recordHistory)
        case let .install(ticket), let .discardInstall(ticket):
            result["ticket"] = .string(ticket)
        default: break
        }
        return result
    }
}

public struct DictionaryStatus: Equatable {
    public enum State: String { case notInstalled = "not_installed", ready, invalid }

    public let state: State
    public let enabled: Bool
    public let size: Int64
    public let sha256: String
    public let dataVersion: String
    public let downloadURL: URL
    public let entryCount: Int64

    public init(payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["state", "enabled", "size", "sha256", "data_version",
                                    "download_url", "entry_count"],
              let state = State(rawValue: payload["state"]?.string ?? ""),
              let enabled = payload["enabled"]?.bool,
              let entries = payload["entry_count"]?.integer, entries >= 0 else {
            throw ProbeError.invalidPayload
        }
        let metadata = try DictionaryMetadata(payload, urlField: "download_url")
        self.state = state
        self.enabled = enabled
        self.size = metadata.size
        self.sha256 = metadata.sha256
        self.dataVersion = metadata.dataVersion
        self.downloadURL = metadata.url
        self.entryCount = entries
    }
}

public struct DictionaryInstallTicket: Equatable {
    public let ticket: String
    public let path: URL
    public let url: URL
    public let size: Int64
    public let sha256: String
    public let dataVersion: String

    public init(payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["ticket", "path", "url", "size", "sha256", "data_version"],
              let ticket = payload["ticket"]?.string, DictionaryDocument.validTicket(ticket),
              let path = payload["path"]?.string, path.hasPrefix("/"), !path.contains("\0") else {
            throw ProbeError.invalidPayload
        }
        let metadata = try DictionaryMetadata(payload, urlField: "url")
        self.ticket = ticket
        self.path = URL(fileURLWithPath: path)
        self.url = metadata.url
        self.size = metadata.size
        self.sha256 = metadata.sha256
        self.dataVersion = metadata.dataVersion
    }
}

public struct DictionarySource: Hashable, Sendable {
    public let id: String
    public let label: String
    public let version: String
    public let license: String

    public init(payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["id", "label", "version", "license"],
              let id = payload["id"]?.string, let label = payload["label"]?.string,
              let version = payload["version"]?.string, let license = payload["license"]?.string else {
            throw ProbeError.invalidPayload
        }
        self.id = id
        self.label = label
        self.version = version
        self.license = license
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id.utf8.elementsEqual(rhs.id.utf8) && lhs.label.utf8.elementsEqual(rhs.label.utf8) &&
            lhs.version.utf8.elementsEqual(rhs.version.utf8) && lhs.license.utf8.elementsEqual(rhs.license.utf8)
    }

    public func hash(into hasher: inout Hasher) {
        for value in [id, label, version, license] { hasher.combine(Data(value.utf8)) }
    }
}

public struct DictionaryLookupResult: Equatable {
    public let status: String
    public let result: [String: JSONValue]?
    public let sources: [DictionarySource]

    public init(payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["status", "result"],
              try JSONValue.object(payload).encoded().count < LineFramer.maxFrameBytes,
              let status = payload["status"]?.string,
              ["hit", "miss", "disabled", "unavailable", "ineligible"].contains(status) else {
            throw ProbeError.invalidPayload
        }
        if status == "hit" {
            guard let result = payload["result"]?.object,
                  result["submitted"] == .bool(false), result["kind"] == .string("dict"),
                  result["target_lang"] == .null, result["summarize"] == .bool(false) else {
                throw ProbeError.invalidPayload
            }
            var translation = result
            if let details = translation.removeValue(forKey: "source_details") {
                guard case .array(let rows) = details else { throw ProbeError.invalidPayload }
                let sources = try rows.map { value -> DictionarySource in
                    guard let row = value.object else { throw ProbeError.invalidPayload }
                    return try DictionarySource(payload: row)
                }
                guard Set(sources).count == sources.count else { throw ProbeError.invalidPayload }
                self.sources = sources
            } else {
                self.sources = []
            }
            try TranslationDocument.validateCompletion(translation, streamed: false)
            self.result = result
        } else {
            guard payload["result"] == .null else { throw ProbeError.invalidPayload }
            self.result = nil
            self.sources = []
        }
        self.status = status
    }
}

private struct DictionaryMetadata {
    let size: Int64
    let sha256: String
    let dataVersion: String
    let url: URL

    init(_ payload: [String: JSONValue], urlField: String) throws {
        guard let size = payload["size"]?.integer, size > 0,
              HistoryDocument.validRevision(payload["sha256"]),
              let sha256 = payload["sha256"]?.string,
              let version = payload["data_version"]?.string, !version.isEmpty,
              let rawURL = payload[urlField]?.string, let url = URL(string: rawURL),
              url.scheme == "https", let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil else {
            throw ProbeError.invalidPayload
        }
        self.size = size
        self.sha256 = sha256
        self.dataVersion = version
        self.url = url
    }
}

enum DictionaryDocument {
    static func validTicket(_ ticket: String) -> Bool {
        ticket.utf8.count == 32 && ticket.utf8.allSatisfy {
            (48...57).contains($0) || (97...102).contains($0)
        }
    }

    static func validateRequest(_ payload: [String: JSONValue]) throws {
        switch payload["operation"]?.string {
        case "dictionary_status", "dictionary_prepare_install", "dictionary_delete":
            guard Set(payload.keys) == ["operation"] else { throw ProbeError.invalidPayload }
        case "dictionary_install", "dictionary_discard_install":
            guard Set(payload.keys) == ["operation", "ticket"],
                  let ticket = payload["ticket"]?.string, validTicket(ticket) else {
                throw ProbeError.invalidPayload
            }
        case "dictionary_lookup":
            guard let origin = payload["origin"]?.string, ["text", "selection"].contains(origin) else {
                throw ProbeError.invalidPayload
            }
            var translation = payload
            translation["operation"] = .string("translate")
            try TranslationDocument.validateRequest(translation)
        default: throw ProbeError.invalidPayload
        }
    }

    static func validateCompletion(_ payload: [String: JSONValue], operation: String?) throws {
        switch operation {
        case "dictionary_status":
            _ = try DictionaryStatus(payload: payload)
        case "dictionary_install":
            let status = try DictionaryStatus(payload: payload)
            guard status.state == .ready, status.enabled else { throw ProbeError.invalidPayload }
        case "dictionary_lookup":
            _ = try DictionaryLookupResult(payload: payload)
        case "dictionary_prepare_install":
            _ = try DictionaryInstallTicket(payload: payload)
        case "dictionary_discard_install":
            guard Set(payload.keys) == ["discarded"], payload["discarded"] == .bool(true) else {
                throw ProbeError.invalidPayload
            }
        case "dictionary_delete":
            guard Set(payload.keys) == ["deleted", "enabled"], payload["deleted"]?.bool != nil,
                  payload["enabled"] == .bool(false) else { throw ProbeError.invalidPayload }
        default: throw ProbeError.invalidPayload
        }
    }
}
