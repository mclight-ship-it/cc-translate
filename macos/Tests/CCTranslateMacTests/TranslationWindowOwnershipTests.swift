import AppKit
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class TranslationWindowOwnershipTests: XCTestCase {
    @MainActor
    private func withApplication(
        _ body: @MainActor (AppDelegate, ProductTestHarness) async throws -> Void
    ) async throws {
        _ = NSApplication.shared
        let focus = NativeTestWindowFocus()
        let menu = NSApp.mainMenu
        let windowsMenu = NSApp.windowsMenu
        let product = try ProductTestHarness()
        let application = AppDelegate(model: product.model, capture: CaptureModel(),
            diagnostics: NativePresentationTestSupport.offline(product.preferences, persists: false),
            loginItems: LoginItemModel(service: LoginItemTestService()))
        application.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        defer {
            for panel in [application.inputPanel, application.resultPanel, application.quickInputPanel]
                .compactMap({ $0 }) {
                focus.close(panel)
            }
            application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            NSApp.mainMenu = menu
            NSApp.windowsMenu = windowsMenu
            product.cleanUp()
        }
        try await body(application, product)
    }

    @MainActor
    func testClosingWorkspaceCancelsItsPreparingAndRunningRequestOnEveryPage() async throws {
        for running in [false, true] {
            for section in [ProductSection.translator, .history, .dictionary, .settings, .about] {
                try await withApplication { application, product in
                    application.navigate(to: .translator)
                    if running { try product.ready() }
                    product.model.input = "Synthetic workspace-owned request."
                    product.model.translate(useCache: false)
                    let helper = try XCTUnwrap(product.helpers.last)
                    XCTAssertEqual(product.model.active, running)
                    XCTAssertFalse(product.model.translationUsesResultPanel)
                    application.navigate(to: section)
                    let window = try XCTUnwrap(application.inputPanel)
                    window.performClose(nil)
                    XCTAssertFalse(window.isVisible)
                    if running {
                        let request = try XCTUnwrap(helper.translations.last)
                        XCTAssertTrue(helper.messages.contains {
                            $0.type == "cancel" && $0.payload["request_id"] == .string(request.id)
                        }, "Closing the workspace must cancel its request even on \(section.rawValue).")
                    } else {
                        XCTAssertFalse(product.model.preparing)
                        try product.ready()
                        XCTAssertTrue(product.helpers.allSatisfy { $0.translations.isEmpty })
                    }
                }
            }
        }
    }

    @MainActor
    func testQuickResultFollowupsStayOwnedByResultWindowAcrossNewIntents() async throws {
        for retry in [false, true] {
            try await withApplication { application, product in
                let helper = try product.ready()
                product.model.input = "Synthetic quick-input source."
                product.model.translate(useCache: false, inResultPanel: true)
                let initial = try XCTUnwrap(helper.translations.last)
                let initialIntent = product.model.translationIntentID
                helper.event("completed", id: initial.id, payload: ScaleTestSupport.result("Synthetic answer"))
                try await CaptureProductFixture.waitFor { !product.model.active }
                if retry { product.model.retranslate() }
                else { product.model.performResultAction(.concise) }
                let followup = try XCTUnwrap(retry ? helper.translations.last?.id : helper.resultActions.last?.id)
                XCTAssertNotEqual(followup, initial.id)
                XCTAssertNotEqual(product.model.translationIntentID, initialIntent)
                XCTAssertTrue(product.model.translationUsesResultPanel)
                XCTAssertEqual(product.model.translationOrigin, "text")
                XCTAssertTrue(product.model.active)
                let result = try XCTUnwrap(application.resultPanel)
                XCTAssertTrue(result.isVisible)
                application.navigate(to: .translator)
                try XCTUnwrap(application.inputPanel).performClose(nil)
                XCTAssertFalse(helper.messages.contains {
                    $0.type == "cancel" && $0.payload["request_id"] == .string(followup)
                }, "The unrelated workspace must not cancel a quick-result follow-up.")
                result.performClose(nil)
                XCTAssertTrue(helper.messages.contains {
                    $0.type == "cancel" && $0.payload["request_id"] == .string(followup)
                })
                helper.event("cancelled", id: followup)
                try await CaptureProductFixture.waitFor { !product.model.active }
                product.model.onTranslationResult?("Late callback")
                XCTAssertFalse(result.isVisible)
                application.navigate(to: .translator)
                product.model.input = "New workspace request."
                product.model.translate(useCache: false)
                XCTAssertFalse(product.model.translationUsesResultPanel)
                XCTAssertFalse(result.isVisible, "A new workspace intent must not reopen the old quick popup.")
            }
        }
    }

    @MainActor
    func testClearingQuickResultResetsWindowOwnership() async throws {
        try await withApplication { application, product in
            let helper = try product.ready()
            product.model.input = "Synthetic quick result."
            product.model.translate(useCache: false, inResultPanel: true)
            let request = try XCTUnwrap(helper.translations.last)
            helper.event("completed", id: request.id, payload: ScaleTestSupport.result("Synthetic answer"))
            try await CaptureProductFixture.waitFor { !product.model.active }
            XCTAssertTrue(product.model.translationUsesResultPanel)
            product.model.clearTranslation()
            XCTAssertFalse(product.model.translationUsesResultPanel)
            XCTAssertEqual(product.model.translationOrigin, "text")
            application.resultPanel?.performClose(nil)
            XCTAssertTrue(helper.messages.allSatisfy { $0.type != "cancel" })
        }
    }
}
