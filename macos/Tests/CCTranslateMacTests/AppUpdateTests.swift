import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac

@MainActor
final class AppUpdateTestService: AppUpdateServing {
    var canCheck = true
    var sessionInProgress = false
    var onChange: (@MainActor () -> Void)?
    var failure: Error?
    private(set) var checks = 0

    func check() throws {
        checks += 1
        if let failure { throw failure }
        canCheck = false
        sessionInProgress = true
        onChange?()
    }

    func finish() {
        canCheck = true
        sessionInProgress = false
        onChange?()
    }

    static var info: [String: Any] {
        ["SUFeedURL": "https://updates.example.invalid/appcast.xml",
         "SUPublicEDKey": Data(repeating: 65, count: 32).base64EncodedString()]
    }
}

final class AppUpdateTests: XCTestCase {
    @MainActor
    func testChannelRequiresAnHTTPSFeedAndPublicKeyWithoutRestrictingTranslation() throws {
        XCTAssertEqual(AppUpdateChannel(info: [:]), .unconfigured)
        XCTAssertEqual(AppUpdateChannel(info: AppUpdateTestService.info), .configured)
        for (key, value) in [
            ("SUFeedURL", "http://updates.example.invalid/appcast.xml"),
            ("SUFeedURL", ""),
            ("SUPublicEDKey", "invalid"),
            ("SUPublicEDKey", Data(repeating: 65, count: 31).base64EncodedString())
        ] {
            var info = AppUpdateTestService.info
            info[key] = value
            XCTAssertEqual(AppUpdateChannel(info: info), .invalid)
        }
        for key in ["SUFeedURL", "SUPublicEDKey"] {
            var info = AppUpdateTestService.info
            info.removeValue(forKey: key)
            XCTAssertEqual(AppUpdateChannel(info: info), .invalid)
        }
        for userOnly in [true, false] {
            var url = try XCTUnwrap(URLComponents(string: "https://updates.example.invalid/appcast.xml"))
            url.user = "fixture-user"
            if !userOnly { url.password = "fixture-password" }
            var info = AppUpdateTestService.info
            info["SUFeedURL"] = try XCTUnwrap(url.string)
            XCTAssertEqual(AppUpdateChannel(info: info), .invalid)
        }
    }

