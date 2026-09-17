import XCTest
import Darwin
import CryptoKit
@testable import CCTranslateSupport

extension HelperIntegrationTests {
    private func dictionaryDigest(_ file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer {
            do { try handle.close() }
            catch { XCTFail("Dictionary fixture file handle cleanup failed") }
        }
        var hash = SHA256()
        while let bytes = try handle.read(upToCount: 1024 * 1024), !bytes.isEmpty {
            hash.update(data: bytes)
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    @MainActor
    private func dictionaryOperation(_ session: ConfigurationNotices, _ request: DictionaryRequest,
                                     id: String = UUID().uuidString) async throws -> [String: JSONValue] {
        let terminal = session.terminal(id)
        session.connection.dictionary(request, id: id, timeout: 40)
        await fulfillment(of: [terminal], timeout: 45)
        session.assertOperation(id)
        let result = try XCTUnwrap(session.result(id))
        guard result.type == "completed" else { throw ProbeError.invalidPayload }
        return result.payload
    }

    @MainActor
    private func dictionaryConfiguration(_ session: ConfigurationNotices) async throws -> [String: JSONValue] {
        let id = UUID().uuidString, terminal = session.terminal(id)
        session.connection.loadConfiguration(id: id)
        await fulfillment(of: [terminal], timeout: 10)
        session.assertOperation(id)
        return try XCTUnwrap(session.result(id)?.payload["config"]?.object)
    }

    @MainActor
    private func saveDictionaryConfiguration(_ session: ConfigurationNotices,
                                            _ configuration: [String: JSONValue]) async throws {
        let id = UUID().uuidString, terminal = session.terminal(id)
        session.connection.saveConfiguration(configuration, id: id)
        await fulfillment(of: [terminal], timeout: 10)
        session.assertOperation(id)
    }

    @MainActor
    private func dictionaryHistory(_ session: ConfigurationNotices) async throws -> [[String: JSONValue]] {
        let id = UUID().uuidString, terminal = session.terminal(id)
        session.connection.loadHistory(id: id)
        await fulfillment(of: [terminal], timeout: 10)
        session.assertOperation(id)
        return try session.historyEntries(id)
    }

    @MainActor
    private func installDictionaryFixture(_ session: ConfigurationNotices, home: URL) async throws
        -> (ticket: DictionaryInstallTicket, installed: URL) {
        let payload = try await dictionaryOperation(session, .prepareInstall)
        let ticket = try DictionaryInstallTicket(payload: payload)
        let directory = ticket.path.deletingLastPathComponent()
        XCTAssertTrue(directory.resolvingSymlinksInPath().path.hasPrefix(
            home.resolvingSymlinksInPath().path + "/"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: ticket.path.path),
                       "Prepare reserves a session ticket, not a fake completed download.")
        let path = try XCTUnwrap(ProcessInfo.processInfo.environment["CC_TRANSLATE_DICTIONARY_TEST_ASSET"],
                                 "Postbuild dictionary tests require the externally acquired pinned asset; never skip.")
        XCTAssertFalse(path.isEmpty)
        let source = URL(fileURLWithPath: path)
        let attributes = try FileManager.default.attributesOfItem(atPath: source.path)
        XCTAssertEqual(attributes[.type] as? FileAttributeType, .typeRegular)
        XCTAssertEqual((attributes[.size] as? NSNumber)?.int64Value, ticket.size)
        XCTAssertEqual(try dictionaryDigest(source), ticket.sha256)
        // The CI utility supplies the already-downloaded bytes. This is not URLSession evidence.
        try FileManager.default.copyItem(at: source, to: ticket.path)
        let installedPayload = try await dictionaryOperation(session, .install(ticket: ticket.ticket))
        let status = try DictionaryStatus(payload: installedPayload)
        XCTAssertEqual(status.state, .ready)
        XCTAssertTrue(status.enabled)
        XCTAssertGreaterThan(status.entryCount, 0)
        XCTAssertEqual(status.sha256, ticket.sha256)
        XCTAssertEqual(status.size, ticket.size)
        XCTAssertEqual(status.dataVersion, ticket.dataVersion)
        XCTAssertEqual(status.downloadURL, ticket.url)
        XCTAssertFalse(FileManager.default.fileExists(atPath: ticket.path.path),
                       "Successful installation must consume the owned staging file.")
        let installed = directory.appendingPathComponent("cc_dictionary.sqlite3")
        XCTAssertEqual(try dictionaryDigest(installed), ticket.sha256)
        return (ticket, installed)
    }

    @MainActor
    private func dictionaryLookup(_ session: ConfigurationNotices, text: String = "\u{4f60}\u{597d}",
                                  useCache: Bool = true, recordHistory: Bool = true) async throws
        -> DictionaryLookupResult {
        let payload = try await dictionaryOperation(session, .lookup(
            text: text, appLanguage: "en_US", origin: "text",
            useCache: useCache, recordHistory: recordHistory))
        return try DictionaryLookupResult(payload: payload)
    }

    @MainActor
    func testBundledDictionaryInstallLookupCacheHistoryAndLiveOptoutWithoutCLI() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let history = context.config.deletingLastPathComponent().appendingPathComponent("history.json")
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        guard case let .array(capabilities)? = session.events.first?.payload["capabilities"] else {
            return XCTFail("Configuration-only dictionary capabilities are required.")
        }
        XCTAssertFalse(capabilities.contains(.string("translate")), "This helper has no CLI provider.")
        let initial = try DictionaryStatus(payload: await dictionaryOperation(session, .status))
        XCTAssertEqual(initial.state, .notInstalled)
        XCTAssertFalse(initial.enabled)
        let disabled = try await dictionaryLookup(session)
        XCTAssertEqual(disabled.status, "disabled")
        XCTAssertNil(disabled.result)
        XCTAssertFalse(FileManager.default.fileExists(atPath: history.path))
        _ = try await installDictionaryFixture(session, home: context.home)
        var configuration = try await dictionaryConfiguration(session)
        XCTAssertEqual(configuration["local_dictionary_enabled"], .bool(true))

        let first = try await dictionaryLookup(session)
        XCTAssertEqual(first.status, "hit")
        let result = try XCTUnwrap(first.result)
        let text = try XCTUnwrap(result["text"]?.string)
        XCTAssertTrue(text.contains("\u{4f60}\u{597d}"))
        XCTAssertTrue(text.contains("n\u{01d0} h\u{01ce}o"), "The real pinned entry must render readable pinyin.")
        XCTAssertTrue(text.contains("CC-CEDICT"), "Readable output must attribute its dictionary source.")
        XCTAssertTrue(text.contains("CC BY-SA"), "Readable output must retain source licensing.")
        XCTAssertTrue(text.contains("\n"))
        XCTAssertFalse(text.hasPrefix("{"), "Dictionary output must be readable text, not a dumped DTO.")
        XCTAssertEqual(result["submitted"], .bool(false))
        XCTAssertEqual(result["kind"], .string("dict"))
        XCTAssertEqual(result["target_lang"], .null)
        XCTAssertEqual(result["summarize"], .bool(false))
        XCTAssertEqual(result["cached"], .bool(false))
        XCTAssertEqual(result["history"], .string("recorded"))
        let entries = try await dictionaryHistory(session)
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries.first?["output"], .string(text))
        let originalHistory = try Data(contentsOf: history)

        let cached = try await dictionaryLookup(session)
        XCTAssertEqual(cached.result?["cached"], .bool(true))
        XCTAssertEqual(cached.result?["history"], .string("unchanged"))
        XCTAssertEqual(cached.result?["text"], .string(text))
        XCTAssertEqual(try Data(contentsOf: history), originalHistory, "Cache hits must not duplicate history.")

        var samples: [Double] = []
        for _ in 0..<8 {
            let start = ProcessInfo.processInfo.systemUptime
            let warm = try await dictionaryLookup(session, useCache: false, recordHistory: false)
            samples.append((ProcessInfo.processInfo.systemUptime - start) * 1000)
            XCTAssertEqual(warm.status, "hit")
            XCTAssertEqual(warm.result?["cached"], .bool(false))
            XCTAssertEqual(warm.result?["history"], .string("disabled"))
        }
        let timing = try JSONValue.object([
            "scope": .string("config_only_foundation_helper_round_trip_not_gui"),
            "unit": .string("ms"), "samples": .array(samples.map(JSONValue.number)),
            "use_cache": .bool(false), "record_history": .bool(false)
        ]).encoded()
        print("CC_TRANSLATE_DICTIONARY_TIMINGS " + String(decoding: timing, as: UTF8.self))
        XCTAssertEqual(try Data(contentsOf: history), originalHistory)

        configuration["history_enabled"] = .bool(false)
        try await saveDictionaryConfiguration(session, configuration)
        let optedOut = try await dictionaryLookup(session)
        XCTAssertEqual(optedOut.status, "hit")
        XCTAssertEqual(optedOut.result?["cached"], .bool(false))
        XCTAssertEqual(optedOut.result?["history"], .string("disabled"))
        XCTAssertEqual(try Data(contentsOf: history), originalHistory)
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let lookup = try await dictionaryLookup(reopened, text: "hello")
        XCTAssertEqual(lookup.status, "hit")
        XCTAssertEqual(lookup.result?["submitted"], .bool(false))
        XCTAssertEqual(lookup.result?["history"], .string("disabled"))
        XCTAssertEqual(try Data(contentsOf: history), originalHistory)
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty)
    }

    @MainActor
    func testBundledDictionaryInvalidStagingDiscardDisableDeleteAndReopenWithoutCLI() async throws {
        let context = try configurationContext()
        defer { removeConfigurationHome(context.home) }
        let history = context.config.deletingLastPathComponent().appendingPathComponent("history.json")
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        session.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [session.ready], timeout: 10)
        let fixture = try await installDictionaryFixture(session, home: context.home)
        let settings = try Data(contentsOf: context.config)
        let neighbor = fixture.installed.deletingLastPathComponent().appendingPathComponent("unrelated-fixture")
        let neighborBytes = Data("Unrelated synthetic file: never delete as ticket cleanup.".utf8)
        try neighborBytes.write(to: neighbor)

        for sameSize in [false, true] {
            let prepared = try await dictionaryOperation(session, .prepareInstall)
            let ticket = try DictionaryInstallTicket(payload: prepared)
            XCTAssertEqual(ticket.path.deletingLastPathComponent(), fixture.installed.deletingLastPathComponent())
            if sameSize {
                try FileManager.default.copyItem(at: fixture.installed, to: ticket.path)
                let file = try FileHandle(forWritingTo: ticket.path)
                defer {
                    do { try file.close() }
                    catch { XCTFail("Invalid dictionary staging handle cleanup failed") }
                }
                try file.seek(toOffset: 0)
                try file.write(contentsOf: Data("NotSQLite".utf8))
                let attributes = try FileManager.default.attributesOfItem(atPath: ticket.path.path)
                XCTAssertEqual((attributes[.size] as? NSNumber)?.int64Value, ticket.size)
            } else {
                try Data("Invalid staged fixture".utf8).write(to: ticket.path)
            }
            let id = UUID().uuidString, rejected = session.terminal(id)
            session.connection.dictionary(.install(ticket: ticket.ticket), id: id, timeout: 40)
            await fulfillment(of: [rejected], timeout: 45)
            session.assertHistoryFailure(id, code: "dictionary_install_failed")
            XCTAssertEqual(try dictionaryDigest(fixture.installed), fixture.ticket.sha256)
            XCTAssertEqual(try Data(contentsOf: context.config), settings)
            XCTAssertEqual(try Data(contentsOf: neighbor), neighborBytes)
        }

        let discardPayload = try await dictionaryOperation(session, .prepareInstall)
        let discardTicket = try DictionaryInstallTicket(payload: discardPayload)
        try Data("Synthetic cancelled producer staging".utf8).write(to: discardTicket.path)
        let discard = try await dictionaryOperation(session, .discardInstall(ticket: discardTicket.ticket))
        XCTAssertEqual(discard, ["discarded": .bool(true)])
        XCTAssertFalse(FileManager.default.fileExists(atPath: discardTicket.path.path))
        XCTAssertEqual(try Data(contentsOf: neighbor), neighborBytes)
        XCTAssertEqual(try dictionaryDigest(fixture.installed), fixture.ticket.sha256)
        XCTAssertEqual(try Data(contentsOf: context.config), settings)

        var configuration = try await dictionaryConfiguration(session)
        configuration["local_dictionary_enabled"] = .bool(false)
        try await saveDictionaryConfiguration(session, configuration)
        let status = try DictionaryStatus(payload: await dictionaryOperation(session, .status))
        XCTAssertEqual(status.state, .ready)
        XCTAssertFalse(status.enabled)
        XCTAssertEqual(try dictionaryDigest(fixture.installed), fixture.ticket.sha256)
        let disabled = try await dictionaryLookup(session)
        XCTAssertEqual(disabled.status, "disabled")
        XCTAssertFalse(FileManager.default.fileExists(atPath: history.path))

        let abandonedPayload = try await dictionaryOperation(session, .prepareInstall)
        let abandoned = try DictionaryInstallTicket(payload: abandonedPayload)
        try Data("Owned unfinished download".utf8).write(to: abandoned.path)
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertFalse(FileManager.default.fileExists(atPath: abandoned.path.path))
        XCTAssertEqual(try Data(contentsOf: neighbor), neighborBytes)
        XCTAssertTrue(session.failures.isEmpty)

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        reopened.connection.startConfiguration(runtime: context.runtime, home: context.home)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let retained = try DictionaryStatus(payload: await dictionaryOperation(reopened, .status))
        XCTAssertEqual(retained.state, .ready)
        XCTAssertFalse(retained.enabled)
        let deletion = try await dictionaryOperation(reopened, .delete)
        XCTAssertEqual(deletion, ["deleted": .bool(true), "enabled": .bool(false)])
        XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.installed.path))
        XCTAssertEqual(try Data(contentsOf: neighbor), neighborBytes)
        let repeated = try await dictionaryOperation(reopened, .delete)
        XCTAssertEqual(repeated, ["deleted": .bool(false), "enabled": .bool(false)])
        let missing = try DictionaryStatus(payload: await dictionaryOperation(reopened, .status))
        XCTAssertEqual(missing.state, .notInstalled)
        XCTAssertFalse(missing.enabled)
        let deletedConfiguration = try await dictionaryConfiguration(reopened)
        XCTAssertEqual(deletedConfiguration["local_dictionary_enabled"], .bool(false))
        XCTAssertFalse(FileManager.default.fileExists(atPath: history.path),
                       "Install, failed install, discard, disable and delete must not write translation history.")
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty)
    }
}

