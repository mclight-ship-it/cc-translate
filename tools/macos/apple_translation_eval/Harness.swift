import AppKit
import CoreGraphics
import Foundation
import SwiftUI
import Translation
import Vision

private func inspectDownloadWindow() throws {
    let args = CommandLine.arguments
    guard args.count == 5, let pid = Int(args[3]), let windowID = UInt32(args[4]),
          let windows = CGWindowListCopyWindowInfo(.optionIncludingWindow, windowID) as? [[String: Any]],
          let window = windows.first,
          window[kCGWindowOwnerPID as String] as? Int == pid,
          window[kCGWindowIsOnscreen as String] as? Bool == true,
          let bounds = window[kCGWindowBounds as String] as? [String: Double],
          let bitmap = NSBitmapImageRep(data: try Data(contentsOf: URL(fileURLWithPath: args[2])))
    else {
        throw NSError(domain: "DownloadWindowInspection", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Owned visible window or capture unavailable"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(url: URL(fileURLWithPath: args[2])).perform([request])
    let texts: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        return ["text": candidate.string, "confidence": candidate.confidence,
                "x": observation.boundingBox.midX, "y": observation.boundingBox.midY]
    }
    let result: [String: Any] = [
        "pid": pid, "window_id": windowID, "bounds": bounds,
        "can_post_events": CGPreflightPostEventAccess(),
        "image_width": bitmap.pixelsWide, "image_height": bitmap.pixelsHigh, "texts": texts,
    ]
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
}

private struct DownloadClick: Decodable {
    let pid: Int32
    let window_id: UInt32
    let bounds: [String: Double]
    let point: [Double]
    let label: String
}

@MainActor
private func postDownloadClick() throws {
    guard CommandLine.arguments.count == 3 else {
        throw NSError(domain: "DownloadWindowClick", code: 1)
    }
    let target = try JSONDecoder().decode(
        DownloadClick.self, from: Data(CommandLine.arguments[2].utf8))
    guard ["Download", "Done"].contains(target.label),
          CGPreflightPostEventAccess(),
          NSWorkspace.shared.frontmostApplication?.processIdentifier == target.pid,
          NSRunningApplication(processIdentifier: target.pid)?.bundleIdentifier == "org.cctranslate.AppleTranslationEval",
          let windows = CGWindowListCopyWindowInfo(.optionIncludingWindow, target.window_id) as? [[String: Any]],
          let window = windows.first,
          window[kCGWindowOwnerPID as String] as? Int32 == target.pid,
          window[kCGWindowIsOnscreen as String] as? Bool == true,
          let bounds = window[kCGWindowBounds as String] as? [String: Double],
          bounds == target.bounds,
          let x = bounds["X"], let y = bounds["Y"],
          let width = bounds["Width"], let height = bounds["Height"],
          target.point.count == 2, target.point.allSatisfy({ $0.isFinite }),
          CGRect(x: x, y: y, width: width, height: height).contains(
            CGPoint(x: target.point[0], y: target.point[1]))
    else {
        throw NSError(domain: "DownloadWindowClick", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Event permission, foreground owner, or observed bounds changed"])
    }
    let point = CGPoint(x: target.point[0], y: target.point[1])
    guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                             mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                           mouseCursorPosition: point, mouseButton: .left) else {
        throw NSError(domain: "DownloadWindowClick", code: 3)
    }
    down.setIntegerValueField(.mouseEventClickState, value: 1)
    up.setIntegerValueField(.mouseEventClickState, value: 1)
    // Accessibility's click-at succeeded without activating this remote system sheet.
    // Use the observed control in the verified foreground window; readiness is checked separately.
    down.post(tap: .cghidEventTap)
    usleep(50_000)
    up.post(tap: .cghidEventTap)
    print("posted_observed_\(target.label.lowercased())")
}

struct EvaluationCase: Decodable {
    let id: String
    let pair: String
    let source: String
}

struct Input: Decodable {
    let run_id: String
    let mode: String
    let state_path: String
    let cases: [EvaluationCase]
    let prepare_timeout: Double
    let request_timeout: Double
    let availability_timeout: Double
    let cancel_timeout: Double
    let cancel_delay_ms: Double
}

@MainActor
final class Evaluation: ObservableObject {
    @Published var configuration: TranslationSession.Configuration?
    @Published var message = "Starting real Apple Translation evaluation…"
    private let input: Input
    private let start = ProcessInfo.processInfo.systemUptime
    private var state: [String: Any]
    private var watchdog: Task<Void, Never>?
    private var started = false
    private var terminal = false
    private var pairIndex = 0
    private var handledPairs = Set<String>()
    private var pairs: [[String: Any]] = []
    private var outputs: [[String: Any]] = []
    private var cancellations: [[String: Any]] = []
    private let languages = LanguageAvailability()
    private let pairIDs = ["en-zh", "zh-en"]

    init() {
        do {
            guard CommandLine.arguments.count == 2 else {
                throw NSError(domain: "EvaluationInput", code: 1)
            }
            input = try JSONDecoder().decode(Input.self, from: Data(
                contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        } catch {
            fputs("Cannot read evaluation input: \(error)\n", stderr)
            exit(2)
        }
        state = [
            "schema_version": 1, "run_id": input.run_id,
            "engine": "Apple.Translation.TranslationSession", "strategy": "system_default",
            "mode": input.mode,
            "status": "starting", "phase": "initializing",
            "pid": ProcessInfo.processInfo.processIdentifier,
            "os_version": ProcessInfo.processInfo.operatingSystemVersionString,
            "process_architecture": "arm64", "outputs": [], "pairs": [], "cancellations": [],
            "model_coldness": "unknown; no model unload or download state reset",
        ]
        persist()
    }

    private func elapsed(_ since: Double) -> Double {
        (ProcessInfo.processInfo.systemUptime - since) * 1000
    }

    private func persist() {
        state["since_app_start_ms"] = elapsed(start)
        if let window = NSApplication.shared.windows.first(where: { $0.isVisible }) {
            state["window_id"] = window.windowNumber
        }
        state["pairs"] = pairs
        state["outputs"] = outputs
        state["cancellations"] = cancellations
        do {
            let data = try JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: URL(fileURLWithPath: input.state_path), options: .atomic)
        } catch {
            fputs("Cannot persist evaluation checkpoint: \(error)\n", stderr)
            exit(3)
        }
    }

    private func phase(_ name: String) {
        guard !terminal else { return }
        state["phase"] = name
        message = name.replacingOccurrences(of: "_", with: " ")
        persist()
    }

    private func arm(_ seconds: Double, condition: String) {
        watchdog?.cancel()
        let operationStart = ProcessInfo.processInfo.systemUptime
        state["operation"] = ["deadline_seconds": seconds, "deadline_condition": condition,
                              "started_since_app_start_ms": elapsed(start)]
        persist()
        watchdog = Task { @MainActor in
            do {
                try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            } catch { return }
            guard !terminal else { return }
            state["status"] = "blocked"
            state["blocked_condition"] = condition
            state["timed_out_operation_ms"] = elapsed(operationStart)
            phase("blocked")
            terminal = true
            // Leave a brief window for the supervisor's owned-window screenshot.
            try? await Task.sleep(nanoseconds: 750_000_000)
            exit(2)
        }
    }

    private func disarm() {
        watchdog?.cancel()
        watchdog = nil
    }

    private func statusName(_ value: LanguageAvailability.Status) -> String {
        switch value {
        case .installed: return "installed"
        case .supported: return "supported"
        case .unsupported: return "unsupported"
        @unknown default: return "unknown"
        }
    }

    private func languagePair(_ id: String) -> (Locale.Language, Locale.Language) {
        id == "en-zh"
            ? (Locale.Language(identifier: "en"), Locale.Language(identifier: "zh-Hans"))
            : (Locale.Language(identifier: "zh-Hans"), Locale.Language(identifier: "en"))
    }

    private func details(_ error: Error) -> [String: Any] {
        let value = error as NSError
        return ["domain": value.domain, "code": value.code, "description": value.localizedDescription,
                "swift_cancellation_error": error is CancellationError]
    }

    private func finish(_ status: String, condition: String? = nil) {
        guard !terminal else { return }
        disarm()
        state["status"] = status
        if let condition { state["blocked_condition"] = condition }
        phase(status)
        terminal = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 750_000_000)
            exit(["completed", "availability_only", "prepared"].contains(status) ? 0 : 2)
        }
    }

    func begin() async {
        guard !started else { return }
        started = true
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        let onConsole = session?[kCGSessionOnConsoleKey as String] as? Bool ?? false
        let loginDone = session?[kCGSessionLoginDoneKey as String] as? Bool ?? false
        state["gui"] = ["session_dictionary_available": session != nil,
                        "on_console": onConsole, "login_done": loginDone]
        guard session != nil, onConsole, loginDone else {
            finish("blocked", condition: "no_logged_in_console_gui_session")
            return
        }
        state["status"] = "running"
        phase("querying_supported_languages")
        arm(input.availability_timeout, condition: "supported_languages_query_deadline")
        let supported = await languages.supportedLanguages
        state["supported_languages"] = supported.map(\.minimalIdentifier).sorted()
        disarm()
        // Query BOTH directed pairs on this OS before configuring any translation task.
        for id in pairIDs {
            let (source, target) = languagePair(id)
            let before = ProcessInfo.processInfo.systemUptime
            arm(input.availability_timeout, condition: "pair_availability_query_deadline:\(id)")
            let status = await languages.status(from: source, to: target)
            disarm()
            pairs.append(["id": id, "source": source.minimalIdentifier, "target": target.minimalIdentifier,
                          "before": statusName(status), "availability_before_ms": elapsed(before)])
            persist()
        }
        if input.mode == "availability_only" {
            finish("availability_only")
        } else {
            advance()
        }
    }

    private func advance() {
        guard !terminal else { return }
        if pairIndex >= pairIDs.count {
            if input.mode == "prepare_only" {
                finish("prepared")
                return
            }
            finish(outputs.contains { ($0["status"] as? String) == "error" } ? "failed" : "completed",
                   condition: outputs.contains { ($0["status"] as? String) == "error" }
                       ? "one_or_more_translation_errors" : nil)
            return
        }
        let id = pairIDs[pairIndex]
        state["active_pair"] = id
        guard pairs[pairIndex]["before"] as? String != "unsupported",
              pairs[pairIndex]["before"] as? String != "unknown" else {
            finish("blocked", condition: "language_pair_not_supported_on_this_os:\(id)")
            return
        }
        let (source, target) = languagePair(id)
        phase("waiting_translation_task")
        arm(input.availability_timeout, condition: "swiftui_translation_task_not_delivered:\(id)")
        configuration = TranslationSession.Configuration(source: source, target: target)
    }

    func translate(_ session: TranslationSession) async {
        guard !terminal else { return }
        let index = pairIndex
        guard index < pairIDs.count else { return }
        let id = pairIDs[index]
        guard handledPairs.insert(id).inserted else { return }
        disarm()
        let (source, target) = languagePair(id)
        let preparing = ProcessInfo.processInfo.systemUptime
        phase("preparing_languages")
        arm(input.prepare_timeout, condition: "language_preparation_deadline:\(id)")
        do {
            // This is Apple's onscreen permission/download flow, not a private prewarm API.
            try await session.prepareTranslation()
            guard !terminal else { return }
            pairs[index]["prepare_api_ms"] = elapsed(preparing)
            var readiness = await languages.status(from: source, to: target)
            pairs[index]["after_prepare_api"] = statusName(readiness)
            persist()
            // prepareTranslation may return while a download is already in progress.
            while statusName(readiness) == "supported" {
                phase("waiting_language_install")
                try await Task.sleep(nanoseconds: 500_000_000)
                readiness = await languages.status(from: source, to: target)
                pairs[index]["latest_readiness"] = statusName(readiness)
                persist()
            }
            pairs[index]["prepare_ms"] = elapsed(preparing)
            pairs[index]["ready_before_requests"] = statusName(readiness)
            disarm()
            guard statusName(readiness) == "installed" else {
                pairs[index]["after"] = statusName(readiness)
                finish("blocked", condition: "languages_not_installed_after_preparation:\(id)")
                return
            }
        } catch {
            disarm()
            pairs[index]["prepare_ms"] = elapsed(preparing)
            pairs[index]["preparation_error"] = details(error)
            persist()
            arm(input.availability_timeout, condition: "availability_after_prepare_error_deadline:\(id)")
            pairs[index]["after"] = statusName(await languages.status(from: source, to: target))
            finish("blocked", condition: "prepare_translation_error:\(id)")
            return
        }
        if input.mode == "prepare_only" {
            pairs[index]["after"] = pairs[index]["ready_before_requests"]
            pairIndex += 1
            advance()
            return
        }
        var requestIndex = 0
        for pass in 0..<2 {
            for item in input.cases.filter({ $0.pair == id }) {
                guard !terminal else { return }
                phase("translating")
                state["active_case"] = item.id
                state["active_pass"] = pass
                persist()
                arm(input.request_timeout, condition: "translation_request_deadline:\(item.id):pass\(pass)")
                let began = ProcessInfo.processInfo.systemUptime
                var row: [String: Any] = [
                    "case_id": item.id, "pair": id, "pass": pass,
                    "phase": requestIndex == 0 ? "first_call_session" : "retained_session",
                    "status": "ok", "source_text": item.source, "target_text": "",
                ]
                do {
                    let response = try await session.translate(item.source)
                    row["target_text"] = response.targetText
                    row["response_source_text"] = response.sourceText
                    row["response_source_language"] = response.sourceLanguage.minimalIdentifier
                    row["response_target_language"] = response.targetLanguage.minimalIdentifier
                } catch {
                    row["status"] = "error"
                    row["error"] = details(error)
                }
                row["elapsed_ms"] = elapsed(began)
                row["since_app_start_ms"] = elapsed(start)
                disarm()
                outputs.append(row)
                requestIndex += 1
                persist()
            }
        }
        await probeCancellation(session, id: id)
        phase("querying_final_availability")
        let after = ProcessInfo.processInfo.systemUptime
        arm(input.availability_timeout, condition: "final_availability_query_deadline:\(id)")
        pairs[index]["after"] = statusName(await languages.status(from: source, to: target))
        pairs[index]["availability_after_ms"] = elapsed(after)
        disarm()
        pairIndex += 1
        // The next configuration creates a new SwiftUI session, not a reused cancelled session.
        advance()
    }

    private func probeCancellation(_ session: TranslationSession, id: String) async {
        let sourceCase = input.cases.first { $0.pair == id && $0.id.hasSuffix("-long") }!
        state["active_case"] = sourceCase.id
        state["active_pass"] = "cancellation"
        state["cancellation_source_repeat_count"] = 4
        phase("cancellation_probe")
        arm(input.cancel_timeout, condition: "cancellation_not_settled_before_deadline:\(id)")
        let text = Array(repeating: sourceCase.source, count: 4).joined(separator: "\n\n")
        let began = ProcessInfo.processInfo.systemUptime
        var done = false
        var requested = false
        var cancelTime: Double?
        let work = Task { @MainActor in try await session.translate(text) }
        let cancel = Task { @MainActor in
            do {
                try await Task.sleep(nanoseconds: UInt64(input.cancel_delay_ms * 1_000_000))
            } catch { return }
            guard !done else { return }
            requested = true
            cancelTime = elapsed(began)
            state["cancellation_requested_ms"] = cancelTime
            persist()
            work.cancel()
        }
        var row: [String: Any] = [
            "pair": id, "case_id": sourceCase.id, "source_repeat_count": 4,
            "mechanism": "swift_task_cancel", "configured_delay_ms": input.cancel_delay_ms,
            "target_text": "",
        ]
        do {
            let response = try await work.value
            row["target_text"] = response.targetText
            row["outcome"] = requested ? "completed_after_cancel" : "completed_before_cancel"
        } catch {
            row["error"] = details(error)
            row["outcome"] = requested
                ? (error is CancellationError ? "cancelled" : "error_after_cancel")
                : "error_before_cancel"
        }
        done = true
        cancel.cancel()
        row["cancel_requested"] = requested
        if let cancelTime { row["cancel_requested_ms"] = cancelTime }
        row["elapsed_ms"] = elapsed(began)
        cancellations.append(row)
        disarm()
        persist()
    }
}

