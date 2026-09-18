import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

enum ClaudeProductFixture {
    static func configuration(model: String = "haiku") -> [String: JSONValue] {
        var config = ProductTestHarness.configuration()
        config["model_provider"] = .string("claude_cli")
        config["claude_model"] = .string(model)
        return config
    }

    @MainActor
    @discardableResult
    static func switchService(_ provider: TranslationProvider, fixture: ProductTestHarness,
                              helper: ProductTestHelper) throws -> ProductTestHelper {
        fixture.model.selectTranslationProvider(provider)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["model_provider"], .string(provider.rawValue))
        helper.event("completed", id: save.id)
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        if helper.stopCount > 0 {
            helper.stopped()
            return try fixture.ready(configuration: save.config)
        }
        return helper
    }
}

final class ClaudeProductTests: XCTestCase {
    func testPresetsAndCustomValidationAreProviderSpecificWithoutAnAccountCheck() {
        let codex = CodexModelSettings()
        let claude = CodexModelSettings(provider: .claude)
        XCTAssertEqual(codex.choices(selection: "auto-fast"), ["auto-fast", "auto"])
        XCTAssertEqual(claude.choices(selection: "haiku"), ["haiku", "sonnet", "opus"])
        XCTAssertEqual(claude.validateCustom("haiku"), .preset)
        XCTAssertNil(codex.validateCustom("haiku"))
        XCTAssertNil(claude.validateCustom("auto"))
        XCTAssertEqual(codex.validateCustom("auto"), .preset)
        XCTAssertNil(claude.validateCustom("future/Custom-Model"))
        XCTAssertEqual(claude.validateCustom("model with spaces"), .whitespace)
    }

    func testClaudeDraftReadbackPreservesByteDistinctIDsAndUnappliedEdits() {
        var settings = CodexModelSettings(provider: .claude)
        settings.edit("future/e\u{301}")
        settings.beginSave(id: "save", profile: settings.draft)
        settings.beginRead(id: "read", afterSave: true)
        settings.edit("future/Next")
        XCTAssertEqual(settings.loaded(profile: "future/\u{e9}", id: "read"),
                       .differentReadback(expected: "future/e\u{301}", actual: "future/\u{e9}"))
        XCTAssertEqual(settings.draft, "future/Next")
        XCTAssertEqual(settings.savedProfile, "future/\u{e9}")
    }

