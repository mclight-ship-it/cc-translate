import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class CustomModelSettingsTests: XCTestCase {
    func testValidationPreservesIDsAndUsesBackendByteLimitWithoutPresetAliases() {
        for id in ["provider/Model-V2:preview", "future_model.2026", "gpt-5.4-mini",
                   "模型-v2", String(repeating: "界", count: 85) + "x"] {
            XCTAssertNil(CodexModelSettings.validateCustom(id), id)
        }
        XCTAssertEqual(CodexModelSettings.validateCustom(""), .empty)
        for id in [" model", "model ", "model\nid", "model\tid", "model\u{0}id", "model\u{00a0}id"] {
            XCTAssertEqual(CodexModelSettings.validateCustom(id), .whitespace)
        }
        XCTAssertEqual(CodexModelSettings.validateCustom(String(repeating: "界", count: 86)), .tooLong)
        XCTAssertEqual(CodexModelSettings.validateCustom("auto"), .preset)
        XCTAssertEqual(CodexModelSettings.validateCustom("auto-fast"), .preset)
        XCTAssertNil(CodexModelSettings.validateCustom("GPT-5.4-mini"), "No case folding or catalogue validation.")
        XCTAssertFalse(CodexModelSettings.sameID("e\u{301}", "\u{e9}"), "Provider IDs compare UTF-8, not canonical Unicode equivalence.")
    }

    func testCanonicallyEquivalentIDsKeepDistinctPickerIdentityAndRememberedChoice() {
        let composed = "model-\u{e9}"
        let decomposed = "model-e\u{301}"
        XCTAssertEqual(composed, decomposed, "Swift String equality alone merges these provider IDs.")
        let first = CodexModelSettings.ChoiceID(value: composed)
        let second = CodexModelSettings.ChoiceID(value: decomposed)
        XCTAssertNotEqual(first, second)
        XCTAssertEqual(Set([first, second]).count, 2)
        XCTAssertEqual(first, CodexModelSettings.ChoiceID(value: composed))
        var settings = CodexModelSettings()
        settings.restoreCustom(decomposed)
        let choices = settings.choices(selection: composed)
        XCTAssertEqual(choices.map { Array($0.utf8) },
                       ["auto-fast", "auto", decomposed, composed].map { Array($0.utf8) })
        XCTAssertEqual(Set(choices.map { CodexModelSettings.ChoiceID(value: $0) }).count, 4)
        XCTAssertEqual(settings.choices(selection: decomposed).count, 3)
    }

    @MainActor
    func testTypingBeforeSettingsLoadDoesNotSelectSaveOrProbeAnything() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        fixture.model.editCustomModelID("provider/Unsent-Draft")
        XCTAssertEqual(fixture.model.modelProfile, "auto-fast")
        XCTAssertEqual(fixture.model.modelSettings.draft, "provider/Unsent-Draft")
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.runtimeRequests, 0)
        XCTAssertEqual(fixture.locatorRequests, 0)
        fixture.model.applyCustomModelID()
        XCTAssertEqual(fixture.model.modelSettings.phase, .failed(.unavailable))
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertNil(fixture.preferences.object(forKey: "lastCustomCodexModel"))
    }

    @MainActor
    func testSavedCustomLoadsButCannotOverwriteAnEditorDraftAndNoCatalogueIsRequested() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.editCustomModelID("new-unsent-id")
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "provider/Saved-V1"))
        XCTAssertEqual(fixture.model.modelProfile, "provider/Saved-V1")
        XCTAssertEqual(fixture.model.modelSettings.draft, "new-unsent-id")
        XCTAssertEqual(fixture.model.modelSettings.rememberedCustom, "provider/Saved-V1")
        XCTAssertEqual(fixture.model.modelSettings.savedProfile, "provider/Saved-V1")
        XCTAssertEqual(fixture.preferences.string(forKey: "lastCustomCodexModel"), "provider/Saved-V1")
        XCTAssertEqual(helper.operations, ["start.translation", "config.load"])
        fixture.model.resetCustomModelDraft()
        XCTAssertEqual(fixture.model.modelSettings.draft, "provider/Saved-V1")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testFocusedUncommittedEditorIsNotSeededByInitialOrLaterReadback() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.setCustomModelEditing(true)
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "saved-after-focus"))
        XCTAssertEqual(fixture.model.modelProfile, "saved-after-focus")
        XCTAssertEqual(fixture.model.modelSettings.draft, "", "Native composition may not have updated the binding yet.")
        XCTAssertTrue(fixture.model.modelSettings.editing)
        fixture.model.reloadModelSetting()
        try fixture.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(model: "new-readback"))
        XCTAssertEqual(fixture.model.modelSettings.draft, "")
        fixture.model.setCustomModelEditing(false)
        fixture.model.resetCustomModelDraft()
        XCTAssertEqual(fixture.model.modelSettings.draft, "new-readback")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testExplicitApplyWorksWithoutCodexAndConfirmsOnlyAfterExactReadback() throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        var config = ProductTestHarness.configuration(direction: "to_zh", history: false)
        config["future_setting"] = .string("preserve me")
        let helper = try fixture.localReady(configuration: config)
        let model = try XCTUnwrap(fixture.model)
        XCTAssertFalse(model.nativeTranslation)
        XCTAssertTrue(model.canApplyModelSetting)
        model.editCustomModelID("provider/My-Model:2026")
        model.applyCustomModelID()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        XCTAssertEqual(save.config["codex_model"], .string("provider/My-Model:2026"))
        XCTAssertEqual(save.config["model_provider"], .string("codex_cli"))
        XCTAssertEqual(save.config["direction"], .string("to_zh"))
        XCTAssertEqual(save.config["history_enabled"], .bool(false))
        XCTAssertEqual(save.config["future_setting"], .string("preserve me"))
        XCTAssertEqual(model.modelSettings.phase, .saving("provider/My-Model:2026"))
        XCTAssertEqual(model.modelSettings.savedProfile, "auto-fast")
        XCTAssertTrue(model.settingsBusy)
        model.applyCustomModelID()
        XCTAssertEqual(helper.configurationSaves.count, 1)
        helper.event("completed", id: save.id)
        XCTAssertEqual(model.modelSettings.phase, .reading)
        XCTAssertFalse(model.settingsReady)
        XCTAssertEqual(helper.configurationLoads.count, 2)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(model.modelSettings.phase, .applied("provider/My-Model:2026"))
        XCTAssertEqual(model.modelSettings.savedProfile, "provider/My-Model:2026")
        XCTAssertFalse(model.modelSettings.draftEdited)
        XCTAssertEqual(model.modelProfile, "provider/My-Model:2026")
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.resultActions.isEmpty)
        XCTAssertFalse(model.cliBusy)
        XCTAssertTrue(model.needsCLI)
    }

    @MainActor
    func testDraftAndLaterSelectionSurviveSaveReadbackAndObsoleteTerminalEvents() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.editCustomModelID("custom-A")
        model.applyCustomModelID()
        let save = try XCTUnwrap(helper.configurationSaves.first)
        model.editCustomModelID("custom-B")
        model.modelProfile = "auto"
        helper.event("completed", id: save.id)
        let read = try XCTUnwrap(helper.configurationLoads.last)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(model.modelSettings.draft, "custom-B")
        XCTAssertTrue(model.modelSettings.draftEdited)
        XCTAssertEqual(model.modelProfile, "auto")
        XCTAssertEqual(model.modelSettings.savedProfile, "custom-A")
        model.applyCustomModelID()
        let second = try XCTUnwrap(helper.configurationSaves.last)
        helper.event("completed", id: save.id)
        helper.event("completed", id: read, payload: ["config": .object(save.config)])
        XCTAssertEqual(model.modelSettings.phase, .saving("custom-B"))
        XCTAssertEqual(model.modelProfile, "custom-B")
        XCTAssertEqual(model.modelSettings.draft, "custom-B")
        helper.event("completed", id: second.id)
        try fixture.finishConfiguration(on: helper, configuration: second.config)
        XCTAssertEqual(model.modelSettings.phase, .applied("custom-B"))
        XCTAssertEqual(helper.configurationSaves.count, 2)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testPresetSwitchRetainsLastCustomChoiceAndPresentationCacheDoesNotChangeSelection() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.preferences.set("remembered-before-launch", forKey: "lastCustomCodexModel")
        fixture.model.loadPresentation()
        XCTAssertEqual(fixture.model.modelProfile, "auto-fast")
        XCTAssertEqual(fixture.model.modelSettings.draft, "remembered-before-launch")
        XCTAssertTrue(fixture.helpers.isEmpty)
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "saved-custom"))
        for preset in ["auto", "auto-fast"] {
            fixture.model.applyModelProfile(preset)
            let save = try XCTUnwrap(helper.configurationSaves.last)
            helper.event("completed", id: save.id)
            try fixture.finishConfiguration(on: helper, configuration: save.config)
            XCTAssertEqual(fixture.model.modelProfile, preset)
            XCTAssertEqual(fixture.model.modelSettings.choices(selection: preset), ["auto-fast", "auto", "saved-custom"])
            XCTAssertEqual(fixture.preferences.string(forKey: "lastCustomCodexModel"), "saved-custom")
        }
        fixture.model.applyModelProfile("saved-custom")
        XCTAssertEqual(helper.configurationSaves.last?.config["codex_model"], .string("saved-custom"))
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testInvalidCustomIncludingMigratedMiniCannotWriteOrSelectAnAutomaticFallback() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "saved-custom"))
        for invalid in ["", "  ", "bad\nid", "auto", "auto-fast", String(repeating: "a", count: 257)] {
            fixture.model.editCustomModelID(invalid)
            fixture.model.applyCustomModelID()
            XCTAssertEqual(fixture.model.modelProfile, "saved-custom")
            XCTAssertEqual(fixture.model.modelSettings.draft, invalid)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertEqual(fixture.model.modelSettings.phase,
                           .failed(.invalidID(try XCTUnwrap(CodexModelSettings.validateCustom(invalid)))))
        }
        XCTAssertTrue(helper.translations.isEmpty)
        fixture.model.editCustomModelID("corrected-id")
        XCTAssertEqual(fixture.model.modelSettings.phase, .idle)
    }

    @MainActor
    func testReadbackMismatchStopsWaitingTranslationWithoutAcceptingAutoOrReplayingSave() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.editCustomModelID("custom-requested")
        fixture.model.applyCustomModelID()
        fixture.model.input = "Explicit request while settings are being saved."
        fixture.model.translate(useCache: false)
        let save = try XCTUnwrap(helper.configurationSaves.first)
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(model: "auto"))
        XCTAssertEqual(fixture.model.modelSettings.phase,
                       .failed(.differentReadback(expected: "custom-requested", actual: "auto")))
        XCTAssertEqual(fixture.model.modelProfile, "custom-requested")
        XCTAssertEqual(fixture.model.modelSettings.savedProfile, "auto")
        XCTAssertEqual(fixture.model.productPhase, .failed)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testSaveAndReadbackFailuresRetainDraftAndRecoverByExplicitReadWithoutWriteReplay() throws {
        for failReadback in [false, true] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready()
            fixture.model.editCustomModelID("custom-retry")
            fixture.model.applyCustomModelID()
            let save = try XCTUnwrap(helper.configurationSaves.first)
            if failReadback { helper.event("completed", id: save.id) }
            let failedID: String
            if failReadback { failedID = try XCTUnwrap(helper.configurationLoads.last) }
            else { failedID = save.id }
            helper.event("failed", id: failedID, payload: ["code": .string("config_io_failed")])
            XCTAssertEqual(fixture.model.modelSettings.phase, .failed(.operation("config_io_failed")))
            XCTAssertEqual(fixture.model.modelSettings.draft, "custom-retry")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            fixture.model.reloadModelSetting()
            try fixture.finishConfiguration(on: helper, configuration: save.config)
            XCTAssertEqual(fixture.model.modelSettings.phase, .applied("custom-retry"))
            XCTAssertEqual(fixture.model.modelSettings.savedProfile, "custom-retry")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testConnectionLossRetiresApplyAndOldHelperCannotOverwriteReconnectedSettings() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        fixture.model.editCustomModelID("old-attempt")
        fixture.model.applyCustomModelID()
        let old = try XCTUnwrap(helper.configurationSaves.first)
        fixture.model.closePanel()
        helper.stopped()
        XCTAssertEqual(fixture.model.modelSettings.phase, .failed(.interrupted))
        XCTAssertNil(fixture.model.modelSettings.savedProfile)
        fixture.model.editCustomModelID("new-unsent-draft")
        fixture.model.openProduct()
        let next = try XCTUnwrap(fixture.helpers.last)
        next.event("ready")
        try fixture.finishConfiguration(on: next, configuration: ProductTestHarness.configuration(model: "actual-saved"))
        helper.event("completed", id: old.id)
        XCTAssertEqual(fixture.model.modelSettings.savedProfile, "actual-saved")
        XCTAssertEqual(fixture.model.modelSettings.draft, "new-unsent-draft")
        XCTAssertTrue(next.configurationSaves.isEmpty)
        XCTAssertTrue(next.translations.isEmpty)
    }

    @MainActor
    func testActiveTranslationBlocksApplyWhileDraftRemainsEditableAndRequestStaysUnchanged() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "frozen-model"))
        fixture.model.input = "Synthetic running model request."
        fixture.model.translate(useCache: false)
        let request = try XCTUnwrap(helper.translations.first)
        XCTAssertTrue(fixture.model.active)
        fixture.model.editCustomModelID("next-draft")
        fixture.model.applyCustomModelID()
        XCTAssertEqual(fixture.model.modelSettings.phase, .failed(.busy))
        XCTAssertEqual(fixture.model.modelProfile, "frozen-model")
        XCTAssertEqual(fixture.model.modelSettings.draft, "next-draft")
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.first?.id, request.id)
    }

    @MainActor
    func testCustomIDSelectionSnapshotsForTextAndOCRUseExistingPayloadOnly() throws {
        for origin in ["text", "ocr"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "prior-custom"))
            fixture.model.modelProfile = "provider/Exact-New-ID"
            fixture.model.input = "Reviewed text that must keep the clicked settings."
            fixture.model.translate(origin: origin, useCache: false)
            let save = try XCTUnwrap(helper.configurationSaves.first)
            fixture.model.editCustomModelID("unsent-draft")
            fixture.model.modelProfile = "auto"
            helper.event("completed", id: save.id)
            try fixture.finishConfiguration(on: helper, configuration: save.config)
            XCTAssertEqual(save.config["codex_model"], .string("provider/Exact-New-ID"))
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertEqual(helper.translations.count, 1)
            XCTAssertEqual(helper.translations.first?.origin, origin)
            XCTAssertEqual(fixture.model.modelProfile, "auto")
            XCTAssertEqual(fixture.model.modelSettings.draft, "unsent-draft")
            XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        }
    }

    @MainActor
    func testCanonicallyEquivalentButByteDifferentModelIDIsSavedInsteadOfSilentlyReused() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready(configuration: ProductTestHarness.configuration(model: "model-\u{e9}"))
        fixture.model.modelProfile = "model-e\u{301}"
        fixture.model.input = "Explicit Unicode ID test."
        fixture.model.translate(useCache: false)
        let save = try XCTUnwrap(helper.configurationSaves.first)
        XCTAssertEqual(Array(try XCTUnwrap(save.config["codex_model"]?.string).utf8), Array("model-e\u{301}".utf8))
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }
}
