import AppKit
import XCTest
@testable import CCTranslateSupport

@MainActor
private final class CopyContext {
    var time: TimeInterval = 10
    var source: PassiveCopySource? = .init(target: .init(pid: 42), focusIdentity: UUID())
    var failure: SelectionResult.Reason?
    var ax: SelectionResult = .unknown(.unsupported)
    var axCalls = 0
    var sourceCalls = 0
    var securityCalls = 0
    var duringAX: (() -> Void)?
    var duringSource: (() -> Void)?
    var results: [SelectionResult] = []

    var environment: PassiveCopyEnvironment {
        .init(now: { self.time }, source: { requireFocus in
            self.sourceCalls += 1
            let action = self.duringSource
            self.duringSource = nil
            action?()
            return self.source.map {
                .init(target: $0.target, focusIdentity: requireFocus ? $0.focusIdentity : nil)
            }
        }, securityFailure: {
            self.securityCalls += 1
            return self.failure
        }, selection: { _ in
            self.axCalls += 1
            let action = self.duringAX
            self.duringAX = nil
            action?()
            return self.ax
        })
    }
}

@MainActor
private final class CopyClipboard: FreshCopyClipboard {
    var count = 5
    var revisionCalls = 0
    var reads: [Int] = []
    var result: SelectionResult = .present("fresh copy")
    var duringRevision: (() -> Void)?
    var duringRead: (() -> Void)?
    var afterReadValidation: (() -> Void)?
    func revision() -> Int {
        revisionCalls += 1
        let action = duringRevision
        duringRevision = nil
        action?()
        return count
    }
    func read(revision: Int, whileValid: () -> Bool) -> SelectionResult {
        guard whileValid(), count == revision else { return .unknown(.clipboardChanged) }
        reads.append(revision)
        let action = duringRead
        duringRead = nil
        action?()
        guard whileValid(), count == revision else { return .unknown(.clipboardChanged) }
        let after = afterReadValidation
        afterReadValidation = nil
        after?()
        return result
    }
}

@MainActor
private final class CopyFixture {
    let context: CopyContext
    let clipboard: CopyClipboard
    let selection: FreshCopySelection
    init(fallback: Bool = true) {
        let context = CopyContext()
        let clipboard = CopyClipboard()
        self.context = context
        self.clipboard = clipboard
        selection = FreshCopySelection(environment: context.environment, clipboard: clipboard)
        selection.onSelection = { context.results.append($0) }
        selection.setFallbackEnabled(fallback)
    }
    func press(_ time: TimeInterval, copy: Bool = true, repeatKey: Bool = false) {
        context.time = time
        selection.observe(time: time, isCopy: copy, isRepeat: repeatKey)
    }
    func pair() { press(10); press(10.2) }
}

