import AppKit
import SwiftUI

enum PearlTheme {
    static let spacing: CGFloat = 16
    static let pagePadding: CGFloat = 24
    static let cardRadius: CGFloat = 14
    static let controlRadius: CGFloat = 8

    static let surface = color(light: 0xF5F6F7, dark: 0x1D2024, highContrast: .windowBackgroundColor)
    static let panel = color(light: 0xFFFFFF, dark: 0x282C31, highContrast: .controlBackgroundColor)
    static let sidebar = color(light: 0xE8EDF0, dark: 0x23282D, highContrast: .windowBackgroundColor)
    static let inset = color(light: 0xF0F3F4, dark: 0x22272B, highContrast: .textBackgroundColor)
    static let border = color(light: 0xD1DADF, dark: 0x49525B, highContrast: .separatorColor)
    static let text = color(light: 0x202B32, dark: 0xEFF3F5, highContrast: .labelColor)
    static let secondary = color(light: 0x53626D, dark: 0xB9C5CE, highContrast: .secondaryLabelColor)
    static let accent = color(light: 0x1C6374, dark: 0x8BD2DE, highContrast: .controlAccentColor)
    static let onAccent = color(light: 0xFFFFFF, dark: 0x18242A, highContrast: .selectedMenuItemTextColor)
    static let captureAccent = color(light: 0x916617, dark: 0xEAC785, highContrast: .labelColor)
    static let historyAccent = color(light: 0x536A91, dark: 0xAEC5EC, highContrast: .labelColor)
    static let dictionaryAccent = color(light: 0x296B52, dark: 0x9BD6B8, highContrast: .labelColor)
    static let settingsAccent = color(light: 0x5B6570, dark: 0xC0CDD5, highContrast: .labelColor)
    static let aboutAccent = color(light: 0x59659A, dark: 0xBAC8ED, highContrast: .labelColor)

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
    var body: some View {
        PearlTheme.surface.accessibilityHidden(true).allowsHitTesting(false)
    }
}

struct PearlSidebarMaterial: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        Group {
            if reduceTransparency || contrast == .increased {
                PearlTheme.sidebar
            } else {
                NativeSidebarMaterial()
                    .overlay(PearlTheme.sidebar.opacity(0.16))
            }
        }
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }
}

private struct NativeSidebarMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .sidebar
        view.blendingMode = .behindWindow
        view.state = .followsWindowActiveState
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
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
