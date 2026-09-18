import AppKit
import Vision
import XCTest

enum NativeRenderedControlKind: String {
    case button, toggle
}

@MainActor
protocol NativeRenderedTestRegion {
    var frame: NSRect { get }
    var visibleRect: NSRect { get }
}

private enum RenderedLookupError: Error {
    case detachedView, unavailableBitmap, missingOrAmbiguousControl, missingOrAmbiguousCaption, focusFailed, disabled
}

@MainActor
private enum RenderedGeometry {
    static func frame(_ view: NSView) -> NSRect {
        if let control = view as? NSControl, let parent = control.superview {
            // Native bezel/shadow outsets are not the control's layout content.
            return parent.convert(control.alignmentRect(forFrame: control.frame), to: nil)
        }
        return view.convert(view.bounds, to: nil)
    }

    static func visibleRect(_ view: NSView) -> NSRect {
        guard view.window != nil else { return .zero }
        var rectangle = frame(view)
        var ancestor: NSView? = view
        while let current = ancestor {
            if current.isHidden || current.alphaValue <= 0 { return .zero }
            rectangle = rectangle.intersection(current.convert(current.visibleRect, to: nil))
            if rectangle.isEmpty { return .zero }
            ancestor = current.superview
        }
        return rectangle
    }
}

@MainActor
struct NativeSettingsTestControl: NativeRenderedTestRegion {
    private static var nextMouseEventNumber = 200_000
    @MainActor
    fileprivate enum Backing {
        case button(NSButton)
        case toggle(NSSwitch)

        var control: NSControl {
            switch self {
            case .button(let control): return control
            case .toggle(let control): return control
            }
        }

        var state: NSControl.StateValue {
            switch self {
            case .button(let control): return control.state
            case .toggle(let control): return control.state
            }
        }
    }

    fileprivate let backing: Backing
    fileprivate let root: NSView
    fileprivate let identifier: String

    var isEnabled: Bool { backing.control.isEnabled }
    var isFocused: Bool {
        guard let window = backing.control.window else { return false }
        return window.firstResponder === backing.control
    }
    var state: NSControl.StateValue? {
        let value = backing.state
        guard value == .on || value == .off || value == .mixed else {
            XCTFail("The rendered control has an unsupported state: \(value.rawValue).")
            return nil
        }
        return value
    }
    var frame: NSRect { RenderedGeometry.frame(backing.control) }
    var visibleRect: NSRect { RenderedGeometry.visibleRect(backing.control) }

    func sameElement(as other: NativeSettingsTestControl) -> Bool {
        backing.control === other.backing.control
    }

    func focus(in window: NSWindow) throws {
        let control = backing.control
        guard control.window === window, root.window === window else { throw RenderedLookupError.detachedView }
        window.makeKeyAndOrderFront(nil)
        print("Rendered focus \(identifier): acceptsFirstResponder=\(control.acceptsFirstResponder)")
        XCTAssertTrue(window.makeFirstResponder(control))
        XCTAssertTrue(window.firstResponder === control, "The actual NSControl must own the responder focus.")
        guard window.firstResponder === control else { throw RenderedLookupError.focusFailed }
    }

