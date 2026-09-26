import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

enum DictionarySourcesFixture {
    static func records(version: String = "1", license: String = "Synthetic license") -> [JSONValue] {
        [.object(["id": .string("fixture"), "label": .string("Synthetic source"),
                  "version": .string(version), "license": .string(license)]),
         .object(["id": .string("fixture"), "label": .string("Synthetic source"),
                  "version": .string("sense-2"), "license": .string("Literal <license> [link](https://example.invalid)")])]
    }

    static func hit(version: String = "1", text: String = DictionaryModelTests.senses,
                    license: String = "Synthetic license") throws -> [String: JSONValue] {
        var payload = DictionaryModelTests.hit(text)
        var result = try XCTUnwrap(payload["result"]?.object)
        result["source_details"] = .array(records(version: version, license: license))
        payload["result"] = .object(result)
        return payload
    }

    @MainActor
    static func publish(_ fixture: ProductTestHarness, helper: ProductTestHelper,
                        version: String = "1", text: String = DictionaryModelTests.senses,
                        license: String = "Synthetic license") throws {
        fixture.model.input = "example"
        fixture.model.translate()
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id),
                     payload: try hit(version: version, text: text, license: license))
    }

    static func supplement(_ text: String) -> [String: JSONValue] {
        ["text": .string(text), "submitted": .bool(true), "cached": .bool(false),
         "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
         "history": .string("disabled"), "history_error": .null]
    }
}

