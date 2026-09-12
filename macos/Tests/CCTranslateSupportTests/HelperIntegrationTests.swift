import XCTest
@testable import CCTranslateSupport

final class HelperIntegrationTests: XCTestCase {
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
