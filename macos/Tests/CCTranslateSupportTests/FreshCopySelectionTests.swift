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
                .init(target: $0.target, focusIdentity: requireFocus ? $0.focusIdentity : nil,
                      launchDate: $0.launchDate)
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
    var deferred = false
    var completion: ((SelectionResult) -> Void)?
    var cancellation: PlainTextPasteCancellation?
    func revision() -> Int {
        revisionCalls += 1
        let action = duringRevision
        duringRevision = nil
        action?()
        return count
    }
    func read(revision: Int, timeout: TimeInterval, cancellation: PlainTextPasteCancellation,
              whileValid: @escaping @MainActor () -> Bool,
              completion: @escaping @MainActor (SelectionResult) -> Void) {
        self.cancellation = cancellation
        let result = readSynchronously(revision: revision, whileValid: whileValid)
        if deferred { self.completion = completion }
        else { completion(result) }
    }
    private func readSynchronously(revision: Int, whileValid: () -> Bool) -> SelectionResult {
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
    func testTranslationGestureTimestampPrecedesReadsAndOnlyFiresForAValidPair() {
        let f = CopyFixture()
        var gestures: [TimeInterval] = []
        f.selection.onTranslationGesture = { time in
            XCTAssertTrue(f.clipboard.reads.isEmpty)
            XCTAssertEqual(f.context.axCalls, 0)
            gestures.append(time)
        }
        f.press(10)
        XCTAssertTrue(gestures.isEmpty)
        f.clipboard.count += 1
        f.press(10.2)
        XCTAssertEqual(gestures, [10.2])
        XCTAssertEqual(f.clipboard.reads, [6])
        f.press(10.3, repeatKey: true)
        XCTAssertEqual(gestures, [10.2])
    }

    @MainActor
    func testPreDispatchBaselineSurvivesFastFirstCopyAndDeduplicatedSecondCopy() {
        let f = CopyFixture()
        // The first native copy finishes before the main-queue observer runs.
        // The second native command leaves that same fresh revision unchanged.
        f.clipboard.count = 6
        f.selection.observe(time: 10, isCopy: true, isRepeat: false, revisionBeforeCopy: 5)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.context.time = 10.2
        f.selection.observe(time: 10.2, isCopy: true, isRepeat: false, revisionBeforeCopy: 6)
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        XCTAssertEqual(f.context.axCalls, 0)
    }

    @MainActor
    func testPreDispatchBaselineDoesNotAuthorizeUnchangedPregestureContents() {
        let f = CopyFixture()
        f.selection.observe(time: 10, isCopy: true, isRepeat: false, revisionBeforeCopy: 5)
        f.context.time = 10.2
        f.selection.observe(time: 10.2, isCopy: true, isRepeat: false, revisionBeforeCopy: 5)
        f.context.time = 12.21
        f.selection.poll()
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        XCTAssertEqual(f.context.results, [.unknown(.copyNotObserved)])
    }

    @MainActor
    func testUnreadableFreshCopyFallsBackToAXForSynchronousAndAsynchronousReaders() {
        for deferred in [false, true] {
            for failure in [SelectionResult.unknown(.clipboardUnsupported), .unknown(.clipboardUnavailable)] {
                let f = CopyFixture()
                f.context.ax = .present("accessible document selection")
                f.press(10)
                f.clipboard.count = 6
                f.clipboard.result = failure
                f.clipboard.deferred = deferred
                f.context.duringAX = {
                    f.context.time = 10.3
                    f.selection.poll()
                }
                f.press(10.2)
                if deferred {
                    XCTAssertEqual(f.context.axCalls, 0)
                    XCTAssertTrue(f.context.results.isEmpty)
                    f.clipboard.completion?(failure)
                }
                XCTAssertEqual(f.context.results, [.present("accessible document selection")])
                XCTAssertEqual(f.context.axCalls, 1)
                XCTAssertEqual(f.clipboard.reads, [6], "Reentrant AX must not launch another clipboard reader.")
                XCTAssertTrue(f.clipboard.cancellation?.isCancelled == true)
                f.clipboard.completion?(.present("late reader callback"))
                f.selection.poll()
                XCTAssertEqual(f.context.results.count, 1)
            }
        }
    }

    @MainActor
    func testValidAsynchronousBrowserCopyRetainsPriorityOverAccessibleText() {
        let f = CopyFixture()
        f.context.ax = .present("different accessible text")
        f.press(10)
        f.clipboard.count = 6
        f.clipboard.deferred = true
        f.press(10.2)
        XCTAssertEqual(f.context.axCalls, 0)
        f.clipboard.completion?(.present("actual browser copy"))
        XCTAssertEqual(f.context.results, [.present("actual browser copy")])
        XCTAssertEqual(f.context.axCalls, 0)
    }

    @MainActor
    func testUnusableAXIsTriedOncePerRevisionAndDoesNotPreventPromisedCopyRetry() {
        for ax in [SelectionResult.absent, .unknown(.unsupported), .unknown(.unavailable)] {
            let f = CopyFixture()
            f.context.ax = ax
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.result = .unknown(.clipboardUnavailable)
            f.press(10.2)
            XCTAssertEqual(f.context.axCalls, 1)
            XCTAssertTrue(f.context.results.isEmpty)
            f.context.time = 10.3
            f.selection.poll()
            XCTAssertEqual(f.clipboard.reads, [6, 6])
            XCTAssertEqual(f.context.axCalls, 1, "Do not repeatedly block on AX while promised bytes are unavailable.")
            f.clipboard.result = .present("promise fulfilled")
            f.context.time = 10.4
            f.selection.poll()
            XCTAssertEqual(f.context.results, [.present("promise fulfilled")])
            XCTAssertEqual(f.context.axCalls, 1)
        }
    }

    @MainActor
    func testClipboardHardFailuresAndRevisionCancellationNeverFallBackToAX() {
        for deferred in [false, true] {
            for failure in [SelectionResult.unknown(.tooLarge), .unknown(.focusChanged),
                            .unknown(.secureInput), .unknown(.accessibility), .unknown(.inputMonitoring),
                            .unknown(.clipboardChanged)] {
                let f = CopyFixture()
                f.context.ax = .present("must not bypass failed authorization or size budget")
                f.press(10)
                f.clipboard.count = 6
                f.clipboard.result = failure
                f.clipboard.deferred = deferred
                f.press(10.2)
                if deferred { f.clipboard.completion?(failure) }
                XCTAssertEqual(f.context.axCalls, 0)
                if failure == .unknown(.clipboardChanged) {
                    XCTAssertTrue(f.context.results.isEmpty)
                    f.context.time = 15.21
                    f.selection.poll()
                }
                XCTAssertEqual(f.context.results, [failure])
            }
        }
    }

    @MainActor
    func testAXFallbackPreservesHardAXFailuresEvenWhenPreflightStillAllowsReading() {
        for failure in [SelectionResult.unknown(.secureInput), .unknown(.accessibility),
                        .unknown(.inputMonitoring), .unknown(.focusChanged), .unknown(.tooLarge)] {
            let f = CopyFixture()
            f.context.ax = failure
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.deferred = true
            f.press(10.2)
            f.clipboard.completion?(.unknown(.clipboardUnsupported))
            XCTAssertEqual(f.context.axCalls, 1)
            XCTAssertEqual(f.context.results, [failure])
            f.selection.poll()
            XCTAssertEqual(f.clipboard.reads, [6])
        }
    }

    @MainActor
    func testAXFallbackRechecksSourceAndSecurityAfterClipboardRevisionLookup() {
        for focusChanged in [false, true] {
            let f = CopyFixture()
            f.context.ax = .present("must not read from an invalidated context")
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.deferred = true
            f.press(10.2)
            f.clipboard.duringRevision = {
                if focusChanged { f.context.source = nil }
                else { f.context.failure = .secureInput }
            }
            f.clipboard.completion?(.unknown(.clipboardUnavailable))
            XCTAssertEqual(f.context.axCalls, 0)
            XCTAssertEqual(f.context.results, [.unknown(focusChanged ? .focusChanged : .secureInput)])
        }
    }

    @MainActor
    func testRevisionChangeBeforeOrDuringAXFallbackDiscardsObsoleteSelection() {
        for duringAX in [false, true] {
            let f = CopyFixture()
            f.context.ax = .present("obsolete AX selection")
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.deferred = true
            f.press(10.2)
            if duringAX { f.context.duringAX = { f.clipboard.count = 7 } }
            else { f.clipboard.count = 7 }
            f.clipboard.completion?(.unknown(.clipboardUnsupported))
            XCTAssertTrue(f.context.results.isEmpty)
            XCTAssertEqual(f.context.axCalls, duringAX ? 1 : 0)
            f.clipboard.deferred = false
            f.clipboard.result = .unknown(.clipboardUnsupported)
            f.context.ax = .present("current AX selection")
            f.selection.poll()
            XCTAssertEqual(f.clipboard.reads, [6, 7])
            XCTAssertEqual(f.context.results, [.present("current AX selection")])
            XCTAssertEqual(f.context.axCalls, duringAX ? 2 : 1)
        }
    }

    @MainActor
    func testCancelledOrInvalidAXFallbackCannotPublishOrReviveItsRequest() {
        for duringAX in [false, true] {
            for cause in ["focus", "secureInput", "accessibility", "inputMonitoring", "deadline", "cancel", "disable"] {
                let f = CopyFixture()
                f.context.ax = .present("obsolete selection")
                f.press(10)
                f.clipboard.count = 6
                f.clipboard.deferred = true
                f.press(10.2)
                let invalidate = {
                    switch cause {
                    case "focus": f.context.source = nil
                    case "secureInput": f.context.failure = .secureInput
                    case "accessibility": f.context.failure = .accessibility
                    case "inputMonitoring": f.context.failure = .inputMonitoring
                    case "deadline": f.context.time = 16
                    case "disable": f.selection.setFallbackEnabled(false)
                    default: f.selection.cancel()
                    }
                }
                if duringAX { f.context.duringAX = invalidate }
                else { invalidate() }
                f.clipboard.completion?(.unknown(.clipboardUnavailable))
                XCTAssertEqual(f.context.axCalls, duringAX ? 1 : 0)
                XCTAssertFalse(f.context.results.contains(.present("obsolete selection")))
                XCTAssertTrue(f.clipboard.cancellation?.isCancelled == true)
                let results = f.context.results
                f.context.failure = nil
                f.context.source = .init(target: .init(pid: 42), focusIdentity: nil)
                f.clipboard.completion?(.present("late callback"))
                f.selection.poll()
                XCTAssertEqual(f.context.results, results)
                XCTAssertEqual(results.count, cause == "cancel" || cause == "disable" ? 0 : 1)
            }
        }
    }

    @MainActor
    func testNewGestureDuringAXOrItsValidationOwnsPendingReadAndResult() {
        for stage in ["ax", "sourceValidation"] {
            let f = CopyFixture()
            f.context.ax = .present("obsolete AX selection")
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.deferred = true
            f.press(10.2)
            let obsoleteCompletion = f.clipboard.completion
            let replace = {
                f.press(10.3)
                f.clipboard.count = 7
                f.press(10.4)
            }
            f.context.duringAX = {
                if stage == "ax" { replace() }
                else { f.context.duringSource = replace }
            }
            obsoleteCompletion?(.unknown(.clipboardUnavailable))
            XCTAssertTrue(f.context.results.isEmpty)
            XCTAssertEqual(f.clipboard.reads, [6, 7])
            XCTAssertFalse(f.clipboard.cancellation?.isCancelled ?? true)
            f.selection.poll()
            XCTAssertEqual(f.clipboard.reads, [6, 7], "Old AX completion cannot clear the new reader's busy state.")
            obsoleteCompletion?(.present("obsolete clipboard callback"))
            XCTAssertTrue(f.context.results.isEmpty)
            f.clipboard.completion?(.present("new explicit copy"))
            XCTAssertEqual(f.context.results, [.present("new explicit copy")])
            XCTAssertEqual(f.context.axCalls, 1)
        }
    }

    @MainActor
    func testAsynchronousCopyAfterOldHalfSecondDeadlineStillUsesOneBoundedRequest() {
        let f = CopyFixture()
        f.context.duringAX = { f.context.time += 0.7 }
        f.pair()
        XCTAssertTrue(f.context.results.isEmpty)
        f.context.time = 11.1
        f.clipboard.count += 1
        f.selection.poll()
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        XCTAssertEqual(FreshCopySelection.copyWaitWindow, 2)
    }

    @MainActor
    func testPartialPublicationRetriesMetadataAndPromisedBytesAtSameOrNewRevision() {
        for failure in [SelectionResult.unknown(.clipboardUnsupported), .unknown(.clipboardUnavailable)] {
            for advances in [false, true] {
                let f = CopyFixture()
                f.pair()
                f.clipboard.count = 6
                f.clipboard.result = failure
                f.context.time = 10.3
                f.selection.poll()
                XCTAssertTrue(f.context.results.isEmpty)
                f.clipboard.result = .present("completed asynchronous copy")
                if advances { f.clipboard.count += 1 }
                f.context.time = 10.4
                f.selection.poll()
                XCTAssertEqual(f.clipboard.reads, [6, advances ? 7 : 6])
                XCTAssertEqual(f.context.results, [.present("completed asynchronous copy")])
            }
        }
    }

    @MainActor
    func testSecondCopyRacingIsolatedFirstReadDiscardsThenRetriesWithoutDuplicateResults() {
        let f = CopyFixture()
        f.press(10)
        f.clipboard.count = 6
        f.clipboard.deferred = true
        f.press(10.2)
        XCTAssertEqual(f.context.axCalls, 0)
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [6], "Only one isolated read may be in flight.")
        f.clipboard.count = 7
        f.clipboard.completion?(.present("obsolete first publication"))
        XCTAssertTrue(f.context.results.isEmpty)
        f.clipboard.deferred = false
        f.clipboard.result = .present("second publication")
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [6, 7])
        f.clipboard.completion?(.present("duplicate late worker callback"))
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.present("second publication")])
    }

    @MainActor
    func testPendingIsolatedReadIsCancelledOnDeadlineFocusPermissionAndExplicitCancellation() {
        for reason in ["deadline", "focus", "permission", "cancel"] {
            let f = CopyFixture()
            f.press(10)
            f.clipboard.count = 6
            f.clipboard.deferred = true
            f.press(10.2)
            switch reason {
            case "deadline": f.context.time = 15.71
            case "focus": f.context.source = nil
            case "permission": f.context.failure = .inputMonitoring
            default: f.selection.cancel()
            }
            f.selection.poll()
            XCTAssertTrue(f.clipboard.cancellation?.isCancelled == true)
            f.clipboard.completion?(.present("late worker text"))
            XCTAssertFalse(f.context.results.contains(.present("late worker text")))
            XCTAssertEqual(f.context.results.count, reason == "cancel" ? 0 : 1)
            if reason == "deadline" {
                XCTAssertEqual(f.context.results, [.unknown(.clipboardUnavailable)],
                               "A timed-out published copy is not the no-selection path.")
            }
        }
    }

    @MainActor
    func testLongerCopyPairUsesGestureBaselineWithoutReadingBeforeSecondPress() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.75)))
        f.press(10)
        f.clipboard.count = 6
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.context.duringAX = { f.clipboard.count = 7 }
        f.press(10.7)
        XCTAssertEqual(f.clipboard.reads, [6])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        f.press(10.8)
        XCTAssertEqual(f.context.axCalls, 0)
    }

    @MainActor
    func testShorterCopyPairReplacesBaselineWhenFirstPressExpires() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.1)))
        f.press(10)
        f.press(10.2)
        XCTAssertEqual(f.context.axCalls, 0)
        XCTAssertEqual(f.clipboard.revisionCalls, 2)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
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
        f.context.time = 13.21
        f.selection.poll()
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        XCTAssertEqual(FreshCopySelection.freshnessWindow, 2)
        XCTAssertEqual(f.context.results, [.unknown(.copyNotObserved)])

        let stale = CopyFixture()
        stale.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 1.5)))
        stale.press(10)
        stale.context.time = 13.1
        stale.selection.observe(time: 11, isCopy: true, isRepeat: false)
        XCTAssertEqual(stale.context.axCalls, 0)
        XCTAssertEqual(stale.clipboard.revisionCalls, 1)
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
    func testAXPresentAndHardFailuresDoNotWaitForClipboardText() {
        for result in [SelectionResult.present("AX selection"), .unknown(.accessibility),
                       .unknown(.secureInput), .unknown(.focusChanged), .unknown(.tooLarge)] {
            let f = CopyFixture()
            f.context.ax = result
            f.context.duringAX = { f.clipboard.count += 1 }
            f.pair()
            XCTAssertEqual(f.context.results, [result])
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
    }

    @MainActor
    func testOnlyNewRevisionWithinCopyGestureIsReadAndPairIsConsumed() {
        let f = CopyFixture()
        f.clipboard.count = 6
        f.press(10)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.clipboard.count = 8
        f.press(10.2)
        XCTAssertEqual(f.clipboard.reads, [8], "A copy completed before the global key notification is fresh.")
        f.context.time = 10.3
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [8])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        f.selection.poll()
        f.press(10.4)
        XCTAssertEqual(f.context.axCalls, 0, "A third press cannot reuse the consumed pair.")
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
            f.context.time = count == 4 ? 10.3 : 12.21
            f.selection.poll()
            XCTAssertTrue(f.clipboard.reads.isEmpty)
            XCTAssertEqual(f.context.results, [.unknown(count == 4 ? .clipboardChanged : .copyNotObserved)])
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertEqual(f.context.results.count, 1)
        }
    }

    @MainActor
    func testProcessChangeOrPIDReuseInvalidatesPendingCopy() {
        for changed in [PassiveCopySource(target: .init(pid: 43), focusIdentity: UUID()),
                        PassiveCopySource(target: .init(pid: 42), focusIdentity: nil,
                                          launchDate: Date(timeIntervalSince1970: 1))] {
            let f = CopyFixture()
            f.pair()
            f.context.source = changed
            f.clipboard.count += 1
            f.selection.poll()
            XCTAssertEqual(f.context.results, [.unknown(.focusChanged)])
            XCTAssertTrue(f.clipboard.reads.isEmpty)
        }
    }

    @MainActor
    func testMissingOrRecreatedAXFocusDoesNotVetoUsersActualCopy() {
        for identity in [nil, UUID()] {
            let f = CopyFixture()
            f.context.source = .init(target: .init(pid: 42), focusIdentity: identity)
            f.press(10)
            f.context.source = .init(target: .init(pid: 42), focusIdentity: UUID())
            f.clipboard.count += 1
            f.press(10.2)
            XCTAssertEqual(f.context.results, [.present("fresh copy")])
            XCTAssertEqual(f.clipboard.reads, [6])
            XCTAssertEqual(f.context.axCalls, 0, "Ordinary copy must not need AXSelectedText.")
        }
    }

    @MainActor
    func testFreshCopyAlreadyAvailableAvoidsSlowOrEmptyBrowserAXSelection() {
        for ax in [SelectionResult.absent, .unknown(.unsupported), .unknown(.unavailable),
                   .present("AX text differs from the explicit copy")] {
            let f = CopyFixture()
            f.context.ax = ax
            f.context.duringAX = { f.context.time += 1 }
            f.press(10)
            f.clipboard.count += 1
            f.press(10.2)
            XCTAssertEqual(f.context.results, [.present("fresh copy")])
            XCTAssertEqual(f.context.axCalls, 0)
            XCTAssertEqual(f.clipboard.reads, [6])
        }
    }

    @MainActor
    func testEmptyOrUnavailableBrowserAXCanUseCopyArrivingDuringOrAfterAX() {
        for ax in [SelectionResult.absent, .unknown(.unsupported), .unknown(.unavailable)] {
            for duringAX in [false, true] {
                let f = CopyFixture()
                f.context.ax = ax
                if duringAX { f.context.duringAX = { f.clipboard.count += 1 } }
                f.pair()
                if !duringAX {
                    XCTAssertTrue(f.context.results.isEmpty)
                    f.clipboard.count += 1
                    f.context.time = 10.3
                    f.selection.poll()
                }
                XCTAssertEqual(f.context.results, [.present("fresh copy")])
                XCTAssertEqual(f.clipboard.reads, [6])
                XCTAssertEqual(f.context.axCalls, 1)
            }
        }
    }

    @MainActor
    func testEmptyBrowserAXNeverReadsClipboardContentsFromBeforeGesture() {
        let f = CopyFixture()
        f.context.ax = .absent
        f.clipboard.count = 100
        f.pair()
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.context.time = 12.21
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.unknown(.copyNotObserved)])
        XCTAssertTrue(f.clipboard.reads.isEmpty)
    }

    @MainActor
    func testProcessChangeBetweenKeysCannotReuseFirstCopyBaseline() {
        let f = CopyFixture()
        f.press(10)
        f.clipboard.count += 1
        f.context.source = .init(target: .init(pid: 43), focusIdentity: UUID())
        f.press(10.2)
        f.press(10.3)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        XCTAssertTrue(f.context.results.isEmpty)
        f.clipboard.count += 1
        f.selection.poll()
        XCTAssertEqual(f.clipboard.reads, [7])
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
    }

    @MainActor
    func testExpiredFirstPressCannotAuthorizeAnEarlierCopy() throws {
        let f = CopyFixture()
        f.selection.setInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.1)))
        f.press(10)
        f.clipboard.count += 1
        f.press(10.2)
        f.press(10.25)
        XCTAssertTrue(f.clipboard.reads.isEmpty)
        f.context.time = 12.26
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.unknown(.copyNotObserved)])
        XCTAssertTrue(f.clipboard.reads.isEmpty)
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
                case "time": f.context.time = 16
                case "focus": f.context.source = nil
                default: f.selection.cancel()
                }
            }
            f.selection.poll()
            XCTAssertFalse(f.context.results.contains(.present("fresh copy")))
            XCTAssertEqual(f.context.results.count, cause == "cancel" || cause == "revision" ? 0 : 1)
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
        f.context.time = 12.3
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
        XCTAssertTrue(f.context.results.isEmpty, "A racing second copy is retried, not delivered incoherently.")
        f.context.time = 15.21
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.unknown(.clipboardChanged)])
    }

    @MainActor
    func testDelayedMainQueueDeliveryStillUsesEventTimesAndPreDispatchCounter() {
        let f = CopyFixture()
        f.clipboard.count = 6
        f.context.time = 11
        f.selection.observe(time: 10, isCopy: true, isRepeat: false, revisionBeforeCopy: 5)
        f.selection.observe(time: 10.2, isCopy: true, isRepeat: false, revisionBeforeCopy: 6)
        XCTAssertEqual(f.context.results, [.present("fresh copy")])
        XCTAssertEqual(f.context.axCalls, 0)
    }

    @MainActor
    func testPromisedReadSurvivesOldDeadlineAndSuccessfulWorkerCleanupGrace() {
        let f = CopyFixture()
        f.press(10)
        f.clipboard.count = 6
        f.clipboard.deferred = true
        f.press(10.2)
        f.context.time = 12.4
        f.selection.poll()
        XCTAssertTrue(f.context.results.isEmpty)
        XCTAssertFalse(f.clipboard.cancellation?.isCancelled ?? true)
        f.context.time = 15.3
        f.selection.poll()
        f.clipboard.completion?(.present("promise resolved before timeout; worker now reaped"))
        XCTAssertEqual(f.context.results, [.present("promise resolved before timeout; worker now reaped")])
    }

    @MainActor
    func testRepeatedIdenticalCopiesRequireFreshRevisionsNotDifferentContent() {
        let f = CopyFixture()
        for index in 0..<3 {
            f.press(10 + Double(index))
            f.clipboard.count += 1
            f.press(10.2 + Double(index))
        }
        XCTAssertEqual(f.context.results, Array(repeating: .present("fresh copy"), count: 3))
        XCTAssertEqual(f.clipboard.reads, [6, 7, 8])
        f.press(13)
        f.press(13.2)
        f.context.time = 15.21
        f.selection.poll()
        XCTAssertEqual(f.context.results.last, .unknown(.copyNotObserved))
        XCTAssertEqual(f.clipboard.reads, [6, 7, 8], "A later empty gesture cannot reuse an earlier successful copy.")
    }

    @MainActor
    func testEmptyPublishedCopyWaitsForPromisesThenReportsAbsence() {
        let f = CopyFixture()
        f.press(10)
        f.clipboard.count += 1
        f.clipboard.result = .absent
        f.press(10.2)
        XCTAssertTrue(f.context.results.isEmpty)
        f.context.time = 15.21
        f.selection.poll()
        XCTAssertEqual(f.context.results, [.absent])
        XCTAssertEqual(f.clipboard.count, 6)
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
    func install(observe: @escaping (NSEvent, Int?) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws {
        installations += 1
        self.observe = { observe($0, nil) }
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
