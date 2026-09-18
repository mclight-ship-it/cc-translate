import XCTest
@testable import CCTranslateSupport

final class DoubleCopyIntervalTests: XCTestCase {
    func testPositiveFiniteIntervalsAreNotAWhitelist() throws {
        XCTAssertEqual(DoubleCopyInterval.standard.seconds, 0.5)
        for seconds in [0.1, 0.2, 0.5, 0.75, 1.5, 3, 60] {
            XCTAssertEqual(try XCTUnwrap(DoubleCopyInterval(seconds: seconds)).seconds, seconds)
        }
        for seconds in [0.0, -0.0, -1, .nan, .infinity, -.infinity] {
            XCTAssertNil(DoubleCopyInterval(seconds: seconds))
        }
    }

    func testConfiguredPairBoundaryIsInclusiveAndConsumedExactlyOnce() throws {
        for seconds in [0.1, 0.75, 1.5, 3] {
            var pair = DoubleCopyState(interval: try XCTUnwrap(DoubleCopyInterval(seconds: seconds)))
            XCTAssertFalse(pair.observe(time: 0, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
            XCTAssertTrue(pair.observe(time: seconds, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
            XCTAssertFalse(pair.observe(time: seconds + 0.01, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
            pair.reset()
            XCTAssertFalse(pair.observe(time: 0, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
            XCTAssertFalse(pair.observe(time: seconds + 0.001, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        }
    }

    func testLongerIntervalStillRejectsRepeatFocusChangeAndSecureInput() throws {
        var pair = DoubleCopyState(interval: try XCTUnwrap(DoubleCopyInterval(seconds: 2)))
        XCTAssertFalse(pair.observe(time: 1, pid: 42, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(pair.observe(time: 1.7, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(pair.observe(time: 1.8, pid: 43, isCopy: true, isRepeat: true, secureInput: false))
        XCTAssertFalse(pair.observe(time: 1.9, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(pair.observe(time: 2.0, pid: 43, isCopy: true, isRepeat: false, secureInput: true))
        XCTAssertFalse(pair.observe(time: 2.1, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
        XCTAssertFalse(pair.observe(time: 2.2, pid: 43, isCopy: false, isRepeat: false, secureInput: false))
        XCTAssertFalse(pair.observe(time: 2.3, pid: 43, isCopy: true, isRepeat: false, secureInput: false))
    }

    func testJSONNumericAccessAcceptsIntegersButNeverCoercesBooleansOrStrings() throws {
        for raw in ["1", "0.75", "5e-1"] {
            XCTAssertEqual(try JSONValue.parse(Data(raw.utf8)).number, Double(raw))
        }
        for raw in ["true", "false", "\"0.75\"", "null", "{}", "[]"] {
            XCTAssertNil(try JSONValue.parse(Data(raw.utf8)).number)
        }
    }

    @MainActor
    func testNativeMonitorTimingChangesNeverRegisterOrEnableFallback() throws {
        let monitor = PassiveCopyMonitor()
        XCTAssertEqual(monitor.copyInterval, .standard)
        monitor.setCopyInterval(try XCTUnwrap(DoubleCopyInterval(seconds: 0.75)))
        XCTAssertEqual(monitor.copyInterval.seconds, 0.75)
        XCTAssertFalse(monitor.running)
        monitor.setCopyInterval(.standard)
        XCTAssertEqual(monitor.copyInterval, .standard)
        XCTAssertFalse(monitor.running)
    }
}
