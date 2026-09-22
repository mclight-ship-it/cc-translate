import AppKit
import SwiftUI

enum PearlTheme {
    static let spacing: CGFloat = 16
    static let pagePadding: CGFloat = 24
    static let cardRadius: CGFloat = 14
    static let controlRadius: CGFloat = 8

    static let surface = color(light: 0xFBFAFC, dark: 0x27153F, highContrast: .windowBackgroundColor)
    static let panel = color(light: 0xFFFFFF, dark: 0x352247, highContrast: .controlBackgroundColor)
    static let sidebar = color(light: 0xEAE4F2, dark: 0x341D4D, highContrast: .windowBackgroundColor)
    static let inset = color(light: 0xF1ECF8, dark: 0x36204F, highContrast: .textBackgroundColor)
    static let border = color(light: 0xDDD6E5, dark: 0x604877, highContrast: .separatorColor)
    static let text = color(light: 0x302B3B, dark: 0xF7F0FF, highContrast: .labelColor)
    static let secondary = color(light: 0x62596E, dark: 0xC9B7DC, highContrast: .secondaryLabelColor)
    static let accent = color(light: 0x7253A7, dark: 0xDDC4FF, highContrast: .controlAccentColor)
    static let onAccent = color(light: 0xFFFFFF, dark: 0x35204D, highContrast: .selectedMenuItemTextColor)

    private static func color(light: UInt32, dark: UInt32, highContrast: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let match = appearance.bestMatch(from: [
                .accessibilityHighContrastAqua, .accessibilityHighContrastDarkAqua, .aqua, .darkAqua
            ])
            if match == .accessibilityHighContrastAqua || match == .accessibilityHighContrastDarkAqua {
                return highContrast
            }
            let rgb = match == .darkAqua ? dark : light
            return NSColor(srgbRed: Double((rgb >> 16) & 255) / 255,
                           green: Double((rgb >> 8) & 255) / 255,
                           blue: Double(rgb & 255) / 255, alpha: 1)
        })
    }
}

struct PearlBackground: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .topTrailing) {
                PearlTheme.surface
                if !reduceTransparency && contrast != .increased {
                    Ellipse()
                        .fill(PearlTheme.accent.opacity(scheme == .dark ? 0.11 : 0.06))
                        .frame(width: min(geometry.size.width, 430), height: 240)
                        .blur(radius: 70)
                        .offset(x: 80, y: -100)
                }
            }
        }
        .clipped()
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }
}

private struct PearlSurfaceModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .foregroundStyle(PearlTheme.text)
            .tint(PearlTheme.accent)
            .background(PearlBackground())
    }
}

extension View {
    func pearlSurface() -> some View { modifier(PearlSurfaceModifier()) }

    func pearlCard(inset: Bool = false) -> some View {
        background(inset ? PearlTheme.inset : PearlTheme.panel,
                   in: RoundedRectangle(cornerRadius: PearlTheme.cardRadius))
            .overlay {
                RoundedRectangle(cornerRadius: PearlTheme.cardRadius)
                    .strokeBorder(PearlTheme.border, lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

struct PearlAppIcon: View {
    var size: CGFloat = 32

    var body: some View {
        Image(nsImage: NSApplication.shared.applicationIconImage)
            .resizable()
            .interpolation(.high)
            .scaledToFit()
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
