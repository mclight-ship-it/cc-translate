import Foundation
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
enum NativePresentationTestSupport {
    static func offline(_ defaults: UserDefaults, persists: Bool = true) -> ProbeModel {
        ProbeModel(preferences: defaults, persistsPreferences: persists, makeConnection: { notice in
            XCTFail("Presentation must not construct a helper.")
            return ProductTestHelper(notice: notice)
        }, runtimeProvider: {
            XCTFail("Presentation must not request a runtime.")
            throw ProbeError.bundleMissing
        }, locateCandidates: { _, _ in
            XCTFail("Presentation must not inspect CLI installations.")
            return []
        })
    }
}
