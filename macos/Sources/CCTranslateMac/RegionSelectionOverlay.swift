import AppKit
import CCTranslateSupport

struct RegionSelectionState {
    enum Outcome: Equatable {
        case pending
        case selected(CGRect)
        case tooSmall
        case cancelled
    }

    private(set) var rectangle: CGRect?
    private var anchor: CGPoint?
    private var press: CGPoint?
    private var completingClick = false
    private var dragged = false
    static let minimumSize: CGFloat = 10

    mutating func mouseDown(at point: CGPoint) {
        completingClick = anchor != nil
        if anchor == nil { anchor = point }
        press = point
        dragged = false
    }

    mutating func mouseMoved(to point: CGPoint) {
        guard let anchor else { return }
        rectangle = Self.rectangle(from: anchor, to: point)
    }

    mutating func mouseDragged(to point: CGPoint) {
        if let press, hypot(point.x - press.x, point.y - press.y) >= 3 { dragged = true }
        mouseMoved(to: point)
    }

    mutating func mouseUp(at point: CGPoint) -> Outcome {
        guard let press else { return .pending }
        if hypot(point.x - press.x, point.y - press.y) >= 3 { dragged = true }
        mouseMoved(to: point)
        self.press = nil
        if dragged || completingClick {
            anchor = nil
            completingClick = false
            return confirm()
        }
        return .pending
    }

    mutating func chooseScreen(_ frame: CGRect) -> Outcome {
        anchor = nil
        press = nil
        rectangle = frame
        return confirm()
    }

    mutating func keyboardRectangle(in frame: CGRect) {
        anchor = nil
        press = nil
        let size = CGSize(width: min(320, frame.width), height: min(180, frame.height))
        rectangle = CGRect(x: frame.midX - size.width / 2, y: frame.midY - size.height / 2,
                           width: size.width, height: size.height)
    }

    mutating func adjust(dx: CGFloat, dy: CGFloat, resize: Bool, bounds: CGRect) {
        if rectangle == nil { keyboardRectangle(in: bounds) }
        guard var value = rectangle else { return }
        anchor = nil
        press = nil
        if resize {
            value.size.width = min(bounds.maxX - value.minX, max(Self.minimumSize, value.width + dx))
            value.size.height = min(bounds.maxY - value.minY, max(Self.minimumSize, value.height + dy))
        } else {
            value.origin.x = max(bounds.minX, min(bounds.maxX - value.width, value.minX + dx))
            value.origin.y = max(bounds.minY, min(bounds.maxY - value.height, value.minY + dy))
        }
        rectangle = value
    }

    func confirm() -> Outcome {
        guard let rectangle, rectangle.width >= Self.minimumSize, rectangle.height >= Self.minimumSize else {
            return .tooSmall
        }
        return .selected(rectangle)
    }

    mutating func cancel() -> Outcome {
        rectangle = nil
        anchor = nil
        press = nil
        completingClick = false
        dragged = false
        return .cancelled
    }

    private static func rectangle(from first: CGPoint, to second: CGPoint) -> CGRect {
        CGRect(x: min(first.x, second.x), y: min(first.y, second.y),
               width: abs(second.x - first.x), height: abs(second.y - first.y))
    }
}

@MainActor
final class RegionSelectionOverlay {
    private final class SelectionPanel: NSPanel {
        var onCancel: (() -> Void)?
        override var canBecomeKey: Bool { true }
        override var canBecomeMain: Bool { false }
        override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
        override func cancelOperation(_ sender: Any?) { onCancel?() }
        override func performClose(_ sender: Any?) { onCancel?() }
        override func sendEvent(_ event: NSEvent) {
            if event.type == .rightMouseDown { onCancel?(); return }
            super.sendEvent(event)
        }
    }

