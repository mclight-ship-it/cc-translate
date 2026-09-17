import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CaptureApplicationTests: XCTestCase {
    @MainActor
    func testApplicationConstructionAndQuitCancelLocalCaptureWithoutHelperOrEarlyPermission() async throws {
        _ = NSApplication.shared
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        let application = AppDelegate(model: fixture.model, capture: capture,
                                      diagnostics: ProbeModel(persistsPreferences: false))
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(fixture.helpers.isEmpty)
        capture.start()
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        let task = try XCTUnwrap(probe.captureTask)
        XCTAssertEqual(application.applicationShouldTerminate(NSApp), .terminateNow)
        XCTAssertEqual(capture.phase, .cancelled)
        XCTAssertTrue(capture.frames.isEmpty)
        source.finishCapture()
        await task.value
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(capture.phase, .cancelled)
        XCTAssertNil(capture.preview)
        XCTAssertTrue(fixture.helpers.isEmpty)
    }

    @MainActor
    func testClosingOtherNativeWindowPreservesCaptureAndCompletedTranslation() async throws {
        _ = NSApplication.shared
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        fixture.model.reuseHistory(.init(id: "saved", input: "Original", output: "Preserved result"))
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        let application = AppDelegate(model: fixture.model, capture: capture,
                                      diagnostics: ProbeModel(persistsPreferences: false))
        try await CaptureProductFixture.recognize(capture, source: source)
        let preview = capture.preview
        let other = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 600),
                             styleMask: .borderless, backing: .buffered, defer: false)
        other.isReleasedWhenClosed = false
        defer { other.close() }
        application.windowWillClose(Notification(name: NSWindow.willCloseNotification, object: other))
        XCTAssertEqual(capture.phase, .ready)
        XCTAssertTrue(capture.preview === preview)
        XCTAssertEqual(fixture.model.output, "Preserved result")
        XCTAssertEqual(source.requests.count, 1)
    }
}
