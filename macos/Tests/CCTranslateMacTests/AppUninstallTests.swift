import AppKit
import XCTest
@testable import CCTranslateMac

@MainActor
private final class UninstallFileFixture {
    let product: ProductTestHarness
    let application: URL
    let home: URL
    let trashRoot: URL
    var moved: [URL] = []
    var failAt: URL?
    lazy var service = AppUninstallService(application: application, home: home,
                                           defaults: product.preferences) { [unowned self] url in
        if url == failAt { throw CocoaError(.fileWriteNoPermission) }
        let destination = trashRoot.appendingPathComponent(String(moved.count))
        try FileManager.default.moveItem(at: url, to: destination)
        moved.append(url)
    }

    init() throws {
        product = try ProductTestHarness(savedCLI: false)
        application = product.root.appendingPathComponent("Fixture.app", isDirectory: true)
        home = product.root.appendingPathComponent("home", isDirectory: true)
        trashRoot = product.root.appendingPathComponent("trash", isDirectory: true)
        do {
            let info = try PropertyListSerialization.data(
                fromPropertyList: ["CFBundleIdentifier": product.suiteName], format: .xml, options: 0)
            try info.write(to: application.appendingPathComponent("Contents/Info.plist"))
            try FileManager.default.createDirectory(at: trashRoot, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        } catch {
            product.cleanUp()
            throw error
        }
    }

    func seed(_ folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("synthetic owned data".utf8).write(to: folder.appendingPathComponent("sentinel"))
    }
}

@MainActor
private final class UninstallTestService: AppUninstallServing {
    let locations = AppUninstallLocations(
        application: URL(fileURLWithPath: "/synthetic/CCTranslate.app"), identifier: "dev.cc-translate.test",
        support: URL(fileURLWithPath: "/synthetic/Library/Application Support/dev.cc-translate.test"),
        cache: URL(fileURLWithPath: "/synthetic/Library/Caches/dev.cc-translate.test"))
    var previews = 0
    var requests: [Bool] = []
    var outcome = AppUninstallOutcome(completed: [.application])
    var onRemove: (() -> Void)?
    func preview() throws -> AppUninstallLocations { previews += 1; return locations }
    func remove(_ locations: AppUninstallLocations, includingData: Bool) -> AppUninstallOutcome {
        XCTAssertEqual(locations, self.locations)
        requests.append(includingData)
        onRemove?()
        return outcome
    }
}

final class AppUninstallTests: XCTestCase {
    @MainActor
    func testNativeTrashMovesRecoverableFixtureDataAndApplication() throws {
        let f = try UninstallFileFixture()
        var destinations: [URL] = []
        defer {
            for url in destinations { XCTAssertNoThrow(try FileManager.default.removeItem(at: url)) }
            f.product.cleanUp()
        }
        let service = AppUninstallService(application: f.application, home: f.home,
                                           defaults: f.product.preferences) { url in
            var destination: NSURL?
            try FileManager.default.trashItem(at: url, resultingItemURL: &destination)
            destinations.append(try XCTUnwrap(destination) as URL)
        }
        let plan = try service.preview()
        for folder in [plan.support, plan.cache] { try f.seed(folder) }
        let result = service.remove(plan, includingData: true)
        XCTAssertNil(result.failure)
        XCTAssertEqual(destinations.count, 3)
        for destination in destinations.prefix(2) {
            XCTAssertEqual(try Data(contentsOf: destination.appendingPathComponent("sentinel")),
                           Data("synthetic owned data".utf8))
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: plan.application.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: plan.support.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: plan.cache.path))
    }

