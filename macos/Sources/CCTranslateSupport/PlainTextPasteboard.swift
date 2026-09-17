import AppKit
import ApplicationServices
import UniformTypeIdentifiers

// The C APIs are not generally thread-safe. This adapter confines its own CF references, leases,
// calls and final CF release to one queue; it never obtains AppKit's cached NSPasteboard object.
final class SystemPlainTextPasteClipboard: PlainTextPasteClipboard, @unchecked Sendable {
    private static let queue = DispatchQueue(label: "CCTranslate.plain-text-paste.clipboard", qos: .userInitiated)
    private static let modified = PasteboardSyncFlags(rawValue: 1 << 0) // kPasteboardModified
    private static let clientIsOwner = PasteboardSyncFlags(rawValue: 1 << 1) // Application-wide, not reference-wide.
    private let name: String
    private let identity = UUID()
    private let trace: (@Sendable (String) -> Void)?
    private var reference: OwnedReference?
    private var witness: OwnedReference?
    private var nextToken = 0
    private var readableToken: Int?
    private var writtenToken: Int?

    private final class OwnedReference {
        private struct Storage: @unchecked Sendable {
            let value: Pasteboard
        }
        private let storage: Storage
        var value: Pasteboard { storage.value }
        init(_ value: Pasteboard) { storage = Storage(value: value) }
        deinit {
            let storage = storage
            // PasteboardCreate's CF_RETURNS_RETAINED output is ARC-managed in Swift.
            // Keep its last strong reference on the queue, including release-time promise handling.
            SystemPlainTextPasteClipboard.queue.async { withExtendedLifetime(storage) {} }
        }
    }

    init(name: NSPasteboard.Name = .general, trace: (@Sendable (String) -> Void)? = nil) {
        self.name = name == .general ? (kPasteboardClipboard as String) : name.rawValue
        self.trace = trace
    }