    private final class SelectionView: NSView {
        let retained: CapturedDisplayFrame
        private let retainedImage: NSImage
        private var globalFrame: CGRect { retained.display.frame }
        weak var owner: RegionSelectionOverlay?
        private let instructions = NSTextField(wrappingLabelWithString: "")
        private var tracking: NSTrackingArea?
        override var acceptsFirstResponder: Bool { true }
        override var isOpaque: Bool { true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        init(retained: CapturedDisplayFrame, owner: RegionSelectionOverlay) {
            self.retained = retained
            retainedImage = retained.preview
            self.owner = owner
            super.init(frame: CGRect(origin: .zero, size: retained.display.frame.size))
            setAccessibilityElement(true)
            setAccessibilityRole(.group)
            setAccessibilityLabel(owner.text("Screenshot region selection", "截图区域选择"))
            setAccessibilityHelp(owner.text(
                "Drag or click two corners. Arrow keys move a rectangle; Shift Arrow resizes. Return selects. F selects this display. Escape cancels. Tab reaches buttons.",
                "拖动或依次点击两个角。方向键移动选框，Shift 加方向键调整大小，Return 确认，F 选择当前屏幕，Escape 取消，Tab 访问按钮。"))
            instructions.font = .systemFont(ofSize: 13, weight: .medium)
            instructions.textColor = .labelColor
            let screen = NSButton(title: owner.text("Use this display (F)", "使用此屏幕（F）"),
                                  target: self, action: #selector(selectDisplay))
            let select = NSButton(title: owner.text("Use selection (Return)", "使用选区（Return）"),
                                  target: self, action: #selector(confirm))
            let cancel = NSButton(title: owner.text("Cancel", "取消"),
                                  target: self, action: #selector(cancelSelection))
            for button in [screen, select, cancel] { button.bezelStyle = .rounded }
            let buttons = NSStackView(views: [screen, select, cancel])
            let narrow = retained.display.frame.width < 600
            buttons.orientation = narrow ? .vertical : .horizontal
            buttons.alignment = narrow ? .leading : .centerY
            buttons.spacing = 10
            let stack = NSStackView(views: [instructions, buttons])
            stack.orientation = .vertical
            stack.alignment = .leading
            stack.spacing = 10
            stack.edgeInsets = NSEdgeInsets(top: 12, left: 14, bottom: 12, right: 14)
            stack.wantsLayer = true
            stack.layer?.cornerRadius = 10
            stack.translatesAutoresizingMaskIntoConstraints = false
            addSubview(stack)
            NSLayoutConstraint.activate([
                stack.topAnchor.constraint(equalTo: topAnchor, constant: 28),
                stack.centerXAnchor.constraint(equalTo: centerXAnchor),
                stack.widthAnchor.constraint(lessThanOrEqualTo: widthAnchor, constant: -32),
                stack.widthAnchor.constraint(lessThanOrEqualToConstant: 820)
            ])
            nextKeyView = screen
            screen.nextKeyView = select
            select.nextKeyView = cancel
            cancel.nextKeyView = self
            updateInstructions()
        }

        required init?(coder: NSCoder) { return nil }

        override func viewDidChangeEffectiveAppearance() {
            super.viewDidChangeEffectiveAppearance()
            for view in subviews { view.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor }
            needsDisplay = true
        }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            if let tracking { removeTrackingArea(tracking) }
            let area = NSTrackingArea(rect: .zero, options: [.mouseMoved, .activeAlways, .inVisibleRect],
                                      owner: self, userInfo: nil)
            addTrackingArea(area)
            tracking = area
        }

        func updateInstructions() {
            guard let owner else { return }
            let hint = owner.text("Drag or click two corners · Arrows move · Shift Arrows resize · Esc cancels",
                                  "拖动或点击两个角 · 方向键移动 · Shift 方向键调整大小 · Esc 取消")
            if let rectangle = owner.state.rectangle {
                let size = "\(Int(rectangle.width)) × \(Int(rectangle.height))"
                let region = owner.text("x \(Int(rectangle.minX)), y \(Int(rectangle.minY)) · \(size) points",
                                        "x \(Int(rectangle.minX))，y \(Int(rectangle.minY)) · \(size) 点")
                instructions.stringValue = owner.warning.isEmpty ? "\(hint)\n\(region)" : "\(owner.warning)\n\(hint)"
                setAccessibilityValue(region)
            } else {
                instructions.stringValue = owner.warning.isEmpty ? hint : owner.warning + "\n" + hint
                setAccessibilityValue(owner.text("No region selected", "尚未选择区域"))
            }
            for view in subviews { view.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor }
            needsDisplay = true
        }

        override func draw(_ dirtyRect: NSRect) {
            retainedImage.draw(in: bounds)
            NSColor.black.withAlphaComponent(0.45).setFill()
            NSBezierPath(rect: bounds).fill()
            guard let rectangle = owner?.state.rectangle else { return }
            let local = rectangle.offsetBy(dx: -globalFrame.minX, dy: -globalFrame.minY)
            let visible = local.intersection(bounds)
            guard !visible.isNull else { return }
            NSGraphicsContext.saveGraphicsState()
            NSBezierPath(rect: visible).addClip()
            retainedImage.draw(in: bounds)
            NSGraphicsContext.restoreGraphicsState()
            NSColor.white.setStroke()
            let outer = NSBezierPath(rect: local)
            outer.lineWidth = 4
            outer.stroke()
            NSColor.controlAccentColor.setStroke()
            let inner = NSBezierPath(rect: local)
            inner.lineWidth = 2
            inner.stroke()
        }

        override func mouseDown(with event: NSEvent) {
            window?.makeFirstResponder(self)
            owner?.state.mouseDown(at: globalPoint(event))
            owner?.refresh()
        }
        override func mouseDragged(with event: NSEvent) {
            owner?.state.mouseDragged(to: globalPoint(event))
            owner?.refresh()
        }
        override func mouseMoved(with event: NSEvent) {
            owner?.state.mouseMoved(to: globalPoint(event))
            owner?.refresh()
        }
        override func mouseUp(with event: NSEvent) {
            guard let owner else { return }
            let outcome = owner.state.mouseUp(at: globalPoint(event))
            owner.handle(outcome)
        }
        override func rightMouseDown(with event: NSEvent) { owner?.cancel() }

        private func globalPoint(_ event: NSEvent) -> CGPoint {
            let point = convert(event.locationInWindow, from: nil)
            return CGPoint(x: point.x + globalFrame.minX, y: point.y + globalFrame.minY)
        }

        override func keyDown(with event: NSEvent) {
            guard let owner else { return }
            switch event.keyCode {
            case 53: owner.cancel()
            case 48:
                if event.modifierFlags.contains(.shift) { window?.selectPreviousKeyView(nil) }
                else { window?.selectNextKeyView(nil) }
            case 36, 76: owner.handle(owner.state.confirm())
            case 3:
                let outcome = owner.state.chooseScreen(globalFrame)
                owner.handle(outcome)
            case 123, 124, 125, 126:
                if (owner.state.rectangle?.width ?? 0) < RegionSelectionState.minimumSize ||
                    (owner.state.rectangle?.height ?? 0) < RegionSelectionState.minimumSize {
                    owner.state.keyboardRectangle(in: globalFrame)
                }
                let step: CGFloat = event.modifierFlags.contains(.option) ? 1 : 10
                let dx: CGFloat = event.keyCode == 123 ? -step : event.keyCode == 124 ? step : 0
                let dy: CGFloat = event.keyCode == 125 ? -step : event.keyCode == 126 ? step : 0
                owner.state.adjust(dx: dx, dy: dy, resize: event.modifierFlags.contains(.shift), bounds: owner.bounds)
                owner.warning = ""
                owner.refresh()
            default: super.keyDown(with: event)
            }
        }

        @objc private func selectDisplay() {
            guard let owner else { return }
            let outcome = owner.state.chooseScreen(globalFrame)
            owner.handle(outcome)
        }
        @objc private func confirm() {
            guard let owner else { return }
            owner.handle(owner.state.confirm())
        }
        @objc private func cancelSelection() { owner?.cancel() }
    }

    private var panels: [SelectionPanel] = []
    private var views: [SelectionView] = []
    private var completion: ((RegionSelectionState.Outcome) -> Void)?
    private let text: (String, String) -> String
    private(set) var state = RegionSelectionState()
    private var warning = ""
    private var bounds = CGRect.zero

    init(text: @escaping (String, String) -> String) { self.text = text }

    func present(frames: [CapturedDisplayFrame], appearance: NSAppearance?,
                 completion: @escaping (RegionSelectionState.Outcome) -> Void) {
        dismiss()
        guard !frames.isEmpty else { completion(.cancelled); return }
        self.completion = completion
        state = RegionSelectionState()
        warning = ""
        bounds = frames.dropFirst().reduce(frames[0].display.frame) { $0.union($1.display.frame) }
        for frame in frames {
            let panel = SelectionPanel(contentRect: frame.display.frame, styleMask: .borderless,
                                       backing: .buffered, defer: false)
            panel.isReleasedWhenClosed = false
            panel.hasShadow = false
            panel.hidesOnDeactivate = false
            panel.level = .screenSaver
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
            panel.acceptsMouseMovedEvents = true
            panel.appearance = appearance
            panel.title = text("Select screenshot region", "选择截图区域")
            panel.onCancel = { [weak self] in self?.cancel() }
            let view = SelectionView(retained: frame, owner: self)
            panel.contentView = view
            panel.setFrame(frame.display.frame, display: true)
            panels.append(panel)
            views.append(view)
            panel.orderFrontRegardless()
        }
        NSApp.activate(ignoringOtherApps: true)
        let active = frames.firstIndex(where: { $0.display.frame.contains(NSEvent.mouseLocation) }) ?? 0
        panels[active].makeKeyAndOrderFront(nil)
        panels[active].makeFirstResponder(views[active])
    }

    func dismiss() {
        completion = nil
        for panel in panels {
            panel.onCancel = nil
            panel.orderOut(nil)
            panel.contentView = nil
            panel.close()
        }
        views = []
        panels = []
        _ = state.cancel()
    }

    func cancel() {
        let outcome = state.cancel()
        handle(outcome)
    }

    private func refresh() { views.forEach { $0.updateInstructions() } }

    private func handle(_ outcome: RegionSelectionState.Outcome) {
        switch outcome {
        case .pending: refresh()
        case .tooSmall:
            warning = text("Select an area at least 10 × 10 points.", "请选择至少 10 × 10 点的区域。")
            refresh()
        case .selected, .cancelled:
            let callback = completion
            dismiss()
            callback?(outcome)
        }
    }
}
