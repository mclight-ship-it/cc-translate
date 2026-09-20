import AppKit
import Darwin
import SwiftUI
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
    private let focus: NativeTestWindowFocus

    init(regularApplication: Bool = false) throws {
        _ = NSApplication.shared
        previousMenu = NSApp.mainMenu
        previousWindowsMenu = NSApp.windowsMenu
        focus = NativeTestWindowFocus()
        product = try ProductTestHarness(savedCLI: false)
        source = CaptureTestSource(image: try CaptureProductFixture.image())
        let ocr = self.ocr
        capture = CaptureModel(screen: ScreenProbe(source: source, makeOCRJob: { ocr },
                                                   notificationCenter: NotificationCenter()))
        application = AppDelegate(model: product.model, capture: capture,
                                  diagnostics: NativePresentationTestSupport.offline(product.preferences, persists: false),
                                  loginItems: LoginItemModel(service: login))
        if regularApplication { CCTranslateApplication.configureNormalApplication(NSApp) }
    }

    func launch() {
        application.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
    }

    func reopen(visible: Bool = false) {
        XCTAssertFalse(application.applicationShouldHandleReopen(NSApp, hasVisibleWindows: visible),
                       "The delegate handles restoration; AppKit must not create an additional window.")
    }

    func cleanUp() {
        if NSApp.isHidden { NSApp.unhide(nil) }
        ocr.gate?.signal()
        for panel in [application.inputPanel, application.resultPanel, application.settingsPanel, application.capturePanel,
                      application.aboutPanel, application.updatesPanel].compactMap({ $0 }) {
            focus.close(panel)
        }
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        NSApp.mainMenu = previousMenu
        NSApp.windowsMenu = previousWindowsMenu
        product.cleanUp()
    }
}

private enum DockTestFailure: Error { case childTimedOut, missingChildResult }

@MainActor
private enum DockApplicationTestProcess {
    private static let childKey = "CC_TRANSLATE_DOCK_TEST_METHOD"

    static var lifecycle: String {
        "Dock host: running=\(NSApp.isRunning), active=\(NSApp.isActive), hidden=\(NSApp.isHidden), " +
            "policy=\(NSApp.activationPolicy().rawValue), key=\(NSApp.keyWindow?.windowNumber ?? -1), " +
            "pid=\(getpid()), frontmost=\(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1)"
    }

