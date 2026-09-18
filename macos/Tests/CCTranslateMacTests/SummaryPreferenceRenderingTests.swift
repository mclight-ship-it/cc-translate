import AppKit
import SwiftUI
import Vision
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct SummarySettingsSurface: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Form {
            TranslationSettingsView(model: model, showDiagnostics: {}, showAbout: {}).translationSection
        }
        .formStyle(.grouped)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

@MainActor
struct NativeSettingsAXElement {
    let object: AnyObject
    private var modern: (any NSAccessibilityProtocol)? { object as? any NSAccessibilityProtocol }
    private var legacy: NSObject? { object as? NSObject }

    private func attribute(_ name: NSAccessibility.Attribute) -> Any? {
        guard let legacy, legacy.accessibilityAttributeNames().contains(name) else { return nil }
        return legacy.accessibilityAttributeValue(name)
    }

    private var legacyRole: NSAccessibility.Role? {
        (attribute(.role) as? String).map { NSAccessibility.Role(rawValue: $0) }
    }

    private var usesLegacy: Bool {
        legacyRole != nil && (modern?.accessibilityRole() == nil || modern?.accessibilityRole() == .unknown)
    }

    var role: NSAccessibility.Role? { usesLegacy ? legacyRole : modern?.accessibilityRole() }
    var identifier: String? {
        usesLegacy ? attribute(.identifier) as? String : modern?.accessibilityIdentifier()
    }
    var label: String? {
        usesLegacy ? attribute(.description) as? String : modern?.accessibilityLabel()
    }
    var title: String? { usesLegacy ? attribute(.title) as? String : modern?.accessibilityTitle() }
    var value: Any? { usesLegacy ? attribute(.value) : modern?.accessibilityValue() }
    var isElement: Bool {
        usesLegacy ? legacy?.accessibilityIsIgnored() == false : modern?.isAccessibilityElement() == true
    }
    var isEnabled: Bool {
        if !usesLegacy, let modern { return modern.isAccessibilityEnabled() }
        guard let enabled = attribute(.enabled) as? Bool else {
            XCTFail("The actual accessibility control did not expose an enabled state.")
            return false
        }
        return enabled
    }
    var isFocused: Bool {
        if !usesLegacy, let modern { return modern.isAccessibilityFocused() }
        guard let focused = attribute(.focused) as? Bool else {
            XCTFail("The actual accessibility control did not expose a focus state.")
            return false
        }
        return focused
    }

    var children: [AnyObject] {
        let modernChildren = (modern?.accessibilityChildren() ?? []).map { $0 as AnyObject }
        // SwiftUI may expose its semantic children through AppKit's informal API.
        let legacyChildren = (attribute(.children) as? [Any] ?? []).map { $0 as AnyObject }
        return modernChildren + legacyChildren
    }

    var frame: NSRect {
        if !usesLegacy, let modern { return modern.accessibilityFrame() }
        guard let position = attribute(.position) as? NSValue, let size = attribute(.size) as? NSValue else {
            XCTFail("The actual accessibility element did not expose a screen frame.")
            return .zero
        }
        return NSRect(origin: position.pointValue, size: size.sizeValue)
    }

    func focus() {
        if usesLegacy, let legacy {
            guard legacy.accessibilityIsAttributeSettable(.focused) else {
                XCTFail("The actual control must allow its focus attribute to be set.")
                return
            }
            legacy.accessibilitySetValue(true, forAttribute: .focused)
        } else if let modern {
            guard modern.isAccessibilitySelectorAllowed(
                #selector(NSAccessibilityProtocol.setAccessibilityFocused(_:))) else {
                XCTFail("The actual control must allow the focus selector.")
                return
            }
            modern.setAccessibilityFocused(true)
        } else {
            XCTFail("The actual accessibility element has no focus interface.")
        }
    }

