import AppKit
import Vision
import XCTest

@MainActor
enum NativeRenderEvidence {
    private static var messages: [String] = []

    static func record(_ message: String) throws {
        print(message)
        guard let directory = ProcessInfo.processInfo.environment["CC_TRANSLATE_UI_SCREENSHOTS_DIR"] else { return }
        let folder = URL(fileURLWithPath: directory, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        messages.append(message)
        try messages.joined(separator: "\n").write(
            to: folder.appendingPathComponent("native-render-diagnostics.txt"), atomically: true, encoding: .utf8)
    }

    static func recognitionImage(_ image: CGImage) throws -> CGImage {
        // Enlarge a recognition-only copy of small UI glyphs; keep the original review PNG unchanged.
        let context = try XCTUnwrap(CGContext(
            data: nil, width: image.width * 2, height: image.height * 2, bitsPerComponent: 8,
            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: CGFloat(image.width * 2), height: CGFloat(image.height * 2)))
        return try XCTUnwrap(context.makeImage())
    }
}

enum NativeRenderedControlKind: String {
    case button, toggle
}

@MainActor
protocol NativeRenderedTestRegion {
    var frame: NSRect { get }
    var visibleRect: NSRect { get }
}

private enum RenderedLookupError: Error {
    case detachedView, unavailableBitmap, missingOrAmbiguousControl, missingOrAmbiguousCaption, focusFailed, disabled, unexpectedEvent
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

