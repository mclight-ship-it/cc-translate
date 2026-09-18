import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac

@MainActor
final class LoginItemTestService: LoginItemServing {
    var current: LoginItemStatus = .notRegistered
    var registeredStatus: LoginItemStatus = .enabled
    var unregisteredStatus: LoginItemStatus = .notRegistered
    var failure: Error?
    var holdUnregister = false
    private var continuation: CheckedContinuation<Void, Error>?
    private(set) var reads = 0
    private(set) var registrations = 0
    private(set) var removals = 0
    private(set) var settingsOpens = 0

    func readStatus() -> LoginItemStatus { reads += 1; return current }
    func register() throws {
        registrations += 1
        if let failure { throw failure }
        current = registeredStatus
    }
    func unregister() async throws {
        removals += 1
        if holdUnregister {
            try await withCheckedThrowingContinuation { continuation = $0 }
        }
        if let failure { throw failure }
        current = unregisteredStatus
    }
    func openSystemSettings() { settingsOpens += 1 }
    func resume() {
        let pending = continuation
        continuation = nil
        pending?.resume()
    }
}

@MainActor
private struct LoginItemTestSurface: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var loginItems: LoginItemModel

    var body: some View {
        Form {
            Section {
                LoginItemSettingsView(model: model, loginItems: loginItems)
            } header: {
                Text(model.text("Login item", "登录项"))
            }
        }.formStyle(.grouped)
    }
}

