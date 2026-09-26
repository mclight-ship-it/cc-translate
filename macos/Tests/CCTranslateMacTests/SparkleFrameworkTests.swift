import AppKit
import Sparkle
import XCTest

@MainActor
final class SparkleFrameworkTests: XCTestCase {
    func testPinnedFrameworkLoadsWithoutStartingAnUpdateSession() {
        _ = NSApplication.shared
        let framework = Bundle(for: SPUUpdater.self)
        XCTAssertEqual(framework.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String, "2.10.0")
        let controller = SPUStandardUpdaterController(
            startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil
        )
        XCTAssertFalse(controller.updater.canCheckForUpdates)
        XCTAssertFalse(controller.updater.sessionInProgress)
    }
}
