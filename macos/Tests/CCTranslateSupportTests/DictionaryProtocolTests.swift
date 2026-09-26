import XCTest
@testable import CCTranslateSupport

final class DictionaryProtocolTests: XCTestCase {
    private let ticket = String(repeating: "a", count: 32)
    private var status: [String: JSONValue] {
        ["state": .string("ready"), "enabled": .bool(true), "size": .integer(4096),
         "sha256": .string(String(repeating: "b", count: 64)), "data_version": .string("synthetic-1"),
         "download_url": .string("https://example.invalid/dictionary.sqlite3"), "entry_count": .integer(2)]
    }
    private var prepared: [String: JSONValue] {
        ["ticket": .string(ticket), "path": .string("/tmp/synthetic dictionary/\(ticket).sqlite3"),
         "url": .string("https://example.invalid/dictionary.sqlite3"), "size": .integer(4096),
         "sha256": .string(String(repeating: "b", count: 64)), "data_version": .string("synthetic-1")]
    }
    private var result: [String: JSONValue] {
        ["text": .string("run\n/r\u{028c}n/\nverb\n1. Move quickly.\nSources: Synthetic, Test license"),
         "submitted": .bool(false), "cached": .bool(false), "kind": .string("dict"), "target_lang": .null,
         "summarize": .bool(false), "history": .string("disabled"), "history_error": .null]
    }
    private var lookup: DictionaryRequest {
        .lookup(text: "run", appLanguage: "en_US", origin: "text", useCache: true, recordHistory: true)
    }

    private func event(_ id: String, _ sequence: Int64, _ type: String,
                       _ payload: [String: JSONValue] = [:]) throws -> Data {
        try JSONValue.object(["v": .integer(1), "id": .string(id), "seq": .integer(sequence),
                              "type": .string(type), "payload": .object(payload)]).encoded()
    }

    private func connected(_ mode: ProtocolState.Mode = .configuration) throws -> ProtocolState {
        var state = ProtocolState(mode: mode)
        try state.register(ClientMessage(id: "hello", type: "hello"))
        let operations = ["config_load", "config_save", "history_load", "history_add", "history_clear"] +
            DictionaryRequest.operations.sorted() + (mode == .translation ? ["translate", "result_action", "translate_image", "model_catalog"] : [])
        var ready: [String: JSONValue] = [
            "protocol": .integer(1), "capabilities": .array(operations.map(JSONValue.string)),
            "fixture": .bool(false), "max_frame_bytes": .integer(65_536)
        ]
        if mode == .translation { ready["backend"] = .string("native_appserver") }
        _ = try state.receive(event("hello", 0, "ready", ready))
        return state
    }

    private func accept(_ request: DictionaryRequest, id: String, state: inout ProtocolState,
                        started: Bool = true) throws {
        try state.register(ClientMessage(id: id, type: "request", payload: request.payload))
        let operation: [String: JSONValue] = ["operation": .string(request.operation)]
        _ = try state.receive(event(id, 0, "accepted", operation))
        if started { _ = try state.receive(event(id, 1, "started", operation)) }
    }

    func testSixTypedRequestsAndCompletionsWorkWithoutModelCapability() throws {
        let cases: [(DictionaryRequest, [String: JSONValue])] = [
            (.status, status), (lookup, ["status": .string("hit"), "result": .object(result)]),
            (.prepareInstall, prepared), (.install(ticket: ticket), status),
            (.discardInstall(ticket: ticket), ["discarded": .bool(true)]),
            (.delete, ["deleted": .bool(false), "enabled": .bool(false)])
        ]
        for mode in [ProtocolState.Mode.configuration, .translation] {
            for (request, completion) in cases {
                var state = try connected(mode)
                let message = ClientMessage(id: "dictionary", type: "request", payload: request.payload)
                let encoded = try message.encoded()
                XCTAssertEqual(encoded.last, 10)
                XCTAssertEqual(try JSONValue.parse(Data(encoded.dropLast())).object?["payload"],
                               .object(request.payload))
                try accept(request, id: "dictionary", state: &state)
                XCTAssertFalse(state.hasPendingTranslation)
                XCTAssertTrue(state.hasPendingDictionary)
                _ = try state.receive(event("dictionary", 2, "completed", completion))
                XCTAssertFalse(state.hasPendingDictionary)
                XCTAssertNil(state.pendingOutcomeUnknown)
            }
        }
    }