    private static func samePointerEvent(_ actual: NSEvent, _ expected: NSEvent) -> Bool {
        actual.type == expected.type && actual.windowNumber == expected.windowNumber &&
            actual.locationInWindow == expected.locationInWindow &&
            abs(actual.timestamp - expected.timestamp) < 0.000_001
    }

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
    fileprivate let kind: NativeRenderedControlKind

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
        try NativeRenderEvidence.record("Rendered focus \(identifier): acceptsFirstResponder=\(control.acceptsFirstResponder)")
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
        try NativeRenderEvidence.record("Rendered press \(identifier): state=\(backing.state.rawValue), " +
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
        // Dequeue before dispatch so gesture-backed SwiftUI controls see the correct currentEvent.
        NSApp.postEvent(down, atStart: true)
        let pending = try XCTUnwrap(NSApp.nextEvent(matching: .leftMouseDown, until: .distantPast,
                                                   inMode: .default, dequeue: false))
        try NativeRenderEvidence.record("Queued event \(identifier): " +
            "actual=(number:\(pending.eventNumber), window:\(pending.windowNumber), point:\(pending.locationInWindow), time:\(pending.timestamp)); " +
            "created=(number:\(down.eventNumber), window:\(down.windowNumber), point:\(down.locationInWindow), time:\(down.timestamp))")
        guard Self.samePointerEvent(pending, down) else {
            XCTFail("Only this fixture's queued pointer event may be dispatched.", file: file, line: line)
            throw RenderedLookupError.unexpectedEvent
        }
        let press = try XCTUnwrap(NSApp.nextEvent(matching: .leftMouseDown, until: .distantPast,
                                                 inMode: .default, dequeue: true))
        let current = NSApp.currentEvent
        let currentNumber = current?.type == .leftMouseDown ? current?.eventNumber : nil
        try NativeRenderEvidence.record("Queued press \(identifier): currentEvent=\(String(describing: current?.type)), " +
            "currentNumber=\(String(describing: currentNumber)), expectedNumber=\(number)")
        var releasedDuringTracking = false
        let timer = Timer(timeInterval: 0.01, repeats: false) { _ in
            MainActor.assumeIsolated {
                releasedDuringTracking = true
                NSApp.postEvent(up, atStart: true)
            }
        }
        RunLoop.main.add(timer, forMode: .eventTracking)
        defer { timer.invalidate() }
        // Native buttons own cell tracking; SwiftUI checkboxes also need gesture dispatch.
        switch kind {
        case .button: control.mouseDown(with: press)
        case .toggle: NSApp.sendEvent(press)
        }
        if !releasedDuringTracking {
            timer.invalidate()
            NSApp.postEvent(up, atStart: true)
        }
        if let queued = NSApp.nextEvent(matching: .leftMouseUp, until: .distantPast,
                                        inMode: .default, dequeue: false),
           Self.samePointerEvent(queued, up) {
            let release = try XCTUnwrap(NSApp.nextEvent(matching: .leftMouseUp, until: .distantPast,
                                                       inMode: .default, dequeue: true))
            XCTAssertTrue(Self.samePointerEvent(release, up))
            switch kind {
            case .button: control.mouseUp(with: release)
            case .toggle: NSApp.sendEvent(release)
            }
        }
        try NativeRenderEvidence.record("Rendered pressed \(identifier): state=\(backing.state.rawValue), " +
            "enabled=\(isEnabled), trackingRelease=\(releasedDuringTracking), attached=\(control.window === window)")
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
        try VNImageRequestHandler(cgImage: NativeRenderEvidence.recognitionImage(image)).perform([request])
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

    private struct Lookup {
        let candidates: [NativeSettingsTestControl.Backing]
        let readback: Readback?
        let route: String
    }

    private static func lookup(in root: NSView, identifier: String, label: String,
                               kind: NativeRenderedControlKind) throws -> Lookup {
        guard !identifier.isEmpty, !normalize(label).isEmpty else {
            XCTFail("A rendered-control lookup needs a fixture identifier and visible caption.")
            throw RenderedLookupError.missingOrAmbiguousControl
        }
        try prepare(root)
        let controls = views(in: root).compactMap { backing($0, kind: kind) }.filter {
            !RenderedGeometry.visibleRect($0.control).isEmpty
        }
        let named = controls.filter {
            $0.control.identifier?.rawValue == identifier ||
                ($0.control as? NSButton).map { normalize($0.title) == normalize(label) } == true
        }
        if !named.isEmpty {
            return Lookup(candidates: named, readback: nil, route: "public identifier/title")
        }
        let pixels = try readCaption(label, in: root)
        let candidates = controls.flatMap { backing in
            pixels.matches.compactMap { match in
                matches(backing.control, caption: match.rectangle, kind: kind, tolerance: pixels.tolerance)
                    ? backing : nil
            }
        }
        return Lookup(candidates: candidates, readback: pixels, route: "native bitmap caption geometry")
    }

    private static func resolved(_ lookup: Lookup, in root: NSView, identifier: String, label: String,
                                 kind: NativeRenderedControlKind) throws -> NativeSettingsTestControl {
        guard lookup.candidates.count == 1, let candidate = lookup.candidates.first else {
            XCTFail("Expected one rendered \(kind.rawValue), found \(lookup.candidates.count). " +
                    "Only public NSButton/NSSwitch controls are actionable.\n" +
                    diagnostics(root, identifier: identifier, caption: label, readback: lookup.readback))
            throw RenderedLookupError.missingOrAmbiguousControl
        }
        let type: String
        switch candidate {
        case .button: type = "NSButton"
        case .toggle: type = "NSSwitch"
        }
        try NativeRenderEvidence.record("Resolved rendered \(kind.rawValue) \(identifier) as \(type) via \(lookup.route); " +
              "frame=\(RenderedGeometry.frame(candidate.control)); fixture caption=\(label)")
        return NativeSettingsTestControl(backing: candidate, root: root, identifier: identifier, kind: kind)
    }

    static func resolve(in root: NSView, identifier: String, label: String,
                        kind: NativeRenderedControlKind) throws -> NativeSettingsTestControl {
        let deadline = Date().addingTimeInterval(2)
        var result = try lookup(in: root, identifier: identifier, label: label, kind: kind)
        while result.candidates.isEmpty && Date() < deadline {
            _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
            result = try lookup(in: root, identifier: identifier, label: label, kind: kind)
        }
        return try resolved(result, in: root, identifier: identifier, label: label, kind: kind)
    }

    static func resolveWhenReady(in root: NSView, identifier: String, label: String,
                                 kind: NativeRenderedControlKind) async throws -> NativeSettingsTestControl {
        let deadline = Date().addingTimeInterval(2)
        var result: Lookup
        repeat {
            // Yield the actor so conditional SwiftUI controls can finish updating their native tree.
            try await Task.sleep(nanoseconds: 10_000_000)
            result = try lookup(in: root, identifier: identifier, label: label, kind: kind)
        } while result.candidates.isEmpty && Date() < deadline
        return try resolved(result, in: root, identifier: identifier, label: label, kind: kind)
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
