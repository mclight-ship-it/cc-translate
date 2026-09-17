import AppKit
import ApplicationServices
import Carbon

@MainActor
final class SystemFreshCopyClipboard: FreshCopyClipboard {
    private let pasteboard: () -> NSPasteboard
    private static let textRepresentations: Set<NSPasteboard.PasteboardType> = [
        .string, .rtf, .html, .tabularText,
        .init("public.utf16-plain-text"), .init("public.utf16-external-plain-text"),
        .init("com.apple.traditional-mac-plain-text")
    ]

    init(pasteboard: @escaping () -> NSPasteboard = { .general }) {
        self.pasteboard = pasteboard
    }

    func revision() -> Int { pasteboard().changeCount }

    func read(revision: Int, whileValid: () -> Bool) -> SelectionResult {
        guard whileValid() else { return .unknown(.clipboardChanged) }
        let board = pasteboard()
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        guard let items = board.pasteboardItems, items.count == 1, let item = items.first else {
            return .unknown(.clipboardUnsupported)
        }
        let types = item.types
        guard Set(types).isSubset(of: Self.textRepresentations),
              let representation = PasteboardTextRepresentation.preferred(in: types.map(\.rawValue)) else {
            return .unknown(.clipboardUnsupported)
        }
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        // Read only this fresh item, never a board-level string or a rich/file fallback.
        let data = item.data(forType: .init(representation.type))
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        guard let data else { return .unknown(.clipboardUnavailable) }
        // UTF-16 can use two bytes per ASCII character plus a two-byte BOM.
        guard data.count <= 16_386 else { return .unknown(.tooLarge) }
        guard let text = representation.decode(data) else { return .unknown(.clipboardUnavailable) }
        guard text.utf8.count <= 8192 else { return .unknown(.tooLarge) }
        guard !text.contains("\0"),
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return .unknown(.clipboardUnavailable)
        }
        return .present(text)
    }
}

@MainActor
final class PassiveCopySourceResolver {
    private var previous: (pid: pid_t, launch: Date, element: AXUIElement, identity: UUID)?
    private var generation = UUID()

    func reset() { generation = UUID(); previous = nil }

    func capture(requireFocusIdentity: Bool) -> PassiveCopySource? {
        let generation = UUID()
        self.generation = generation
        guard let target = SelectionProbe.currentTarget(),
              let app = NSRunningApplication(processIdentifier: target.pid), !app.isTerminated else {
            if self.generation == generation { reset() }
            return nil
        }
        let uncorrelated = PassiveCopySource(target: target, focusIdentity: nil)
        guard requireFocusIdentity else { return uncorrelated }
        let application = AXUIElementCreateApplication(target.pid)
        var value: CFTypeRef?
        let launch = app.launchDate
        let fetched = launch != nil && AXUIElementSetMessagingTimeout(application, 0.1) == .success &&
            AXUIElementCopyAttributeValue(application, kAXFocusedUIElementAttribute as CFString, &value) == .success
        let sameTarget = SelectionProbe.currentTarget() == target && !app.isTerminated
        // AX can reenter the run loop; a retired read must not reset a newer focus snapshot.
        guard self.generation == generation else { return uncorrelated }
        guard let launch, fetched,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID(), sameTarget else {
            reset()
            return uncorrelated
        }
        let element = value as! AXUIElement
        if let previous, previous.pid == target.pid, previous.launch == launch,
           CFEqual(previous.element, element) {
            return PassiveCopySource(target: target, focusIdentity: previous.identity)
        }
        let identity = UUID()
        previous = (target.pid, launch, element, identity)
        return PassiveCopySource(target: target, focusIdentity: identity)
    }

    static func securityFailure() -> SelectionResult.Reason? {
        if IsSecureEventInputEnabled() { return .secureInput }
        if !AXIsProcessTrusted() { return .accessibility }
        if !CGPreflightListenEventAccess() { return .inputMonitoring }
        return nil
    }
}
