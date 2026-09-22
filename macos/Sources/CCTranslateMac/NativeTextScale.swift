import AppKit
import SwiftUI

enum NativeTextScale: String, CaseIterable {
    case smaller = "90"
    case standard = "100"
    case larger = "125"
    case largest = "150"

    static let preferenceKey = "nativeTextScale"

    var multiplier: CGFloat {
        switch self {
        case .smaller: return 0.9
        case .standard: return 1
        case .larger: return 1.25
        case .largest: return 1.5
        }
    }

    func points(_ baseline: CGFloat) -> CGFloat { baseline * multiplier }

    var bodyFont: Font {
        self == .standard ? .body : .system(size: points(NSFont.preferredFont(forTextStyle: .body).pointSize))
    }

    var captionFont: Font {
        self == .standard ? .caption : .system(size: points(NSFont.preferredFont(forTextStyle: .caption1).pointSize))
    }

    @MainActor
    func apply(to text: NSMutableAttributedString, replacing previous: NativeTextScale = .standard) {
        guard self != previous, text.length > 0 else { return }
        let ratio = multiplier / previous.multiplier
        let whole = NSRange(location: 0, length: text.length)
        var fonts: [(NSRange, NSFont)] = []
        var paragraphs: [(NSRange, NSMutableParagraphStyle)] = []
        text.enumerateAttribute(.font, in: whole) { value, range, _ in
            if let font = value as? NSFont {
                fonts.append((range, NSFontManager.shared.convert(font, toSize: font.pointSize * ratio)))
            }
        }
        text.enumerateAttribute(.paragraphStyle, in: whole) { value, range, _ in
            if let paragraph = (value as? NSParagraphStyle)?.mutableCopy() as? NSMutableParagraphStyle {
                paragraph.lineSpacing *= ratio
                paragraphs.append((range, paragraph))
            }
        }
        text.beginEditing()
        for (range, font) in fonts { text.addAttribute(.font, value: font, range: range) }
        for (range, paragraph) in paragraphs { text.addAttribute(.paragraphStyle, value: paragraph, range: range) }
        text.endEditing()
    }
}

@MainActor
struct NativeTextScalePicker: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Picker(model.text("Text size", "文字大小"), selection: $model.nativeTextScale) {
            ForEach(NativeTextScale.allCases, id: \.self) { scale in
                Text(scale == .standard ? model.text("100% (Default)", "100%（默认）") : "\(scale.rawValue)%")
                    .tag(scale)
            }
        }
        .help(model.text("Changes translation, history, and recognized text on this Mac.",
                         "调整此 Mac 上的翻译、历史记录和识别文字大小。"))
        .onChange(of: model.nativeTextScale) { _, _ in model.persistPresentation() }
    }
}

@MainActor
struct NativeTranslationEditor: NSViewRepresentable {
    @Binding var text: String
    var textScale: NativeTextScale
    @Binding var focused: Bool
    var label: String
    var hint: String = ""
    var placeholder: String = ""
    var drawsBackground = false
    @Environment(\.isEnabled) private var isEnabled
    // SwiftUI must also invalidate for canonically equivalent spellings of a binding.
    private let originalBytes: [UInt8]
    private var renderedText: String { String(decoding: originalBytes, as: UTF8.self) }

    init(text: Binding<String>, textScale: NativeTextScale, focused: Binding<Bool>,
         label: String, hint: String = "", placeholder: String = "", drawsBackground: Bool = false) {
        _text = text
        self.textScale = textScale
        _focused = focused
        self.label = label
        self.hint = hint
        self.placeholder = placeholder
        self.drawsBackground = drawsBackground
        originalBytes = Array(text.wrappedValue.utf8)
    }

    @MainActor
    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: NativeTranslationEditor
        var modelText: String
        var scale: NativeTextScale
        var applyingModel = false
        private var requestedFocus = false

