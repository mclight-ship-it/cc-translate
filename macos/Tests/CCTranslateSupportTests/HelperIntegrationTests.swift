import XCTest
@testable import CCTranslateSupport

final class HelperIntegrationTests: XCTestCase {
    @MainActor
    private final class ConfigurationNotices {
        let ready = XCTestExpectation(description: "configuration ready")
        let stopped = XCTestExpectation(description: "configuration helper exited")
        private(set) var events: [ServerEvent] = []
        private(set) var failures: [ProbeError] = []
        private var terminals: [String: XCTestExpectation] = [:]
        private(set) var connection: HelperConnection!

        init() {
            connection = HelperConnection { [weak self] notice in
                MainActor.assumeIsolated {
                    guard let self = self else { return }
                    switch notice {
                    case .event(let event):
                        self.events.append(event)
                        if event.type == "ready" { self.ready.fulfill() }
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

        func result(_ id: String) -> ServerEvent? {
            events.last { $0.id == id && $0.isTerminal }
        }

        func assertOperation(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
            let operation = events.filter { $0.id == id }
            XCTAssertEqual(operation.map(\.type), ["accepted", "started", "completed"], file: file, line: line)
            XCTAssertEqual(operation.map(\.sequence), [0, 1, 2], file: file, line: line)
            XCTAssertTrue(operation.allSatisfy { $0.payload["fixture"] == nil }, file: file, line: line)
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
