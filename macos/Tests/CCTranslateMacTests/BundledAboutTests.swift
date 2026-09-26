import XCTest
import Foundation
import CryptoKit
#if CC_TRANSLATE_RESOURCE_HARNESS
@testable import CCTranslateAppResources
#else
@testable import CCTranslateMac
#endif

final class BundledAboutTests: XCTestCase {
    func testPackagedAboutReadsRealMetadataAndEveryCompleteBundledLicenseWithoutLaunchingAnything() throws {
        guard let path = ProcessInfo.processInfo.environment["CC_TRANSLATE_APP"], !path.isEmpty else {
            throw XCTSkip("POSTBUILD only: set CC_TRANSLATE_APP to read its actual metadata and licenses; no host fallback.")
        }
        let root = URL(fileURLWithPath: path)
        let resources = AboutBundleResources(location: { root })
        let result = resources.overview()
        XCTAssertTrue(result.issues.isEmpty, "\(result.issues)")
        let hash = try XCTUnwrap(result.source?.resource_hashes?[AboutBundleResources.supportImagePath])
        let supportImage = try resources.supportImage(expectedSHA256: hash).get()
        XCTAssertEqual(hash, "73174e37515115d72d72c90985bb6dafe8d40f3d06dad3599614a94681160d4c")
        XCTAssertEqual(SHA256.hash(data: supportImage).map { String(format: "%02x", $0) }.joined(), hash)
        XCTAssertEqual(supportImage.count, 262177)
        let info = try XCTUnwrap(try PropertyListSerialization.propertyList(
            from: Data(contentsOf: root.appendingPathComponent("Contents/Info.plist")),
            format: nil) as? [String: Any])
        XCTAssertEqual(result.info?.version, try XCTUnwrap(info["CFBundleShortVersionString"] as? String))
        XCTAssertEqual(result.info?.build, try XCTUnwrap(info["CFBundleVersion"] as? String))
        XCTAssertEqual(result.info?.name, "CC Translate")
        if let build = ProcessInfo.processInfo.environment["GITHUB_RUN_NUMBER"] {
            XCTAssertEqual(result.info?.build, build, "The same App must retain its producer workflow build number.")
        }
        XCTAssertNotNil(result.source?.source_commit)
        XCTAssertTrue(result.documents.contains { $0.path == "THIRD_PARTY_NOTICES" })
        for group in ["Python/", "certifi/", "dictionary/"] {
            XCTAssertTrue(result.documents.contains { $0.path.hasPrefix(group) })
        }
        for document in result.documents {
            XCTAssertNotNil(document.expectedSHA256, document.path)
            let text = try resources.license(document).get()
            let raw = try Data(contentsOf: root.appendingPathComponent("Contents/Resources/Licenses/" + document.path))
            XCTAssertFalse(text.isEmpty, document.path)
            XCTAssertEqual(Data(text.utf8), raw, "No loss, normalization or truncation: \(document.path)")
        }
    }
}
