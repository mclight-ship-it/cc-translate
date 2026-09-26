import AppKit
import Darwin

struct Configuration: Decodable {
    let name: String
    let identity: Data
    let fileIdentity: Data?
    let promisedType: String
    let plainText: String?
    let response: String
    let text: Data?
    let fileURL: String?
}

struct Receipt: Encodable {
    let event: String
    let pid: Int32
    let mainThread: Bool
    let calls: Int
    let itemMatches: Bool
    let boardMatches: Bool
    let typeMatches: Bool
}

func emit(_ receipt: Receipt) {
    do {
        var bytes = try JSONEncoder().encode(receipt)
        bytes.append(0x0A)
        try FileHandle.standardOutput.write(contentsOf: bytes)
    } catch { _exit(74) }
}

func readLine() throws -> Data? {
    var line = Data()
    while let byte = try FileHandle.standardInput.read(upToCount: 1), !byte.isEmpty {
        if byte.first == 0x0A { return line }
        line.append(byte)
        if line.count > 1_048_576 { throw ProducerError.invalidConfiguration }
    }
    return line.isEmpty ? nil : line
}

enum ProducerError: Error { case invalidConfiguration }

final class Producer: NSObject, NSPasteboardItemDataProvider {
    let configuration: Configuration
    let gate = DispatchSemaphore(value: 0)
    private let board: NSPasteboard
    private let item = NSPasteboardItem()
    private var items: [NSPasteboardItem] = []
    private var calls = 0

    init(_ configuration: Configuration) throws {
        let reserved: Set<NSPasteboard.Name> = [.general, .drag, .find, .font, .ruler]
        let name = NSPasteboard.Name(configuration.name)
        guard Thread.isMainThread, !reserved.contains(name), !name.rawValue.isEmpty,
              name.rawValue.utf8.count <= 4096, !name.rawValue.contains("\0"),
              ["unavailable", "text", "blocked", "replaceOwner"].contains(configuration.response),
              !configuration.identity.isEmpty else { throw ProducerError.invalidConfiguration }
        self.configuration = configuration
        board = NSPasteboard(name: name)
        super.init()
        if let text = configuration.plainText {
            guard item.setData(Data(text.utf8), forType: .string) else { throw ProducerError.invalidConfiguration }
        }
        guard item.setDataProvider(self, forTypes: [.init(configuration.promisedType)]),
              item.setData(configuration.identity, forType: .init("org.cctranslate.tests.item-identity")) else {
            throw ProducerError.invalidConfiguration
        }
        items = [item]
        if let url = configuration.fileURL {
            let file = NSPasteboardItem()
            guard let identity = configuration.fileIdentity,
                  file.setData(Data(url.utf8), forType: .fileURL),
                  file.setData(identity, forType: .init("org.cctranslate.tests.item-identity")) else {
                throw ProducerError.invalidConfiguration
            }
            items.append(file)
        }
        board.clearContents()
        guard board.writeObjects(items), calls == 0 else { throw ProducerError.invalidConfiguration }
        receipt("ready")
    }

    func pasteboard(_ pasteboard: NSPasteboard?, item: NSPasteboardItem,
                    provideDataForType type: NSPasteboard.PasteboardType) {
        if Thread.isMainThread { provide(pasteboard, item: item, type: type) }
        else { DispatchQueue.main.sync { self.provide(pasteboard, item: item, type: type) } }
    }

    private func provide(_ pasteboard: NSPasteboard?, item: NSPasteboardItem,
                         type: NSPasteboard.PasteboardType) {
        precondition(Thread.isMainThread)
        calls += 1
        let matches = item === self.item && pasteboard?.name == board.name &&
            type.rawValue == configuration.promisedType
        emit(Receipt(event: "call", pid: getpid(), mainThread: Thread.isMainThread, calls: calls,
                     itemMatches: item === self.item, boardMatches: pasteboard?.name == board.name,
                     typeMatches: type.rawValue == configuration.promisedType))
        guard matches else { _exit(65) }
        switch configuration.response {
        case "unavailable": break
        case "text", "blocked":
            if configuration.response == "blocked", gate.wait(timeout: .now() + 15) != .success { _exit(75) }
            guard let data = configuration.text, item.setData(data, forType: type) else { _exit(65) }
        case "replaceOwner":
            let count = board.changeCount
            board.clearContents()
            guard board.setData(Data("new provider owner".utf8), forType: .string),
                  board.changeCount != count else { _exit(65) }
        default: _exit(64)
        }
        receipt("fulfilled")
    }

    func receipt(_ event: String) {
        precondition(Thread.isMainThread)
        emit(Receipt(event: event, pid: getpid(), mainThread: Thread.isMainThread, calls: calls,
                     itemMatches: true, boardMatches: true, typeMatches: true))
    }

    func serve() {
        let parent = getppid()
        // Keep the command-line owner's main run loop alive even between pasteboard callbacks.
        let keepAlive = Timer(timeInterval: 60, repeats: true) { _ in }
        RunLoop.main.add(keepAlive, forMode: .default)
        let watchdog = DispatchSource.makeTimerSource(queue: .global())
        watchdog.schedule(deadline: .now() + 1, repeating: 1)
        watchdog.setEventHandler { if getppid() != parent { _exit(70) } }
        watchdog.resume()
        DispatchQueue.global().async {
            do {
                while let line = try readLine() {
                    switch String(decoding: line, as: UTF8.self) {
                    case "release": self.gate.signal()
                    case "status": DispatchQueue.main.async { self.receipt("status") }
                    case "shutdown":
                        self.gate.signal()
                        DispatchQueue.main.async { self.receipt("stopped"); exit(0) }
                        return
                    default: _exit(64)
                    }
                }
                _exit(70)
            } catch { _exit(74) }
        }
        withExtendedLifetime(watchdog) { RunLoop.main.run() }
    }
}

do {
    guard let data = try readLine() else { throw ProducerError.invalidConfiguration }
    let configuration = try JSONDecoder().decode(Configuration.self, from: data)
    let producer = try Producer(configuration)
    producer.serve()
    exit(70)
} catch {
    // Synthetic-only fixture failures never echo configuration or text.
    exit(64)
}