    @MainActor
    func testConstructionSettingsAndActivationDoNotCreateUpdaterOrOpenDownloads() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        var creations = 0
        var downloads = 0
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: {
            creations += 1
            return AppUpdateTestService()
        }, openDownloads: { downloads += 1; return true })
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, updates: updates)
        XCTAssertTrue(app.settingsContent().updates === updates)
        app.applicationDidBecomeActive(Notification(name: NSApplication.didBecomeActiveNotification))
        XCTAssertTrue(updates.canCheck)
        XCTAssertEqual(creations + downloads, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testUnavailableChannelsNeverCreateUpdaterOrSilentlyOpenBrowser() {
        for info in [[:], ["SUFeedURL": "https://updates.example.invalid/feed"]] {
            var creations = 0
            var downloads = 0
            let updates = AppUpdateModel(info: info, factory: {
                creations += 1
                return AppUpdateTestService()
            }, openDownloads: { downloads += 1; return true })
            XCTAssertFalse(updates.check())
            XCTAssertFalse(updates.canCheck)
            XCTAssertEqual(updates.issue, .channelUnavailable)
            XCTAssertEqual(creations + downloads, 0)
        }
    }

    @MainActor
    func testExplicitCheckCreatesOneServiceAndBusyStatePreventsDuplicateRequests() {
        let service = AppUpdateTestService()
        var creations = 0
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: {
            creations += 1
            return service
        })
        XCTAssertTrue(updates.check())
        XCTAssertFalse(updates.canCheck)
        XCTAssertTrue(updates.sessionInProgress)
        XCTAssertFalse(updates.check())
        XCTAssertEqual(updates.issue, .notReady)
        XCTAssertEqual(service.checks, 1)
        service.finish()
        XCTAssertTrue(updates.canCheck)
        XCTAssertFalse(updates.sessionInProgress)
        XCTAssertTrue(updates.check())
        XCTAssertNil(updates.issue)
        XCTAssertEqual(service.checks, 2)
        XCTAssertEqual(creations, 1)
    }

    @MainActor
    func testStartupFailureIsVisibleAndOnlyExplicitRetryStartsAnotherCheck() {
        let service = AppUpdateTestService()
        service.failure = NSError(domain: "fixture", code: 42,
                                  userInfo: [NSLocalizedDescriptionKey: "PRIVATE DETAIL MUST NOT APPEAR"])
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service })
        XCTAssertFalse(updates.check())
        XCTAssertEqual(updates.issue, .failed(42))
        service.finish()
        XCTAssertEqual(updates.issue, .failed(42))
        XCTAssertEqual(service.checks, 1)
        service.failure = nil
        XCTAssertTrue(updates.check())
        XCTAssertNil(updates.issue)
        XCTAssertEqual(service.checks, 2)
    }

    @MainActor
    func testNativeNotReadyErrorRemainsDifferentFromAStartupFailure() {
        let service = AppUpdateTestService()
        service.failure = AppUpdateServiceError.notReady
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service })
        XCTAssertFalse(updates.check())
        XCTAssertEqual(updates.issue, .notReady)
        XCTAssertEqual(service.checks, 1)
    }

    @MainActor
    func testDownloadsRequireTheirOwnActionAndSurfaceBrowserFailure() {
        var downloads = 0
        var succeeds = false
        let updates = AppUpdateModel(info: [:], openDownloads: {
            downloads += 1
            return succeeds
        })
        XCTAssertFalse(updates.check())
        XCTAssertEqual(downloads, 0)
        updates.showDownloads()
        XCTAssertEqual(downloads, 1)
        XCTAssertEqual(updates.issue, .downloadsUnavailable)
        succeeds = true
        updates.showDownloads()
        XCTAssertEqual(downloads, 2)
        XCTAssertNil(updates.issue)
    }

    @MainActor
    func testTerminationRejectsNewActionsAndLateCallbacksCannotReenableChecks() throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = AppUpdateTestService()
        var downloads = 0
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service },
                                     openDownloads: { downloads += 1; return true })
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, updates: updates)
        app.terminationReply = { _ in }
        XCTAssertTrue(updates.check())
        XCTAssertEqual(app.applicationShouldTerminate(NSApp), .terminateNow)
        service.finish()
        XCTAssertFalse(updates.canCheck)
        XCTAssertFalse(updates.canOpenDownloads)
        XCTAssertFalse(updates.check())
        updates.showDownloads()
        XCTAssertEqual(updates.issue, .terminating)
        XCTAssertEqual(service.checks, 1)
        XCTAssertEqual(downloads, 0)
    }

    @MainActor
    func testSparkleAdapterConstructionDoesNotStartASession() {
        let service = SparkleUpdateService()
        XCTAssertTrue(service.canCheck)
        XCTAssertFalse(service.sessionInProgress)
        XCTAssertNil(service.onChange)
    }

    @MainActor
    func testActualUnconfiguredMenuOpensReusablePanelWithoutStartingHelperOrBrowser() throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        var downloads = 0
        let updates = AppUpdateModel(info: [:], openDownloads: { downloads += 1; return true })
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, updates: updates)
        let priorMenu = NSApp.mainMenu
        let focus = NativeTestWindowFocus()
        defer {
            if let panel = app.updatesPanel { focus.close(panel) }
            NSApp.mainMenu = priorMenu
        }
        app.configureMenus()
        let menu = try XCTUnwrap(NSApp.mainMenu?.items.first?.submenu)
        let item = try XCTUnwrap(menu.items.first { $0.action == NSSelectorFromString("checkForUpdates") })
        XCTAssertTrue(app.validateMenuItem(item))
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        let panel = try XCTUnwrap(app.updatesPanel)
        XCTAssertTrue(panel.isVisible)
        XCTAssertEqual(updates.issue, .channelUnavailable)
        panel.orderOut(nil)
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        XCTAssertTrue(app.updatesPanel === panel)
        XCTAssertTrue(panel.isVisible)
        XCTAssertEqual(downloads, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testActualConfiguredMenuChecksAndReflectsFrameworkAvailabilityWithoutHelper() throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = AppUpdateTestService()
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service })
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, updates: updates)
        let priorMenu = NSApp.mainMenu
        defer { NSApp.mainMenu = priorMenu }
        app.configureMenus()
        let menu = try XCTUnwrap(NSApp.mainMenu?.items.first?.submenu)
        let item = try XCTUnwrap(menu.items.first { $0.action == NSSelectorFromString("checkForUpdates") })
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        XCTAssertFalse(app.validateMenuItem(item))
        service.finish()
        XCTAssertTrue(app.validateMenuItem(item))
        XCTAssertEqual(service.checks, 1)
        XCTAssertNil(app.updatesPanel)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testRestoringTranslationDefaultsDoesNotStartOrResetUpdater() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        let service = AppUpdateTestService()
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service })
        let app = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, updates: updates)
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        try SettingsDefaultsFixture.finish(f, helper: helper)
        XCTAssertTrue(app.settingsContent().updates === updates)
        XCTAssertEqual(updates.channel, .configured)
        XCTAssertEqual(service.checks, 0)
    }

    @MainActor
    func testRealBilingualUpdateAndDownloadButtonsUseOnlyInjectedService() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            f.model.interfaceLanguage = language
            let service = AppUpdateTestService()
            var downloads = 0
            let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { service },
                                         openDownloads: { downloads += 1; return true })
            let surface = NativeSettingsTestHost(AppUpdatePanelView(model: f.model, updates: updates),
                                                 size: NSSize(width: 760, height: 420))
            defer { surface.close() }
            let check = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "check-software-update",
                label: f.model.text("Check for Updates…", "检查更新…"), kind: .button, authoredCaption: true)
            try await check.press()
            try await surface.waitFor { service.checks == 1 && !check.isEnabled }
            service.finish()
            try await surface.waitFor { check.isEnabled }
            let download = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "open-verified-downloads",
                label: f.model.text("View verified downloads", "查看已验证下载"), kind: .button, authoredCaption: true)
            try await download.press()
            XCTAssertEqual(downloads, 1)
            XCTAssertEqual(service.checks, 1)
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.locatorRequests, 0)
        }
    }

    @MainActor
    func testUnavailableChannelKeepsNativeDownloadActionAvailable() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        f.model.interfaceLanguage = "en"
        var downloads = 0
        let updates = AppUpdateModel(info: [:], openDownloads: { downloads += 1; return true })
        let surface = NativeSettingsTestHost(AppUpdatePanelView(model: f.model, updates: updates),
                                             size: NSSize(width: 760, height: 420))
        defer { surface.close() }
        let download = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "open-verified-downloads",
            label: "View verified downloads", kind: .button, authoredCaption: true)
        XCTAssertFalse(updates.canCheck)
        XCTAssertTrue(download.isEnabled)
        try await download.press()
        XCTAssertEqual(downloads, 1)
        XCTAssertTrue(f.helpers.isEmpty)
    }
}

