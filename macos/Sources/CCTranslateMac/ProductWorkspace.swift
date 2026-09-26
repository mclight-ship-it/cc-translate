import SwiftUI

enum ProductSection: String, CaseIterable {
    case translator, capture, history, dictionary, settings, about

    var accent: Color {
        switch self {
        case .translator: return PearlTheme.accent
        case .capture: return PearlTheme.captureAccent
        case .history: return PearlTheme.historyAccent
        case .dictionary: return PearlTheme.dictionaryAccent
        case .settings: return PearlTheme.settingsAccent
        case .about: return PearlTheme.aboutAccent
        }
    }

    var symbol: String {
        switch self {
        case .translator: return "character.bubble"
        case .capture: return "viewfinder"
        case .history: return "clock.arrow.circlepath"
        case .dictionary: return "book.closed"
        case .settings: return "slider.horizontal.3"
        case .about: return "info.circle"
        }
    }

    @MainActor
    func title(using model: ProbeModel) -> String {
        switch self {
        case .translator: return model.text("Translate", "翻译")
        case .capture: return model.text("Screenshot", "截图翻译")
        case .history: return model.text("History", "历史记录")
        case .dictionary: return model.text("Local dictionary", "本地词典")
        case .settings: return model.text("Settings", "设置")
        case .about: return model.text("About", "关于")
        }
    }
}

@MainActor
struct ProductWorkspace<Content: View>: View {
    @ObservedObject var model: ProbeModel
    let selection: ProductSection
    let navigate: (ProductSection) -> Void
    let content: Content

    init(model: ProbeModel, selection: ProductSection,
         navigate: @escaping (ProductSection) -> Void, @ViewBuilder content: () -> Content) {
        self.model = model
        self.selection = selection
        self.navigate = navigate
        self.content = content()
    }

    var body: some View {
        GeometryReader { geometry in
            let expanded = geometry.size.width >= 860
            HStack(spacing: 0) {
                sidebar(expanded: expanded)
                    .frame(width: expanded ? 196 : 56)
                    .frame(maxHeight: .infinity)
                    .background(PearlSidebarMaterial())
                Rectangle().fill(PearlTheme.border).frame(width: 1).accessibilityHidden(true)
                content.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .pearlSurface()
        .preferredColorScheme(model.preferredColorScheme)
    }

    private func sidebar(expanded: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                PearlAppIcon()
                if expanded { Text("CC Translate").font(.system(size: 14, weight: .semibold)) }
            }
            .padding(.horizontal, expanded ? 8 : 0)
            .padding(.top, 16)
            .padding(.bottom, 24)
            ForEach([ProductSection.translator, .capture, .history, .dictionary], id: \.self) { section in
                navigationButton(section, expanded: expanded)
            }
            Spacer(minLength: 24)
            ForEach([ProductSection.settings, .about], id: \.self) { section in
                navigationButton(section, expanded: expanded)
            }
        }
        .padding(.horizontal, expanded ? 10 : 6)
        .padding(.bottom, 16)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(model.text("Navigation", "导航"))
    }

    private func navigationButton(_ section: ProductSection, expanded: Bool) -> some View {
        PearlNavigationButton(title: section.title(using: model), symbol: section.symbol,
                              accent: section.accent, expanded: expanded,
                              selected: section == selection) { navigate(section) }
            .accessibilityIdentifier("workspace-nav-\(section.rawValue)")
    }
}

private struct PearlNavigationButton: View {
    let title: String
    let symbol: String
    let accent: Color
    let expanded: Bool
    let selected: Bool
    let action: () -> Void
    @State private var hovered = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).font(.system(size: 16)).frame(width: 20)
                    .foregroundStyle(accent)
                    .accessibilityHidden(true)
                if expanded {
                    Text(title).font(.system(size: 13, weight: selected ? .semibold : .regular))
                        .lineLimit(1)
                    Spacer(minLength: 0)
                }
            }
            .foregroundStyle(selected ? PearlTheme.text : PearlTheme.secondary)
            .padding(.horizontal, expanded ? 10 : 12)
            .frame(height: 40)
            .frame(maxWidth: .infinity, alignment: expanded ? .leading : .center)
            .background(selected ? PearlTheme.panel : hovered ? PearlTheme.inset : Color.clear,
                        in: RoundedRectangle(cornerRadius: 9))
            .overlay(alignment: .leading) {
                if selected {
                    Capsule().fill(accent).frame(width: 3, height: 18)
                        .accessibilityHidden(true).allowsHitTesting(false)
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 9)
                    .strokeBorder(focused ? PearlTheme.accent : Color.clear, lineWidth: 2)
                    .allowsHitTesting(false)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .focused($focused)
        .onHover { hovered = $0 }
        .help(title)
        .accessibilityLabel(title)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
