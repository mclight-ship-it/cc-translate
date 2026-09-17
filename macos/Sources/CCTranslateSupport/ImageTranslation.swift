import Foundation

enum ImageTranslationDocument {
    static let operation = "translate_image"
    static let failureCodes: Set<String> = [
        "invalid_image_translation", "image_unavailable", "image_too_large", "image_changed", "image_cleanup_failed"
    ]

    static func validateRequest(_ payload: [String: JSONValue]) throws {
        guard Set(payload.keys) == ["operation", "image_path", "image_bytes", "image_sha256", "app_language", "record_history"],
              payload["operation"] == .string(operation),
              let path = payload["image_path"]?.string, path.hasPrefix("/"), !path.contains("\0"),
              let bytes = payload["image_bytes"]?.integer,
              bytes > 0, bytes <= Int64(ImageTranslationAttachment.maxBytes),
              let digest = payload["image_sha256"]?.string, digest.utf8.count == 64,
              digest.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              let language = payload["app_language"]?.string, ["en_US", "zh_CN"].contains(language),
              payload["record_history"]?.bool != nil else { throw ProbeError.invalidPayload }
    }

    static func validateCompletion(_ payload: [String: JSONValue]) throws {
        guard payload["kind"] == .string("ocr"), payload["cached"] == .bool(false),
              payload["summarize"] == .bool(false), payload["submitted"] == .bool(true),
              payload["history"] != .string("unchanged") else { throw ProbeError.invalidPayload }
    }
}
