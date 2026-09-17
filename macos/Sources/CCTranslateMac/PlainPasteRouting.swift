import AppKit

enum PlainPasteDestination: Equatable { case ownApplication, externalApplication, unavailable }

enum PlainPasteRoute: Equatable {
    case ownApplication(dispatched: Bool)
    case externalApplication
    case unavailable
}

@MainActor
protocol PlainPasteRouting {
    func destination() -> PlainPasteDestination
    func pasteInOwnApplication() -> Bool
}

@MainActor
final class NativePlainPasteRouting: PlainPasteRouting {
    static let action = #selector(NSTextView.pasteAsPlainText(_:))
    private let foreground: () -> PlainPasteDestination
    private let sendAction: (Selector, Any?, Any?) -> Bool

    init(foreground: (() -> PlainPasteDestination)? = nil,
         sendAction: ((Selector, Any?, Any?) -> Bool)? = nil) {
        self.foreground = foreground ?? {
            Self.destination(ownKeyWindow: NSApp?.keyWindow?.isKeyWindow == true,
                             frontmostPID: NSWorkspace.shared.frontmostApplication?.processIdentifier)
        }
        self.sendAction = sendAction ?? { action, target, sender in
            NSApp?.sendAction(action, to: target, from: sender) ?? false
        }
    }

    func destination() -> PlainPasteDestination { foreground() }

    static func destination(ownKeyWindow: Bool, frontmostPID: pid_t?) -> PlainPasteDestination {
        // A nonactivating result panel can own keyboard focus while another app remains frontmost.
        if ownKeyWindow { return .ownApplication }
        guard let frontmostPID else { return .unavailable }
        return frontmostPID == ProcessInfo.processInfo.processIdentifier ? .ownApplication : .externalApplication
    }

    func pasteInOwnApplication() -> Bool {
        // A nil target preserves the current field editor and native responder chain.
        // Dispatch is not evidence that text was inserted; never retry through the external service.
        sendAction(Self.action, nil, nil)
    }
}