extension ProductRenderingTests {
    @MainActor
    func testUpdatePanelRendersDevelopmentChannelConfiguredAndFailureStates() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        for (name, language, scheme, configured, fails) in [
            ("updates-development-en-light", "en", ColorScheme.light, false, false),
            ("updates-configured-zh-dark", "zh", .dark, true, false),
            ("updates-error-en-light", "en", .light, true, true)
        ] {
            f.model.interfaceLanguage = language
            let service = AppUpdateTestService()
            if fails {
                service.failure = NSError(domain: "fixture", code: 42,
                                          userInfo: [NSLocalizedDescriptionKey: "PRIVATE DETAIL MUST NOT APPEAR"])
            }
            let updates = AppUpdateModel(info: configured ? AppUpdateTestService.info : [:],
                                         factory: { service }, openDownloads: { true })
            if fails { XCTAssertFalse(updates.check()) }
            let png = try render(AppUpdatePanelView(model: f.model, updates: updates),
                                 named: name, size: NSSize(width: 760, height: 420), scheme: scheme,
                                 highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
            XCTAssertFalse(words.contains("private detail"), words)
            if fails {
                XCTAssertTrue(words.contains("error 42"), words)
            } else if configured {
                XCTAssertTrue(words.filter { !$0.isWhitespace }.contains("检查新版本"), words)
            } else {
                XCTAssertTrue(words.contains("has not been published"), words)
            }
        }
        XCTAssertTrue(f.helpers.isEmpty)
    }
}