    @MainActor
    func testDefaultRemovalKeepsDataCachePreferencesAndExternalFiles() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        try f.seed(plan.support)
        try f.seed(plan.cache)
        let external = f.home.appendingPathComponent("external-cli-and-dictionary")
        try f.seed(external)
        f.product.preferences.set("retained", forKey: "native-preference")
        XCTAssertTrue(f.moved.isEmpty)
        let result = f.service.remove(plan, includingData: false)
        XCTAssertNil(result.failure)
        XCTAssertEqual(result.completed, [.application])
        XCTAssertEqual(f.moved, [plan.application])
        for folder in [plan.support, plan.cache, external] {
            XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appendingPathComponent("sentinel").path))
        }
        XCTAssertEqual(f.product.preferences.string(forKey: "native-preference"), "retained")
        XCTAssertTrue(f.product.helpers.isEmpty)
    }

    @MainActor
    func testOptInMovesOnlyOwnDirectoriesAndDoesNotFollowTheirContents() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        for folder in [plan.support, plan.cache] { try f.seed(folder) }
        let neighbor = plan.support.deletingLastPathComponent().appendingPathComponent("another-application")
        let external = f.home.appendingPathComponent("external-dictionary")
        try f.seed(neighbor)
        try f.seed(external)
        try FileManager.default.createSymbolicLink(
            at: plan.support.appendingPathComponent("custom-dictionary"), withDestinationURL: external)
        f.product.preferences.set("remove", forKey: "native-preference")
        let result = f.service.remove(plan, includingData: true)
        XCTAssertNil(result.failure)
        XCTAssertEqual(result.completed, [.support, .cache, .preferences, .application])
        XCTAssertEqual(f.moved, [plan.support, plan.cache, plan.application])
        XCTAssertTrue((f.product.preferences.persistentDomain(forName: f.product.suiteName) ?? [:]).isEmpty)
        for folder in [neighbor, external] {
            XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appendingPathComponent("sentinel").path))
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: f.trashRoot.appendingPathComponent("0/sentinel").path))
    }

    @MainActor
    func testRedirectedParentIsRejectedBeforeAnyFileMoves() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        let external = f.home.appendingPathComponent("shared-parent")
        try f.seed(external.appendingPathComponent(plan.identifier))
        try FileManager.default.createDirectory(at: plan.support.deletingLastPathComponent().deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: plan.support.deletingLastPathComponent(),
                                                   withDestinationURL: external)
        let result = f.service.remove(plan, includingData: true)
        XCTAssertEqual(result.failure?.step, .support)
        XCTAssertTrue(result.completed.isEmpty)
        XCTAssertTrue(f.moved.isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: plan.application.path))
    }

    @MainActor
    func testMissingDataAndBrokenLeafLinkDoNotExpandCleanupScope() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        try FileManager.default.createDirectory(at: plan.support.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: plan.support,
                                                   withDestinationURL: f.home.appendingPathComponent("absent-target"))
        let result = f.service.remove(plan, includingData: true)
        XCTAssertNil(result.failure)
        XCTAssertEqual(f.moved, [plan.support, plan.application])
        XCTAssertThrowsError(try FileManager.default.attributesOfItem(atPath: plan.support.path))
        XCTAssertEqual(try FileManager.default.attributesOfItem(
            atPath: f.trashRoot.appendingPathComponent("0").path)[.type] as? FileAttributeType, .typeSymbolicLink)
    }

    @MainActor
    func testPartialFailureStopsBeforePreferencesAndAppWithoutPretendingRollback() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        for folder in [plan.support, plan.cache] { try f.seed(folder) }
        f.product.preferences.set("retained", forKey: "native-preference")
        f.failAt = plan.cache
        let result = f.service.remove(plan, includingData: true)
        XCTAssertEqual(result.completed, [.support])
        XCTAssertEqual(result.failure?.step, .cache)
        XCTAssertEqual(f.moved, [plan.support])
        XCTAssertEqual(f.product.preferences.string(forKey: "native-preference"), "retained")
        XCTAssertTrue(FileManager.default.fileExists(atPath: plan.application.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: plan.cache.path))
    }

    @MainActor
    func testChangedPreviewAndNonAppTargetsCannotRemoveAnything() throws {
        let f = try UninstallFileFixture()
        defer { f.product.cleanUp() }
        let plan = try f.service.preview()
        let changed = AppUninstallLocations(application: plan.application, identifier: plan.identifier,
                                             support: f.home, cache: plan.cache)
        XCTAssertEqual(f.service.remove(changed, includingData: true).failure?.step, .application)
        XCTAssertTrue(f.moved.isEmpty)
        let notApp = AppUninstallService(application: f.home, home: f.home,
                                         trash: { _ in XCTFail("Never trash a home directory") })
        XCTAssertThrowsError(try notApp.preview())
    }

    @MainActor
    func testConfirmationIsLocalizedAndDefaultsToKeepingDataWithoutCallingServices() throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = UninstallTestService()
        let login = LoginItemTestService()
        let application = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model,
                                      loginItems: LoginItemModel(service: login), uninstaller: service)
        for language in ["en", "zh"] {
            f.model.interfaceLanguage = language
            let alert = application.uninstallConfirmation(service.locations)
            XCTAssertEqual(alert.buttons.count, 2)
            XCTAssertEqual(alert.buttons[0].title, language == "en" ? "Cancel" : "取消")
            let checkbox = try XCTUnwrap(alert.accessoryView as? NSButton)
            XCTAssertEqual(checkbox.state, .off)
            XCTAssertFalse(checkbox.title.isEmpty)
            XCTAssertTrue(alert.informativeText.contains(service.locations.application.path))
        }
        XCTAssertEqual(service.previews, 0)
        XCTAssertTrue(service.requests.isEmpty)
        XCTAssertEqual(login.reads + login.registrations + login.removals, 0)
        XCTAssertTrue(f.helpers.isEmpty)
    }

    @MainActor
    func testUninstallWaitsForBothHelpersAndThenSuppressesLatePreferenceWrites() async throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness()
        let diagnostic = try ProductTestHarness()
        defer { f.cleanUp(); diagnostic.cleanUp() }
        let primary = try f.ready()
        let secondary = try diagnostic.ready()
        let service = UninstallTestService()
        let login = LoginItemTestService()
        login.current = .enabled
        let application = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: diagnostic.model,
                                      loginItems: LoginItemModel(service: login), uninstaller: service)
        var requested = 0
        var replies: [Bool] = []
        application.terminateApplication = { requested += 1 }
        application.terminationReply = { replies.append($0) }
        application.uninstallNotice = { XCTFail($0) }
        service.onRemove = {
            XCTAssertFalse(f.model.hasProcesses)
            XCTAssertFalse(diagnostic.model.hasProcesses)
            f.preferences.removePersistentDomain(forName: f.suiteName)
        }
        await application.requestUninstall(service.locations, includingData: true)
        XCTAssertEqual(requested, 1)
        XCTAssertEqual(login.removals, 1)
        XCTAssertTrue(service.requests.isEmpty)
        XCTAssertEqual(application.applicationShouldTerminate(NSApp), .terminateLater)
        XCTAssertTrue(service.requests.isEmpty)
        primary.stopped()
        try await CaptureProductFixture.waitFor { !f.model.hasProcesses }
        XCTAssertTrue(service.requests.isEmpty)
        XCTAssertTrue(replies.isEmpty)
        secondary.stopped()
        try await CaptureProductFixture.waitFor { replies == [true] }
        XCTAssertEqual(service.requests, [true])
        f.model.rememberResultFrame(NSRect(x: 10, y: 10, width: 500, height: 400))
        f.model.persistPresentation()
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        XCTAssertTrue((f.preferences.persistentDomain(forName: f.suiteName) ?? [:]).isEmpty)
        XCTAssertEqual(service.requests.count, 1)
    }

    @MainActor
    func testLoginFailureKeepsAppRunningAndDoesNotTouchFiles() async throws {
        for status in [LoginItemStatus.enabled, .requiresApproval, .notFound, .unknown] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let service = UninstallTestService()
            let login = LoginItemTestService()
            login.current = status
            login.unregisteredStatus = status
            let application = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model,
                                          loginItems: LoginItemModel(service: login), uninstaller: service)
            var notices: [String] = []
            application.uninstallNotice = { notices.append($0) }
            application.terminateApplication = { XCTFail("Do not quit on failed preconditions") }
            await application.requestUninstall(service.locations, includingData: false)
            XCTAssertEqual(notices.count, 1)
            XCTAssertTrue(service.requests.isEmpty)
            XCTAssertEqual(login.removals, 1)
            XCTAssertTrue(f.helpers.isEmpty)
        }
    }

    @MainActor
    func testActiveUpdateBlocksUninstallAndLoginAwaitRechecksForANewUpdate() async throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = UninstallTestService()
        let login = LoginItemTestService()
        login.current = .enabled
        login.holdUnregister = true
        let updater = AppUpdateTestService()
        let updates = AppUpdateModel(info: AppUpdateTestService.info, factory: { updater })
        let application = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model,
                                      loginItems: LoginItemModel(service: login), updates: updates, uninstaller: service)
        var notices: [String] = []
        application.uninstallNotice = { notices.append($0) }
        application.terminateApplication = { XCTFail("Never remove an app while its updater is active") }
        XCTAssertTrue(updates.check())
        await application.requestUninstall(service.locations, includingData: false)
        XCTAssertEqual(login.reads, 0)
        XCTAssertTrue(service.requests.isEmpty)
        XCTAssertEqual(notices.count, 1)
        updater.finish()
        let pending = Task { await application.requestUninstall(service.locations, includingData: false) }
        try await CaptureProductFixture.waitFor { login.removals == 1 }
        XCTAssertTrue(service.requests.isEmpty)
        await application.requestUninstall(service.locations, includingData: true)
        XCTAssertEqual(login.removals, 1, "Duplicate requests cannot change an already confirmed removal.")
        XCTAssertTrue(updates.check())
        login.resume()
        await pending.value
        XCTAssertEqual(notices.count, 3)
        XCTAssertTrue(service.requests.isEmpty)
        XCTAssertEqual(login.current, .notRegistered)
        XCTAssertTrue(notices.last?.contains("login item was removed") == true)
    }

    @MainActor
    func testLoginUnregisterErrorSurfacesAndAlreadyAbsentLoginNeedsNoChange() async {
        let service = LoginItemTestService()
        let login = LoginItemModel(service: service)
        let alreadyAbsent = await login.removeForUninstall()
        XCTAssertTrue(alreadyAbsent)
        XCTAssertEqual(service.removals, 0)
        service.current = .enabled
        service.failure = CocoaError(.fileWriteNoPermission)
        let removed = await login.removeForUninstall()
        XCTAssertFalse(removed)
        XCTAssertEqual(login.issue, .operationFailed(CocoaError.Code.fileWriteNoPermission.rawValue))
        XCTAssertFalse(login.busy)
        XCTAssertEqual(login.status, .enabled)
    }

    @MainActor
    func testPartialRemovalFailureIsReportedAndNormalQuitDoesNotUninstall() async throws {
        _ = NSApplication.shared
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let service = UninstallTestService()
        service.outcome = .init(completed: [.support], failure: .init(step: .cache, code: 513))
        let application = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model,
                                      loginItems: LoginItemModel(service: LoginItemTestService()), uninstaller: service)
        var notices: [String] = []
        application.uninstallNotice = { notices.append($0) }
        application.terminateApplication = {}
        await application.requestUninstall(service.locations, includingData: true)
        XCTAssertEqual(application.applicationShouldTerminate(NSApp), .terminateNow)
        XCTAssertEqual(service.requests, [true])
        XCTAssertEqual(notices.count, 1)
        XCTAssertTrue(notices[0].contains("cache"))
        XCTAssertTrue(notices[0].contains("513"))
        application.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        XCTAssertEqual(service.requests.count, 1)
        let normal = AppDelegate(model: f.model, capture: CaptureModel(), diagnostics: f.model, uninstaller: service)
        XCTAssertEqual(normal.applicationShouldTerminate(NSApp), .terminateNow)
        XCTAssertEqual(service.requests.count, 1, "Ordinary Quit must never uninstall.")
    }
}
