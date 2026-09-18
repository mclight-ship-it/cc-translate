import Foundation
import CCTranslateSupport

enum TextInputPreflight {
    // Raw text protocol budget; independent of the configurable code point limit.
    static let maxBytes = TranslationDocument.maxInputBytes
    static let characterRange: ClosedRange<Int64> = 1...ConfigurationDocument.maxNumber

    enum Issue: Equatable {
        case empty, unconfirmedLimit
        case bytes(Int)
        case invalidLimit(Int64)
        case characters(count: Int, limit: Int64)

        @MainActor
        func message(using model: ProbeModel) -> String {
            switch self {
            case .empty:
                return model.text("Enter some text to translate.", "请输入要翻译的文字。")
            case .unconfirmedLimit:
                return model.text("The saved input limit is not confirmed. Reload it in Settings before translating.",
                                  "尚未确认已保存的输入长度限制，请在设置中重新读取后再翻译。")
            case .bytes(let count):
                return model.text("Input uses \(count) UTF-8 bytes; the limit is 8192. Shorten it; nothing was truncated or sent.",
                                  "输入占用 \(count) 个 UTF-8 字节，上限为 8192。请缩短文字；未截断或发送。")
            case .invalidLimit(let limit):
                return model.text("The saved input limit (\(limit)) is invalid. Set a positive whole number in Settings; nothing was sent.",
                                  "已保存的输入长度限制（\(limit)）无效。请在设置中保存正整数；未发送内容。")
            case .characters(let count, let limit):
                return model.text("Input has \(count) Unicode code points; the saved limit is \(limit). Shorten it or change Settings; nothing was truncated or sent.",
                                  "输入有 \(count) 个 Unicode 码点，已保存上限为 \(limit)。请缩短文字或更改设置；未截断或发送。")
            }
        }
    }

    static func check(_ text: String, limit: Int64?, requireLimit: Bool) -> Issue? {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .empty }
        let bytes = text.utf8.count
        guard bytes <= maxBytes else { return .bytes(bytes) }
        guard let limit else { return requireLimit ? .unconfirmedLimit : nil }
        guard characterRange.contains(limit) else { return .invalidLimit(limit) }
        let count = text.unicodeScalars.count
        return Int64(count) > limit ? .characters(count: count, limit: limit) : nil
    }
}