@MainActor
struct EvaluationView: View {
    @ObservedObject var evaluation: Evaluation

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Real Apple Translation · public synthetic corpus").font(.headline)
            Text("This isolated test does not change the product. Summary remains first.")
            Text("Faithful text translation only. Not a replacement for summary or code explanation.")
            Text("If Apple asks, download English and Simplified Chinese to continue.")
            Text(evaluation.message).monospaced()
            Text("Outputs and elapsed times are saved after each request. Quality is assessed manually.")
                .font(.caption)
        }
        .padding(24)
        .frame(width: 640, height: 300)
        .translationTask(evaluation.configuration) { session in
            await evaluation.translate(session)
        }
    }
}

@MainActor
final class EvaluationDelegate: NSObject, NSApplicationDelegate {
    private let evaluation = Evaluation()
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 688, height: 348),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Apple Translation Evaluation"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: EvaluationView(evaluation: evaluation))
        window.center()
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        Task { @MainActor in await evaluation.begin() }
    }
}

@main
enum AppleTranslationEvaluationApp {
    @MainActor
    static func main() {
        if CommandLine.arguments.dropFirst().first == "--click-download-window" {
            do {
                try postDownloadClick()
            } catch {
                fputs("Cannot click download window: \(error)\n", stderr)
                exit(2)
            }
            return
        }
        if CommandLine.arguments.dropFirst().first == "--inspect-download-window" {
            do {
                try inspectDownloadWindow()
            } catch {
                fputs("Cannot inspect download window: \(error)\n", stderr)
                exit(2)
            }
            return
        }
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = EvaluationDelegate()
        application.delegate = delegate
        // Like the product, own the window explicitly when launched as a child executable.
        withExtendedLifetime(delegate) { application.run() }
    }
}
