import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class SelectionMonitorFixture: PassiveSelectionMonitoring {
    var running = false
    var onSelection: ((SelectionResult) -> Void)?
    var onCopyIntent: (() -> Void)?
    var onTranslationGesture: ((TimeInterval) -> Void)?
    var onStop: ((String) -> Void)?
    var starts = 0
    var stops = 0
    var cancellations = 0
    var fallback = false
    var pending = false
    var failure: ProbeError?
    var copyInterval = DoubleCopyInterval.standard
    func setCopyInterval(_ interval: DoubleCopyInterval) {
        guard copyInterval != interval else { return }
        cancelPendingSelection()
        copyInterval = interval
    }
    func setClipboardFallbackEnabled(_ enabled: Bool) {
        if fallback != enabled { cancelPendingSelection() }
        fallback = enabled
    }
    func start() throws {
        if let failure { throw failure }
        guard !running else { return }
        starts += 1
        running = true
    }
    func stop() {
        stops += 1
        running = false
        cancelPendingSelection()
    }
    func cancelPendingSelection() { cancellations += 1; pending = false }
    func emit(_ result: SelectionResult) {
        guard pending, running else { return }
        pending = false
        onSelection?(result)
    }
}

final class SelectionShortcutModelTests: XCTestCase {
    @MainActor
    func testDefaultProductDoesNotStartMonitoringOrReadBusinessState() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
        defer { f.cleanUp() }
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertFalse(monitor.fallback)
        XCTAssertFalse(f.model.translatePassiveSelections)
        XCTAssertFalse(f.model.monitorEnabled)
        XCTAssertFalse(f.model.monitorRequestedEnabled)
        XCTAssertNil(f.model.permissionSnapshot)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testUnconfiguredExplicitSwitchGatesFallbackWithoutImplicitRegistrationOrHelperSetup() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp(); f.model.stopMonitor() }
        f.model.interfaceLanguage = "en"
        f.model.translatePassiveSelections = true
        XCTAssertTrue(monitor.fallback)
        XCTAssertEqual(monitor.starts, 0)
        f.model.startMonitor()
        XCTAssertTrue(f.model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 1)
        XCTAssertTrue(monitor.fallback)
        XCTAssertTrue(f.model.monitorStatus.contains("newly copied text"))
        XCTAssertTrue(f.model.monitorStatus.contains("clipboard stays unchanged"))
        monitor.pending = true
        f.model.translatePassiveSelections = false
        XCTAssertFalse(monitor.pending)
        XCTAssertFalse(monitor.fallback)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testDiagnosticStartRemainsAXOnlyEvenWithExistingProductOptIn() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp(); f.model.stopMonitor() }
        f.model.translatePassiveSelections = true
        f.model.startMonitor(accessibilityOnly: true)
        XCTAssertFalse(monitor.fallback)
        XCTAssertTrue(f.model.monitorStatus.contains("AX-only"))
        f.model.startMonitor()
        XCTAssertTrue(monitor.fallback)
        XCTAssertEqual(monitor.starts, 1)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testExplicitStopAndFailedStartCannotDeliverQueuedSelection() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        var results: [SelectionResult] = []
        f.model.onSelection = { results.append($0) }
        f.model.translatePassiveSelections = true
        f.model.startMonitor()
        f.model.stopMonitor()
        XCTAssertFalse(f.model.monitorRequestedEnabled)
        XCTAssertFalse(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
        monitor.onSelection?(.present("late"))
        XCTAssertTrue(results.isEmpty)
        monitor.failure = .secureInput
        f.model.startMonitor()
        XCTAssertFalse(f.model.monitorEnabled)
        XCTAssertTrue(f.model.monitorRequestedEnabled, "A denied runtime start must not erase the user's choice.")
        XCTAssertEqual(monitor.starts, 1)
        monitor.onSelection?(.present("still late"))
        XCTAssertTrue(results.isEmpty)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testNewExplicitIntentClearHistoryReuseCloseAndQuitCancelPendingSelection() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp() }
        var results: [SelectionResult] = []
        f.model.onSelection = { results.append($0) }
        f.model.translatePassiveSelections = true
        f.model.startMonitor()
        let operations: [@MainActor () -> Void] = [
            { f.model.cancel() },
            { f.model.input = ""; f.model.translate() },
            { f.model.performResultAction(.summary) },
            { f.model.reuseHistory(.init(id: "saved", input: "input", output: "output")) },
            { f.model.clearTranslation() },
            { f.model.closePanel() },
            { f.model.prepareToQuit() }
        ]
        for action in operations {
            monitor.pending = true
            let before = monitor.cancellations
            action()
            XCTAssertGreaterThan(monitor.cancellations, before)
            XCTAssertFalse(monitor.pending)
            monitor.emit(.present("stale copy"))
        }
        XCTAssertTrue(results.isEmpty)
        XCTAssertFalse(monitor.running)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testQualifiedCopiedSelectionUsesExistingTranslationAndDictionaryPipelineOnly() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
        defer { f.cleanUp(); f.model.stopMonitor() }
        let helper = try f.localReady()
        f.model.onSelection = { f.model.translateSelection($0) }
        f.model.translatePassiveSelections = true
        f.model.startMonitor()
        monitor.pending = true
        monitor.emit(.present("fresh explicit copy"))
        XCTAssertEqual(f.model.input, "fresh explicit copy")
        XCTAssertEqual(f.model.translationOrigin, "selection")
        XCTAssertEqual(helper.dictionaryRequests.last?.request,
                       .lookup(text: "fresh explicit copy", appLanguage: "en_US", origin: "selection",
                               useCache: true, recordHistory: true))
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
        f.model.onSelection = nil
    }

    @MainActor
    func testFailedCorrelationReportsFailureWithoutSubmittingOrReplacingOutput() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
        defer { f.cleanUp() }
        f.model.reuseHistory(.init(id: "visible", input: "original", output: "existing result"))
        for reason in [SelectionResult.Reason.clipboardChanged, .clipboardUnsupported,
                       .clipboardUnavailable, .inputMonitoring] {
            f.model.translateSelection(.unknown(reason))
            XCTAssertEqual(f.model.output, "existing result")
            XCTAssertEqual(f.model.productPhase, .failed)
            XCTAssertTrue(f.model.status.contains("Nothing submitted"))
            XCTAssertFalse(f.model.status.contains("No clipboard fallback"))
        }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testNoSelectionRequestsQuickInputWithoutFailingOrSubmittingExistingResult() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        f.model.reuseHistory(.init(id: "visible", input: "original", output: "existing result"))
        let previousPhase = f.model.productPhase
        let previousOrigin = f.model.translationOrigin
        var requests = 0
        var resultPresentations = 0
        f.model.onQuickInputRequested = { requests += 1 }
        f.model.onTranslationResult = { _ in resultPresentations += 1 }
        for selection in [SelectionResult.absent, .unknown(.copyNotObserved), .present(" \n\t")] {
            f.model.translateSelection(selection)
            XCTAssertEqual(f.model.input, "original")
            XCTAssertEqual(f.model.output, "existing result")
            XCTAssertEqual(f.model.productPhase, previousPhase)
            XCTAssertEqual(f.model.translationOrigin, previousOrigin)
        }
        XCTAssertEqual(requests, 3)
        XCTAssertEqual(resultPresentations, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testUnconfiguredUserOptInSurvivesQuitAndRestoresWithoutHelperOrPermissionPrompt() throws {
        let first = SelectionMonitorFixture()
        let f = try ProductTestHarness(savedCLI: false, selectionMonitor: first)
        defer { f.cleanUp() }
        f.model.startMonitor()
        XCTAssertTrue(f.model.monitorRequestedEnabled)
        XCTAssertTrue(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
        f.model.prepareToQuit()
        XCTAssertFalse(first.running)
        XCTAssertTrue(f.model.monitorRequestedEnabled)
        XCTAssertTrue(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))

        let next = SelectionMonitorFixture()
        let restored = ProbeModel(preferences: f.preferences, selectionMonitor: next,
                                  readPermissions: { Self.granted })
        defer { restored.suspendMonitor() }
        XCTAssertEqual(next.starts, 0, "Construction remains inert.")
        restored.loadPresentation()
        restored.loadPresentation()
        restored.restoreSelectionMonitorIfNeeded()
        XCTAssertTrue(restored.monitorRequestedEnabled)
        XCTAssertTrue(restored.monitorEnabled)
        XCTAssertTrue(restored.translatePassiveSelections)
        XCTAssertEqual(next.starts, 1, "Launch and activation restoration are idempotent.")
        XCTAssertTrue(next.fallback)
        XCTAssertFalse(restored.connected)
        XCTAssertFalse(restored.cliBusy)
        XCTAssertEqual(restored.permissionSnapshot, Self.granted)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testMissingPermissionAndSecureInputPauseKeepChoiceAndRecoverWhenAllowed() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set(true, forKey: ProbeModel.selectionMonitorPreferenceKey)
        let monitor = SelectionMonitorFixture()
        var permissions = PermissionSnapshot(accessibility: .notGranted, inputMonitoring: .granted,
                                             screenCapture: .notGranted, secureInput: false)
        let model = ProbeModel(preferences: f.preferences, selectionMonitor: monitor,
                               readPermissions: { permissions })
        defer { model.suspendMonitor() }
        model.loadPresentation()
        XCTAssertTrue(model.monitorRequestedEnabled)
        XCTAssertFalse(model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertEqual(model.permissionSnapshot?.accessibility, .notGranted)
        permissions = .init(accessibility: .granted, inputMonitoring: .granted,
                            screenCapture: .notGranted, secureInput: true)
        model.refreshPermissions()
        XCTAssertEqual(monitor.starts, 0)
        permissions = Self.granted
        model.refreshPermissions()
        XCTAssertTrue(model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 1)
        monitor.running = false
        monitor.onStop?("localized or implementation-specific runtime message")
        XCTAssertTrue(model.monitorRequestedEnabled)
        XCTAssertFalse(model.monitorEnabled)
        XCTAssertTrue(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
        model.refreshPermissions()
        XCTAssertTrue(model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 2)
        XCTAssertFalse(model.connected)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testExplicitDisableSurvivesRestartAndCannotBeUndoneByPermissionRefresh() throws {
        let f = try ProductTestHarness(selectionMonitor: SelectionMonitorFixture())
        defer { f.cleanUp() }
        f.model.startMonitor()
        f.model.stopMonitor()
        f.model.refreshPermissions()
        XCTAssertFalse(f.model.monitorRequestedEnabled)
        XCTAssertFalse(f.model.monitorEnabled)
        XCTAssertFalse(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
        let monitor = SelectionMonitorFixture()
        let model = ProbeModel(preferences: f.preferences, selectionMonitor: monitor,
                               readPermissions: { Self.granted })
        defer { model.suspendMonitor() }
        model.loadPresentation()
        model.refreshPermissions()
        XCTAssertEqual(monitor.starts, 0)
        XCTAssertFalse(model.monitorRequestedEnabled)
        XCTAssertFalse(model.translatePassiveSelections)
    }

    @MainActor
    func testDiagnosticMonitoringDoesNotPersistProductOptIn() throws {
        let f = try ProductTestHarness(selectionMonitor: SelectionMonitorFixture())
        defer { f.model.suspendMonitor(); f.cleanUp() }
        f.model.startMonitor(accessibilityOnly: true)
        XCTAssertTrue(f.model.monitorEnabled)
        XCTAssertFalse(f.model.monitorRequestedEnabled)
        XCTAssertFalse(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
        let monitor = SelectionMonitorFixture()
        let model = ProbeModel(preferences: f.preferences, selectionMonitor: monitor,
                               readPermissions: { Self.granted })
        model.loadPresentation()
        XCTAssertFalse(model.monitorRequestedEnabled)
        XCTAssertEqual(monitor.starts, 0)
    }

    @MainActor
    func testHelperStoppingDoesNotStopAnIndependentEnabledSelectionShortcut() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
        defer { f.model.stopMonitor(); f.cleanUp() }
        let helper = try f.localReady()
        f.model.startMonitor()
        f.model.closePanel()
        helper.stopped()
        XCTAssertTrue(f.model.monitorEnabled)
        XCTAssertTrue(f.model.monitorRequestedEnabled)
        XCTAssertTrue(f.model.translatePassiveSelections)
        XCTAssertTrue(monitor.running)
        XCTAssertTrue(f.preferences.bool(forKey: ProbeModel.selectionMonitorPreferenceKey))
    }

    @MainActor
    func testUserMonitorStateIsTypedAndRelocalizesWithoutParsingDiagnosticMessages() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let monitor = SelectionMonitorFixture()
        var permissions = PermissionSnapshot(accessibility: .notGranted, inputMonitoring: .notGranted,
                                             screenCapture: .notGranted, secureInput: false)
        let model = ProbeModel(preferences: f.preferences, selectionMonitor: monitor,
                               readPermissions: { permissions })
        defer { model.suspendMonitor() }
        model.loadPresentation()
        XCTAssertEqual(model.selectionMonitorState, .off)
        monitor.failure = .permissionDenied
        model.startMonitor()
        XCTAssertEqual(model.selectionMonitorState, .requiresPermissions)
        XCTAssertFalse(model.selectionShortcutActive)
        model.interfaceLanguage = "en"
        XCTAssertTrue(model.selectionMonitorStateMessage.contains("Accessibility"))
        model.interfaceLanguage = "zh"
        XCTAssertTrue(model.selectionMonitorStateMessage.contains("辅助功能"))
        permissions = .init(accessibility: .granted, inputMonitoring: .granted,
                            screenCapture: .notGranted, secureInput: true)
        model.refreshPermissions()
        XCTAssertEqual(model.selectionMonitorState, .secureInput)
        XCTAssertTrue(model.selectionMonitorStateMessage.contains("安全输入"))
        permissions = Self.granted
        monitor.failure = nil
        model.refreshPermissions()
        XCTAssertEqual(model.selectionMonitorState, .active)
        XCTAssertTrue(model.selectionShortcutActive)
        XCTAssertTrue(model.selectionMonitorStateMessage.contains("快速输入"))
        model.translatePassiveSelections = false
        XCTAssertEqual(model.selectionMonitorState, .diagnostic)
        XCTAssertFalse(model.selectionShortcutActive)
        model.translatePassiveSelections = true
        XCTAssertEqual(model.selectionMonitorState, .active)
        monitor.running = false
        monitor.onStop?("Arbitrary diagnostic content, not a user-state contract")
        XCTAssertEqual(model.selectionMonitorState, .temporarilyUnavailable)
        XCTAssertFalse(model.selectionMonitorStateMessage.contains("Arbitrary"))
        model.stopMonitor()
        XCTAssertEqual(model.selectionMonitorState, .off)
        model.startMonitor(accessibilityOnly: true)
        XCTAssertEqual(model.selectionMonitorState, .diagnostic)
        XCTAssertFalse(model.selectionShortcutActive)
        XCTAssertFalse(model.monitorRequestedEnabled)
    }

    private static var granted: PermissionSnapshot {
        .init(accessibility: .granted, inputMonitoring: .granted, screenCapture: .notGranted, secureInput: false)
    }
}
