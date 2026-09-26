import XCTest
@testable import CCTranslateSupport

final class CodexVersionTests: XCTestCase {
    private func parse(_ text: String) -> CodexVersion? {
        CodexVersion.parse(Data(text.utf8))
    }

    func testStableMinimumFutureMinorAndMajorCompareNumerically() throws {
        XCTAssertEqual(CodexVersion.minimum, "0.146.0")
        for numeric in ["0.146.0", "0.146.1", "0.147.0", "0.154.0", "0.200.0",
                        "0.1000.0", "1.0.0", "999999999.999999999.999999999"] {
            let version = try XCTUnwrap(parse("codex-cli " + numeric))
            XCTAssertEqual(version.numericVersion, numeric)
            XCTAssertEqual(version.policy, .meetsMinimum)
            XCTAssertFalse(version.isPrerelease)
        }
        for numeric in ["0.0.0", "0.9.999", "0.145.999999999"] {
            let version = try XCTUnwrap(parse("codex-cli " + numeric))
            XCTAssertEqual(version.numericVersion, numeric)
            XCTAssertEqual(version.policy, .tooOld)
        }
    }

    func testStableBuildMetadataIsAcceptedButNeverRetained() throws {
        let plain = try XCTUnwrap(parse("codex-cli 0.147.0"))
        let built = try XCTUnwrap(parse("codex-cli 0.147.0+SYNTHETIC-PRIVATE.build.01"))
        XCTAssertEqual(built, plain)
        XCTAssertEqual(try XCTUnwrap(parse("codex-cli 0.147.0+01")), plain)
        XCTAssertEqual(String(reflecting: built), String(reflecting: plain))
        XCTAssertEqual(built.policy, .meetsMinimum)
    }

    func testPrereleaseIsRecognizedButUnsupportedRegardlessOfNumericVersion() throws {
        for numeric in ["0.145.0", "0.146.0", "1.0.0"] {
            for suffix in ["alpha.1", "rc.2+SYNTHETIC-PRIVATE.build", "0", "01a", "-"] {
                let version = try XCTUnwrap(parse("codex-cli " + numeric + "-" + suffix))
                XCTAssertTrue(version.isPrerelease)
                XCTAssertEqual(version.numericVersion, numeric)
                XCTAssertEqual(version.policy, .prerelease)
                XCTAssertFalse(String(reflecting: version).contains("SYNTHETIC-PRIVATE"))
            }
        }
    }

    func testOnlyIdentifiedVersionLineCountsAmidUnrelatedWarningVersions() throws {
        let text = """
        Warning: runtime 99.0.0 /SYNTHETIC-PRIVATE/path
        unrelated-cli 0.145.0
        codex-cli 0.147.0
        Warning: another version 123.456.789
        """
        XCTAssertEqual(try XCTUnwrap(parse(text)).numericVersion, "0.147.0")
        XCTAssertNil(parse("Warning: runtime 0.146.0"))
        XCTAssertNil(parse("other-cli 0.146.0"))
        XCTAssertNil(parse("warning codex-cli 0.146.0"))
        XCTAssertNil(parse("0.146.0"))
    }

    func testCRLFTabsAndSurroundingWhitespaceMatchPythonPolicy() throws {
        XCTAssertEqual(try XCTUnwrap(parse("\r\n \tcodex-cli\t0.146.0 \r\n")).numericVersion, "0.146.0")
        XCTAssertEqual(try XCTUnwrap(parse("\u{001F}\u{2003}codex-cli 0.146.0\u{3000}")).policy,
                       .meetsMinimum)
        for newline in ["\n", "\r", "\r\n", "\u{000B}", "\u{000C}", "\u{001C}", "\u{001D}",
                        "\u{001E}", "\u{0085}", "\u{2028}", "\u{2029}"] {
            XCTAssertEqual(try XCTUnwrap(parse("warning 99.0.0" + newline + "codex-cli 0.146.0")).policy,
                           .meetsMinimum)
            XCTAssertNil(parse("codex-cli 0.146.0" + newline + "codex-cli 0.147.0"))
        }
    }

    func testDuplicateConflictingAndMalformedCandidateLinesAreAmbiguous() {
        for second in ["codex-cli 0.146.0", "codex-cli 1.0.0", " codex-cli bad ",
                       "codex-client 0.146.0", "codex-cli"] {
            XCTAssertNil(parse("codex-cli 0.146.0\n" + second))
            XCTAssertNil(parse(second + "\ncodex-cli 0.146.0"))
        }
    }