final class FreshCopySelectionTests: XCTestCase {
    @MainActor
    func testLongerCopyPairCapturesSecondBaselineBeforeAXWithoutReadingFirstCopy() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.75)))
        f.press(10)
        f.clipboard.count = 6
        f.context.duringAX = { f.clipboard.count = 7 }
        f.press(10.7)
        XCTAssertEqual(f.clipboard.reads, [7])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        f.press(10.8)
        XCTAssertEqual(f.context.axCalls, 1)
    }

    @MainActor
    func testShorterCopyPairDoesNotCaptureBaselineUntilWithinConfiguredInterval() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.1)))
        f.press(10)
        f.press(10.2)
        XCTAssertEqual(f.context.axCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
        f.context.duringAX = { f.clipboard.count += 1 }
        f.press(10.25)
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
    }

    @MainActor
    func testLongerPairDoesNotExtendCopyDeadlineOrAcceptStaleKeyEvents() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 1.5)))
        f.press(10)
        f.press(11.2)
        f.clipboard.count += 1
        f.context.time = 11.71
        f.selection.poll()
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        XCTAssertEqual(FreshCopySelection.freshnessWindow, 0.5)
        XCTAssertEqual(f.context.results, [.unknown(.copyNotObserved)])

        let stale = CopyFixture()
        stale.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 1.5)))
        stale.press(10)
        stale.context.time = 11.6
        stale.selection.observe(time: 11, isCopy: true, isRepeat: false)
        XCTAssertEqual(stale.context.axCalls, 0)
        XCTAssertEqual(stale.clipboard.revisionCalls, 0)
        XCTAssertTrue(stale.context.results.isEmpty)
    }

    @MainActor
    func testChangedIntervalInvalidatesPartialPairAndPendingFallback() throws {
        for pending in [false, true] {
            let f = CopyFixture()
            f.press(10)
            if pending { f.press(10.2) }
            let calls = f.context.axCalls
            f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.75)))
            f.clipboard.count += 1
            f.context.time = 10.3
            f.selection.poll()
            f.press(10.4)
            XCTAssertEqual(f.context.axCalls, calls)
            XCTAssertTrue(f.clipboard.reads.isEmpty)
            XCTAssertTrue(f.context.results.isEmpty)
        }
    }

    @MainActor
    func testUnchangedIntervalDoesNotDiscardPendingCopy() {
        let f = CopyFixture()
        f.pair()
        f.selection.setInterval(.standard)
        f.clipboard.count += 1
        f.context.time = 10.3
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
    }

    @MainActor
    func testChangedIntervalDuringReadCannotDeliverRetiredSelection() throws {
        let f = CopyFixture()
        let interval = try XCTUnwrap(DoubleCopyInterval(seconds: 0.75))
        f.pair()
        f.clipboard.count += 1
        f.context.time = 10.3
        f.clipboard.duringRead = { f.selection.setInterval(interval) }
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertTrue(f.context.results.isEmpty)
        XCTAssertEqual(f.selection.interval, interval)
    }

    @MainActor
    func testConstructionAXOnlyAndSingleCopyDoNotReadClipboard() {
        let f = CopyFixture(fallback: false)
        XCTAssertEqual(f.context.sourceCalls, 0)
        XCTAssertEqual(f.context.securityCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
        f.press(10)
        XCTAssertEqual(f.context.axCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
        f.press(10.2)
        XCTAssertEqual(f.context.results, [.unknown(.unsupported)])
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
    }

    @MainActor
    func testAXPresentAbsentAndNonUnsupportedFailuresNeverReadClipboardText() {
        for result in [SelectionResult.present("AX selection"), .absent, .unknown(.accessibility),
                       .unknown(.secureInput), .unknown(.focusChanged), .unknown(.unavailable), .unknown(.tooLarge)] {
            let f = CopyFixture()
            f.context.ax = result
            f.context.duringAX = { f.clipboard.count += 1 }
            f.pair()
            XCTAssertEqual(f.context.results, [result])
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
    }

    @MainActor
    func testOnlyNewRevisionAfterSecondKeyIsReadAndPairIsConsumed() {
        let f = CopyFixture()
        f.press(10)
        f.clipboard.count = 6
        f.press(10.2)
        XCTAssertTrue(f.clipboard.reads.isEmpty, "The first copy or any pre-trigger value is not a fallback.")
        f.clipboard.count = 8
        f.context.time = 10.3
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [8])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        f.selection.poll()
        f.press(10.4)
        XCTAssertEqual(f.context.axCalls, 1, "A third press cannot reuse the consumed pair.")
        XCTAssertEqual(f.clipboard.count, 8)
    }

    @MainActor
    func testCopyArrivingDuringAXReadCanBeUsedOnlyForUnsupportedSelection() {
        let f = CopyFixture()
        f.context.duringAX = { f.clipboard.count += 1 }
        f.pair()
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
    }

    @MainActor
    func testSecondPressCapturesRevisionBeforeSlowAXFocusRoundTrip() {
        let f = CopyFixture()
        f.press(10)
        f.context.duringSource = { f.clipboard.count += 1 }
        f.press(10.2)
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
    }

    @MainActor
    func testUnchangedResetAndExpiredClipboardNeverBecomeSelections() {
        for count in [5, 4, 6] {
            let f = CopyFixture()
            f.pair()
            f.clipboard.count = count
            f.context.time = count == 4 ? 10.3 : 10.71
            f.selection.poll()
            XCTAssertTrue(f.clipboard.reads.isEmpty)
            XCTAssertEqual(f.context.results, [.unknown(count == 4 ? .clipboardChanged : .copyNotObserved)])
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertEqual(f.context.results.count, 1)
        }
    }

    @MainActor
    func testFocusProcessAndMissingIdentityCannotAuthorizeCopyFallback() {
        for changed in [PassiveCopySource(target: .init(pid: 43), focusIdentity: UUID()),
                        PassiveCopySource(target: .init(pid: 42), focusIdentity: UUID())] {
            let f = CopyFixture()
            f.pair()
            f.context.source = changed
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertEqual(f.context.results, [.unknown(.focusChanged)])
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
        let f = CopyFixture()
        f.context.source = .init(target: .init(pid: 42), focusIdentity: nil)
        f.pair()
        XCTAssertEqual(f.context.results, [.unknown(.unsupported)])
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
    }

    @MainActor
    func testInterveningKeyRepeatOrCancellationDiscardsPendingAndFirstPress() {
        for interruption in ["key", "repeat", "cancel", "disable"] {
            let f = CopyFixture()
            f.pair()
            switch interruption {
            case "key": f.press(10.3, copy: false)
            case "repeat": f.press(10.3, repeatKey: true)
            case "disable": f.selection.setFallbackEnabled(false)
            default: f.selection.cancel()
            }
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertTrue(f.clipboard.reads.isEmpty)
            XCTAssertTrue(f.context.results.isEmpty)
            f.press(10.4)
            XCTAssertEqual(f.context.axCalls, 1)
        }
    }

    @MainActor
    func testSecureInputAndPermissionLossBeforeOrDuringReadPreventPublication() {
        for reason in [SelectionResult.Reason.secureInput, .accessibility, .inputMonitoring] {
            for duringRead in [false, true] {
                let f = CopyFixture()
                f.pair()
                f.clipboard.count += 1
                if duringRead { f.clipboard.duringRead = { f.context.failure = reason } }
                else { f.context.failure = reason }
                f.selection.poll()
                XCTAssertEqual(f.context.results, [.unknown(reason)])
                XCTAssertEqual(f.clipboard.reads.count, duringRead ? 1 : 0)
            }
        }
    }

    @MainActor
    func testLateChangedOrCancelledReadCannotPublishOldText() {
        for cause in ["revision", "time", "focus", "cancel"] {
            let f = CopyFixture()
            f.pair()
            f.clipboard.count += 1
            f.clipboard.duringRead = {
                switch cause {
                case "revision": f.clipboard.count += 1
                case "time": f.context.time = 11
                case "focus": f.context.source = nil
                default: f.selection.cancel()
                }
            }
            f.selection.poll()
            XCTAssertFalse(f.context.results.contains(.present("fresh copy")))
            XCTAssertEqual(f.context.results.count, cause == "cancel" ? 0 : 1)
        }
    }

    @MainActor
    func testReentrantNewPairOwnsItsResultAndOldReadCannotOverwriteIt() {
        let f = CopyFixture()
        f.pair()
        f.clipboard.count += 1
        f.clipboard.duringRead = {
            f.press(10.3)
            f.context.ax = .present("new explicit selection")
            f.press(10.4)
        }
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.present("new explicit selection")])
    }

    @MainActor
    func testCancellationInsideAXRevisionOrSourceCaptureCannotArmOrDeliverStaleIntent() {
        for stage in ["source", "revision", "ax"] {
            let f = CopyFixture()
            if stage == "source" {
                f.context.duringSource = { f.selection.cancel() }
            } else if stage == "revision" {
                f.clipboard.duringRevision = { f.selection.cancel() }
            } else {
                f.context.duringAX = { f.selection.cancel() }
            }
            f.pair()
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertTrue(f.context.results.isEmpty)
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
    }

    @MainActor
    func testDelayedPreEnableEventsNonfiniteAndBackwardsTimesDoNotTrigger() {
        for stamp in [Double.nan, Double.infinity, 9.9, 11] {
            let f = CopyFixture()
            f.selection.observe(time: stamp, isCopy: true, isRepeat: false)
            f.press(10.2)
            XCTAssertEqual(f.context.axCalls, 0)
        }
        let f = CopyFixture()
        f.press(10)
        f.context.time = 11
        f.selection.observe(time: 10.2, isCopy: true, isRepeat: false)
        XCTAssertEqual(f.context.axCalls, 0)
        let reversed = CopyFixture()
        reversed.press(10.3)
        reversed.press(10.2)
        reversed.press(10.4)
        XCTAssertEqual(reversed.context.axCalls, 0)
    }

    @MainActor
    func testUnavailableBaselineCannotAuthorizeAReadButStillAllowsAX() {
        for ax in [SelectionResult.unknown(.unsupported), .present("AX text")] {
            let f = CopyFixture()
            f.clipboard.count = -1
            f.context.ax = ax
            f.pair()
            f.clipboard.count = 1
            f.selection.poll()
            XCTAssertEqual(f.context.results, [ax])
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
    }

    @MainActor
    func testRevisionChangedDuringFinalSourceValidationCannotPublishReadText() {
        let f = CopyFixture()
        f.pair()
        f.clipboard.count += 1
        f.clipboard.afterReadValidation = {
            f.context.duringSource = { f.clipboard.count += 1 }
        }
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.unknown(.clipboardChanged)])
    }
}

