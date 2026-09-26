import AppKit
import CoreServices
import UniformTypeIdentifiers
import Darwin

enum ClipboardReadFailure: String, Codable, Error {
    case noText, unsupportedRepresentation, unavailableData, invalidRichText, clipboardChanged, clipboardTimedOut, tooLarge

    var reason: PlainTextPasteReason {
        switch self {
        case .noText: return .noText
        case .unsupportedRepresentation: return .unsupportedRepresentation
        case .unavailableData: return .unavailableData
        case .invalidRichText: return .invalidRichText
        case .clipboardChanged: return .clipboardChanged
        case .clipboardTimedOut: return .clipboardTimedOut
        case .tooLarge: return .unavailableData
        }
    }
}

struct ClipboardReadFrame: Codable {
    var version = 1
    let request: UUID
    let event: String
    var pid: Int32?
    var mainThread: Bool?
    var type: String?
    var bytes: Int?
    var revision: Int?
    var failure: ClipboardReadFailure?
}

enum ClipboardReadWireError: Error { case malformed }

// Only metadata is newline-framed. The terminal UTF-8 body has an explicit byte length,
// no translation IPC limit, and is never a log, argument, environment value, or disk file.
struct ClipboardReadDecoder {
    let request: UUID
    let revision: Int
    private(set) var pid: Int32?
    private var pending = Data()
    private var body = Data()
    private var terminal: ClipboardReadFrame?
    private let copied: (String, Int) -> Void
    private let maximumBodyBytes: Int?

    init(request: UUID, revision: Int, maximumBodyBytes: Int? = nil,
         copied: @escaping (String, Int) -> Void = { _, _ in }) {
        self.request = request
        self.revision = revision
        self.copied = copied
        self.maximumBodyBytes = maximumBodyBytes
    }

    mutating func append(_ data: Data) throws {
        if let terminal {
            guard let size = terminal.bytes, data.count <= size - body.count else {
                throw ClipboardReadWireError.malformed
            }
            body.append(data)
            return
        }
        pending.append(data)
        while terminal == nil, let end = pending.firstIndex(of: 0x0A) {
            guard pending.distance(from: pending.startIndex, to: end) <= 8192 else {
                throw ClipboardReadWireError.malformed
            }
            let frame = try JSONDecoder().decode(ClipboardReadFrame.self, from: pending[..<end])
            pending.removeSubrange(...end)
            guard frame.version == 1, frame.request == request else { throw ClipboardReadWireError.malformed }
            switch frame.event {
            case "hello":
                guard pid == nil, let process = frame.pid, process > 1, frame.mainThread == true,
                      frame.bytes == nil, frame.failure == nil else { throw ClipboardReadWireError.malformed }
                pid = process
            case "copy":
                guard pid != nil, let type = frame.type, let count = frame.bytes, count >= -1,
                      type == "public.rtf" || PasteboardTextRepresentation.preferred(in: [type]) != nil,
                      frame.failure == nil else { throw ClipboardReadWireError.malformed }
                copied(type, count)
            case "result":
                guard pid != nil, let count = frame.bytes, count >= 0,
                      maximumBodyBytes.map({ count <= $0 }) ?? true,
                      frame.revision == revision,
                      frame.failure == nil || count == 0 else { throw ClipboardReadWireError.malformed }
                terminal = frame
            default: throw ClipboardReadWireError.malformed
            }
        }
        if terminal != nil {
            let remaining = pending
            pending.removeAll()
            try append(remaining)
        } else if pending.count > 8192 { throw ClipboardReadWireError.malformed }
    }

    func result(process: ClipboardProcessResult) -> Result<String, ClipboardReadFailure> {
        if process.failure == "timed_out" { return .failure(.clipboardTimedOut) }
        guard process.failure == nil, process.exitCode == 0, process.pid == pid,
              let terminal, terminal.bytes == body.count else { return .failure(.unavailableData) }
        if let failure = terminal.failure { return .failure(failure) }
        guard let text = PasteboardTextRepresentation.preferred(in: ["public.utf8-plain-text"])?.decode(body) else {
            return .failure(.unavailableData)
        }
        return .success(text)
    }
}

