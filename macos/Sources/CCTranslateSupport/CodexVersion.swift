import Foundation

public enum CodexVersionPolicy: String {
    case meetsMinimum = "meets minimum"
    case tooOld = "too old"
    case prerelease = "prerelease"
    case unrecognized = "unrecognized"
}

public struct CodexVersion: Equatable {
    public static let maxOutputBytes = 8192
    public static let minimum = "0.146.0"
    public let major: Int
    public let minor: Int
    public let patch: Int
    public let isPrerelease: Bool

    public var numericVersion: String { "\(major).\(minor).\(patch)" }
    public var policy: CodexVersionPolicy {
        if isPrerelease { return .prerelease }
        return (major, minor, patch) >= (0, 146, 0) ? .meetsMinimum : .tooOld
    }

    private static let component = "(0|[1-9][0-9]{0,8})"
    private static let identifier = "[0-9A-Za-z-]+"
    private static let pattern = try? NSRegularExpression(
        pattern: "\\Acodex-cli[ \\t]+" + component + "\\." + component + "\\." + component
            + "(?:-(" + identifier + "(?:\\." + identifier + ")*))?"
            + "(?:\\+" + identifier + "(?:\\." + identifier + ")*)?\\z"
    )
    // Match Python splitlines()/strip(), including its additional ASCII separators.
    private static let lineBreaks = CharacterSet(charactersIn:
        "\n\r\u{000B}\u{000C}\u{001C}\u{001D}\u{001E}\u{0085}\u{2028}\u{2029}")
    private static let whitespace = CharacterSet(charactersIn:
        "\u{0009}\u{000A}\u{000B}\u{000C}\u{000D}\u{001C}\u{001D}\u{001E}\u{001F} "
            + "\u{0085}\u{00A0}\u{1680}\u{2000}\u{2001}\u{2002}\u{2003}\u{2004}\u{2005}"
            + "\u{2006}\u{2007}\u{2008}\u{2009}\u{200A}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}")

    public static func parse(_ output: Data) -> CodexVersion? {
        guard output.count <= maxOutputBytes, let text = String(data: output, encoding: .utf8) else {
            return nil
        }
        let candidates = text.components(separatedBy: lineBreaks)
            .map { $0.trimmingCharacters(in: whitespace) }
            .filter { $0.hasPrefix("codex-cli") }
        guard candidates.count == 1, let candidate = candidates.first, let pattern = pattern,
              let match = pattern.firstMatch(in: candidate, range: NSRange(candidate.startIndex..., in: candidate))
        else { return nil }
        func group(_ index: Int) -> String? {
            guard let range = Range(match.range(at: index), in: candidate) else { return nil }
            return String(candidate[range])
        }
        guard let major = group(1).flatMap(Int.init), let minor = group(2).flatMap(Int.init),
              let patch = group(3).flatMap(Int.init) else { return nil }
        let prerelease = group(4)
        if let prerelease = prerelease, prerelease.split(separator: ".").contains(where: {
            $0.count > 1 && $0.first == "0" && $0.utf8.allSatisfy { (48...57).contains($0) }
        }) { return nil }
        return CodexVersion(major: major, minor: minor, patch: patch, isPrerelease: prerelease != nil)
    }
}

public struct CLIVersionResult: Equatable {
    public let codexVersion: CodexVersion?
    public var codexPolicy: CodexVersionPolicy { codexVersion?.policy ?? .unrecognized }

    public init(codexVersion: CodexVersion?) {
        self.codexVersion = codexVersion
    }

    public var codexStatus: String {
        let detected = codexVersion.map {
            $0.numericVersion + ($0.isPrerelease ? " (prerelease)" : "")
        } ?? "unrecognized"
        let action: String
        switch codexPolicy {
        case .meetsMinimum: action = "Actual protocol validation is still required."
        case .tooOld: action = "Update the selected Codex CLI to a supported stable release."
        case .prerelease: action = "Prereleases are not supported. Select a stable Codex CLI."
        case .unrecognized: action = "Choose a Codex CLI with recognizable --version output and probe again."
        }
        return """
        Detected Codex version: \(detected).
        Version policy: \(codexPolicy.rawValue). Minimum stable version: \(CodexVersion.minimum).
        \(action)
        Version does not prove authentication, protocol, or model compatibility. No model call.
        """
    }
}
