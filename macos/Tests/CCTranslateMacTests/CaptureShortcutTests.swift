import XCTest
import AppKit
import Carbon
import Combine
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CaptureShortcutTests: XCTestCase {
    @MainActor
    func testDefaultConstructionAndPresentationDoNotReserveKeysOrStartBusinessIO() throws {
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
        defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
        let shortcut = f.model.captureShortcut
        XCTAssertFalse(shortcut.enabled)
        XCTAssertEqual(shortcut.registration, .off)
        XCTAssertEqual(registrar.registrations, 0)
        f.model.loadPresentation()
        shortcut.restore()
        XCTAssertEqual(registrar.registrations, 0)
        XCTAssertNil(f.preferences.object(forKey: CaptureShortcutModel.preferenceKey))
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testSavedOptInRestoresOnceAndShutdownPreservesNextLaunchChoice() throws {
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
        defer { f.cleanUp() }
        f.preferences.set(true, forKey: CaptureShortcutModel.preferenceKey)
        let shortcut = f.model.captureShortcut
        defer { shortcut.shutdown() }
        XCTAssertEqual(registrar.registrations, 0)
        f.model.loadPresentation()
        f.model.loadPresentation()
        shortcut.restore()
        shortcut.choose(true)
        XCTAssertTrue(shortcut.enabled)
        XCTAssertEqual(shortcut.registration, .registered)
        XCTAssertEqual(registrar.registrations, 1)
        var captures = 0
        shortcut.canCapture = { true }
        shortcut.onCapture = { captures += 1 }
        let lease = try XCTUnwrap(registrar.leases.first)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(captures, 1)
        shortcut.shutdown()
        shortcut.shutdown()
        shortcut.restore()
        shortcut.choose(false)
        XCTAssertEqual(lease.releases, 1)
        XCTAssertEqual(registrar.registrations, 1)
        XCTAssertTrue(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
        let nextRegistrar = PasteTestRegistrar()
        let next = CaptureShortcutModel(preferences: f.preferences, registrar: nextRegistrar)
        defer { next.shutdown() }
        next.restore()
        XCTAssertEqual(nextRegistrar.registrations, 1)
        XCTAssertEqual(next.registration, .registered)
        next.choose(false)
        XCTAssertFalse(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testCompletePressReleasePairRunsOnceAndSuppressesAutorepeat() throws {
        let registrar = PasteTestRegistrar()
        let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
        defer { shortcut.shutdown() }
        var captures = 0
        shortcut.onCapture = { captures += 1 }
        shortcut.choose(true)
        let lease = try XCTUnwrap(registrar.leases.first)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(captures, 0, "An unwired owner cannot start capture.")
        shortcut.canCapture = { true }
        lease.fire(.released)
        lease.fire(.pressed)
        lease.fire(.pressed)
        XCTAssertEqual(captures, 0)
        lease.fire(.released)
        lease.fire(.released)
        XCTAssertEqual(captures, 1)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(captures, 2)
    }

    @MainActor
    func testBusyAtEitherEdgeAndCancelledPendingPairCannotStartCapture() throws {
        let registrar = PasteTestRegistrar()
        let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
        defer { shortcut.shutdown() }
        var available = false
        var captures = 0
        shortcut.canCapture = { available }
        shortcut.onCapture = { captures += 1 }
        shortcut.choose(true)
        let lease = try XCTUnwrap(registrar.leases.first)
        lease.fire(.pressed)
        available = true
        lease.fire(.released)
        XCTAssertEqual(captures, 0)
        lease.fire(.pressed)
        available = false
        lease.fire(.released)
        XCTAssertEqual(captures, 0)
        available = true
        lease.fire(.pressed)
        shortcut.cancelPendingTrigger()
        lease.fire(.released)
        XCTAssertEqual(captures, 0)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(captures, 1)
    }

    @MainActor
    func testDisableReenableAndShutdownRejectRetiredHandlersAndUnpairedRelease() throws {
        let registrar = PasteTestRegistrar()
        let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
        defer { shortcut.shutdown() }
        var captures = 0
        shortcut.canCapture = { true }
        shortcut.onCapture = { captures += 1 }
        shortcut.choose(true)
        let old = try XCTUnwrap(registrar.leases.first)
        old.fire(.pressed)
        shortcut.choose(false)
        shortcut.choose(true)
        let current = try XCTUnwrap(registrar.leases.last)
        XCTAssertFalse(old === current)
        old.fire(.pressed)
        old.fire(.released)
        current.fire(.released)
        XCTAssertEqual(captures, 0)
        current.fire(.pressed)
        current.fire(.released)
        XCTAssertEqual(captures, 1)
        current.fire(.pressed)
        shortcut.shutdown()
        current.fire(.released)
        current.fire(.pressed)
        current.fire(.released)
        XCTAssertEqual(captures, 1)
        XCTAssertEqual(old.releases, 1)
        XCTAssertEqual(current.releases, 1)
        XCTAssertFalse(shortcut.canRetry)
    }

    @MainActor
    func testConflictAndUnavailableRequireExplicitRetryWithoutFallbackOrCapture() throws {
        for error in [NativeShortcutRegistrationError.conflict, .unavailable(-50)] {
            let registrar = PasteTestRegistrar()
            registrar.failure = error
            let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
            defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
            let shortcut = f.model.captureShortcut
            var captures = 0
            shortcut.canCapture = { true }
            shortcut.onCapture = { captures += 1 }
            shortcut.choose(true)
            XCTAssertEqual(shortcut.registration, .failed(error))
            XCTAssertTrue(shortcut.enabled)
            XCTAssertTrue(shortcut.canRetry)
            XCTAssertTrue(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
            f.model.loadPresentation()
            shortcut.restore()
            XCTAssertEqual(registrar.registrations, 1)
            registrar.failure = nil
            shortcut.retry()
            XCTAssertEqual(registrar.registrations, 2)
            XCTAssertEqual(shortcut.registration, .registered)
            XCTAssertEqual(captures, 0)
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.runtimeRequests, 0)
            XCTAssertEqual(f.locatorRequests, 0)
        }
    }

    @MainActor
    func testReleaseFailureRetainsInertLeaseAndRetryHonorsCurrentIntent() throws {
        let registrar = PasteTestRegistrar()
        let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
        defer { shortcut.shutdown() }
        var captures = 0
        shortcut.canCapture = { true }
        shortcut.onCapture = { captures += 1 }
        shortcut.choose(true)
        let old = try XCTUnwrap(registrar.leases.first)
        old.releaseError = .releaseFailed(-50)
        old.fire(.pressed)
        shortcut.choose(false)
        XCTAssertEqual(shortcut.registration, .failed(.releaseFailed(-50)))
        XCTAssertTrue(shortcut.canRetry)
        old.fire(.released)
        old.fire(.pressed)
        old.fire(.released)
        XCTAssertEqual(captures, 0)
        old.releaseError = nil
        shortcut.retry()
        XCTAssertEqual(shortcut.registration, .off)
        XCTAssertEqual(old.releases, 2)
        XCTAssertEqual(registrar.registrations, 1)
        shortcut.choose(true)
        let current = try XCTUnwrap(registrar.leases.last)
        old.fire(.pressed)
        old.fire(.released)
        current.fire(.pressed)
        current.fire(.released)
        XCTAssertEqual(captures, 1)
        current.releaseError = .releaseFailed(-51)
        shortcut.choose(false)
        shortcut.choose(true)
        XCTAssertEqual(registrar.registrations, 2, "Do not register over an unreleased lease.")
        XCTAssertEqual(shortcut.registration, .failed(.releaseFailed(-51)))
        current.releaseError = nil
        shortcut.retry()
        XCTAssertEqual(registrar.registrations, 3)
        XCTAssertEqual(shortcut.registration, .registered)
    }

    @MainActor
    func testRegistrationCleanupFailureWithoutLeaseCannotBlindlyReregister() {
        let registrar = PasteTestRegistrar()
        registrar.failure = .releaseFailed(-50)
        let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
        defer { shortcut.shutdown() }
        shortcut.choose(true)
        XCTAssertEqual(shortcut.registration, .failed(.releaseFailed(-50)))
        XCTAssertFalse(shortcut.canRetry)
        registrar.failure = nil
        shortcut.retry()
        shortcut.choose(false)
        shortcut.choose(true)
        XCTAssertEqual(registrar.registrations, 1)
        XCTAssertEqual(shortcut.registration, .failed(.releaseFailed(-50)))
    }

    @MainActor
    func testReentrantDisableOrShutdownDuringRegistrationReleasesObsoleteLease() throws {
        for shutdown in [false, true] {
            let registrar = PasteTestRegistrar()
            let shortcut = CaptureShortcutModel(persistsPreferences: false, registrar: registrar)
            defer { shortcut.shutdown() }
            var captures = 0
            shortcut.canCapture = { true }
            shortcut.onCapture = { captures += 1 }
            registrar.onRegister = { shutdown ? shortcut.shutdown() : shortcut.choose(false) }
            shortcut.choose(true)
            registrar.onRegister = nil
            let lease = try XCTUnwrap(registrar.leases.first)
            XCTAssertEqual(lease.releases, 1)
            XCTAssertEqual(shortcut.registration, .off)
            XCTAssertEqual(shortcut.isShutDown, shutdown)
            lease.fire(.pressed)
            lease.fire(.released)
            XCTAssertEqual(captures, 0)
        }
    }

    @MainActor
    func testPresentationObservationAndHelperWindowLifecycleLeaveLocalShortcutIndependent() throws {
        let registrar = PasteTestRegistrar()
        let f = try ProductTestHarness(captureRegistrar: registrar)
        defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
        let helper = try f.ready()
        f.model.reuseHistory(.init(id: "saved", input: "Original", output: "Preserved result"))
        let shortcut = f.model.captureShortcut
        var changes = 0
        let observation = f.model.objectWillChange.sink { changes += 1 }
        defer { observation.cancel() }
        let saves = helper.configurationSaves.count
        shortcut.choose(true)
        XCTAssertGreaterThan(changes, 0)
        XCTAssertEqual(f.model.output, "Preserved result")
        XCTAssertEqual(helper.configurationSaves.count, saves)
        XCTAssertEqual(f.helpers.count, 1)
        f.model.closePanel()
        f.model.stopMonitor()
        XCTAssertEqual(shortcut.registration, .registered)
        XCTAssertFalse(shortcut.isShutDown)
        XCTAssertEqual(registrar.registrations, 1)
        XCTAssertEqual(registrar.leases.first?.releases, 0)
    }

    @MainActor
    func testPersistenceOptOutAndMalformedSavedValueDoNotRegisterOrRewritePreferences() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set(true, forKey: CaptureShortcutModel.preferenceKey)
        let registrar = PasteTestRegistrar()
        let ephemeral = CaptureShortcutModel(preferences: f.preferences, persistsPreferences: false, registrar: registrar)
        defer { ephemeral.shutdown() }
        ephemeral.restore()
        XCTAssertFalse(ephemeral.enabled)
        XCTAssertEqual(registrar.registrations, 0)
        ephemeral.choose(true)
        ephemeral.choose(false)
        XCTAssertTrue(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
        f.preferences.set("not a Boolean", forKey: CaptureShortcutModel.preferenceKey)
        let malformed = CaptureShortcutModel(preferences: f.preferences, registrar: registrar)
        defer { malformed.shutdown() }
        malformed.restore()
        XCTAssertFalse(malformed.enabled)
        XCTAssertEqual(malformed.registration, .off)
        XCTAssertEqual(registrar.registrations, 1)
        XCTAssertEqual(f.preferences.string(forKey: CaptureShortcutModel.preferenceKey), "not a Boolean")
    }
}

final class NativeShortcutRegistrationTests: XCTestCase {
    @MainActor
    func testScreenshotAndPlainPasteUseDistinctIdentitiesAndPreserveExistingBinding() {
        XCTAssertEqual(NativeShortcutBinding.screenshot.keyCode, UInt32(kVK_ANSI_X))
        XCTAssertEqual(NativeShortcutBinding.screenshot.modifiers, UInt32(cmdKey | optionKey | shiftKey))
        XCTAssertEqual(NativeShortcutBinding.plainPaste.keyCode, CarbonPlainPasteShortcut.keyCode)
        XCTAssertEqual(NativeShortcutBinding.plainPaste.signature, CarbonPlainPasteShortcut.signature)
        XCTAssertEqual(NativeShortcutBinding.plainPaste.modifiers, CarbonPlainPasteShortcut.modifiers)
        XCTAssertEqual(CarbonPlainPasteShortcut.options, UInt32(kEventHotKeyExclusive))
        XCTAssertEqual(CarbonNativeShortcut.options, CarbonPlainPasteShortcut.options)
        XCTAssertNotEqual(NativeShortcutBinding.screenshot.signature, NativeShortcutBinding.plainPaste.signature)
        for binding in [NativeShortcutBinding.screenshot, .plainPaste] {
            XCTAssertTrue(binding.matches(binding.identity))
            XCTAssertFalse(binding.matches(EventHotKeyID(signature: binding.signature, id: 2)))
        }
        XCTAssertFalse(NativeShortcutBinding.screenshot.matches(NativeShortcutBinding.plainPaste.identity))
        XCTAssertFalse(NativeShortcutBinding.plainPaste.matches(NativeShortcutBinding.screenshot.identity))
    }

    @MainActor
    func testRealCarbonLeasesRouteOnlyTheirOwnProcessLocalEventsAndReleaseIndependently() throws {
        _ = NSApplication.shared
        let first = NativeShortcutBinding(signature: 0x43535431, keyCode: UInt32(kVK_F19),
                                          modifiers: UInt32(cmdKey | optionKey | shiftKey | controlKey))
        let second = NativeShortcutBinding(signature: 0x43535432, keyCode: UInt32(kVK_F18),
                                           modifiers: first.modifiers)
        var firstEvents: [NativeShortcutKeyEvent] = []
        var secondEvents: [NativeShortcutKeyEvent] = []
        let firstLease = try CarbonNativeShortcut(binding: first).register { firstEvents.append($0) }.get()
        defer { XCTAssertNoThrow(try firstLease.release().get()) }
        let secondLease = try CarbonNativeShortcut(binding: second).register { secondEvents.append($0) }.get()
        defer { XCTAssertNoThrow(try secondLease.release().get()) }
        // Target this process's Carbon event chain, not the global keyboard or another application.
        try send(first.identity, kind: UInt32(kEventHotKeyPressed))
        XCTAssertEqual(firstEvents, [.pressed])
        XCTAssertTrue(secondEvents.isEmpty)
        try send(second.identity, kind: UInt32(kEventHotKeyPressed))
        XCTAssertEqual(firstEvents, [.pressed])
        XCTAssertEqual(secondEvents, [.pressed])
        try send(EventHotKeyID(signature: first.signature, id: 2), kind: UInt32(kEventHotKeyReleased))
        XCTAssertEqual(firstEvents, [.pressed])
        XCTAssertEqual(secondEvents, [.pressed])
        try firstLease.release().get()
        try send(first.identity, kind: UInt32(kEventHotKeyReleased))
        try send(second.identity, kind: UInt32(kEventHotKeyReleased))
        XCTAssertEqual(firstEvents, [.pressed])
        XCTAssertEqual(secondEvents, [.pressed, .released])
        try secondLease.release().get()
        try send(second.identity, kind: UInt32(kEventHotKeyPressed))
        XCTAssertEqual(secondEvents, [.pressed, .released])
    }

    @MainActor
    private func send(_ identity: EventHotKeyID, kind: UInt32) throws {
        var event: EventRef?
        let creation = CreateEvent(nil, OSType(kEventClassKeyboard), kind, GetCurrentEventTime(), 0, &event)
        XCTAssertEqual(creation, noErr)
        let eventRef = try XCTUnwrap(event)
        defer { ReleaseEvent(eventRef) }
        var identity = identity
        XCTAssertEqual(SetEventParameter(eventRef, EventParamName(kEventParamDirectObject),
                                        EventParamType(typeEventHotKeyID), MemoryLayout<EventHotKeyID>.size,
                                        &identity), noErr)
        let status = SendEventToEventTarget(eventRef, GetApplicationEventTarget())
        XCTAssertTrue(status == noErr || status == OSStatus(eventNotHandledErr))
    }
}