        init(_ parent: NativeTranslationEditor) {
            self.parent = parent
            modelText = parent.renderedText
            scale = parent.textScale
        }

        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NativeTranslationTextView else { return }
            publishCommittedText(from: view)
        }

        func publishCommittedText(from view: NativeTranslationTextView) {
            view.needsDisplay = true
            guard !applyingModel, !view.settingMarkedText, !view.hasMarkedText() else { return }
            modelText = view.string
            // Equivalent Unicode spellings can have different scalar and byte budgets.
            if !parent.text.utf8.elementsEqual(view.string.utf8) { parent.text = view.string }
        }

        func focusChanged(_ focused: Bool) {
            requestedFocus = focused
            if parent.focused != focused { parent.focused = focused }
        }

        func synchronizeFocus(_ view: NativeTranslationTextView, attached: Bool = false) {
            guard attached || requestedFocus != parent.focused else { return }
            requestedFocus = parent.focused
            guard let window = view.window else { return }
            if requestedFocus && view.isEditable && window.firstResponder !== view {
                window.makeFirstResponder(view)
            } else if !requestedFocus && window.firstResponder === view {
                window.makeFirstResponder(nil)
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.hasHorizontalScroller = false
        scroll.borderType = .noBorder
        let view = NativeTranslationTextView(frame: .zero)
        view.isRichText = false
        view.isSelectable = true
        view.allowsUndo = true
        view.isVerticallyResizable = true
        view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.textContainerInset = NSSize(width: 0, height: 8)
        view.textContainer?.widthTracksTextView = true
        view.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        view.minSize = .zero
        view.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        view.font = .systemFont(ofSize: textScale.points(15))
        view.textColor = NSColor(PearlTheme.text)
        view.string = renderedText
        view.delegate = context.coordinator
        let coordinator = context.coordinator
        view.onCommittedTextChange = { [weak coordinator, weak view] in
            guard let view else { return }
            coordinator?.publishCommittedText(from: view)
        }
        view.onFocusChange = { [weak coordinator] in coordinator?.focusChanged($0) }
        view.onWindowAttachment = { [weak coordinator, weak view] in
            guard let view else { return }
            coordinator?.synchronizeFocus(view, attached: true)
        }
        scroll.documentView = view
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NativeTranslationTextView else { return }
        let text = renderedText
        let coordinator = context.coordinator
        coordinator.parent = self
        coordinator.applyingModel = true
        if view.isEditable != isEnabled { view.isEditable = isEnabled }
        view.drawsBackground = drawsBackground
        scroll.drawsBackground = drawsBackground
        view.backgroundColor = NSColor(PearlTheme.panel)
        scroll.backgroundColor = NSColor(PearlTheme.panel)
        view.setAccessibilityLabel(label)
        view.setAccessibilityHelp(hint)
        view.placeholder = placeholder

        // Marked text belongs to AppKit, not the last committed SwiftUI binding.
        // Only a genuine external edit (Clear, history reuse, new OCR) replaces it.
        var replacedText = false
        if !coordinator.modelText.utf8.elementsEqual(text.utf8) {
            coordinator.modelText = text
            if !view.string.utf8.elementsEqual(text.utf8) {
                let selected = view.selectedRange()
                if view.hasMarkedText() {
                    view.inputContext?.discardMarkedText()
                    view.unmarkText()
                }
                view.string = text
                replacedText = true
                view.setSelectedRange(NSRange(location: min(selected.location, (text as NSString).length), length: 0))
                view.undoManager?.removeAllActions()
            }
        }
        let updatingFont = coordinator.scale != textScale || replacedText
        let viewport = updatingFont ? NativeTextViewport(view: view, scroll: scroll) : nil
        if updatingFont {
            let selected = view.selectedRanges
            let font = NSFont.systemFont(ofSize: textScale.points(15))
            if let storage = view.textStorage, storage.length > 0 {
                storage.addAttribute(.font, value: font, range: NSRange(location: 0, length: storage.length))
            }
            view.typingAttributes[.font] = font
            coordinator.scale = textScale
            if view.selectedRanges != selected { view.selectedRanges = selected }
        }
        // Resolve the document height before AppKit constrains a scroll or restores its anchor.
        if let container = view.textContainer { view.layoutManager?.ensureLayout(for: container) }
        viewport?.restore(view: view, scroll: scroll)
        view.placeholderFont = .systemFont(ofSize: textScale.points(15))
        coordinator.applyingModel = false
        coordinator.synchronizeFocus(view)
    }

    static func dismantleNSView(_ scroll: NSScrollView, coordinator: Coordinator) {
        guard let view = scroll.documentView as? NativeTranslationTextView else { return }
        view.delegate = nil
        view.onCommittedTextChange = nil
        view.onFocusChange = nil
        view.onWindowAttachment = nil
    }
}

