import AppKit
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private final class DockApplicationFixture {
    let product: ProductTestHarness
    let source: CaptureTestSource
    let ocr = CaptureTestOCR(blocked: true)
    let capture: CaptureModel
    let login = LoginItemTestService()
    let application: AppDelegate
    private let previousMenu: NSMenu?
    private let previousWindowsMenu: NSMenu?
    private let previousPolicy: NSApplication.ActivationPolicy
    private let focus: NativeTestWindowFocus

    init() throws {
        _ = NSApplication.shared
        previousMenu = NSApp.mainMenu
        previousWindowsMenu = NSApp.windowsMenu
        previousPolicy = NSApp.activationPolicy()
        focus = NativeTestWindowFocus()
        product = try ProductTestHarness(savedCLI: false)
        source = CaptureTestSource(image: try CaptureProductFixture.image())
        let ocr = self.ocr
        capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { ocr },
                                                   notificationCenter: NotificationCenter()))
        application = AppDelegate(model: product.model, capture: capture,
                                  diagnostics: NativePresentationTestSupport.offline(product.preferences, persists: false),
                                  loginItems: LoginItemModel(service: login))
        CCTranslateApplication.configureNormalApplication(NSApp)
    }

    func launch() {
        application.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
    }

    func reopen(visible: Bool = false) {
        XCTAssertFalse(application.applicationShouldHandleReopen(NSApp, hasVisibleWindows: visible),
                       "The delegate handles restoration; AppKit must not create an additional window.")
    }

    func cleanUp() {
        NSApp.unhide(nil)
        ocr.gate?.signal()
        for panel in [application.inputPanel, application.resultPanel, application.settingsPanel, application.capturePanel,
                      application.aboutPanel, application.updatesPanel].compactMap({ $0 }) {
            focus.close(panel)
        }
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        NSApp.mainMenu = previousMenu
        NSApp.windowsMenu = previousWindowsMenu
        NSApp.setActivationPolicy(previousPolicy)
        product.cleanUp()
    }
}

