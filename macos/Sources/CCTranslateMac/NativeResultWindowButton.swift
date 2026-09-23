import AppKit
import SwiftUI

@MainActor
struct NativeResultWindowButton: NSViewRepresentable {
    let title: String
    let symbol: String
    let identifier: String
    let help: String
    var value: String? = nil
    var highlighted = false
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    final class Coordinator: NSObject {
        var action: () -> Void
        init(action: @escaping () -> Void) { self.action = action }
        @objc func invoke() { action() }
    }

    func makeCoordinator() -> Coordinator { Coordinator(action: action) }

    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(frame: .zero)
        button.setButtonType(.momentaryPushIn)
        button.isBordered = false
        button.imagePosition = .imageOnly
        button.focusRingType = .exterior
        button.target = context.coordinator
        button.action = #selector(Coordinator.invoke)
        return button
    }

    func updateNSView(_ button: NSButton, context: Context) {
        context.coordinator.action = action
        button.title = ""
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: title)?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .regular))
        button.imagePosition = .imageOnly
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.setAccessibilityIdentifier(identifier)
        button.setAccessibilityLabel(title)
        button.setAccessibilityValue(value)
        button.toolTip = help
        button.contentTintColor = NSColor(highlighted ? PearlTheme.accent : PearlTheme.secondary)
        button.isEnabled = isEnabled
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSButton, context: Context) -> CGSize? {
        CGSize(width: 28, height: 28)
    }
}
