import AppKit
import ApplicationServices
import Carbon

@MainActor
final class SystemFreshCopyClipboard: FreshCopyClipboard {
    private let pasteboard: () -> NSPasteboard
    private let readerExecutable: URL?

    init(pasteboard: @escaping () -> NSPasteboard = { .general }, readerExecutable: URL? = nil) {
        self.pasteboard = pasteboard
        self.readerExecutable = readerExecutable
    }

    func revision() -> Int { pasteboard().changeCount }

    func read(revision: Int, timeout: TimeInterval, cancellation: PlainTextPasteCancellation,
              whileValid: @escaping @MainActor () -> Bool,
              completion: @escaping @MainActor (SelectionResult) -> Void) {
        guard !cancellation.isCancelled, whileValid() else {
            completion(.unknown(.clipboardChanged))
            return
        }
        guard let executable = readerExecutable ?? Bundle.main.executableURL,
              readerExecutable != nil || executable.lastPathComponent == "CCTranslateMac" else {
            completion(.unknown(.clipboardUnavailable))
            return
        }
        let board = pasteboard()
        guard board.changeCount == revision else {
            completion(.unknown(.clipboardChanged))
            return
        }
        let name = board.name.rawValue
        Task {
            let run = ClipboardReadRun(request: UUID(), revision: revision, freshCopy: true, trace: { _ in })
            let result = await run.read(executable: executable, name: name,
                                        cancellation: cancellation, timeout: timeout)
            guard !cancellation.isCancelled, whileValid(), board.changeCount == revision else {
                completion(.unknown(.clipboardChanged))
                return
            }
            switch result {
            case .success(let text):
                completion(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .absent : .present(text))
            case .failure(.tooLarge): completion(.unknown(.tooLarge))
            case .failure(.clipboardChanged): completion(.unknown(.clipboardChanged))
            case .failure(.noText), .failure(.unsupportedRepresentation):
                completion(.unknown(.clipboardUnsupported))
            default: completion(.unknown(.clipboardUnavailable))
            }
        }
    }
}

// Used only in the isolated worker (and named-board tests). A promised AppKit
// read can block its physical main thread; it must never run on the UI process.
@MainActor
final class FreshCopyPasteboardReader {
    private let pasteboard: () -> NSPasteboard
    private static let excludedMarkers: Set<String> = [
        "org.nspasteboard.concealedtype"
    ]

    init(pasteboard: @escaping () -> NSPasteboard = { .general }) {
        self.pasteboard = pasteboard
    }

    func revision() -> Int { pasteboard().changeCount }

    func read(revision: Int, whileValid: () -> Bool) -> SelectionResult {
        guard whileValid() else { return .unknown(.clipboardChanged) }
        let board = pasteboard()
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        let items = board.pasteboardItems ?? []
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        guard !items.isEmpty else { return .absent }
        var representations: [(NSPasteboardItem, [PasteboardTextRepresentation], Bool)] = []
        for item in items {
            let types = item.types.map(\.rawValue)
            guard !types.contains(where: { Self.excludedMarkers.contains($0.lowercased()) }) else {
                return .unknown(.clipboardUnsupported)
            }
            // Explicitly copied text can be accompanied by a PDF/file URL,
            // browser metadata or sync markers. None is a text fallback.
            let plain = types.compactMap { PasteboardTextRepresentation.preferred(in: [$0]) }
            let rich = types.contains(NSPasteboard.PasteboardType.rtf.rawValue)
            guard !plain.isEmpty || rich else { return .unknown(.clipboardUnsupported) }
            representations.append((item, plain, rich))
        }
        var strings: [String] = []
        var totalBytes = 0
        for (item, plain, rich) in representations {
            var decoded: String?
            var sawEmpty = false
            for representation in plain {
                guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
                let data = item.data(forType: .init(representation.type))
                guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
                guard let data else { continue }
                // UTF-16 can use two bytes per ASCII character plus a BOM.
                guard data.count <= 16_386 else { return .unknown(.tooLarge) }
                guard let text = representation.decode(data), !text.contains("\0") else { continue }
                guard text.utf8.count <= 8192 else { return .unknown(.tooLarge) }
                if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    sawEmpty = true
                    continue
                }
                decoded = text
                break
            }
            if decoded == nil, rich {
                guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
                let data = item.data(forType: .rtf)
                guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
                if let data {
                    guard data.count <= 1_048_576 else { return .unknown(.tooLarge) }
                    if case .success(let text) = ClipboardReadWorker.decodeRichText(data), !text.contains("\0") {
                        decoded = text
                    }
                }
            }
            guard let text = decoded ?? (sawEmpty ? "" : nil) else { return .unknown(.clipboardUnavailable) }
            totalBytes += text.utf8.count + (strings.isEmpty ? 0 : 1)
            guard totalBytes <= 8192 else { return .unknown(.tooLarge) }
            strings.append(text)
        }
        guard whileValid(), board.changeCount == revision else { return .unknown(.clipboardChanged) }
        let text = strings.joined(separator: "\n")
        return text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .absent : .present(text)
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
        let uncorrelated = PassiveCopySource(target: target, focusIdentity: nil, launchDate: app.launchDate)
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
