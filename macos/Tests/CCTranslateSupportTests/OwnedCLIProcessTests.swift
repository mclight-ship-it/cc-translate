import XCTest
import Darwin
@testable import CCTranslateSupport

final class OwnedCLIProcessTests: XCTestCase {
    @MainActor
    func testNormalLeaderExitStillCleansItsBackgroundChild() async throws {
        try await exercise(mode: "exit", expected: nil)
    }

    @MainActor
    func testCancellationKillsOnlyOwnedGroupIncludingTERMResistantChild() async throws {
        try await exercise(mode: "wait", expected: .cliCancelled, cancel: true)
    }

    @MainActor
    func testTimeoutKillsOnlyOwnedGroupIncludingTERMResistantChild() async throws {
        try await exercise(mode: "wait", expected: .cliTimeout)
    }

    @MainActor
    func testOutputLimitCleansOwnedDescendants() async throws {
        try await exercise(mode: "flood", expected: .cliOutputLimit)
    }

    @MainActor
    func testLaunchFailureCompletesOnceAndCanBeCancelledAgain() async {
        let finished = expectation(description: "spawn failure")
        finished.assertForOverFulfill = true
        let run = CLIVersionRun { result in
            if case .failure(let error) = result { XCTAssertEqual(error, .cliFailed) }
            else { XCTFail("A nonexistent executable must fail.") }
            finished.fulfill()
        }
        run.start(executable: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        await fulfillment(of: [finished], timeout: 3)
        run.cancel()
        run.cancel()
    }

    @MainActor
    private func exercise(mode: String, expected: ProbeError?, cancel: Bool = false) async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("CC owned process \(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer {
            do { try FileManager.default.removeItem(at: directory) }
            catch { XCTFail("Synthetic fixture cleanup failed.") }
        }
        let rootFile = directory.appendingPathComponent("root.pid")
        let childFile = directory.appendingPathComponent("child.pid")
        let executable = directory.appendingPathComponent("fake-cli")
        func quoted(_ url: URL) -> String {
            "'" + url.path.replacingOccurrences(of: "'", with: "'\\''") + "'"
        }
        let script = """
        #!/bin/sh
        test "$#" -eq 1 && test "$1" = "--version" || exit 71
        test "$PWD" = "$HOME" || exit 72
        if read -r unexpected; then exit 73; fi
        trap '' TERM
        echo $$ > \(quoted(rootFile))
        /bin/sh -c 'trap "" TERM; echo $$ > "$1"; exec /bin/sleep 30' fixture \(quoted(childFile)) &
        while [ ! -s \(quoted(childFile)) ]; do /bin/sleep 0.01; done
        printf 'SYNTHETIC VERSION\\n'
        case '\(mode)' in
          exit) exit 0 ;;
          flood) while :; do printf 'SYNTHETIC OUTPUT LIMIT\\n'; done ;;
          wait) wait ;;
          *) exit 74 ;;
        esac
        """
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)

        let unrelated = Process()
        unrelated.executableURL = URL(fileURLWithPath: "/bin/sleep")
        unrelated.arguments = ["30"]
        unrelated.standardInput = FileHandle.nullDevice
        unrelated.standardOutput = FileHandle.nullDevice
        unrelated.standardError = FileHandle.nullDevice
        try unrelated.run()
        defer {
            if unrelated.isRunning { unrelated.terminate() }
            unrelated.waitUntilExit()
        }

        let finished = expectation(description: "owned group cleaned and pipes closed")
        finished.assertForOverFulfill = true
        let run = CLIVersionRun { result in
            switch (result, expected) {
            case (.success, nil): break
            case (.failure(let actual), .some(let expected)): XCTAssertEqual(actual, expected)
            default: XCTFail("Unexpected synthetic version probe outcome.")
            }
            finished.fulfill()
        }
        defer { run.cancel() }
        run.start(executable: executable)
        let root = try await waitForPID(rootFile)
        let child = try await waitForPID(childFile)
        if mode == "wait" {
            XCTAssertEqual(getpgid(root), root)
            XCTAssertEqual(getpgid(child), root)
            XCTAssertNotEqual(root, getpgrp())
        }
        if cancel { run.cancel() }
        await fulfillment(of: [finished], timeout: 10)
        try await assertGone(root)
        try await assertGone(child)
        XCTAssertTrue(unrelated.isRunning, "A sibling process must not be terminated.")
        run.cancel()
    }

    @MainActor
    private func waitForPID(_ file: URL) async throws -> pid_t {
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: file.path) {
                let text = try String(contentsOf: file, encoding: .utf8)
                if let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 {
                    return pid
                }
            }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Synthetic process did not become ready.")
        throw ProbeError.cliFailed
    }

    @MainActor
    private func assertGone(_ pid: pid_t) async throws {
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline {
            if Darwin.kill(pid, 0) == -1, errno == ESRCH { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Synthetic owned process remains alive or unreaped.")
    }
}