    func press(file: StaticString = #filePath, line: UInt = #line) async throws {
        let control = backing.control
        try await CaptureProductFixture.waitFor(file: file, line: line) {
            root.layoutSubtreeIfNeeded()
            root.displayIfNeeded()
            return control.isEnabled
        }
        guard let window = root.window, control.window === window,
              control.isDescendant(of: root), !visibleRect.isEmpty else {
            XCTFail("The resolved control must still be attached and visible in its fixture.")
            throw RenderedLookupError.detachedView
        }
        XCTAssertTrue(isEnabled)
        guard isEnabled else { throw RenderedLookupError.disabled }
        print("Rendered press \(identifier): state=\(backing.state.rawValue), " +
              "action=\(String(describing: control.action)), targetPresent=\(control.target != nil)")
        window.makeKeyAndOrderFront(nil)
        let point = NSPoint(x: frame.midX, y: frame.midY)
        let number = Self.nextMouseEventNumber
        Self.nextMouseEventNumber += 2
        let down = try XCTUnwrap(NSEvent.mouseEvent(
            with: .leftMouseDown, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, eventNumber: number, clickCount: 1, pressure: 1))
        let up = try XCTUnwrap(NSEvent.mouseEvent(
            with: .leftMouseUp, location: point, modifierFlags: [], timestamp: down.timestamp + 0.1,
            windowNumber: window.windowNumber, context: nil, eventNumber: number + 1, clickCount: 1, pressure: 0))
        // Tracking controls may consume mouse-up before sendEvent returns.
        NSApp.postEvent(up, atStart: true)
        NSApp.sendEvent(down)
        if let queued = NSApp.nextEvent(matching: .leftMouseUp, until: .distantPast,
                                        inMode: .default, dequeue: false),
           queued.windowNumber == up.windowNumber, queued.eventNumber == up.eventNumber {
            let release = try XCTUnwrap(NSApp.nextEvent(matching: .leftMouseUp, until: .distantPast,
                                                       inMode: .default, dequeue: true))
            XCTAssertEqual(release.eventNumber, up.eventNumber)
            NSApp.sendEvent(release)
        }
        print("Rendered pressed \(identifier): state=\(backing.state.rawValue), enabled=\(isEnabled)")
    }
}

@MainActor
struct NativeRenderedTestCaption: NativeRenderedTestRegion {
    let frame: NSRect
    let visibleRect: NSRect
}

@MainActor
enum NativeSettingsTestControls {
    private struct CaptionMatch {
        let rectangle: NSRect
        let excerpt: String
    }

    private struct Readback {
        let matches: [CaptionMatch]
        let fragments: [String]
        let observationCount: Int
        let tolerance: CGFloat
    }

    private static func views(in root: NSView) -> [NSView] {
        [root] + root.subviews.flatMap { views(in: $0) }
    }

    private static func backing(_ view: NSView, kind: NativeRenderedControlKind) -> NativeSettingsTestControl.Backing? {
        if view is NSPopUpButton { return nil }
        if let button = view as? NSButton { return .button(button) }
        if kind == .toggle, let toggle = view as? NSSwitch { return .toggle(toggle) }
        return nil
    }

