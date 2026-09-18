import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class NativeResultPlacementGeometryTests: XCTestCase {
    private let window = NSRect(x: 100, y: 100, width: 590, height: 422)
    private let screen = NSRect(x: 0, y: 40, width: 1440, height: 836)

    func testFirstUseCentersAndExistingPositionKeepsCurrentWindowSize() throws {
        let first = try XCTUnwrap(NativeResultPlacement.remembered.frame(
            current: window, remembered: nil, pointer: NSPoint(x: 100, y: 100), visibleScreens: [screen]))
        XCTAssertEqual(first, NSRect(x: 425, y: 247, width: 590, height: 422))
        let old = NSRect(x: 33, y: 80, width: 700, height: 600)
        let restored = try XCTUnwrap(NativeResultPlacement.remembered.frame(
            current: window, remembered: old, pointer: .zero, visibleScreens: [screen]))
        XCTAssertEqual(restored.origin, old.origin)
        XCTAssertEqual(restored.size, window.size, "Position preferences do not override the current window size.")
    }

    func testCenterUsesPointerScreenIncludingNegativeOriginsAndMenuBarGap() throws {
        let left = NSRect(x: -1920, y: -200, width: 1920, height: 1050)
        for point in [NSPoint(x: -100, y: 300), NSPoint(x: -100, y: 870)] {
            let frame = try XCTUnwrap(NativeResultPlacement.center.frame(
                current: window, remembered: window, pointer: point, visibleScreens: [screen, left]))
            XCTAssertEqual(frame.midX, left.midX)
            XCTAssertEqual(frame.midY, left.midY)
            XCTAssertTrue(left.contains(frame))
        }
    }

    func testRememberedScreenWinsOverPointerAndDisconnectedDisplayReturnsOnScreen() throws {
        let left = NSRect(x: -1440, y: 40, width: 1440, height: 836)
        let previous = NSRect(x: -1200, y: 120, width: 590, height: 422)
        let retained = try XCTUnwrap(NativeResultPlacement.remembered.frame(
            current: window, remembered: previous, pointer: NSPoint(x: 500, y: 500), visibleScreens: [screen, left]))
        XCTAssertEqual(retained, previous)
        let relocated = try XCTUnwrap(NativeResultPlacement.remembered.frame(
            current: window, remembered: previous, pointer: NSPoint(x: 500, y: 500), visibleScreens: [screen]))
        XCTAssertTrue(screen.contains(relocated))
        XCTAssertEqual(relocated.origin, NSPoint(x: 0, y: 120))
    }

    func testPointerPlacementFlipsAtRightAndBottomEdgesWithoutCoveringPointer() throws {
        for point in [NSPoint(x: 200, y: 700), NSPoint(x: 1400, y: 700), NSPoint(x: 200, y: 60)] {
            let frame = try XCTUnwrap(NativeResultPlacement.pointer.frame(
                current: window, remembered: window, pointer: point, visibleScreens: [screen]))
            XCTAssertTrue(screen.contains(frame))
            XCTAssertFalse(frame.contains(point))
            XCTAssertEqual(frame.size, window.size)
        }
        let frame = try XCTUnwrap(NativeResultPlacement.pointer.frame(
            current: window, remembered: nil, pointer: NSPoint(x: 200, y: 700), visibleScreens: [screen]))
        XCTAssertEqual(frame.origin, NSPoint(x: 216, y: 262))
    }

    func testSmallDisplayKeepsWholeFrameAndTitlebarInVisibleArea() throws {
        let small = NSRect(x: 20, y: -400, width: 400, height: 300)
        for mode in NativeResultPlacement.allCases {
            let frame = try XCTUnwrap(mode.frame(current: window, remembered: window,
                                                 pointer: NSPoint(x: 100, y: -100), visibleScreens: [small]))
            XCTAssertEqual(frame, small)
        }
    }

    func testMissingDisplayAndInvalidGeometryCannotProduceAPlacement() {
        XCTAssertNil(NativeResultPlacement.center.frame(current: window, remembered: nil,
                                                         pointer: .zero, visibleScreens: []))
        XCTAssertNil(NativeResultPlacement.center.frame(current: .zero, remembered: nil,
                                                         pointer: .zero, visibleScreens: [screen]))
        XCTAssertNil(NativeResultPlacement.pointer.frame(current: window, remembered: nil,
            pointer: NSPoint(x: CGFloat.infinity, y: 0), visibleScreens: [screen]))
        for invalid in [NSRect(x: 0, y: 0, width: -1, height: 200),
                        NSRect(x: 0, y: 0, width: 200, height: -1)] {
            XCTAssertNil(NativeResultPlacement.center.frame(current: invalid, remembered: nil,
                                                             pointer: .zero, visibleScreens: [screen]))
            XCTAssertNil(NativeResultPlacement.center.frame(current: window, remembered: nil,
                                                             pointer: .zero, visibleScreens: [invalid]))
        }
        for raw in [nil, "", "invalid", "{{0, 0}, {0, 200}}", "{{0, 0}, {-1, 200}}", "{{0, 0}, {200, -1}}"] {
            XCTAssertNil(NativeResultPlacement.restoredFrame(raw))
        }
        XCTAssertEqual(NativeResultPlacement.restoredFrame(NSStringFromRect(window)), window)
    }
}

