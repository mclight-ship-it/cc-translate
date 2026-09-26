import XCTest
import Foundation
@testable import CCTranslateMac
@testable import CCTranslateSupport

private final class DictionaryFixtureProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "dictionary.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url else { return }
        let mode = url.lastPathComponent
        let headers = mode == "declared-large" ? ["Content-Length": "5"] : [:]
        guard let response = HTTPURLResponse(url: url, statusCode: mode == "http-error" ? 404 : 200,
                                            httpVersion: "HTTP/1.1", headerFields: headers) else { return }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        switch mode {
        case "too-large": client?.urlProtocol(self, didLoad: Data("12345".utf8))
        case "short": client?.urlProtocol(self, didLoad: Data("123".utf8))
        case "cancel":
            client?.urlProtocol(self, didLoad: Data("12".utf8))
            client?.urlProtocol(self, didLoad: Data("34".utf8))
        default: client?.urlProtocol(self, didLoad: Data("1234".utf8))
        }
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class DictionaryDownloaderTests: XCTestCase {
    @MainActor
    private func transfer(_ mode: String, existing: Bool = false) async throws
        -> (Result<Void, DictionaryDownloadError>, Data?) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("dictionary-transfer-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { XCTAssertNoThrow(try FileManager.default.removeItem(at: root)) }
        let destination = root.appendingPathComponent("ticket")
        if existing { try Data("original".utf8).write(to: destination) }
        var payload = DictionaryModelTests.ticket(path: destination)
        payload["url"] = .string("https://dictionary.invalid/\(mode)")
        let ticket = try DictionaryInstallTicket(payload: payload)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [DictionaryFixtureProtocol.self]
        let downloader = DictionaryDownloader(configuration: configuration)
        var progressValues: [Int64] = []
        let result: Result<Void, DictionaryDownloadError> = await withCheckedContinuation { continuation in
            downloader.start(ticket, progress: { bytes in
                progressValues.append(bytes)
                if mode == "cancel" { downloader.cancel() }
            }, completion: { continuation.resume(returning: $0) })
        }
        XCTAssertTrue(progressValues.allSatisfy { $0 <= ticket.size })
        let data = FileManager.default.fileExists(atPath: destination.path) ? try Data(contentsOf: destination) : nil
        return (result, data)
    }

    @MainActor
    func testExactBoundedTransferMovesOnlyCompleteFile() async throws {
        let (result, data) = try await transfer("success")
        guard case .success = result else { return XCTFail("Expected transfer success: \(result)") }
        XCTAssertEqual(data, Data("1234".utf8))
    }

    @MainActor
    func testOversizedDeclaredAndActualBodiesCannotProduceStagingFile() async throws {
        for mode in ["too-large", "declared-large"] {
            let (result, data) = try await transfer(mode)
            guard case .failure(.tooLarge) = result else { XCTFail("Expected size cap: \(result)"); continue }
            XCTAssertNil(data)
        }
    }

    @MainActor
    func testShortAndFailedHTTPResponsesAreNotInstalled() async throws {
        for mode in ["short", "http-error"] {
            let (result, data) = try await transfer(mode)
            guard case .failure(let error) = result else { XCTFail("Unexpected success"); continue }
            XCTAssertEqual(error, mode == "short" ? .sizeMismatch : .invalidResponse)
            XCTAssertNil(data)
        }
    }

    @MainActor
    func testCancellationStopsQueuedDataAndNeverCreatesLateStagingFile() async throws {
        let (result, data) = try await transfer("cancel")
        guard case .failure(.cancelled) = result else { return XCTFail("Expected cancellation: \(result)") }
        XCTAssertNil(data)
    }

    @MainActor
    func testTransferNeverOverwritesAnExistingDestination() async throws {
        let (result, data) = try await transfer("success", existing: true)
        guard case .failure(.fileIO) = result else { return XCTFail("Existing destination must reject a move.") }
        XCTAssertEqual(data, Data("original".utf8))
    }

    @MainActor
    func testHTTPDowngradeRedirectIsRefusedWithoutStartingNetwork() throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [DictionaryFixtureProtocol.self]
        let downloader = DictionaryDownloader(configuration: configuration)
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let source = try XCTUnwrap(URL(string: "https://dictionary.invalid/source"))
        let target = try XCTUnwrap(URL(string: "http://dictionary.invalid/downgrade"))
        let task = session.dataTask(with: source)
        let response = try XCTUnwrap(HTTPURLResponse(url: source, statusCode: 302,
                                                   httpVersion: "HTTP/1.1", headerFields: ["Location": target.absoluteString]))
        var accepted = true
        downloader.urlSession(session, task: task, willPerformHTTPRedirection: response,
                              newRequest: URLRequest(url: target)) { accepted = $0 != nil }
        XCTAssertFalse(accepted)
    }
}
