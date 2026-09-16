import AppKit
import CryptoKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

private enum DictionaryProductTestError: Error {
    case invalidAsset, invalidDestination, destinationExists, assetDigestMismatch
    case readinessTimeout, lookupFailed, viewTimeout, shutdownTimeout
}

// Copies only externally supplied, pinned fixture bytes. This is not a URLSession/network measurement.
@MainActor
private final class PinnedDictionaryFixtureCopier: DictionaryDownloading {
    let asset: URL
    let home: URL
    private(set) var copies = 0
    private(set) var failure: Error?

    init(asset: URL, home: URL) { self.asset = asset; self.home = home }

    func start(_ ticket: DictionaryInstallTicket, progress: @escaping (Int64) -> Void,
               completion: @escaping (Result<Void, DictionaryDownloadError>) -> Void) {
        do {
            let attributes = try FileManager.default.attributesOfItem(atPath: asset.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular,
                  (attributes[.size] as? NSNumber)?.int64Value == ticket.size else {
                throw DictionaryProductTestError.invalidAsset
            }
            // The reserved file does not exist yet; resolve its existing parent, as in the Foundation harness.
            let directory = ticket.path.deletingLastPathComponent().resolvingSymlinksInPath()
            guard directory.path.hasPrefix(home.resolvingSymlinksInPath().path + "/") else {
                throw DictionaryProductTestError.invalidDestination
            }
            guard !FileManager.default.fileExists(atPath: ticket.path.path) else {
                throw DictionaryProductTestError.destinationExists
            }
            let handle = try FileHandle(forReadingFrom: asset)
            var hash = SHA256()
            do {
                while let bytes = try handle.read(upToCount: 1024 * 1024), !bytes.isEmpty {
                    hash.update(data: bytes)
                }
                try handle.close()
            } catch {
                do { try handle.close() } catch { XCTFail("Pinned fixture read handle did not close.") }
                throw error
            }
            guard hash.finalize().map({ String(format: "%02x", $0) }).joined() == ticket.sha256 else {
                throw DictionaryProductTestError.assetDigestMismatch
            }
            try FileManager.default.copyItem(at: asset, to: ticket.path)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: ticket.path.path)
            copies += 1
            progress(ticket.size)
            completion(.success(()))
        } catch {
            failure = error
            completion(.failure(.fileIO))
        }
    }

    func cancel() {
        // Setup copying is synchronous on the main actor; no writer survives start's completion.
    }
}

@MainActor
private final class DictionaryProductNotices {
    var connections: [HelperConnection] = []
    var stopped = Set<UUID>()
    var lookupTerminal: UInt64?
    var lastLookup: DictionaryLookupResult?
    var failure: ProbeError?
    var configurationOnly = false

    func record(_ notice: HelperNotice, connection: UUID) {
        switch notice {
        case .event(let event):
            if event.type == "ready", case let .array(capabilities)? = event.payload["capabilities"] {
                configurationOnly = !capabilities.contains(.string("translate"))
                XCTAssertTrue(capabilities.contains(.string("dictionary_lookup")))
            }
            if event.type == "completed", event.payload["status"] == .string("hit") {
                lookupTerminal = DispatchTime.now().uptimeNanoseconds
                do { lastLookup = try DictionaryLookupResult(payload: event.payload) }
                catch { XCTFail("Bundled helper returned an invalid dictionary hit: \(error)") }
            }
        case .failure(let error): failure = error
        case .stopped: stopped.insert(connection)
        }
    }
}

