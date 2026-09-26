import XCTest
import AppKit
import SwiftUI
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CaptureApplicationTests: XCTestCase {
    @MainActor
    func testScreenshotMenuTranslatesDirectlyAndDoesNotReopenAClosedResult() async throws {
        _ = NSApplication.shared
        let main = NSApp.mainMenu
        let windows = NSApp.windowsMenu
        let focus = NativeTestWindowFocus()
        let f = try ProductTestHarness()
        let helper = try f.ready()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                                       notificationCenter: NotificationCenter()))
        let app = AppDelegate(model: f.model, capture: capture, diagnostics: ProbeModel(persistsPreferences: false))
        defer {
            for panel in [app.capturePanel, app.resultPanel, app.settingsPanel].compactMap({ $0 }) { focus.close(panel) }
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            NSApp.mainMenu = main
            NSApp.windowsMenu = windows
            f.cleanUp()
        }

        app.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        let item = try XCTUnwrap(app.statusItem?.menu?.items.first { $0.action == Selector("startCapture") })
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertNil(app.capturePanel, "Capturing a region must not open an OCR review window.")
        capture.select(source.layout[0].frame)
        try await CaptureProductFixture.waitFor {
            helper.translations.count == 1 && app.resultPanel?.isVisible == true && app.capturePanel == nil
        }
        XCTAssertEqual(helper.translations[0].text, "Captured local words")
        XCTAssertEqual(helper.translations[0].origin, "ocr")
        XCTAssertFalse(helper.translations[0].useCache)
        XCTAssertNil(capture.preview)
        let result = try XCTUnwrap(app.resultPanel)
        result.performClose(nil)
        capture.objectWillChange.send()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertFalse(result.isVisible, "A queued capture update must not reopen a dismissed result.")
        XCTAssertNil(app.capturePanel)
        XCTAssertEqual(helper.translations.count, 1)
    }

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