final class NativeResultPlacementModelTests: XCTestCase {
    @MainActor
    func testAllModesAndPositionPersistReopenWithoutServicesOrBusinessConfiguration() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        XCTAssertEqual(f.model.resultPlacement, .remembered)
        XCTAssertNil(f.model.rememberedResultFrame)
        XCTAssertNil(f.preferences.object(forKey: NativeResultPlacement.preferenceKey))
        let frame = NSRect(x: -1200, y: 50, width: 600, height: 450)
        for mode in NativeResultPlacement.allCases {
            f.model.resultPlacement = mode
            f.model.persistPresentation()
            f.model.rememberResultFrame(frame)
            let reopened = NativePresentationTestSupport.offline(f.preferences)
            reopened.loadPresentation()
            XCTAssertEqual(reopened.resultPlacement, mode)
            XCTAssertEqual(reopened.rememberedResultFrame, frame)
            f.preferences.set("invalid", forKey: NativeResultPlacement.preferenceKey)
            reopened.loadPresentation()
            XCTAssertEqual(reopened.resultPlacement, mode)
            XCTAssertFalse(reopened.hasProcesses)
            XCTAssertFalse(reopened.monitorEnabled)
            XCTAssertEqual(reopened.permissions, "Not checked.")
        }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testInvalidPreferencesRemainUnmodifiedAndDiagnosticOptOutDoesNotReadOrWrite() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set("invalid", forKey: NativeResultPlacement.preferenceKey)
        f.preferences.set("invalid", forKey: NativeResultPlacement.frameKey)
        f.model.loadPresentation()
        XCTAssertEqual(f.model.resultPlacement, .remembered)
        XCTAssertNil(f.model.rememberedResultFrame)
        XCTAssertEqual(f.preferences.string(forKey: NativeResultPlacement.frameKey), "invalid")
        f.preferences.set("pointer", forKey: NativeResultPlacement.preferenceKey)
        let model = NativePresentationTestSupport.offline(f.preferences, persists: false)
        model.loadPresentation()
        XCTAssertEqual(model.resultPlacement, .remembered)
        XCTAssertNil(model.rememberedResultFrame)
        model.resultPlacement = .center
        model.persistPresentation()
        model.rememberResultFrame(NSRect(x: 20, y: 40, width: 500, height: 400))
        XCTAssertEqual(f.preferences.string(forKey: NativeResultPlacement.preferenceKey), "pointer")
        XCTAssertEqual(f.preferences.string(forKey: NativeResultPlacement.frameKey), "invalid")
    }

    @MainActor
    func testPreferenceChangeDoesNotCancelOrReplayActiveTranslation() async throws {
        let f = try ProductTestHarness()
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.input = "Synthetic translation stays active."
        f.model.translate()
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("delta", id: request.id, payload: ["text": .string("Partial"), "submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { f.model.output == "Partial" }
        let operations = helper.operations
        for mode in NativeResultPlacement.allCases {
            f.model.resultPlacement = mode
            f.model.persistPresentation()
            f.model.rememberResultFrame(NSRect(x: 10, y: 40, width: 590, height: 400))
            XCTAssertEqual(helper.operations, operations)
            XCTAssertTrue(f.model.active)
            XCTAssertEqual(f.model.output, "Partial")
            XCTAssertTrue(helper.configurationSaves.isEmpty)
        }
    }

    @MainActor
    func testRealNonactivatingResultPanelAppliesPreferenceButDoesNotJumpOnStreamingUpdate() async throws {
        _ = NSApplication.shared
        let focus = NativeTestWindowFocus()
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        f.model.resultPlacement = .center
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(source: source, notificationCenter: NotificationCenter()))
        let application = AppDelegate(model: f.model, capture: capture,
                                      diagnostics: ProbeModel(persistsPreferences: false))
        application.showResult(reposition: true)
        let panel = try XCTUnwrap(application.resultPanel)
        defer { focus.close(panel) }
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
        XCTAssertTrue(panel.delegate === application)
        XCTAssertTrue(panel.isVisible)
        let screen = try XCTUnwrap(panel.screen)
        XCTAssertTrue(screen.visibleFrame.contains(panel.frame))
        XCTAssertEqual(panel.frame.midX, screen.visibleFrame.midX, accuracy: 1)
        XCTAssertEqual(panel.frame.midY, screen.visibleFrame.midY, accuracy: 1)
        panel.setFrameOrigin(NSPoint(x: panel.frame.minX + 20, y: panel.frame.minY + 20))
        let moved = panel.frame
        try await CaptureProductFixture.waitFor { f.model.rememberedResultFrame == moved }
        XCTAssertEqual(f.model.rememberedResultFrame, moved)
        application.showResult()
        XCTAssertEqual(panel.frame, moved, "Stream updates must not apply the placement policy again.")
        panel.orderOut(nil)
        f.model.resultPlacement = .remembered
        application.showResult()
        XCTAssertEqual(panel.frame, moved)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
    }
}

extension ProductRenderingTests {
    @MainActor
    func testResultPositionPickerRendersNativeLocalizedPreferencesWithoutCLI() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        for (language, mode, scheme) in [
            ("en", NativeResultPlacement.remembered, ColorScheme.light),
            ("zh", NativeResultPlacement.pointer, ColorScheme.dark),
            ("en", NativeResultPlacement.center, ColorScheme.dark)
        ] {
            f.model.interfaceLanguage = language
            f.model.resultPlacement = mode
            _ = try render(
                Form { NativeResultPlacementPicker(model: f.model) }.formStyle(.grouped)
                    .background(Color(nsColor: .windowBackgroundColor)),
                named: "result-position-\(mode.rawValue)-\(language)-\(scheme == .light ? "light" : "dark")",
                size: NSSize(width: 640, height: 180), scheme: scheme,
                inspect: { host in
                    let pickers = ScaleTestSupport.views(NSPopUpButton.self, in: host)
                    XCTAssertEqual(pickers.count, 1)
                    let picker = try XCTUnwrap(pickers.first)
                    XCTAssertTrue(picker.isEnabled)
                    XCTAssertGreaterThan(picker.visibleRect.width, 0)
                    XCTAssertGreaterThan(picker.visibleRect.height, 0)
                })
        }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
    }
}