    @MainActor
    func testColdTranslationLoadsSavedClaudeAndRebindsBeforeAnyModelSubmission() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        f.model.input = "A synthetic sentence for translation."
        f.model.translate()
        let first = try XCTUnwrap(f.helpers.first)
        XCTAssertEqual(first.selectedProvider, .codex)
        let helper = try f.ready(configuration: ClaudeProductFixture.configuration(model: "future/Claude-ID"))
        XCTAssertEqual(first.stopCount, 1)
        XCTAssertTrue(first.translations.isEmpty)
        XCTAssertEqual(helper.selectedProvider, .claude)
        XCTAssertEqual(f.model.translationProvider, .claude)
        XCTAssertEqual(f.model.modelProfile, "future/Claude-ID")
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertFalse(f.model.cliBusy)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testSwitchWaitsForReadbackKeepsBothPathsAndUnappliedDraftsAndNeverReplays() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        f.preferences.set(f.alternateExecutable.path, forKey: "selectedClaudePath")
        let first = try f.ready()
        f.model.editCustomModelID("codex/Unapplied")
        f.model.selectTranslationProvider(.claude)
        XCTAssertEqual(f.model.translationProvider, .codex)
        XCTAssertEqual(f.model.pendingProvider, .claude)
        XCTAssertEqual(first.stopCount, 0)
        let save = try XCTUnwrap(first.configurationSaves.last)
        XCTAssertEqual(save.config["codex_model"], .string("auto-fast"))
        XCTAssertEqual(save.config["claude_model"], .string("haiku"))
        first.event("completed", id: save.id)
        XCTAssertEqual(first.stopCount, 0)
        try f.finishConfiguration(on: first, configuration: save.config)
        XCTAssertEqual(f.model.translationProvider, .claude)
        XCTAssertNil(f.model.pendingProvider)
        XCTAssertEqual(f.model.selectedCLI, f.alternateExecutable.path)
        first.stopped()
        let claude = try f.ready(configuration: save.config)
        XCTAssertEqual(claude.selectedProvider, .claude)
        f.model.editCustomModelID("claude/Unapplied")
        let codex = try ClaudeProductFixture.switchService(.codex, fixture: f, helper: claude)
        XCTAssertEqual(f.model.modelSettings.draft, "codex/Unapplied")
        XCTAssertEqual(f.model.selectedCLI, f.executable.path)
        _ = try ClaudeProductFixture.switchService(.claude, fixture: f, helper: codex)
        XCTAssertEqual(f.model.modelSettings.draft, "claude/Unapplied")
        XCTAssertEqual(f.model.selectedCLI, f.alternateExecutable.path)
        XCTAssertTrue(f.helpers.allSatisfy { $0.translations.isEmpty && $0.resultActions.isEmpty })
    }

    @MainActor
    func testClaudeCustomApplySavesOnlyItsModelAndConfirmsNormalizedReadback() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady(configuration: ClaudeProductFixture.configuration())
        f.model.editCustomModelID("future/Case-Sensitive")
        f.model.applyCustomModelID()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["claude_model"], .string("future/Case-Sensitive"))
        XCTAssertEqual(save.config["codex_model"], .string("auto-fast"))
        XCTAssertEqual(save.config["model_provider"], .string("claude_cli"))
        helper.event("completed", id: save.id)
        try f.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(f.model.modelSettings.phase, .applied("future/Case-Sensitive"))
        XCTAssertEqual(f.preferences.string(forKey: "lastCustomClaudeModel"), "future/Case-Sensitive")
        XCTAssertNil(f.preferences.string(forKey: "lastCustomCodexModel"))
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testServiceReadbackMismatchShowsTheActualSelectionWithoutRetry() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.selectTranslationProvider(.claude)
        helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
        try f.finishConfiguration(on: helper)
        XCTAssertEqual(f.model.translationProvider, .codex)
        XCTAssertNil(f.model.pendingProvider)
        XCTAssertTrue(f.model.providerMessage.contains("differs"))
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.stopCount, 0)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testFailedOrDisconnectedServiceChangeDoesNotClaimSuccessOrRetry() throws {
        for disconnect in [false, true] {
            let f = try ProductTestHarness()
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.selectTranslationProvider(.claude)
            if disconnect { helper.stopped() }
            else {
                helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                             payload: ["code": .string("config_write_failed")])
            }
            XCTAssertEqual(f.model.translationProvider, .codex)
            XCTAssertNil(f.model.pendingProvider)
            XCTAssertTrue(f.model.providerMessage.contains("not confirmed"))
            XCTAssertEqual(helper.configurationSaves.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testLocalDictionaryMissUpgradesOnlyToTheSelectedClaudeAfterAnExplicitNewIntent() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let config = ClaudeProductFixture.configuration()
        let local = try f.localReady(configuration: config, automaticReplies: true)
        f.model.input = "synthetic"
        f.model.translate()
        XCTAssertTrue(f.model.productMessage.contains("Claude"))
        XCTAssertTrue(local.translations.isEmpty)
        XCTAssertEqual(local.stopCount, 0)
        f.canLocateCLI = true
        XCTAssertTrue(local.translations.isEmpty)
        f.model.translate()
        XCTAssertEqual(local.stopCount, 1)
        local.stopped()
        let helper = try f.ready(configuration: config)
        XCTAssertEqual(helper.selectedProvider, .claude)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testClaudeCatalogRefreshDoesNotLaunchOrSubmitAndModelsRemainEditable() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady(configuration: ClaudeProductFixture.configuration())
        let previous = helper.operations
        f.model.refreshModels()
        XCTAssertEqual(f.model.modelCatalog.phase, .failed(.unavailable))
        XCTAssertEqual(helper.operations, previous)
        XCTAssertEqual(f.model.modelChoices(selection: "haiku"), ["haiku", "sonnet", "opus"])
        f.model.editCustomModelID("future/model")
        f.model.applyCustomModelID()
        XCTAssertEqual(helper.configurationSaves.last?.config["claude_model"], .string("future/model"))
    }

    @MainActor
    func testActiveTranslationCannotBeSwitchedOrReplayedByTheServicePicker() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.input = "A synthetic sentence."
        f.model.translate()
        XCTAssertTrue(f.model.active)
        f.model.selectTranslationProvider(.claude)
        XCTAssertNil(f.model.pendingProvider)
        XCTAssertEqual(f.model.translationProvider, .codex)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertEqual(helper.stopCount, 0)
    }

    @MainActor
    func testConfirmedProviderHintRestoresClaudeWithoutStartingAnythingAtConstruction() throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        f.preferences.set("claude_cli", forKey: "lastConfirmedTranslationProvider")
        f.preferences.set(f.alternateExecutable.path, forKey: "selectedClaudePath")
        f.preferences.set("future/Claude-Remembered", forKey: "lastCustomClaudeModel")
        XCTAssertTrue(f.helpers.isEmpty)
        f.model.loadPresentation()
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.locatorRequests, 0)
        XCTAssertEqual(f.model.translationProvider, .claude)
        XCTAssertEqual(f.model.modelSettings.draft, "future/Claude-Remembered")
        f.model.openProduct()
        XCTAssertEqual(f.helpers.first?.selectedProvider, .claude)
        XCTAssertEqual(f.helpers.first?.selectedExecutable, f.alternateExecutable)
        XCTAssertTrue(f.helpers.first?.translations.isEmpty == true)
    }

    @MainActor
    func testDefaultsRestoreBothModelsAndDraftsButPreserveBothCLIPaths() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set(f.executable.path, forKey: "selectedCodexPath")
        f.preferences.set(f.alternateExecutable.path, forKey: "selectedClaudePath")
        f.preferences.set("codex/Remembered", forKey: "lastCustomCodexModel")
        f.preferences.set("claude/Remembered", forKey: "lastCustomClaudeModel")
        let helper = try f.localReady(configuration: ClaudeProductFixture.configuration(model: "claude/Saved"))
        f.model.editCustomModelID("claude/Unapplied")
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["claude_model"], .string("haiku"))
        XCTAssertEqual(save.config["codex_model"], .string("auto-fast"))
        try SettingsDefaultsFixture.finish(f, helper: helper)
        XCTAssertEqual(f.model.defaultsPhase, .restored)
        XCTAssertEqual(f.model.translationProvider, .codex)
        XCTAssertEqual(f.model.modelProfile, "auto-fast")
        XCTAssertEqual(f.model.modelSettings.draft, "")
        XCTAssertNil(f.preferences.string(forKey: "lastCustomCodexModel"))
        XCTAssertNil(f.preferences.string(forKey: "lastCustomClaudeModel"))
        XCTAssertEqual(f.preferences.string(forKey: "selectedCodexPath"), f.executable.path)
        XCTAssertEqual(f.preferences.string(forKey: "selectedClaudePath"), f.alternateExecutable.path)
        _ = try ClaudeProductFixture.switchService(.claude, fixture: f, helper: helper)
        XCTAssertEqual(f.model.modelSettings.draft, "")
        XCTAssertEqual(f.model.modelProfile, "haiku")
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testImageIntentUsesTheSelectedClaudeConnectionAndOwnedAttachment() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.base.preferences.set("claude_cli", forKey: "lastConfirmedTranslationProvider")
        let client = try f.ready(config: ClaudeProductFixture.configuration())
        XCTAssertEqual(client.base.selectedProvider, .claude)
        let request = try await f.send(client)
        XCTAssertEqual(request.payload["operation"], .string("translate_image"))
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertTrue(client.base.translations.isEmpty)
        client.base.event("completed", id: request.id, payload: ImageAppFixture.completion)
        try await CaptureProductFixture.waitFor { f.factory.attachments.first?.removed == true }
    }

    @MainActor
    func testActualNativeServiceMenuSavesClaudeOnlyAfterTheUserChoosesItsItem() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady()
        let surface = NativeSettingsTestHost(
            Form { TranslationSettingsView(model: f.model).translationSection }.formStyle(.grouped),
            size: NSSize(width: 760, height: 980))
        defer { surface.close() }
        try await surface.waitFor {
            ScaleTestSupport.views(NSPopUpButton.self, in: surface.host).contains { $0.title == "Codex" }
        }
        let button = try XCTUnwrap(ScaleTestSupport.views(NSPopUpButton.self, in: surface.host)
            .first { $0.title == "Codex" })
        XCTAssertTrue(button.isEnabled)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        surface.window.orderFront(nil)
        var invoked = false
        let timer = Timer(timeInterval: 0.05, repeats: false) { _ in
            MainActor.assumeIsolated {
                guard let menu = button.menu else { XCTFail("Native service menu missing"); return }
                defer { menu.cancelTrackingWithoutAnimation() }
                let matches = menu.items.indices.filter { menu.items[$0].title == "Claude" }
                XCTAssertEqual(matches.count, 1)
                guard matches.count == 1, let index = matches.first else { return }
                XCTAssertTrue(menu.items[index].isEnabled)
                XCTAssertNotNil(menu.items[index].action)
                menu.performActionForItem(at: index)
                invoked = true
            }
        }
        RunLoop.main.add(timer, forMode: .eventTracking)
        defer { timer.invalidate() }
        button.performClick(nil)
        XCTAssertTrue(invoked)
        try await surface.waitFor { f.model.pendingProvider == .claude }
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["model_provider"], .string("claude_cli"))
        XCTAssertEqual(f.model.translationProvider, .codex)
        helper.event("completed", id: save.id)
        try f.finishConfiguration(on: helper, configuration: save.config)
        try await surface.waitFor { button.title == "Claude" && f.model.translationProvider == .claude }
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationSaves.count, 1)
    }
}

extension ProductRenderingTests {
    @MainActor
    func testClaudeSettingsRenderInEnglishLightAndChineseDarkWithoutDiscoveryOrModelCalls() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.localReady(configuration: ClaudeProductFixture.configuration())
        f.model.editCustomModelID("future/Claude-Custom")
        for (language, scheme) in [("en", ColorScheme.light), ("zh", ColorScheme.dark)] {
            f.model.interfaceLanguage = language
            _ = try render(Form { TranslationSettingsView(model: f.model).translationSection }
                .formStyle(.grouped), named: "claude-settings-\(language)-\(scheme == .light ? "light" : "dark")",
                           size: NSSize(width: 760, height: 980), scheme: scheme, inspect: { host in
                let buttons = ScaleTestSupport.views(NSPopUpButton.self, in: host)
                XCTAssertTrue(buttons.contains { $0.title == "Claude" })
                XCTAssertTrue(buttons.contains { $0.title == "Haiku" })
                XCTAssertTrue(ScaleTestSupport.views(NSTextField.self, in: host).contains {
                    $0.isEditable && $0.stringValue == "future/Claude-Custom"
                })
            })
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertFalse(helper.messages.contains { $0.payload["operation"] == .string("model_catalog") })
    }
}
