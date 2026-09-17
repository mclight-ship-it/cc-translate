import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class AboutApplicationTests: XCTestCase {
    @MainActor
    func testNativeMenuAndProductionSettingsEntryReachSameIndependentWindowWithoutCLI() async throws {
        _ = NSApplication.shared
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let product = try ProductTestHarness(savedCLI: false)
        defer { product.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let capture = CaptureModel(screen: ScreenProbe(source: source, notificationCenter: NotificationCenter()))
        let about = AboutModel(resources: bundle.resources)
        let application = AppDelegate(model: product.model, capture: capture,
                                      diagnostics: ProbeModel(persistsPreferences: false), about: about)
        let priorMenu = NSApp.mainMenu
        defer {
            application.aboutPanel?.close()
            NSApp.mainMenu = priorMenu
        }
        application.configureMenus()
        XCTAssertNil(application.aboutPanel)
        XCTAssertEqual(about.phase, .idle)
        let menu = try XCTUnwrap(NSApp.mainMenu?.items.first?.submenu)
        let item = try XCTUnwrap(menu.items.first { $0.action == #selector(AppDelegate.openAbout) })
        XCTAssertTrue(application.validateMenuItem(item))
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        await about.loadTask?.value
        let firstWindow = try XCTUnwrap(application.aboutPanel)
        XCTAssertTrue(firstWindow.isVisible)
        XCTAssertEqual(firstWindow.contentMinSize, NSSize(width: 660, height: 520))
        XCTAssertEqual(about.overview.info?.version, "9.8.7")
        let settings = application.settingsContent()
        settings.showAbout()
        XCTAssertTrue(application.aboutPanel === firstWindow)
        let submit = NSMenuItem(title: "Translate", action: Selector("submitInput"), keyEquivalent: "\r")
        XCTAssertFalse(application.validateMenuItem(submit))
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(product.helpers.isEmpty)
        XCTAssertEqual(product.runtimeRequests, 0)
        XCTAssertEqual(product.locatorRequests, 0)
        firstWindow.performClose(nil)
        XCTAssertNil(application.aboutPanel)
        XCTAssertEqual(about.phase, .idle)
        settings.showAbout()
        await about.loadTask?.value
        XCTAssertFalse(application.aboutPanel === firstWindow)
        XCTAssertEqual(about.phase, .loaded)
        XCTAssertTrue(product.helpers.isEmpty)
    }

    @MainActor
    func testAboutEscapeClosePreservesActiveTranslationCaptureAndOriginalEditorState() async throws {
        _ = NSApplication.shared
        let bundle = try AboutBundleFixture()
        defer { bundle.cleanUp() }
        let product = try ProductTestHarness()
        defer { product.cleanUp() }
        let helper = try product.ready()
        product.model.input = "A synthetic translation remains in progress."
        product.model.translate()
        XCTAssertTrue(product.model.active)
        let operations = helper.operations
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let screen = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        capture.start()
        defer { capture.cancel(); source.finishCapture() }
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        let captureTask = try XCTUnwrap(screen.captureTask)
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 200))
        editor.string = "Keep the original editor selection"
        editor.setSelectedRange(NSRange(location: 5, length: 3))
        let original = NSWindow(contentRect: editor.frame, styleMask: [.titled, .closable],
                                backing: .buffered, defer: false)
        original.isReleasedWhenClosed = false
        original.contentView = editor
        XCTAssertTrue(original.makeFirstResponder(editor))
        defer { original.close() }
        let about = AboutModel(resources: bundle.resources)
        let application = AppDelegate(model: product.model, capture: capture,
                                      diagnostics: ProbeModel(persistsPreferences: false), about: about)
        application.openAbout()
        defer { application.aboutPanel?.close() }
        await about.loadTask?.value
        let window = try XCTUnwrap(application.aboutPanel)
        application.windowWillClose(Notification(name: NSWindow.willCloseNotification, object: original))
        XCTAssertTrue(application.aboutPanel === window)
        XCTAssertEqual(about.phase, .loaded)
        window.cancelOperation(nil)
        XCTAssertNil(application.aboutPanel)
        XCTAssertEqual(about.phase, .idle)
        XCTAssertTrue(product.model.active)
        XCTAssertEqual(helper.operations, operations)
        XCTAssertEqual(capture.phase, .capturing)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertTrue(original.firstResponder === editor)
        XCTAssertEqual(editor.selectedRange(), NSRange(location: 5, length: 3))
        source.finishCapture()
        await captureTask.value
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertFalse(capture.frames.isEmpty)
        XCTAssertEqual(helper.operations, operations)
    }

    @MainActor
    func testAboutDoesNotIntroduceTerminationWaitOrBusinessProcesses() async throws {
        _ = NSApplication.shared
        for requestsTermination in [true, false] {
            let bundle = try AboutBundleFixture()
            defer { bundle.cleanUp() }
            let product = try ProductTestHarness(savedCLI: false)
            defer { product.cleanUp() }
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let capture = CaptureModel(screen: ScreenProbe(source: source, notificationCenter: NotificationCenter()))
            let about = AboutModel(resources: bundle.resources)
            let application = AppDelegate(model: product.model, capture: capture,
                                          diagnostics: ProbeModel(persistsPreferences: false), about: about)
            application.openAbout()
            defer { application.aboutPanel?.close() }
            await about.loadTask?.value
            if requestsTermination {
                XCTAssertEqual(application.applicationShouldTerminate(NSApp), .terminateNow)
            } else {
                application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            }
            XCTAssertEqual(about.phase, .idle)
            XCTAssertTrue(product.helpers.isEmpty)
            XCTAssertEqual(source.permissionCalls, 0)
            XCTAssertEqual(product.runtimeRequests, 0)
        }
    }
}
