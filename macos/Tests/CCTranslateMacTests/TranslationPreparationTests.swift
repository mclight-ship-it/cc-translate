import AppKit
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class TranslationPreparationTests: XCTestCase {
    private static let granted = PermissionSnapshot(
        accessibility: .granted, inputMonitoring: .granted,
        screenCapture: .granted, secureInput: false)

    private func prewarms(_ helper: ProductTestHelper) -> [ClientMessage] {
        helper.messages.filter { $0.payload["operation"] == .string("prewarm") }
    }

    func testEnablingConfiguredShortcutPreparesOnceAndFirstCopyRefreshesOnlyAfterThrottle() throws {
        var time = 100.0
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor,
                                      readPermissions: { Self.granted }, latencyClock: { time })
        defer { f.model.stopMonitor(); f.cleanUp() }
        f.model.startMonitor()
        XCTAssertEqual(f.helpers.count, 1)
        XCTAssertEqual(f.helpers[0].operations, ["start.translation"])
        let helper = try f.ready(capabilities: ["prewarm"])
        let first = try XCTUnwrap(prewarms(helper).last)
        XCTAssertEqual(first.payload, ["operation": .string("prewarm"), "app_language": .string("en_US")])
        helper.event("completed", id: first.id, payload: ["warmed": .bool(true)])
        f.model.startMonitor()
        f.model.restoreSelectionMonitorIfNeeded()
        for _ in 0..<10 { monitor.onCopyIntent?() }
        XCTAssertEqual(prewarms(helper).count, 1)
        time += 541
        monitor.onCopyIntent?()
        XCTAssertEqual(prewarms(helper).count, 2)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertNil(f.model.latency.current, "First copy must not start a translation timing sample.")
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    func testFirstCopyReconnectsAnIdleDisconnectedHelperWithoutSubmittingText() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor, readPermissions: { Self.granted })
        defer { f.model.stopMonitor(); f.cleanUp() }
        f.model.startMonitor()
        let old = try f.ready(capabilities: ["prewarm"])
        f.model.closePanel()
        old.stopped()
        XCTAssertFalse(f.model.connected)
        XCTAssertTrue(f.model.selectionShortcutActive)
        monitor.onCopyIntent?()
        XCTAssertEqual(f.helpers.count, 2)
        let next = try f.ready(capabilities: ["prewarm"])
        XCTAssertEqual(prewarms(next).count, 1)
        XCTAssertTrue(next.translations.isEmpty)
        XCTAssertFalse(f.model.active)
        XCTAssertFalse(f.model.preparing)
    }

    func testPermissionRecoveryPreparesOnlyOnAnActiveTransition() throws {
        var time = 100.0
        let monitor = SelectionMonitorFixture()
        monitor.failure = .permissionDenied
        let f = try ProductTestHarness(selectionMonitor: monitor,
                                      readPermissions: { Self.granted }, latencyClock: { time })
        defer { f.model.stopMonitor(); f.cleanUp() }
        f.model.startMonitor()
        XCTAssertTrue(f.helpers.isEmpty)
        monitor.failure = nil
        f.model.restoreSelectionMonitorIfNeeded()
        let helper = try f.ready(capabilities: ["prewarm"])
        helper.event("completed", id: try XCTUnwrap(prewarms(helper).last).id,
                     payload: ["warmed": .bool(true)])
        time += 541
        f.model.restoreSelectionMonitorIfNeeded()
        XCTAssertEqual(prewarms(helper).count, 1, "The recovery timer must not keep idle processes alive.")
        monitor.running = false
        monitor.onStop?("permission temporarily unavailable")
        f.model.restoreSelectionMonitorIfNeeded()
        XCTAssertEqual(prewarms(helper).count, 2)
        XCTAssertEqual(monitor.starts, 2)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    func testSleepWakeNotificationRestoresIntentBeforeOrAfterHelperDrain() throws {
        _ = NSApplication.shared
        for drainFirst in [false, true] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let notifications = NotificationCenter()
            let app = AppDelegate(model: f.model, capture: CaptureModel(),
                                  diagnostics: ProbeModel(persistsPreferences: false),
                                  workspaceNotifications: notifications)
            let old = try f.ready(capabilities: ["prewarm"])
            f.model.prepareTranslation()
            let warm = try XCTUnwrap(prewarms(old).last)
            notifications.post(name: NSWorkspace.willSleepNotification, object: nil)
            notifications.post(name: NSWorkspace.willSleepNotification, object: nil)
            XCTAssertEqual(old.stopCount, 1)
            f.model.prepareTranslation(onlyIfConfigured: true)
            old.event("completed", id: warm.id, payload: ["warmed": .bool(true)])
            XCTAssertEqual(prewarms(old).count, 1)
            if drainFirst { old.stopped() }
            notifications.post(name: NSWorkspace.didWakeNotification, object: nil)
            if !drainFirst {
                XCTAssertEqual(f.helpers.count, 1, "Do not create a second helper while the old one drains.")
                old.stopped()
            }
            XCTAssertEqual(f.helpers.count, 2)
            let next = try f.ready(capabilities: ["prewarm"])
            notifications.post(name: NSWorkspace.didWakeNotification, object: nil)
            XCTAssertEqual(prewarms(next).count, 1)
            XCTAssertTrue(next.translations.isEmpty)
            withExtendedLifetime(app) {}
        }
    }

    func testIdleUnconfiguredDisabledAndDiagnosticMonitoringDoNotPrepare() throws {
        for configured in [false, true] {
            let monitor = SelectionMonitorFixture()
            let f = try ProductTestHarness(savedCLI: configured, selectionMonitor: monitor,
                                          readPermissions: { Self.granted })
            defer { f.model.stopMonitor(); f.cleanUp() }
            monitor.onCopyIntent?()
            f.model.suspendTranslationPreparation()
            f.model.resumeTranslationPreparationAfterWake()
            XCTAssertTrue(f.helpers.isEmpty, "A saved CLI alone is not translation intent.")
            f.model.startMonitor(accessibilityOnly: true)
            monitor.onCopyIntent?()
            monitor.onTranslationGesture?(100)
            XCTAssertTrue(f.helpers.isEmpty, "AX-only diagnostics must not prepare a model.")
            if !configured {
                f.model.startMonitor()
                monitor.onCopyIntent?()
                f.model.suspendTranslationPreparation()
                f.model.resumeTranslationPreparationAfterWake()
                XCTAssertTrue(f.helpers.isEmpty)
                XCTAssertEqual(f.locatorRequests, 0)
            }
        }
    }

    func testCancellationCloseQuitAndFailureDiscardDeferredWakePreparation() throws {
        for action in ["cancel", "close", "quit", "failure"] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready(capabilities: ["prewarm"])
            f.model.prepareTranslation()
            f.model.suspendTranslationPreparation()
            f.model.resumeTranslationPreparationAfterWake()
            switch action {
            case "cancel": f.model.cancel()
            case "close": f.model.closePanel()
            case "quit": f.model.prepareToQuit()
            default: helper.failure(.translationOutcomeUnknown)
            }
            helper.stopped()
            XCTAssertEqual(f.helpers.count, 1, action)
            XCTAssertTrue(helper.translations.isEmpty)
            if action == "quit" || action == "failure" {
                f.model.prepareTranslation(onlyIfConfigured: true)
                XCTAssertEqual(f.helpers.count, 1, "No background retry of failures or shutdown.")
            }
        }
    }

    func testBackgroundPreparationNeverInterruptsForegroundAndWaitsForSettings() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(capabilities: ["prewarm"])
        f.model.input = "An existing foreground translation."
        f.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        let before = helper.messages
        f.model.prepareTranslation(onlyIfConfigured: true)
        XCTAssertEqual(helper.messages.count, before.count)
        XCTAssertEqual(helper.stopCount, 0)
        XCTAssertEqual(helper.translations.count, 1)
        helper.event("completed", id: request.id, payload: [
            "text": .string("Finished foreground result."), "cached": .bool(false),
            "kind": .string("text"), "history": .string("disabled")
        ])
        XCTAssertEqual(prewarms(helper).count, 1)
        XCTAssertEqual(f.model.output, "Finished foreground result.")

        let edited = try ProductTestHarness()
        defer { edited.cleanUp() }
        let other = try edited.ready(capabilities: ["prewarm"])
        edited.model.modelProfile = "gpt-5.4-mini"
        edited.model.prepareTranslation(onlyIfConfigured: true)
        XCTAssertTrue(prewarms(other).isEmpty, "Do not warm an unsaved model choice.")
        XCTAssertTrue(other.translations.isEmpty)
    }

    func testCaptureStartsPreparationBeforePermissionAndCancellationNeverSubmits() async throws {
        for mode in CaptureTranslationMode.allCases {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let capture = CaptureModel(screen: ScreenProbe(
                source: source, makeOCRJob: { CaptureTestOCR() }, notificationCenter: NotificationCenter()))
            defer { capture.cancel() }
            source.onPermission = {
                XCTAssertEqual(f.helpers.count, 1, "Helper startup overlaps even the initial capture setup.")
                XCTAssertTrue(f.helpers[0].translations.isEmpty)
                XCTAssertFalse(f.model.active)
            }
            capture.startTranslation(using: f.model, mode: mode)
            try await CaptureProductFixture.waitFor { capture.phase == .selecting }
            let helper = try f.ready(capabilities: ["prewarm"])
            XCTAssertEqual(prewarms(helper).count, 1)
            XCTAssertNil(capture.preview)
            capture.cancel()
            helper.event("completed", id: try XCTUnwrap(prewarms(helper).last).id,
                         payload: ["warmed": .bool(true)])
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertFalse(helper.messages.contains { $0.payload["operation"] == .string("translate_image") })
            XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
            XCTAssertTrue(capture.frames.isEmpty)
        }
    }
}