final class DockApplicationTests: XCTestCase {
    @MainActor
    func testNormalLaunchHasDockIdentityButNoWindowOrBusinessWork() throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        XCTAssertEqual(NSApp.activationPolicy(), .regular)
        XCTAssertNil(f.application.inputPanel)
        XCTAssertNil(f.application.resultPanel)
        XCTAssertNil(f.application.settingsPanel)
        XCTAssertFalse(f.application.applicationShouldTerminateAfterLastWindowClosed(NSApp))
        XCTAssertTrue(f.product.helpers.isEmpty)
        XCTAssertEqual(f.product.runtimeRequests, 0)
        XCTAssertEqual(f.product.locatorRequests, 0)
        XCTAssertEqual(f.login.reads, 0)
        XCTAssertEqual(f.source.permissionCalls, 0)
        XCTAssertEqual(f.source.layoutCalls, 0)
        XCTAssertFalse(f.product.model.monitorEnabled)
        XCTAssertEqual(f.product.model.permissions, "Not checked.")
    }

    @MainActor
    func testDockOpensTranslatorOnlyOnRequestThenRestoresSameDraftAndWindow() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.product.model.input = "Keep this unfinished draft; do not translate it."
        f.reopen()
        let window = try XCTUnwrap(f.application.inputPanel)
        let content = try XCTUnwrap(window.contentView)
        let helper = try f.product.ready()
        let operations = helper.operations
        XCTAssertTrue(window.isVisible)
        XCTAssertTrue(window.canBecomeMain)
        XCTAssertTrue(window.styleMask.contains(.miniaturizable))
        XCTAssertFalse(window.isExcludedFromWindowsMenu)
        window.orderOut(nil)
        f.reopen()
        XCTAssertTrue(window.isVisible)
        XCTAssertTrue(f.application.inputPanel === window)
        XCTAssertTrue(window.contentView === content)
        window.miniaturize(nil)
        try await CaptureProductFixture.waitFor { window.isMiniaturized }
        f.reopen()
        try await CaptureProductFixture.waitFor { !window.isMiniaturized && window.isVisible }
        f.reopen(visible: true)
        XCTAssertTrue(f.application.inputPanel === window)
        XCTAssertEqual(f.product.model.input, "Keep this unfinished draft; do not translate it.")
        XCTAssertEqual(helper.operations, operations)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testHideAndDockRestorePreserveEditorAndDoNotSubmit() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.reopen()
        let window = try XCTUnwrap(f.application.inputPanel)
        let helper = try f.product.ready()
        f.product.model.input = "An unsent draft survives Hide."
        NSApp.hide(nil)
        try await CaptureProductFixture.waitFor { NSApp.isHidden }
        f.reopen()
        try await CaptureProductFixture.waitFor { !NSApp.isHidden && window.isVisible }
        XCTAssertTrue(f.application.inputPanel === window)
        XCTAssertEqual(f.product.model.input, "An unsent draft survives Hide.")
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testMostRecentlyFocusedExistingWindowWinsAndLastCloseLeavesAppReopenable() throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.reopen()
        let editor = try XCTUnwrap(f.application.inputPanel)
        let helper = try f.product.ready()
        f.product.model.input = "Retained after closing every window."
        XCTAssertTrue(NSApp.sendAction(Selector("openSettings"), to: f.application, from: nil))
        let settings = try XCTUnwrap(f.application.settingsPanel)
        editor.orderOut(nil)
        settings.orderOut(nil)
        let operations = helper.operations
        f.reopen()
        XCTAssertTrue(settings.isVisible)
        XCTAssertFalse(editor.isVisible, "Dock restoration must not open an unrelated translator.")
        XCTAssertEqual(helper.operations, operations)
        settings.performClose(nil)
        f.reopen()
        XCTAssertTrue(editor.isVisible)
        editor.performClose(nil)
        XCTAssertFalse(f.application.applicationShouldTerminateAfterLastWindowClosed(NSApp))
        f.reopen()
        XCTAssertTrue(f.application.inputPanel === editor)
        XCTAssertTrue(editor.isVisible)
        XCTAssertFalse(settings.isVisible)
        XCTAssertEqual(f.product.model.input, "Retained after closing every window.")
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testResultRestorationDoesNotCreateTranslatorAndBackgroundRefreshDoesNotRaiseWindow() throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.application.showResult(reposition: true)
        let result = try XCTUnwrap(f.application.resultPanel)
        result.orderOut(nil)
        let previousKey = NSApp.keyWindow
        f.application.showResult()
        XCTAssertFalse(result.isVisible, "A stream update must not undo a user's hide or raise the panel.")
        XCTAssertTrue(NSApp.keyWindow === previousKey)
        XCTAssertNil(f.application.inputPanel)
        f.reopen()
        XCTAssertTrue(result.isVisible)
        XCTAssertTrue(f.application.resultPanel === result)
        XCTAssertNil(f.application.inputPanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
        result.performClose(nil)
        f.reopen()
        XCTAssertNotNil(f.application.inputPanel, "Closed results are not resurrected instead of a translator.")
        XCTAssertFalse(result.isVisible)
    }

    @MainActor
    func testSelectionAndTerminationBoundariesIgnoreDockReopen() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.capture.start()
        try await CaptureProductFixture.waitFor { f.capture.phase == .selecting }
        f.reopen()
        XCTAssertEqual(f.capture.phase, .selecting)
        XCTAssertNil(f.application.inputPanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
        f.capture.cancel()
        try await CaptureProductFixture.waitFor { f.capture.phase == .cancelled }
        XCTAssertEqual(f.application.applicationShouldTerminate(NSApp), .terminateNow)
        f.reopen()
        XCTAssertNil(f.application.inputPanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
    }

    @MainActor
    func testOCRCompletionDoesNotUndoHideOrInterruptAnotherWindow() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.capture.start()
        try await CaptureProductFixture.waitFor { f.capture.phase == .selecting }
        f.capture.select(f.source.layout[0].frame)
        try await CaptureProductFixture.waitFor {
            f.capture.phase == .recognizing && f.application.capturePanel?.isVisible == true
        }
        let panel = try XCTUnwrap(f.application.capturePanel)
        NSApp.hide(nil)
        try await CaptureProductFixture.waitFor { NSApp.isHidden }
        f.ocr.gate?.signal()
        try await CaptureProductFixture.waitFor { f.capture.phase == .ready }
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(NSApp.isHidden, "Finishing local OCR is not a new request to activate the app.")
        XCTAssertTrue(f.application.capturePanel === panel)
        XCTAssertNil(f.application.inputPanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
        f.reopen()
        XCTAssertFalse(NSApp.isHidden)
        XCTAssertTrue(panel.isVisible)
        XCTAssertEqual(f.capture.phase, .ready)
    }

    @MainActor
    func testHidingSelectionCancelsOnlyCaptureAndDoesNotStrandDockReopen() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.product.model.input = "The draft is independent of screenshot selection."
        f.capture.start()
        try await CaptureProductFixture.waitFor { f.capture.phase == .selecting }
        f.application.applicationWillHide(Notification(name: NSApplication.willHideNotification))
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertNil(f.application.capturePanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
        f.reopen()
        XCTAssertTrue(f.application.inputPanel?.isVisible == true)
        XCTAssertEqual(f.product.model.input, "The draft is independent of screenshot selection.")
        XCTAssertTrue(f.product.helpers.allSatisfy { $0.translations.isEmpty })
    }

    @MainActor
    func testUninstallPreparationAndPendingTerminationIgnoreDockReopen() async throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        f.login.holdUnregister = true
        var terminations = 0
        f.application.terminateApplication = { terminations += 1 }
        let root = f.product.root
        let locations = AppUninstallLocations(application: root.appendingPathComponent("Fixture.app"),
                                              identifier: f.product.suiteName,
                                              support: root.appendingPathComponent("support"),
                                              cache: root.appendingPathComponent("cache"))
        let uninstall = Task { await f.application.requestUninstall(locations, includingData: false) }
        try await CaptureProductFixture.waitFor { f.login.removals == 1 }
        f.reopen()
        XCTAssertNil(f.application.inputPanel)
        f.login.resume()
        await uninstall.value
        XCTAssertEqual(terminations, 1)
        f.reopen()
        XCTAssertNil(f.application.inputPanel)
        XCTAssertTrue(f.product.helpers.isEmpty)
    }

    @MainActor
    func testRegularMenusKeepTranslationAndStandardHideAndWindowActions() throws {
        let f = try DockApplicationFixture()
        defer { f.cleanUp() }
        f.launch()
        let main = try XCTUnwrap(NSApp.mainMenu)
        let application = try XCTUnwrap(main.items.first?.submenu)
        let hide = try XCTUnwrap(application.items.first { $0.action == #selector(NSApplication.hide(_:)) })
        XCTAssertTrue(hide.target === NSApp)
        XCTAssertEqual(hide.keyEquivalent, "h")
        XCTAssertEqual(hide.keyEquivalentModifierMask, [.command])
        let others = try XCTUnwrap(application.items.first {
            $0.action == #selector(NSApplication.hideOtherApplications(_:))
        })
        XCTAssertEqual(others.keyEquivalentModifierMask, [.command, .option])
        let window = try XCTUnwrap(NSApp.windowsMenu)
        XCTAssertTrue(main.items.contains { $0.submenu === window })
        let minimize = try XCTUnwrap(window.items.first { $0.action == #selector(NSWindow.performMiniaturize(_:)) })
        XCTAssertEqual(minimize.keyEquivalent, "m")
        XCTAssertNil(minimize.target, "The Window menu must use the current responder chain.")
        XCTAssertTrue(main.items.compactMap(\.submenu).flatMap(\.items).contains {
            $0.action == Selector("openInput") && $0.keyEquivalent == "n"
        })
        f.product.model.interfaceLanguage = "zh"
        f.application.configureMenus()
        XCTAssertEqual(NSApp.windowsMenu?.title, "窗口")
        XCTAssertTrue(f.product.helpers.isEmpty)
    }

    @MainActor
    func testPackagedDockIconIsARealDecodableApplicationResource() throws {
        guard let path = ProcessInfo.processInfo.environment["CC_TRANSLATE_DOCK_TEST_APP"], !path.isEmpty else {
            throw XCTSkip("POSTBUILD only: set CC_TRANSLATE_DOCK_TEST_APP to validate the actual bundled icon.")
        }
        let app = try XCTUnwrap(Bundle(url: URL(fileURLWithPath: path)))
        XCTAssertEqual(app.object(forInfoDictionaryKey: "LSUIElement") as? Bool, true)
        let filename = try XCTUnwrap(app.object(forInfoDictionaryKey: "CFBundleIconFile") as? String)
        XCTAssertEqual(filename, "CCTranslate.icns")
        let icon = try XCTUnwrap(app.url(forResource: "CCTranslate", withExtension: "icns"))
        let image = try XCTUnwrap(NSImage(contentsOf: icon))
        XCTAssertTrue(image.isValid)
        XCTAssertTrue(image.representations.contains { $0.pixelsWide >= 256 && $0.pixelsHigh >= 256 })
    }
}
