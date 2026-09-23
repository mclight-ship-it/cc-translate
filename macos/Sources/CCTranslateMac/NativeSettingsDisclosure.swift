import AppKit
import SwiftUI

@MainActor
struct NativeSettingsDisclosure<Content: View>: View {
    @ObservedObject var model: ProbeModel
    let title: String
    let identifier: String
    private let expansion: Binding<Bool>?
    private let content: Content
    @State private var expanded = false

    init(_ title: String, model: ProbeModel, identifier: String,
         isExpanded: Binding<Bool>? = nil, @ViewBuilder content: () -> Content) {
        self.model = model
        self.title = title
        self.identifier = identifier
        expansion = isExpanded
        self.content = content()
    }

    private var isExpanded: Binding<Bool> { expansion ?? $expanded }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            NativeSettingsDisclosureControl(
                title: title, identifier: identifier, expanded: isExpanded.wrappedValue,
                stateLabel: isExpanded.wrappedValue
                    ? model.text("Expanded", "已展开") : model.text("Collapsed", "已折叠"),
                toggle: { isExpanded.wrappedValue.toggle() }
            )
            .frame(maxWidth: .infinity, minHeight: 28)
            if isExpanded.wrappedValue {
                content.frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

@MainActor
private struct NativeSettingsDisclosureControl: NSViewRepresentable {
    let title: String
    let identifier: String
    let expanded: Bool
    let stateLabel: String
    let toggle: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    func makeNSView(context: Context) -> NativeSettingsDisclosureButton {
        let button = NativeSettingsDisclosureButton(frame: .zero)
        updateNSView(button, context: context)
        return button
    }

    func updateNSView(_ button: NativeSettingsDisclosureButton, context: Context) {
        let changed = button.isAccessibilityExpanded() != expanded
        button.title = title
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.setAccessibilityIdentifier(identifier)
        button.setAccessibilityLabel(title)
        button.setAccessibilityExpanded(expanded)
        button.setAccessibilityValue(stateLabel)
        button.image = NSImage(systemSymbolName: expanded ? "chevron.down" : "chevron.right",
                               accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 10, weight: .medium))
        button.isEnabled = isEnabled
        button.toggle = toggle
        if changed { NSAccessibility.post(element: button, notification: .valueChanged) }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NativeSettingsDisclosureButton,
                     context: Context) -> CGSize? {
        let width = proposal.width ?? nsView.intrinsicContentSize.width
        let textSize = (title as NSString).boundingRect(
            with: NSSize(width: max(1, width - 32), height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: nsView.font ?? NSFont.systemFont(ofSize: NSFont.systemFontSize)])
        return NSSize(width: width, height: max(28, ceil(textSize.height) + 8))
    }

    static func dismantleNSView(_ button: NativeSettingsDisclosureButton, coordinator: ()) {
        button.toggle = nil
    }
}

@MainActor
final class NativeSettingsDisclosureButton: NSButton {
    var toggle: (() -> Void)?

    override init(frame: NSRect) {
        super.init(frame: frame)
        setButtonType(.momentaryPushIn)
        bezelStyle = .regularSquare
        isBordered = false
        alignment = .left
        imagePosition = .imageLeading
        imageHugsTitle = true
        font = NSFont.preferredFont(forTextStyle: .body)
        cell?.wraps = true
        cell?.lineBreakMode = .byWordWrapping
        focusRingType = .exterior
        setContentHuggingPriority(.defaultLow, for: .horizontal)
        setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        target = self
        action = #selector(toggleDisclosure)
    }

    required init?(coder: NSCoder) { return nil }

    override var acceptsFirstResponder: Bool { isEnabled }
    override var focusRingMaskBounds: NSRect { bounds.insetBy(dx: 2, dy: 2) }

    override func drawFocusRingMask() {
        NSColor.black.setFill()
        NSBezierPath(roundedRect: focusRingMaskBounds, xRadius: 4, yRadius: 4).fill()
    }

    override func keyDown(with event: NSEvent) {
        if event.charactersIgnoringModifiers == "\r",
           event.modifierFlags.intersection([.command, .control, .option]).isEmpty {
            if !event.isARepeat { performClick(nil) }
        } else {
            super.keyDown(with: event)
        }
    }

    @objc private func toggleDisclosure() {
        window?.makeFirstResponder(self)
        toggle?()
    }
}