    static func isolated(_ function: String, body: () throws -> Void) throws {
        let method = String(function.prefix { $0 != "(" })
        if ProcessInfo.processInfo.environment[childKey] == method {
            try body()
            return
        }
        let host = NSApplication.shared
        let policy = host.activationPolicy()
        let hidden = host.isHidden
        let frontmost = NSWorkspace.shared.frontmostApplication
        let previousWindow = host.keyWindow
        let previousResponder = previousWindow?.firstResponder
        let previousSelection = (previousResponder as? NSTextView)?.selectedRanges
        defer {
            if let frontmost, !frontmost.isTerminated { frontmost.activate(options: []) }
            if let previousWindow, previousWindow.isVisible {
                previousWindow.makeKeyAndOrderFront(nil)
                if let previousResponder {
                    XCTAssertTrue(previousWindow.makeFirstResponder(previousResponder))
                    XCTAssertTrue(previousWindow.firstResponder === previousResponder)
                    if let previousSelection, let editor = previousResponder as? NSTextView {
                        XCTAssertEqual(editor.selectedRanges, previousSelection,
                                       "Dock process isolation must not reset the shared host's editor selection.")
                    }
                }
            }
            XCTAssertEqual(host.activationPolicy(), policy, "The shared XCTest host must retain its activation policy.")
            XCTAssertEqual(host.isHidden, hidden, "Child Hide must never hide the shared XCTest host.")
        }
        // Activation-policy transitions and Hide belong to a disposable application,
        // not the XCTest host used by every subsequent focus/editor/menu test.
        let directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent(".fixtures-dock-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { XCTAssertNoThrow(try FileManager.default.removeItem(at: directory)) }
        let log = directory.appendingPathComponent("xctest.log")
        try Data().write(to: log)
        let output = try FileHandle(forWritingTo: log)
        defer { XCTAssertNoThrow(try output.close()) }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        process.arguments = ["-XCTest", "CCTranslateMacTests.DockApplicationTests/\(method)",
                             Bundle(for: DockApplicationTests.self).bundlePath]
        var environment = ProcessInfo.processInfo.environment
        environment[childKey] = method
        process.environment = environment
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let deadline = Date().addingTimeInterval(45)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
        if process.isRunning {
            process.terminate()
            let terminationDeadline = Date().addingTimeInterval(2)
            while process.isRunning && Date() < terminationDeadline { Thread.sleep(forTimeInterval: 0.02) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            XCTFail("Isolated Dock test timed out:\n\(try String(contentsOf: log, encoding: .utf8))")
            throw DockTestFailure.childTimedOut
        }
        process.waitUntilExit()
        let transcript = try String(contentsOf: log, encoding: .utf8)
        XCTAssertEqual(process.terminationReason, .exit, transcript)
        XCTAssertEqual(process.terminationStatus, 0, transcript)
        XCTAssertTrue(transcript.contains(
            "Test Case '-[CCTranslateMacTests.DockApplicationTests \(method)]' passed"), transcript)
    }

    static func running(_ body: @escaping @MainActor (DockApplicationFixture) async throws -> Void) throws {
        let fixture = try DockApplicationFixture(regularApplication: true)
        let previousDelegate = NSApp.delegate
        defer {
            NSApp.delegate = previousDelegate
            fixture.cleanUp()
        }
        var result: Result<Void, Error>?
        Task { @MainActor in
            XCTAssertTrue(NSApp.isRunning, "Hide must run inside the real application event loop.")
            NSApp.delegate = fixture.application
            fixture.launch()
            do {
                try await body(fixture)
                result = .success(())
            } catch {
                result = .failure(error)
            }
            NSApp.stop(nil)
            if let wake = NSEvent.otherEvent(with: .applicationDefined, location: .zero,
                modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: 0,
                context: nil, subtype: 0, data1: 0, data2: 0) {
                NSApp.postEvent(wake, atStart: true)
            }
        }
        NSApp.run()
        guard let result else { throw DockTestFailure.missingChildResult }
        try result.get()
    }
}

final class DockApplicationTests: XCTestCase {
    @MainActor
    func testNormalLaunchHasDockIdentityButNoWindowOrBusinessWork() throws {
        try DockApplicationTestProcess.isolated(#function) {
            let f = try DockApplicationFixture(regularApplication: true)
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
    func testHideAndDockRestorePreserveEditorAndDoNotSubmit() throws {
        try DockApplicationTestProcess.isolated(#function) {
            try DockApplicationTestProcess.running { f in
                f.reopen()
                let window = try XCTUnwrap(f.application.inputPanel)
                let helper = try f.product.ready()
                f.product.model.input = "An unsent draft survives Hide."
                try await CaptureProductFixture.waitFor(diagnostics: { DockApplicationTestProcess.lifecycle }) {
                    NSApp.isActive && window.isKeyWindow
                }
                NSApp.hide(nil)
                try await CaptureProductFixture.waitFor(diagnostics: { DockApplicationTestProcess.lifecycle }) {
                    NSApp.isHidden
                }
                f.reopen()
                try await CaptureProductFixture.waitFor { !NSApp.isHidden && window.isVisible }
                XCTAssertTrue(f.application.inputPanel === window)
                XCTAssertEqual(f.product.model.input, "An unsent draft survives Hide.")
                XCTAssertTrue(helper.translations.isEmpty)
            }
        }
    }

    @MainActor
    func testIsolatedDockHidePreservesHostCaretAndNativePickerActions() async throws {
        let product = try ProductTestHarness(savedCLI: false)
        defer { product.cleanUp() }
        product.model.loadPresentation()
        let focus = NativeTestWindowFocus()
        let editorWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 200),
                                    styleMask: [.titled, .closable], backing: .buffered, defer: false)
        editorWindow.isReleasedWhenClosed = false
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
        editor.string = "e\u{301}"
        editorWindow.contentView = editor
        editorWindow.makeKeyAndOrderFront(nil)
        XCTAssertTrue(editorWindow.makeFirstResponder(editor))
        editor.setSelectedRange(NSRange(location: 2, length: 0))
        defer { focus.close(editorWindow) }

        try DockApplicationTestProcess.isolated("testHideAndDockRestorePreserveEditorAndDoNotSubmit") {
            XCTFail("This method must run the separate Hide test, not execute its body in the shared host.")
        }
        XCTAssertTrue(editorWindow.firstResponder === editor)
        XCTAssertEqual(editor.selectedRange(), NSRange(location: 2, length: 0))
        XCTAssertEqual(Array(editor.string.utf8), Array("e\u{301}".utf8))

        let pickerWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 400),
                                    styleMask: [.titled, .closable], backing: .buffered, defer: false)
        pickerWindow.isReleasedWhenClosed = false
        let picker = NSHostingView(rootView: Form {
            NativeTextScalePicker(model: product.model)
        }.formStyle(.grouped))
        pickerWindow.contentView = picker
        pickerWindow.makeKeyAndOrderFront(nil)
        defer { focus.close(pickerWindow) }
        picker.layoutSubtreeIfNeeded()
        picker.displayIfNeeded()
        let button = try XCTUnwrap(ScaleTestSupport.views(NSPopUpButton.self, in: picker).first)
        var inspectedMenu = false
        var invokedItem = false
        let timer = Timer(timeInterval: 0.05, repeats: false) { _ in
            MainActor.assumeIsolated {
                inspectedMenu = true
                guard let menu = button.menu else { XCTFail("The opened picker must own a menu."); return }
                defer { menu.cancelTrackingWithoutAnimation() }
                let matches = menu.items.indices.filter {
                    menu.items[$0].title.filter { !$0.isWhitespace } == "150%"
                }
                XCTAssertEqual(matches.count, 1)
                guard matches.count == 1, let index = matches.first else { return }
                XCTAssertTrue(menu.items[index].isEnabled)
                XCTAssertNotNil(menu.items[index].action)
                guard menu.items[index].isEnabled, menu.items[index].action != nil else { return }
                menu.performActionForItem(at: index)
                invokedItem = true
            }
        }
        RunLoop.main.add(timer, forMode: .eventTracking)
        defer { timer.invalidate() }
        button.performClick(nil)
        XCTAssertTrue(inspectedMenu, "A Dock child must not prevent later native-menu tracking.")
        XCTAssertTrue(invokedItem, "The real SwiftUI menu-item action must remain usable after Dock Hide.")
        try await CaptureProductFixture.waitFor {
            product.model.nativeTextScale == .largest &&
                product.preferences.string(forKey: NativeTextScale.preferenceKey) == "150"
        }
        XCTAssertEqual(editor.selectedRange(), NSRange(location: 2, length: 0))
        XCTAssertTrue(product.helpers.isEmpty)
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
    func testOCRCompletionDoesNotUndoHideOrInterruptAnotherWindow() throws {
        try DockApplicationTestProcess.isolated(#function) {
            try DockApplicationTestProcess.running { f in
                f.capture.start()
                try await CaptureProductFixture.waitFor { f.capture.phase == .selecting }
                f.capture.select(f.source.layout[0].frame)
                try await CaptureProductFixture.waitFor {
                    f.capture.phase == .recognizing && f.application.capturePanel?.isVisible == true
                }
                let panel = try XCTUnwrap(f.application.capturePanel)
                try await CaptureProductFixture.waitFor(diagnostics: { DockApplicationTestProcess.lifecycle }) {
                    NSApp.isActive && panel.isKeyWindow
                }
                NSApp.hide(nil)
                try await CaptureProductFixture.waitFor(diagnostics: { DockApplicationTestProcess.lifecycle }) {
                    NSApp.isHidden
                }
                f.ocr.gate?.signal()
                try await CaptureProductFixture.waitFor { f.capture.phase == .ready }
                try await Task.sleep(nanoseconds: 30_000_000)
                XCTAssertTrue(NSApp.isHidden, "Finishing local OCR is not a new request to activate the app.")
                XCTAssertTrue(f.application.capturePanel === panel)
                XCTAssertNil(f.application.inputPanel)
                XCTAssertTrue(f.product.helpers.isEmpty)
                f.reopen()
                try await CaptureProductFixture.waitFor { !NSApp.isHidden && panel.isVisible }
                XCTAssertEqual(f.capture.phase, .ready)
            }
        }
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
        f.login.current = .enabled
        f.login.holdUnregister = true
        defer { f.login.resume() }
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
