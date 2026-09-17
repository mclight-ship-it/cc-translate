import XCTest
import AppKit
import CryptoKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class AboutBundleFixture {
    static let text = "SYNTHETIC NOTICE — not an actual license\r\n\r\n" +
        "**Keep these literal markers**\r\n  - Spaces and 中文 stay intact.\r\n" +
        "[Do not open](https://example.invalid)\r\n```text\r\n\tliteral code\r\n```\r\nEND OF NOTICE\r\n"
    static let paths = ["THIRD_PARTY_NOTICES", "Python/licenses/LICENSE.fixture.txt",
                        "certifi/LICENSE", "certifi/MPL-2.0.txt", "dictionary/Fixture-LICENSE.txt"]
    let root: URL

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("About-\(UUID().uuidString).app")
        do {
            try writeInfo()
            for path in Self.paths { try write(Self.text, path: "Contents/Resources/Licenses/" + path) }
            try write("{}", path: "Contents/Resources/Licenses/Python/PYTHON.json")
            try writeManifest()
        } catch {
            try FileManager.default.removeItem(at: root)
            throw error
        }
    }

    var resources: AboutBundleResources {
        let root = self.root
        return AboutBundleResources(location: { root })
    }

    func url(_ path: String) -> URL { root.appendingPathComponent(path) }

    func write(_ text: String, path: String) throws { try write(Data(text.utf8), path: path) }

    func write(_ data: Data, path: String) throws {
        let target = url(path)
        try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: target)
    }

    func writeInfo(_ info: [String: String]? = nil) throws {
        let info = info ?? [
            "CFBundleName": "Synthetic CC Translate", "CFBundleShortVersionString": "9.8.7",
            "CFBundleVersion": "42", "CFBundleIdentifier": "invalid.example.about-fixture",
            "LSMinimumSystemVersion": "14.0"
        ]
        try write(PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0),
                  path: "Contents/Info.plist")
    }

    func writeManifest(schema: Int = 1) throws {
        var hashes: [String: String] = [:]
        for path in Self.paths {
            let key = "Resources/Licenses/" + path
            hashes[key] = SHA256.hash(data: try Data(contentsOf: url("Contents/" + key)))
                .map { String(format: "%02x", $0) }.joined()
        }
        let manifest: [String: Any] = [
            "schema": schema, "source_commit": String(repeating: "a", count: 40),
            "source_tree_dirty": true, "development_only": true,
            "signing": "NOT performed by these scripts", "release_gate": "NOT PASSED",
            "application_license": "requires separate confirmation",
            "toolchain": ["xcode": "Xcode fixture\nBuild fixture", "sdk": "15.5", "architecture": "arm64"],
            "lock": ["python_version": "3.12.fixture"], "resource_hashes": hashes
        ]
        try write(JSONSerialization.data(withJSONObject: manifest), path: "Contents/Resources/source-manifest.json")
    }

    func cleanUp() { XCTAssertNoThrow(try FileManager.default.removeItem(at: root)) }
}

