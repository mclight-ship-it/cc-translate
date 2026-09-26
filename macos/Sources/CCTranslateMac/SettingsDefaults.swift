import Foundation
import CCTranslateSupport

enum SettingsDefaultsPhase: Equatable {
    case idle, loading, confirming, saving, readingBack, restored, shortcutCleanupRequired
    case differentReadback, failed(String)

    var busy: Bool {
        switch self {
        case .loading, .saving, .readingBack: return true
        default: return false
        }
    }
}

struct SettingsDefaults {
    private(set) var values: [String: JSONValue]

    init?(_ config: [String: JSONValue]) {
        let booleans = ["history_enabled", "summary_enabled", "local_dictionary_enabled",
                        "plain_text_paste_enabled", "codex_model_default_migrated"]
        let strings = ["model_provider", "codex_model", "claude_model", "direction"]
        guard booleans.allSatisfy({ config[$0]?.bool != nil }),
              strings.allSatisfy({ config[$0]?.string?.isEmpty == false }),
              let limit = config["history_limit"]?.integer, HistoryLimitPreference.supported.contains(limit),
              let characters = config["max_chars"]?.integer, TextInputPreflight.characterRange.contains(characters),
              let interval = config["double_press_window"]?.number,
              DoubleCopyInterval(seconds: interval) != nil else { return nil }
        let keys = booleans + strings + ["history_limit", "max_chars", "double_press_window"]
        values = config.filter { keys.contains($0.key) }
    }

    func merging(into config: [String: JSONValue]) -> [String: JSONValue] {
        config.merging(values) { _, canonical in canonical }
    }

    func matches(_ config: [String: JSONValue]) -> Bool {
        values.allSatisfy { key, value in
            if key == "double_press_window" { return config[key]?.number == value.number }
            if let string = value.string { return CodexModelSettings.sameID(config[key]?.string, string) }
            return config[key] == value
        }
    }
}