    func testRequestsDoNotAcceptCallerPathsURLsPinsOrInvalidLookupData() throws {
        let invalid: [[String: JSONValue]] = [
            ["operation": .string("dictionary_status"), "path": .string("/tmp/private")],
            ["operation": .string("dictionary_prepare_install"), "url": .string("https://example.invalid")],
            ["operation": .string("dictionary_install"), "sha256": .string(String(repeating: "a", count: 64)),
             "ticket": .string(ticket)],
            ["operation": .string("dictionary_install"), "ticket": .string("../dictionary")],
            ["operation": .string("dictionary_discard_install"), "ticket": .string(ticket.uppercased())],
            ["operation": .string("dictionary_delete"), "recursive": .bool(true)],
            lookup.payload.merging(["text": .string(String(repeating: "\u{1f600}", count: 2049))]) { _, new in new },
            lookup.payload.merging(["origin": .string("ocr")]) { _, new in new },
            lookup.payload.merging(["record_history": .integer(1)]) { _, new in new }
        ]
        for payload in invalid {
            var state = try connected()
            let request = ClientMessage(id: "bad", type: "request", payload: payload)
            XCTAssertThrowsError(try request.encoded())
            XCTAssertThrowsError(try state.register(request))
            XCTAssertFalse(state.hasPendingDictionary)
        }
        var state = try connected()
        try accept(.lookup(text: String(repeating: "\u{1f600}", count: 2048), appLanguage: "zh_CN",
                           origin: "selection", useCache: false, recordHistory: false), id: "limit", state: &state)
    }

    func testMetadataRequiresHTTPSAndExplicitBoundedDownloadIdentity() throws {
        let metadata = try DictionaryStatus(payload: status)
        XCTAssertEqual(metadata.state, .ready)
        XCTAssertEqual(metadata.entryCount, 2)
        let staging = try DictionaryInstallTicket(payload: prepared)
        XCTAssertEqual(staging.path.path, "/tmp/synthetic dictionary/\(ticket).sqlite3")
        var credentialURL = URLComponents()
        credentialURL.scheme = "https"
        credentialURL.host = "example.invalid"
        credentialURL.user = "synthetic"
        credentialURL.path = "/data"
        for changed in [
            ["size": JSONValue.integer(0)], ["size": .bool(true)],
            ["sha256": .string("missing")], ["url": .string("http://example.invalid/data")],
            ["url": .string(try XCTUnwrap(credentialURL.string))], ["path": .string("relative")],
            ["data_version": .string("")]
        ] {
            XCTAssertThrowsError(try DictionaryInstallTicket(payload: prepared.merging(changed) { _, new in new }))
        }
        XCTAssertThrowsError(try DictionaryStatus(payload: status.merging(["state": .string("unknown")]) { _, new in new }))
        for change in [["state": JSONValue.string("not_installed")], ["enabled": .bool(false)]] {
            XCTAssertThrowsError(try DictionaryDocument.validateCompletion(
                status.merging(change) { _, new in new }, operation: "dictionary_install"))
        }
    }

    func testLocalCompletionCannotClaimSubmissionOrHideMissAndHistoryState() throws {
        for miss in ["miss", "disabled", "unavailable", "ineligible"] {
            XCTAssertNil(try DictionaryLookupResult(payload: ["status": .string(miss), "result": .null]).result)
            XCTAssertThrowsError(try DictionaryLookupResult(payload: ["status": .string(miss), "result": .object(result)]))
        }
        for changes in [
            ["submitted": JSONValue.bool(true)], ["kind": .string("text")],
            ["target_lang": .string("en")], ["summarize": .bool(true)], ["cached": .bool(true)],
            ["history": .string("failed")],
            ["text": .string(String(repeating: "a", count: 24_001))]
        ] {
            XCTAssertThrowsError(try DictionaryLookupResult(payload: [
                "status": .string("hit"), "result": .object(result.merging(changes) { _, new in new })
            ]))
        }
        let cached = result.merging(["cached": .bool(true), "history": .string("unchanged")]) { _, new in new }
        XCTAssertNoThrow(try DictionaryLookupResult(payload: ["status": .string("hit"), "result": .object(cached)]))
        let historyFailed = result.merging([
            "history": .string("failed"), "history_error": .string("history_io_failed")
        ]) { _, new in new }
        XCTAssertNoThrow(try DictionaryLookupResult(payload: ["status": .string("hit"), "result": .object(historyFailed)]))
    }