    func press() {
        if usesLegacy, let legacy {
            let supported = legacy.accessibilityActionNames().contains(.press)
            XCTAssertTrue(supported, "The actual control must advertise its press action.")
            if supported { legacy.accessibilityPerformAction(.press) }
        } else if let modern {
            XCTAssertTrue(modern.accessibilityPerformPress(), "Dispatch the real control action, not a model setter.")
        } else {
            XCTFail("The actual accessibility element has no press interface.")
        }
    }
}

@MainActor
struct NativeSettingsTestControl {
    let element: NativeSettingsAXElement
    let root: NSView
    let onValue: String?
    let offValue: String?

    var isEnabled: Bool { element.isEnabled }
    var isFocused: Bool { element.isFocused }
    var state: NSControl.StateValue? {
        if let button = element.object as? NSButton { return button.state }
        let value = element.value
        if let number = value as? NSNumber {
            switch number.intValue {
            case 0: return .off
            case 1: return .on
            case -1: return .mixed
            default: return nil
            }
        }
        if let value = value as? String {
            if value == onValue { return .on }
            if value == offValue { return .off }
        }
        return nil
    }

    var frame: NSRect { element.frame }

    var visibleRect: NSRect {
        guard let window = root.window else { return .zero }
        let screenBounds = window.convertToScreen(root.convert(root.visibleRect, to: nil))
        return frame.intersection(screenBounds)
    }

    func sameElement(as other: NativeSettingsTestControl) -> Bool {
        element.object === other.element.object
    }

    func focus(in window: NSWindow) {
        if let view = element.object as? NSView {
            XCTAssertTrue(view.acceptsFirstResponder)
            XCTAssertTrue(window.makeFirstResponder(view))
        } else {
            element.focus()
        }
    }

    func press() {
        XCTAssertTrue(isEnabled)
        element.press()
    }
}

@MainActor
enum NativeSettingsTestControls {
    private enum LookupError: Error { case missingOrAmbiguousControl }

    private static func objects(from roots: [AnyObject]) -> [AnyObject] {
        var pending = roots
        var visited = Set<ObjectIdentifier>()
        var result: [AnyObject] = []
        while let object = pending.popLast() {
            guard visited.insert(ObjectIdentifier(object)).inserted else { continue }
            result.append(object)
            pending.append(contentsOf: NativeSettingsAXElement(object: object).children)
            if let view = object as? NSView {
                pending.append(contentsOf: view.subviews.map { $0 as AnyObject })
            }
        }
        return result
    }

    private static func description(_ element: NativeSettingsAXElement) -> String {
        "\(type(of: element.object)) role=\(element.role?.rawValue ?? "-") " +
            "id=\(element.identifier ?? "-") label=\(element.label ?? "-") " +
            "title=\(element.title ?? "-") value=\(String(describing: element.value))"
    }

    static func resolve(in root: NSView, identifier: String, label: String, role: NSAccessibility.Role,
                        onValue: String? = nil, offValue: String? = nil) throws -> NativeSettingsTestControl {
        // Present the fixture before querying SwiftUI's live semantic tree.
        if let window = root.window, !window.isVisible { window.orderFront(nil) }
        var roots: [AnyObject] = [root]
        if let window = root.window { roots.append(window) }
        let deadline = Date().addingTimeInterval(1)
        var raw: [AnyObject] = []
        var inventory: [NativeSettingsAXElement] = []
        var candidates: [NativeSettingsAXElement] = []
        repeat {
            root.layoutSubtreeIfNeeded()
            root.displayIfNeeded()
            raw = objects(from: roots)
            inventory = raw.map { NativeSettingsAXElement(object: $0) }
            let anchors = inventory.filter {
                $0.identifier == identifier || $0.label == label || $0.title == label
            }
            let actionable = { (element: NativeSettingsAXElement) in
                element.isElement && element.role == role
            }
            candidates = anchors.filter { actionable($0) }
            if candidates.isEmpty {
                // SwiftUI can put the identifier on an AX container, not its actionable child.
                candidates = objects(from: anchors.map(\.object))
                    .map { NativeSettingsAXElement(object: $0) }.filter { actionable($0) }
            }
            if !candidates.isEmpty { break }
            _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
        } while Date() < deadline
        guard candidates.count == 1, let element = candidates.first else {
            XCTFail("Expected one \(identifier) \(role.rawValue); found \(candidates.count).\n" +
                    inventory.map { description($0) }.joined(separator: "\n"))
            throw LookupError.missingOrAmbiguousControl
        }
        print("Resolved native settings control: \(description(element))")
        return NativeSettingsTestControl(element: element, root: root, onValue: onValue, offValue: offValue)
    }
}