final class DictionaryProductIntegrationTests: XCTestCase {
    @MainActor
    func testBundledWarmDictionaryIntentToNativePaintReportsMeasuredGoal() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let app = environment["CC_TRANSLATE_APP"], !app.isEmpty else {
            throw XCTSkip("POSTBUILD only: set CC_TRANSLATE_APP; never use host Python.")
        }
        let asset = try XCTUnwrap(environment["CC_TRANSLATE_DICTIONARY_TEST_ASSET"],
                                 "POSTBUILD requires the externally supplied pinned dictionary; no automatic download.")
        XCTAssertFalse(asset.isEmpty)
        let runtime = try BundleRuntime(appURL: URL(fileURLWithPath: app, isDirectory: true))
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("cc-dictionary-product-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: false)
        let copier = PinnedDictionaryFixtureCopier(asset: URL(fileURLWithPath: asset), home: home)
        let notices = DictionaryProductNotices()
        let model = ProbeModel(persistsPreferences: false, makeConnection: { callback in
            let id = UUID()
            let connection = HelperConnection { notice in
                MainActor.assumeIsolated {
                    notices.record(notice, connection: id)
                    callback(notice)
                }
            }
            notices.connections.append(connection)
            return connection
        }, runtimeProvider: { runtime }, locateCandidates: { _, _ in [] },
           dictionaryDownloader: copier, homeDirectory: home)
        model.interfaceLanguage = "en"
        model.onConfigurationRequired = { XCTFail("The real pinned entry must not require Codex setup.") }
        _ = NSApplication.shared
        let size = NSSize(width: 760, height: 620)
        let host = NSHostingView(rootView: TranslationResultView(model: model, compact: true)
            .environment(\.colorScheme, .light))
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .aqua)
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)

        do {
            let appearance = try XCTUnwrap(window.appearance)
            host.appearance = appearance
            model.openProduct()
            guard await eventually(timeout: 15, { model.settingsReady && !model.settingsBusy }) else {
                throw DictionaryProductTestError.readinessTimeout
            }
            model.dictionary.download()
            guard await eventually(timeout: 65, {
                copier.failure != nil ||
                    (model.dictionary.status?.state == .ready && model.dictionary.status?.enabled == true &&
                     !model.dictionary.busy && model.settingsReady && !model.settingsBusy)
            }), copier.failure == nil else {
                XCTFail("Pinned fixture setup failed: \(model.dictionary.messageEnglish); \(String(describing: copier.failure))")
                throw DictionaryProductTestError.readinessTimeout
            }
            model.saveSettings(history: false)
            guard await eventually(timeout: 15, {
                model.settingsReady && !model.settingsBusy && !model.historyEnabled
            }) else { throw DictionaryProductTestError.readinessTimeout }

            host.layoutSubtreeIfNeeded()
            guard await eventually(timeout: 5, {
                host.layoutSubtreeIfNeeded()
                return self.resultText(in: host) != nil
            }) else { throw DictionaryProductTestError.viewTimeout }
            let nativeText = try XCTUnwrap(resultText(in: host))
            let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            var samples: [Double] = []
            var terminalSamples: [Double] = []
            // Two unmeasured intents warm SQLite, text layout, fonts, and the retained SwiftUI/AppKit tree.
            for index in 0..<12 {
                model.clearTranslation()
                guard await eventually(timeout: 5, {
                    host.layoutSubtreeIfNeeded()
                    return nativeText.string.isEmpty
                }) else { throw DictionaryProductTestError.viewTimeout }
                model.input = "\u{4f60}\u{597d}"
                notices.lookupTerminal = nil
                notices.lastLookup = nil
                let start = DispatchTime.now().uptimeNanoseconds
                model.translate()
                guard await eventually(timeout: 5, {
                    model.productPhase == .completed || model.productPhase == .failed
                }), model.productPhase == .completed, model.resultKind == "dict" else {
                    XCTFail("Real model lookup did not complete locally: \(model.productMessage)")
                    throw DictionaryProductTestError.lookupFailed
                }
                guard await eventually(timeout: 5, {
                    host.layoutSubtreeIfNeeded()
                    return nativeText.string == model.output && !nativeText.string.isEmpty
                }) else { throw DictionaryProductTestError.viewTimeout }
                host.displayIfNeeded()
                appearance.performAsCurrentDrawingAppearance {
                    host.cacheDisplay(in: host.bounds, to: bitmap)
                }
                let painted = DispatchTime.now().uptimeNanoseconds
                let terminal = try XCTUnwrap(notices.lookupTerminal)
                if index >= 2 {
                    samples.append(Double(painted - start) / 1_000_000)
                    terminalSamples.append(Double(terminal - start) / 1_000_000)
                }
                XCTAssertTrue(resultText(in: host) === nativeText, "Streaming/result updates must retain the native view.")
                XCTAssertFalse(nativeText.isEditable)
                XCTAssertTrue(nativeText.isSelectable)
                XCTAssertFalse(model.nativeTranslation)
                XCTAssertTrue(model.selectedCLI.isEmpty)
                XCTAssertEqual(notices.lastLookup?.result?["submitted"], .bool(false))
                XCTAssertEqual(notices.lastLookup?.result?["cached"], .bool(false))
                XCTAssertEqual(notices.lastLookup?.result?["history"], .string("disabled"))
            }
            let sorted = samples.sorted()
            let maximum = try XCTUnwrap(sorted.last)
            let p95 = sorted[Int(ceil(Double(sorted.count) * 0.95)) - 1]
            let image = try XCTUnwrap(bitmap.cgImage)
            let recognized = try LocalOCR.recognize(image).text.lowercased()
            let sourceVisible = recognized.contains("cedict")
            let marker = try JSONValue.object([
                "scope": .string("same_source_model_and_retained_swiftui_appkit_view_with_bundled_configuration_helper"),
                "source_cohort": .string("current Swift package model/view; helper from CC_TRANSLATE_APP; not the packaged GUI process"),
                "endpoint": .string("explicit_model_translate_to_matching_read_only_nstextview_and_offscreen_bitmap_paint"),
                "physical_keyboard": .bool(false), "full_gui": .bool(false), "onscreen_presentation": .bool(false),
                "unit": .string("ms"), "warmup_intents": .integer(2), "warm_intents": .integer(Int64(samples.count)),
                "native_paint_samples": .array(samples.map(JSONValue.number)),
                "intent_to_lookup_terminal_samples": .array(terminalSamples.map(JSONValue.number)),
                "native_paint_p95": .number(p95), "native_paint_max": .number(maximum),
                "native_view_goal_ms": .integer(150),
                "native_view_goal_result": .string(maximum <= 150 ? "met" : "measured_miss"),
                "goal_is_asserted": .bool(false),
                "query_format_goal_ms": .integer(10), "query_format_directly_measured": .bool(false),
                "query_format_note": .string("terminal samples include Swift scheduling, framing and IPC; not isolated query/format time"),
                "setup_and_install_included": .bool(false), "network_download_measured": .bool(false),
                "history_enabled": .bool(false), "cache_hits": .bool(false), "cli_candidates": .integer(0),
                "poll_interval_ms": .integer(1), "ocr_source_visible": .bool(sourceVisible),
                "os": .string(ProcessInfo.processInfo.operatingSystemVersionString)
            ]).encoded()
            print("CC_TRANSLATE_DICTIONARY_PRODUCT_TIMINGS " + String(decoding: marker, as: UTF8.self))
            XCTAssertEqual(samples.count, 10)
            XCTAssertTrue(sourceVisible, "The painted native dictionary must show its real CC-CEDICT attribution.")
            XCTAssertTrue(notices.configurationOnly)
            XCTAssertNil(notices.failure)
            XCTAssertEqual(notices.connections.count, 1)
            XCTAssertEqual(copier.copies, 1)
        } catch {
            do { try await cleanup(model, notices: notices, window: window, home: home) }
            catch { XCTFail("Dictionary product cleanup failed: \(error)") }
            throw error
        }
        try await cleanup(model, notices: notices, window: window, home: home)
    }

    @MainActor
    private func eventually(timeout: TimeInterval, _ condition: () -> Bool) async -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        while ProcessInfo.processInfo.systemUptime < deadline {
            if condition() { return true }
            do { try await Task.sleep(nanoseconds: 1_000_000) }
            catch { return false }
        }
        return condition()
    }

    @MainActor
    private func resultText(in view: NSView) -> NSTextView? {
        if let text = view as? NSTextView, !text.isEditable { return text }
        for child in view.subviews {
            if let text = resultText(in: child) { return text }
        }
        return nil
    }

    @MainActor
    private func cleanup(_ model: ProbeModel, notices: DictionaryProductNotices,
                         window: NSWindow, home: URL) async throws {
        model.prepareToQuit()
        window.contentView = nil
        window.close()
        if !(await eventually(timeout: 70, { !model.hasProcesses && notices.stopped.count == notices.connections.count })) {
            notices.connections.forEach { $0.forceStop() }
            XCTFail("Graceful dictionary helper shutdown exceeded its bounded drain window.")
            guard await eventually(timeout: 10, { notices.stopped.count == notices.connections.count }) else {
                throw DictionaryProductTestError.shutdownTimeout
            }
        }
        try FileManager.default.removeItem(at: home)
    }
}
