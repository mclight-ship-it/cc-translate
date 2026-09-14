import XCTest
import Darwin
@testable import CCTranslateSupport

final class HelperIntegrationTests: XCTestCase {
    @MainActor
    private final class ConfigurationNotices {
        let ready = XCTestExpectation(description: "configuration ready")
        let stopped = XCTestExpectation(description: "configuration helper exited")
        private(set) var events: [ServerEvent] = []
        private(set) var failures: [ProbeError] = []
        private var terminals: [String: XCTestExpectation] = [:]
        private var starts: [String: XCTestExpectation] = [:]
        private(set) var connection: HelperConnection!

        init() {
            connection = HelperConnection { [weak self] notice in
                MainActor.assumeIsolated {
                    guard let self = self else { return }
                    switch notice {
                    case .event(let event):
                        self.events.append(event)
                        if event.type == "ready" { self.ready.fulfill() }
                        if event.type == "started" { self.starts[event.id]?.fulfill() }
                        if event.isTerminal { self.terminals[event.id]?.fulfill() }
                    case .failure(let error): self.failures.append(error)
                    case .stopped: self.stopped.fulfill()
                    }
                }
            }
        }

        func terminal(_ id: String) -> XCTestExpectation {
            let expectation = XCTestExpectation(description: "configuration request terminal")
            terminals[id] = expectation
            return expectation
        }

        func started(_ id: String) -> XCTestExpectation {
            let expectation = XCTestExpectation(description: "business request started")
            starts[id] = expectation
            return expectation
        }

        func result(_ id: String) -> ServerEvent? {
            events.last { $0.id == id && $0.isTerminal }
        }

        func assertOperation(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
            let operation = events.filter { $0.id == id }
            XCTAssertEqual(operation.map(\.type), ["accepted", "started", "completed"], file: file, line: line)
            XCTAssertEqual(operation.map(\.sequence), [0, 1, 2], file: file, line: line)
            XCTAssertTrue(operation.allSatisfy { $0.payload["fixture"] == nil }, file: file, line: line)
        }

        func assertHistoryFailure(_ id: String, code: String,
                                  file: StaticString = #filePath, line: UInt = #line) {
            let operation = events.filter { $0.id == id }
            XCTAssertEqual(operation.map(\.type), ["accepted", "started", "failed"], file: file, line: line)
            XCTAssertEqual(operation.map(\.sequence), [0, 1, 2], file: file, line: line)
            XCTAssertEqual(result(id)?.safeFailureCode, code, file: file, line: line)
        }

        func historyEntries(_ id: String) throws -> [[String: JSONValue]] {
            guard case let .array(entries)? = result(id)?.payload["entries"] else {
                XCTFail("History completion must contain an entries array")
                throw ProbeError.invalidPayload
            }
            return try entries.map { try XCTUnwrap($0.object) }
        }
    }

    private func configurationIO<T>(_ operation: () throws -> T) throws -> T {
        do { return try operation() }
        catch { throw ProbeError.configUnavailable }
    }

    private func removeConfigurationHome(_ home: URL) {
        do { try FileManager.default.removeItem(at: home) }
        catch { XCTFail("Synthetic configuration home cleanup failed") }
    }

    private func configurationContext() throws -> (runtime: BundleRuntime, home: URL, config: URL) {
        guard let app = ProcessInfo.processInfo.environment["CC_TRANSLATE_APP"], !app.isEmpty else {
            throw XCTSkip("Set CC_TRANSLATE_APP to a built .app to test its bundled Python; no host fallback.")
        }
        let appURL = URL(fileURLWithPath: app, isDirectory: true)
        let runtime = try BundleRuntime(appURL: appURL)
        let data = try configurationIO { try Data(contentsOf: appURL.appendingPathComponent("Contents/Info.plist")) }
        let plist = try configurationIO { try PropertyListSerialization.propertyList(from: data, format: nil) }
        guard let fields = plist as? [String: Any], let identifier = fields["CFBundleIdentifier"] as? String,
              !identifier.isEmpty else { throw ProbeError.bundleMissing }
        XCTAssertTrue(try runtime.configurationApplicationIdentifier() == identifier, "Selected app identifier required")
        // The disposable home is inside the test working directory, never the account home.
        let home = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
            .appendingPathComponent(".configuration-integration-\(UUID().uuidString)", isDirectory: true)
        try configurationIO { try FileManager.default.createDirectory(at: home, withIntermediateDirectories: false) }
        let config = home.appendingPathComponent("Library/Application Support", isDirectory: true)
            .appendingPathComponent(identifier, isDirectory: true).appendingPathComponent("config.json")
        return (runtime, home, config)
    }

    @MainActor
    func testBundledConfigurationLoadSaveNormalizeStopAndReopen() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        XCTAssertTrue(session.events.first?.payload["fixture"] == .bool(false))