@MainActor
private enum SummarySettingsControls {
    static func toggle(in root: NSView, model: ProbeModel) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(
            in: root, identifier: "automatic-long-text-summary",
            label: model.text("Automatic long-text summary", "长文自动摘要"), role: .checkBox,
            onValue: model.text("On", "已开启"), offValue: model.text("Off", "已关闭"))
    }

    static func reload(in root: NSView, model: ProbeModel) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(
            in: root, identifier: "reload-summary-setting",
            label: model.text("Reload saved summary setting", "重新读取已保存的摘要设置"), role: .button)
    }
}

@MainActor
private final class SummarySettingsHost {
    let host: NSHostingView<SummarySettingsSurface>
    let window: NSWindow
    let model: ProbeModel

    init(model: ProbeModel) {
        _ = NSApplication.shared
        self.model = model
        host = NSHostingView(rootView: SummarySettingsSurface(model: model))
        let size = NSSize(width: 640, height: 860)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .titled,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func waitFor(_ condition: @MainActor () -> Bool) async throws {
        try await CaptureProductFixture.waitFor {
            self.flush()
            return condition()
        }
    }

    func toggle() throws -> NativeSettingsTestControl {
        flush()
        return try SummarySettingsControls.toggle(in: host, model: model)
    }

    func close() {
        window.contentView = nil
        window.close()
    }
}

final class SummaryPreferenceInteractionTests: XCTestCase {
    @MainActor
    func testEnglishAndChineseNativeCheckboxSavesReadbacksAndReopensWithoutCLI() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = SummarySettingsHost(model: f.model)
            defer { surface.close() }
            let button = try surface.toggle()
            XCTAssertTrue(button.isEnabled)
            XCTAssertEqual(button.state, .on)
            surface.window.makeKeyAndOrderFront(nil)
            button.focus(in: surface.window)
            try await surface.waitFor { button.isFocused }
            let source = f.model.input
            button.press()
            try await surface.waitFor { helper.configurationSaves.count == 1 && !button.isEnabled }
            let save = try XCTUnwrap(helper.configurationSaves.last)
            XCTAssertEqual(save.config["summary_enabled"], .bool(false))
            XCTAssertEqual(f.model.summaryEnabled, true)
            XCTAssertEqual(f.model.summaryPreferencePhase, .saving)
            helper.event("completed", id: save.id)
            try await surface.waitFor { f.model.summaryPreferencePhase == .readingBack && !button.isEnabled }
            try f.finishConfiguration(on: helper, configuration: save.config)
            try await surface.waitFor { button.state == .off && button.isEnabled }
            XCTAssertTrue(try surface.toggle().sameElement(as: button), "Keep the same real control through save/readback.")
            XCTAssertEqual(f.model.summaryPreferencePhase, .saved)
            XCTAssertEqual(f.model.input, source)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.resultActions.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)
            XCTAssertFalse(f.model.cliBusy)
            XCTAssertEqual(f.model.permissions, "Not checked.")

