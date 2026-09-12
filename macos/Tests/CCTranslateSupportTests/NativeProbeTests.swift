import XCTest
import AppKit
import CoreText
@testable import CCTranslateSupport

final class NativeProbeTests: XCTestCase {
    func testSelectionThreeStatesAndConservativeGuards() {
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: true, sameTarget: true, selectedText: "selected"
        ), .present("selected"))
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: true, sameTarget: true, selectedText: ""
        ), .absent)
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: true, sameTarget: true, selectedText: nil
        ), .unknown(.unsupported))
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: true, trusted: true, sameTarget: true, selectedText: "secret"
        ), .unknown(.secureInput))
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: false, sameTarget: true, selectedText: ""
        ), .unknown(.accessibility))
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: true, sameTarget: false, selectedText: ""
        ), .unknown(.focusChanged))
        XCTAssertEqual(SelectionResult.evaluate(
            secureInput: false, trusted: true, sameTarget: true, selectedText: String(repeating: "x", count: 8193)
        ), .unknown(.tooLarge))
    }

    func testDoubleCopyPairsDoNotTripleTrigger() {
        var state = DoubleCopyState()
        XCTAssertFalse(state.observe(time: 1, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertTrue(state.observe(time: 1.3, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 1.4, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertTrue(state.observe(time: 1.7, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
    }

    func testDoubleCopyRejectsRepeatFocusChangeAndSecureInput() {
        var state = DoubleCopyState()
        XCTAssertFalse(state.observe(time: 1, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 1.2, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 1.3, pid: 43, isCopy: true, isRepeat: true, secureInput: false))
        XCTAssertFalse(state.observe(time: 1.4, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 1.5, pid: 43, isCopy: true, isRepeat: false, secureInput: true))
        XCTAssertFalse(state.observe(time: 1.6, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 2.2, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 2.3, pid: 43, isCopy: false, isRepeat: false, secureInput: false))
        XCTAssertFalse(state.observe(time: 2.4, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        state.reset()
        XCTAssertFalse(state.observe(time: 2.5, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
    }

    func testVisionRecognizesSyntheticInMemoryImage() throws {
        let width = 1100
        let height = 180
        let context = try XCTUnwrap(CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 64, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)
        ]
        let line = CTLineCreateWithAttributedString(
            NSAttributedString(string: "SYNTHETIC FIXTURE 12345", attributes: attributes) as CFAttributedString
        )
        context.textPosition = CGPoint(x: 24, y: 65)
        CTLineDraw(line, context)
        let result = try LocalOCR.recognize(XCTUnwrap(context.makeImage()))
        XCTAssertFalse(result.supportedLanguages.isEmpty)
        XCTAssertTrue(result.text.uppercased().contains("SYNTHETIC"), result.text)
        XCTAssertTrue(result.text.contains("12345"), result.text)
    }

    @MainActor
    func testScreenClearDoesNotRequestPermissionOrCapture() {
        let probe = ScreenProbe()
        probe.clear()
        XCTAssertNil(probe.preview)
        XCTAssertEqual(probe.text, "")
        XCTAssertFalse(probe.canConfirm)
        XCTAssertFalse(probe.busy)
    }

    func testFinderPATHIsExplicitAndDoesNotSourceProfiles() {
        XCTAssertEqual(CLILocator.searchPath, [
            NSHomeDirectory() + "/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"
        ].joined(separator: ":"))
        XCTAssertTrue(CLILocator.candidates(name: "not_a_supported_cli").isEmpty)
        let candidates = CLILocator.candidates(name: "codex")
        XCTAssertTrue(candidates.contains { $0.url.path == NSHomeDirectory() + "/.local/bin/codex" })
    }

    @MainActor
    func testExplicitVersionProbeReportsEmptyOutputAndExits() async {
        let finished = expectation(description: "child exited and both pipes closed")
        let run = CLIVersionRun { result in
            switch result {
            case .failure(let error): XCTAssertEqual(error, .cliFailed)
            case .success: XCTFail("An empty response must not look like a successful version probe.")
            }
            finished.fulfill()
        }
        run.start(executable: URL(fileURLWithPath: "/usr/bin/true"))
        await fulfillment(of: [finished], timeout: 10)
        run.cancel()
    }

    @MainActor
    func testVersionProbeDiscardsOutputInsteadOfReturningText() async {
        let finished = expectation(description: "child output discarded")
        let run = CLIVersionRun { result in
            switch result {
            case .success: break
            case .failure(let error): XCTFail("Unexpected fixed diagnostic: \(error.rawValue)")
            }
            finished.fulfill()
        }
        run.start(executable: URL(fileURLWithPath: "/bin/echo"))
        await fulfillment(of: [finished], timeout: 10)
        run.cancel()
    }

    @MainActor
    func testVersionProbeBoundsOutputAndTerminatesItsChild() async {
        let finished = expectation(description: "output limit terminated child")
        let run = CLIVersionRun { result in
            switch result {
            case .failure(let error): XCTAssertEqual(error, .cliOutputLimit)
            case .success: XCTFail("An unbounded writer must not succeed.")
            }
            finished.fulfill()
        }
        run.start(executable: URL(fileURLWithPath: "/usr/bin/yes"))
        await fulfillment(of: [finished], timeout: 10)
        run.cancel()
    }

    @MainActor
    func testVersionProbeCancelledBeforeLaunchNeverStarts() async {
        let finished = expectation(description: "cancelled without launching")
        let run = CLIVersionRun { result in
            switch result {
            case .failure(let error): XCTAssertEqual(error, .cliCancelled)
            case .success: XCTFail("A pre-cancelled probe must not run.")
            }
            finished.fulfill()
        }
        run.cancel()
        run.start(executable: URL(fileURLWithPath: "/usr/bin/yes"))
        await fulfillment(of: [finished], timeout: 5)
    }
}
