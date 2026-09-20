import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class FeedbackSettingsTests: XCTestCase {
    @MainActor
    func testMenuActionsStayTextOnlyWithoutCommandNAndKeepToggleState() throws {
        _ = NSApplication.shared
        let main = NSApp.mainMenu
        let windows = NSApp.windowsMenu
        let f = try ProductTestHarness(savedCLI: false)
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: ProbeModel(persistsPreferences: false))
        defer {
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            NSApp.mainMenu = main
            NSApp.windowsMenu = windows
            f.cleanUp()
        }

        extension ProductRenderingTests {
            @MainActor
            func testScreenshotImageModeSettingsRenderInBothLanguages() throws {
                for (language, scheme) in [("en", ColorScheme.light), ("zh", ColorScheme.dark)] {
                    let f = try ProductTestHarness(savedCLI: false)
                    defer { f.cleanUp() }
                    f.model.loadPresentation()
                    f.model.interfaceLanguage = language
                    f.model.chooseCaptureTranslationMode(.image)
                    _ = try render(Form {
                        CaptureShortcutSettingsSection(model: f.model, shortcut: f.model.captureShortcut)
                    }.formStyle(.grouped).background(Color(nsColor: .windowBackgroundColor)),
                        named: "screenshot-image-mode-\(language)", size: NSSize(width: 760, height: 540),
                        scheme: scheme, inspect: { host in
                            let title = f.model.text("Send image", "发送图片")
                            let picker = try XCTUnwrap(InputLimitNativeViews.views(NSPopUpButton.self, in: host).first {
                                $0.itemTitles.contains(title)
                            })
                            XCTAssertEqual(picker.titleOfSelectedItem, title)
                            XCTAssertGreaterThan(picker.visibleRect.height, 0)
                            XCTAssertEqual(picker.visibleRect.height, picker.bounds.height, accuracy: 1)
                        })
                    XCTAssertTrue(f.helpers.isEmpty)
                }
            }
        }
        app.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        let menu = try XCTUnwrap(app.statusItem?.menu)
        for item in menu.items where !item.isSeparatorItem {
            item.image = NSImage(size: NSSize(width: 16, height: 16))
            XCTAssertNil(item.image, "System-supplied action images must not indent individual rows.")
            XCTAssertNotEqual(item.keyEquivalent, "n")
        }
        let monitor = try XCTUnwrap(menu.item(withTag: 10))
        monitor.state = .on
        XCTAssertEqual(monitor.state, .on, "Removing action icons must retain toggle checkmarks.")
        let settings = try XCTUnwrap(menu.items.first { $0.action == Selector("openSettings") })
        XCTAssertEqual(settings.keyEquivalent, ",")
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testPermissionFailureOpensShortcutsInBothNewAndExistingSettingsWindow() async throws {
        _ = NSApplication.shared
        let main = NSApp.mainMenu
        let windows = NSApp.windowsMenu
        let focus = NativeTestWindowFocus()
        let monitor = SelectionMonitorFixture()
        monitor.failure = .permissionDenied
        let f = try ProductTestHarness(selectionMonitor: monitor)
        let helper = try f.ready()
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: ProbeModel(persistsPreferences: false))
        defer {
            if let panel = app.settingsPanel { focus.close(panel) }
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            NSApp.mainMenu = main
            NSApp.windowsMenu = windows
            f.cleanUp()
        }
        app.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        let item = try XCTUnwrap(app.statusItem?.menu?.item(withTag: 10))
        let action = try XCTUnwrap(item.action)
        XCTAssertTrue(NSApp.sendAction(action, to: item.target, from: item))
        let panel = try XCTUnwrap(app.settingsPanel)
        let content = try XCTUnwrap(panel.contentView)
        try await CaptureProductFixture.waitFor {
            InputLimitNativeViews.views(NSSegmentedControl.self, in: content).first?.selectedSegment == 1
        }
        f.model.editCustomModelID("fixture/preserve-settings-draft")
        app.showSettings(pane: .more)
        try await CaptureProductFixture.waitFor {
            InputLimitNativeViews.views(NSSegmentedControl.self, in: content).first?.selectedSegment == 3
        }
        XCTAssertTrue(NSApp.sendAction(action, to: item.target, from: item))
        try await CaptureProductFixture.waitFor {
            InputLimitNativeViews.views(NSSegmentedControl.self, in: content).first?.selectedSegment == 1
        }
        XCTAssertTrue(app.settingsPanel === panel)
        XCTAssertTrue(panel.contentView === content)
        XCTAssertEqual(f.model.modelSettings.draft, "fixture/preserve-settings-draft")
        XCTAssertFalse(f.model.monitorEnabled)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.configurationSaves.isEmpty)
    }

    @MainActor
    func testScreenshotModeDefaultsToLocalTextAndPersistsWithoutStartingServices() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        XCTAssertEqual(f.model.captureTranslationMode, .text)
        f.model.chooseCaptureTranslationMode(.image)
        XCTAssertEqual(f.preferences.string(forKey: CaptureTranslationMode.preferenceKey), "image")
        let reopened = ProbeModel(preferences: f.preferences)
        reopened.loadPresentation()
        XCTAssertEqual(reopened.captureTranslationMode, .image)
        reopened.chooseCaptureTranslationMode(.text)
        XCTAssertEqual(f.preferences.string(forKey: CaptureTranslationMode.preferenceKey), "text")
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.runtimeRequests, 0)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testUnknownScreenshotModeDoesNotOptIntoImageSubmission() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.preferences.set("unrecognized-future-mode", forKey: CaptureTranslationMode.preferenceKey)
        f.model.loadPresentation()
        XCTAssertEqual(f.model.captureTranslationMode, .text)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testScreenshotModePickerChangesTheSavedPreferenceThroughNativeControl() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.loadPresentation()
        f.model.interfaceLanguage = "en"
        let surface = NativeSettingsTestHost(Form {
            CaptureShortcutSettingsSection(model: f.model, shortcut: f.model.captureShortcut)
        }.formStyle(.grouped))
        defer { surface.close() }
        let picker = try XCTUnwrap(InputLimitNativeViews.views(NSPopUpButton.self, in: surface.host).first {
            $0.itemTitles.contains("Send image")
        })
        picker.selectItem(withTitle: "Send image")
        XCTAssertTrue(picker.sendAction(try XCTUnwrap(picker.action), to: picker.target))
        try await surface.waitFor { f.model.captureTranslationMode == .image }
        XCTAssertEqual(f.preferences.string(forKey: CaptureTranslationMode.preferenceKey), "image")
        XCTAssertFalse(f.model.captureShortcut.enabled)
        XCTAssertTrue(f.helpers.isEmpty)
    }
}
