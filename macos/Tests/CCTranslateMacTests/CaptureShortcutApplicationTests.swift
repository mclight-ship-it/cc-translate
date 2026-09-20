import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CaptureShortcutApplicationTests: XCTestCase {
    @MainActor
    func testShortcutTranslatesAfterSelectionAndIgnoresRepeatedPressesWhileCapturing() async throws {
        _ = NSApplication.shared
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(captureRegistrar: registrar)
        let helper = try f.ready()
        let runtimeRequests = f.runtimeRequests
        let locatorRequests = f.locatorRequests
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
        XCTAssertTrue(helper.translations.isEmpty)
        ocr.gate?.signal()
        try await CaptureProductFixture.waitFor { capture.submitted && helper.translations.count == 1 }
        XCTAssertNil(capture.preview)
        XCTAssertTrue(capture.frames.isEmpty)
        shortcut.choose(false)
        XCTAssertTrue(capture.submitted)
        XCTAssertEqual(helper.translations[0].text, "Captured local words")
        XCTAssertEqual(helper.translations[0].origin, "ocr")
        XCTAssertFalse(helper.translations[0].useCache)
        helper.event("completed", id: helper.translations[0].id, payload: ["text": .string("Translated screenshot")])
        shortcut.choose(true)
        source.automatic = true
        let next = try XCTUnwrap(registrar.leases.last)
        next.fire(.pressed)
        next.fire(.released)
        try await CaptureProductFixture.waitFor { capture.phase == .selecting && source.requests.count == 2 }
        XCTAssertEqual(source.permissionCalls, 2)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(f.runtimeRequests, runtimeRequests)
        XCTAssertEqual(f.locatorRequests, locatorRequests)
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
