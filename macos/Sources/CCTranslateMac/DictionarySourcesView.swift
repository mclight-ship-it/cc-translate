import AppKit
import SwiftUI
import CCTranslateSupport

struct DictionarySourcesLabels: Equatable {
    let title: String
    let explanation: String
    let sourceID: String
    let version: String
    let license: String
    let missing: String
    let close: String

    func body(_ sources: [DictionarySource]) -> String {
        func supplied(_ value: String) -> String { value.isEmpty ? missing : value }
        return sources.map {
            "\(supplied($0.label))\n\(sourceID): \(supplied($0.id))\n" +
                "\(version): \(supplied($0.version))\n\(license): \(supplied($0.license))"
        }.joined(separator: "\n\n")
    }
}

@MainActor
struct DictionarySourcesControl: NSViewRepresentable {
    let sources: [DictionarySource]
    let labels: DictionarySourcesLabels

    func makeNSView(context: Context) -> DictionarySourcesButton {
        let button = DictionarySourcesButton(frame: .zero)
        button.configure(sources: sources, labels: labels)
        return button
    }

    func updateNSView(_ button: DictionarySourcesButton, context: Context) {
        button.configure(sources: sources, labels: labels)
    }

    static func dismantleNSView(_ button: DictionarySourcesButton, coordinator: ()) {
        button.retire()
    }
}

@MainActor
final class DictionarySourcesButton: NSButton, NSPopoverDelegate {
    let sourcesPopover = NSPopover()
    let sourcesContent = DictionarySourcesContent()
    private(set) var sources: [DictionarySource] = []
    private(set) var labels: DictionarySourcesLabels?
    private var generation = UUID()
    private var trackingGeneration: UUID?
    private var retired = false
    private var restoreFocus = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        bezelStyle = .rounded
        setButtonType(.momentaryPushIn)
        target = self
        action = #selector(toggleSources(_:))
        setAccessibilityExpanded(false)
        sourcesPopover.behavior = .transient
        sourcesPopover.animates = false
        sourcesPopover.delegate = self
        sourcesPopover.contentViewController = sourcesContent
        sourcesPopover.contentSize = NSSize(width: 380, height: 320)
        sourcesContent.onDismiss = { [weak self] in self?.closeSources(nil) }
    }

    required init?(coder: NSCoder) { return nil }

    override var intrinsicContentSize: NSSize {
        var size = super.intrinsicContentSize
        size.height = max(28, size.height)
        return size
    }

    func configure(sources: [DictionarySource], labels: DictionarySourcesLabels) {
        let changed = self.sources != sources
        if changed {
            generation = UUID()
            closeSources(nil)
            self.sources = sources
        }
        isEnabled = !sources.isEmpty && !retired
        if title != labels.title {
            title = labels.title
            setAccessibilityLabel(labels.title)
            toolTip = labels.explanation
        }
        if changed || self.labels != labels {
            sourcesContent.update(sources: sources, labels: labels)
            self.labels = labels
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        sourcesPopover.appearance = effectiveAppearance
    }

    override func mouseDown(with event: NSEvent) {
        // AppKit keeps tracking the same button while async results are delivered.
        // A different provenance must not receive the release of an older press.
        trackingGeneration = generation
        defer { trackingGeneration = nil }
        super.mouseDown(with: event)
    }

    @objc private func toggleSources(_ sender: Any?) {
        guard !retired, !sources.isEmpty, window != nil,
              trackingGeneration == nil || trackingGeneration == generation else { return }
        if sourcesPopover.isShown {
            closeSources(sender)
        } else {
            restoreFocus = false
            sourcesPopover.appearance = effectiveAppearance
            sourcesPopover.show(relativeTo: bounds, of: self, preferredEdge: .maxY)
            setAccessibilityExpanded(true)
        }
    }

    @objc func closeSources(_ sender: Any?) {
        restoreFocus = sourcesPopover.isShown
        sourcesPopover.performClose(sender)
    }

    func retire() {
        retired = true
        generation = UUID()
        if sourcesPopover.isShown {
            window?.selectNextKeyView(self)
        }
        restoreFocus = false
        sourcesPopover.close()
        sources = []
        sourcesContent.textView.string = ""
        sourcesContent.onDismiss = nil
    }

    func popoverDidShow(_ notification: Notification) {
        sourcesContent.view.window?.makeFirstResponder(sourcesContent.textView)
    }

    func popoverDidClose(_ notification: Notification) {
        setAccessibilityExpanded(false)
        if restoreFocus && !retired { window?.makeFirstResponder(self) }
        restoreFocus = false
    }

    func popoverShouldClose(_ popover: NSPopover) -> Bool {
        if let event = NSApp.currentEvent, event.type == .keyDown, event.keyCode == 53 {
            restoreFocus = true
        }
        return true
    }
}

