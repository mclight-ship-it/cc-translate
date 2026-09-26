import XCTest
import AppKit
import Carbon
import Combine
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class PasteTestService: PlainPasteServicing {
    private(set) var state = PlainPasteServiceState(enabled: false, busy: false, status: .idle)
    private let subject = CurrentValueSubject<PlainPasteServiceState, Never>(
        .init(enabled: false, busy: false, status: .idle))
    var updates: AnyPublisher<PlainPasteServiceState, Never> { subject.eraseToAnyPublisher() }
    private(set) var enableCalls: [Bool] = []
    private(set) var requests: [[CGKeyCode]] = []
    private(set) var accepted = 0
    private(set) var cancellations = 0
    private(set) var shutdowns = 0
    var holdDrain = true
    var onEnable: (() -> Void)?
    private var stopped = false

    func setEnabled(_ enabled: Bool) {
        enableCalls.append(enabled)
        guard !stopped else { return }
        state.enabled = enabled
        subject.send(state)
        if enabled { onEnable?() }
        if !enabled, state.busy, !holdDrain { finish(.disabled) }
    }
    func requestPaste(releasing: [CGKeyCode]) -> PlainTextPasteAdmission {
        requests.append(releasing)
        if stopped { return .shutDown }
        guard state.enabled else { return .disabled }
        guard !state.busy else { return .busy }
        accepted += 1
        state.busy = true
        state.status = .reading
        subject.send(state)
        return .accepted
    }
    func progress(_ status: PlainTextPasteStatus) {
        state.busy = true
        state.status = status
        subject.send(state)
    }
    func finish(_ reason: PlainTextPasteReason,
                clipboard: PlainTextPasteOutcome.ClipboardEffect = .unchanged,
                events: PlainTextPasteOutcome.EventEffect = .notPosted) {
        state.busy = false
        state.status = .finished(.init(reason: reason, clipboard: clipboard, events: events))
        subject.send(state)
    }
    func cancel() {
        cancellations += 1
        if !holdDrain { finish(.cancelled) }
    }
    func shutdown() {
        shutdowns += 1
        stopped = true
        state.enabled = false
        subject.send(state)
        if state.busy, !holdDrain { finish(.shutDown) }
    }
}

@MainActor
final class PasteTestLease: PlainPasteShortcutLease {
    private let handler: @MainActor (PlainPasteKeyEvent) -> Void
    private(set) var releases = 0
    var releaseError: PlainPasteRegistrationError?
    init(_ handler: @escaping @MainActor (PlainPasteKeyEvent) -> Void) { self.handler = handler }
    func fire(_ event: PlainPasteKeyEvent) { handler(event) }
    func release() -> Result<Void, PlainPasteRegistrationError> {
        releases += 1
        if let releaseError { return .failure(releaseError) }
        return .success(())
    }
}

@MainActor
final class PasteTestRegistrar: PlainPasteShortcutRegistering {
    private(set) var registrations = 0
    private(set) var leases: [PasteTestLease] = []
    var failure: PlainPasteRegistrationError?
    var onRegister: (() -> Void)?
    func register(_ handler: @escaping @MainActor (PlainPasteKeyEvent) -> Void)
        -> Result<any PlainPasteShortcutLease, PlainPasteRegistrationError> {
        registrations += 1
        if let failure { return .failure(failure) }
        let lease = PasteTestLease(handler)
        leases.append(lease)
        onRegister?()
        return .success(lease)
    }
}

@MainActor
final class PasteTestRouting: PlainPasteRouting {
    var foreground: PlainPasteDestination = .externalApplication
    var handlesNativePaste = true
    var onDestination: (() -> Void)?
    var onNativePaste: (() -> Void)?
    private(set) var foregroundReads = 0
    private(set) var nativePastes = 0
    func destination() -> PlainPasteDestination {
        foregroundReads += 1
        let current = foreground
        onDestination?()
        return current
    }
    func pasteInOwnApplication() -> Bool {
        nativePastes += 1
        onNativePaste?()
        return handlesNativePaste
    }
}