extension HelperIntegrationTests {
    private struct TranslationContext {
        let runtime: BundleRuntime
        let cleanupRoot: URL
        let root: URL
        let home: URL
        let command: URL
        let gate: URL
        let configFile: URL
        let environment: [String: String]
        let config: [String: JSONValue]
        let request: [String: JSONValue]
        let expected: [String: JSONValue]

        var historyFile: URL { configFile.deletingLastPathComponent().appendingPathComponent("history.json") }
    }

    private func translationFixture(runtime: BundleRuntime, home: URL,
                                    arguments: [String], image: Bool = false) throws -> [String: JSONValue] {
        let process = Process(), output = Pipe(), errors = Pipe()
        var handles = [output.fileHandleForReading, output.fileHandleForWriting,
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
                catch { XCTFail("Synthetic translation fixture pipe cleanup failed") }
            }
        }
        let fixture = runtime.launcher.deletingLastPathComponent()
            .appendingPathComponent("cc_macos")
            .appendingPathComponent(image ? "image_fixture.py" : "translation_fixture.py")
        process.executableURL = runtime.executable
        process.arguments = ["-I", "-B", fixture.path] + arguments
        process.currentDirectoryURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath,
                                          isDirectory: true)
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": home.path, "TMPDIR": home.path]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output.fileHandleForWriting
        process.standardError = errors.fileHandleForWriting
        try process.run()
        try close(output.fileHandleForWriting)
        try close(errors.fileHandleForWriting)
        let bytes = output.fileHandleForReading.readDataToEndOfFile()
        let diagnostic = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0, "Bundled translation fixture failed")
        XCTAssertTrue(diagnostic.isEmpty, "Synthetic fixture must not emit diagnostics")
        guard process.terminationStatus == 0, diagnostic.isEmpty else { throw ProbeError.launchFailed }
        return try XCTUnwrap(JSONValue.parse(bytes).object)
    }

    private func translationContext(scenario: String = "normal", resultAction: ResultAction? = nil,
                                    targetLanguage: String? = nil, origin: String? = nil,
                                    model: String? = nil, image: Bool = false,
                                    imageDirection: String = "auto") throws -> TranslationContext {
        let base = try configurationContext()
        do {
            let identifier = try base.runtime.configurationApplicationIdentifier()
            var arguments = [
                "--prepare", base.home.appendingPathComponent("translation").path,
                "--application-id", identifier, "--scenario", scenario
            ]
            if let resultAction { arguments += ["--result-action", resultAction.rawValue] }
            if let targetLanguage { arguments += ["--target-language", targetLanguage] }
            if let origin { arguments += ["--origin", origin] }
            if let model { arguments += ["--model", model] }
            if image { arguments += ["--direction", imageDirection] }
            let fixture = try translationFixture(runtime: base.runtime, home: base.home,
                                                  arguments: arguments, image: image)
            let home = URL(fileURLWithPath: try XCTUnwrap(fixture["home"]?.string), isDirectory: true)
            let environment = try XCTUnwrap(fixture["environment"]?.object)
                .mapValues { try XCTUnwrap($0.string) }
            XCTAssertEqual(environment["HOME"], home.path)
            XCTAssertNotNil(environment["PATH"])
            XCTAssertTrue(home.path.hasPrefix(base.home.path + "/"))
            let configFile = home.appendingPathComponent("Library/Application Support")
                .appendingPathComponent(identifier).appendingPathComponent("config.json")
            return TranslationContext(
                runtime: base.runtime, cleanupRoot: base.home,
                root: URL(fileURLWithPath: try XCTUnwrap(fixture["root"]?.string), isDirectory: true),
                home: home, command: URL(fileURLWithPath: try XCTUnwrap(fixture["command"]?.string)),
                gate: URL(fileURLWithPath: try XCTUnwrap(fixture["gate"]?.string)),
                configFile: configFile, environment: environment,
                config: try XCTUnwrap(fixture["config"]?.object),
                request: try XCTUnwrap(fixture["request"]?.object),
                expected: try XCTUnwrap(fixture["expected"]?.object))
        } catch {
            removeConfigurationHome(base.home)
            throw error
        }
    }

    @MainActor
    private func startTranslation(_ session: ConfigurationNotices, _ context: TranslationContext) {
        session.connection.startTranslation(runtime: context.runtime, home: context.home,
                                            codexCommand: context.command, environment: context.environment)
    }

    @MainActor
    private func sendTranslation(_ session: ConfigurationNotices, _ context: TranslationContext,
                                 id: String = "translation", useCache: Bool = true) throws {
        session.connection.translate(text: try XCTUnwrap(context.request["text"]?.string),
                                     appLanguage: try XCTUnwrap(context.request["app_language"]?.string),
                                     origin: try XCTUnwrap(context.request["origin"]?.string),
                                     useCache: useCache, id: id)
    }

    @MainActor
    private func assertTranslation(_ session: ConfigurationNotices, _ context: TranslationContext,
                                   id: String = "translation", cached: Bool = false,
                                   history: String = "recorded",
                                   file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(session.result(id)?.type, "completed", file: file, line: line)
        XCTAssertEqual(session.result(id)?.payload, [
            "text": context.expected["output"]!,
            "submitted": .bool(!cached), "cached": .bool(cached),
            "kind": context.expected["kind"]!, "target_lang": context.expected["target_lang"]!,
            "summarize": context.expected["summarize"]!,
            "history": .string(history), "history_error": .null
        ], file: file, line: line)
        let events = session.events.filter { $0.id == id }
        XCTAssertEqual(events.map(\.sequence), (0..<events.count).map { Int64($0) }, file: file, line: line)
        XCTAssertEqual(Array(events.prefix(2)).map(\.type), ["accepted", "started"], file: file, line: line)
        XCTAssertTrue(events.allSatisfy { $0.payload["fixture"] == nil }, file: file, line: line)
    }

    private func assertNoTranslationCLI(_ context: TranslationContext,
                                        file: StaticString = #filePath, line: UInt = #line) {
        for name in ["calls.jsonl", "version.jsonl", "native-processes.jsonl", "native-rpc.jsonl"] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: context.root.appendingPathComponent(name).path),
                           "Hello/configuration must not start any CLI", file: file, line: line)
        }
    }

    private func verifyTranslation(_ context: TranslationContext, turns: Int64, cleanup: Bool,
                                   descendant: Bool = false) throws {
        var arguments = ["--verify", context.root.path]
        if cleanup { arguments.append("--require-cleanup") }
        if descendant { arguments.append("--require-descendant") }
        let evidence = try translationFixture(runtime: context.runtime, home: context.home, arguments: arguments)
        XCTAssertEqual(evidence["submitted_turns"], .integer(turns))
        XCTAssertEqual(evidence["prompt_verified"], .bool(true))
        XCTAssertEqual(evidence["cleanup_verified"], .bool(cleanup))
        if descendant { XCTAssertGreaterThan(try XCTUnwrap(evidence["descendants"]?.integer), 0) }
    }

    @MainActor
    func testBundledImageTranslationOwnsPNGStreamsWithoutCacheAndDrainsCancellationOrUnknown() async throws {
        for action in ["normal", "cancel", "eof", "optout"] {
            let gated = action != "normal"
            let model = "provider/Exact-Image-e\u{0301}"
            let context = try translationContext(scenario: gated ? "gated" : "normal",
                                                 model: model, image: true, imageDirection: "to_ja")
            let png: Data
            let attachment: ImageTranslationAttachment
            do {
                let source = URL(fileURLWithPath: try XCTUnwrap(context.request["image_path"]?.string))
                png = try Data(contentsOf: source)
                attachment = try ImageTranslationAttachment(pngData: png, temporaryParent: context.home)
                XCTAssertNotEqual(attachment.url, source)
            } catch {
                removeConfigurationHome(context.cleanupRoot)
                throw error
            }
            let session = ConfigurationNotices()
            defer {
                if session.didStop {
                    do {
                        try attachment.cleanup()
                        try FileManager.default.removeItem(at: context.cleanupRoot)
                    } catch { XCTFail("Synthetic image attachment/home cleanup failed") }
                } else {
                    session.connection.forceStop()
                    XCTFail("Synthetic image home retained because helper drain was not observed")
                }
            }
            @MainActor
            func sendImage(_ id: String, sha256: String? = nil) {
                session.connection.translateImage(imagePath: attachment.url.path, imageBytes: attachment.byteCount,
                                                   imageSHA256: sha256 ?? attachment.sha256,
                                                   appLanguage: "en_US", recordHistory: true, id: id, timeout: 40)
            }
            startTranslation(session, context)
            await fulfillment(of: [session.ready], timeout: 10)
            guard case let .array(capabilities)? = session.events.first?.payload["capabilities"] else {
                throw ProbeError.invalidPayload
            }
            XCTAssertTrue(capabilities.contains(.string("translate_image")))
            assertNoTranslationCLI(context)
            let saved = session.terminal("save")
            session.connection.saveConfiguration(context.config, id: "save")
            await fulfillment(of: [saved], timeout: 10)
            session.assertOperation("save")
            XCTAssertTrue(try XCTUnwrap(context.expected["model"]?.string).utf8.elementsEqual(model.utf8))
            assertNoTranslationCLI(context)
            let workspace = context.configFile.deletingLastPathComponent().appendingPathComponent("NativeWorkspace")
            func assertNoHelperImages(file: StaticString = #filePath, line: UInt = #line) throws {
                let directories = try FileManager.default.contentsOfDirectory(at: workspace,
                                                                              includingPropertiesForKeys: nil)
                XCTAssertFalse(directories.contains { $0.lastPathComponent.hasPrefix(".cc-image-") },
                               "Helper-owned PNGs must be gone at terminal, not merely at shutdown", file: file, line: line)
            }
            if action == "normal" {
                let rejected = session.terminal("changed")
                sendImage("changed", sha256: String(repeating: "0", count: 64))
                await fulfillment(of: [rejected], timeout: 15)
                XCTAssertEqual(session.result("changed")?.type, "failed")
                XCTAssertEqual(session.result("changed")?.payload,
                               ["code": .string("image_changed"), "submitted": .bool(false)])
                assertNoTranslationCLI(context)
            }
            let terminal = session.terminal("image")
            let delta = gated ? session.firstDelta("image") : nil
            sendImage("image")
            if let delta {
                await fulfillment(of: [delta], timeout: 25)
                XCTAssertNil(session.result("image"), "Only the explicit synthetic gate holds completion")
                XCTAssertEqual(try Data(contentsOf: attachment.url), png)
                let directories = try FileManager.default.contentsOfDirectory(at: workspace,
                                                                              includingPropertiesForKeys: nil)
                XCTAssertEqual(directories.filter { $0.lastPathComponent.hasPrefix(".cc-image-") }.count, 1)
                switch action {
                case "cancel":
                    let cancelled = session.terminal("cancel")
                    session.connection.send(ClientMessage(id: "cancel", type: "cancel",
                                                          payload: ["request_id": .string("image")]))
                    await fulfillment(of: [cancelled, terminal], timeout: 15)
                    XCTAssertEqual(session.result("cancel")?.payload, ["cancel_requested": .bool(true)])
                    XCTAssertEqual(session.result("image")?.type, "cancelled")
                    XCTAssertEqual(session.result("image")?.payload, ["submitted": .bool(true)])
                case "eof":
                    // forceStop closes helper stdin and signals only its owned process; the
                    // in-flight model result stays unknown even when native cleanup succeeds.
                    session.connection.forceStop()
                    await fulfillment(of: [session.stopped], timeout: 20)
                    XCTAssertEqual(session.failures, [.translationOutcomeUnknown])
                    XCTAssertFalse(session.events.contains { $0.id == "image" && $0.type == "completed" })
                case "optout":
                    var config = context.config
                    config["history_enabled"] = .bool(false)
                    config["direction"] = .string("to_en")
                    let optedOut = session.terminal("optout")
                    session.connection.saveConfiguration(config, id: "optout")
                    await fulfillment(of: [optedOut], timeout: 10)
                    session.assertOperation("optout")
                    try Data("release".utf8).write(to: context.gate)
                    await fulfillment(of: [terminal], timeout: 15)
                    assertTranslation(session, context, id: "image", history: "disabled")
                    XCTAssertEqual(session.result("image")?.payload["target_lang"], .string("ja"))
                default: XCTFail("Unexpected synthetic image action")
                }
            } else {
                await fulfillment(of: [terminal], timeout: 25)
                assertTranslation(session, context, id: "image")
                try assertNoHelperImages()
                let repeated = session.terminal("repeat")
                sendImage("repeat")
                await fulfillment(of: [repeated], timeout: 25)
                assertTranslation(session, context, id: "repeat")
            }
            if action != "eof" {
                try assertNoHelperImages()
                let history = session.terminal("history")
                session.connection.loadHistory(id: "history")
                await fulfillment(of: [history], timeout: 10)
                session.assertOperation("history")
                let entries = try session.historyEntries("history")
                XCTAssertEqual(entries.count, action == "normal" ? 2 : 0)
                for entry in entries {
                    XCTAssertEqual(entry["input"], .null)
                    XCTAssertEqual(entry["kind"], .string("ocr"))
                    XCTAssertEqual(entry["output"], context.expected["output"])
                }
                if action == "normal" {
                    let historyText = try String(contentsOf: context.historyFile, encoding: .utf8)
                    for privateValue in [attachment.url.path, attachment.sha256, png.base64EncodedString()] {
                        XCTAssertFalse(historyText.contains(privateValue), "Image metadata must not enter history")
                    }
                }
                session.connection.stop()
                await fulfillment(of: [session.stopped], timeout: 15)
                XCTAssertTrue(session.failures.isEmpty)
            }
            XCTAssertTrue(session.didStop)
            guard session.didStop else { throw ProbeError.requestTimeout }
            try verifyTranslation(context, turns: action == "normal" ? 2 : 1, cleanup: true, descendant: gated)
            let imageReads = try String(contentsOf: context.root.appendingPathComponent("image-read.jsonl"),
                                        encoding: .utf8).split(separator: "\n")
            XCTAssertEqual(imageReads.count, action == "normal" ? 2 : 1)
            for row in imageReads {
                XCTAssertEqual(try JSONValue.parse(Data(row.utf8)),
                               .object(["verified": .bool(true), "task": .string("image")]))
            }
            try assertNoHelperImages()
            XCTAssertFalse(FileManager.default.fileExists(atPath:
                context.configFile.deletingLastPathComponent().appendingPathComponent("state.json").path))
            if action != "normal" { XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path)) }
            XCTAssertEqual(try Data(contentsOf: attachment.url), png)
            try attachment.cleanup()
            XCTAssertFalse(FileManager.default.fileExists(atPath: attachment.url.deletingLastPathComponent().path))
        }
    }

    @MainActor
    func testBundledModelCatalogReadsExactMetadataWithoutTurnsThenTranslatesKnownModel() async throws {
        let context = try translationContext()
        defer { removeConfigurationHome(context.cleanupRoot) }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        startTranslation(session, context)
        await fulfillment(of: [session.ready], timeout: 10)
        guard case let .array(capabilities)? = session.events.first?.payload["capabilities"] else {
            return XCTFail("Native catalog capability is required")
        }
        XCTAssertTrue(capabilities.contains(.string("model_catalog")))
        assertNoTranslationCLI(context)
        let saved = session.terminal("save")
        session.connection.saveConfiguration(context.config, id: "save")
        await fulfillment(of: [saved], timeout: 10)
        session.assertOperation("save")
        let configuration = try Data(contentsOf: context.configFile)
        assertNoTranslationCLI(context)
        let cache = context.home.appendingPathComponent("Library/Caches")
            .appendingPathComponent(try context.runtime.configurationApplicationIdentifier())
            .appendingPathComponent("CodexModels")
        XCTAssertFalse(FileManager.default.fileExists(atPath: cache.path))
        let codexHome = URL(fileURLWithPath: try XCTUnwrap(context.environment["CODEX_HOME"]), isDirectory: true)
        let codexConfig = codexHome.appendingPathComponent("config.toml")
        let nativeConfiguration = try Data(contentsOf: codexConfig)
        let mode = context.root.appendingPathComponent("catalog-mode.txt")
        try Data("metadata".utf8).write(to: mode)
        let listed = session.terminal("catalog")
        session.connection.modelCatalog(id: "catalog")
        await fulfillment(of: [listed], timeout: 20)
        session.assertOperation("catalog")
        let models = try CodexModelEntry.decode(payload: XCTUnwrap(session.result("catalog")?.payload))
        let expected: [[String: JSONValue]] = [
            ["id": .string("synthetic"), "name": .string("Synthetic \u{4e2d}"), "description": .string("Local fixture only")],
            ["id": .string("synthetic-\u{00e9}"), "name": .string("synthetic-\u{00e9}"), "description": .string("")],
            ["id": .string("synthetic-e\u{0301}"), "name": .string("synthetic-e\u{0301}"), "description": .string("")],
            ["id": .string(" synthetic "), "name": .string(" synthetic "), "description": .string("")]
        ]
        XCTAssertEqual(models, try expected.map { try CodexModelEntry(payload: $0) })
        XCTAssertEqual(Set(models).count, 4, "Canonical-equivalent provider IDs must remain distinct")
        XCTAssertTrue(session.events.filter { $0.id == "catalog" }.allSatisfy { $0.payload["submitted"] == nil })

        for (index, fault) in ["malformed", "oversized"].enumerated() {
            try Data(fault.utf8).write(to: mode)
            let id = "failed_\(index)", terminal = session.terminal("failed_\(index)")
            session.connection.modelCatalog(id: id)
            await fulfillment(of: [terminal], timeout: 20)
            XCTAssertEqual(session.events.filter { $0.id == id }.map(\.type), ["accepted", "started", "failed"])
            XCTAssertEqual(session.result(id)?.payload, ["code": .string(
                fault == "oversized" ? "model_catalog_too_large" : "model_catalog_failed")])
        }
        XCTAssertTrue(session.failures.isEmpty)
        XCTAssertEqual(try Data(contentsOf: context.configFile), configuration)
        XCTAssertEqual(try Data(contentsOf: codexConfig), nativeConfiguration)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: cache.path), "Discovery must not install override metadata")
        for name in ["native-processes.jsonl", "native-rpc.jsonl", "version.jsonl"] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: context.root.appendingPathComponent(name).path),
                           "Catalog alone must not initialize app-server, probe versions, or submit thread/turn/model")
        }
        let calls = try Data(contentsOf: context.root.appendingPathComponent("calls.jsonl"))
            .split(separator: 10).map { try XCTUnwrap(JSONValue.parse(Data($0)).object) }
        XCTAssertEqual(calls.count, 3, "One debug export per explicit request; no automatic fallback or retry")
        for call in calls {
            guard case let .array(values)? = call["args"] else { return XCTFail("Expected synthetic argv receipt") }
            let args = try values.map { try XCTUnwrap($0.string) }
            XCTAssertEqual(Array(args.prefix(2)), ["debug", "models"])
            XCTAssertFalse(args.contains { $0.hasPrefix("model_catalog_json=") })
            XCTAssertFalse(args.contains("app-server"))
        }
        try verifyTranslation(context, turns: 0, cleanup: true)

        // Switch only the synthetic export fixture back to the existing known-model catalog.
        try Data("normal".utf8).write(to: mode)
        let translated = session.terminal("translation")
        try sendTranslation(session, context, useCache: false)
        await fulfillment(of: [translated], timeout: 25)
        assertTranslation(session, context)
        let records = try Data(contentsOf: context.root.appendingPathComponent("native-rpc.jsonl"))
            .split(separator: 10).map { try XCTUnwrap(JSONValue.parse(Data($0)).object) }
        let modelRequests = try records.compactMap { record -> [String: JSONValue]? in
            let request = try XCTUnwrap(record["request"]?.object)
            return ["thread/start", "turn/start"].contains(request["method"]?.string ?? "") ? request : nil
        }
        XCTAssertEqual(modelRequests.count, 2)
        for request in modelRequests {
            XCTAssertTrue(try XCTUnwrap(request["params"]?.object?["model"]?.string).utf8.elementsEqual("synthetic".utf8))
        }
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)
        try verifyTranslation(context, turns: 1, cleanup: true)
    }

    @MainActor
    func testBundledTranslationConfigurationStreamHistoryCacheAndReopen() async throws {
        let context = try translationContext(scenario: "direction")
        defer { removeConfigurationHome(context.cleanupRoot) }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        startTranslation(session, context)
        await fulfillment(of: [session.ready], timeout: 10)
        XCTAssertEqual(session.events.first?.payload["backend"], .string("native_appserver"))
        XCTAssertEqual(session.events.first?.payload["fixture"], .bool(false))
        assertNoTranslationCLI(context)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.configFile.path))

        let saved = session.terminal("save")
        session.connection.saveConfiguration(context.config, id: "save")
        await fulfillment(of: [saved], timeout: 10)
        session.assertOperation("save")
        assertNoTranslationCLI(context)
        let firstDelta = session.firstDelta("translation"), translated = session.terminal("translation")
        try sendTranslation(session, context)
        await fulfillment(of: [firstDelta, translated], timeout: 25, enforceOrder: true)
        assertTranslation(session, context)
        XCTAssertEqual(session.result("translation")?.payload["target_lang"], .string("ja"))
        XCTAssertEqual(session.events.filter { $0.id == "translation" }.map(\.type),
                       ["accepted", "started", "delta", "completed"])
        let history = session.terminal("history")
        session.connection.loadHistory(id: "history")
        await fulfillment(of: [history], timeout: 10)
        let entries = try session.historyEntries("history")
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries.first?["input"], context.request["text"])
        XCTAssertEqual(entries.first?["output"], context.expected["output"])
        XCTAssertEqual(entries.first?["sig"], context.expected["signature"])
        let stored = try Data(contentsOf: context.historyFile)
        let cached = session.terminal("cached")
        try sendTranslation(session, context, id: "cached")
        await fulfillment(of: [cached], timeout: 10)
        assertTranslation(session, context, id: "cached", cached: true, history: "unchanged")
        XCTAssertEqual(try Data(contentsOf: context.historyFile), stored)
        try verifyTranslation(context, turns: 1, cleanup: false)
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)
        try verifyTranslation(context, turns: 1, cleanup: true)

        let reopened = ConfigurationNotices()
        defer { reopened.connection.forceStop() }
        startTranslation(reopened, context)
        await fulfillment(of: [reopened.ready], timeout: 10)
        let replay = reopened.terminal("translation")
        try sendTranslation(reopened, context)
        await fulfillment(of: [replay], timeout: 10)
        assertTranslation(reopened, context, cached: true, history: "unchanged")
        XCTAssertEqual(try Data(contentsOf: context.historyFile), stored)
        let reloaded = reopened.terminal("history")
        reopened.connection.loadHistory(id: "history")
        await fulfillment(of: [reloaded], timeout: 10)
        XCTAssertEqual(try reopened.historyEntries("history"), entries)
        reopened.connection.stop()
        await fulfillment(of: [reopened.stopped], timeout: 10)
        XCTAssertTrue(reopened.failures.isEmpty)
        try verifyTranslation(context, turns: 1, cleanup: true)
    }

    @MainActor
    func testBundledCustomModelSettingsSurviveReopenAndReachExactProviderID() async throws {
        for model in ["provider/Exact-ID:2026", "gpt-5.4-mini", "model-e\u{301}",
                      String(repeating: "m", count: 256)] {
            for origin in ["text", "ocr"] {
                let context = try translationContext(origin: origin, model: model)
                defer { removeConfigurationHome(context.cleanupRoot) }
                let session = ConfigurationNotices()
                defer { session.connection.forceStop() }
                startTranslation(session, context)
                await fulfillment(of: [session.ready], timeout: 10)
                let loaded = session.terminal("load")
                session.connection.loadConfiguration(id: "load")
                await fulfillment(of: [loaded], timeout: 10)
                var config = try XCTUnwrap(session.result("load")?.payload["config"]?.object)
                XCTAssertEqual(config["codex_model_default_migrated"], .bool(true))
                assertNoTranslationCLI(context)
                config["codex_model"] = .string(model)
                config["future_model_setting"] = .string("preserve this synthetic field")
                let saved = session.terminal("save")
                session.connection.saveConfiguration(config, id: "save")
                await fulfillment(of: [saved], timeout: 10)
                session.assertOperation("save")
                let readback = session.terminal("readback")
                session.connection.loadConfiguration(id: "readback")
                await fulfillment(of: [readback], timeout: 10)
                let confirmed = try XCTUnwrap(session.result("readback")?.payload["config"]?.object)
                XCTAssertTrue(try XCTUnwrap(confirmed["codex_model"]?.string).utf8.elementsEqual(model.utf8))
                XCTAssertEqual(confirmed["future_model_setting"], config["future_model_setting"])
                let bytes = try Data(contentsOf: context.configFile)
                assertNoTranslationCLI(context)
                let translated = session.terminal("translation")
                try sendTranslation(session, context, useCache: false)
                await fulfillment(of: [translated], timeout: 25)
                assertTranslation(session, context)
                session.connection.stop()
                await fulfillment(of: [session.stopped], timeout: 10)
                XCTAssertTrue(session.failures.isEmpty)
                try verifyTranslation(context, turns: 1, cleanup: true)

                let reopened = ConfigurationNotices()
                defer { reopened.connection.forceStop() }
                startTranslation(reopened, context)
                await fulfillment(of: [reopened.ready], timeout: 10)
                let reloaded = reopened.terminal("reload")
                reopened.connection.loadConfiguration(id: "reload")
                await fulfillment(of: [reloaded], timeout: 10)
                let restored = try XCTUnwrap(reopened.result("reload")?.payload["config"]?.object)
                XCTAssertTrue(try XCTUnwrap(restored["codex_model"]?.string).utf8.elementsEqual(model.utf8))
                XCTAssertEqual(restored["future_model_setting"], config["future_model_setting"])
                XCTAssertEqual(try Data(contentsOf: context.configFile), bytes)
                let next = reopened.terminal("translation")
                try sendTranslation(reopened, context, useCache: false)
                await fulfillment(of: [next], timeout: 25)
                assertTranslation(reopened, context)
                reopened.connection.stop()
                await fulfillment(of: [reopened.stopped], timeout: 10)
                XCTAssertTrue(reopened.failures.isEmpty)
                try verifyTranslation(context, turns: 2, cleanup: true)
                let records = try Data(contentsOf: context.root.appendingPathComponent("native-rpc.jsonl"))
                    .split(separator: 0x0a).map { try XCTUnwrap(JSONValue.parse(Data($0)).object) }
                var actualModels: [String] = []
                for record in records {
                    let request = try XCTUnwrap(record["request"]?.object)
                    if request["method"] == .string("thread/start") || request["method"] == .string("turn/start") {
                        actualModels.append(try XCTUnwrap(request["params"]?.object?["model"]?.string))
                    }
                }
                XCTAssertEqual(actualModels.count, 4)
                XCTAssertTrue(actualModels.allSatisfy { $0.utf8.elementsEqual(model.utf8) },
                              "Both actual native thread/start and turn/start must keep the selected ID.")
            }
        }
    }

    @MainActor
    func testBundledOCRTextPreservesLayoutClassificationAndNeverUsesCacheOrAutomaticSummary() async throws {
        for scenario in ["normal", "summary", "dictionary", "code"] {
            let context = try translationContext(scenario: scenario, origin: "ocr")
            defer { removeConfigurationHome(context.cleanupRoot) }
            XCTAssertEqual(context.request["origin"], .string("ocr"))
            XCTAssertEqual(context.request["use_cache"], .bool(true))
            XCTAssertEqual(context.expected["kind"], .string("ocr"))
            XCTAssertEqual(context.expected["task"], .string("text"))
            XCTAssertEqual(context.expected["summarize"], .bool(false))
            let session = ConfigurationNotices()
            defer { session.connection.forceStop() }
            startTranslation(session, context)
            await fulfillment(of: [session.ready], timeout: 10)
            assertNoTranslationCLI(context)
            let saved = session.terminal("save")
            session.connection.saveConfiguration(context.config, id: "save")
            await fulfillment(of: [saved], timeout: 10)
            session.assertOperation("save")
            assertNoTranslationCLI(context)
            for click in 0..<2 {
                let id = "ocr_\(click)"
                let completed = session.terminal(id)
                try sendTranslation(session, context, id: id)
                await fulfillment(of: [completed], timeout: 25)
                assertTranslation(session, context, id: id)
                XCTAssertTrue(session.events.contains { $0.id == id && $0.type == "delta" })
            }
            let loaded = session.terminal("history")
            session.connection.loadHistory(kind: "ocr", id: "history")
            await fulfillment(of: [loaded], timeout: 10)
            session.assertOperation("history")
            let entries = try session.historyEntries("history")
            XCTAssertEqual(entries.count, 2, "Each explicit OCR click is a new translation, not a cache hit.")
            for entry in entries {
                XCTAssertEqual(entry["input"], context.request["text"])
                XCTAssertEqual(entry["output"], context.expected["output"])
                XCTAssertEqual(entry["kind"], .string("ocr"))
                XCTAssertEqual(entry["sig"], context.expected["signature"])
                XCTAssertEqual(entry["is_dict"], .bool(scenario == "dictionary"))
                XCTAssertEqual(entry["is_code"], .bool(scenario == "code"))
            }
            try verifyTranslation(context, turns: 2, cleanup: false)
            session.connection.stop()
            await fulfillment(of: [session.stopped], timeout: 10)
            XCTAssertTrue(session.failures.isEmpty)
            try verifyTranslation(context, turns: 2, cleanup: true)
        }
    }

    @MainActor
    func testBundledResultActionsUseNativeProviderWithoutReadingOrWritingHistory() async throws {
        for action in ResultAction.allCases {
            let target = action == .retranslate ? "ja" : nil
            let scenario = action == .asText ? "dictionary" : action == .explainCode ? "code" : "normal"
            let context = try translationContext(scenario: scenario, resultAction: action, targetLanguage: target)
            defer { removeConfigurationHome(context.cleanupRoot) }
            let session = ConfigurationNotices()
            defer { session.connection.forceStop() }
            startTranslation(session, context)
            await fulfillment(of: [session.ready], timeout: 10)
            assertNoTranslationCLI(context)
            let saved = session.terminal("save")
            session.connection.saveConfiguration(context.config, id: "save")
            await fulfillment(of: [saved], timeout: 10)
            session.assertOperation("save")
            let historyBytes = Data("invalid synthetic history must not be read by result actions".utf8)
            try historyBytes.write(to: context.historyFile)
            for id in ["first_action", "second_action"] {
                let terminal = session.terminal(id)
                session.connection.resultAction(
                    action, text: try XCTUnwrap(context.request["text"]?.string),
                    appLanguage: "zh_CN", targetLanguage: target, id: id)
                await fulfillment(of: [terminal], timeout: 25)
                assertTranslation(session, context, id: id, history: "disabled")
                XCTAssertEqual(session.result(id)?.payload["kind"], .string("text"))
                XCTAssertEqual(try Data(contentsOf: context.historyFile), historyBytes)
            }
            try verifyTranslation(context, turns: 2, cleanup: false)
            session.connection.stop()
            await fulfillment(of: [session.stopped], timeout: 10)
            XCTAssertTrue(session.failures.isEmpty)
            try verifyTranslation(context, turns: 2, cleanup: true)
        }
    }

    @MainActor
    func testBundledResultActionCancellationDrainsOwnedGroupsWithoutHistory() async throws {
        let context = try translationContext(scenario: "gated", resultAction: .summary)
        defer { removeConfigurationHome(context.cleanupRoot) }
        let session = ConfigurationNotices()
        defer { session.connection.forceStop() }
        startTranslation(session, context)
        await fulfillment(of: [session.ready], timeout: 10)
        let saved = session.terminal("save")
        session.connection.saveConfiguration(context.config, id: "save")
        await fulfillment(of: [saved], timeout: 10)
        let delta = session.firstDelta("action"), terminal = session.terminal("action")
        session.connection.resultAction(.summary, text: try XCTUnwrap(context.request["text"]?.string),
                                        appLanguage: "zh_CN", id: "action")
        await fulfillment(of: [delta], timeout: 25)
        XCTAssertNil(session.result("action"))
        let cancelled = session.terminal("cancel")
        session.connection.send(ClientMessage(id: "cancel", type: "cancel",
                                              payload: ["request_id": .string("action")]))
        await fulfillment(of: [cancelled, terminal], timeout: 15)
        XCTAssertEqual(session.result("cancel")?.payload, ["cancel_requested": .bool(true)])
        XCTAssertEqual(session.result("action")?.type, "cancelled")
        XCTAssertEqual(session.result("action")?.payload, ["submitted": .bool(true)])
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path))
        session.connection.stop()
        await fulfillment(of: [session.stopped], timeout: 10)
        XCTAssertTrue(session.failures.isEmpty)
        try verifyTranslation(context, turns: 1, cleanup: true, descendant: true)
    }

    @MainActor
    func testBundledTranslationConcurrentOptoutAndCancellationDrain() async throws {
        for cancel in [false, true] {
            let context = try translationContext(scenario: "gated")
            defer { removeConfigurationHome(context.cleanupRoot) }
            let session = ConfigurationNotices()
            defer { session.connection.forceStop() }
            startTranslation(session, context)
            await fulfillment(of: [session.ready], timeout: 10)
            let saved = session.terminal("save")
            session.connection.saveConfiguration(context.config, id: "save")
            await fulfillment(of: [saved], timeout: 10)
            session.assertOperation("save")
            let delta = session.firstDelta("translation"), terminal = session.terminal("translation")
            try sendTranslation(session, context)
            await fulfillment(of: [delta], timeout: 25)
            XCTAssertNil(session.result("translation"), "The explicit gate holds completion, not a timing guess")
            if cancel {
                let cancelled = session.terminal("cancel")
                session.connection.send(ClientMessage(id: "cancel", type: "cancel", payload: [
                    "request_id": .string("translation")
                ]))
                await fulfillment(of: [cancelled, terminal], timeout: 15)
                XCTAssertEqual(session.result("cancel")?.payload, ["cancel_requested": .bool(true)])
                XCTAssertEqual(session.result("translation")?.type, "cancelled")
                XCTAssertEqual(session.result("translation")?.payload, ["submitted": .bool(true)])
                try verifyTranslation(context, turns: 1, cleanup: true, descendant: true)
            } else {
                let optout = session.terminal("optout")
                var config = context.config
                config["history_enabled"] = .bool(false)
                config["direction"] = .string("to_ja")
                session.connection.saveConfiguration(config, id: "optout")
                await fulfillment(of: [optout], timeout: 10)
                session.assertOperation("optout")
                try Data("release".utf8).write(to: context.gate)
                await fulfillment(of: [terminal], timeout: 15)
                assertTranslation(session, context, history: "disabled")
                XCTAssertEqual(session.result("translation")?.payload["target_lang"], .string("zh"),
                               "The in-flight snapshot must not adopt the later direction")
            }
            let history = session.terminal("history")
            session.connection.loadHistory(id: "history")
            await fulfillment(of: [history], timeout: 10)
            XCTAssertEqual(try session.historyEntries("history"), [])
            XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path))
            session.connection.stop()
            await fulfillment(of: [session.stopped], timeout: 10)
            XCTAssertTrue(session.failures.isEmpty)
            try verifyTranslation(context, turns: 1, cleanup: true, descendant: true)
        }
    }

    @MainActor
    func testBundledTranslationCorruptionAndOutputBudgets() async throws {
        for scenario in ["config-corrupt", "history-corrupt", "controls", "output-limit", "envelope-limit"] {
            let corrupt = scenario.hasSuffix("-corrupt")
            let context = try translationContext(scenario: corrupt ? "normal" : scenario)
            defer { removeConfigurationHome(context.cleanupRoot) }
            let session = ConfigurationNotices()
            defer { session.connection.forceStop() }
            startTranslation(session, context)
            await fulfillment(of: [session.ready], timeout: 10)
            let saved = session.terminal("save")
            session.connection.saveConfiguration(context.config, id: "save")
            await fulfillment(of: [saved], timeout: 10)
            session.assertOperation("save")
            let malformed = Data(#"{"SYNTHETIC_PRIVATE_CORRUPTION":"#.utf8)
            let damaged = scenario == "config-corrupt" ? context.configFile : context.historyFile
            // Direct bytes are fault injection only; normal business writes always use HelperConnection.
            if corrupt { try malformed.write(to: damaged) }
            let terminal = session.terminal("translation")
            try sendTranslation(session, context)
            await fulfillment(of: [terminal], timeout: 90)
            if corrupt {
                XCTAssertEqual(session.result("translation")?.type, "failed")
                XCTAssertEqual(session.result("translation")?.payload, [
                    "code": .string(scenario == "config-corrupt" ? "invalid_config" : "invalid_history"),
                    "submitted": .bool(false)
                ])
                XCTAssertEqual(try Data(contentsOf: damaged), malformed)
                assertNoTranslationCLI(context)
            } else if scenario == "controls" {
                assertTranslation(session, context)
                let deltas = session.events.filter { $0.id == "translation" && $0.type == "delta" }
                XCTAssertGreaterThan(deltas.count, 1)
                XCTAssertEqual(deltas.compactMap { $0.payload["text"]?.string }.joined(),
                               context.expected["output"]?.string)
                let history = session.terminal("history")
                session.connection.loadHistory(id: "history")
                await fulfillment(of: [history], timeout: 10)
                XCTAssertEqual(try session.historyEntries("history").first?["output"], context.expected["output"])
            } else {
                XCTAssertEqual(session.result("translation")?.type, "failed")
                XCTAssertEqual(session.result("translation")?.payload, [
                    "code": .string("translation_output_limit"), "submitted": .bool(true)
                ])
                XCTAssertNil(session.result("translation")?.payload["text"], "Budget failure is never truncated success")
                XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path))
                if scenario == "envelope-limit" {
                    XCTAssertGreaterThan(session.events.filter { $0.type == "delta" }.count, 1000)
                }
            }
            session.connection.stop()
            await fulfillment(of: [session.stopped], timeout: 10)
            XCTAssertTrue(session.failures.isEmpty)
            if corrupt { XCTAssertEqual(try Data(contentsOf: damaged), malformed) }
            else { try verifyTranslation(context, turns: 1, cleanup: true) }
        }
        let context = try translationContext()
        defer { removeConfigurationHome(context.cleanupRoot) }
        let invalid = ConfigurationNotices()
        defer { invalid.connection.forceStop() }
        startTranslation(invalid, context)
        await fulfillment(of: [invalid.ready], timeout: 10)
        invalid.connection.translate(text: String(repeating: "\u{4e2d}", count: 2731),
                                     appLanguage: "zh_CN", id: "oversize")
        await fulfillment(of: [invalid.stopped], timeout: 10)
        XCTAssertFalse(invalid.failures.isEmpty)
        XCTAssertFalse(invalid.events.contains { $0.id == "oversize" && $0.type == "started" })
        assertNoTranslationCLI(context)
    }

    @MainActor
    func testBundledTranslationCompetingHelpersForceStopAndReopen() async throws {
        let context = try translationContext(scenario: "gated")
        defer { removeConfigurationHome(context.cleanupRoot) }
        let owner = ConfigurationNotices()
        defer { owner.connection.forceStop() }
        startTranslation(owner, context)
        await fulfillment(of: [owner.ready], timeout: 10)
        let saved = owner.terminal("save")
        owner.connection.saveConfiguration(context.config, id: "save")
        await fulfillment(of: [saved], timeout: 10)
        owner.assertOperation("save")
        let competitor = ConfigurationNotices()
        defer { competitor.connection.forceStop() }
        let refused = competitor.terminal("hello")
        startTranslation(competitor, context)
        await fulfillment(of: [refused, competitor.stopped], timeout: 10, enforceOrder: true)
        XCTAssertEqual(competitor.result("hello")?.safeFailureCode, "config_in_use")
        XCTAssertEqual(competitor.failures, [.configInUse])
        assertNoTranslationCLI(context)
        let delta = owner.firstDelta("translation")
        try sendTranslation(owner, context)
        await fulfillment(of: [delta], timeout: 25)
        owner.connection.forceStop()
        await fulfillment(of: [owner.stopped], timeout: 15)
        XCTAssertEqual(owner.failures, [.translationOutcomeUnknown])
        XCTAssertFalse(owner.events.contains { $0.id == "translation" && $0.type == "completed" })
        try verifyTranslation(context, turns: 1, cleanup: true, descendant: true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: context.historyFile.path))

        let successor = ConfigurationNotices()
        defer { successor.connection.forceStop() }
        startTranslation(successor, context)
        await fulfillment(of: [successor.ready], timeout: 10)
        let history = successor.terminal("history"), config = successor.terminal("config")
        successor.connection.loadHistory(id: "history")
        successor.connection.loadConfiguration(id: "config")
        await fulfillment(of: [history, config], timeout: 10, enforceOrder: true)
        XCTAssertEqual(try successor.historyEntries("history"), [])
        XCTAssertEqual(successor.result("config")?.payload["config"]?.object?["codex_model"], .string("synthetic"))
        try verifyTranslation(context, turns: 1, cleanup: true, descendant: true)
        successor.connection.stop()
        await fulfillment(of: [successor.stopped], timeout: 10)
        XCTAssertTrue(successor.failures.isEmpty, "Reopening releases locks but never replays an unknown submission")
    }
}

