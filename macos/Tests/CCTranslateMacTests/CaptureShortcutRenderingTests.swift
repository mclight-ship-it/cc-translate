import XCTest
import AppKit
import SwiftUI
@testable import CCTranslateMac

@MainActor
private struct CaptureShortcutSurface: View {
    @ObservedObject var model: ProbeModel
    var body: some View {
        Form { CaptureShortcutSettingsSection(model: model, shortcut: model.captureShortcut) }
            .formStyle(.grouped)
            .background(Color(nsColor: .windowBackgroundColor))
            .preferredColorScheme(model.preferredColorScheme)
    }
}

@MainActor
private enum CaptureShortcutControls {
    static func toggle(in host: NSView, model: ProbeModel) async throws -> NativeSettingsTestControl {
        let labels = CaptureShortcutSettingsSection(model: model, shortcut: model.captureShortcut)
        return try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: "screenshot-shortcut", label: labels.toggleTitle, kind: .toggle,
            authoredCaption: true)
    }
}

final class CaptureShortcutInteractionTests: XCTestCase {
    @MainActor
    func testNativeBilingualTogglePersistsAndReopensWithoutCLIHelperOrSelectionMonitor() async throws {
        for language in ["en", "zh"] {
            let registrar = PasteTestRegistrar()
            let monitor = SelectionMonitorFixture()
            let f = try ProductTestHarness(savedCLI: false, selectionMonitor: monitor, captureRegistrar: registrar)
            defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
            f.model.loadPresentation()
            f.model.interfaceLanguage = language
            let surface = NativeSettingsTestHost(CaptureShortcutSurface(model: f.model), size: NSSize(width: 760, height: 540))
            defer { surface.close() }
            let toggle = try await CaptureShortcutControls.toggle(in: surface.host, model: f.model)
            XCTAssertEqual(toggle.state, .off)
            XCTAssertTrue(toggle.isEnabled)
            try await toggle.press()
            try await surface.waitFor { toggle.state == .on && f.model.captureShortcut.registration == .registered }
            XCTAssertEqual(registrar.registrations, 1)
            XCTAssertTrue(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
            surface.close()
            f.model.closePanel()
            f.model.loadPresentation()
            XCTAssertEqual(registrar.leases.first?.releases, 0)
            let reopened = NativeSettingsTestHost(CaptureShortcutSurface(model: f.model), size: NSSize(width: 760, height: 540))
            defer { reopened.close() }
            let reopenedToggle = try await CaptureShortcutControls.toggle(in: reopened.host, model: f.model)
            XCTAssertEqual(reopenedToggle.state, .on)
            try await reopenedToggle.press()
            try await reopened.waitFor { reopenedToggle.state == .off && f.model.captureShortcut.registration == .off }
            XCTAssertEqual(registrar.registrations, 1)
            XCTAssertEqual(registrar.leases.first?.releases, 1)
            XCTAssertFalse(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
            XCTAssertEqual(monitor.starts, 0)
            XCTAssertFalse(monitor.fallback)
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.runtimeRequests, 0)
            XCTAssertEqual(f.locatorRequests, 0)
        }
    }

    @MainActor
    func testNativeBilingualConflictRetryAndFailedReleaseFollowExplicitUserIntent() async throws {
        for language in ["en", "zh"] {
            let registrar = PasteTestRegistrar()
            registrar.failure = .conflict
            let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
            defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
            f.model.loadPresentation()
            f.model.interfaceLanguage = language
            let shortcut = f.model.captureShortcut
            let labels = CaptureShortcutSettingsSection(model: f.model, shortcut: shortcut)
            let surface = NativeSettingsTestHost(CaptureShortcutSurface(model: f.model), size: NSSize(width: 760, height: 540))
            defer { surface.close() }
            let toggle = try await CaptureShortcutControls.toggle(in: surface.host, model: f.model)
            try await toggle.press()
            try await surface.waitFor { toggle.state == .on && shortcut.registration == .failed(.conflict) }
            XCTAssertEqual(registrar.registrations, 1)
            registrar.failure = nil
            try await NativeSettingsTestControls.remainingActionWhenReady(
                in: surface.host, excluding: toggle,
                identifier: "retry-screenshot-shortcut", label: labels.retryTitle).press()
            try await surface.waitFor { shortcut.registration == .registered && toggle.state == .on }
            XCTAssertEqual(registrar.registrations, 2)
            let lease = try XCTUnwrap(registrar.leases.last)
            lease.releaseError = .releaseFailed(-50)
            try await toggle.press()
            try await surface.waitFor { toggle.state == .off && shortcut.registration == .failed(.releaseFailed(-50)) }
            XCTAssertFalse(f.preferences.bool(forKey: CaptureShortcutModel.preferenceKey))
            lease.releaseError = nil
            try await NativeSettingsTestControls.remainingActionWhenReady(
                in: surface.host, excluding: toggle,
                identifier: "retry-screenshot-shortcut", label: labels.retryTitle).press()
            try await surface.waitFor { shortcut.registration == .off }
            XCTAssertEqual(registrar.registrations, 2)
            XCTAssertEqual(lease.releases, 2)
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.runtimeRequests, 0)
            XCTAssertEqual(f.locatorRequests, 0)
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testScreenshotShortcutRendersOffReadyAndConflictInEnglishLightAndChineseDark() throws {
        for (name, language, scheme, enabled, conflict) in [
            ("screenshot-shortcut-en-light", "en", ColorScheme.light, false, false),
            ("screenshot-shortcut-zh-dark", "zh", ColorScheme.dark, true, false),
            ("screenshot-shortcut-conflict-en-light", "en", ColorScheme.light, true, true)
        ] {
            let registrar = PasteTestRegistrar()
            registrar.failure = conflict ? .conflict : nil
            let f = try ProductTestHarness(savedCLI: false, captureRegistrar: registrar)
            defer { f.model.captureShortcut.shutdown(); f.cleanUp() }
            f.model.loadPresentation()
            f.model.interfaceLanguage = language
            f.model.captureShortcut.choose(enabled)
            let labels = CaptureShortcutSettingsSection(model: f.model, shortcut: f.model.captureShortcut)
            _ = try render(CaptureShortcutSurface(model: f.model), named: name,
                           size: NSSize(width: 760, height: 540), scheme: scheme, inspect: { host in
                let toggle = try NativeSettingsTestControls.resolve(
                    in: host, identifier: "screenshot-shortcut", label: labels.toggleTitle, kind: .toggle,
                    authoredCaption: true)
                XCTAssertTrue(toggle.isEnabled)
                XCTAssertEqual(toggle.state, enabled ? .on : .off)
                XCTAssertGreaterThan(toggle.visibleRect.height, 0)
                XCTAssertEqual(toggle.visibleRect.height, toggle.frame.height, accuracy: 1)
                XCTAssertEqual(toggle.visibleRect.width, toggle.frame.width, accuracy: 1)
                if conflict {
                    let retry = try NativeSettingsTestControls.resolve(
                        in: host, identifier: "retry-screenshot-shortcut", label: labels.retryTitle, kind: .button,
                        authoredCaption: true)
                    XCTAssertTrue(retry.isEnabled)
                    XCTAssertGreaterThan(retry.visibleRect.height, 0)
                    XCTAssertEqual(retry.visibleRect.height, retry.frame.height, accuracy: 1)
                    XCTAssertEqual(retry.visibleRect.width, retry.frame.width, accuracy: 1)
                }
            })
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.runtimeRequests, 0)
            XCTAssertEqual(f.locatorRequests, 0)
        }
    }
}