    func read(cancellation: PlainTextPasteCancellation) async -> PlainTextPasteRead {
        await onQueue {
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            guard let board = self.openReference() else { return .failure(.unavailableData) }
            self.readableToken = nil
            self.writtenToken = nil
            guard let witness = self.witness?.value else { return .failure(.unavailableData) }
            _ = PasteboardSynchronize(witness)
            _ = PasteboardSynchronize(board)
            self.nextToken += 1
            let token = self.nextToken
            var count = 0
            let countStatus = PasteboardGetItemCount(board, &count)
            if let failure = self.failure(after: countStatus, cancellation: cancellation) {
                return .failure(failure)
            }
            guard count > 0 else { return .failure(.noText) }
            var items: [(id: PasteboardItemID, flavors: [String])] = []
            for index in 1...count {
                guard !cancellation.isCancelled else { return .failure(.cancelled) }
                var item: PasteboardItemID?
                let itemStatus = PasteboardGetItemIdentifier(board, index, &item)
                if let failure = self.failure(after: itemStatus, cancellation: cancellation) {
                    return .failure(failure)
                }
                guard let item else { return .failure(.unavailableData) }
                var flavorArray: CFArray?
                let flavorsStatus = PasteboardCopyItemFlavors(board, item, &flavorArray)
                if let failure = self.failure(after: flavorsStatus, cancellation: cancellation) {
                    return .failure(failure)
                }
                guard let flavors = flavorArray as? [String] else { return .failure(.unavailableData) }
                guard !flavors.contains(where: Self.isFileFlavor) else { return .failure(.noText) }
                var explicitFlavors: [String] = []
                for flavor in flavors {
                    var flags = PasteboardFlavorFlags(rawValue: 0)
                    let status = PasteboardGetItemFlavorFlags(board, item, flavor as CFString, &flags)
                    self.trace?("flavor \(flavor), flags \(flags.rawValue), status \(status)")
                    if let failure = self.failure(after: status, cancellation: cancellation) {
                        return .failure(failure)
                    }
                    // Do not ask an implicit system translator to turn HTML/RTF into "plain" text.
                    if flags.rawValue & (1 << 8) == 0 { explicitFlavors.append(flavor) }
                }
                items.append((item, explicitFlavors))
            }
            var strings: [String] = []
            // Inspect every item's metadata before requesting any promised text from a mixed file clipboard.
            for (item, flavors) in items {
                if let flavor = Self.textFlavors.first(where: { flavors.contains($0.type) }) {
                    switch self.copyData(board, item: item, type: flavor.type, cancellation: cancellation) {
                    case .failure(let failure): return .failure(failure)
                    case .success(let data):
                        guard let text = Self.decodeText(data, type: flavor.type, encoding: flavor.encoding) else {
                            return .failure(.unavailableData)
                        }
                        strings.append(text)
                    }
                } else if flavors.contains("public.rtf") {
                    switch self.copyData(board, item: item, type: "public.rtf", cancellation: cancellation) {
                    case .failure(let failure): return .failure(failure)
                    case .success(let data):
                        // AppKit may accept non-RTF bytes as an empty attributed string. Do not clear for those.
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
                    // No HTML/RTFD import, file promise resolution, image decoding or external-resource loading.
                    return .failure(flavors.contains("public.html") || flavors.contains("com.apple.flat-rtfd")
                                    ? .unsupportedRepresentation : .noText)
                }
            }
            if let failure = self.failure(after: noErr, cancellation: cancellation) {
                return .failure(failure)
            }
            self.readableToken = token
            return .text(PlainTextPasteSnapshot(changeCount: token, text: strings.joined(separator: "\n"),
                                                sourceIdentity: self.identity))
        }
    }

    func replace(_ snapshot: PlainTextPasteSnapshot, cancellation: PlainTextPasteCancellation) async -> PlainTextPasteWrite {
        await onQueue {
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            guard snapshot.sourceIdentity == self.identity, self.readableToken == snapshot.changeCount,
                  let board = self.reference?.value else { return .failure(.clipboardChanged) }
            self.readableToken = nil
            self.writtenToken = nil
            if let failure = self.failure(after: noErr, cancellation: cancellation) {
                return .failure(failure)
            }
            guard let item = PasteboardItemID(bitPattern: 1) else { return .failure(.writeFailed) }
            let data = Data(snapshot.text.utf8)
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            cancellation.record(clipboard: .mayHaveChanged)
            // Synchronize is a check, not an atomic compare-and-clear. Another owner can still race this call.
            let clearStatus = PasteboardClear(board)
            self.trace?("clear status \(clearStatus)")
            guard clearStatus == noErr else { return .failure(.writeFailed) }
            cancellation.record(clipboard: .cleared)
            guard !cancellation.isCancelled else { return .failure(.cancelled) }
            // Synchronize the writer before publishing. A separate observer can receive our own
            // clear later, so it cannot distinguish that notification from a subsequent owner's clear.
            let flags = PasteboardSynchronize(board)
            self.trace?("after clear: writer \(flags.rawValue)")
            guard flags.contains(Self.clientIsOwner) else {
                return .failure(.clipboardChanged)
            }
            guard self.unchanged(requireOwnership: true) else { return .failure(.clipboardChanged) }
            let result = PasteboardPutItemFlavor(board, item, "public.utf8-plain-text" as CFString,
                                                data as CFData, PasteboardFlavorFlags(rawValue: 0))
            self.trace?("put status \(result)")
            guard result == noErr else { return .failure(.writeFailed) }
            cancellation.record(clipboard: .plainTextWritten)
            guard self.unchanged(requireOwnership: true) else { return .failure(.clipboardChanged) }
            self.nextToken += 1
            let token = self.nextToken
            self.writtenToken = token
            return .written(token)
        }
    }

    func stillOwns(_ count: Int) async -> Bool {
        await onQueue {
            guard self.writtenToken == count else { return false }
            return self.unchanged(requireOwnership: true)
        }
    }

    private func openReference() -> Pasteboard? {
        dispatchPrecondition(condition: .onQueue(Self.queue))
        if let reference { return reference.value }
        var value: Pasteboard?
        guard PasteboardCreate(name as CFString, &value) == noErr, let value else { return nil }
        var observer: Pasteboard?
        guard PasteboardCreate(name as CFString, &observer) == noErr, let observer else { return nil }
        reference = OwnedReference(value)
        witness = OwnedReference(observer)
        return value
    }

    private func unchanged(requireOwnership: Bool = false) -> Bool {
        // Reads use an independent witness because data calls can acknowledge changes. After our
        // write, only synchronize the writer: no read may acknowledge a later owner's modification.
        guard let board = requireOwnership ? reference?.value : witness?.value else { return false }
        let flags = PasteboardSynchronize(board)
        trace?("lease flags \(flags.rawValue), requireOwnership \(requireOwnership)")
        guard !flags.contains(Self.modified), !requireOwnership || flags.contains(Self.clientIsOwner) else {
            readableToken = nil
            writtenToken = nil
            return false
        }
        return true
    }

    private func failure(after status: OSStatus,
                         cancellation: PlainTextPasteCancellation) -> PlainTextPasteReason? {
        guard !cancellation.isCancelled else { return .cancelled }
        guard unchanged() else { return .clipboardChanged }
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
        if let failure = failure(after: noErr, cancellation: cancellation) { return .failure(failure) }
        var data: CFData?
        // This public call can synchronously wait on a promise keeper. Cancellation only gates its eventual result.
        let result = PasteboardCopyItemFlavorData(board, item, type as CFString, &data)
        trace?("copy \(type), status \(result), bytes \(data.map { CFDataGetLength($0) } ?? -1)")
        if let failure = failure(after: result, cancellation: cancellation) {
            return .failure(failure)
        }
        guard let data else { return .failure(.unavailableData) }
        return .success(data as Data)
    }

    private static let textFlavors: [(type: String, encoding: String.Encoding)] = [
        ("public.utf8-plain-text", .utf8),
        ("public.utf8-tab-separated-values-text", .utf8),
        // An external UTF-16 flavor can acquire an unflagged native alias with normalized line endings.
        ("public.utf16-external-plain-text", .utf16),
        ("public.utf16-plain-text", .utf16),
        ("com.apple.traditional-mac-plain-text", .macOSRoman)
    ]

    private static func decodeText(_ data: Data, type: String, encoding: String.Encoding) -> String? {
        // Native UTF-16 need not carry a BOM; external UTF-16 uses Foundation's BOM-aware decoder.
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
