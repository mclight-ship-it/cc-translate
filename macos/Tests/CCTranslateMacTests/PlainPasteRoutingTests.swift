import XCTest
import AppKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

final class PlainPasteRoutingTests: XCTestCase {
    @MainActor
    func testNonactivatingOwnKeyWindowTakesPriorityOverExternalFrontmostApplication() {
        let ownPID = ProcessInfo.processInfo.processIdentifier
        for frontmost in [nil, Optional(ownPID), Optional(ownPID + 1)] {
            XCTAssertEqual(NativePlainPasteRouting.destination(ownKeyWindow: true, frontmostPID: frontmost),
                           .ownApplication)
        }
        XCTAssertEqual(NativePlainPasteRouting.destination(ownKeyWindow: false, frontmostPID: ownPID),
                       .ownApplication)
        XCTAssertEqual(NativePlainPasteRouting.destination(ownKeyWindow: false, frontmostPID: ownPID + 1),
                       .externalApplication)
        XCTAssertEqual(NativePlainPasteRouting.destination(ownKeyWindow: false, frontmostPID: nil), .unavailable)
    }

    @MainActor
    func testNativeAdapterIsLazyAndDispatchesExactlyTheNilTargetResponderAction() {
        var reads = 0
        var actions: [Selector] = []
        let routing = NativePlainPasteRouting(foreground: {
            reads += 1
            return .ownApplication
        }, sendAction: { action, target, sender in
            actions.append(action)
            XCTAssertNil(target, "AppKit, not the global service, must choose the current field editor.")
            XCTAssertNil(sender)
            return true
        })
        XCTAssertEqual(reads, 0)
        XCTAssertTrue(actions.isEmpty)
        if case .ownApplication = routing.destination() {} else { XCTFail("Expected the injected own-app route.") }
        XCTAssertTrue(routing.pasteInOwnApplication())
        XCTAssertEqual(reads, 1)
        XCTAssertEqual(actions, [#selector(NSTextView.pasteAsPlainText(_:))])
    }

    @MainActor
    func testOwnApplicationUsesNativeResponderOnceWithoutExternalRequestOrPermissions() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.routing.foreground = .ownApplication
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        lease.fire(.pressed)
        lease.fire(.released)
        XCTAssertEqual(fixture.routing.foregroundReads, 1)
        XCTAssertEqual(fixture.routing.nativePastes, 1)
        XCTAssertEqual(fixture.paste.lastRoute, .ownApplication(dispatched: true))
        XCTAssertNil(fixture.paste.lastAdmission)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertEqual(fixture.service.state.status, .idle)
        XCTAssertEqual(fixture.model.permissions, "Not checked.")
        XCTAssertTrue(fixture.helpers.allSatisfy { $0.translations.isEmpty })
    }

    @MainActor
    func testMissingOwnResponderNeverFallsBackToExternalService() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.routing.foreground = .ownApplication
        fixture.routing.handlesNativePaste = false
        try XCTUnwrap(fixture.registrar.leases.last).fire(.pressed)
        XCTAssertEqual(fixture.paste.lastRoute, .ownApplication(dispatched: false))
        XCTAssertEqual(fixture.routing.nativePastes, 1)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertNil(fixture.paste.lastAdmission)
    }