    func testDictionaryAndConfigurationHaveIndependentOrderedLanes() throws {
        var state = try connected()
        let load: [String: JSONValue] = ["operation": .string("config_load")]
        try state.register(ClientMessage(id: "config", type: "request", payload: load))
        _ = try state.receive(event("config", 0, "accepted", load))
        _ = try state.receive(event("config", 1, "started", load))
        try accept(.status, id: "first", state: &state)
        try accept(.status, id: "second", state: &state, started: false)
        XCTAssertThrowsError(try state.receive(event("second", 1, "started", ["operation": .string("dictionary_status")])))
        _ = try state.receive(event("first", 2, "completed", status))
        _ = try state.receive(event("second", 1, "started", ["operation": .string("dictionary_status")]))
        _ = try state.receive(event("config", 2, "completed", ["config": .object([:])]))
        _ = try state.receive(event("second", 2, "completed", status))
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    private func source(id: String = "fixture", version: String = "1") -> JSONValue {
        .object(["id": .string(id), "label": .string("Synthetic source"), "version": .string(version),
                 "license": .string("Literal <license> [link](https://example.invalid)\nNo markup.")])
    }

    func testStructuredSourcesTraverseBothModesAndKeepVerbatimText() throws {
        let details: [JSONValue] = [source(), source(version: "2")]
        let enriched = result.merging(["source_details": .array(details)]) { _, new in new }
        let completion: [String: JSONValue] = ["status": .string("hit"), "result": .object(enriched)]
        for mode in [ProtocolState.Mode.configuration, .translation] {
            var state = try connected(mode)
            try accept(lookup, id: "sources", state: &state)
            let received = try state.receive(event("sources", 2, "completed", completion))
            let decoded = try DictionaryLookupResult(payload: received.payload)
            XCTAssertEqual(decoded.sources.map(\.id), ["fixture", "fixture"])
            XCTAssertEqual(decoded.sources.map(\.version), ["1", "2"])
            XCTAssertEqual(decoded.sources.first?.license,
                           "Literal <license> [link](https://example.invalid)\nNo markup.")
            XCTAssertEqual(decoded.result?["text"], result["text"])
            XCTAssertFalse(state.hasPendingDictionary)
        }
        XCTAssertThrowsError(try TranslationDocument.validateCompletion(enriched, streamed: false),
                             "Structured provenance is dictionary-only, not arbitrary model output.")
    }

    func testLegacyAndEmptySourceMetadataStayReadableWithoutInventedSources() throws {
        let legacy = try DictionaryLookupResult(payload: ["status": .string("hit"), "result": .object(result)])
        XCTAssertTrue(legacy.sources.isEmpty)
        XCTAssertEqual(legacy.result?["text"], result["text"])
        let empty = result.merging(["source_details": .array([])]) { _, new in new }
        XCTAssertTrue(try DictionaryLookupResult(payload: [
            "status": .string("hit"), "result": .object(empty)
        ]).sources.isEmpty)
        for status in ["miss", "disabled", "unavailable", "ineligible"] {
            XCTAssertTrue(try DictionaryLookupResult(payload: [
                "status": .string(status), "result": .null
            ]).sources.isEmpty)
        }
    }

    func testMalformedOrDuplicateSourceRecordsAreNotSilentlyIgnored() throws {
        let valid = try XCTUnwrap(source().object)
        var missing = valid
        missing.removeValue(forKey: "license")
        let bad: [JSONValue] = [
            .null, .object(valid), .array([.string("source")]), .array([.object(missing)]),
            .array([.object(valid.merging(["license": .integer(1)]) { _, new in new })]),
            .array([.object(valid.merging(["url": .string("https://example.invalid")]) { _, new in new })]),
            .array([source(), source()])
        ]
        for details in bad {
            let enriched = result.merging(["source_details": details]) { _, new in new }
            XCTAssertThrowsError(try DictionaryLookupResult(payload: [
                "status": .string("hit"), "result": .object(enriched)
            ]))
        }
    }

    func testSourceEqualityAndDeduplicationUseAllLiteralUTF8Fields() throws {
        let composed = try DictionarySource(payload: XCTUnwrap(source(id: "\u{e9}").object))
        let decomposed = try DictionarySource(payload: XCTUnwrap(source(id: "e\u{301}").object))
        let otherVersion = try DictionarySource(payload: XCTUnwrap(source(id: "\u{e9}", version: "2").object))
        XCTAssertNotEqual(composed, decomposed)
        XCTAssertNotEqual(composed, otherVersion)
        XCTAssertEqual(Set([composed, decomposed, otherVersion, composed]).count, 3)
        let literal = result.merging([
            "source_details": .array([source(id: "\u{e9}"), source(id: "e\u{301}")])
        ]) { _, new in new }
        XCTAssertEqual(try DictionaryLookupResult(payload: [
            "status": .string("hit"), "result": .object(literal)
        ]).sources.count, 2)
    }

    func testSourceMetadataRespectsUTF8FrameBudgetWithoutTruncation() throws {
        let identifier = String(repeating: "s", count: 64_000)
        let valid = result.merging(["source_details": .array([source(id: identifier)])]) { _, new in new }
        XCTAssertEqual(try DictionaryLookupResult(payload: [
            "status": .string("hit"), "result": .object(valid)
        ]).sources.first?.id, identifier)
        let excessive = result.merging([
            "source_details": .array([source(id: String(repeating: "\u{6e90}", count: 22_000))])
        ]) { _, new in new }
        let payload: [String: JSONValue] = ["status": .string("hit"), "result": .object(excessive)]
        XCTAssertThrowsError(try DictionaryLookupResult(payload: payload))
        var state = try connected()
        try accept(lookup, id: "sources", state: &state)
        XCTAssertThrowsError(try state.receive(event("sources", 2, "completed", payload)))
        XCTAssertTrue(state.hasPendingDictionary)
    }

    func testStartedCancellationAcknowledgementDoesNotReleaseLookupOrShutdown() throws {
        var state = try connected()
        try accept(lookup, id: "lookup", state: &state)
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("lookup")]))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .dictionaryOutcomeUnknown)
        try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
        XCTAssertThrowsError(try state.receive(event("shutdown", 0, "completed")))
        XCTAssertThrowsError(try state.receive(event("lookup", 2, "cancelled", ["submitted": .bool(false)])))
        _ = try state.receive(event("lookup", 2, "cancelled"))
        _ = try state.receive(event("shutdown", 0, "completed"))
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    func testQueuedCancellationAndFailureRemainDefiniteWithoutModelSubmission() throws {
        var state = try connected()
        try accept(.install(ticket: ticket), id: "queued", state: &state, started: false)
        try state.register(ClientMessage(id: "cancel", type: "cancel", payload: ["request_id": .string("queued")]))
        _ = try state.receive(event("cancel", 0, "completed", ["cancel_requested": .bool(true)]))
        XCTAssertThrowsError(try state.receive(event("queued", 1, "started", ["operation": .string("dictionary_install")])))
        _ = try state.receive(event("queued", 1, "cancelled"))
        try accept(.install(ticket: ticket), id: "failed", state: &state)
        let failure = try state.receive(event("failed", 2, "failed", ["code": .string("dictionary_install_failed")]))
        XCTAssertEqual(failure.safeFailureCode, "dictionary_install_failed")
        XCTAssertNil(state.pendingOutcomeUnknown)
        try accept(.install(ticket: ticket), id: "cleanup", state: &state, started: false)
        try state.register(ClientMessage(id: "cancel_cleanup", type: "cancel",
                                         payload: ["request_id": .string("cleanup")]))
        _ = try state.receive(event("cancel_cleanup", 0, "completed", ["cancel_requested": .bool(true)]))
        let cleanup = try state.receive(event("cleanup", 1, "failed", [
            "code": .string("dictionary_cleanup_failed")
        ]))
        XCTAssertEqual(cleanup.safeFailureCode, "dictionary_cleanup_failed")
        XCTAssertNil(state.pendingOutcomeUnknown)
    }

    func testModelUnknownTakesPriorityWithoutReclassifyingLocalRequests() throws {
        var state = try connected(.translation)
        try accept(lookup, id: "lookup", state: &state)
        var translate = lookup.payload
        translate["operation"] = .string("translate")
        try state.register(ClientMessage(id: "translate", type: "request", payload: translate))
        XCTAssertEqual(state.pendingOutcomeUnknown, .translationOutcomeUnknown)
        _ = try state.receive(event("translate", 0, "failed", ["code": .string("invalid_translation")]))
        XCTAssertEqual(state.pendingOutcomeUnknown, .dictionaryOutcomeUnknown)
        XCTAssertFalse(state.hasPendingTranslation)
    }

    func testOwnedStagingCleanupFailureIsSurfacedOnBusinessShutdown() throws {
        for mode in [ProtocolState.Mode.configuration, .translation] {
            var state = try connected(mode)
            try accept(.prepareInstall, id: "prepare", state: &state)
            _ = try state.receive(event("prepare", 2, "completed", prepared))
            try state.register(ClientMessage(id: "shutdown", type: "shutdown"))
            let failed = try state.receive(event("shutdown", 0, "failed", [
                "code": .string("dictionary_cleanup_failed")
            ]))
            XCTAssertEqual(failed.safeFailureCode, "dictionary_cleanup_failed")
            XCTAssertNil(state.pendingOutcomeUnknown)
        }
    }
}
