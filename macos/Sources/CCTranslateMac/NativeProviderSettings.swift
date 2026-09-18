import CCTranslateSupport

extension TranslationProvider {
    var modelPresets: [String] {
        switch self {
        case .codex: return ["auto-fast", "auto"]
        case .claude: return ["haiku", "sonnet", "opus"]
        }
    }

    var defaultModel: String {
        switch self {
        case .codex: return "auto-fast"
        case .claude: return "haiku"
        }
    }

    var pathPreferenceKey: String {
        self == .codex ? "selectedCodexPath" : "selectedClaudePath"
    }

    var customModelPreferenceKey: String {
        self == .codex ? "lastCustomCodexModel" : "lastCustomClaudeModel"
    }
}
