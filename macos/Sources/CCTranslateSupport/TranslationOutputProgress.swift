import Foundation

/// Structural evidence only: completion means a nonempty summary was followed by
/// its contract's translation heading, not that the summary is semantically complete.
public struct TranslationOutputProgress: Equatable {
    /// The first nonblank line is a complete, delimited summary-contract heading.
    public let hasSummary: Bool
    public let summaryComplete: Bool
    public let hasMeaningfulContent: Bool

    private static let headings = [
        ("摘要", "译文"), ("Summary", "Translation"), ("要約", "翻訳"),
        ("요약", "번역"), ("Résumé", "Traduction"),
        ("Zusammenfassung", "Übersetzung"), ("Resumen", "Traducción")
    ]

    /// Examine the current generated text, not a previous result/action UI prefix.
    /// A trailing streaming heading needs a newline before it is a section boundary.
    public static func inspect(_ text: String, isFinal: Bool = false) -> Self {
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let lines = normalized.components(separatedBy: "\n")
        var summary = false
        var complete = false
        var meaningful = false
        var summaryContent = false
        var sawNonblankLine = false
        var expectedTranslation: String?
        var fence: (marker: Character, count: Int)?
        for (index, raw) in lines.enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            let terminated = index < lines.count - 1 || isFinal
            let startsOutput = !sawNonblankLine
            if !line.isEmpty { sawNonblankLine = true }
            if let active = fence {
                let count = line.prefix(while: { $0 == active.marker }).count
                if count >= active.count &&
                    line.dropFirst(count).trimmingCharacters(in: .whitespaces).isEmpty {
                    fence = nil
                } else if containsContent(line) {
                    meaningful = true
                    if expectedTranslation != nil { summaryContent = true }
                }
                continue
            }
            if let marker = line.first, marker == "`" || marker == "~" {
                let count = line.prefix(while: { $0 == marker }).count
                if count >= 3 {
                    fence = (marker, count)
                    continue
                }
            }
            let hashes = line.prefix(while: { $0 == "#" }).count
            if hashes > 0 {
                // Even a partial/unknown heading is not generated body content.
                let indentation = raw.prefix(while: { $0 == " " }).count
                guard indentation <= 3, raw.dropFirst(indentation).hasPrefix("#"),
                      hashes <= 6, line.dropFirst(hashes).first?.isWhitespace == true else { continue }
                if hashes == 1 {
                    if terminated { expectedTranslation = nil }
                    continue
                }
                guard hashes == 2 else { continue }
                let title = line.dropFirst(hashes).trimmingCharacters(in: .whitespaces)
                    .replacingOccurrences(of: "[\\t ]+#+$", with: "", options: .regularExpression)
                let pair = headings.first(where: { $0.0 == title })
                if startsOutput, terminated, let pair {
                    summary = true
                    expectedTranslation = pair.1
                    summaryContent = false
                } else if let pair, pair.1 == expectedTranslation {
                    // Repeated contract titles are not new summary content or a reset.
                    continue
                } else if terminated, title == expectedTranslation {
                    complete = complete || summaryContent
                    expectedTranslation = nil
                } else if terminated {
                    expectedTranslation = nil
                }
                continue
            }
            if containsContent(line) {
                meaningful = true
                if expectedTranslation != nil { summaryContent = true }
            }
        }
        return Self(hasSummary: summary, summaryComplete: complete, hasMeaningfulContent: meaningful)
    }

    private static func containsContent(_ line: String) -> Bool {
        // Markdown-only deltas (including an unfinished numbered-list marker) are
        // not first content; ordinary prose, CJK, code and symbols still count.
        if line.last == "." || line.last == ")" {
            let number = line.dropLast()
            if !number.isEmpty && number.allSatisfy(\.isNumber) { return false }
        }
        let markers = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: "#*_`~>-+|[]()\\!"))
        return !line.trimmingCharacters(in: markers).isEmpty
    }
}