    private static func normalize(_ value: String) -> String {
        value.filter { !$0.isWhitespace }.folding(
            options: [.caseInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
    }

    private static func ranges(of caption: String, in text: String) -> [Range<String.Index>] {
        let expected = Array(normalize(caption))
        guard !expected.isEmpty else { return [] }
        var characters: [Character] = []
        var originals: [Range<String.Index>] = []
        for index in text.indices {
            let end = text.index(after: index)
            for character in normalize(String(text[index])) {
                characters.append(character)
                originals.append(index..<end)
            }
        }
        guard characters.count >= expected.count else { return [] }
        func wordCharacter(_ character: Character) -> Bool {
            character.isASCII && (character.isLetter || character.isNumber || character == "_")
        }
        var result: [Range<String.Index>] = []
        for start in 0...(characters.count - expected.count) {
            guard characters[start..<(start + expected.count)].elementsEqual(expected) else { continue }
            let range = originals[start].lowerBound..<originals[start + expected.count - 1].upperBound
            if let first = caption.first, wordCharacter(first), range.lowerBound > text.startIndex,
               wordCharacter(text[text.index(before: range.lowerBound)]) { continue }
            if let last = caption.last, wordCharacter(last), range.upperBound < text.endIndex,
               wordCharacter(text[range.upperBound]) { continue }
            result.append(range)
        }
        return result
    }

    private static func prepare(_ root: NSView) throws {
        guard let window = root.window else {
            XCTFail("Rendered-control lookup requires an attached fixture window.")
            throw RenderedLookupError.detachedView
        }
        if !window.isVisible { window.orderFront(nil) }
        root.layoutSubtreeIfNeeded()
        root.displayIfNeeded()
    }

    private static func readCaption(_ caption: String, in root: NSView) throws -> Readback {
        let bounds = root.bounds
        guard bounds.width > 0, bounds.height > 0,
              let bitmap = root.bitmapImageRepForCachingDisplay(in: bounds) else {
            XCTFail("The fixture cannot provide a native bitmap for its caption.")
            throw RenderedLookupError.unavailableBitmap
        }
        root.effectiveAppearance.performAsCurrentDrawingAppearance {
            root.cacheDisplay(in: bounds, to: bitmap)
        }
        guard let image = bitmap.cgImage else {
            XCTFail("The native fixture bitmap has no CGImage.")
            throw RenderedLookupError.unavailableBitmap
        }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.minimumTextHeight = 0
        request.recognitionLanguages = caption.unicodeScalars.allSatisfy { $0.isASCII }
            ? ["en-US"] : ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([request])
        var matches: [CaptionMatch] = []
        var fragments: [String] = []
        let words = Set(caption.split(whereSeparator: { $0.isWhitespace }).map(String.init))
        for observation in request.results ?? [] {
            guard let candidate = observation.topCandidates(1).first else { continue }
            // Diagnostics may include only exact pieces of the requested fixture caption.
            for word in words where word.count > 1 && normalize(word) != normalize(caption) {
                for range in ranges(of: word, in: candidate.string) {
                    if fragments.count < 8 { fragments.append(String(candidate.string[range])) }
                }
            }
            for range in ranges(of: caption, in: candidate.string) {
                guard let box = try candidate.boundingBox(for: range) else { continue }
                let normalized = box.boundingBox
                guard !normalized.isEmpty else { continue }
                // Vision uses the image's lower-left origin; NSHostingView can be flipped.
                let rectangle = NSRect(
                    x: bounds.minX + normalized.minX * bounds.width,
                    y: root.isFlipped ? bounds.maxY - normalized.maxY * bounds.height :
                        bounds.minY + normalized.minY * bounds.height,
                    width: normalized.width * bounds.width, height: normalized.height * bounds.height)
                matches.append(CaptionMatch(rectangle: root.convert(rectangle, to: nil),
                                            excerpt: String(candidate.string[range])))
            }
        }
        return Readback(matches: matches, fragments: fragments, observationCount: request.results?.count ?? 0,
                        tolerance: max(bounds.width / CGFloat(image.width), bounds.height / CGFloat(image.height)))
    }

    private static func matches(_ control: NSControl, caption: NSRect, kind: NativeRenderedControlKind,
                                tolerance: CGFloat) -> Bool {
        let rectangle = RenderedGeometry.frame(control)
        let expanded = rectangle.insetBy(dx: -tolerance, dy: -tolerance)
        let intersection = expanded.intersection(caption)
        // Vision's substring box can extend past a glyph into the next label.
        if expanded.contains(NSPoint(x: caption.midX, y: caption.midY)),
           !intersection.isEmpty, intersection.width * intersection.height >= caption.width * caption.height / 2 {
            return true
        }
        guard kind == .toggle, rectangle.midX < caption.minX + tolerance else { return false }
        // Checkbox captions may be separate SwiftUI drawing, beside the actual public control.
        return (expanded.minY...expanded.maxY).contains(caption.midY) ||
            ((caption.minY - tolerance)...(caption.maxY + tolerance)).contains(rectangle.midY)
    }

    private static func diagnostics(_ root: NSView, identifier: String, caption: String,
                                    readback: Readback?) -> String {
        let controls = views(in: root).compactMap { $0 as? NSControl }.map { control in
            let name: String
            if control is NSPopUpButton { name = "NSPopUpButton" }
            else if control is NSButton { name = "NSButton" }
            else if control is NSSwitch { name = "NSSwitch" }
            else if control is NSTextField { name = "NSTextField" }
            else { name = "NSControl (unsupported public control type)" }
            return "\(name) layout=\(RenderedGeometry.frame(control)) " +
                "nativeBounds=\(control.convert(control.bounds, to: nil)) visible=\(RenderedGeometry.visibleRect(control))"
        }
        let excerpts = readback?.matches.map { "caption=\($0.excerpt) bounds=\($0.rectangle)" } ?? []
        return "Fixture \(identifier), expected caption=\(caption).\n" +
            controls.joined(separator: "\n") +
            "\nOCR observations=\(readback?.observationCount ?? 0); matched fixture captions:\n" +
            (excerpts.isEmpty ? "<none; other rendered text omitted>" : excerpts.joined(separator: "\n")) +
            "\nExact fixture-caption fragments: \((readback?.fragments ?? []).joined(separator: " | "))"
    }

    static func resolve(in root: NSView, identifier: String, label: String,
                        kind: NativeRenderedControlKind) throws -> NativeSettingsTestControl {
        guard !identifier.isEmpty, !normalize(label).isEmpty else {
            XCTFail("A rendered-control lookup needs a fixture identifier and visible caption.")
            throw RenderedLookupError.missingOrAmbiguousControl
        }
        let deadline = Date().addingTimeInterval(2)
        var readback: Readback?
        var candidates: [NativeSettingsTestControl.Backing] = []
        var route = "public identifier/title"
        repeat {
            try prepare(root)
            let controls = views(in: root).compactMap { backing($0, kind: kind) }.filter {
                !RenderedGeometry.visibleRect($0.control).isEmpty
            }
            candidates = controls.filter {
                $0.control.identifier?.rawValue == identifier ||
                    ($0.control as? NSButton).map { normalize($0.title) == normalize(label) } == true
            }
            if !candidates.isEmpty { break }
            let pixels = try readCaption(label, in: root)
            readback = pixels
            candidates = controls.flatMap { backing in
                pixels.matches.compactMap { match in
                    matches(backing.control, caption: match.rectangle, kind: kind, tolerance: pixels.tolerance)
                        ? backing : nil
                }
            }
            if !candidates.isEmpty { route = "native bitmap caption geometry"; break }
            _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
        } while Date() < deadline
        guard candidates.count == 1, let candidate = candidates.first else {
            XCTFail("Expected one rendered \(kind.rawValue), found \(candidates.count). " +
                    "Only public NSButton/NSSwitch controls are actionable.\n" +
                    diagnostics(root, identifier: identifier, caption: label, readback: readback))
            throw RenderedLookupError.missingOrAmbiguousControl
        }
        let type: String
        switch candidate {
        case .button: type = "NSButton"
        case .toggle: type = "NSSwitch"
        }
        print("Resolved rendered \(kind.rawValue) \(identifier) as \(type) via \(route); " +
              "frame=\(RenderedGeometry.frame(candidate.control)); fixture caption=\(label)")
        return NativeSettingsTestControl(backing: candidate, root: root, identifier: identifier)
    }

    static func caption(in root: NSView, identifier: String, label: String) throws -> NativeRenderedTestCaption {
        let deadline = Date().addingTimeInterval(2)
        var readback: Readback?
        repeat {
            try prepare(root)
            let pixels = try readCaption(label, in: root)
            readback = pixels
            if !pixels.matches.isEmpty { break }
            _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
        } while Date() < deadline
        guard let readback, readback.matches.count == 1, let match = readback.matches.first else {
            XCTFail("Expected one rendered fixture caption.\n" +
                    diagnostics(root, identifier: identifier, caption: label, readback: readback))
            throw RenderedLookupError.missingOrAmbiguousCaption
        }
        return NativeRenderedTestCaption(frame: match.rectangle,
                                         visibleRect: match.rectangle.intersection(RenderedGeometry.visibleRect(root)))
    }
}

@MainActor
struct NativeTestWindowFocus {
    private weak var previousWindow: NSWindow?
    private weak var previousResponder: NSResponder?

    init() {
        let application = NSApplication.shared
        previousWindow = application.keyWindow
        previousResponder = application.keyWindow?.firstResponder
    }

    func close(_ window: NSWindow) {
        let restore = window.isKeyWindow
        window.orderOut(nil)
        window.contentView = nil
        window.close()
        XCTAssertFalse(window.isVisible)
        guard restore, let previousWindow, previousWindow.isVisible else { return }
        previousWindow.makeKeyAndOrderFront(nil)
        if let responder = previousResponder,
           responder === previousWindow || (responder as? NSView)?.window === previousWindow {
            XCTAssertTrue(previousWindow.makeFirstResponder(responder))
            XCTAssertTrue(previousWindow.firstResponder === responder)
        }
    }
}