public enum ClipboardReadWorker {
    public static let argument = "--cc-clipboard-read"
    static let freshCopyArgument = "--cc-clipboard-fresh-copy"

    /// Called before NSApplication/AppDelegate construction. nil means normal UI launch.
    @MainActor
    public static func runIfRequested(arguments: [String] = CommandLine.arguments) -> Int32? {
        guard arguments.dropFirst().contains(where: { $0.hasPrefix("--cc-clipboard-") }) else { return nil }
        guard Thread.isMainThread, arguments.count == 5,
              arguments[1] == argument || arguments[1] == freshCopyArgument,
              !arguments[2].isEmpty, arguments[2].utf8.count <= 4096, !arguments[2].contains("\0"),
              let revision = Int(arguments[3]), revision >= 0,
              let request = UUID(uuidString: arguments[4]) else {
            return 64
        }
        var outputInfo = stat()
        guard fstat(STDOUT_FILENO, &outputInfo) == 0,
              outputInfo.st_mode & mode_t(S_IFMT) == mode_t(S_IFIFO) else { return 64 }
        let parent = getppid()
        guard parent > 1 else { return 70 }
        let watchdog = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        watchdog.schedule(deadline: .now() + 1, repeating: 1)
        watchdog.setEventHandler {
            // A reader must not survive its owning app, even while AppKit is blocked.
            if getppid() != parent { _exit(70) }
        }
        watchdog.resume()
        defer { watchdog.cancel() }
        do {
            try emit(ClipboardReadFrame(request: request, event: "hello",
                                        pid: getpid(), mainThread: Thread.isMainThread))
            let result: Result<String, ClipboardReadFailure>
            if arguments[1] == freshCopyArgument {
                result = readFreshCopy(name: NSPasteboard.Name(arguments[2]), revision: revision)
            } else {
                result = try read(name: NSPasteboard.Name(arguments[2]), revision: revision, request: request)
            }
            switch result {
            case .success(let text):
                let data = Data(text.utf8)
                try emit(ClipboardReadFrame(request: request, event: "result", bytes: data.count,
                                            revision: revision))
                try FileHandle.standardOutput.write(contentsOf: data)
            case .failure(let failure):
                try emit(ClipboardReadFrame(request: request, event: "result", bytes: 0,
                                            revision: revision, failure: failure))
            }
            return 0
        } catch {
            // stdout may contain an incomplete frame; a nonzero exit forbids accepting it.
            return 74
        }
    }

    @MainActor
    private static func readFreshCopy(name: NSPasteboard.Name, revision: Int) -> Result<String, ClipboardReadFailure> {
        let reader = FreshCopyPasteboardReader(pasteboard: { NSPasteboard(name: name) })
        switch reader.read(revision: revision, whileValid: { true }) {
        case .present(let text): return .success(text)
        case .absent: return .success("")
        case .unknown(.tooLarge): return .failure(.tooLarge)
        case .unknown(.clipboardChanged): return .failure(.clipboardChanged)
        case .unknown(.clipboardUnsupported): return .failure(.unsupportedRepresentation)
        default: return .failure(.unavailableData)
        }
    }

    private static func emit(_ frame: ClipboardReadFrame) throws {
        var data = try JSONEncoder().encode(frame)
        guard data.count <= 8192 else { throw ClipboardReadWireError.malformed }
        data.append(0x0A)
        try FileHandle.standardOutput.write(contentsOf: data)
    }