@MainActor
final class DictionarySourcesContent: NSViewController {
    let heading = NSTextField(labelWithString: "")
    let explanation = NSTextField(wrappingLabelWithString: "")
    let closeButton = NSButton(title: "", target: nil, action: nil)
    let textView = DictionarySourcesTextView(frame: NSRect(x: 0, y: 0, width: 348, height: 220))
    var onDismiss: (() -> Void)?

    override func loadView() {
        let root = DictionarySourcesContainer(frame: NSRect(x: 0, y: 0, width: 380, height: 320))
        root.onDismiss = { [weak self] in self?.onDismiss?() }
        view = root
        heading.font = .systemFont(ofSize: 15, weight: .semibold)
        heading.lineBreakMode = .byTruncatingTail
        explanation.font = .systemFont(ofSize: 12)
        explanation.textColor = .secondaryLabelColor
        closeButton.bezelStyle = .rounded
        closeButton.target = self
        closeButton.action = #selector(close(_:))
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        textView.isEditable = false
        textView.isSelectable = true
        textView.isRichText = false
        textView.drawsBackground = false
        textView.isAutomaticLinkDetectionEnabled = false
        textView.isAutomaticDataDetectionEnabled = false
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.minSize = .zero
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.autoresizingMask = [.width]
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.containerSize = NSSize(width: 348, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainerInset = NSSize(width: 2, height: 6)
        textView.font = .systemFont(ofSize: 14)
        textView.textColor = .labelColor
        textView.onDismiss = { [weak self] in self?.onDismiss?() }
        scroll.documentView = textView
        let children: [NSView] = [heading, explanation, closeButton, scroll]
        for child in children {
            child.translatesAutoresizingMaskIntoConstraints = false
            root.addSubview(child)
        }
        NSLayoutConstraint.activate([
            heading.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 16),
            heading.topAnchor.constraint(equalTo: root.topAnchor, constant: 16),
            heading.trailingAnchor.constraint(lessThanOrEqualTo: closeButton.leadingAnchor, constant: -8),
            closeButton.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -16),
            closeButton.centerYAnchor.constraint(equalTo: heading.centerYAnchor),
            explanation.leadingAnchor.constraint(equalTo: heading.leadingAnchor),
            explanation.trailingAnchor.constraint(equalTo: closeButton.trailingAnchor),
            explanation.topAnchor.constraint(equalTo: heading.bottomAnchor, constant: 12),
            scroll.leadingAnchor.constraint(equalTo: heading.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: closeButton.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: explanation.bottomAnchor, constant: 12),
            scroll.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),
        ])
    }

    func update(sources: [DictionarySource], labels: DictionarySourcesLabels) {
        _ = view
        title = labels.title
        view.setAccessibilityRole(.group)
        view.setAccessibilityLabel(labels.title)
        heading.stringValue = labels.title
        explanation.stringValue = labels.explanation
        closeButton.title = labels.close
        textView.setAccessibilityLabel(labels.title)
        let selection = textView.selectedRange()
        textView.string = labels.body(sources)
        let length = (textView.string as NSString).length
        if NSMaxRange(selection) <= length { textView.setSelectedRange(selection) }
    }

    @objc private func close(_ sender: Any?) { onDismiss?() }
}

@MainActor
final class DictionarySourcesTextView: NSTextView {
    var onDismiss: (() -> Void)?
    override func cancelOperation(_ sender: Any?) { onDismiss?() }
}

@MainActor
private final class DictionarySourcesContainer: NSView {
    var onDismiss: (() -> Void)?
    override func cancelOperation(_ sender: Any?) { onDismiss?() }
}
