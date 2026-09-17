import Foundation

struct PasteboardTextRepresentation {
    let type: String
    private let encoding: String.Encoding

    private static let encodings: [String: String.Encoding] = [
        "public.utf16-external-plain-text": .utf16,
        "public.utf16-plain-text": .utf16,
        "public.utf8-plain-text": .utf8,
        "public.utf8-tab-separated-values-text": .utf8,
        "com.apple.traditional-mac-plain-text": .macOSRoman
    ]

    static func preferred(in types: [String]) -> PasteboardTextRepresentation? {
        // Converted aliases may follow the source type. Preserve each item's order,
        // rather than selecting a conversion using a global encoding preference.
        for type in types {
            if let encoding = encodings[type] {
                return PasteboardTextRepresentation(type: type, encoding: encoding)
            }
        }
        return nil
    }

    func decode(_ data: Data) -> String? {
        if encoding == .utf8 {
            // Foundation can consume an initial U+FEFF. Swift preserves it, and the
            // byte round-trip rejects malformed UTF-8 instead of accepting repairs.
            let text = String(decoding: data, as: UTF8.self)
            return text.utf8.elementsEqual(data) ? text : nil
        }
        if type == "public.utf16-plain-text", !data.starts(with: [0xFF, 0xFE]), !data.starts(with: [0xFE, 0xFF]) {
            let native: String.Encoding = UInt16(littleEndian: 1) == 1 ? .utf16LittleEndian : .utf16BigEndian
            return String(data: data, encoding: native)
        }
        return String(data: data, encoding: encoding)
    }
}