final class HelperIntegrationTests: XCTestCase {
    @MainActor
    private final class ConfigurationNotices {
        let ready = XCTestExpectation(description: "configuration ready")
        let stopped = XCTestExpectation(description: "configuration helper exited")
        private(set) var events: [ServerEvent] = []
        private(set) var failures: [ProbeError] = []
        private(set) var didStop = false
        private var terminals: [String: XCTestExpectation] = [:]
        private var starts: [String: XCTestExpectation] = [:]
        private var deltas: [String: XCTestExpectation] = [:]
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
                        if event.type == "delta" { self.deltas.removeValue(forKey: event.id)?.fulfill() }
                        if event.isTerminal { self.terminals[event.id]?.fulfill() }
                    case .failure(let error): self.failures.append(error)
                    case .stopped:
                        self.didStop = true
                        self.stopped.fulfill()
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

        func firstDelta(_ id: String) -> XCTestExpectation {
            let expectation = XCTestExpectation(description: "native translation first delta")
            deltas[id] = expectation
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
        XCTAssertEqual(session.result("missing")?.payload["config"]?.object?["plain_text_paste_enabled"], .bool(false))
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
        var pasteSettings = try XCTUnwrap(normalized)
        pasteSettings["plain_text_paste_enabled"] = .bool(true)
        let pasteSaved = session.terminal("paste-save")
        session.connection.saveConfiguration(pasteSettings, id: "paste-save")
        await fulfillment(of: [pasteSaved], timeout: 10)
        session.assertOperation("paste-save")
        XCTAssertEqual(session.result("paste-save")?.payload, ["saved": .bool(true)])
        let pasteRead = session.terminal("paste-read")
        session.connection.loadConfiguration(id: "paste-read")
        await fulfillment(of: [pasteRead], timeout: 10)
        session.assertOperation("paste-read")
        XCTAssertEqual(session.result("paste-read")?.payload["config"]?.object?["plain_text_paste_enabled"], .bool(true))
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
        XCTAssertEqual(view?["plain_text_paste_enabled"], .bool(true))
        var disabledSettings = try XCTUnwrap(view)
        disabledSettings["plain_text_paste_enabled"] = .bool(false)
        let pasteDisabled = reopened.terminal("paste-disable")
        reopened.connection.saveConfiguration(disabledSettings, id: "paste-disable")
        await fulfillment(of: [pasteDisabled], timeout: 10)
        reopened.assertOperation("paste-disable")
        XCTAssertEqual(reopened.result("paste-disable")?.payload, ["saved": .bool(true)])
        let disabledRead = reopened.terminal("paste-disabled-read")
        reopened.connection.loadConfiguration(id: "paste-disabled-read")
        await fulfillment(of: [disabledRead], timeout: 10)
        reopened.assertOperation("paste-disabled-read")
        XCTAssertEqual(reopened.result("paste-disabled-read")?.payload["config"]?.object?["plain_text_paste_enabled"], .bool(false))
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
            return XCTFail("Business readiness must expose storage and dictionary capabilities")
        }
        XCTAssertEqual(Set(capabilities.compactMap(\.string)),
                       Set(["config_load", "config_save", "history_load", "history_add", "history_clear"])
                        .union(DictionaryRequest.operations))
        XCTAssertEqual(capabilities.count, 11)
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

