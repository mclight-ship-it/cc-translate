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

// Reflow should keep the same passage in view, not the old document's pixel offset.
@MainActor
struct NativeTextViewport {
    let origin: NSPoint
    private let character: Int?
    private let lineOffset: CGFloat

    init(view: NSTextView, scroll: NSScrollView) {
        origin = scroll.contentView.bounds.origin
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
        point.y = min(max(0, point.y), max(0, view.bounds.height - scroll.contentView.bounds.height))
        scroll.contentView.scroll(to: point)
        scroll.reflectScrolledClipView(scroll.contentView)
    }
}