@MainActor
final class NativeTranslationTextView: NSTextView {
    var onCommittedTextChange: (() -> Void)?
    var onFocusChange: ((Bool) -> Void)?
    var onWindowAttachment: (() -> Void)?
    private(set) var settingMarkedText = false
    var placeholder = "" { didSet { if placeholder != oldValue { needsDisplay = true } } }
    var placeholderFont = NSFont.systemFont(ofSize: 15) { didSet { needsDisplay = true } }

    override func setMarkedText(_ string: Any, selectedRange: NSRange, replacementRange: NSRange) {
        settingMarkedText = true
        defer { settingMarkedText = false; needsDisplay = true }
        super.setMarkedText(string, selectedRange: selectedRange, replacementRange: replacementRange)
    }

    override func unmarkText() {
        super.unmarkText()
        onCommittedTextChange?()
    }

    override func insertText(_ insertString: Any, replacementRange: NSRange) {
        super.insertText(insertString, replacementRange: replacementRange)
        onCommittedTextChange?()
    }

    override func becomeFirstResponder() -> Bool {
        let became = super.becomeFirstResponder()
        if became { onFocusChange?(true) }
        return became
    }

    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned { onFocusChange?(false) }
        return resigned
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window != nil { onWindowAttachment?() }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard string.isEmpty, !placeholder.isEmpty else { return }
        let inset = textContainerOrigin
        let padding = textContainer?.lineFragmentPadding ?? 5
        (placeholder as NSString).draw(in: NSRect(
            x: inset.x + padding, y: inset.y,
            width: max(0, bounds.width - 2 * (inset.x + padding)), height: max(0, bounds.height - inset.y)
        ), withAttributes: [.font: placeholderFont, .foregroundColor: NSColor(PearlTheme.secondary)])
    }
}

// Reflow should keep the same passage in view, not the old document's pixel offset.
@MainActor
struct NativeTextViewport {
    let origin: NSPoint
    private let character: Int?
    private let lineOffset: CGFloat

    init(view: NSTextView, scroll: NSScrollView) {
        // AppKit may offset the document frame; glyph positions are document-local.
        origin = scroll.documentVisibleRect.origin
        guard origin.y > view.textContainerOrigin.y, let layout = view.layoutManager, let container = view.textContainer,
              !view.string.isEmpty else {
            character = nil
            lineOffset = 0
            return
        }
        layout.ensureLayout(for: container)
        let point = NSPoint(x: max(0, origin.x - view.textContainerOrigin.x),
                            y: max(0, origin.y - view.textContainerOrigin.y))
        let glyph = layout.glyphIndex(for: point, in: container)
        guard glyph < layout.numberOfGlyphs else {
            character = nil
            lineOffset = 0
            return
        }
        let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
        character = layout.characterIndexForGlyph(at: glyph)
        lineOffset = (point.y - line.minY) / max(1, line.height)
    }

    func restore(view: NSTextView, scroll: NSScrollView) {
        var point = origin
        if let character, let layout = view.layoutManager, let container = view.textContainer,
           character < (view.string as NSString).length {
            layout.ensureLayout(for: container)
            let glyph = layout.glyphIndexForCharacter(at: character)
            let line = layout.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            point.y = view.textContainerOrigin.y + line.minY + lineOffset * line.height
        }
        point.y = min(max(view.bounds.minY, point.y),
                      max(view.bounds.minY, view.bounds.maxY - scroll.documentVisibleRect.height))
        scroll.contentView.scroll(to: view.convert(point, to: scroll.contentView))
        scroll.reflectScrolledClipView(scroll.contentView)
    }
}
