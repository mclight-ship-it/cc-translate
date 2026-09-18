public enum TranslationProvider: String, CaseIterable, Sendable {
    case codex = "codex_cli"
    case claude = "claude_cli"

    public var cliName: String {
        switch self {
        case .codex: return "codex"
        case .claude: return "claude"
        }
    }

    public var displayName: String {
        switch self {
        case .codex: return "Codex"
        case .claude: return "Claude"
        }
    }

    public var modelKey: String {
        switch self {
        case .codex: return "codex_model"
        case .claude: return "claude_model"
        }
    }

    var commandArgument: String { "--\(cliName)-command" }
    var environmentKey: String { "CC_TRANSLATE_\(cliName.uppercased())_ENV" }
    var backend: String { self == .codex ? "native_appserver" : "native_print" }
}