@MainActor
final class PasteAppFixture {
    let base: ProductTestHarness
    var service: PasteTestService
    var registrar: PasteTestRegistrar
    var routing: PasteTestRouting
    private(set) var paste: PlainPasteModel
    private(set) var helpers: [ProductTestHelper] = []
    private(set) var runtimeCalls = 0
    private(set) var locatorCalls = 0
    var exposeCLI = false
    var model: ProbeModel { base.model }

    init() throws {
        base = try ProductTestHarness(savedCLI: false)
        let service = PasteTestService()
        let registrar = PasteTestRegistrar()
        let routing = PasteTestRouting()
        self.service = service
        self.registrar = registrar
        self.routing = routing
        paste = PlainPasteModel(service: service, registrar: registrar, routing: routing)
        installModel()
    }

    private func installModel() {
        base.model = ProbeModel(preferences: base.preferences, makeConnection: { [weak self] notice in
            let helper = ProductTestHelper(notice: notice)
            self?.helpers.append(helper)
            return helper
        }, runtimeProvider: { [weak self] in
            guard let self else { throw ProbeError.launchFailed }
            self.runtimeCalls += 1
            return self.base.runtime
        }, locateCandidates: { [weak self] _, _ in
            guard let self else { return [] }
            self.locatorCalls += 1
            return self.exposeCLI ? [CLICandidate(url: self.base.executable, executable: true)] : []
        }, writeClipboard: { _ in
            XCTFail("These tests never copy text or read the real clipboard.")
            return false
        }, homeDirectory: base.root, plainPaste: paste)
    }

    func reopen() {
        model.prepareToQuit()
        helpers.last?.stopped()
        service = PasteTestService()
        registrar = PasteTestRegistrar()
        routing = PasteTestRouting()
        paste = PlainPasteModel(service: service, registrar: registrar, routing: routing)
        installModel()
    }

    static func configuration(_ enabled: Bool? = false) -> [String: JSONValue] {
        var value = ProductTestHarness.configuration(model: "fixture-custom-model")
        if let enabled { value["plain_text_paste_enabled"] = .bool(enabled) }
        return value
    }

    @discardableResult
    func ready(_ enabled: Bool? = false, native: Bool = false) throws -> ProductTestHelper {
        if !model.connected {
            if native { exposeCLI = true; model.openProduct() }
            else { model.reloadPlainPastePreference() }
        }
        let helper = try XCTUnwrap(helpers.last)
        helper.event("ready")
        try finishRead(configuration: Self.configuration(enabled))
        return helper
    }

    func finishRead(configuration: [String: JSONValue]) throws {
        let helper = try XCTUnwrap(helpers.last)
        helper.event("completed", id: try XCTUnwrap(helper.configurationLoads.last),
                     payload: ["config": .object(configuration)])
    }

    @discardableResult
    func finishSave() throws -> ProductTestHelper.Save {
        let helper = try XCTUnwrap(helpers.last)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        try finishRead(configuration: save.config)
        return save
    }

    func cleanUp() {
        registrar.onRegister = nil
        service.onEnable = nil
        routing.onDestination = nil
        routing.onNativePaste = nil
        model.prepareToQuit()
        helpers.last?.stopped()
        if service.state.busy { service.finish(.shutDown) }
        base.cleanUp()
    }
}

