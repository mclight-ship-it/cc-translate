import AppKit
import Darwin
import XCTest
@testable import CCTranslateMac

@MainActor
enum WorkspaceJourneyProcess {
    private static let childKey = "CC_TRANSLATE_WORKSPACE_JOURNEY"
    private enum Failure: Error { case timeout, missingResult }

    // Like the existing Dock tests, Hide and activation run in a disposable XCTest
    // application, never in the shared host that owns subsequent tests' focus.
    static func run(_ function: String, body: @escaping @MainActor () async throws -> Void) throws {
        let method = String(function.prefix { $0 != "(" })
        if ProcessInfo.processInfo.environment[childKey] == method {
            _ = NSApplication.shared
            CCTranslateApplication.configureNormalApplication(NSApp)
            var result: Result<Void, Error>?
            Task { @MainActor in
                do {
                    XCTAssertTrue(NSApp.isRunning)
                    try await body()
                    result = .success(())
                } catch {
                    result = .failure(error)
                }
                NSApp.stop(nil)
                if let wake = NSEvent.otherEvent(
                    with: .applicationDefined, location: .zero, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: 0,
                    context: nil, subtype: 0, data1: 0, data2: 0) {
                    NSApp.postEvent(wake, atStart: true)
                }
            }
            NSApp.run()
            guard let result else { throw Failure.missingResult }
            try result.get()
            return
        }

        let host = NSApplication.shared
        let policy = host.activationPolicy()
        let hidden = host.isHidden
        let frontmost = NSWorkspace.shared.frontmostApplication
        let previousWindow = host.keyWindow
        let previousResponder = previousWindow?.firstResponder
        let previousSelection = (previousResponder as? NSTextView)?.selectedRanges
        defer {
            if let frontmost, !frontmost.isTerminated { frontmost.activate(options: []) }
            if let previousWindow, previousWindow.isVisible {
                previousWindow.makeKeyAndOrderFront(nil)
                if let previousResponder {
                    XCTAssertTrue(previousWindow.makeFirstResponder(previousResponder))
                    XCTAssertTrue(previousWindow.firstResponder === previousResponder)
                    if let previousSelection, let editor = previousResponder as? NSTextView {
                        XCTAssertEqual(editor.selectedRanges, previousSelection)
                    }
                }
            }
            XCTAssertEqual(host.activationPolicy(), policy)
            XCTAssertEqual(host.isHidden, hidden, "A journey must not hide the shared XCTest host.")
        }

        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("cc-workspace-journey-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { XCTAssertNoThrow(try FileManager.default.removeItem(at: directory)) }
        let log = directory.appendingPathComponent("xctest.log")
        try Data().write(to: log)
        let output = try FileHandle(forWritingTo: log)
        defer { XCTAssertNoThrow(try output.close()) }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        process.arguments = ["-XCTest", "CCTranslateMacTests.WorkspaceUserJourneyTests/\(method)",
                             Bundle(for: WorkspaceUserJourneyTests.self).bundlePath]
        var environment = ProcessInfo.processInfo.environment
        environment[childKey] = method
        if let path = environment["CC_TRANSLATE_UI_SCREENSHOTS_DIR"], !path.isEmpty {
            environment["CC_TRANSLATE_UI_SCREENSHOTS_DIR"] = URL(fileURLWithPath: path)
                .appendingPathComponent(method, isDirectory: true).path
        }
        process.environment = environment
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let deadline = Date().addingTimeInterval(120)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
        if process.isRunning {
            process.terminate()
            let terminationDeadline = Date().addingTimeInterval(2)
            while process.isRunning && Date() < terminationDeadline { Thread.sleep(forTimeInterval: 0.02) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            let transcript = try String(contentsOf: log, encoding: .utf8)
            try NativeRenderEvidence.record("Workspace journey timeout \(method):\n\(transcript)")
            XCTFail("Isolated workspace journey exceeded 120 seconds.")
            throw Failure.timeout
        }
        process.waitUntilExit()
        let transcript = try String(contentsOf: log, encoding: .utf8)
        let passed = transcript.contains(
            "Test Case '-[CCTranslateMacTests.WorkspaceUserJourneyTests \(method)]' passed")
        if process.terminationStatus != 0 || !passed {
            try NativeRenderEvidence.record("Workspace journey failure \(method):\n\(transcript)")
        }
        XCTAssertEqual(process.terminationReason, .exit, transcript)
        XCTAssertEqual(process.terminationStatus, 0, transcript)
        XCTAssertTrue(passed, transcript)
        if process.terminationReason == .exit && process.terminationStatus == 0 && passed {
            let evidence = transcript.split(separator: "\n").filter {
                $0.hasPrefix("WORKSPACE JOURNEY ") || $0.hasPrefix("BOUNDARY: ") ||
                    $0.hasPrefix("JOURNEY seed=") ||
                    ($0.hasPrefix("JOURNEY ") && $0.contains(" COMPLETE ")) ||
                    $0.hasPrefix("RETAINED PAGE: ") || $0.hasPrefix("SHEET GUARD COMPLETE: ") ||
                    $0.hasPrefix("COLD QUICK INPUT") || $0.hasPrefix("BUSY QUICK REPLACEMENT:")
            }
            XCTAssertFalse(evidence.isEmpty, "Successful child tests must supply scoped journey evidence.")
            try NativeRenderEvidence.record(
                "WORKSPACE CHILD VERIFIED \(method)\n" + evidence.joined(separator: "\n"))
        }
    }
}
