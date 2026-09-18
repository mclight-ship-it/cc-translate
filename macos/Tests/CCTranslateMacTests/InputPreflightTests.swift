import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class InputPreflightTests: XCTestCase {
    func testRawUnicodeScalarsNotGraphemesUTF16OrTrimmedNormalizedText() {
        for (text, count) in [("é", 1), ("e\u{301}", 2), ("😀", 1), ("👩‍💻", 3),
                              (" \r\né\t", 5), ("\u{feff}é", 2)] {
            XCTAssertEqual(text.unicodeScalars.count, count)
            XCTAssertNil(TextInputPreflight.check(text, limit: Int64(count), requireLimit: true))
            if count > 1 {
                XCTAssertEqual(TextInputPreflight.check(text, limit: Int64(count - 1), requireLimit: true),
                               .characters(count: count, limit: Int64(count - 1)))
            }
        }
        XCTAssertEqual(TextInputPreflight.maxBytes, TranslationDocument.maxInputBytes)
        XCTAssertEqual(TextInputPreflight.characterRange.upperBound, ConfigurationDocument.maxNumber)
    }

    func testUTF8AndCharacterBudgetsAreIndependentAtExactBoundaries() {
        for (text, allowed) in [(String(repeating: "中", count: 2730), true),
                                (String(repeating: "中", count: 2731), false),
                                (String(repeating: "a", count: 8192), true),
                                (String(repeating: "a", count: 8193), false)] {
            XCTAssertEqual(TextInputPreflight.check(text, limit: 20_001, requireLimit: true),
                           allowed ? nil : .bytes(text.utf8.count))
        }
        XCTAssertEqual(TextInputPreflight.check(String(repeating: "a", count: 5001), limit: 5000, requireLimit: true),
                       .characters(count: 5001, limit: 5000))
        XCTAssertNil(TextInputPreflight.check(String(repeating: "a", count: 5000), limit: 5000, requireLimit: true))
        XCTAssertEqual(TextInputPreflight.check("a", limit: nil, requireLimit: true), .unconfirmedLimit)
        XCTAssertNil(TextInputPreflight.check("a", limit: nil, requireLimit: false))
    }

    func testLargestEscapedRawTextStillFitsExistingNDJSONFrameBudget() throws {
        let source = String(repeating: "\u{0000}", count: TextInputPreflight.maxBytes)
        XCTAssertNil(TextInputPreflight.check(source, limit: ConfigurationDocument.maxNumber, requireLimit: true))
        let message = ClientMessage(id: "input-limit-wire-boundary", type: "request", payload: [
            "operation": .string("translate"), "text": .string(source), "app_language": .string("en_US"),
            "origin": .string("text"), "use_cache": .bool(true), "record_history": .bool(true)
        ])
        let wire = try message.encoded()
        XCTAssertGreaterThan(wire.count, source.utf8.count)
        XCTAssertLessThanOrEqual(wire.count, LineFramer.maxFrameBytes)
        XCTAssertEqual(LineFramer.maxFrameBytes, 65_536)
    }

    @MainActor
    func testEveryRawTextRouteRejectsBeforeDictionaryModelOrCacheAndRetainsOriginalBytes() throws {
        for origin in ["text", "selection", "ocr"] {
            for (source, limit) in [(String(repeating: "a", count: 5001), Int64(5000)),
                                    ("e\u{301}", 1), (" \r\né\t", 4),
                                    (String(repeating: "中", count: 2731), 20_001)] {
                let f = try ProductTestHarness()
                defer { f.cleanUp() }
                let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: limit))
                f.model.reuseHistory(.init(id: "previous", input: "Earlier", output: "Keep result"))
                var presented: [String] = []
                f.model.onTranslationResult = { presented.append($0) }
                if origin == "selection" { f.model.translateSelection(.present(source)) }
                else { f.model.input = source; f.model.translate(origin: origin, useCache: true) }
                XCTAssertEqual(Array(f.model.input.utf8), Array(source.utf8))
                XCTAssertEqual(f.model.output, "Keep result")
                XCTAssertEqual(f.model.resultInput, "Earlier")
                XCTAssertEqual(f.model.productPhase, .failed)
                XCTAssertEqual(f.model.productMessage, try XCTUnwrap(f.model.inputIssue(for: source)).message(using: f.model))
                if origin == "selection" { XCTAssertTrue(presented.last?.contains(f.model.productMessage) == true) }
                XCTAssertTrue(helper.translations.isEmpty)
                XCTAssertTrue(helper.dictionaryRequests.isEmpty, "Do not misreport an input limit as invalid_dictionary.")
                XCTAssertTrue(helper.configurationSaves.isEmpty)
                XCTAssertTrue(helper.messages.isEmpty)
            }
        }
    }

    @MainActor
    func testColdStartChecksLoadedLimitBeforeBusinessRequestsAndInvalidBytesNeverLaunch() throws {
        for source in [" \r\n\t", String(repeating: "a", count: 8193)] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            f.model.input = source
            f.model.translate()
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(Array(f.model.input.utf8), Array(source.utf8))
        }
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.input = String(repeating: "a", count: 5001)
        f.model.translate()
        let helper = try f.ready()
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(f.model.input.utf8.count, 5001)
        XCTAssertEqual(helper.configurationLoads.count, 1)
    }

    @MainActor
    func testValidOriginalTextReachesDictionaryAndOCRWithoutNormalizationOrCacheChanges() throws {
        for origin in ["text", "selection", "ocr"] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 5))
            let source = " \r\né\t"
            if origin == "selection" { f.model.translateSelection(.present(source)) }
            else { f.model.input = source; f.model.translate(origin: origin) }
            let request = try XCTUnwrap(helper.translations.last)
            XCTAssertEqual(Array(request.text.utf8), Array(source.utf8))
            XCTAssertEqual(request.origin, origin)
            XCTAssertEqual(request.useCache, origin != "ocr")
            XCTAssertEqual(helper.dictionaryRequests.count, origin == "ocr" ? 0 : 1)
            if let lookup = helper.dictionaryRequests.first {
                XCTAssertEqual(lookup.request, .lookup(text: source, appLanguage: "en_US", origin: origin,
                                                       useCache: true, recordHistory: true))
            }
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
    }

    @MainActor
    func testQueuedPreparationAndDictionaryMissUseLatestReadbackWithoutReplayingLookup() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        InputLimitFixture.save(f, "1")
        f.model.input = "ab"
        f.model.translate()
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        try InputLimitFixture.finish(f, helper)
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)

        InputLimitFixture.save(f, "5")
        try InputLimitFixture.finish(f, helper)
        helper.automaticDictionaryReplies = false
        f.model.input = "abc"
        f.model.translate()
        let lookup = try XCTUnwrap(helper.dictionaryRequests.last)
        helper.event("accepted", id: lookup.id)
        InputLimitFixture.save(f, "1")
        try InputLimitFixture.finish(f, helper)
        XCTAssertTrue(f.model.active)
        XCTAssertTrue(helper.messages.isEmpty)
        helper.event("completed", id: lookup.id, payload: ["status": .string("miss"), "result": .null])
        XCTAssertEqual(helper.dictionaryRequests.count, 1)
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertTrue(helper.translations.isEmpty, "A miss starts a separate model request; recheck before that request.")
        XCTAssertEqual(f.model.input, "abc")
    }

    @MainActor
    func testCancellationDuringLimitReadbackDoesNotResubmitPendingText() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        InputLimitFixture.save(f, "20001")
        f.model.input = "Queued text"
        f.model.translate()
        f.model.cancel()
        try InputLimitFixture.finish(f, helper)
        XCTAssertEqual(f.model.productPhase, .cancelled)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(f.model.inputLimit.saved, 20_001)
    }

    @MainActor
    func testNewLimitRejectsPreviouslyCachedInputBeforeAnotherLookupAndPreservesResult() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.input = "cached source"
        f.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("completed", id: request.id,
                     payload: SummaryPreferenceFixture.completed("Cached response", cached: true))
        InputLimitFixture.save(f, "1")
        try InputLimitFixture.finish(f, helper)
        f.model.translate(useCache: true)
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertEqual(f.model.output, "Cached response")
        XCTAssertEqual(f.model.resultInput, "cached source")
        XCTAssertEqual(helper.dictionaryRequests.count, 1)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(request.useCache)
        XCTAssertTrue(helper.messages.isEmpty)
    }

    @MainActor
    func testInFlightDictionaryHitCanCompleteAfterLimitChangeWithoutCancelOrNewModelRequest() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        f.model.input = "example"
        f.model.translate()
        let lookup = try XCTUnwrap(helper.dictionaryRequests.last)
        InputLimitFixture.save(f, "1")
        try InputLimitFixture.finish(f, helper)
        XCTAssertTrue(f.model.active)
        XCTAssertTrue(helper.messages.isEmpty)
        helper.event("completed", id: lookup.id, payload: DictionaryModelTests.hit("Local response", history: "disabled"))
        XCTAssertEqual(f.model.output, "Local response")
        XCTAssertEqual(f.model.resultInput, "example")
        XCTAssertTrue(f.model.isLocalDictionaryResult)
        XCTAssertEqual(f.model.productPhase, .completed)
        XCTAssertEqual(helper.dictionaryRequests.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(f.model.inputLimit.saved, 1)
    }

    @MainActor
    func testCaptureUsesSameLimitWithoutTruncatingReviewedTextOrOverwritingMainInput() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: 1))
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let screen = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(text: "e\u{301}") },
                                 notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        f.model.input = "Keep main input"
        XCTAssertFalse(capture.canTranslate(using: f.model))
        XCTAssertTrue(capture.canTranslateImage)
        capture.translate(using: f.model)
        XCTAssertEqual(Array(capture.text.utf8), Array("e\u{301}".utf8))
        XCTAssertEqual(f.model.input, "Keep main input")
        XCTAssertFalse(capture.submitted)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(capture.message(using: f.model),
                       TextInputPreflight.Issue.characters(count: 2, limit: 1).message(using: f.model))
        let rejectedMessage = capture.message(using: f.model)
        InputLimitFixture.save(f, "2")
        try InputLimitFixture.finish(f, helper)
        XCTAssertTrue(capture.canTranslate(using: f.model))
        XCTAssertNotEqual(capture.message(using: f.model), rejectedMessage,
                          "A confirmed setting change must not leave the previous limit in the capture notice.")
        XCTAssertTrue(helper.translations.isEmpty, "Updating validation is not an automatic retry.")
        InputLimitFixture.save(f, "1")
        try InputLimitFixture.finish(f, helper)
        XCTAssertFalse(capture.canTranslate(using: f.model))
        capture.text = "é"
        capture.translate(using: f.model)
        XCTAssertEqual(helper.translations.last?.text, "é")
        XCTAssertEqual(helper.translations.last?.origin, "ocr")
        XCTAssertEqual(helper.translations.last?.useCache, false)
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id),
                     payload: SummaryPreferenceFixture.completed("Original response"))
        XCTAssertTrue(capture.showsTranslationStatus)
        capture.text = "e\u{301}"
        XCTAssertFalse(capture.showsTranslationStatus, "A different raw spelling is not the submitted text.")
        XCTAssertFalse(capture.canTranslate(using: f.model))
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testImagesAndResultActionsDoNotUseRawTextLengthBudget() async throws {
        let image = try ImageAppFixture()
        defer { image.cleanUp() }
        let client = try image.ready(config: ProductTestHarness.configuration(maxChars: 0))
        image.model.input = String(repeating: "a", count: 8193)
        let request = try await image.send(client)
        XCTAssertEqual(client.imageRequests.count, 1, "Image translation does not consume max_chars.")
        image.complete(client, request: request)

        for limit in [Int64(0), 1] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(maxChars: limit))
            let original = String(repeating: "a", count: 9000)
            f.model.reuseHistory(.init(id: "long-history", input: original, output: "Retained result"))
            f.model.performResultAction(.asText)
            let action = try XCTUnwrap(helper.resultActions.last)
            XCTAssertEqual(action.text, original)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.dictionaryRequests.isEmpty)
            XCTAssertEqual(f.model.primaryResult, "Retained result")
            // Real backend config validation remains authoritative, including positivity for result actions.
            if limit == 0 {
                helper.event("failed", id: action.id, payload: ["code": .string("invalid_translation_settings")])
                XCTAssertEqual(f.model.productPhase, .failed)
                XCTAssertEqual(f.model.primaryResult, "Retained result")
            }
        }
    }
}
