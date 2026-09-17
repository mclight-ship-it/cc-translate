import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class OCRTranslationModelTests: XCTestCase {
    @MainActor
    func testExplicitOCRTranslationSkipsDictionaryAndCacheAndKeepsOCRSource() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.input = "Captured and reviewed text"
        fixture.model.translate(origin: "ocr")
        let sent = try XCTUnwrap(helper.translations.last)
        XCTAssertEqual(sent.text, "Captured and reviewed text")
        XCTAssertEqual(sent.origin, "ocr")
        XCTAssertFalse(sent.useCache)
        XCTAssertTrue(sent.recordHistory)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(fixture.model.translationOrigin, "ocr")
        XCTAssertEqual(fixture.model.resultKind, "ocr")
        helper.event("completed", id: sent.id, payload: [
            "text": .string("Reviewed translation"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("ocr"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("recorded"), "history_error": .null
        ])
        XCTAssertEqual(fixture.model.resultKind, "ocr")
        fixture.model.copyBilingual()
        XCTAssertEqual(fixture.copiedText.last, "Captured and reviewed text\n\nReviewed translation")
    }

    @MainActor
    func testOCRDraftFreezesTextLanguageDirectionAndProfileBeforeSettingsLoad() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.loadPresentation()
        model.input = "Reviewed capture text"
        model.translate(origin: "ocr")
        model.input = "Unsent editor change"
        model.direction = "to_en"
        model.modelProfile = "auto"
        model.interfaceLanguage = "zh"
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        try fixture.finishConfiguration(on: helper,
            configuration: ProductTestHarness.configuration(direction: "to_zh", model: "auto"))
        let saved = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(saved.config["direction"], .string("auto"))
        XCTAssertEqual(saved.config["codex_model"], .string("auto-fast"))
        XCTAssertEqual(saved.config["language"], .string("en_US"))
        helper.event("completed", id: saved.id)
        try fixture.finishConfiguration(on: helper)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "Reviewed capture text")
        XCTAssertEqual(helper.translations.last?.language, "en_US")
        XCTAssertEqual(helper.translations.last?.origin, "ocr")
        XCTAssertEqual(model.input, "Unsent editor change")
        XCTAssertEqual(model.direction, "to_en")
        XCTAssertEqual(model.modelProfile, "auto")
        XCTAssertEqual(model.interfaceLanguage, "zh")
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
    }

    @MainActor
    func testOCRConfigOnlyUpgradeSubmitsOnceAndKeepsFrozenInput() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let local = try fixture.localReady()
        fixture.canLocateCLI = true
        let model = try XCTUnwrap(fixture.model)
        model.input = "Reviewed OCR input"
        model.translate(origin: "ocr", useCache: true)
        model.input = "Changed after submission"
        XCTAssertEqual(local.stopCount, 1)
        XCTAssertTrue(local.dictionaryRequests.isEmpty)
        local.stopped()
        let upgraded = try XCTUnwrap(fixture.helpers.last)
        XCTAssertFalse(upgraded === local)
        upgraded.event("ready")
        try fixture.finishConfiguration(on: upgraded)
        XCTAssertEqual(upgraded.translations.count, 1)
        XCTAssertEqual(upgraded.translations.last?.text, "Reviewed OCR input")
        XCTAssertEqual(upgraded.translations.last?.origin, "ocr")
        XCTAssertEqual(upgraded.translations.last?.useCache, false)
        // Reconnection refreshes dictionary status, but must never look up OCR content.
        XCTAssertEqual(upgraded.dictionaryRequests.map(\.request), [.status])
        XCTAssertEqual(model.input, "Changed after submission")
    }

    @MainActor
    func testOCROverBudgetAndEmptyTextNeverLaunchHelperOrTruncate() throws {
        for input in [" \n\t", String(repeating: "字", count: 2731)] {
            let fixture = try ProductTestHarness(savedCLI: false)
            defer { fixture.cleanUp() }
            fixture.model.input = input
            fixture.model.translate(origin: "ocr")
            XCTAssertTrue(fixture.helpers.isEmpty)
            XCTAssertEqual(fixture.model.input, input)
            XCTAssertEqual(fixture.model.productPhase, .failed)
        }
    }

    @MainActor
    func testOCRCancelledPreparationNeverSubmitsAfterSettingsTerminal() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.input = "Reviewed capture"
        fixture.model.translate(origin: "ocr")
        let helper = try XCTUnwrap(fixture.helpers.last)
        helper.event("ready")
        fixture.model.cancel()
        XCTAssertEqual(fixture.model.productPhase, .cancelled)
        try fixture.finishConfiguration(on: helper)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertFalse(fixture.model.preparing)
    }
}
