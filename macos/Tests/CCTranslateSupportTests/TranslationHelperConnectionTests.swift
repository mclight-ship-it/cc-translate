import XCTest
@testable import CCTranslateSupport

final class TranslationHelperConnectionTests: XCTestCase {
    @MainActor
    private final class Notices {
        let ready = XCTestExpectation(description: "helper ready")
        let stopped = XCTestExpectation(description: "helper and pipes stopped")
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
            let result = XCTestExpectation(description: "\(id) terminal")
            terminals[id] = result
            return result
        }

        func started(_ id: String) -> XCTestExpectation {
            let result = XCTestExpectation(description: "\(id) started")
            starts[id] = result
            return result
        }
    }

    private struct Fixture {
        let root: URL
        let home: URL
        let runtime: BundleRuntime
        var codex: URL { home.appendingPathComponent("codex-never-executed") }
        var environment: [String: String] { ["HOME": home.path, "PATH": "/synthetic/cli/bin"] }
    }

    private func fixture(script: String) throws -> Fixture {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
            .appendingPathComponent(".translation-connection-\(UUID().uuidString)", isDirectory: true)
        let app = root.appendingPathComponent("Synthetic.app", isDirectory: true)
        let home = root.appendingPathComponent("home", isDirectory: true)
        let bin = app.appendingPathComponent("Contents/Helpers/python/bin", isDirectory: true)
        let core = app.appendingPathComponent("Contents/Resources/Core", isDirectory: true)
        do {
            for directory in [home, bin, core] {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            }
            let executable = bin.appendingPathComponent("python3")
            try Data(script.utf8).write(to: executable)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
            try Data("# synthetic launcher\n".utf8).write(to: core.appendingPathComponent("launch.py"))
            let plist = try PropertyListSerialization.data(fromPropertyList: [
                "CFBundleIdentifier": "dev.cc-translate.synthetic"
            ], format: .xml, options: 0)
            try plist.write(to: app.appendingPathComponent("Contents/Info.plist"))
            return Fixture(root: root, home: home, runtime: try BundleRuntime(appURL: app))
        } catch {
            do { try FileManager.default.removeItem(at: root) }
            catch { XCTFail("Synthetic helper fixture initialization cleanup failed") }
            throw error
        }
    }

    private func remove(_ fixture: Fixture) {
        do { try FileManager.default.removeItem(at: fixture.root) }
        catch { XCTFail("Synthetic helper fixture cleanup failed") }
    }

    private func emit(_ id: String, _ seq: Int64, _ type: String,
                      _ payload: [String: JSONValue] = [:]) throws -> String {
        let data = try JSONValue.object([
            "v": .integer(1), "id": .string(id), "seq": .integer(seq),
            "type": .string(type), "payload": .object(payload)
        ]).encoded()
        let quoted = String(decoding: data, as: UTF8.self).replacingOccurrences(of: "'", with: "'\\''")
        return "printf '%s\\n' '\(quoted)'"
    }

    private let readLine = "IFS= read -r line"
    private let shutdown = #"""
    IFS= read -r line
    printf '%s\n' "$line" | /usr/bin/sed 's/"type":"shutdown"/"seq":0,"type":"completed"/'
    """#
    private let operation: [String: JSONValue] = ["operation": .string("translate")]
    private var completion: [String: JSONValue] {
        [
            "text": .string("synthetic translated text"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("text"), "target_lang": .string("zh"), "summarize": .bool(false),
            "history": .string("disabled"), "history_error": .null
        ]
    }

    private func connectedScript(_ body: String, mode: ProtocolState.Mode = .translation) throws -> String {
        let operations = mode == .diagnostic ? ["fixture", "runtime_probe"] :
            ["config_load", "config_save", "history_load", "history_add", "history_clear"] +
                DictionaryRequest.operations.sorted() + (mode == .translation ? ["translate", "result_action", "model_catalog"] : [])
        var ready: [String: JSONValue] = [
            "protocol": .integer(1), "capabilities": .array(operations.map(JSONValue.string)),
            "max_frame_bytes": .integer(65_536), "fixture": .bool(mode == .diagnostic)
        ]
        if mode == .translation { ready["backend"] = .string("native_appserver") }
        return "#!/bin/sh\nset -eu\n\(readLine)\n\(try emit("hello", 0, "ready", ready))\n\(body)\n"
    }

    func testTranslationEnvironmentIsolatedTypedAndCompactByteBounded() throws {
        let home = URL(fileURLWithPath: "/synthetic/home", isDirectory: true)
        let command = URL(fileURLWithPath: "/synthetic/bin/codex")
        let environment = [
            "HOME": home.path, "PATH": "/synthetic/cli/bin", "LANG": "synthetic-locale",
            "TOKEN": "synthetic-private-value", "PYTHONPATH": "/synthetic/python",
            "DYLD_INSERT_LIBRARIES": "/synthetic/library", "CODEX_HOME": "/synthetic/codex",
            "TEXT": "\u{4e2d}\u{6587}\"\\\n"
        ]
        let helper = try HelperConnection.translationEnvironment(home: home, codexCommand: command,
                                                                 environment: environment)
        XCTAssertEqual(Set(helper.keys), ["PATH", "LANG", "HOME", "CC_TRANSLATE_CODEX_ENV"])
        XCTAssertEqual(helper["PATH"], "/usr/bin:/bin")
        XCTAssertEqual(helper["LANG"], "en_US.UTF-8")
        XCTAssertEqual(helper["HOME"], home.path)
        let encoded = try XCTUnwrap(helper["CC_TRANSLATE_CODEX_ENV"])
        XCTAssertEqual(try JSONValue.parse(Data(encoded.utf8)), .object(environment.mapValues(JSONValue.string)))
        for invalid in [
            environment.filter { $0.key != "HOME" }, environment.filter { $0.key != "PATH" },
            environment.merging(["HOME": "/different/home"]) { _, new in new },
            environment.merging(["": "value"]) { _, new in new },
            environment.merging(["X=Y": "value"]) { _, new in new },
            environment.merging(["X\0Y": "value"]) { _, new in new },
            environment.merging(["TOKEN": "a\0b"]) { _, new in new }
        ] {
            XCTAssertThrowsError(try HelperConnection.translationEnvironment(
                home: home, codexCommand: command, environment: invalid
            )) { XCTAssertEqual($0 as? ProbeError, .translationUnavailable) }
        }
        XCTAssertThrowsError(try HelperConnection.translationEnvironment(
            home: home, codexCommand: XCTUnwrap(URL(string: "https://example.invalid/codex")), environment: environment
        ))
        var boundary = ["HOME": home.path, "PATH": "", "TOKEN": ""]
        let overhead = try JSONValue.object(boundary.mapValues(JSONValue.string)).encoded().count
        boundary["TOKEN"] = String(repeating: "a", count: 32_768 - overhead)
        let exact = try HelperConnection.translationEnvironment(home: home, codexCommand: command, environment: boundary)
        XCTAssertEqual(exact["CC_TRANSLATE_CODEX_ENV"]?.utf8.count, 32_768)
        boundary["TOKEN"]! += "\u{4e2d}"
        XCTAssertThrowsError(try HelperConnection.translationEnvironment(home: home, codexCommand: command, environment: boundary))
        boundary["TOKEN"] = String(repeating: "\"", count: 16_384)
        XCTAssertThrowsError(try HelperConnection.translationEnvironment(home: home, codexCommand: command, environment: boundary))
    }

    @MainActor
    func testStartTranslationPassesExplicitCommandAndIsolatesCLIEnvironmentAndRequestDefaults() async throws {
        let script = try connectedScript("""
        printf '%s\\n' "$@" > "$HOME/arguments"
        printf '%s' "$CC_TRANSLATE_CODEX_ENV" > "$HOME/cli-environment"
        printf '%s\\n' "$PATH" "$LANG" "$HOME" "${TOKEN-unset}" "${PYTHONPATH-unset}" "${CODEX_HOME-unset}" > "$HOME/loader-environment"
        \(readLine)
        printf '%s' "$line" > "$HOME/default-request"
        \(try emit("one", 0, "accepted", operation))
        \(try emit("one", 1, "started", operation))
        \(try emit("one", 2, "completed", completion))
        \(readLine)
        printf '%s' "$line" > "$HOME/explicit-request"
        \(try emit("two", 0, "accepted", operation))
        \(try emit("two", 1, "started", operation))
        \(try emit("two", 2, "completed", completion))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        let environment = context.environment.merging([
            "TOKEN": "synthetic-secret", "PYTHONPATH": "/synthetic/python", "CODEX_HOME": "/synthetic/codex"
        ]) { _, new in new }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let one = notices.terminal("one")
        XCTAssertEqual(notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "one"), "one")
        await fulfillment(of: [one], timeout: 10)
        let two = notices.terminal("two")
        notices.connection.translate(text: "selection", appLanguage: "en_US", origin: "selection",
                                     useCache: false, recordHistory: false, id: "two", timeout: 30)
        await fulfillment(of: [two], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        let arguments = try String(contentsOf: context.home.appendingPathComponent("arguments"), encoding: .utf8)
        XCTAssertEqual(Array(arguments.components(separatedBy: "\n").dropLast()), [
            "-I", "-B", context.runtime.launcher.path, "--config-home", context.home.path,
            "--application-id", "dev.cc-translate.synthetic", "--codex-command", context.codex.path
        ])
        XCTAssertFalse(arguments.contains("synthetic-secret"))
        let cli = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("cli-environment")))
        XCTAssertEqual(cli, .object(environment.mapValues(JSONValue.string)))
        let loader = try String(contentsOf: context.home.appendingPathComponent("loader-environment"), encoding: .utf8)
        XCTAssertEqual(loader, "/usr/bin:/bin\nen_US.UTF-8\n\(context.home.path)\nunset\nunset\nunset\n")
        let defaults = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("default-request")))
        XCTAssertEqual(defaults.object?["payload"], .object([
            "operation": .string("translate"), "text": .string("synthetic"), "app_language": .string("zh_CN"),
            "origin": .string("text"), "use_cache": .bool(true), "record_history": .bool(true)
        ]))
        let explicit = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("explicit-request")))
        XCTAssertEqual(explicit.object?["payload"], .object([
            "operation": .string("translate"), "text": .string("selection"), "app_language": .string("en_US"),
            "origin": .string("selection"), "use_cache": .bool(false), "record_history": .bool(false)
        ]))
    }

    @MainActor
    func testOCRTextTypedAPIKeepsUnicodeLayoutAndOriginWithoutImageTransport() async throws {
        let text = "Heading\n  1. \u{4e2d}\u{6587}\n  2. second line"
        var result = completion
        result["kind"] = .string("ocr")
        let script = try connectedScript("""
        \(readLine)
        printf '%s' "$line" > "$HOME/ocr-request"
        \(try emit("ocr", 0, "accepted", operation))
        \(try emit("ocr", 1, "started", operation))
        \(try emit("ocr", 2, "delta", ["text": .string("partial"), "submitted": .bool(true)]))
        \(try emit("ocr", 3, "completed", result))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let terminal = notices.terminal("ocr")
        XCTAssertEqual(notices.connection.translate(text: text, appLanguage: "zh_CN", origin: "ocr",
                                                    useCache: false, id: "ocr"), "ocr")
        await fulfillment(of: [terminal], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        let captured = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("ocr-request")))
        XCTAssertEqual(captured.object?["payload"], .object([
            "operation": .string("translate"), "text": .string(text), "app_language": .string("zh_CN"),
            "origin": .string("ocr"), "use_cache": .bool(false), "record_history": .bool(true)
        ]))
        XCTAssertEqual(notices.events.filter { $0.id == "ocr" }.map(\.type), ["accepted", "started", "delta", "completed"])
        XCTAssertEqual(notices.events.first { $0.id == "ocr" && $0.type == "completed" }?.payload, result)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.codex.path))
    }

    @MainActor
    func testResultActionTypedAPIEncodesExactRequestAndStreamsWithoutHistory() async throws {
        let action: [String: JSONValue] = ["operation": .string("result_action")]
        var result = completion
        result["target_lang"] = .null
        let script = try connectedScript("""
        \(readLine)
        printf '%s' "$line" > "$HOME/action-request"
        \(try emit("action", 0, "accepted", action))
        \(try emit("action", 1, "started", action))
        \(try emit("action", 2, "delta", ["text": .string("partial"), "submitted": .bool(true)]))
        \(try emit("action", 3, "completed", result))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let terminal = notices.terminal("action")
        XCTAssertEqual(notices.connection.resultAction(
            .summary, text: "Primary result", appLanguage: "en_US", id: "action"), "action")
        await fulfillment(of: [terminal], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        let captured = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("action-request")))
        XCTAssertEqual(captured.object?["payload"], .object([
            "operation": .string("result_action"), "action": .string("summary"),
            "text": .string("Primary result"), "app_language": .string("en_US"), "target_language": .null
        ]))
        XCTAssertEqual(notices.events.filter { $0.id == "action" }.map(\.type),
                       ["accepted", "started", "delta", "completed"])
        XCTAssertEqual(notices.events.first { $0.id == "action" && $0.type == "completed" }?.payload, result)
    }

    @MainActor
    func testResultActionEOFReportsUnknownAheadOfStorageWithoutReplay() async throws {
        let action: [String: JSONValue] = ["operation": .string("result_action")]
        let script = try connectedScript("""
        \(readLine)
        \(readLine)
        \(readLine)
        \(try emit("action", 0, "accepted", action))
        \(try emit("action", 1, "started", action))
        exit 0
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        notices.connection.resultAction(.summary, text: "Primary result", appLanguage: "en_US", id: "action")
        notices.connection.loadConfiguration(id: "config")
        notices.connection.clearHistory(id: "history")
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertEqual(notices.failures, [.translationOutcomeUnknown])
        XCTAssertEqual(notices.events.map(\.type), ["ready", "accepted", "started"])
        XCTAssertFalse(notices.events.contains { $0.id == "action" && $0.isTerminal })
    }

    @MainActor
    func testInvalidTranslationEnvironmentStopsBeforeLaunchingHelper() async throws {
        let context = try fixture(script: "#!/bin/sh\nprintf launched > \"$HOME/launched\"\n")
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: ["HOME": context.home.path])
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertEqual(notices.failures, [.translationUnavailable])
        XCTAssertTrue(notices.events.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.home.appendingPathComponent("launched").path))
    }

    @MainActor
    func testLegacyStartsDoNotEnableCodexEnvironmentOrArguments() async throws {
        for mode in [ProtocolState.Mode.configuration, .diagnostic] {
            let script = try connectedScript("""
            test "${CC_TRANSLATE_CODEX_ENV-unset}" = unset
            test "$#" -eq \(mode == .diagnostic ? 3 : 7)
            \(shutdown)
            """, mode: mode)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            if mode == .diagnostic { notices.connection.start(runtime: context.runtime) }
            else { notices.connection.startBusiness(runtime: context.runtime, home: context.home) }
            await fulfillment(of: [notices.ready], timeout: 10)
            notices.connection.stop()
            await fulfillment(of: [notices.stopped], timeout: 10)
            XCTAssertTrue(notices.failures.isEmpty)
            XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: context.home.path).isEmpty)
        }
    }

    @MainActor
    func testTranslationEOFReportsUnknownAheadOfConfigurationAndHistoryWithoutReplay() async throws {
        let script = try connectedScript("""
        \(readLine)
        \(readLine)
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "started", operation))
        exit 0
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t")
        notices.connection.loadConfiguration(id: "config")
        notices.connection.clearHistory(id: "history")
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertEqual(notices.failures, [.translationOutcomeUnknown])
        XCTAssertEqual(notices.events.map(\.type), ["ready", "accepted", "started"])
    }

    @MainActor
    func testTranslationReservedInternalErrorBecomesUnknownWithoutInventedTerminal() async throws {
        let script = try connectedScript("""
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "started", operation))
        \(try emit("protocol", 0, "failed", ["code": .string("internal_error")]))
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t")
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertEqual(notices.failures, [.translationOutcomeUnknown])
        XCTAssertEqual(notices.events.map(\.type), ["ready", "accepted", "started"])
        XCTAssertFalse(notices.events.contains { $0.id == "t" && $0.isTerminal })
    }

    @MainActor
    func testTranslationShutdownCleanupFailureIsDeliveredAsDeterminateControlTerminal() async throws {
        let script = try connectedScript("""
        \(readLine)
        \(try emit("shutdown", 0, "failed", ["code": .string("provider_cleanup_failed")]))
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let shutdown = notices.terminal("shutdown")
        notices.connection.send(ClientMessage(id: "shutdown", type: "shutdown"))
        await fulfillment(of: [shutdown, notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        XCTAssertEqual(notices.events.last?.id, "shutdown")
        XCTAssertEqual(notices.events.last?.sequence, 0)
        XCTAssertEqual(notices.events.last?.type, "failed")
        XCTAssertEqual(notices.events.last?.payload, ["code": .string("provider_cleanup_failed")])
    }

    @MainActor
    func testTranslationForceStopAndTimeoutRemainUnknownWithoutSubmissionEvidence() async throws {
        for force in [true, false] {
            let script = try connectedScript("""
            \(readLine)
            \(try emit("t", 0, "accepted", operation))
            \(try emit("t", 1, "started", operation))
            while IFS= read -r line; do :; done
            """)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                               environment: context.environment)
            await fulfillment(of: [notices.ready], timeout: 10)
            let started = notices.started("t")
            notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t", timeout: force ? 20 : 1)
            await fulfillment(of: [started], timeout: 10)
            if force { notices.connection.forceStop() }
            await fulfillment(of: [notices.stopped], timeout: 10)
            XCTAssertEqual(notices.failures, [.translationOutcomeUnknown])
            XCTAssertEqual(notices.events.filter { $0.id == "t" }.map(\.type), ["accepted", "started"])
        }
    }

    func testBusinessTerminationGraceAllowsNativeCleanupAndDictionaryCommit() {
        for mode in [ProtocolState.Mode.translation, .configuration] {
            let business = HelperConnection.terminationGrace(for: mode)
            XCTAssertEqual(business.eof, 30)
            XCTAssertEqual(business.term, 30)
        }
        let diagnostic = HelperConnection.terminationGrace(for: .diagnostic)
        XCTAssertEqual(diagnostic.eof, 3)
        XCTAssertEqual(diagnostic.term, 1)
    }

    @MainActor
    func testTranslationFailureAndForceStopAllowCleanupBeyondFixtureGrace() async throws {
        for trigger in ["timeout", "force", "eof"] {
            let script = try connectedScript("""
            cleanup() {
                trap '' TERM
                /bin/sleep 5
                printf cleaned > "$HOME/cleanup-finished"
                exit 0
            }
            trap cleanup TERM
            \(readLine)
            \(try emit("t", 0, "accepted", operation))
            \(try emit("t", 1, "started", operation))
            \(trigger == "eof" ? "exec 1>&-" : ":")
            while IFS= read -r line; do :; done
            cleanup
            """)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                               environment: context.environment)
            await fulfillment(of: [notices.ready], timeout: 10)
            let started = notices.started("t")
            notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t",
                                         timeout: trigger == "timeout" ? 1 : 20)
            await fulfillment(of: [started], timeout: 10)
            if trigger == "force" { notices.connection.forceStop() }
            await fulfillment(of: [notices.stopped], timeout: 15)
            XCTAssertEqual(notices.failures, [.translationOutcomeUnknown], trigger)
            XCTAssertEqual(try String(contentsOf: context.home.appendingPathComponent("cleanup-finished"),
                                      encoding: .utf8), "cleaned", trigger)
            XCTAssertFalse(notices.events.contains { $0.id == "t" && $0.isTerminal }, trigger)
        }
    }

    @MainActor
    func testTranslationGracefulStopDrainsBeyondDiagnosticTerminationDeadline() async throws {
        let script = try connectedScript("""
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "started", operation))
        \(readLine)
        /bin/sleep 4
        \(try emit("t", 2, "completed", completion))
        printf '%s\\n' "$line" | /usr/bin/sed 's/"type":"shutdown"/"seq":0,"type":"completed"/'
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let started = notices.started("t")
        let terminal = notices.terminal("t")
        notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t")
        await fulfillment(of: [started], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [terminal, notices.stopped], timeout: 15)
        XCTAssertTrue(notices.failures.isEmpty)
        XCTAssertEqual(notices.events.filter { $0.id == "t" }.map(\.type), ["accepted", "started", "completed"])
    }

    @MainActor
    func testTranslationBootstrapAndPrestartWorkerFailuresStayDeterminate() async throws {
        let bootstrap = try fixture(script: """
        #!/bin/sh
        \(readLine)
        \(try emit("hello", 0, "failed", ["code": .string("translation_unavailable")]))
        """)
        defer { remove(bootstrap) }
        let unavailable = Notices()
        defer { unavailable.connection.forceStop() }
        unavailable.connection.startTranslation(runtime: bootstrap.runtime, home: bootstrap.home, codexCommand: bootstrap.codex,
                                               environment: bootstrap.environment)
        await fulfillment(of: [unavailable.stopped], timeout: 10)
        XCTAssertEqual(unavailable.failures, [.translationUnavailable])
        XCTAssertEqual(unavailable.events.first?.safeFailureCode, "translation_unavailable")

        let script = try connectedScript("""
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "failed", ["code": .string("worker_start_failed")]))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let terminal = notices.terminal("t")
        notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t")
        await fulfillment(of: [terminal], timeout: 10)
        notices.connection.send(ClientMessage(id: "shutdown", type: "shutdown"))
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        XCTAssertEqual(notices.events.filter { $0.id == "t" }.map(\.type), ["accepted", "failed"])
        XCTAssertEqual(notices.events.last?.id, "shutdown")
    }

    @MainActor
    func testConfirmedTranslationCancellationDrainsActualSubmittedTerminal() async throws {
        let script = try connectedScript("""
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "started", operation))
        \(readLine)
        \(try emit("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        \(try emit("t", 2, "delta", ["text": .string("in flight"), "submitted": .bool(true)]))
        \(try emit("t", 3, "cancelled", ["submitted": .bool(true)]))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let started = notices.started("t")
        let terminal = notices.terminal("t")
        notices.connection.translate(text: "synthetic", appLanguage: "zh_CN", id: "t")
        await fulfillment(of: [started], timeout: 10)
        let cancel = notices.terminal("cancel")
        notices.connection.send(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("t")]))
        await fulfillment(of: [cancel, terminal], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        XCTAssertEqual(notices.events.last { $0.id == "t" }?.payload, ["submitted": .bool(true)])
    }

    @MainActor
    func testDictionaryLookupUsesConfigurationTransportAndExactTypedRequest() async throws {
        let request = DictionaryRequest.lookup(text: "run", appLanguage: "en_US", origin: "selection",
                                               useCache: true, recordHistory: false)
        let expected = String(decoding: try ClientMessage(id: "lookup", type: "request", payload: request.payload)
            .encoded().dropLast(), as: UTF8.self).replacingOccurrences(of: "'", with: "'\\''")
        let local: [String: JSONValue] = [
            "text": .string("run\nverb\n1. Synthetic definition.\nSources: Fixture, Test license"),
            "submitted": .bool(false), "cached": .bool(false), "kind": .string("dict"),
            "target_lang": .null, "summarize": .bool(false), "history": .string("disabled"),
            "history_error": .null
        ]
        let operation: [String: JSONValue] = ["operation": .string(request.operation)]
        let script = try connectedScript("""
        case "$*" in *--codex-command*) exit 91 ;; esac
        \(readLine)
        [ "$line" = '\(expected)' ]
        \(try emit("lookup", 0, "accepted", operation))
        \(try emit("lookup", 1, "started", operation))
        \(try emit("lookup", 2, "completed", ["status": .string("hit"), "result": .object(local)]))
        \(shutdown)
        """, mode: .configuration)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [notices.ready], timeout: 10)
        let terminal = notices.terminal("lookup")
        notices.connection.dictionary(request, id: "lookup")
        await fulfillment(of: [terminal], timeout: 10)
        let response = try XCTUnwrap(notices.events.last { $0.id == "lookup" })
        XCTAssertEqual(try DictionaryLookupResult(payload: response.payload).result, local)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
    }

    @MainActor
    func testDictionaryCancellationDrainsNonModelTerminalBeforeShutdown() async throws {
        let operation: [String: JSONValue] = ["operation": .string("dictionary_lookup")]
        let script = try connectedScript("""
        \(readLine)
        \(try emit("lookup", 0, "accepted", operation))
        \(try emit("lookup", 1, "started", operation))
        \(readLine)
        \(try emit("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        \(try emit("lookup", 2, "cancelled"))
        \(shutdown)
        """, mode: .configuration)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [notices.ready], timeout: 10)
        let started = notices.started("lookup")
        let terminal = notices.terminal("lookup")
        notices.connection.dictionary(.lookup(text: "run", appLanguage: "en_US", origin: "text",
                                              useCache: true, recordHistory: true), id: "lookup")
        await fulfillment(of: [started], timeout: 10)
        let cancel = notices.terminal("cancel")
        notices.connection.send(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("lookup")]))
        await fulfillment(of: [cancel, terminal], timeout: 10)
        XCTAssertEqual(notices.events.last { $0.id == "lookup" }?.payload, [:])
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
    }

    @MainActor
    func testDictionaryEOFReportsLocalUnknownWithoutModelSubmissionOrReplay() async throws {
        let operation: [String: JSONValue] = ["operation": .string("dictionary_status")]
        let script = try connectedScript("""
        \(readLine)
        \(try emit("status", 0, "accepted", operation))
        \(try emit("status", 1, "started", operation))
        """, mode: .configuration)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [notices.ready], timeout: 10)
        notices.connection.dictionary(.status, id: "status")
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertEqual(notices.failures, [.dictionaryOutcomeUnknown])
        XCTAssertEqual(notices.events.filter { $0.id == "status" }.map(\.type), ["accepted", "started"])
    }

    @MainActor
    func testCatalogConvenienceSendsOnlyExplicitReadOnlyRequestAndAllowsLaterTranslation() async throws {
        let catalog: [String: JSONValue] = ["operation": .string("model_catalog")]
        let result: [String: JSONValue] = ["models": .array([
            .object(["id": .string("model-e\u{0301}"), "name": .string("Synthetic"), "description": .string("")])
        ])]
        let script = try connectedScript("""
        \(readLine)
        printf '%s\\n' "$line" > "$HOME/catalog-request.json"
        \(try emit("catalog", 0, "accepted", catalog))
        \(try emit("catalog", 1, "started", catalog))
        \(try emit("catalog", 2, "completed", result))
        \(readLine)
        \(try emit("t", 0, "accepted", operation))
        \(try emit("t", 1, "started", operation))
        \(try emit("t", 2, "completed", completion))
        \(shutdown)
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.home.appendingPathComponent("catalog-request.json").path))
        let listed = notices.terminal("catalog")
        XCTAssertEqual(notices.connection.modelCatalog(id: "catalog"), "catalog")
        await fulfillment(of: [listed], timeout: 10)
        let request = try JSONValue.parse(Data(contentsOf: context.home.appendingPathComponent("catalog-request.json")))
        XCTAssertEqual(request, .object(["v": .integer(1), "id": .string("catalog"), "type": .string("request"),
                                         "payload": .object(catalog)]))
        let events = notices.events.filter { $0.id == "catalog" }
        XCTAssertEqual(events.map(\.type), ["accepted", "started", "completed"])
        XCTAssertTrue(events.allSatisfy { $0.payload["submitted"] == nil })
        let models = try CodexModelEntry.decode(payload: XCTUnwrap(events.last?.payload))
        XCTAssertTrue(try XCTUnwrap(models.first).matchesID("model-e\u{0301}"))
        let translated = notices.terminal("t")
        notices.connection.translate(text: "synthetic", appLanguage: "en_US", id: "t")
        await fulfillment(of: [translated], timeout: 10)
        notices.connection.stop()
        await fulfillment(of: [notices.stopped], timeout: 10)
        XCTAssertTrue(notices.failures.isEmpty)
        XCTAssertEqual(notices.events.last { $0.id == "t" }?.payload, completion)
    }

    @MainActor
    func testCatalogFailureIsDeterminateAndNextExplicitRefreshIsNotReplayed() async throws {
        let catalog: [String: JSONValue] = ["operation": .string("model_catalog")]
        for code in ["model_catalog_failed", "model_catalog_too_large"] {
            let script = try connectedScript("""
            \(readLine)
            printf '%s\\n' "$line" >> "$HOME/catalog-requests.jsonl"
            \(try emit("catalog", 0, "accepted", catalog))
            \(try emit("catalog", 1, "started", catalog))
            \(try emit("catalog", 2, "failed", ["code": .string(code)]))
            \(readLine)
            printf '%s\\n' "$line" >> "$HOME/catalog-requests.jsonl"
            \(try emit("refresh", 0, "accepted", catalog))
            \(try emit("refresh", 1, "started", catalog))
            \(try emit("refresh", 2, "completed", ["models": .array([])]))
            \(shutdown)
            """)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                               environment: context.environment)
            await fulfillment(of: [notices.ready], timeout: 10)
            let terminal = notices.terminal("catalog")
            notices.connection.modelCatalog(id: "catalog")
            await fulfillment(of: [terminal], timeout: 10)
            XCTAssertEqual(notices.events.last { $0.id == "catalog" }?.safeFailureCode, code)
            let refreshed = notices.terminal("refresh")
            notices.connection.modelCatalog(id: "refresh")
            await fulfillment(of: [refreshed], timeout: 10)
            notices.connection.stop()
            await fulfillment(of: [notices.stopped], timeout: 10)
            XCTAssertTrue(notices.failures.isEmpty)
            let requests = try Data(contentsOf: context.home.appendingPathComponent("catalog-requests.jsonl"))
                .split(separator: 10).map { try JSONValue.parse(Data($0)) }
            XCTAssertEqual(requests.compactMap { $0.object?["id"]?.string }, ["catalog", "refresh"])
        }
    }

    @MainActor
    func testCatalogCancellationAndGracefulStopDrainEmptyTerminalBeforeShutdown() async throws {
        let catalog: [String: JSONValue] = ["operation": .string("model_catalog")]
        let script = try connectedScript("""
        \(readLine)
        \(try emit("catalog", 0, "accepted", catalog))
        \(try emit("catalog", 1, "started", catalog))
        \(readLine)
        \(try emit("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        \(readLine)
        /bin/sleep 1
        printf drained > "$HOME/catalog-drained"
        \(try emit("catalog", 2, "cancelled"))
        printf '%s\\n' "$line" | /usr/bin/sed 's/"type":"shutdown"/"seq":0,"type":"completed"/'
        """)
        let context = try fixture(script: script)
        defer { remove(context) }
        let notices = Notices()
        defer { notices.connection.forceStop() }
        notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                           environment: context.environment)
        await fulfillment(of: [notices.ready], timeout: 10)
        let started = notices.started("catalog"), terminal = notices.terminal("catalog")
        notices.connection.modelCatalog(id: "catalog")
        await fulfillment(of: [started], timeout: 10)
        let cancelled = notices.terminal("cancel")
        notices.connection.send(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("catalog")]))
        await fulfillment(of: [cancelled], timeout: 10)
        XCTAssertFalse(notices.events.contains { $0.id == "catalog" && $0.isTerminal })
        notices.connection.stop()
        await fulfillment(of: [terminal, notices.stopped], timeout: 10, enforceOrder: true)
        XCTAssertEqual(notices.events.last { $0.id == "catalog" }?.payload, [:])
        XCTAssertEqual(try String(contentsOf: context.home.appendingPathComponent("catalog-drained"), encoding: .utf8), "drained")
        XCTAssertTrue(notices.failures.isEmpty)
    }

    @MainActor
    func testCatalogEOFAndTimeoutReportUnderlyingFailureWithoutTranslationUnknown() async throws {
        let catalog: [String: JSONValue] = ["operation": .string("model_catalog")]
        for timeout in [false, true] {
            let script = try connectedScript("""
            \(readLine)
            \(try emit("catalog", 0, "accepted", catalog))
            \(try emit("catalog", 1, "started", catalog))
            \(timeout ? "while IFS= read -r line; do :; done" : "exec 1>&-")
            printf drained > "$HOME/catalog-drained"
            """)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                               environment: context.environment)
            await fulfillment(of: [notices.ready], timeout: 10)
            notices.connection.modelCatalog(id: "catalog", timeout: timeout ? 0.5 : 20)
            await fulfillment(of: [notices.stopped], timeout: 10)
            XCTAssertEqual(notices.failures, [timeout ? .requestTimeout : .helperEOF])
            XCTAssertEqual(notices.events.filter { $0.id == "catalog" }.map(\.type), ["accepted", "started"])
            XCTAssertEqual(try String(contentsOf: context.home.appendingPathComponent("catalog-drained"), encoding: .utf8), "drained")
        }
    }

    @MainActor
    func testCatalogMalformedUnsolicitedAndLateCompletionNeverReachClients() async throws {
        let catalog: [String: JSONValue] = ["operation": .string("model_catalog")]
        let completed: [String: JSONValue] = ["models": .array([])]
        for scenario in ["malformed", "unsolicited", "late"] {
            let terminal = scenario == "late" ? try emit("catalog", 2, "cancelled") : ""
            let result = scenario == "malformed" ? ["models": JSONValue.array([.object(["id": .string("missing")])])] : completed
            let script = try connectedScript("""
            \(readLine)
            \(try emit("catalog", 0, "accepted", catalog))
            \(try emit("catalog", 1, "started", catalog))
            \(terminal)
            \(try emit(scenario == "unsolicited" ? "unsolicited" : "catalog", scenario == "late" ? 3 : 2, "completed", result))
            while IFS= read -r line; do :; done
            """)
            let context = try fixture(script: script)
            defer { remove(context) }
            let notices = Notices()
            defer { notices.connection.forceStop() }
            notices.connection.startTranslation(runtime: context.runtime, home: context.home, codexCommand: context.codex,
                                               environment: context.environment)
            await fulfillment(of: [notices.ready], timeout: 10)
            notices.connection.modelCatalog(id: "catalog")
            await fulfillment(of: [notices.stopped], timeout: 10)
            XCTAssertEqual(notices.failures, [scenario == "malformed" ? .invalidPayload :
                                                (scenario == "unsolicited" ? .invalidID : .invalidTransition)])
            XCTAssertFalse(notices.events.contains { $0.type == "completed" })
            XCTAssertEqual(notices.events.filter { $0.id == "catalog" && $0.isTerminal }.map(\.type),
                           scenario == "late" ? ["cancelled"] : [])
        }
    }
}
