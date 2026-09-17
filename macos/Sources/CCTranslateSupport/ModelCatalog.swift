import Foundation

public struct CodexModelEntry: Hashable, Sendable {
    public let id: String
    public let name: String
    public let description: String

    public init(payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["id", "name", "description"],
              let id = payload["id"]?.string,
              !id.trimmingCharacters(in: TranslationDocument.whitespace).isEmpty, !id.contains("\0"),
              let name = payload["name"]?.string, !name.isEmpty,
              let description = payload["description"]?.string,
              try JSONValue.object(payload).encoded().count < LineFramer.maxFrameBytes else {
            throw ProbeError.invalidPayload
        }
        self.id = id
        self.name = name
        self.description = description
    }

    public func matchesID(_ candidate: String) -> Bool {
        id.utf8.elementsEqual(candidate.utf8)
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.matchesID(rhs.id) && lhs.name == rhs.name && lhs.description == rhs.description
    }

    public func hash(into hasher: inout Hasher) {
        // Swift String equality folds canonical Unicode equivalents; provider identifiers must not.
        hasher.combine(Data(id.utf8))
        hasher.combine(name)
        hasher.combine(description)
    }

    public static func decode(payload: [String: JSONValue]) throws -> [CodexModelEntry] {
        guard Set(payload.keys) == ["models"], case let .array(values)? = payload["models"],
              try JSONValue.object(payload).encoded().count < LineFramer.maxFrameBytes else {
            throw ProbeError.invalidPayload
        }
        var identities = Set<Data>()
        return try values.map { value in
            guard let object = value.object else { throw ProbeError.invalidPayload }
            let entry = try CodexModelEntry(payload: object)
            guard identities.insert(Data(entry.id.utf8)).inserted else { throw ProbeError.invalidPayload }
            return entry
        }
    }
}

enum ModelCatalogDocument {
    static let operation = "model_catalog"
    static let failureCodes: Set<String> = [
        "model_catalog_failed", "model_catalog_too_large", "provider_cleanup_failed"
    ]
    static let discoveryFailureCodes: Set<String> = ["model_catalog_failed", "model_catalog_too_large"]

    static func validateRequest(_ payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["operation"], payload["operation"] == .string(operation) else {
            throw ProbeError.invalidPayload
        }
    }
}
