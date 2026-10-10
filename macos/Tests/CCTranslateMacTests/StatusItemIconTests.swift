import AppKit
import XCTest
@testable import CCTranslateMac

final class StatusItemIconTests: XCTestCase {
    private let names = ["CCTranslateStatusTemplate.png", "CCTranslateStatusTemplate-2x.png"]
    private var root: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
    }

    private func templates() throws -> [String: Data] {
        try Dictionary(uniqueKeysWithValues: names.map {
            ($0, try Data(contentsOf: root.appendingPathComponent("assets/macos/" + $0)))
        })
    }

    private func fixture(images: [String: Data]) throws -> Bundle {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("cc-menu-icon-\(UUID().uuidString).app", isDirectory: true)
        let resources = root.appendingPathComponent("Contents/Resources", isDirectory: true)
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        addTeardownBlock { try FileManager.default.removeItem(at: root) }
        let info = ["CFBundleIdentifier": "test.cc-translate.menu-icon.\(UUID().uuidString)",
                    "CFBundlePackageType": "APPL", "CFBundleIconFile": "CCTranslate.icns"]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: root.appendingPathComponent("Contents/Info.plist"))
        for (name, data) in images { try data.write(to: resources.appendingPathComponent(name)) }
        return try XCTUnwrap(Bundle(url: root))
    }

    @MainActor
    func testTemplateSmileReplacesTextWithoutChangingMenuOrDockArtwork() throws {
        _ = NSApplication.shared
        let bundle = try fixture(images: templates())
        let url = root.appendingPathComponent("assets/icon-dark.png")
        let original = try XCTUnwrap(NSImage(contentsOf: url))
        let originalSize = original.size
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        defer { NSStatusBar.system.removeStatusItem(item) }
        let button = try XCTUnwrap(item.button)
        let menu = NSMenu()
        let toggle = NSMenuItem(title: "Synthetic toggle", action: nil, keyEquivalent: "")
        toggle.state = .on
        menu.addItem(toggle)
        item.menu = menu
        button.title = "CC"

        StatusItemIcon.configure(button, bundle: bundle)

        let image = try XCTUnwrap(button.image)
        XCTAssertTrue(image.isValid)
        XCTAssertTrue(image.isTemplate, "AppKit must tint the silhouette for light, dark and selected menus.")
        XCTAssertEqual(image.size, NSSize(width: 18, height: 18))
        XCTAssertEqual(image.representations.map(\.pixelsWide).sorted(), [18, 36])
        for rep in image.representations {
            let bitmap = try XCTUnwrap(rep as? NSBitmapImageRep)
            XCTAssertEqual(bitmap.size, image.size)
            XCTAssertTrue(bitmap.hasAlpha)
            var coverage: CGFloat = 0
            for y in 0..<bitmap.pixelsHigh {
                for x in 0..<bitmap.pixelsWide {
                    let color = try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                    coverage += color.alphaComponent
                    if x == 0 || y == 0 || x == bitmap.pixelsWide - 1 || y == bitmap.pixelsHigh - 1 {
                        XCTAssertEqual(color.alphaComponent, 0, "No opaque tile or edge.")
                    }
                    XCTAssertEqual(color.redComponent, 0)
                    XCTAssertEqual(color.greenComponent, 0)
                    XCTAssertEqual(color.blueComponent, 0)
                }
            }
            let fraction = coverage / CGFloat(bitmap.pixelsWide * bitmap.pixelsHigh)
            XCTAssertGreaterThan(fraction, 0.15)
            XCTAssertLessThan(fraction, 0.4, "The smile must not become a solid rounded square.")
        }
        XCTAssertEqual(image.accessibilityDescription, "CC Translate")
        XCTAssertEqual(button.title, "")
        XCTAssertEqual(button.imagePosition, .imageOnly)
        XCTAssertEqual(button.toolTip, "CC Translate")
        XCTAssertEqual(button.accessibilityLabel(), "CC Translate")
        XCTAssertTrue(item.menu === menu)
        XCTAssertEqual(toggle.state, .on)
        XCTAssertEqual(original.size, originalSize)
        XCTAssertFalse(original.isTemplate)
    }

    @MainActor
    func testMissingCorruptOrWrongScaleTemplateKeepsTheMenuReachable() throws {
        _ = NSApplication.shared
        let valid = try templates()
        for (index, name) in names.enumerated() {
            let wrongSize = try XCTUnwrap(valid[names[1 - index]])
            let replacements: [Data?] = [nil, Data("not an image".utf8), wrongSize]
            for replacement in replacements {
                var images = valid
                images[name] = replacement
                let bundle = try fixture(images: images)
                let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
                defer { NSStatusBar.system.removeStatusItem(item) }
                let button = try XCTUnwrap(item.button)
                StatusItemIcon.configure(button, bundle: bundle)
                XCTAssertNil(button.image)
                XCTAssertEqual(button.title, "CC")
                XCTAssertEqual(button.imagePosition, .noImage)
                XCTAssertEqual(button.accessibilityLabel(), "CC Translate")
            }
        }
    }
}
