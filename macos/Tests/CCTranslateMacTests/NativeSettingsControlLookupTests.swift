import AppKit
import SwiftUI
import XCTest

@MainActor
private struct NativeSettingsLookupFixture: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 120))
        let sidebar = NSButton(title: "Translate", target: nil, action: nil)
        sidebar.identifier = NSUserInterfaceItemIdentifier("lookup-sidebar")
        sidebar.frame = NSRect(x: 10, y: 70, width: 120, height: 28)
        let submit = NSButton(title: "Translate", target: nil, action: nil)
        submit.setAccessibilityIdentifier("lookup-submit")
        submit.frame = NSRect(x: 150, y: 70, width: 120, height: 28)
        let reset = NSButton(title: "Reset", target: nil, action: nil)
        reset.frame = NSRect(x: 10, y: 20, width: 120, height: 28)
        view.addSubview(sidebar)
        view.addSubview(submit)
        view.addSubview(reset)
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {}
}

final class NativeSettingsControlLookupTests: XCTestCase {
    @MainActor
    func testExactNativeAndAccessibilityIdentifiersTakePriorityOverDuplicateTitles() async throws {
        let surface = NativeSettingsTestHost(NativeSettingsLookupFixture().frame(width: 420, height: 120),
                                             size: NSSize(width: 420, height: 120))
        defer { surface.close() }
        let sidebar = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "lookup-sidebar", label: "Translate", kind: .button)
        let submit = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "lookup-submit", label: "Translate", kind: .button)
        XCTAssertFalse(sidebar.sameElement(as: submit))
        XCTAssertLessThan(sidebar.frame.maxX, submit.frame.minX)
        let reset = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "lookup-without-identifier", label: "Reset", kind: .button)
        XCTAssertFalse(reset.sameElement(as: sidebar))
        XCTAssertFalse(reset.sameElement(as: submit))
    }
}