final class PlainPasteAppTests: XCTestCase {
    @MainActor
    func testDefaultOffConstructionAndLaunchRestoreDoNotCreateAnyRuntimeOrShortcutActivity() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.model.loadPresentation()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeCalls, 0)
        XCTAssertEqual(fixture.locatorCalls, 0)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertTrue(fixture.service.enableCalls.isEmpty)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertEqual(fixture.routing.foregroundReads, 0)
        XCTAssertEqual(fixture.routing.nativePastes, 0)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertEqual(fixture.paste.registration, .off)
    }

    @MainActor
    func testOptInHintBootstrapsConfigOnlyOnceButNeverAuthorizesBeforeReadback() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.base.preferences.set(true, forKey: "plainPasteOptInHint")
        fixture.base.preferences.set(fixture.base.executable.path, forKey: "selectedCodexPath")
        fixture.exposeCLI = true
        fixture.model.loadPresentation()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        let helper = try XCTUnwrap(fixture.helpers.first)
        XCTAssertEqual(helper.operations, ["start.configuration"])
        XCTAssertEqual(fixture.locatorCalls, 0)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        helper.event("ready")
        XCTAssertTrue(fixture.service.enableCalls.isEmpty)
        try fixture.finishRead(configuration: PasteAppFixture.configuration(true))
        XCTAssertEqual(fixture.registrar.registrations, 1)
        XCTAssertTrue(fixture.service.state.enabled)
        XCTAssertFalse(fixture.model.nativeTranslation)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertEqual(helper.operations, ["start.configuration", "config.load"])
    }

    @MainActor
    func testStaleMirrorWithDisabledOrMalformedFlagCannotRegisterOrEnableService() throws {
        for enabled in [Optional(false), nil] {
            let fixture = try PasteAppFixture()
            defer { fixture.cleanUp() }
            fixture.base.preferences.set(true, forKey: "plainPasteOptInHint")
            fixture.model.restorePlainPastePreferenceIfNeeded()
            let helper = try fixture.ready(enabled)
            XCTAssertEqual(helper.operations.first, "start.configuration")
            XCTAssertEqual(fixture.registrar.registrations, 0)
            XCTAssertFalse(fixture.service.state.enabled)
            XCTAssertTrue(fixture.service.requests.isEmpty)
            if enabled == false { XCTAssertFalse(fixture.base.preferences.bool(forKey: "plainPasteOptInHint")) }
            else { XCTAssertEqual(fixture.paste.preference.phase, .failed(.missingFlag)) }
        }
    }

    @MainActor
    func testEnableWithoutCLIWritesOnlyPasteFlagAndWaitsForSavePlusReadback() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Unsent translation"
        fixture.model.direction = "to_ja"
        fixture.model.modelProfile = "unsaved-model-choice"
        fixture.model.setPlainPasteEnabled(true)
        let save = try XCTUnwrap(helper.configurationSaves.first)
        var expected = PasteAppFixture.configuration()
        expected["plain_text_paste_enabled"] = .bool(true)
        XCTAssertEqual(save.config, expected, "Do not accidentally commit translation drafts.")
        XCTAssertEqual(fixture.paste.preference.phase, .saving)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertFalse(fixture.base.preferences.bool(forKey: "plainPasteOptInHint"))
        helper.event("completed", id: save.id)
        XCTAssertEqual(fixture.paste.preference.phase, .reading)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        try fixture.finishRead(configuration: save.config)
        XCTAssertTrue(fixture.paste.preference.authorized)
        XCTAssertEqual(fixture.paste.registration, .registered)
        XCTAssertTrue(fixture.base.preferences.bool(forKey: "plainPasteOptInHint"))
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertEqual(fixture.locatorCalls, 0)
    }

    @MainActor
    func testEnabledSavedPreferenceSurvivesReopenButFreshFalseStillWins() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        _ = try fixture.finishSave()
        fixture.reopen()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        XCTAssertEqual(fixture.registrar.registrations, 0)
        _ = try fixture.ready(true)
        XCTAssertEqual(fixture.registrar.registrations, 1)
        fixture.reopen()
        fixture.model.restorePlainPastePreferenceIfNeeded()
        _ = try fixture.ready(false)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertFalse(fixture.base.preferences.bool(forKey: "plainPasteOptInHint"))
    }

    @MainActor
    func testDisableDuringEnableSaveCannotBeUndoneByOlderTrueReadback() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        let first = try XCTUnwrap(helper.configurationSaves.first)
        fixture.model.setPlainPasteEnabled(false)
        helper.event("completed", id: first.id)
        let oldRead = try XCTUnwrap(helper.configurationLoads.last)
        try fixture.finishRead(configuration: first.config)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertEqual(helper.configurationSaves.count, 2)
        let second = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(second.config["plain_text_paste_enabled"], .bool(false))
        helper.event("completed", id: oldRead, payload: ["config": .object(first.config)])
        helper.event("completed", id: first.id)
        XCTAssertEqual(helper.configurationSaves.count, 2)
        _ = try fixture.finishSave()
        XCTAssertEqual(fixture.paste.preference.phase, .confirmed)
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertFalse(fixture.base.preferences.bool(forKey: "plainPasteOptInHint"))
    }

    @MainActor
    func testInitialReadAndUnrelatedSaveCannotClobberLatestQueuedToggle() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.model.reloadPlainPastePreference()
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        fixture.model.setPlainPasteEnabled(true)
        try fixture.finishRead(configuration: PasteAppFixture.configuration(false))
        XCTAssertTrue(fixture.paste.preference.toggleValue)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        _ = try fixture.finishSave()
        fixture.model.saveSettings()
        let unrelated = try XCTUnwrap(helper.configurationSaves.last)
        fixture.model.setPlainPasteEnabled(false)
        XCTAssertFalse(fixture.service.state.enabled)
        helper.event("completed", id: unrelated.id)
        try fixture.finishRead(configuration: unrelated.config)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertEqual(helper.configurationSaves.last?.config["plain_text_paste_enabled"], .bool(false))
        XCTAssertEqual(fixture.registrar.registrations, 1)
        _ = try fixture.finishSave()
        XCTAssertFalse(fixture.paste.preference.authorized)
    }

    @MainActor
    func testFailedSaveHasNoReplayAndExplicitReadCanReconcileUnknownWrite() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("failed", id: save.id, payload: ["code": .string("config_io_failed")])
        XCTAssertEqual(fixture.paste.preference.phase, .failed(.operation("config_io_failed")))
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        fixture.model.reloadPlainPastePreference()
        try fixture.finishRead(configuration: save.config)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(fixture.registrar.registrations, 1)
        XCTAssertTrue(fixture.paste.preference.authorized)
    }

    @MainActor
    func testMismatchedOrFailedReadbackNeverEnablesAndExplicitRetryIsRequired() throws {
        for failed in [false, true] {
            let fixture = try PasteAppFixture()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.setPlainPasteEnabled(true)
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            if failed {
                helper.event("failed", id: try XCTUnwrap(helper.configurationLoads.last),
                             payload: ["code": .string("config_io_failed")])
            } else { try fixture.finishRead(configuration: PasteAppFixture.configuration(false)) }
            XCTAssertEqual(fixture.registrar.registrations, 0)
            XCTAssertTrue(fixture.paste.preference.toggleValue)
            XCTAssertEqual(helper.configurationSaves.count, 1)
            fixture.model.reloadPlainPastePreference()
            try fixture.finishRead(configuration: PasteAppFixture.configuration(false))
            XCTAssertEqual(helper.configurationSaves.count, 1)
            fixture.model.setPlainPasteEnabled(true)
            _ = try fixture.finishSave()
            XCTAssertEqual(helper.configurationSaves.count, 2)
            XCTAssertEqual(fixture.registrar.registrations, 1)
        }
    }

    @MainActor
    func testRegistrationConflictAndUnavailableRequireExplicitRetryWithoutFallback() throws {
        for failure in [PlainPasteRegistrationError.conflict, .unavailable(-50)] {
            let fixture = try PasteAppFixture()
            defer { fixture.cleanUp() }
            fixture.registrar.failure = failure
            _ = try fixture.ready(true)
            XCTAssertEqual(fixture.paste.registration, .failed(failure))
            XCTAssertFalse(fixture.service.state.enabled)
            fixture.model.loadSettings()
            try fixture.finishRead(configuration: PasteAppFixture.configuration(true))
            XCTAssertEqual(fixture.registrar.registrations, 1, "Ordinary reads must not retry a conflict.")
            fixture.registrar.failure = nil
            fixture.paste.retryRegistration()
            XCTAssertEqual(fixture.registrar.registrations, 2)
            XCTAssertEqual(fixture.paste.registration, .registered)
            XCTAssertTrue(fixture.service.requests.isEmpty)
        }
    }

    @MainActor
    func testExclusiveChordAndHeldKeySuppressionProduceOneRequestPerPressCycle() throws {
        XCTAssertEqual(CarbonPlainPasteShortcut.options, UInt32(kEventHotKeyExclusive))
        XCTAssertEqual(CarbonPlainPasteShortcut.keyCode, UInt32(kVK_ANSI_V))
        XCTAssertEqual(CarbonPlainPasteShortcut.modifiers, UInt32(cmdKey | optionKey | shiftKey))
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        lease.fire(.pressed)
        fixture.service.finish(.eventsSubmitted, clipboard: .plainTextWritten, events: .submittedUnconfirmed)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 1)
        XCTAssertEqual(Set(try XCTUnwrap(fixture.service.requests.first)), Set(CarbonPlainPasteShortcut.releasingKeys))
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 2)
        XCTAssertEqual(fixture.service.accepted, 2)
    }

    @MainActor
    func testBusyAdmissionNeverQueuesAnotherPasteAndCancellationDrainsVisibly() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastAdmission, .busy)
        XCTAssertEqual(fixture.service.accepted, 1)
        fixture.paste.cancelAction()
        XCTAssertTrue(fixture.paste.stoppingAction)
        XCTAssertTrue(fixture.paste.serviceState.busy)
        fixture.service.finish(.cancelled, clipboard: .mayHaveChanged)
        XCTAssertFalse(fixture.paste.serviceState.busy)
        XCTAssertFalse(fixture.paste.stoppingAction)
        XCTAssertEqual(fixture.service.accepted, 1)
        XCTAssertEqual(fixture.paste.serviceState.status,
                       .finished(.init(reason: .cancelled, clipboard: .mayHaveChanged, events: .notPosted)))
    }

    @MainActor
    func testReenableDuringDrainStillRequiresANewPressAfterTheWorkerFinishes() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        try XCTUnwrap(fixture.registrar.leases.last).fire(.pressed)
        fixture.model.setPlainPasteEnabled(false)
        _ = try fixture.finishSave()
        fixture.model.setPlainPasteEnabled(true)
        _ = try fixture.finishSave()
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastAdmission, .busy)
        XCTAssertTrue(fixture.paste.stoppingAction)
        fixture.service.finish(.disabled, clipboard: .mayHaveChanged)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.accepted, 1)
        XCTAssertEqual(fixture.service.requests.count, 2)
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.accepted, 2)
    }

    @MainActor
    func testDisabledAndShutdownAdmissionsAreKeptWithoutRetryingOrInventingResults() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        fixture.service.setEnabled(false)
        lease.fire(.pressed)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastAdmission, .disabled)
        XCTAssertEqual(fixture.service.requests.count, 1)
        fixture.service.shutdown()
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastAdmission, .shutDown)
        XCTAssertEqual(fixture.service.accepted, 0)
        XCTAssertEqual(fixture.paste.serviceState.status, .idle)
    }

    @MainActor
    func testDisableImmediatelyReleasesShortcutStopsServiceAndRejectsLateCallbacks() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let old = try XCTUnwrap(fixture.registrar.leases.last)
        old.fire(.pressed)
        fixture.model.setPlainPasteEnabled(false)
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertGreaterThan(old.releases, 0)
        XCTAssertTrue(fixture.paste.stoppingAction)
        XCTAssertFalse(fixture.base.preferences.bool(forKey: "plainPasteOptInHint"))
        old.fire(.released)
        old.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 1)
        fixture.service.finish(.disabled, clipboard: .plainTextWritten)
        _ = try fixture.finishSave()
        fixture.model.setPlainPasteEnabled(true)
        _ = try fixture.finishSave()
        old.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 1, "A retired registration must not invoke the replacement.")
    }

    @MainActor
    func testDisableReenteredDuringRegistrationCannotEnableReturnedObsoleteLease() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.registrar.onRegister = { [weak fixture] in fixture?.model.setPlainPasteEnabled(false) }
        _ = try fixture.ready(true)
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertGreaterThan(try XCTUnwrap(fixture.registrar.leases.first).releases, 0)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        _ = try fixture.finishSave()
        XCTAssertEqual(fixture.paste.registration, .off)
    }

    @MainActor
    func testReleaseFailureIsVisibleAndDoesNotPermitPasteWhileDisabled() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.releaseError = .releaseFailed(-50)
        fixture.model.setPlainPasteEnabled(false)
        XCTAssertEqual(fixture.paste.registration, .failed(.releaseFailed(-50)))
        lease.fire(.pressed)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertFalse(fixture.service.state.enabled)
        lease.releaseError = nil
        fixture.paste.retryRegistration()
        XCTAssertEqual(fixture.paste.registration, .off)
        XCTAssertEqual(fixture.registrar.registrations, 1)
    }

    @MainActor
    func testHelperLossStopsShortcutAndRetiredResponsesCannotRestoreAuthority() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(true)
        let oldRead = try XCTUnwrap(helper.configurationLoads.last)
        helper.failure(.launchFailed)
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertFalse(fixture.paste.preference.authorized)
        helper.event("completed", id: oldRead, payload: ["config": .object(PasteAppFixture.configuration(true))])
        XCTAssertEqual(fixture.registrar.registrations, 1)
        fixture.model.reloadPlainPastePreference()
        helper.stopped()
        let next = try XCTUnwrap(fixture.helpers.last)
        XCTAssertFalse(next === helper)
        XCTAssertEqual(next.operations, ["start.configuration"])
        next.event("ready")
        try fixture.finishRead(configuration: PasteAppFixture.configuration(false))
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertEqual(fixture.locatorCalls, 0)
    }

    @MainActor
    func testShutdownIsPermanentAndIncludesInFlightPasteInTerminationDrain() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        fixture.model.prepareToQuit()
        helper.stopped()
        XCTAssertTrue(fixture.model.hasProcesses, "The paste worker must settle before termination replies.")
        fixture.model.setPlainPasteEnabled(true)
        fixture.paste.retryRegistration()
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 1)
        XCTAssertEqual(fixture.service.shutdowns, 1)
        fixture.service.finish(.shutDown, clipboard: .cleared, events: .mayHavePosted)
        XCTAssertFalse(fixture.model.hasProcesses)
        XCTAssertEqual(fixture.paste.serviceState.status,
                       .finished(.init(reason: .shutDown, clipboard: .cleared, events: .mayHavePosted)))
    }

    @MainActor
    func testPlainPreferenceAndCancelDoNotCancelAnActiveTranslationOrChangeItsDraft() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(true, native: true)
        fixture.model.loadSettings()
        var configuration = PasteAppFixture.configuration(true)
        configuration["history_enabled"] = .bool(false)
        try fixture.finishRead(configuration: configuration)
        fixture.model.input = "A synthetic translation remains active."
        fixture.model.translate(useCache: false)
        let translation = try XCTUnwrap(helper.translations.last)
        let phase = fixture.model.productPhase
        let operations = helper.operations
        try XCTUnwrap(fixture.registrar.leases.last).fire(.pressed)
        fixture.paste.cancelAction()
        fixture.model.setPlainPasteEnabled(false)
        XCTAssertTrue(fixture.model.active)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.id, translation.id)
        XCTAssertEqual(Array(helper.operations.prefix(operations.count)), operations)
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
        XCTAssertEqual(fixture.model.input, translation.text)
        XCTAssertEqual(helper.configurationSaves.last?.config["history_enabled"], .bool(false))
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        XCTAssertTrue(fixture.model.active)
        XCTAssertEqual(fixture.model.productPhase, phase)
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
    }

    @MainActor
    func testNewestToggleAfterOlderSaveFailureIsWrittenOnceIncludingABAChoices() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        let old = try XCTUnwrap(helper.configurationSaves.last)
        fixture.model.setPlainPasteEnabled(false)
        fixture.model.setPlainPasteEnabled(true)
        helper.event("failed", id: old.id, payload: ["code": .string("config_io_failed")])
        XCTAssertTrue(fixture.paste.preference.toggleValue)
        XCTAssertEqual(helper.configurationSaves.count, 2)
        XCTAssertNotEqual(helper.configurationSaves.last?.id, old.id)
        XCTAssertEqual(fixture.registrar.registrations, 0)
        _ = try fixture.finishSave()
        XCTAssertEqual(fixture.registrar.registrations, 1)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testUnrelatedReadAfterSaveFailureKeepsRecoverableFailureInsteadOfEndlessSpinner() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.setPlainPasteEnabled(true)
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        fixture.model.loadSettings()
        try fixture.finishRead(configuration: PasteAppFixture.configuration(true))
        XCTAssertEqual(fixture.paste.preference.phase, .failed(.operation("config_io_failed")))
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertTrue(fixture.paste.preference.toggleValue)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }

    @MainActor
    func testExplicitChoiceWhileOldHelperStopsSurvivesConfigOnlyReconnect() throws {
        for fails in [false, true] {
            let fixture = try PasteAppFixture()
            defer { fixture.cleanUp() }
            let old = try fixture.ready()
            if fails { old.failure(.launchFailed) }
            else { fixture.model.stopHelper() }
            fixture.model.setPlainPasteEnabled(true)
            old.stopped()
            let helper = try XCTUnwrap(fixture.helpers.last)
            XCTAssertFalse(helper === old)
            XCTAssertEqual(helper.operations, ["start.configuration"])
            helper.event("ready")
            try fixture.finishRead(configuration: PasteAppFixture.configuration(false))
            XCTAssertTrue(fixture.paste.preference.toggleValue)
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertEqual(fixture.registrar.registrations, 0)
            _ = try fixture.finishSave()
            XCTAssertEqual(fixture.registrar.registrations, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testDisableReenteredFromServiceEnableCannotLeaveServiceOn() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.service.onEnable = { [weak fixture] in fixture?.model.setPlainPasteEnabled(false) }
        _ = try fixture.ready(true)
        XCTAssertFalse(fixture.service.state.enabled)
        XCTAssertEqual(fixture.paste.registration, .off)
        XCTAssertFalse(fixture.paste.preference.toggleValue)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testWrongTypeFlagAndNativeCleanupErrorFailClosedWithoutFakeRetry() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.model.reloadPlainPastePreference()
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        var invalid = PasteAppFixture.configuration()
        invalid["plain_text_paste_enabled"] = .string("true")
        try fixture.finishRead(configuration: invalid)
        XCTAssertEqual(fixture.paste.preference.phase, .failed(.missingFlag))
        XCTAssertEqual(fixture.registrar.registrations, 0)
        fixture.registrar.failure = .releaseFailed(-50)
        fixture.model.reloadPlainPastePreference()
        try fixture.finishRead(configuration: PasteAppFixture.configuration(true))
        XCTAssertEqual(fixture.paste.registration, .failed(.releaseFailed(-50)))
        XCTAssertFalse(fixture.paste.canRetryRegistration, "No lease exists that this controller can retry releasing.")
        XCTAssertFalse(fixture.service.state.enabled)
    }

    @MainActor
    func testAllTypedOutcomeReceiptsSurviveWithoutBeingReclassifiedAsSuccessfulInsertion() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let reasons: [PlainTextPasteReason] = [
            .eventsSubmitted, .noText, .unsupportedRepresentation, .unavailableData, .invalidRichText,
            .clipboardChanged, .clipboardTimedOut, .writeFailed, .targetUnavailable, .targetChanged,
            .accessibilityUnavailable, .secureInput, .keysStillPressed, .keyReleaseTimedOut,
            .eventCreationFailed, .eventPostingFailed, .cancelled, .disabled, .shutDown
        ]
        let clipboardEffects: [PlainTextPasteOutcome.ClipboardEffect] = [.unchanged, .mayHaveChanged, .cleared, .plainTextWritten]
        let eventEffects: [PlainTextPasteOutcome.EventEffect] = [.notPosted, .mayHavePosted, .submittedUnconfirmed]
        for reason in reasons {
            for clipboard in clipboardEffects {
                for events in eventEffects {
                    fixture.service.finish(reason, clipboard: clipboard, events: events)
                    XCTAssertEqual(fixture.paste.serviceState.status,
                                   .finished(.init(reason: reason, clipboard: clipboard, events: events)))
                }
            }
        }
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertEqual(fixture.model.permissions, "Not checked.")
    }

    @MainActor
    func testWindowClosingAndPasteDisableDoNotCancelCaptureOrStartRecognition() async throws {
        _ = NSApplication.shared
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.model.reuseHistory(.init(id: "fixture", input: "Original", output: "Preserved"))
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        let diagnostics = ProbeModel(persistsPreferences: false,
                                     plainPaste: PlainPasteModel(service: PasteTestService(), registrar: PasteTestRegistrar()))
        let application = AppDelegate(model: fixture.model, capture: capture, diagnostics: diagnostics)
        XCTAssertTrue(application.settingsContent().model === fixture.model)
        capture.start()
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        let task = try XCTUnwrap(probe.captureTask)
        let other = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 660, height: 650),
                             styleMask: .borderless, backing: .buffered, defer: false)
        other.isReleasedWhenClosed = false
        defer { other.close() }
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        application.windowWillClose(Notification(name: NSWindow.willCloseNotification, object: other))
        XCTAssertTrue(fixture.service.state.busy)
        XCTAssertTrue(fixture.service.state.enabled)
        XCTAssertEqual(fixture.service.cancellations, 0)
        fixture.model.setPlainPasteEnabled(false)
        fixture.paste.cancelAction()
        XCTAssertEqual(probe.phase, .capturing)
        source.finishCapture()
        await task.value
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertNil(probe.ocrTask)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(fixture.model.output, "Preserved")
    }

    @MainActor
    func testApplicationQuitWaitsForOnlyPasteWorkerAndNeverRestartsIt() throws {
        _ = NSApplication.shared
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(source: source, notificationCenter: NotificationCenter()))
        let diagnostics = ProbeModel(persistsPreferences: false,
                                     plainPaste: PlainPasteModel(service: PasteTestService(), registrar: PasteTestRegistrar()))
        let application = AppDelegate(model: fixture.model, capture: capture, diagnostics: diagnostics)
        fixture.service.progress(.verifying)
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(application.applicationShouldTerminate(NSApp), .terminateLater)
        XCTAssertTrue(fixture.model.hasProcesses)
        XCTAssertTrue(fixture.paste.stoppingAction)
        XCTAssertEqual(source.permissionCalls, 0)
        fixture.service.finish(.shutDown, clipboard: .plainTextWritten, events: .mayHavePosted)
        XCTAssertFalse(fixture.model.hasProcesses)
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        XCTAssertEqual(fixture.service.shutdowns, 1)
        XCTAssertEqual(fixture.registrar.registrations, 0)
    }
}
