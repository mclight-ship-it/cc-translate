import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CaptureShortcutApplicationTests: XCTestCase {
    @MainActor
    func testShortcutUsesExistingLocalCaptureAndIgnoresBusyPhasesWithoutSendingTranslation() async throws {
        _ = NSApplication.shared
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let ocr = CaptureTestOCR(blocked: true)
        let probe = ScreenProbe(source: source, makeOCRJob: { ocr }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        let app = AppDelegate(model: f.model, capture: capture, diagnostics: ProbeModel(persistsPreferences: false))
        defer {
            ocr.gate?.signal()
            source.finishCapture()
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            f.cleanUp()
        }
        let shortcut = f.model.captureShortcut
        f.model.loadPresentation()
        shortcut.choose(true)
        let lease = try XCTUnwrap(registrar.leases.first)
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        lease.fire(.pressed)
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(capture.phase, .idle)
        lease.fire(.released)
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        XCTAssertEqual(capture.phase, .capturing)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
        source.finishCapture()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(capture.phase, .selecting)
        XCTAssertEqual(source.requests.count, 1)
        capture.select(source.layout[0].frame)
        try await CaptureProductFixture.waitFor { capture.phase == .recognizing && ocr.image != nil }
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(capture.phase, .recognizing)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(ocr.cancelCount, 0)
        ocr.gate?.signal()
        try await CaptureProductFixture.waitFor { capture.phase == .ready }
        let preview = try XCTUnwrap(capture.preview)
        shortcut.choose(false)
        XCTAssertEqual(capture.phase, .ready)
        XCTAssertTrue(capture.preview === preview)
        shortcut.choose(true)
        source.automatic = true
        let next = try XCTUnwrap(registrar.leases.last)
        next.fire(.pressed)
        next.fire(.released)
        try await CaptureProductFixture.waitFor { capture.phase == .selecting && source.requests.count == 2 }
        XCTAssertEqual(source.permissionCalls, 2)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertTrue(f.model.output.isEmpty)
    }

    @MainActor
    func testQuitDisarmsHeldShortcutAndReleasesAtFinalTerminationWithoutClearingOptIn() throws {
        _ = NSApplication.shared
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
        defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(source: source, notificationCenter: NotificationCenter()))
        let app = AppDelegate(model: f.model, capture: capture, diagnostics: ProbeModel(persistsPreferences: false))
        let shortcut = f.model.captureShortcut
        f.preferences.set(true, forKey: CaptureShortcutModel.preferenceKey)
        f.model.loadPresentation()
        let lease = try XCTUnwrap(registrar.leases.first)
        lease.fire(.pressed)
        XCTAssertEqual(app.applicationShouldTerminate(NSApp), .terminateNow)
        lease.fire(.released)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertEqual(lease.releases, 0)
        XCTAssertFalse(shortcut.isShutDown)
        app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        XCTAssertTrue(shortcut.isShutDown)
        XCTAssertEqual(lease.releases, 1)
        XCTAssertEqual(shortcut.registration, .off)
        XCTAssertTrue(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
        XCTAssertTrue(f.helpers.isEmpty)
    }
}