@MainActor
private final class CopyEvents: PassiveCopyEvents {
    var installations = 0
    var removals = 0
    var fail = false
    var observe: ((NSEvent) -> Void)?
    var invalidate: (() -> Void)?
    var tick: (() -> Void)?
    func install(observe: @escaping (NSEvent) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws {
        installations += 1
        self.observe = observe
        self.invalidate = invalidate
        self.tick = tick
        if fail { throw ProbeError.permissionDenied }
    }
    func remove() { removals += 1 }
}

final class PassiveCopyMonitorTests: XCTestCase {
    @MainActor
    func testNativeLocalObserverReturnsTheIdenticalCopyEventWithoutChangingModifiers() throws {
        let event = try key(10)
        var invalidations = 0
        let observer = SystemPassiveCopyEvents.localObserver { invalidations += 1 }
        let returned = try XCTUnwrap(observer(event))
        XCTAssertTrue(returned === event)
        XCTAssertEqual(returned.modifierFlags, [.command])
        XCTAssertEqual(returned.charactersIgnoringModifiers, "c")
        XCTAssertEqual(invalidations, 1)
    }

    @MainActor
    private func key(_ time: TimeInterval, flags: NSEvent.ModifierFlags = [.command],
                     characters: String = "c", repeatKey: Bool = false) throws -> NSEvent {
        try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: flags,
                                      timestamp: time, windowNumber: 0, context: nil, characters: characters,
                                      charactersIgnoringModifiers: characters, isARepeat: repeatKey, keyCode: 8))
    }

    @MainActor
    private func monitor(_ f: CopyFixture, _ events: CopyEvents) -> PassiveCopyMonitor {
        let context = f.context
        let monitor = PassiveCopyMonitor(selection: f.selection, events: events, securityFailure: {
            context.securityCalls += 1
            return context.failure
        })
        monitor.onSelection = { context.results.append($0) }
        return monitor
    }

    @MainActor
    func testMonitorConstructionAndPolicyDoNotRegisterOrProbeAndStartIsIdempotent() throws {
        let f = CopyFixture(fallback: false)
        let events = CopyEvents()
        let monitor = monitor(f, events)
        monitor.setClipboardFallbackEnabled(true)
        XCTAssertEqual(f.context.securityCalls, 0)
        XCTAssertEqual(f.context.sourceCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
        XCTAssertEqual(events.installations, 0)
        try monitor.start()
        defer { monitor.stop() }
        try monitor.start()
        XCTAssertEqual(events.installations, 1)
        XCTAssertTrue(monitor.running)
    }

    @MainActor
    func testMonitorRequiresBothPermissionsAndSecureInputGuardBeforeRegistration() {
        for reason in [SelectionResult.Reason.accessibility, .inputMonitoring, .secureInput] {
            let f = CopyFixture()
            let events = CopyEvents()
            let monitor = monitor(f, events)
            f.context.failure = reason
            XCTAssertThrowsError(try monitor.start())
            XCTAssertFalse(monitor.running)
            XCTAssertEqual(events.installations, 0)
            XCTAssertEqual(f.clipboard.revisionCalls, 0)
        }
    }

    @MainActor
    func testFailedRegistrationIsRemovedAndLateCallbacksCannotAct() throws {
        let f = CopyFixture()
        let events = CopyEvents()
        events.fail = true
        let monitor = monitor(f, events)
        XCTAssertThrowsError(try monitor.start())
        events.observe?(try key(10))
        events.observe?(try key(10.2))
        events.tick?()
        XCTAssertEqual(events.removals, 1)
        XCTAssertEqual(f.context.axCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 0)
    }

    @MainActor
    func testExactNativeCommandCopyModifiersAndRepeatRemainPassive() throws {
        let modifiers: [NSEvent.ModifierFlags] = [[.command, .shift], [.command, .option], [.control], []]
        for flags in modifiers {
            let f = CopyFixture()
            let events = CopyEvents()
            let monitor = monitor(f, events)
            try monitor.start()
            defer { monitor.stop() }
            events.observe?(try key(10, flags: flags))
            f.context.time = 10.2
            events.observe?(try key(10.2))
            XCTAssertEqual(f.context.axCalls, 0)
        }
        let f = CopyFixture()
        let events = CopyEvents()
        let monitor = monitor(f, events)
        try monitor.start()
        defer { monitor.stop() }
        f.context.ax = .present("native AX")
        events.observe?(try key(10, flags: [.command, .capsLock], characters: "C"))
        f.context.time = 10.2
        events.observe?(try key(10.2))
        XCTAssertEqual(f.context.results, [.present("native AX")])
        f.context.time = 10.3
        events.observe?(try key(10.3, repeatKey: true))
        XCTAssertEqual(f.context.axCalls, 1)
    }

    @MainActor
    func testSourceLocalEventSleepAndStopInvalidationPreventLateCopyAcrossRestart() throws {
        let f = CopyFixture()
        let events = CopyEvents()
        let monitor = monitor(f, events)
        try monitor.start()
        let oldObserve = events.observe
        let oldTick = events.tick
        events.observe?(try key(10))
        f.context.time = 10.2
        events.observe?(try key(10.2))
        events.invalidate?()
        f.clipboard.count += 1
        events.tick?()
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        monitor.stop()
        try monitor.start()
        defer { monitor.stop() }
        f.context.time = 10.3
        oldObserve?(try key(10.3))
        oldTick?()
        f.context.time = 10.4
        events.observe?(try key(10.4))
        XCTAssertEqual(f.context.axCalls, 1)
    }

    @MainActor
    func testPermissionLossTickStopsWithoutAutomaticRestartOrDelayedRead() throws {
        let f = CopyFixture()
        let events = CopyEvents()
        let monitor = monitor(f, events)
        var stopped: [String] = []
        monitor.onStop = { stopped.append($0) }
        try monitor.start()
        events.observe?(try key(10))
        f.context.time = 10.2
        events.observe?(try key(10.2))
        f.context.failure = .secureInput
        f.clipboard.count += 1
        events.tick?()
        XCTAssertFalse(monitor.running)
        XCTAssertEqual(stopped.count, 1)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.context.failure = nil
        events.tick?()
        XCTAssertEqual(events.installations, 1)
    }

    @MainActor
    func testSecureInputObservedByAXStopsEvenWhenTheNextPreflightIsGranted() throws {
        let f = CopyFixture()
        let events = CopyEvents()
        let monitor = monitor(f, events)
        var stopped = 0
        monitor.onStop = { _ in stopped += 1 }
        f.context.ax = .unknown(.secureInput)
        try monitor.start()
        events.observe?(try key(10))
        f.context.time = 10.2
        events.observe?(try key(10.2))
        XCTAssertFalse(monitor.running)
        XCTAssertEqual(stopped, 1)
        XCTAssertTrue(f.context.results.isEmpty)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        events.tick?()
        XCTAssertEqual(events.installations, 1)
    }
}
