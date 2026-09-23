import AppKit
import SwiftUI
import XCTest
@testable import CCTranslateMac

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
    func testTraversalHandlesNativeSegmentAndScrollerAccessibilityChildren() throws {
        _ = NSApplication.shared
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 300))
        let segmented = NSSegmentedControl(labels: ["First", "Second"], trackingMode: .selectOne,
                                           target: nil, action: nil)
        segmented.frame = NSRect(x: 10, y: 250, width: 240, height: 28)
        segmented.setAccessibilityIdentifier("native-segments")
        let scroll = NSScrollView(frame: NSRect(x: 10, y: 10, width: 240, height: 200))
        scroll.hasVerticalScroller = true
        scroll.setAccessibilityIdentifier("native-scroll")
        scroll.documentView = NSTextView(frame: NSRect(x: 0, y: 0, width: 220, height: 1000))
        root.addSubview(segmented)
        root.addSubview(scroll)
        root.layoutSubtreeIfNeeded()
        XCTAssertFalse((segmented.accessibilityChildren() ?? []).isEmpty)
        XCTAssertNotNil(scroll.verticalScroller)
        // AppKit's navigation-order array can contain private segment/scroller
        // objects that cannot bridge to its declared [NSAccessibilityElement].
        let identifiers = Set(NativeSettingsTestAccessibility.elements(in: root).compactMap(\.identifier))
        XCTAssertTrue(identifiers.contains("native-segments"))
        XCTAssertTrue(identifiers.contains("native-scroll"))
    }

    @MainActor
    func testIdentifierTraversalIncludesPublicLegacyVirtualAccessibilityNodes() throws {
        let root = NativeLegacyAccessibilityFixture(frame: .zero)
        let virtualElement = try XCTUnwrap(NativeSettingsTestAccessibility.elements(in: root)
            .first { $0.identifier == "virtual-action" })
        XCTAssertEqual(virtualElement.frame, NSRect(x: 100, y: 200, width: 80, height: 28))
    }

    @MainActor
    func testSwiftUISemanticIdentifiersResolveDuplicateCaptionsToActualNativeButtons() async throws {
        var sidebarPresses = 0
        var submitPresses = 0
        let surface = NativeSettingsTestHost(
            HStack(spacing: 40) {
                Button("Translate") { sidebarPresses += 1 }
                    .accessibilityIdentifier("semantic-sidebar")
                Button("Translate") { submitPresses += 1 }
                    .accessibilityIdentifier("semantic-submit")
            }
            .buttonStyle(.bordered).padding(20).pearlSurface(),
            size: NSSize(width: 420, height: 120))
        defer { surface.close() }
        let sidebar = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "semantic-sidebar", label: "Translate", kind: .button)
        let submit = try await NativeSettingsTestControls.resolveWhenReady(
            in: surface.host, identifier: "semantic-submit", label: "Translate", kind: .button)
        XCTAssertFalse(sidebar.sameElement(as: submit))
        try await sidebar.press()
        try await surface.waitFor { sidebarPresses == 1 }
        XCTAssertEqual(submitPresses, 0)
        try await submit.press()
        try await surface.waitFor { submitPresses == 1 }
        XCTAssertEqual(sidebarPresses, 1)
    }

    @MainActor
    private final class NativeLegacyAccessibilityFixture: NSView {
        private let element = VirtualAction()

        override func accessibilityChildren() -> [Any]? { [element] }

        private final class VirtualAction: NSObject {
            override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
                [.identifier, .position, .size]
            }

            override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
                switch attribute {
                case .identifier: return "virtual-action"
                case .position: return NSValue(point: NSPoint(x: 100, y: 200))
                case .size: return NSValue(size: NSSize(width: 80, height: 28))
                default: return super.accessibilityAttributeValue(attribute)
                }
            }
        }
    }

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
