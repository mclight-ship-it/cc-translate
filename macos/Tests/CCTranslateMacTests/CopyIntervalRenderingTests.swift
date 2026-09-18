import XCTest
import AppKit
import SwiftUI
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct CopyIntervalSurface: View {
    @ObservedObject var model: ProbeModel
    var body: some View {
        Form { Section { CopyIntervalSettingsView(model: model) } }
            .formStyle(.grouped)
            .background(Color(nsColor: .windowBackgroundColor))
            .preferredColorScheme(model.preferredColorScheme)
    }
}

final class CopyIntervalInteractionTests: XCTestCase {
    @MainActor
    func testNativeIntervalEntryApplyReadbackAndReopenInBothLanguagesWithoutCLI() async throws {
        for language in ["en", "zh"] {
            let monitor = SelectionMonitorFixture()
            let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(CopyIntervalSurface(model: f.model))
            defer { surface.close() }
            let labels = CopyIntervalSettingsView(model: f.model)
            try await surface.waitForFieldValue("0.5")
            let operations = helper.operations
            try await surface.enterValue("0", draft: { f.model.copyInterval.draft })
            // This fixture has one action. Read its native state without OCR of a dimmed caption.
            try await surface.waitFor {
                let buttons = surface.visibleButtons()
                return buttons.count == 1 && !buttons[0].isEnabled
            }
            XCTAssertEqual(surface.visibleButtons().count, 1)
            XCTAssertFalse(try XCTUnwrap(surface.visibleButtons().first).isEnabled)
            try await surface.enterValue("0.75", draft: { f.model.copyInterval.draft })
            XCTAssertEqual(helper.operations, operations)
            try await surface.buttonWhenReady("apply-copy-interval", labels.applyTitle).press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            XCTAssertEqual(monitor.copyInterval, .standard)
            let save = try XCTUnwrap(helper.configurationSaves.last)
            XCTAssertEqual(save.config["double_press_window"], .number(0.75))
            try CopyIntervalFixture.finish(f, helper)
            try await surface.waitForFieldValue("0.75")
            XCTAssertEqual(f.model.copyInterval.phase, .saved)
            XCTAssertEqual(monitor.copyInterval.seconds, 0.75)
            f.model.stopHelper()
            helper.stopped()
            f.model.openProduct()
            let reopened = try f.ready(configuration: save.config)
            try await surface.waitForFieldValue("0.75")
            XCTAssertEqual(monitor.copyInterval.seconds, 0.75)
            XCTAssertEqual(monitor.starts, 0)
            XCTAssertFalse(monitor.fallback)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertTrue(reopened.configurationSaves.isEmpty)
            XCTAssertTrue(reopened.translations.isEmpty)
        }
    }

    @MainActor
    func testNativeFailedIntervalSaveReloadAndMismatchRetainDraftInBothLanguages() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(CopyIntervalSurface(model: f.model))
            defer { surface.close() }
            let labels = CopyIntervalSettingsView(model: f.model)
            try await surface.waitForFieldValue("0.5")
            try await surface.enterValue("0.75", draft: { f.model.copyInterval.draft })
            try await surface.buttonWhenReady("apply-copy-interval", labels.applyTitle).press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                         payload: ["code": .string("config_io_failed")])
            try await surface.buttonWhenReady("reload-copy-interval", labels.reloadTitle).press()
            try await surface.waitFor { helper.configurationLoads.count == 2 }
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(copyInterval: 0.6))
            try await surface.waitForFieldValue("0.75")
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.6)
            XCTAssertEqual(helper.configurationSaves.count, 1)
            try await surface.buttonWhenReady("apply-copy-interval", labels.applyTitle).press()
            try await surface.waitFor { helper.configurationSaves.count == 2 }
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(copyInterval: 0.8))
            try await surface.waitForFieldValue("0.75")
            XCTAssertEqual(f.model.copyInterval.phase, .differentReadback)
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.8)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testCopyIntervalSettingsRenderEnglishLightAndChineseDarkWithVisibleControls() throws {
        for (language, scheme) in [("en", ColorScheme.light), ("zh", ColorScheme.dark)] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(copyInterval: 0.75))
            f.model.interfaceLanguage = language
            f.model.editCopyInterval("1.0")
            let labels = CopyIntervalSettingsView(model: f.model)
            _ = try render(CopyIntervalSurface(model: f.model),
                           named: "copy-interval-\(language)-\(scheme == .light ? "light" : "dark")",
                           size: NSSize(width: 760, height: 540), scheme: scheme, inspect: { host in
                let apply = try NativeSettingsTestControls.resolve(
                    in: host, identifier: "apply-copy-interval", label: labels.applyTitle, kind: .button)
                XCTAssertTrue(apply.isEnabled)
                XCTAssertGreaterThan(apply.visibleRect.height, 0)
                XCTAssertEqual(apply.visibleRect.height, apply.frame.height, accuracy: 1)
                XCTAssertEqual(apply.visibleRect.width, apply.frame.width, accuracy: 1)
            })
            XCTAssertEqual(f.model.activeCopyInterval.seconds, 0.75)
            XCTAssertTrue(helper.configurationSaves.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }
}
