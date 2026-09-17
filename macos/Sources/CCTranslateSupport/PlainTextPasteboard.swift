import AppKit
import CoreServices
import UniformTypeIdentifiers

// AppKit change-count checks and eager writes stay on MainActor. Item enumeration and
// potentially blocking promise/RTF reads use queue-confined AppKit items for one interaction.
final class SystemPlainTextPasteClipboard: PlainTextPasteClipboard, @unchecked Sendable {
    private static let queue = DispatchQueue(label: "CCTranslate.plain-text-paste.clipboard", qos: .userInitiated)
    private let name: NSPasteboard.Name
    private let identity = UUID()
    private let trace: (@Sendable (String) -> Void)?
    private var nextToken = 0
    private var readable: Lease?
    private var written: Lease?

    private struct Lease: Sendable {
        let token: Int
        let changeCount: Int
    }

    init(name: NSPasteboard.Name = .general, trace: (@Sendable (String) -> Void)? = nil) {
        self.name = name
        self.trace = trace
    }

    func read(cancellation: PlainTextPasteCancellation) async -> PlainTextPasteRead {
        let token = await onQueue {
            self.readable = nil
            self.written = nil
            self.nextToken += 1
            return self.nextToken
        }
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let initialCount = await currentChangeCount()
        let result = await onQueue {
            self.trace?("read token \(token), initial change count \(initialCount)")
            return self.readText(token: token, expectedChangeCount: initialCount, cancellation: cancellation)
        }
        let count = await currentChangeCount()
        return await onQueue {
            self.trace?("read finish token \(token)/\(self.nextToken), change count \(initialCount)/\(count), cancelled \(cancellation.isCancelled)")
            guard !cancellation.isCancelled else {
                self.trace?("read rejected cancelled")
                return .failure(.cancelled)
            }
            guard self.nextToken == token, count == initialCount else {
                self.trace?("read rejected clipboardChanged")
                return .failure(.clipboardChanged)
            }
            switch result {
            case .text:
                self.readable = Lease(token: token, changeCount: count)
                self.trace?("read accepted text lease")
            case .failure(let reason):
                self.trace?("read rejected \(reason)")
            }
            return result
        }
    }

    func replace(_ snapshot: PlainTextPasteSnapshot, cancellation: PlainTextPasteCancellation) async -> PlainTextPasteWrite {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let prepared: (Lease, Data)? = await onQueue {
            guard snapshot.sourceIdentity == self.identity, let lease = self.readable,
                  lease.token == snapshot.changeCount else { return nil }
            self.readable = nil
            self.written = nil
            return (lease, Data(snapshot.text.utf8))
        }
        guard let (lease, data) = prepared else { return .failure(.clipboardChanged) }
        let result = await write(data, replacing: lease.changeCount, cancellation: cancellation)
        return await onQueue {
            switch result {
            case .failure: return result
            case .written(let changeCount):
                guard self.nextToken == lease.token else { return .failure(.clipboardChanged) }
                self.nextToken += 1
                self.written = Lease(token: self.nextToken, changeCount: changeCount)
                return .written(self.nextToken)
            }
        }
    }

    func stillOwns(_ token: Int) async -> Bool {
        let lease = await onQueue { self.written }
        guard let lease, lease.token == token else { return false }
        let count = await currentChangeCount()
        return await onQueue {
            guard self.written?.token == token else { return false }
            guard count == lease.changeCount else {
                self.written = nil
                return false
            }
            return true
        }
    }

    @MainActor
    private func currentChangeCount() -> Int {
        NSPasteboard(name: name).changeCount
    }

    @MainActor
    private func write(_ data: Data, replacing expected: Int,
                       cancellation: PlainTextPasteCancellation) -> PlainTextPasteWrite {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let board = NSPasteboard(name: name)
        guard board.changeCount == expected else { return .failure(.clipboardChanged) }
        cancellation.record(clipboard: .mayHaveChanged)
        // Check/clear is not an atomic CAS against other applications. Never restore or retry.
        let count = board.clearContents()
        cancellation.record(clipboard: .cleared)
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard board.changeCount == count else { return .failure(.clipboardChanged) }
        guard board.setData(data, forType: .string) else { return .failure(.writeFailed) }
        cancellation.record(clipboard: .plainTextWritten)
        guard board.changeCount == count else { return .failure(.clipboardChanged) }
        return .written(count)
    }