final class DictionarySourcesModelTests: XCTestCase {
    @MainActor
    func testAcceptedSourcesAreResultScopedAndDoNotChangeCopyOrTriggerIO() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        XCTAssertTrue(f.model.resultSources.isEmpty)
        try DictionarySourcesFixture.publish(f, helper: helper)
        let sources = f.model.resultSources
        XCTAssertEqual(sources.map(\.version), ["1", "sense-2"])
        f.model.copyResult()
        f.model.copyBilingual()
        XCTAssertEqual(f.copiedText, [DictionaryModelTests.senses, "example\n\n" + DictionaryModelTests.senses])
        let requests = helper.dictionaryRequests.count
        f.model.interfaceLanguage = "zh"
        f.model.appearance = "dark"
        f.model.input = "unsent edit"
        XCTAssertEqual(f.model.resultSources, sources)
        XCTAssertEqual(helper.dictionaryRequests.count, requests)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertNil(helper.selectedExecutable)
    }

    @MainActor
    func testSupplementsPreserveSourcesThroughUpgradeStreamingAndEveryTerminal() throws {
        for terminal in ["completed", "cancelled", "failed"] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let local = try f.localReady()
            try DictionarySourcesFixture.publish(f, helper: local)
            let sources = f.model.resultSources
            f.model.performResultAction(.summary)
            XCTAssertEqual(f.model.resultSources, sources)
            local.stopped()
            let provider = try f.ready()
            let request = try XCTUnwrap(provider.resultActions.last)
            XCTAssertEqual(f.model.resultSources, sources)
            provider.event("delta", id: request.id, payload: ["text": .string("partial"), "submitted": .bool(true)])
            if terminal == "cancelled" { f.model.cancel() }
            XCTAssertEqual(f.model.resultSources, sources)
            var payload: [String: JSONValue] = ["submitted": .bool(true)]
            if terminal == "completed" { payload = DictionarySourcesFixture.supplement("supplement") }
            if terminal == "failed" { payload["code"] = .string("provider_failed") }
            provider.event(terminal, id: request.id, payload: payload)
            XCTAssertEqual(f.model.resultSources, sources)
            XCTAssertEqual(f.model.primaryResult, DictionaryModelTests.senses)
            XCTAssertTrue(f.model.output.hasPrefix(DictionaryModelTests.senses))
            XCTAssertTrue(provider.translations.isEmpty)
            XCTAssertFalse(provider.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
        }
    }

    @MainActor
    func testCancelledLookupCannotReplaceVisibleSourcesAndNewHitReplacesPrecisely() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        let original = f.model.resultSources
        f.model.input = "next word"
        f.model.translate()
        let cancelled = try XCTUnwrap(helper.dictionaryRequests.last?.id)
        XCTAssertEqual(f.model.resultSources, original)
        f.model.cancel()
        helper.event("completed", id: cancelled, payload: try DictionarySourcesFixture.hit(version: "cancelled"))
        XCTAssertEqual(f.model.resultSources, original)
        XCTAssertEqual(f.model.output, DictionaryModelTests.senses)
        try DictionarySourcesFixture.publish(f, helper: helper, version: "new")
        let current = f.model.resultSources
        XCTAssertEqual(current.first?.version, "new")
        helper.event("completed", id: cancelled, payload: try DictionarySourcesFixture.hit(version: "late"))
        XCTAssertEqual(f.model.resultSources, current)
        f.model.clearTranslation()
        XCTAssertTrue(f.model.resultSources.isEmpty)
        helper.event("completed", id: cancelled, payload: try DictionarySourcesFixture.hit())
        XCTAssertTrue(f.model.resultSources.isEmpty)
    }

    @MainActor
    func testHistoryLegacyAndModelResultsNeverInventStructuredProvenance() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let local = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: local)
        f.model.reuseHistory(.init(id: "local", input: "example", output: DictionaryModelTests.senses,
                                  kind: "dict", signature: "local-dictionary|fixture|native-plain-v1:en_US"))
        XCTAssertTrue(f.model.isLocalDictionaryResult)
        XCTAssertTrue(f.model.resultSources.isEmpty)
        XCTAssertEqual(f.model.output, DictionaryModelTests.senses)
        try DictionarySourcesFixture.publish(f, helper: local)
        f.model.input = "legacy"
        f.model.translate()
        local.event("completed", id: try XCTUnwrap(local.dictionaryRequests.last?.id),
                    payload: DictionaryModelTests.hit())
        XCTAssertTrue(f.model.resultSources.isEmpty)
        try DictionarySourcesFixture.publish(f, helper: local)
        let sources = f.model.resultSources
        f.model.translate(useCache: false)
        XCTAssertEqual(f.model.resultSources, sources, "The previous result remains visible during preparation.")
        local.stopped()
        let provider = try f.ready()
        XCTAssertFalse(f.model.isLocalDictionaryResult)
        XCTAssertTrue(f.model.resultSources.isEmpty)
        provider.event("completed", id: try XCTUnwrap(provider.translations.last?.id),
                       payload: DictionarySourcesFixture.supplement(DictionaryModelTests.senses))
        XCTAssertTrue(f.model.resultSources.isEmpty, "Do not parse an attribution-looking model response.")
    }

    @MainActor
    func testClosingPanelClearsSourcesAndLateLookupCannotResurrectThem() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        f.model.input = "pending"
        f.model.translate()
        let request = try XCTUnwrap(helper.dictionaryRequests.last?.id)
        f.model.closePanel()
        XCTAssertTrue(f.model.resultSources.isEmpty)
        helper.event("completed", id: request, payload: try DictionarySourcesFixture.hit())
        XCTAssertTrue(f.model.resultSources.isEmpty)
    }

    @MainActor
    func testInvalidSourcesFailWithoutFallbackOrReplacingPreviousProvenance() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        try DictionarySourcesFixture.publish(f, helper: helper)
        let previous = f.model.resultSources
        f.model.input = "invalid"
        f.model.translate()
        var hit = DictionaryModelTests.hit("invalid result")
        var value = try XCTUnwrap(hit["result"]?.object)
        value["source_details"] = .array([.string("not a source record")])
        hit["result"] = .object(value)
        helper.event("completed", id: try XCTUnwrap(helper.dictionaryRequests.last?.id), payload: hit)
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertEqual(f.model.resultSources, previous)
        XCTAssertEqual(f.model.output, DictionaryModelTests.senses)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(f.helpers.count, 1)
    }
}