    @MainActor
    func testForegroundChangeDoesNotRerouteAHeldChordButNextPressUsesItsNewDestination() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastRoute, .externalApplication)
        fixture.routing.foreground = .ownApplication
        lease.fire(.pressed)
        XCTAssertEqual(fixture.service.requests.count, 1)
        XCTAssertEqual(fixture.routing.nativePastes, 0)
        XCTAssertEqual(fixture.routing.foregroundReads, 1)
        lease.fire(.released)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.routing.nativePastes, 1)
        XCTAssertEqual(fixture.service.requests.count, 1)
        XCTAssertEqual(fixture.service.cancellations, 0)
        XCTAssertTrue(fixture.service.state.busy, "The old external receipt/drain remains independently visible.")
        XCTAssertEqual(fixture.paste.lastRoute, .ownApplication(dispatched: true))
    }

    @MainActor
    func testUnidentifiedForegroundDispatchesNeitherRouteAndDoesNotProbeAgainOnRepeat() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.routing.foreground = .unavailable
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        lease.fire(.pressed)
        lease.fire(.pressed)
        XCTAssertEqual(fixture.paste.lastRoute, .unavailable)
        XCTAssertEqual(fixture.routing.foregroundReads, 1)
        XCTAssertEqual(fixture.routing.nativePastes, 0)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }

    @MainActor
    func testDisableDuringForegroundResolutionInvalidatesBothRoutesAndLateCallbacks() throws {
        for own in [false, true] {
            let fixture = try PasteAppFixture()
            defer { fixture.cleanUp() }
            _ = try fixture.ready(true)
            fixture.routing.foreground = own ? .ownApplication : .externalApplication
            fixture.routing.onDestination = { [weak fixture] in fixture?.model.setPlainPasteEnabled(false) }
            let lease = try XCTUnwrap(fixture.registrar.leases.last)
            lease.fire(.pressed)
            lease.fire(.released)
            lease.fire(.pressed)
            XCTAssertEqual(fixture.routing.foregroundReads, 1)
            XCTAssertEqual(fixture.routing.nativePastes, 0)
            XCTAssertTrue(fixture.service.requests.isEmpty)
            XCTAssertNil(fixture.paste.lastRoute)
            XCTAssertFalse(fixture.service.state.enabled)
        }
    }

    @MainActor
    func testNativeResponderReentryCannotAlsoSubmitTheSameChordToAnExternalApp() throws {
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        _ = try fixture.ready(true)
        fixture.routing.foreground = .ownApplication
        let lease = try XCTUnwrap(fixture.registrar.leases.last)
        fixture.routing.onNativePaste = { [weak fixture, weak lease] in
            fixture?.routing.foreground = .externalApplication
            lease?.fire(.pressed)
        }
        lease.fire(.pressed)
        XCTAssertEqual(fixture.routing.nativePastes, 1)
        XCTAssertEqual(fixture.routing.foregroundReads, 1)
        XCTAssertTrue(fixture.service.requests.isEmpty)
        XCTAssertEqual(fixture.paste.lastRoute, .ownApplication(dispatched: true))
    }

    @MainActor
    func testNativeEditMenuRetainsPlainPasteChordAndResponderChainWhenGlobalFeatureIsOff() throws {
        _ = NSApplication.shared
        let fixture = try PasteAppFixture()
        defer { fixture.cleanUp() }
        fixture.model.loadPresentation()
        let diagnostics = ProbeModel(persistsPreferences: false,
                                     plainPaste: PlainPasteModel(service: PasteTestService(),
                                                                registrar: PasteTestRegistrar(),
                                                                routing: PasteTestRouting()))
        let application = AppDelegate(model: fixture.model, capture: CaptureModel(), diagnostics: diagnostics)
        for chinese in [false, true] {
            fixture.model.interfaceLanguage = chinese ? "zh" : "en"
            // Build only the production Edit menu; do not open/validate a menu or read pasteboard types.
            let menu = application.makeEditMenu()
            let matches = menu.items.filter { $0.action == NativePlainPasteRouting.action }
            let native = try XCTUnwrap(matches.first)
            XCTAssertEqual(matches.count, 1)
            XCTAssertEqual(native.title, chinese ? "粘贴并匹配样式" : "Paste and Match Style")
            XCTAssertNil(native.target)
            XCTAssertEqual(native.keyEquivalent, "v")
            XCTAssertEqual(native.keyEquivalentModifierMask, [.command, .option, .shift])
            let ordinary = try XCTUnwrap(menu.items.first { $0.action == Selector("paste:") })
            XCTAssertEqual(ordinary.keyEquivalent, "v")
            XCTAssertEqual(ordinary.keyEquivalentModifierMask, .command)
            XCTAssertTrue(menu.items.contains { $0.action == Selector("selectAll:") })
            XCTAssertTrue(menu.items.contains { $0.action == Selector("copy:") })
        }
        XCTAssertEqual(fixture.registrar.registrations, 0)
        XCTAssertEqual(fixture.routing.foregroundReads, 0)
        XCTAssertEqual(fixture.routing.nativePastes, 0)
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertTrue(fixture.service.requests.isEmpty)
    }
}