    private func readText(token: Int, expectedChangeCount: Int,
                          cancellation: PlainTextPasteCancellation) -> PlainTextPasteRead {
        dispatchPrecondition(condition: .onQueue(Self.queue))
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let board = NSPasteboard(name: name)
        guard board.changeCount == expectedChangeCount else { return .failure(.clipboardChanged) }
        // Global types include legacy AppKit file declarations; item types are UTIs.
        guard !(board.types ?? []).contains(where: { Self.isFileFlavor($0.rawValue) }) else {
            return .failure(.noText)
        }
        guard let pasteboardItems = board.pasteboardItems else { return .failure(.unavailableData) }
        trace?("AppKit item count \(pasteboardItems.count)")
        guard !pasteboardItems.isEmpty else { return .failure(.noText) }
        var items: [(item: NSPasteboardItem, flavors: [String])] = []
        for (index, item) in pasteboardItems.enumerated() {
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            guard board.changeCount == expectedChangeCount else { return .failure(.clipboardChanged) }
            let flavors = item.types.map(\.rawValue)
            trace?("AppKit item \(index), types \(flavors)")
            guard !flavors.contains(where: Self.isFileFlavor) else { return .failure(.noText) }
            items.append((item, flavors))
        }
        // Inspect every item's metadata before requesting any promised data. Exact type
        // membership avoids conformance-based object importers (notably HTML and file URLs).
        var strings: [String] = []
        for (item, flavors) in items {
            if let flavor = Self.textFlavors.first(where: { flavors.contains($0.type) }) {
                switch copyData(board, item: item, type: flavor.type,
                                expectedChangeCount: expectedChangeCount, cancellation: cancellation) {
                case .failure(let reason): return .failure(reason)
                case .success(let data):
                    guard let text = Self.decodeText(data, type: flavor.type, encoding: flavor.encoding) else {
                        trace?("decode \(flavor.type) failed, bytes \(data.count)")
                        return .failure(.unavailableData)
                    }
                    trace?("decode \(flavor.type) succeeded, bytes \(data.count)")
                    strings.append(text)
                }
            } else if flavors.contains("public.rtf") {
                switch copyData(board, item: item, type: "public.rtf",
                                expectedChangeCount: expectedChangeCount, cancellation: cancellation) {
                case .failure(let reason): return .failure(reason)
                case .success(let data):
                    guard data.starts(with: Array("{\\rtf".utf8)),
                          let version = data.dropFirst(5).first, (0x30...0x39).contains(version),
                          let rich = NSAttributedString(rtf: data, documentAttributes: nil) else {
                        return .failure(.invalidRichText)
                    }
                    var attachment = false
                    rich.enumerateAttribute(.attachment, in: NSRange(location: 0, length: rich.length), options: []) { value, _, stop in
                        if value != nil { attachment = true; stop.pointee = true }
                    }
                    guard !attachment else { return .failure(.unsupportedRepresentation) }
                    strings.append(rich.string)
                }
            } else {
                return .failure(flavors.contains("public.html") || flavors.contains("com.apple.flat-rtfd")
                                ? .unsupportedRepresentation : .noText)
            }
        }
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        return .text(PlainTextPasteSnapshot(changeCount: token, text: strings.joined(separator: "\n"),
                                            sourceIdentity: identity))
    }

    private enum FlavorData {
        case success(Data), failure(PlainTextPasteReason)
    }

    private func copyData(_ board: NSPasteboard, item: NSPasteboardItem, type: String,
                          expectedChangeCount: Int,
                          cancellation: PlainTextPasteCancellation) -> FlavorData {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard board.changeCount == expectedChangeCount else { return .failure(.clipboardChanged) }
        let data = item.data(forType: NSPasteboard.PasteboardType(type))
        trace?("AppKit copy \(type), bytes \(data?.count ?? -1)")
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard board.changeCount == expectedChangeCount else { return .failure(.clipboardChanged) }
        guard let data else { return .failure(.unavailableData) }
        return .success(data)
    }

    private static let textFlavors: [(type: String, encoding: String.Encoding)] = [
        ("public.utf8-plain-text", .utf8),
        ("public.utf8-tab-separated-values-text", .utf8),
        // The unflagged native alias of external UTF-16 can normalize line endings.
        ("public.utf16-external-plain-text", .utf16),
        ("public.utf16-plain-text", .utf16),
        ("com.apple.traditional-mac-plain-text", .macOSRoman)
    ]

    private static func decodeText(_ data: Data, type: String, encoding: String.Encoding) -> String? {
        if type == "public.utf16-plain-text", !data.starts(with: [0xFF, 0xFE]), !data.starts(with: [0xFE, 0xFF]) {
            let native: String.Encoding = UInt16(littleEndian: 1) == 1 ? .utf16LittleEndian : .utf16BigEndian
            return String(data: data, encoding: native)
        }
        return String(data: data, encoding: encoding)
    }

    private static let legacyFileFlavors: Set<String> = [
        "NSFilenamesPboardType", "NSFileContentsPboardType", "NSFilesPromisePboardType"
    ]
    private static let pasteboardTagClass = UTTagClass(rawValue: kUTTagClassNSPboardType as String)

    private static func isFileFlavor(_ type: String) -> Bool {
        if type == "public.file-url" || legacyFileFlavors.contains(type) ||
            type.hasPrefix("com.apple.pasteboard.promised-file-") { return true }
        guard let uniformType = UTType(type) else { return false }
        if uniformType.conforms(to: .fileURL) { return true }
        // Carbon exposes some legacy AppKit file flavors as dynamic UTIs. Their public
        // tag specification preserves the pasteboard type; the opaque dyn.* string is not stable.
        return uniformType.tags[pasteboardTagClass]?.contains(where: legacyFileFlavors.contains) == true
    }

    private func onQueue<Value: Sendable>(_ work: @escaping @Sendable () -> Value) async -> Value {
        await withCheckedContinuation { continuation in
            Self.queue.async {
                let value = autoreleasepool(invoking: work)
                continuation.resume(returning: value)
            }
        }
    }
}