            f.model.stopHelper()
            helper.stopped()
            f.model.openProduct()
            let reopened = try f.ready(configuration: save.config)
            try await surface.waitFor { button.state == .off && button.isEnabled }
            button.press()
            try await surface.waitFor { reopened.configurationSaves.count == 1 }
            try SummaryPreferenceFixture.completeSave(f, helper: reopened)
            try await surface.waitFor { button.state == .on && button.isEnabled }
            XCTAssertEqual(f.model.summaryEnabled, true)
            XCTAssertTrue(reopened.translations.isEmpty)
            XCTAssertTrue(reopened.resultActions.isEmpty)
        }
    }

    @MainActor
    func testNativeReloadRecoversFailedSaveAndMismatchWithoutImplicitRewriteInBothLanguages() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = SummarySettingsHost(model: f.model)
            defer { surface.close() }
            let button = try surface.toggle()
            button.press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                         payload: ["code": .string("config_io_failed")])
            try await surface.waitFor { !button.isEnabled && f.model.summaryEnabled == nil }
            let reload = try SummarySettingsControls.reload(in: surface.host, model: f.model)
            XCTAssertTrue(reload.isEnabled)
            surface.window.makeKeyAndOrderFront(nil)
            reload.focus(in: surface.window)
            try await surface.waitFor { reload.isFocused }
            reload.press()
            try await surface.waitFor { helper.configurationLoads.count == 2 }
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { button.state == .on && button.isEnabled }
            XCTAssertEqual(helper.configurationSaves.count, 1)
            button.press()
            try await surface.waitFor { helper.configurationSaves.count == 2 }
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { button.isEnabled && button.state == .on }
            XCTAssertEqual(f.model.summaryPreferencePhase, .differentReadback)
            XCTAssertEqual(helper.configurationSaves.count, 2, "Mismatch is shown, not silently fixed with another save.")
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testAutomaticSummarySettingRendersConfirmedEnglishLightAndChineseDark() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        for (language, scheme, name) in [
            ("en", ColorScheme.light, "summary-setting-en-light"),
            ("zh", .dark, "summary-setting-zh-dark")
        ] {
            f.model.interfaceLanguage = language
            f.model.appearance = scheme == .dark ? "dark" : "light"
            let png = try render(SummarySettingsSurface(model: f.model), named: name,
                                 size: NSSize(width: 640, height: 860), scheme: scheme, inspect: { host in
                let button = try SummarySettingsControls.toggle(in: host, model: f.model)
                XCTAssertTrue(button.isEnabled)
                XCTAssertEqual(button.state, .on)
                XCTAssertGreaterThan(button.visibleRect.height, 0)
            })
            let words = try summarySettingsWords(png, chinese: language == "zh")
            for expected in language == "zh"
                ? ["长文自动摘要", "400", "后续翻译", "生成摘要"]
                : ["automaticlong-textsummary", "400", "futuretranslations", "summarize"] {
                XCTAssertTrue(words.contains(expected), words)
            }
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertEqual(helper.configurationLoads.count, 1)
    }

    @MainActor
    func testAutomaticSummaryFailureRendersUnknownStateAndExplicitRecoveryWithoutFalseSuccess() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready()
        f.model.interfaceLanguage = "en"
        f.model.saveSummaryPreference(false)
        helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                     payload: ["code": .string("config_io_failed")])
        let png = try render(SummarySettingsSurface(model: f.model),
                             named: "summary-setting-save-failed-en-light",
                             size: NSSize(width: 640, height: 860), scheme: .light, inspect: { host in
            XCTAssertFalse(try SummarySettingsControls.toggle(in: host, model: f.model).isEnabled)
            XCTAssertTrue(try SummarySettingsControls.reload(in: host, model: f.model).isEnabled)
        })
        let words = try summarySettingsWords(png)
        XCTAssertTrue(words.contains("couldnotbeconfirmed"), words)
        XCTAssertTrue(words.contains("reloadsavedsummarysetting"), words)
        XCTAssertFalse(words.contains("savedandreadback"), words)
        XCTAssertEqual(helper.configurationSaves.count, 1)
        XCTAssertEqual(helper.configurationLoads.count, 1)
    }

    @MainActor
    private func summarySettingsWords(_ png: Data, chinese: Bool = false) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined().lowercased().filter { !$0.isWhitespace }
    }
}