    @MainActor
    private static func read(name: NSPasteboard.Name, revision: Int, request: UUID) throws
        -> Result<String, ClipboardReadFailure> {
        precondition(Thread.isMainThread)
        let board = NSPasteboard(name: name)
        guard board.changeCount == revision else { return .failure(.clipboardChanged) }
        guard !(board.types ?? []).contains(where: { isFileFlavor($0.rawValue) }) else { return .failure(.noText) }
        guard let items = board.pasteboardItems else { return .failure(.unavailableData) }
        guard !items.isEmpty else { return .failure(.noText) }
        var representations: [(NSPasteboardItem, [String])] = []
        for item in items {
            guard board.changeCount == revision else { return .failure(.clipboardChanged) }
            let types = item.types.map(\.rawValue)
            guard !types.contains(where: isFileFlavor) else { return .failure(.noText) }
            representations.append((item, types))
        }
        var strings: [String] = []
        for (item, types) in representations {
            let plain = PasteboardTextRepresentation.preferred(in: types)
            guard let type = plain?.type ?? (types.contains("public.rtf") ? "public.rtf" : nil) else {
                return .failure(types.contains("public.html") || types.contains("com.apple.flat-rtfd")
                                ? .unsupportedRepresentation : .noText)
            }
            guard board.changeCount == revision else { return .failure(.clipboardChanged) }
            let data = item.data(forType: .init(type))
            try emit(ClipboardReadFrame(request: request, event: "copy", type: type, bytes: data?.count ?? -1))
            guard board.changeCount == revision else { return .failure(.clipboardChanged) }
            guard let data else { return .failure(.unavailableData) }
            if let plain {
                guard let text = plain.decode(data) else { return .failure(.unavailableData) }
                strings.append(text)
            } else {
                switch decodeRichText(data) {
                case .success(let text): strings.append(text)
                case .failure(let failure): return .failure(failure)
                }
            }
        }
        guard board.changeCount == revision else { return .failure(.clipboardChanged) }
        return .success(strings.joined(separator: "\n"))
    }

    @MainActor
    static func decodeRichText(_ data: Data) -> Result<String, ClipboardReadFailure> {
        guard data.starts(with: Array("{\\rtf".utf8)),
              let version = data.dropFirst(5).first, (0x30...0x39).contains(version),
              let rich = NSAttributedString(rtf: data, documentAttributes: nil) else {
            return .failure(.invalidRichText)
        }
        var attachment = false
        rich.enumerateAttribute(.attachment, in: NSRange(location: 0, length: rich.length), options: []) {
            value, _, stop in
            if value != nil { attachment = true; stop.pointee = true }
        }
        return attachment ? .failure(.unsupportedRepresentation) : .success(rich.string)
    }

    private static let legacyFileFlavors: Set<String> = [
        "NSFilenamesPboardType", "NSFileContentsPboardType", "NSFilesPromisePboardType"
    ]
    private static let pasteboardTagClass = UTTagClass(rawValue: kUTTagClassNSPboardType as String)

    static func isFileFlavor(_ type: String) -> Bool {
        if type == "public.file-url" || legacyFileFlavors.contains(type) ||
            type.hasPrefix("com.apple.pasteboard.promised-file-") { return true }
        guard let uniformType = UTType(type) else { return false }
        if uniformType.conforms(to: .fileURL) { return true }
        return uniformType.tags[pasteboardTagClass]?.contains(where: legacyFileFlavors.contains) == true
    }
}

final class ClipboardReadRun: @unchecked Sendable {
    private var decoder: ClipboardReadDecoder
    private var process: ClipboardProcess?
    private let trace: @Sendable (String) -> Void
    private let freshCopy: Bool

    init(request: UUID, revision: Int, freshCopy: Bool = false, trace: @escaping @Sendable (String) -> Void) {
        self.trace = trace
        self.freshCopy = freshCopy
        decoder = ClipboardReadDecoder(request: request, revision: revision,
                                        maximumBodyBytes: freshCopy ? 8192 : nil) {
            trace("AppKit copy \($0), bytes \($1)")
        }
    }

    func read(executable: URL, name: String, cancellation: PlainTextPasteCancellation,
              timeout: TimeInterval) async -> Result<String, ClipboardReadFailure> {
        await withCheckedContinuation { continuation in
            let process = ClipboardProcess(timeout: timeout, cancelled: { cancellation.isCancelled },
                                           trace: trace, receive: { [self] in try decoder.append($0) },
                                           completion: { [self] result in
                let decoded = decoder.result(process: result)
                self.process = nil
                continuation.resume(returning: decoded)
            })
            self.process = process
            process.start(executable: executable, arguments: [
                freshCopy ? ClipboardReadWorker.freshCopyArgument : ClipboardReadWorker.argument,
                name, String(decoder.revision), decoder.request.uuidString
            ])
        }
    }
}