final class LoginItemTests: XCTestCase {
    @MainActor
    func testConstructionAndClosedSettingsActivationDoNotQueryOrRegister() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        let application = AppDelegate(model: f.model, capture: CaptureModel(),
                                      diagnostics: f.model, loginItems: login)
        XCTAssertTrue(application.settingsContent().loginItems === login)
        application.applicationDidBecomeActive(Notification(name: NSApplication.didBecomeActiveNotification))
        XCTAssertEqual(login.status, .notChecked)
        XCTAssertFalse(login.enabled)
        XCTAssertEqual(service.reads, 0)
        XCTAssertEqual(service.registrations + service.removals + service.settingsOpens, 0)
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(f.locatorRequests, 0)
    }

    @MainActor
    func testEveryReadbackStateIsAuthoritativeAndOnlyEnabledMeansOn() {
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        for status in [LoginItemStatus.notRegistered, .enabled, .requiresApproval, .notFound, .unknown] {
            service.current = status
            login.refresh()
            XCTAssertEqual(login.status, status)
            XCTAssertEqual(login.enabled, status == .enabled)
        }
        XCTAssertEqual(service.reads, 5)
        XCTAssertEqual(service.registrations + service.removals + service.settingsOpens, 0)
    }

    @MainActor
    func testRegisterReadbackDoesNotPretendApprovalOrUnconfirmedStatesAreEnabled() async throws {
        for status in [LoginItemStatus.enabled, .requiresApproval, .notRegistered, .notFound, .unknown] {
            let service = LoginItemTestService()
            service.registeredStatus = status
            let login = LoginItemModel(service: service)
            login.setEnabled(true)
            XCTAssertTrue(login.busy)
            XCTAssertFalse(login.enabled)
            try await CaptureProductFixture.waitFor { !login.busy }
            XCTAssertEqual(login.status, status)
            XCTAssertEqual(login.enabled, status == .enabled)
            XCTAssertEqual(login.issue, [.enabled, .requiresApproval].contains(status) ? nil : .notConfirmed)
            XCTAssertEqual(service.registrations, 1)
            XCTAssertEqual(service.reads, 2)
            XCTAssertEqual(service.settingsOpens, 0)
        }
    }

    @MainActor
    func testPendingApprovalOpensSystemSettingsWithoutRegisteringAgainAndCanBeRemoved() async throws {
        let service = LoginItemTestService()
        service.current = .requiresApproval
        let login = LoginItemModel(service: service)
        login.setEnabled(true)
        XCTAssertEqual(service.settingsOpens, 1)
        XCTAssertEqual(service.registrations, 0)
        XCTAssertFalse(login.enabled)
        login.setEnabled(false)
        try await CaptureProductFixture.waitFor { !login.busy }
        XCTAssertEqual(login.status, .notRegistered)
        XCTAssertEqual(service.removals, 1)
        XCTAssertNil(login.issue)
    }

    @MainActor
    func testUnregisterWaitsForCompletionAndRepeatedClicksCannotRaceOrReplay() async throws {
        let service = LoginItemTestService()
        service.current = .enabled
        service.holdUnregister = true
        defer { service.resume() }
        let login = LoginItemModel(service: service)
        login.setEnabled(false)
        try await CaptureProductFixture.waitFor { service.removals == 1 }
        let reads = service.reads
        login.setEnabled(false)
        login.setEnabled(true)
        login.refresh()
        XCTAssertEqual(service.reads, reads)
        XCTAssertEqual(service.registrations, 0)
        XCTAssertEqual(service.removals, 1)
        XCTAssertTrue(login.busy)
        XCTAssertTrue(login.enabled)
        service.resume()
        try await CaptureProductFixture.waitFor { !login.busy }
        XCTAssertFalse(login.enabled)
        XCTAssertNil(login.issue)
    }

    @MainActor
    func testRegistrationAndRemovalErrorsSurfaceOnlySafeCodeAndKeepActualStatus() async throws {
        for enable in [false, true] {
            let service = LoginItemTestService()
            service.current = enable ? .notRegistered : .enabled
            service.failure = NSError(domain: "synthetic.private-domain", code: 42,
                                      userInfo: [NSLocalizedDescriptionKey: "PRIVATE DETAIL MUST NOT APPEAR"])
            let login = LoginItemModel(service: service)
            login.setEnabled(enable)
            try await CaptureProductFixture.waitFor { !login.busy }
            XCTAssertEqual(login.issue, .operationFailed(42))
            XCTAssertEqual(login.enabled, !enable)
            XCTAssertEqual(service.registrations + service.removals, 1)
            XCTAssertEqual(service.settingsOpens, 0)
            login.refresh()
            XCTAssertEqual(login.issue, .operationFailed(42))
            XCTAssertEqual(service.registrations + service.removals, 1)
        }
    }

    @MainActor
    func testUnconfirmedRemovalAndExternalChangesNeverTriggerAutomaticRegistration() async throws {
        let service = LoginItemTestService()
        service.current = .enabled
        service.unregisteredStatus = .enabled
        let login = LoginItemModel(service: service)
        login.setEnabled(false)
        try await CaptureProductFixture.waitFor { !login.busy }
        XCTAssertEqual(login.issue, .notConfirmed)
        XCTAssertTrue(login.enabled)
        service.current = .notRegistered
        login.refresh()
        XCTAssertFalse(login.enabled)
        XCTAssertEqual(service.registrations, 0)
        XCTAssertEqual(service.removals, 1)
    }

    @MainActor
    func testAlreadyMatchingStateDoesNotWriteAndExplicitSettingsLinkDoesNotChangeStatus() {
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        login.setEnabled(false)
        service.current = .enabled
        login.setEnabled(true)
        login.openSystemSettings()
        XCTAssertTrue(login.enabled)
        XCTAssertEqual(service.registrations + service.removals, 0)
        XCTAssertEqual(service.settingsOpens, 1)
    }

    @MainActor
    func testRealSettingsMenuRefreshesOnOpenAndReturnButNotWhileHidden() throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        let application = AppDelegate(model: f.model, capture: CaptureModel(),
                                      diagnostics: ProbeModel(persistsPreferences: false), loginItems: login)
        let priorMenu = NSApp.mainMenu
        let focus = NativeTestWindowFocus()
        defer {
            if let window = application.settingsPanel { focus.close(window) }
            NSApp.mainMenu = priorMenu
        }
        application.configureMenus()
        let menu = try XCTUnwrap(NSApp.mainMenu?.items.first?.submenu)
        let item = try XCTUnwrap(menu.items.first { $0.action == NSSelectorFromString("openSettings") })
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        let window = try XCTUnwrap(application.settingsPanel)
        XCTAssertTrue(window.isVisible)
        XCTAssertEqual(login.status, .notRegistered)
        service.current = .enabled
        application.applicationDidBecomeActive(Notification(name: NSApplication.didBecomeActiveNotification))
        XCTAssertTrue(login.enabled)
        window.orderOut(nil)
        let reads = service.reads
        service.current = .notRegistered
        application.applicationDidBecomeActive(Notification(name: NSApplication.didBecomeActiveNotification))
        XCTAssertEqual(service.reads, reads)
        XCTAssertTrue(login.enabled)
        XCTAssertTrue(NSApp.sendAction(try XCTUnwrap(item.action), to: item.target, from: item))
        XCTAssertFalse(login.enabled)
        XCTAssertTrue(application.settingsPanel === window)
        XCTAssertEqual(service.registrations + service.removals + service.settingsOpens, 0)
        XCTAssertTrue(f.helpers.allSatisfy { $0.translations.isEmpty })
    }

    @MainActor
    func testRestoringTranslationDefaultsDoesNotRemoveAnApprovedLoginItem() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: SettingsDefaultsFixture.canonical())
        let service = LoginItemTestService()
        service.current = .enabled
        let login = LoginItemModel(service: service)
        let application = AppDelegate(model: f.model, capture: CaptureModel(),
                                      diagnostics: f.model, loginItems: login)
        login.refresh()
        try SettingsDefaultsFixture.prepare(f.model, helper: helper)
        f.model.confirmDefaultsRestore()
        try SettingsDefaultsFixture.finish(f, helper: helper)
        XCTAssertEqual(f.model.defaultsPhase, .restored)
        XCTAssertTrue(application.settingsContent().loginItems === login)
        XCTAssertTrue(login.enabled)
        XCTAssertEqual(service.reads, 1)
        XCTAssertEqual(service.registrations + service.removals, 0)
    }

    @MainActor
    func testNativeBilingualToggleApprovalRefreshAndRemovalUseOnlyInjectedSystemService() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            f.model.interfaceLanguage = language
            let service = LoginItemTestService()
            service.registeredStatus = .requiresApproval
            let login = LoginItemModel(service: service)
            login.refresh()
            let surface = NativeSettingsTestHost(LoginItemTestSurface(model: f.model, loginItems: login),
                                                 size: NSSize(width: 760, height: 520))
            defer { surface.close() }
            let toggle = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "launch-at-login",
                label: f.model.text("Launch at login", "登录时启动"), kind: .toggle)
            XCTAssertEqual(toggle.state, .off)
            try await toggle.press()
            try await surface.waitFor { login.status == .requiresApproval && !login.busy }
            XCTAssertEqual(toggle.state, .off, "Pending approval is not enabled.")
            let remove = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "remove-pending-login-item",
                label: f.model.text("Remove pending login item", "移除待批准的登录项"),
                kind: .button, authoredCaption: true)
            try await remove.press()
            try await surface.waitFor { login.status == .notRegistered && !login.busy }
            try await toggle.press()
            try await surface.waitFor { login.status == .requiresApproval && !login.busy }
            let settings = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "open-login-items",
                label: f.model.text("Open Login Items…", "打开登录项设置…"),
                kind: .button, authoredCaption: true)
            try await settings.press()
            XCTAssertEqual(service.settingsOpens, 1)
            service.current = .enabled
            let refresh = try await NativeSettingsTestControls.resolveWhenReady(
                in: surface.host, identifier: "refresh-login-item",
                label: f.model.text("Refresh status", "刷新状态"), kind: .button, authoredCaption: true)
            try await refresh.press()
            try await surface.waitFor { toggle.state == .on }
            try await toggle.press()
            try await surface.waitFor { toggle.state == .off && !login.busy }
            XCTAssertEqual(service.registrations, 2)
            XCTAssertEqual(service.removals, 2)
            XCTAssertTrue(f.helpers.isEmpty)
            XCTAssertEqual(f.locatorRequests, 0)
            XCTAssertEqual(f.model.permissions, "Not checked.")
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testLoginItemSettingsRenderOffPendingApprovalAndFailureWithoutRealRegistration() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        for (name, language, scheme, status) in [
            ("login-item-off-en-light", "en", ColorScheme.light, LoginItemStatus.notRegistered),
            ("login-item-approval-zh-dark", "zh", .dark, .requiresApproval),
            ("login-item-error-en-light", "en", .light, .notRegistered)
        ] {
            f.model.interfaceLanguage = language
            service.current = status
            login.refresh()
            if name.contains("error") {
                service.failure = NSError(domain: "fixture", code: 42,
                                          userInfo: [NSLocalizedDescriptionKey: "PRIVATE DETAIL MUST NOT APPEAR"])
                login.setEnabled(true)
                try await CaptureProductFixture.waitFor { !login.busy }
            }
            let png = try render(LoginItemTestSurface(model: f.model, loginItems: login),
                                 named: name, size: NSSize(width: 760, height: 520), scheme: scheme,
                                 highResolution: true)
            let words = try NativeRenderEvidence.settingsWords(png, chinese: language == "zh")
            XCTAssertFalse(words.contains("private detail"), words)
            if name.contains("error") {
                XCTAssertTrue(words.contains("system error 42"), words)
                XCTAssertTrue(words.contains("no automatic retry"), words)
            } else if language == "zh" {
                XCTAssertTrue(words.filter { !$0.isWhitespace }.contains("尚未开启"), words)
            } else {
                XCTAssertTrue(words.contains("not registered"), words)
            }
        }
        XCTAssertTrue(f.helpers.isEmpty)
        XCTAssertEqual(service.registrations, 1)
        XCTAssertEqual(service.removals, 0)
        XCTAssertEqual(service.settingsOpens, 0)
    }
}