        let missing = session.terminal("missing")
        session.connection.loadConfiguration(id: "missing")
        await fulfillment(of: [missing], timeout: 10)
        session.assertOperation("missing")
        XCTAssertTrue(session.result("missing")?.payload["config"]?.object != nil, "Missing file must return defaults")
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.config.path), "Missing load must not write")

        let saved = session.terminal("save")
        session.connection.saveConfiguration([
            "font_size": .string("23"), "future_setting": .string("synthetic-\u{4e2d}\u{6587}")
        ], id: "save")
        await fulfillment(of: [saved], timeout: 10)
        session.assertOperation("save")
        XCTAssertTrue(session.result("save")?.payload == ["saved": .bool(true)])
        let bytes = try configurationIO { try Data(contentsOf: context.config) }
        let expected = Data("{\n  \"font_size\": \"23\",\n  \"future_setting\": \"synthetic-\u{4e2d}\u{6587}\"\n}".utf8)
        XCTAssertTrue(bytes == expected, "Save must preserve raw values and UTF-8 storage bytes")

        let loaded = session.terminal("load")
        session.connection.loadConfiguration(id: "load")
        await fulfillment(of: [loaded], timeout: 10)
        session.assertOperation("load")
        let normalized = session.result("load")?.payload["config"]?.object
        XCTAssertTrue(normalized?["font_size"] == .integer(23), "Known field must be normalized")
        XCTAssertTrue(normalized?["future_setting"] == .string("synthetic-\u{4e2d}\u{6587}"),
                      "Unknown field must survive normalization")
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty, "Configuration stop must drain without transport failures")
        XCTAssertTrue(session.events.last?.type == "completed" && session.events.last?.payload.isEmpty == true,
                      "Shutdown must complete after request terminals")

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let reloaded = reopened.terminal("reload")
        reopened.connection.loadConfiguration(id: "reload")
        await fulfillment(of: [reloaded], timeout: 10)
        reopened.assertOperation("reload")
        let view = reopened.result("reload")?.payload["config"]?.object
        XCTAssertTrue(view?["font_size"] == .integer(23))
        XCTAssertTrue(view?["future_setting"] == .string("synthetic-\u{4e2d}\u{6587}"))
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty, "Reopened owner must complete normally")
    }

    @MainActor
    func testBundledConfigurationWriteAndMigrationBudgetsPreserveReadableData() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        func nested(_ count: Int) -> [String: JSONValue] {
            var value = JSONValue.array(Array(repeating: .integer(0), count: count))
            for _ in 0..<7 { value = .array([value]) }
            return ["future": value]
        }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        let valid = nested(1000)
        let saved = session.terminal("save")
        session.connection.saveConfiguration(valid, id: "save")
        await fulfillment(of: [saved], timeout: 10)
        session.assertOperation("save")
        let before = try configurationIO { try Data(contentsOf: context.config) }
        let nearLimit: [String: JSONValue] = [
            "history_enabled": .string(String(repeating: "x", count: 16_362))
        ]
        XCTAssertEqual(try JSONValue.object(nearLimit).encoded().count, ConfigurationDocument.maxBytes)
        for (id, raw) in [("expanded", nested(4000)), ("migration", nearLimit)] {
            XCTAssertNoThrow(try ConfigurationDocument.validate(.object(raw)))
            let rejected = session.terminal(id)
            session.connection.saveConfiguration(raw, id: id)
            await fulfillment(of: [rejected], timeout: 10)
            XCTAssertEqual(session.events.filter { $0.id == id }.map(\.type), ["failed"])
            XCTAssertEqual(session.result(id)?.sequence, 0)
            XCTAssertEqual(session.result(id)?.safeFailureCode, "invalid_config")
            let unchanged = try configurationIO { try Data(contentsOf: context.config) }
            XCTAssertTrue(unchanged == before, "Rejected save must preserve the last readable raw bytes")
        }
        let loaded = session.terminal("load")
        session.connection.loadConfiguration(id: "load")
        await fulfillment(of: [loaded], timeout: 10)
        session.assertOperation("load")
        XCTAssertEqual(session.result("load")?.payload["config"]?.object?["future"], valid["future"])
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let reloaded = reopened.terminal("reload")
        reopened.connection.loadConfiguration(id: "reload")
        await fulfillment(of: [reloaded], timeout: 10)
        reopened.assertOperation("reload")
        XCTAssertEqual(reopened.result("reload")?.payload["config"]?.object?["future"], valid["future"])

        let external = try JSONValue.object(nearLimit).encoded()
        try configurationIO { try external.write(to: context.config) }
        for id in ["external_first", "external_second"] {
            let rejected = reopened.terminal(id)
            reopened.connection.loadConfiguration(id: id)
            await fulfillment(of: [rejected], timeout: 10)
            XCTAssertEqual(reopened.result(id)?.safeFailureCode, "invalid_config")
            XCTAssertEqual(reopened.events.filter { $0.id == id }.map(\.type), ["accepted", "started", "failed"])
            let unchanged = try configurationIO { try Data(contentsOf: context.config) }
            XCTAssertTrue(unchanged == external, "Failed migration must not replace external raw bytes")
        }
        let names = try configurationIO {
            try FileManager.default.contentsOfDirectory(atPath: context.config.deletingLastPathComponent().path)
        }
        XCTAssertFalse(names.contains { $0.hasPrefix(".tmp_") })
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty, "Storage validation rejection must remain a domain error")
    }

    @MainActor
    func testBundledConfigurationCorruptFileFailsWithoutChangingBytes() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        try configurationIO {
            try FileManager.default.createDirectory(at: context.config.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
        }
        let corrupt = Data("{\"font_size\":23,\"font_size\":24}".utf8)
        try configurationIO { try corrupt.write(to: context.config) }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        let failed = session.terminal("corrupt")
        session.connection.loadConfiguration(id: "corrupt")
        await fulfillment(of: [failed], timeout: 10)
        let events = session.events.filter { $0.id == "corrupt" }
        XCTAssertEqual(events.map(\.type), ["accepted", "started", "failed"])
        XCTAssertEqual(events.map(\.sequence), [0, 1, 2])
        XCTAssertEqual(session.result("corrupt")?.safeFailureCode, "invalid_config")
        let unchanged = try configurationIO { try Data(contentsOf: context.config) }
        XCTAssertTrue(unchanged == corrupt, "Invalid configuration must remain unchanged")
        let invalidSave = session.terminal("invalid_save")
        session.connection.saveConfiguration(["font_size": .string("not-an-integer")], id: "invalid_save")
        await fulfillment(of: [invalidSave], timeout: 10)
        XCTAssertEqual(session.events.filter { $0.id == "invalid_save" }.map(\.type), ["failed"])
        XCTAssertEqual(session.result("invalid_save")?.sequence, 0)
        XCTAssertEqual(session.result("invalid_save")?.safeFailureCode, "invalid_config")
        let stillUnchanged = try configurationIO { try Data(contentsOf: context.config) }
        XCTAssertTrue(stillUnchanged == corrupt, "Unnormalizable save must not change the file")
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty, "Domain failure must not become a transport failure")

        let unavailable = ConfigurationNotices()
        defer { unavailable.connection.forceStop() }
        let bootstrap = unavailable.terminal("hello")
        let missingHome = context.home.appendingPathComponent("missing-home", isDirectory: true)
        unavailable.connection.startConfiguration(runtime: context.runtime, home: missingHome)
        await fulfillment(of: [bootstrap, unavailable.stopped], timeout: 10, enforceOrder: true)
        XCTAssertEqual(unavailable.result("hello")?.safeFailureCode, "config_unavailable")
        XCTAssertEqual(unavailable.failures, [.configUnavailable])
        XCTAssertFalse(FileManager.default.fileExists(atPath: missingHome.path))
    }

    @MainActor
    func testBundledConfigurationCompetingHelperFailsThenTakesReleasedOwnership() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let owner = ConfigurationNotices()
        defer { owner.connection.forceStop() }
        owner.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [owner.ready], timeout: 10)

        let competitor = ConfigurationNotices()
        defer { competitor.connection.forceStop() }
        let rejected = competitor.terminal("hello")
        competitor.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [rejected, competitor.stopped], timeout: 10, enforceOrder: true)
        XCTAssertEqual(competitor.events.map(\.type), ["failed"])
        XCTAssertEqual(competitor.events.map(\.sequence), [0])
        XCTAssertEqual(competitor.result("hello")?.safeFailureCode, "config_in_use")
        XCTAssertEqual(competitor.failures, [.configInUse])
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.config.path))

        let live = owner.terminal("still_owned")
        owner.connection.loadConfiguration(id: "still_owned")
        await fulfillment(of: [live], timeout: 10)
        owner.assertOperation("still_owned")
        owner.connection.stop()
        await fulfillment(of: [owner.stopped], timeout: 10)
        XCTAssertTrue(owner.failures.isEmpty)

        let successor = ConfigurationNotices()
        defer { successor.connection.forceStop() }
        successor.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [successor.ready], timeout: 10)
        let loaded = successor.terminal("takeover")
        successor.connection.loadConfiguration(id: "takeover")
        await fulfillment(of: [loaded], timeout: 10)
        successor.assertOperation("takeover")
        successor.connection.stop()
        await fulfillment(of: [successor.stopped], timeout: 10)
        XCTAssertTrue(successor.failures.isEmpty, "Released ownership must be available to a new connection")
    }

    @MainActor
    func testBundledHistoryLifecyclePaginationUnicodeAndConfigurationCoexistence() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let history = context.config.deletingLastPathComponent().appendingPathComponent("history.json")
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        XCTAssertEqual(session.events.first?.payload["fixture"], .bool(false))
        guard case let .array(capabilities)? = session.events.first?.payload["capabilities"] else {
            return XCTFail("Business readiness must expose all five capabilities")
        }
        XCTAssertEqual(Set(capabilities.compactMap(\.string)),
                       ["config_load", "config_save", "history_load", "history_add", "history_clear"])
        XCTAssertEqual(capabilities.count, 5)
        let missing = session.terminal("missing_history")
        session.connection.loadHistory(id: "missing_history")
        await fulfillment(of: [missing], timeout: 10)
        session.assertOperation("missing_history")
        XCTAssertEqual(try session.historyEntries("missing_history"), [])
        XCTAssertEqual(session.result("missing_history")?.payload["total"], .integer(0))
        XCTAssertEqual(session.result("missing_history")?.payload["next_cursor"], .null)
        XCTAssertFalse(FileManager.default.fileExists(atPath: history.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.config.path))

        let unicodeInput = String(repeating: "\u{4e2d}/", count: 4500)
        let unicodeOutput = String(repeating: "\u{1f600}", count: 3000)
        let records: [(input: String, output: String, isDict: Bool, isCode: Bool, kind: String, sig: String)] = [
            ("synthetic-dict", "definition", true, false, "dict", "dict-sig"),
            ("synthetic-code", "print(1)", false, true, "code", "code-sig"),
            (unicodeInput, unicodeOutput, false, false, "ocr", "\u{4e2d}-ocr-sig")
        ]
        let saved = session.terminal("config_save")
        let added = records.indices.map { session.terminal("add\($0)") }
        session.connection.saveConfiguration(["font_size": .integer(23)], id: "config_save")
        for (index, record) in records.enumerated() {
            session.connection.addHistory(input: record.input, output: record.output, isDict: record.isDict,
                                          isCode: record.isCode, kind: record.kind, sig: record.sig,
                                          limit: 3, id: "add\(index)")
        }
        await fulfillment(of: [saved] + added, timeout: 20, enforceOrder: true)
        for id in ["config_save", "add0", "add1", "add2"] { session.assertOperation(id) }
        XCTAssertEqual(session.events.filter { $0.type == "started" && $0.id != "missing_history" }.map(\.id),
                       ["config_save", "add0", "add1", "add2"])
        for index in records.indices {
            XCTAssertEqual(session.result("add\(index)")?.payload["recorded"], .bool(true))
            XCTAssertTrue(HistoryDocument.validRevision(session.result("add\(index)")?.payload["revision"]))
        }
        let all = session.terminal("all")
        session.connection.loadHistory(id: "all")
        await fulfillment(of: [all], timeout: 10)
        session.assertOperation("all")
        let entries = try session.historyEntries("all")
        XCTAssertEqual(entries.count, 3)
        XCTAssertGreaterThan(try JSONValue.object(try XCTUnwrap(session.result("all")?.payload)).encoded().count,
                             ConfigurationDocument.maxBytes)
        for record in records {
            let entry = try XCTUnwrap(entries.first { $0["input"] == .string(record.input) })
            XCTAssertEqual(entry["output"], .string(record.output))
            XCTAssertEqual(entry["is_dict"], .bool(record.isDict))
            XCTAssertEqual(entry["is_code"], .bool(record.isCode))
            XCTAssertEqual(entry["kind"], .string(record.kind))
            XCTAssertEqual(entry["sig"], .string(record.sig))
            XCTAssertFalse(try XCTUnwrap(entry["ts"]?.string).isEmpty)
        }
        let first = session.terminal("first")
        session.connection.loadHistory(pageSize: 1, id: "first")
        await fulfillment(of: [first], timeout: 10)
        let cursor = try XCTUnwrap(session.result("first")?.payload["next_cursor"])
        XCTAssertNotEqual(cursor, .null)
        let rest = session.terminal("rest")
        session.connection.loadHistory(pageSize: 2, cursor: cursor, id: "rest")
        await fulfillment(of: [rest], timeout: 10)
        session.assertOperation("first")
        session.assertOperation("rest")
        XCTAssertEqual(try session.historyEntries("first") + session.historyEntries("rest"), entries)
        XCTAssertEqual(session.result("first")?.payload["revision"], session.result("all")?.payload["revision"])
        XCTAssertEqual(session.result("rest")?.payload["next_cursor"], .null)
        let stored = try Data(contentsOf: history)
        XCTAssertEqual(try JSONValue.parse(stored), .array(entries.map(JSONValue.object)))

        let limited = session.terminal("limited")
        session.connection.addHistory(input: "synthetic-text", output: "translated", isDict: false,
                                      isCode: false, kind: "text", sig: "text-sig", limit: 2, id: "limited")
        await fulfillment(of: [limited], timeout: 10)
        session.assertOperation("limited")
        XCTAssertNotEqual(session.result("limited")?.payload["revision"], session.result("all")?.payload["revision"])
        let expired = session.terminal("after_add")
        session.connection.loadHistory(pageSize: 1, cursor: cursor, id: "after_add")
        await fulfillment(of: [expired], timeout: 10)
        session.assertHistoryFailure("after_add", code: "history_cursor_expired")
        let limitedPage = session.terminal("limited_page")
        session.connection.loadHistory(pageSize: 1, id: "limited_page")
        await fulfillment(of: [limitedPage], timeout: 10)
        XCTAssertEqual(session.result("limited_page")?.payload["total"], .integer(2))
        let reconnectCursor = try XCTUnwrap(session.result("limited_page")?.payload["next_cursor"])
        XCTAssertNotEqual(reconnectCursor, .null)
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let generation = reopened.terminal("old_generation")
        reopened.connection.loadHistory(cursor: reconnectCursor, id: "old_generation")
        await fulfillment(of: [generation], timeout: 10)
        reopened.assertHistoryFailure("old_generation", code: "history_cursor_expired")
        let reload = reopened.terminal("reload")
        reopened.connection.loadHistory(id: "reload")
        await fulfillment(of: [reload], timeout: 10)
        let remaining = try reopened.historyEntries("reload")
        XCTAssertEqual(remaining.count, 2)
        XCTAssertTrue(remaining.contains { $0["input"] == .string(unicodeInput) })
        XCTAssertTrue(remaining.contains { $0["input"] == .string("synthetic-text") })
        let beforeClear = reopened.terminal("before_clear")
        reopened.connection.loadHistory(pageSize: 1, id: "before_clear")
        await fulfillment(of: [beforeClear], timeout: 10)
        let clearCursor = try XCTUnwrap(reopened.result("before_clear")?.payload["next_cursor"])
        let cleared = reopened.terminal("clear")
        reopened.connection.clearHistory(id: "clear")
        await fulfillment(of: [cleared], timeout: 10)
        reopened.assertOperation("clear")
        XCTAssertEqual(reopened.result("clear")?.payload["cleared"], .bool(true))
        let afterClear = reopened.terminal("after_clear")
        reopened.connection.loadHistory(cursor: clearCursor, id: "after_clear")
        await fulfillment(of: [afterClear], timeout: 10)
        reopened.assertHistoryFailure("after_clear", code: "history_cursor_expired")
        let config = reopened.terminal("config")
        reopened.connection.loadConfiguration(id: "config")
        await fulfillment(of: [config], timeout: 10)
        reopened.assertOperation("config")
        XCTAssertEqual(reopened.result("config")?.payload["config"]?.object?["font_size"], .integer(23))
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty)

        let final = ConfigurationNotices()
        defer { final.connection.forceStop() }
        final.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [final.ready], timeout: 10)
        let empty = final.terminal("empty")
        final.connection.loadHistory(id: "empty")
        await fulfillment(of: [empty], timeout: 10)
        XCTAssertEqual(try final.historyEntries("empty"), [])
        let newAdd = final.terminal("new_add")
        final.connection.addHistory(input: "synthetic-after-clear", output: "new", isDict: false,
                                    isCode: false, kind: "text", sig: "", limit: 1, id: "new_add")
        await fulfillment(of: [newAdd], timeout: 10)
        final.assertOperation("new_add")
        let newLoad = final.terminal("new_load")
        final.connection.loadHistory(id: "new_load")
        await fulfillment(of: [newLoad], timeout: 10)
        XCTAssertEqual(try final.historyEntries("new_load").map { $0["input"] }, [.string("synthetic-after-clear")])
        final.connection.stop()
        await fulfillment(of: [final.stopped], timeout: 10)
        XCTAssertTrue(final.failures.isEmpty)
    }

    @MainActor
    func testBundledHistoryCorruptOversizedAndRejectedAddsPreserveBytes() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let history = context.config.deletingLastPathComponent().appendingPathComponent("history.json")
        try FileManager.default.createDirectory(at: history.deletingLastPathComponent(), withIntermediateDirectories: true)
        let corrupt = Data("[{\"input\":\"synthetic\",\"input\":\"duplicate\"}]".utf8)
        try corrupt.write(to: history)
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        for id in ["corrupt", "corrupt_again"] {
            let terminal = session.terminal(id)
            session.connection.loadHistory(id: id)
            await fulfillment(of: [terminal], timeout: 10)
            session.assertHistoryFailure(id, code: "invalid_history")
            XCTAssertEqual(try Data(contentsOf: history), corrupt)
        }

        let tailID = String(repeating: "t", count: 64)
        func completionFrame(_ payload: [String: JSONValue]) throws -> Data {
            try JSONValue.object([
                "v": .integer(1), "id": .string(tailID), "seq": .integer(2),
                "type": .string("completed"), "payload": .object(payload)
            ]).encoded()
        }
        let revision = JSONValue.string(String(repeating: "a", count: 64))
        var tailPayload: [String: JSONValue] = [
            "entries": .array([.object(["input": .string("")]), .object([:])]),
            "revision": revision, "total": .integer(2), "next_cursor": .null
        ]
        let tailOverhead = try completionFrame(tailPayload).count + 1
        let largeLegacy = JSONValue.object(["input": .string(String(repeating: "a", count: 65_536 - tailOverhead))])
        let tailEntries = JSONValue.array([largeLegacy, .object([:])])
        tailPayload["entries"] = tailEntries
        XCTAssertEqual(try completionFrame(tailPayload).count + 1, 65_536)
        var prefix = tailPayload
        prefix["entries"] = .array([largeLegacy])
        prefix["next_cursor"] = .object(["revision": revision, "offset": .integer(1)])
        XCTAssertGreaterThan(try completionFrame(prefix).count + 1, 65_536)
        let tailBytes = try tailEntries.encoded()
        try tailBytes.write(to: history)
        let tail = session.terminal(tailID)
        session.connection.loadHistory(pageSize: 2, id: tailID)
        await fulfillment(of: [tail], timeout: 10)
        session.assertOperation(tailID)
        let receivedTail = try XCTUnwrap(session.result(tailID)?.payload)
        XCTAssertTrue(receivedTail["entries"] == tailEntries)
        XCTAssertEqual(receivedTail["next_cursor"], .null)
        XCTAssertEqual(try completionFrame(receivedTail).count + 1, 65_536)
        XCTAssertTrue(try Data(contentsOf: history) == tailBytes, "Legacy reads must not rewrite the file")

        let oversizedEntry = try JSONValue.array([.object([
            "input": .string(String(repeating: "a", count: 65_536))
        ])]).encoded()
        try oversizedEntry.write(to: history)
        let oversizedRead = session.terminal("oversized_read")
        session.connection.loadHistory(pageSize: 1, id: "oversized_read")
        await fulfillment(of: [oversizedRead], timeout: 10)
        session.assertHistoryFailure("oversized_read", code: "history_entry_too_large")
        XCTAssertEqual(try Data(contentsOf: history), oversizedEntry)

        let legacy = JSONValue.object([
            "input": .string("synthetic-preserved"), "output": .null, "kind": .string("legacy-kind"),
            "ts": .null, "sig": .null, "future": .object(["x": .array([.bool(true), .integer(1)])])
        ])
        let before = try JSONValue.array([legacy]).encoded()
        try before.write(to: history)
        let validRead = session.terminal("valid_read")
        session.connection.loadHistory(id: "valid_read")
        await fulfillment(of: [validRead], timeout: 10)
        session.assertOperation("valid_read")
        XCTAssertEqual(try session.historyEntries("valid_read").map(JSONValue.object), [legacy])
        var payload: [String: JSONValue] = [
            "operation": .string("history_add"), "input": .string(String(repeating: "\u{0}", count: 10_000)),
            "output": .string(""), "is_dict": .bool(false), "is_code": .bool(false),
            "kind": .string("text"), "sig": .string(""), "limit": .integer(10)
        ]
        let overhead = try ClientMessage(id: "oversize", type: "request", payload: payload).encoded().count
        payload["output"] = .string(String(repeating: "a", count: 65_536 - overhead))
        let admissible = ClientMessage(id: "oversize", type: "request", payload: payload)
        XCTAssertEqual(try admissible.encoded().count, 65_536)
        let rejected = session.terminal("oversize")
        session.connection.send(admissible)
        await fulfillment(of: [rejected], timeout: 10)
        XCTAssertEqual(session.result("oversize")?.type, "failed")
        XCTAssertEqual(session.result("oversize")?.safeFailureCode, "history_entry_too_large")
        XCTAssertEqual(try Data(contentsOf: history), before)
        let unchanged = session.terminal("unchanged")
        session.connection.loadHistory(id: "unchanged")
        await fulfillment(of: [unchanged], timeout: 10)
        XCTAssertEqual(try session.historyEntries("unchanged").map(JSONValue.object), [legacy])
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)

        for invalidField in [
            ["kind": JSONValue.string("legacy-kind")],
            ["is_dict": .integer(1)],
            ["input": .string(String(repeating: "\u{1f600}", count: 6001))],
            ["input": .string(String(repeating: "\u{0}", count: 24_000))]
        ] {
            let invalid = ConfigurationNotices()
            defer { invalid.connection.forceStop() }
            invalid.connection.startBusiness(runtime: context.runtime, home: context.home)
            await fulfillment(of: [invalid.ready], timeout: 10)
            var request: [String: JSONValue] = [
                "operation": .string("history_add"), "input": .string("synthetic"), "output": .string("value"),
                "is_dict": .bool(false), "is_code": .bool(false), "kind": .string("text"),
                "sig": .string("sig"), "limit": .integer(1)
            ]
            request.merge(invalidField) { _, new in new }
            invalid.connection.send(ClientMessage(id: "invalid", type: "request", payload: request))
            await fulfillment(of: [invalid.stopped], timeout: 10)
            XCTAssertTrue(invalid.events.allSatisfy { $0.id != "invalid" })
            let expected: ProbeError = invalidField["input"] == .string(String(repeating: "\u{0}", count: 24_000)) ?
                .frameTooLarge : .invalidPayload
            XCTAssertEqual(invalid.failures, [expected])
            XCTAssertEqual(try Data(contentsOf: history), before)
        }
        let names = try FileManager.default.contentsOfDirectory(atPath: history.deletingLastPathComponent().path)
        XCTAssertFalse(names.contains { $0.hasPrefix(".tmp_") })
    }

    @MainActor
    func testBundledHistoryCompetingHelpersReleaseBothOwners() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let owner = ConfigurationNotices()
        defer { owner.connection.forceStop() }
        owner.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [owner.ready], timeout: 10)
        let saved = owner.terminal("config_save")
        let added = owner.terminal("history_add")
        owner.connection.saveConfiguration(["font_size": .integer(21)], id: "config_save")
        owner.connection.addHistory(input: "synthetic-owner", output: "kept", isDict: false,
                                    isCode: false, kind: "text", sig: "owner-sig", limit: 10, id: "history_add")
        await fulfillment(of: [saved, added], timeout: 10, enforceOrder: true)
        owner.assertOperation("config_save")
        owner.assertOperation("history_add")

        let competitor = ConfigurationNotices()
        defer { competitor.connection.forceStop() }
        let bootstrap = competitor.terminal("hello")
        competitor.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [bootstrap, competitor.stopped], timeout: 10, enforceOrder: true)
        XCTAssertEqual(competitor.events.map(\.type), ["failed"])
        XCTAssertEqual(competitor.result("hello")?.safeFailureCode, "config_in_use")
        XCTAssertEqual(competitor.failures, [.configInUse])
        let live = owner.terminal("live")
        owner.connection.loadHistory(id: "live")
        await fulfillment(of: [live], timeout: 10)
        owner.assertOperation("live")
        XCTAssertEqual(try owner.historyEntries("live").first?["input"], .string("synthetic-owner"))
        owner.connection.stop()
        await fulfillment(of: [owner.stopped], timeout: 10)
        XCTAssertTrue(owner.failures.isEmpty)

        let successor = ConfigurationNotices()
        defer { successor.connection.forceStop() }
        successor.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [successor.ready], timeout: 10)
        let config = successor.terminal("config")
        let history = successor.terminal("history")
        successor.connection.loadConfiguration(id: "config")
        successor.connection.loadHistory(id: "history")
        await fulfillment(of: [config, history], timeout: 10, enforceOrder: true)
        successor.assertOperation("config")
        successor.assertOperation("history")
        XCTAssertEqual(successor.result("config")?.payload["config"]?.object?["font_size"], .integer(21))
        XCTAssertEqual(try successor.historyEntries("history"), try owner.historyEntries("live"))
        let clearStarted = successor.started("clear")
        let cleared = successor.terminal("clear")
        successor.connection.clearHistory(id: "clear")
        await fulfillment(of: [clearStarted], timeout: 10)
        successor.connection.stop()
        await fulfillment(of: [cleared, successor.stopped], timeout: 10, enforceOrder: true)
        successor.assertOperation("clear")
        XCTAssertEqual(successor.events.last?.type, "completed")
        XCTAssertTrue(successor.events.last?.payload.isEmpty == true)
        XCTAssertTrue(successor.failures.isEmpty, "Normal stop must drain the started history write")
        let historyFile = context.config.deletingLastPathComponent().appendingPathComponent("history.json")
        XCTAssertFalse(FileManager.default.fileExists(atPath: historyFile.path))
        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startBusiness(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let empty = reopened.terminal("empty")
        reopened.connection.loadHistory(id: "empty")
        await fulfillment(of: [empty], timeout: 10)
        reopened.assertOperation("empty")
        XCTAssertEqual(try reopened.historyEntries("empty"), [])
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty)
    }

    @MainActor
    func testBundledWorkerStartFailureFramesAreDeterminateForAllBusinessOperations() async throws {
        let requests = [
            ClientMessage(id: "config_load", type: "request", payload: ["operation": .string("config_load")]),
            ClientMessage(id: "config_save", type: "request", payload: [
                "operation": .string("config_save"), "config": .object(["font_size": .integer(21)])
            ]),
            ClientMessage(id: "history_load", type: "request", payload: [
                "operation": .string("history_load"), "page_size": .integer(1), "cursor": .null
            ]),
            ClientMessage(id: "history_add", type: "request", payload: [
                "operation": .string("history_add"), "input": .string("synthetic"), "output": .string("never"),
                "is_dict": .bool(false), "is_code": .bool(false), "kind": .string("text"),
                "sig": .string(""), "limit": .integer(10)
            ]),
            ClientMessage(id: "history_clear", type: "request", payload: ["operation": .string("history_clear")])
        ]
        let script = """
        from pathlib import Path
        import signal
        import sys
        import threading
        signal.alarm(10)
        core = Path(sys.argv[1]).resolve()
        sys.path.insert(0, str(core))
        from cc_macos import server
        assert Path(server.__file__).resolve() == core / "cc_macos" / "server.py"
        def fail_start(thread):
            if thread.name != "cc-macos-configuration":
                raise AssertionError("unexpected synthetic worker")
            raise RuntimeError("synthetic thread start failure")
        threading.Thread.start = fail_start
        raise SystemExit(server.main(["--config-home", sys.argv[2], "--application-id", sys.argv[3]]))
        """
        for existing in [false, true] {
            for request in requests {
                let context = try configurationContext()
                defer { removeConfigurationHome(context.home) }
                let directory = context.config.deletingLastPathComponent()
                let history = directory.appendingPathComponent("history.json")
                let configBytes = Data(#"{"font_size":"16","future":"keep"}"#.utf8)
                let historyBytes = Data(#"[{"input":"synthetic","output":"keep","kind":"text"}]"#.utf8)
                if existing {
                    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                    try configBytes.write(to: context.config)
                    try historyBytes.write(to: history)
                }
                let process = Process(), input = Pipe(), output = Pipe(), errors = Pipe()
                var handles = [input.fileHandleForReading, input.fileHandleForWriting,
                               output.fileHandleForReading, output.fileHandleForWriting,
                               errors.fileHandleForReading, errors.fileHandleForWriting]
                func close(_ handle: FileHandle) throws {
                    try handle.close()
                    handles.removeAll { $0 === handle }
                }
                defer {
                    if process.isRunning {
                        process.terminate()
                        process.waitUntilExit()
                    }
                    for handle in handles {
                        do { try handle.close() }
                        catch { XCTFail("Synthetic worker failure pipe cleanup failed") }
                    }
                }
                process.executableURL = context.runtime.executable
                process.arguments = ["-I", "-B", "-c", script, context.runtime.launcher.deletingLastPathComponent().path,
                                     context.home.path, try context.runtime.configurationApplicationIdentifier()]
                process.currentDirectoryURL = context.home
                process.environment = ["PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                                       "HOME": context.home.path, "TMPDIR": context.home.path]
                process.standardInput = input
                process.standardOutput = output
                process.standardError = errors
                guard fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
                    throw ProbeError.writeFailed
                }
                try process.run()
                try close(input.fileHandleForReading)
                try close(output.fileHandleForWriting)
                try close(errors.fileHandleForWriting)
                let hello = ClientMessage(id: "hello", type: "hello")
                try input.fileHandleForWriting.write(contentsOf: try hello.encoded() + request.encoded())
                try close(input.fileHandleForWriting)
                let stdout = output.fileHandleForReading.readDataToEndOfFile()
                let stderr = errors.fileHandleForReading.readDataToEndOfFile()
                try close(output.fileHandleForReading)
                try close(errors.fileHandleForReading)
                process.waitUntilExit()
                XCTAssertEqual(process.terminationReason, .exit)
                XCTAssertEqual(process.terminationStatus, 2)
                XCTAssertEqual(stderr, Data("cc_macos:worker_start_failed\n".utf8))
                var framer = LineFramer()
                let frames = try framer.append(stdout)
                try framer.finish()
                XCTAssertEqual(frames.count, 3)
                guard frames.count == 3 else { throw ProbeError.invalidEnvelope }
                // Feed actual Python frames to the exact state machine used by HelperConnection.
                var state = ProtocolState(mode: .configuration)
                try state.register(hello)
                XCTAssertEqual(try state.receive(frames[0]).type, "ready")
                try state.register(request)
                let accepted = try state.receive(frames[1])
                XCTAssertEqual(accepted.type, "accepted")
                XCTAssertEqual(accepted.sequence, 0)
                let failed = try state.receive(frames[2])
                XCTAssertEqual(failed.type, "failed")
                XCTAssertEqual(failed.sequence, 1)
                XCTAssertEqual(failed.safeFailureCode, "worker_start_failed")
                XCTAssertTrue(failed.isTerminal)
                XCTAssertFalse(state.hasPendingResponses)
                XCTAssertFalse(state.hasPendingConfiguration)
                XCTAssertFalse(state.hasPendingHistory)
                XCTAssertNil(state.pendingOutcomeUnknown)
                let successor = ConfigurationNotices()
                defer { successor.connection.forceStop() }
                successor.connection.startBusiness(runtime: context.runtime, home: context.home)
                await fulfillment(of: [successor.ready], timeout: 10)
                successor.connection.stop()
                await fulfillment(of: [successor.stopped], timeout: 10)
                XCTAssertTrue(successor.failures.isEmpty)
                if existing {
                    XCTAssertEqual(try Data(contentsOf: context.config), configBytes)
                    XCTAssertEqual(try Data(contentsOf: history), historyBytes)
                } else {
                    XCTAssertFalse(FileManager.default.fileExists(atPath: context.config.path))
                    XCTAssertFalse(FileManager.default.fileExists(atPath: history.path))
                }
                let expected = existing
                    ? Set(["config.json", "history.json", "config.json.lock", "history.json.lock"])
                    : Set(["config.json.lock", "history.json.lock"])
                XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: directory.path)), expected)
            }
        }
    }

    @MainActor
    func testOptionalBundledHelperHandshakeFixtureAndShutdown() async throws {
        guard let app = ProcessInfo.processInfo.environment["CC_TRANSLATE_APP"], !app.isEmpty else {
            throw XCTSkip("Set CC_TRANSLATE_APP to a built .app to test its bundled Python; no host fallback.")
        }
        let runtime = try BundleRuntime(appURL: URL(fileURLWithPath: app, isDirectory: true))
        let ready = expectation(description: "ready")
        let fixture = expectation(description: "fixture completed")
        let runtimeProbe = expectation(description: "runtime completed without network")
        let stopped = expectation(description: "helper exited")
        var connection: HelperConnection?
        connection = HelperConnection { notice in
            MainActor.assumeIsolated {
                switch notice {
                case .event(let event):
                    if event.type == "ready" {
                        ready.fulfill()
                        connection?.send(ClientMessage(id: "fixture", type: "request", payload: [
                            "operation": .string("fixture"), "text": .string("integration synthetic")
                        ]))
                    }
                    if event.id == "fixture", event.type == "completed" {
                        XCTAssertEqual(event.payload["fixture"], .bool(true))
                        XCTAssertNotNil(event.payload["text"]?.string)
                        fixture.fulfill()
                        connection?.send(ClientMessage(id: "runtime", type: "request", payload: [
                            "operation": .string("runtime_probe"), "https": .bool(false)
                        ]))
                    }
                    if event.id == "runtime", event.type == "completed" {
                        XCTAssertEqual(event.payload["python"]?.object?["isolated"], .bool(true))
                        XCTAssertEqual(event.payload["python"]?.object?["bytecode_disabled"], .bool(true))
                        XCTAssertEqual(event.payload["python"]?.object?["bundle_runtime"], .bool(true))
                        XCTAssertEqual(event.payload["sqlite"]?.object?["status"], .string("passed"))
                        XCTAssertEqual(event.payload["dictionary"]?.object?["status"], .string("passed"))
                        XCTAssertEqual(event.payload["dictionary"]?.object?["read_only"], .bool(true))
                        XCTAssertEqual(event.payload["dictionary"]?.object?["sources_preserved"], .bool(true))
                        XCTAssertEqual(event.payload["dictionary"]?.object?["reopened"], .bool(true))
                        XCTAssertEqual(event.payload["catalog_storage_fixture"], .object([
                            "status": .string("passed"), "cli_simulated": .bool(true),
                            "cache_verified": .bool(true), "reopen_verified": .bool(true)
                        ]))
                        XCTAssertEqual(event.payload["catalog_process_fixture"], .object([
                            "status": .string("passed"), "fixture": .bool(true), "process_verified": .bool(true),
                            "cache_verified": .bool(true), "reopen_verified": .bool(true)
                        ]))
                        XCTAssertEqual(event.payload["codex_config_fixture"], .object([
                            "status": .string("passed"), "fixture": .bool(true),
                            "methods_verified": .bool(true), "routing_preserved": .bool(true)
                        ]))
                        XCTAssertEqual(event.payload["https"]?.object?["status"], .string("not_run"))
                        runtimeProbe.fulfill()
                        connection?.stop()
                    }
                case .failure(let error): XCTFail("Bundled helper failed: \(error.rawValue)")
                case .stopped: stopped.fulfill()
                }
            }
        }
        connection?.start(runtime: runtime)
        await fulfillment(of: [ready, fixture, runtimeProbe, stopped], timeout: 20, enforceOrder: true)
        connection?.stop()
        connection = nil
    }
}
