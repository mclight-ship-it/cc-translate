import AppKit
import ApplicationServices
import UniformTypeIdentifiers

// AppKit metadata and eager writes stay on MainActor. Potentially blocking promise/RTF reads
// use a fresh, queue-confined C reference; no AppKit pasteboard object crosses threads.
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

    private struct Metadata: Sendable {
        let changeCount: Int
        let itemCount: Int
    }

    private enum Inspection: Sendable {
        case ready(Metadata), failure(PlainTextPasteReason)
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
        let metadata: Metadata
        switch await inspect(cancellation: cancellation) {
        case .failure(let reason): return .failure(reason)
        case .ready(let value): metadata = value
        }
        let result = await onQueue {
            self.readText(metadata: metadata, token: token, cancellation: cancellation)
        }
        let count = await currentChangeCount()
        return await onQueue {
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            guard self.nextToken == token, count == metadata.changeCount else {
                return .failure(.clipboardChanged)
            }
            if case .text = result { self.readable = Lease(token: token, changeCount: count) }
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
    private func inspect(cancellation: PlainTextPasteCancellation) -> Inspection {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let board = NSPasteboard(name: name)
        let count = board.changeCount
        // Enumerating item types does not request their promised data.
        let items = board.pasteboardItems ?? []
        let types = (board.types ?? []) + items.flatMap(\.types)
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        guard board.changeCount == count else { return .failure(.clipboardChanged) }
        guard !types.contains(where: { Self.isFileFlavor($0.rawValue) }), !items.isEmpty else {
            return .failure(.noText)
        }
        return .ready(Metadata(changeCount: count, itemCount: items.count))
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

    private func readText(metadata: Metadata, token: Int,
                          cancellation: PlainTextPasteCancellation) -> PlainTextPasteRead {
        dispatchPrecondition(condition: .onQueue(Self.queue))
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        let boardName = name == .general ? (kPasteboardClipboard as String) : name.rawValue
        var reference: Pasteboard?
        let createStatus = PasteboardCreate(boardName as CFString, &reference)
        trace?("create status \(createStatus), reference \(reference != nil)")
        guard createStatus == noErr, let board = reference else {
            return .failure(.unavailableData)
        }
        defer { withExtendedLifetime(board) {} }
        _ = PasteboardSynchronize(board)
        var count = 0
        let countStatus = PasteboardGetItemCount(board, &count)
        trace?("count status \(countStatus), actual \(count), expected \(metadata.itemCount)")
        if let failure = failure(after: countStatus, cancellation: cancellation) { return .failure(failure) }
        guard count == metadata.itemCount else { return .failure(.clipboardChanged) }
        var items: [(id: PasteboardItemID, flavors: [String])] = []
        for index in 1...count {
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            var item: PasteboardItemID?
            let status = PasteboardGetItemIdentifier(board, index, &item)
            trace?("identifier index \(index), status \(status), present \(item != nil)")
            if let failure = failure(after: status, cancellation: cancellation) { return .failure(failure) }
            guard let item else { return .failure(.unavailableData) }
            var array: CFArray?
            let flavorsStatus = PasteboardCopyItemFlavors(board, item, &array)
            trace?("flavors status \(flavorsStatus), count \(array.map { CFArrayGetCount($0) } ?? -1)")
            if let failure = failure(after: flavorsStatus, cancellation: cancellation) { return .failure(failure) }
            guard let flavors = array as? [String] else { return .failure(.unavailableData) }
            guard !flavors.contains(where: Self.isFileFlavor) else { return .failure(.noText) }
            var explicitFlavors: [String] = []
            for flavor in flavors {
                var flags = PasteboardFlavorFlags(rawValue: 0)
                let status = PasteboardGetItemFlavorFlags(board, item, flavor as CFString, &flags)
                trace?("flavor \(flavor), flags \(flags.rawValue), status \(status)")
                if let failure = failure(after: status, cancellation: cancellation) { return .failure(failure) }
                if flags.rawValue & (1 << 8) == 0 { explicitFlavors.append(flavor) }
            }
            items.append((item, explicitFlavors))
        }
        var strings: [String] = []
        for (item, flavors) in items {
            if let flavor = Self.textFlavors.first(where: { flavors.contains($0.type) }) {
                switch copyData(board, item: item, type: flavor.type, cancellation: cancellation) {
                case .failure(let reason): return .failure(reason)
                case .success(let data):
                    guard let text = Self.decodeText(data, type: flavor.type, encoding: flavor.encoding) else {
                        return .failure(.unavailableData)
                    }
                    strings.append(text)
                }
            } else if flavors.contains("public.rtf") {
                switch copyData(board, item: item, type: "public.rtf", cancellation: cancellation) {
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

    private func failure(after status: OSStatus,
                         cancellation: PlainTextPasteCancellation) -> PlainTextPasteReason? {
        guard !cancellation.isCancelled else { return .cancelled }
        if status == OSStatus(badPasteboardSyncErr) || status == OSStatus(notPasteboardOwnerErr) {
            return .clipboardChanged
        }
        return status == noErr ? nil : .unavailableData
    }

    private enum FlavorData {
        case success(Data), failure(PlainTextPasteReason)
    }

    private func copyData(_ board: Pasteboard, item: PasteboardItemID, type: String,
                          cancellation: PlainTextPasteCancellation) -> FlavorData {
        guard !cancellation.isCancelled else { return .failure(.cancelled) }
        var data: CFData?
        let result = PasteboardCopyItemFlavorData(board, item, type as CFString, &data)
        trace?("copy \(type), status \(result), bytes \(data.map { CFDataGetLength($0) } ?? -1)")
        if let failure = failure(after: result, cancellation: cancellation) { return .failure(failure) }
        guard let data else { return .failure(.unavailableData) }
        return .success(data as Data)
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

    private static func isFileFlavor(_ type: String) -> Bool {
        type == "public.file-url" || type == "NSFilenamesPboardType" || type == "NSFileContentsPboardType" ||
            type == "NSFilesPromisePboardType" || type.hasPrefix("com.apple.pasteboard.promised-file-") ||
            UTType(type)?.conforms(to: .fileURL) == true
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