        let search = session.terminal("search_dict")
        session.connection.loadHistory(pageSize: 1, query: "  DEFINITION ", kind: "dict", id: "search_dict")
        await fulfillment(of: [search], timeout: 10)
        session.assertOperation("search_dict")
        XCTAssertEqual(try session.historyEntries("search_dict"), entries.filter { $0["kind"] == .string("dict") })
        XCTAssertEqual(session.result("search_dict")?.payload["total"], .integer(1))
        XCTAssertEqual(session.result("search_dict")?.payload["next_cursor"], .null)
        XCTAssertNotEqual(session.result("search_dict")?.payload["revision"], session.result("all")?.payload["revision"])
        XCTAssertNotEqual(try session.historyEntries("search_dict"), try session.historyEntries("first"))

        let filteredFirst = session.terminal("filtered_first")
        session.connection.loadHistory(pageSize: 1, query: "synthetic", id: "filtered_first")
        await fulfillment(of: [filteredFirst], timeout: 10)
        session.assertOperation("filtered_first")
        XCTAssertEqual(session.result("filtered_first")?.payload["total"], .integer(2))
        let filteredCursor = try XCTUnwrap(session.result("filtered_first")?.payload["next_cursor"])
        XCTAssertNotEqual(filteredCursor, .null)
        let filteredRest = session.terminal("filtered_rest")
        session.connection.loadHistory(pageSize: 1, cursor: filteredCursor, query: " SYNTHETIC ", id: "filtered_rest")
        await fulfillment(of: [filteredRest], timeout: 10)
        session.assertOperation("filtered_rest")
        XCTAssertEqual(try session.historyEntries("filtered_first") + session.historyEntries("filtered_rest"),
                       entries.filter { $0["input"]?.string?.hasPrefix("synthetic") == true })

        let changedFilter = session.terminal("changed_filter")
        session.connection.loadHistory(pageSize: 1, cursor: filteredCursor, query: "synthetic",
                                       kind: "code", id: "changed_filter")
        await fulfillment(of: [changedFilter], timeout: 10)
        session.assertHistoryFailure("changed_filter", code: "history_cursor_expired")
        let unicodeSearch = session.terminal("unicode_search")
        session.connection.loadHistory(query: "\u{4e2d}", kind: "ocr", id: "unicode_search")
        await fulfillment(of: [unicodeSearch], timeout: 10)
        session.assertOperation("unicode_search")
        XCTAssertEqual(try session.historyEntries("unicode_search"), entries.filter { $0["kind"] == .string("ocr") })
        let noMatches = session.terminal("no_matches")
        session.connection.loadHistory(query: "not in this synthetic history", id: "no_matches")
        await fulfillment(of: [noMatches], timeout: 10)
        session.assertOperation("no_matches")
        XCTAssertEqual(try session.historyEntries("no_matches"), [])
        XCTAssertEqual(session.result("no_matches")?.payload["total"], .integer(0))
        XCTAssertEqual(try Data(contentsOf: history), stored, "Search must not rewrite history.")

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
