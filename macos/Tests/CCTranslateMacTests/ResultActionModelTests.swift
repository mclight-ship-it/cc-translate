import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class ResultActionModelTests: XCTestCase {
    private func completed(_ text: String) -> [String: JSONValue] {
        ["text": .string(text), "submitted": .bool(true), "cached": .bool(false),
         "kind": .string("text"), "target_lang": .null, "summarize": .bool(false),
         "history": .string("disabled"), "history_error": .null]
    }

    @MainActor
    private func prepare(_ fixture: ProductTestHarness, original: String = "Original source",
                         primary: String = "Primary translation") throws -> ProductTestHelper {
        fixture.model.interfaceLanguage = "en"
        let helper = try fixture.ready()
        fixture.model.reuseHistory(.init(id: "saved", input: original, output: primary, kind: "code"))
        return helper
    }

    @MainActor
    func testActionsUseImmutablePrimaryOrOriginalNotEditedInputOrEarlierActions() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        fixture.model.interfaceLanguage = "en"
        let helper = try fixture.ready()
        let model = try XCTUnwrap(fixture.model)
        model.input = "Original source"
        model.translate()
        let translation = try XCTUnwrap(helper.translations.last)
        helper.event("completed", id: translation.id, payload: completed("Primary translation"))
        model.input = "Later editor change"

        for action in ResultAction.allCases {
            let previous = model.output
            model.performResultAction(action, targetLanguage: action == .retranslate ? "ja" : nil)
            let sent = try XCTUnwrap(helper.resultActions.last)
            XCTAssertEqual(sent.action, action)
            XCTAssertEqual(sent.text, action.usesOriginalInput ? "Original source" : "Primary translation")
            XCTAssertEqual(sent.language, "en_US")
            XCTAssertEqual(sent.targetLanguage, action == .retranslate ? "ja" : nil)
            XCTAssertTrue(model.output.hasPrefix(previous))
            helper.event("completed", id: sent.id, payload: completed("Section \(action.rawValue)"))
            XCTAssertEqual(model.primaryResult, "Primary translation")
            XCTAssertEqual(model.resultInput, "Original source")
            XCTAssertEqual(model.input, "Later editor change")
            XCTAssertTrue(model.output.hasPrefix(previous))
            XCTAssertTrue(model.output.hasSuffix("Section \(action.rawValue)"))
            XCTAssertTrue(model.canRunResultAction)
        }
        XCTAssertEqual(helper.resultActions.count, 6)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
    }

    @MainActor
    func testStreamBudgetIsPerActionAndFinalCorrectionOnlyReplacesItsOwnSection() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let primary = String(repeating: "p", count: 23_000)
        let helper = try prepare(fixture, primary: primary)
        let model = try XCTUnwrap(fixture.model)
        for action in [ResultAction.concise, .formal] {
            model.performResultAction(action)
            let sent = try XCTUnwrap(helper.resultActions.last)
            helper.event("completed", id: sent.id, payload: completed(String(repeating: "s", count: 23_000)))
        }
        let previous = model.output
        XCTAssertGreaterThan(previous.utf8.count, 65_536)
        model.performResultAction(.summary)
        let sent = try XCTUnwrap(helper.resultActions.last)
        let prefix = model.output
        helper.event("delta", id: sent.id, payload: ["text": .string("partial"), "submitted": .bool(true)])
        try await Task.sleep(nanoseconds: 80_000_000)
        XCTAssertEqual(model.output, prefix + "partial")
        XCTAssertTrue(model.ready)
        XCTAssertTrue(model.active)
        XCTAssertEqual(helper.stopCount, 0)
        helper.event("completed", id: sent.id, payload: completed("corrected final"))
        XCTAssertEqual(model.output, prefix + "corrected final")
        XCTAssertTrue(model.output.hasPrefix(previous))
        XCTAssertEqual(model.primaryResult, primary)
    }

    @MainActor
    func testCancelledAndFailedActionsRetainPrimaryAndLabelPartialSectionsWithoutReplay() throws {
        for terminal in ["cancelled", "failed"] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try prepare(fixture)
            let model = try XCTUnwrap(fixture.model)
            model.performResultAction(.summary)
            let sent = try XCTUnwrap(helper.resultActions.last)
            helper.event("delta", id: sent.id, payload: ["text": .string("partial"), "submitted": .bool(true)])
            var payload: [String: JSONValue] = ["submitted": .bool(true)]
            if terminal == "failed" { payload["code"] = .string("provider_failed") }
            helper.event(terminal, id: sent.id, payload: payload)
            XCTAssertEqual(model.primaryResult, "Primary translation")
            XCTAssertEqual(model.resultInput, "Original source")
            XCTAssertTrue(model.output.hasPrefix("Primary translation"))
            XCTAssertTrue(model.output.contains("partial"))
            XCTAssertTrue(model.output.hasSuffix("[Result action \(terminal)]"))
            XCTAssertEqual(model.productPhase, terminal == "failed" ? .failed : .cancelled)
            XCTAssertTrue(model.canRunResultAction)
            XCTAssertEqual(helper.resultActions.count, 1)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }

    @MainActor
    func testActionPreparationPreservesClickedProfileAndSubsequentEdits() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try prepare(fixture)
        let model = try XCTUnwrap(fixture.model)
        model.modelProfile = "auto"
        model.performResultAction(.formal)
        let save = try XCTUnwrap(helper.configurationSaves.last)
        XCTAssertEqual(save.config["codex_model"], .string("auto"))
        XCTAssertTrue(helper.resultActions.isEmpty)
        model.modelProfile = "auto-fast"
        model.direction = "to_en"
        model.input = "New editor text"
        helper.event("completed", id: save.id, payload: ["saved": .bool(true)])
        try fixture.finishConfiguration(on: helper, configuration: save.config)
        XCTAssertEqual(helper.resultActions.count, 1)
        XCTAssertEqual(helper.resultActions.first?.text, "Primary translation")
        XCTAssertEqual(model.modelProfile, "auto-fast")
        XCTAssertEqual(model.direction, "to_en")
        XCTAssertEqual(model.input, "New editor text")
        XCTAssertEqual(model.primaryResult, "Primary translation")
    }

    @MainActor
    func testClearAndHistoryReuseDiscardLateActionOutputAndBufferedCallbacks() async throws {
        for reuse in [false, true] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            let helper = try prepare(fixture)
            let model = try XCTUnwrap(fixture.model)
            model.performResultAction(.summary)
            let sent = try XCTUnwrap(helper.resultActions.last)
            helper.event("delta", id: sent.id, payload: ["text": .string("late partial"), "submitted": .bool(true)])
            if reuse {
                model.reuseHistory(.init(id: "new", input: "New original", output: "New primary"))
            } else {
                model.clearTranslation()
            }
            helper.event("completed", id: sent.id, payload: completed("late final"))
            try await Task.sleep(nanoseconds: 80_000_000)
            XCTAssertEqual(model.output, reuse ? "New primary" : "")
            XCTAssertEqual(model.primaryResult, reuse ? "New primary" : "")
            XCTAssertEqual(model.resultInput, reuse ? "New original" : "")
            XCTAssertEqual(model.productPhase, reuse ? .completed : .idle)
            XCTAssertTrue(helper.messages.contains {
                $0.type == "cancel" && $0.payload["request_id"] == .string(sent.id)
            })
            if reuse {
                model.performResultAction(.concise)
                XCTAssertEqual(helper.resultActions.last?.text, "New primary")
            }
        }
    }

    @MainActor
    func testNewTranslationWaitsForActionTerminalNotCancelAcknowledgement() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try prepare(fixture)
        let model = try XCTUnwrap(fixture.model)
        model.performResultAction(.summary)
        let action = try XCTUnwrap(helper.resultActions.last)
        model.input = "Next source"
        model.translate()
        let cancel = try XCTUnwrap(helper.messages.last)
        XCTAssertEqual(cancel.payload["request_id"], .string(action.id))
        helper.event("completed", id: cancel.id, payload: ["cancel_requested": .bool(true)])
        XCTAssertTrue(helper.translations.isEmpty)
        helper.event("cancelled", id: action.id, payload: ["submitted": .bool(true)])
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.first?.text, "Next source")
        XCTAssertEqual(model.output, "")
        XCTAssertEqual(model.primaryResult, "")
        helper.event("completed", id: action.id, payload: completed("stale callback"))
        XCTAssertEqual(model.output, "")
        XCTAssertEqual(model.resultInput, "Next source")
    }

    @MainActor
    func testUnknownActionPreservesPrimaryAndExplicitReconnectDoesNotReplay() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try prepare(fixture)
        let model = try XCTUnwrap(fixture.model)
        model.performResultAction(.summary)
        helper.failure(.translationOutcomeUnknown)
        XCTAssertTrue(model.productMessage.contains("Result action outcome unknown"))
        XCTAssertFalse(model.productMessage.contains("history may have changed"))
        XCTAssertEqual(model.primaryResult, "Primary translation")
        helper.stopped()
        let previous = model.output
        model.openProduct()
        let replacement = try fixture.ready()
        XCTAssertFalse(helper === replacement)
        XCTAssertEqual(model.output, previous)
        XCTAssertEqual(model.primaryResult, "Primary translation")
        XCTAssertTrue(replacement.resultActions.isEmpty)
        XCTAssertTrue(replacement.translations.isEmpty)
        XCTAssertEqual(helper.resultActions.count, 1)
    }

    @MainActor
    func testMissingPrimaryOrInvalidActionInputsDoNotStartHelper() throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let model = try XCTUnwrap(fixture.model)
        model.performResultAction(.concise)
        XCTAssertEqual(model.productPhase, .failed)
        XCTAssertTrue(fixture.helpers.isEmpty)
        model.reuseHistory(.init(id: "saved", input: "Original", output: "Primary"))
        model.performResultAction(.retranslate, targetLanguage: "unsupported")
        XCTAssertTrue(fixture.helpers.isEmpty)
        model.performResultAction(.formal, targetLanguage: "en")
        XCTAssertTrue(fixture.helpers.isEmpty)
        model.reuseHistory(.init(id: "empty-source", input: "", output: "Primary"))
        model.performResultAction(.asText)
        XCTAssertTrue(fixture.helpers.isEmpty)
    }
}