    func testMalformedNoncanonicalNonASCIIAndUnidentifiedVersionsAreUnrecognized() {
        for version in ["01.146.0", "0.0146.0", "0.146.00", "0.146", "0.146.0.1",
                        "0.146.0-", "0.146.0+", "0.146.0-a..b", "0.146.0+bad..build",
                        "0.146.0-01", "0.146.0-alpha.01", "0.146.0+build-rc+more",
                        "0.146.0 suffix", "0.146.0/private", "9999999999.0.0",
                        "0.9999999999.0", "0.146.9999999999", "\u{0660}.146.0",
                        "0.146.0-\u{4E2D}", "0.146.0+build_1", "0.146.0\u{0000}"] {
            XCTAssertNil(parse("codex-cli " + version))
        }
        for text in ["", " \r\n", "codex-cli", "codex-cli0.146.0", "Codex-cli 0.146.0",
                     "codex-cli\u{00A0}0.146.0", "\u{001B}[32mcodex-cli 0.146.0",
                     "codex-cli 0.146.0\u{001B}[0m", "\u{200B}codex-cli 0.146.0",
                     "\u{FEFF}codex-cli 0.146.0"] {
            let result = CLIVersionResult(codexVersion: parse(text))
            XCTAssertNil(result.codexVersion)
            XCTAssertEqual(result.codexPolicy, .unrecognized)
            XCTAssertFalse(result.codexStatus.contains("too old"))
        }
    }

    func testInvalidUTF8AnywhereInOutputIsUnrecognized() {
        for invalid in [Data([0xFF]), Data([0xC0, 0xAF]), Data([0xED, 0xA0, 0x80]), Data([0xE2, 0x82])] {
            XCTAssertNil(CodexVersion.parse(invalid))
            XCTAssertNil(CodexVersion.parse(Data("codex-cli 0.146.0\n".utf8) + invalid))
            XCTAssertNil(CodexVersion.parse(invalid + Data("\ncodex-cli 0.146.0".utf8)))
        }
    }

    func testWholeOutputBudgetUsesUTF8BytesAndNeverAcceptsTruncatedPrefix() throws {
        XCTAssertEqual(CodexVersion.maxOutputBytes, 8192)
        let line = Data("codex-cli 0.146.0\n".utf8)
        let boundary = line + Data(repeating: 120, count: 8192 - line.count)
        XCTAssertEqual(try XCTUnwrap(CodexVersion.parse(boundary)).policy, .meetsMinimum)
        XCTAssertNil(CodexVersion.parse(boundary + Data([120])))
        XCTAssertNil(parse("codex-cli 0.146.0\n" + String(repeating: "\u{4E2D}", count: 2730)))
        let candidate = "codex-cli 0.146.0+"
        let boundedBuild = candidate + String(repeating: "x", count: 8192 - candidate.utf8.count)
        XCTAssertEqual(try XCTUnwrap(parse(boundedBuild)).numericVersion, "0.146.0")
        XCTAssertNil(parse(boundedBuild + "x"))
    }

    func testDiagnosticsOnlyExposeNumbersFixedPolicyAndCompatibilityCaveat() throws {
        let cases: [(String, CodexVersionPolicy, String)] = [
            ("codex-cli 0.147.0+SYNTHETIC-PRIVATE", .meetsMinimum, "0.147.0"),
            ("codex-cli 0.145.0", .tooOld, "0.145.0"),
            ("codex-cli 1.0.0-SYNTHETIC-PRIVATE+build", .prerelease, "1.0.0 (prerelease)")
        ]
        for (output, policy, detected) in cases {
            let result = CLIVersionResult(codexVersion: try XCTUnwrap(parse(
                "warning /SYNTHETIC-PRIVATE/path token=SYNTHETIC-PRIVATE\n" + output)))
            XCTAssertEqual(result.codexPolicy, policy)
            XCTAssertTrue(result.codexStatus.contains("Detected Codex version: \(detected)."))
            XCTAssertTrue(result.codexStatus.contains("Version policy: \(policy.rawValue)."))
            assertSanitizedStatus(result)
        }
        let unknown = CLIVersionResult(codexVersion: nil)
        XCTAssertEqual(unknown.codexPolicy, .unrecognized)
        XCTAssertTrue(unknown.codexStatus.contains("Detected Codex version: unrecognized."))
        assertSanitizedStatus(unknown)
    }

    private func assertSanitizedStatus(_ result: CLIVersionResult,
                                       file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(result.codexStatus.contains("Minimum stable version: 0.146.0."), file: file, line: line)
        XCTAssertTrue(result.codexStatus.contains(
            "Version does not prove authentication, protocol, or model compatibility. No model call."),
                      file: file, line: line)
        for forbidden in ["SYNTHETIC-PRIVATE", "/path", "token=", "warning", "build"] {
            XCTAssertFalse(result.codexStatus.contains(forbidden), file: file, line: line)
            XCTAssertFalse(String(reflecting: result).contains(forbidden), file: file, line: line)
        }
    }