final class AboutTests: XCTestCase {
    func testReadsActualBundleFieldsAndAllNestedLicenseFilesWithoutRuntimeMetadataAsLicense() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let result = fixture.resources.overview()
        XCTAssertEqual(result.info?.name, "Synthetic CC Translate")
        XCTAssertEqual(result.info?.version, "9.8.7")
        XCTAssertEqual(result.info?.build, "42")
        XCTAssertEqual(result.info?.identifier, "invalid.example.about-fixture")
        XCTAssertEqual(result.info?.minimumOS, "14.0")
        XCTAssertEqual(result.source?.source_commit, String(repeating: "a", count: 40))
        XCTAssertEqual(result.source?.source_tree_dirty, true)
        XCTAssertEqual(result.source?.toolchain?.architecture, "arm64")
        XCTAssertEqual(result.source?.lock?.python_version, "3.12.fixture")
        XCTAssertEqual(result.documents.first?.id, "THIRD_PARTY_NOTICES")
        XCTAssertEqual(Set(result.documents.map(\.path)), Set(AboutBundleFixture.paths))
        XCTAssertTrue(result.documents.allSatisfy { $0.expectedSHA256?.count == 64 })
        XCTAssertTrue(result.issues.isEmpty)
    }

    func testLicenseReadPreservesCompleteUnicodeCRLFWhitespaceAndMarkdownLiterally() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let text = String(repeating: AboutBundleFixture.text, count: 2000) + "FINAL SYNTHETIC SENTINEL\r\n"
        try fixture.write(text, path: "Contents/Resources/Licenses/THIRD_PARTY_NOTICES")
        try fixture.writeManifest()
        let document = try XCTUnwrap(fixture.resources.overview().documents.first)
        XCTAssertEqual(try fixture.resources.license(document).get(), text)
    }

    func testMissingFieldsAreNotReplacedByGuessedVersionsOrSourceClaims() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        try fixture.writeInfo([:])
        try fixture.write(#"{"schema":1}"#, path: "Contents/Resources/source-manifest.json")
        let result = fixture.resources.overview()
        XCTAssertNil(result.info?.version)
        XCTAssertNil(result.info?.build)
        XCTAssertNil(result.info?.name)
        XCTAssertNil(result.source?.source_commit)
        XCTAssertNil(result.source?.signing)
        XCTAssertNil(result.source?.development_only)
    }

    func testMissingMetadataAndDeclaredLicenseProduceIndependentErrorsAndRecover() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        try FileManager.default.removeItem(at: fixture.url("Contents/Info.plist"))
        try FileManager.default.removeItem(at: fixture.url("Contents/Resources/Licenses/certifi/LICENSE"))
        let result = fixture.resources.overview()
        XCTAssertTrue(result.issues.contains(.init(resource: "Info.plist", error: .missing)))
        XCTAssertTrue(result.issues.contains(.init(resource: "Licenses/certifi/LICENSE", error: .missing)))
        XCTAssertNotNil(result.source)
        try fixture.writeInfo()
        try fixture.write(AboutBundleFixture.text, path: "Contents/Resources/Licenses/certifi/LICENSE")
        XCTAssertTrue(fixture.resources.overview().issues.isEmpty)
    }

    func testMalformedMetadataAndUnsupportedSchemaAreExplicitRatherThanFallbackSuccess() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        try fixture.write("not a plist", path: "Contents/Info.plist")
        try fixture.write(#"{"schema":1,"source_tree_dirty":"false"}"#, path: "Contents/Resources/source-manifest.json")
        var result = fixture.resources.overview()
        XCTAssertNil(result.info)
        XCTAssertNil(result.source)
        XCTAssertTrue(result.issues.contains(.init(resource: "Info.plist", error: .invalidMetadata)))
        XCTAssertTrue(result.issues.contains(.init(resource: "source-manifest.json", error: .invalidMetadata)))
        try fixture.writeInfo()
        try fixture.writeManifest(schema: 2)
        result = fixture.resources.overview()
        XCTAssertEqual(result.info?.version, "9.8.7")
        XCTAssertTrue(result.issues.contains(.init(resource: "source-manifest.json", error: .unsupportedSchema)))
    }

    func testUnavailableBundleAndMissingNoticesAreExplicit() throws {
        XCTAssertEqual(AboutBundleResources(location: { nil }).overview().issues,
                       [.init(resource: "Application bundle", error: .unavailable)])
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        try FileManager.default.removeItem(at: fixture.url("Contents/Resources/Licenses/THIRD_PARTY_NOTICES"))
        let result = fixture.resources.overview()
        XCTAssertEqual(result.issues.filter { $0.resource == "Licenses/THIRD_PARTY_NOTICES" }.count, 1)
        XCTAssertEqual(result.issues.first?.error, .missing)
        XCTAssertEqual(fixture.resources.license(.init(path: "THIRD_PARTY_NOTICES")), .failure(.missing))
    }

    func testInvalidEncodingEmptyAndOversizedDocumentsNeverShowReplacementOrTruncatedText() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let path = "Contents/Resources/Licenses/THIRD_PARTY_NOTICES"
        let document = AboutLicenseDocument(path: "THIRD_PARTY_NOTICES")
        try fixture.write(Data([0xff, 0xfe, 0x00]), path: path)
        XCTAssertEqual(fixture.resources.license(document), .failure(.invalidEncoding))
        try fixture.write("", path: path)
        XCTAssertEqual(fixture.resources.license(document), .failure(.emptyDocument))
        try fixture.write(Data(repeating: 65, count: 16 * 1024 * 1024 + 1), path: path)
        XCTAssertEqual(fixture.resources.license(document), .failure(.tooLarge))
    }

    func testChangedLicenseFailsChecksumAndCanBeRepairedWithoutGuessingItsContent() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let document = try XCTUnwrap(fixture.resources.overview().documents.first)
        try fixture.write("Truncated synthetic document", path: "Contents/Resources/Licenses/THIRD_PARTY_NOTICES")
        XCTAssertEqual(fixture.resources.license(document), .failure(.checksumMismatch))
        try fixture.write(AboutBundleFixture.text, path: "Contents/Resources/Licenses/THIRD_PARTY_NOTICES")
        XCTAssertEqual(try fixture.resources.license(document).get(), AboutBundleFixture.text)
    }

    func testTraversalSymlinksAndNonRegularDocumentsCannotEscapeBundleReadingScope() throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        for path in ["../source-manifest.json", "/Contents/Info.plist", "dictionary/../../Info.plist", "bad\\path"] {
            XCTAssertEqual(fixture.resources.license(.init(path: path)), .failure(.invalidLocation))
        }
        let linked = fixture.url("Contents/Resources/Licenses/linked")
        try FileManager.default.createSymbolicLink(at: linked, withDestinationURL: fixture.url("Contents/Info.plist"))
        XCTAssertEqual(fixture.resources.license(.init(path: "linked")), .failure(.linkedResource))
        XCTAssertTrue(fixture.resources.overview().issues.contains(.init(resource: "Licenses/linked", error: .linkedResource)))
        XCTAssertEqual(fixture.resources.license(.init(path: "dictionary")), .failure(.invalidLocation))
        let folder = fixture.url("Contents/Resources/Licenses/linked-directory")
        try FileManager.default.createSymbolicLink(at: folder, withDestinationURL: fixture.url("Contents"))
        XCTAssertEqual(fixture.resources.license(.init(path: "linked-directory/Info.plist")), .failure(.linkedResource))
    }

    @MainActor
    func testConstructionAndViewCreationDoNotReadBundleOrStartHelperAndDocumentsAreExplicit() async throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let product = try ProductTestHarness(savedCLI: false)
        defer { product.cleanUp() }
        let reader = AboutRecordingResources(base: fixture.resources)
        let model = AboutModel(resources: reader)
        _ = AboutView(model: model, presentation: product.model, close: {})
        XCTAssertEqual(reader.counts, [0, 0])
        XCTAssertEqual(model.phase, .idle)
        model.openResources()
        await model.loadTask?.value
        XCTAssertEqual(reader.counts, [1, 0])
        XCTAssertEqual(model.phase, .loaded)
        XCTAssertNil(model.documentText)
        model.selectedDocument = "THIRD_PARTY_NOTICES"
        await model.documentTask?.value
        XCTAssertEqual(reader.counts, [1, 1])
        XCTAssertEqual(model.documentText, AboutBundleFixture.text)
        XCTAssertTrue(product.helpers.isEmpty)
        XCTAssertEqual(product.runtimeRequests, 0)
        XCTAssertEqual(product.locatorRequests, 0)
        XCTAssertTrue(product.copiedText.isEmpty)
        model.close()
    }

    @MainActor
    func testExplicitDocumentRetryAndReloadRecoverWithoutRetainingStaleText() async throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let model = AboutModel(resources: fixture.resources)
        model.openResources()
        await model.loadTask?.value
        try fixture.write("Changed", path: "Contents/Resources/Licenses/THIRD_PARTY_NOTICES")
        model.selectedDocument = "THIRD_PARTY_NOTICES"
        await model.documentTask?.value
        XCTAssertNil(model.documentText)
        XCTAssertEqual(model.documentError, .checksumMismatch)
        try fixture.write(AboutBundleFixture.text, path: "Contents/Resources/Licenses/THIRD_PARTY_NOTICES")
        model.loadDocument()
        await model.documentTask?.value
        XCTAssertEqual(model.documentText, AboutBundleFixture.text)
        XCTAssertNil(model.documentError)
        try fixture.write("malformed", path: "Contents/Resources/source-manifest.json")
        model.reload()
        XCTAssertNil(model.documentText)
        await model.loadTask?.value
        await model.documentTask?.value
        XCTAssertTrue(model.overview.issues.contains(.init(resource: "source-manifest.json", error: .invalidMetadata)))
        try fixture.writeManifest()
        model.reload()
        await model.loadTask?.value
        await model.documentTask?.value
        XCTAssertTrue(model.overview.issues.isEmpty)
        model.close()
        XCTAssertNil(model.documentText)
        XCTAssertNil(model.selectedDocument)
        XCTAssertTrue(model.overview.documents.isEmpty)
    }

    @MainActor
    func testCopyInformationIsExplicitAndCopyFailureDoesNotChangeTranslationStatus() async throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let product = try ProductTestHarness(savedCLI: false)
        defer { product.cleanUp() }
        var copied: [String] = []
        var succeeds = true
        let model = AboutModel(resources: fixture.resources, writeClipboard: {
            copied.append($0)
            return succeeds
        })
        model.openResources()
        await model.loadTask?.value
        XCTAssertTrue(copied.isEmpty)
        product.model.interfaceLanguage = "en"
        let originalStatus = product.model.status
        model.copyInformation(using: product.model)
        XCTAssertEqual(model.copyStatus, .copied)
        XCTAssertTrue(try XCTUnwrap(copied.first).contains("9.8.7"))
        XCTAssertTrue(try XCTUnwrap(copied.first).contains(String(repeating: "a", count: 40)))
        XCTAssertTrue(try XCTUnwrap(copied.first).contains("were not verified"))
        succeeds = false
        model.copyInformation(using: product.model)
        XCTAssertEqual(model.copyStatus, .failed)
        XCTAssertEqual(product.model.status, originalStatus)
        XCTAssertTrue(product.helpers.isEmpty)
        model.close()
        model.copyInformation(using: product.model)
        XCTAssertEqual(copied.count, 2)
    }

    @MainActor
    func testClosedOldOverviewCannotReplaceReopenedWindowAndCloseDoesNotWaitForIO() async throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        let entered = expectation(description: "First resource reader entered")
        let gate = DispatchSemaphore(value: 0)
        defer { gate.signal() }
        let reader = AboutRecordingResources(base: fixture.resources, beforeFirstOverview: {
            entered.fulfill()
            XCTAssertEqual(gate.wait(timeout: .now() + 5), .success)
        })
        let model = AboutModel(resources: reader)
        model.openResources()
        let oldTask = try XCTUnwrap(model.loadTask)
        await fulfillment(of: [entered], timeout: 3)
        model.close()
        XCTAssertEqual(model.phase, .idle)
        model.openResources()
        await model.loadTask?.value
        XCTAssertEqual(model.phase, .loaded)
        XCTAssertTrue(model.overview.issues.isEmpty)
        gate.signal()
        await oldTask.value
        XCTAssertEqual(model.phase, .loaded)
        XCTAssertTrue(model.overview.issues.isEmpty)
        XCTAssertEqual(reader.counts[0], 2)
        model.close()
    }

    @MainActor
    func testLateDocumentReadCannotOverwriteNewSelectionOrRestoreClosedContent() async throws {
        let fixture = try AboutBundleFixture()
        defer { fixture.cleanUp() }
        try fixture.write("SECOND SYNTHETIC DOCUMENT", path: "Contents/Resources/Licenses/certifi/LICENSE")
        try fixture.writeManifest()
        let entered = expectation(description: "First document reader entered")
        let gate = DispatchSemaphore(value: 0)
        defer { gate.signal() }
        let reader = AboutRecordingResources(base: fixture.resources, beforeFirstLicense: {
            entered.fulfill()
            XCTAssertEqual(gate.wait(timeout: .now() + 5), .success)
        })
        let model = AboutModel(resources: reader)
        model.openResources()
        await model.loadTask?.value
        model.selectedDocument = "THIRD_PARTY_NOTICES"
        let oldTask = try XCTUnwrap(model.documentTask)
        await fulfillment(of: [entered], timeout: 3)
        model.selectedDocument = "certifi/LICENSE"
        await model.documentTask?.value
        XCTAssertEqual(model.documentText, "SECOND SYNTHETIC DOCUMENT")
        gate.signal()
        await oldTask.value
        XCTAssertEqual(model.documentText, "SECOND SYNTHETIC DOCUMENT")
        XCTAssertNil(model.documentError)
        model.close()
        model.selectedDocument = "THIRD_PARTY_NOTICES"
        XCTAssertNil(model.documentText)
        XCTAssertEqual(reader.counts, [1, 2])
        model.openResources()
        await model.loadTask?.value
        XCTAssertNil(model.selectedDocument)
        XCTAssertNil(model.documentText)
        XCTAssertEqual(reader.counts, [2, 2])
        model.close()
    }
}

private final class AboutRecordingResources: AboutResourceReading, @unchecked Sendable {
    private let lock = NSLock()
    private var calls = [0, 0]
    private let base: AboutBundleResources
    private let beforeFirstOverview: @Sendable () -> Void
    private let beforeFirstLicense: @Sendable () -> Void
    init(base: AboutBundleResources, beforeFirstOverview: @escaping @Sendable () -> Void = {},
         beforeFirstLicense: @escaping @Sendable () -> Void = {}) {
        self.base = base
        self.beforeFirstOverview = beforeFirstOverview
        self.beforeFirstLicense = beforeFirstLicense
    }
    var counts: [Int] { lock.lock(); defer { lock.unlock() }; return calls }
    func overview() -> AboutOverview {
        lock.lock()
        calls[0] += 1
        let first = calls[0] == 1
        lock.unlock()
        if first { beforeFirstOverview() }
        return base.overview()
    }
    func license(_ document: AboutLicenseDocument) -> Result<String, AboutResourceError> {
        lock.lock()
        calls[1] += 1
        let first = calls[1] == 1
        lock.unlock()
        if first { beforeFirstLicense() }
        return base.license(document)
    }
}
