import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class SelectionMonitorFixture: PassiveSelectionMonitoring {
    var running = false
    var onSelection: ((SelectionResult) -> Void)?
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
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertTrue(f.copiedText.isEmpty)
    }

    @MainActor
    func testExistingExplicitSwitchGatesFallbackWithoutImplicitRegistrationOrHelperSetup() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
        defer { f.cleanUp(); f.model.stopMonitor() }
        f.model.translatePassiveSelections = true
        XCTAssertTrue(monitor.fallback)
        XCTAssertEqual(monitor.starts, 0)
        f.model.startMonitor()
        XCTAssertTrue(f.model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 1)
        XCTAssertTrue(monitor.fallback)
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
        let f = try ProductTestHarness(selectionMonitor: monitor)
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
    func testStopAndPermissionFailureCannotDeliverQueuedSelectionOrAutoRestart() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
        defer { f.cleanUp() }
        var results: [SelectionResult] = []
        f.model.onSelection = { results.append($0) }
        f.model.translatePassiveSelections = true
        f.model.startMonitor()
        f.model.stopMonitor()
        monitor.onSelection?(.present("late"))
        XCTAssertTrue(results.isEmpty)
        monitor.failure = .secureInput
        f.model.startMonitor()
        XCTAssertFalse(f.model.monitorEnabled)
        XCTAssertEqual(monitor.starts, 1)
        monitor.onSelection?(.present("still late"))
        XCTAssertTrue(results.isEmpty)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testNewExplicitIntentClearHistoryReuseCloseAndQuitCancelPendingSelection() throws {
        let monitor = SelectionMonitorFixture()
        let f = try ProductTestHarness(selectionMonitor: monitor)
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
        for reason in [SelectionResult.Reason.copyNotObserved, .clipboardChanged, .clipboardUnsupported,
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
}