    @MainActor
    func testExplicitVersionRunReturnsSanitizedSyntheticExecutableResults() async throws {
        let line = Data("codex-cli 0.146.0\n".utf8)
        let cases: [(Data, CodexVersionPolicy, String?)] = [
            (line, .meetsMinimum, "0.146.0"),
            (Data("codex-cli 0.147.0+SYNTHETIC-PRIVATE.build\n".utf8), .meetsMinimum, "0.147.0"),
            (Data("codex-cli 0.147.0+01\n".utf8), .meetsMinimum, "0.147.0"),
            (Data("codex-cli 1.0.0\n".utf8), .meetsMinimum, "1.0.0"),
            (Data("codex-cli 0.145.999\n".utf8), .tooOld, "0.145.999"),
            (Data("codex-cli 1.0.0-SYNTHETIC-PRIVATE+build\n".utf8), .prerelease, "1.0.0"),
            (Data("warning 99.0.0 /SYNTHETIC-PRIVATE/path\ncodex-cli 0.147.0\n".utf8),
             .meetsMinimum, "0.147.0"),
            (line + Data("codex-cli bad\n".utf8), .unrecognized, nil),
            (Data("codex-cli 0.0146.0\n".utf8), .unrecognized, nil),
            (Data("codex-cli 0.147.0-01\n".utf8), .unrecognized, nil),
            (Data("codex-cli 0.147.0-alpha.01\n".utf8), .unrecognized, nil),
            (line + Data([0xFF]), .unrecognized, nil),
            (line + Data(repeating: 120, count: 8192 - line.count), .meetsMinimum, "0.146.0"),
            (line + Data(repeating: 120, count: 8193 - line.count), .unrecognized, nil),
            (Data("Claude Code 2.0.0\n".utf8), .unrecognized, nil)
        ]
        for (output, policy, numeric) in cases {
            let executable = try fixture(output: output)
            defer { removeFixture(executable) }
            let finished = expectation(description: "synthetic version output sanitized after EOF")
            finished.assertForOverFulfill = true
            let run = CLIVersionRun { result in
                switch result {
                case .success(let version):
                    XCTAssertEqual(version.codexPolicy, policy)
                    XCTAssertEqual(version.codexVersion?.numericVersion, numeric)
                    self.assertSanitizedStatus(version)
                case .failure(let error):
                    XCTFail("Unexpected fixed version diagnostic: \(error.rawValue)")
                }
                finished.fulfill()
            }
            run.start(executable: executable)
            await fulfillment(of: [finished], timeout: 10)
            run.cancel()
        }
    }

    @MainActor
    func testExplicitVersionRunDoesNotUseStderrOrIgnoreNonzeroExit() async throws {
        for (output, exitCode) in [(Data(), 0), (Data("codex-cli 0.146.0\n".utf8), 1)] {
            let executable = try fixture(output: output, exitCode: exitCode)
            defer { removeFixture(executable) }
            let finished = expectation(description: "invalid process result rejected")
            finished.assertForOverFulfill = true
            let run = CLIVersionRun { result in
                switch result {
                case .success: XCTFail("Stderr alone and nonzero exits must not pass the probe.")
                case .failure(let error): XCTAssertEqual(error, .cliFailed)
                }
                finished.fulfill()
            }
            run.start(executable: executable)
            await fulfillment(of: [finished], timeout: 10)
            run.cancel()
        }
    }

    private func fixture(output: Data, exitCode: Int = 0) throws -> URL {
        let directory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
            .appendingPathComponent(".build/codex-version-fixtures", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let executable = directory.appendingPathComponent("synthetic-cli")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        do {
            let octal = output.map { "\\" + String(format: "%03o", Int($0)) }.joined()
            let script = """
            #!/bin/sh
            test "$#" -eq 1 && test "$1" = "--version" || exit 71
            printf 'codex-cli 9.0.0\\nwarning token=SYNTHETIC-PRIVATE /SYNTHETIC-PRIVATE/path\\n' >&2
            printf '\(octal)'
            exit \(exitCode)
            """
            try script.write(to: executable, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
            return executable
        } catch {
            removeFixture(executable)
            throw error
        }
    }

    private func removeFixture(_ executable: URL) {
        do { try FileManager.default.removeItem(at: executable.deletingLastPathComponent()) }
        catch { XCTFail("Synthetic version fixture cleanup failed.") }
    }
}
