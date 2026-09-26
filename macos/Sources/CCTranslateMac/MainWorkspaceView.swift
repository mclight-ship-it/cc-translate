import AppKit
import SwiftUI

@MainActor
final class WorkspaceNavigation: ObservableObject {
    @Published var section: ProductSection = .translator
}

@MainActor
struct MainWorkspaceView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var navigation: WorkspaceNavigation
    let about: AboutModel
    let loginItems: LoginItemModel
    let updates: AppUpdateModel
    let settingsNavigation: SettingsNavigation
    let navigate: (ProductSection) -> Void
    let showDiagnostics: () -> Void
    let close: () -> Void

    var body: some View {
        ProductWorkspace(model: model, selection: navigation.section, navigate: navigate) {
            RetainedWorkspacePages(selection: navigation.section, content: page)
        }
    }

    private func page(_ section: ProductSection) -> AnyView {
        switch section {
        case .translator:
            return AnyView(TranslatorView(model: model,
                showHistory: { navigate(.history) }, showSettings: { navigate(.settings) },
                showCapture: { navigate(.capture) }, embedded: true,
                showInstallationSettings: {
                    settingsNavigation.openInstallationSettings()
                    navigate(.settings)
                }))
        case .history:
            return AnyView(TranslationHistoryView(model: model, useEntry: { navigate(.translator) }))
        case .dictionary:
            return AnyView(DictionaryLibraryView(model: model, search: model.dictionarySearch))
        case .settings:
            return AnyView(TranslationSettingsView(model: model, showDiagnostics: showDiagnostics,
                showAbout: { navigate(.about) }, loginItems: loginItems, updates: updates,
                navigation: settingsNavigation))
        case .about:
            return AnyView(AboutView(model: about, presentation: model, close: close))
        case .capture:
            preconditionFailure("Screenshot selection is an action, not a workspace page.")
        }
    }
}

@MainActor
struct RetainedWorkspacePages: NSViewRepresentable {
    let selection: ProductSection
    let content: (ProductSection) -> AnyView

    func makeNSView(context: Context) -> RetainedWorkspaceContainer {
        RetainedWorkspaceContainer()
    }

    func updateNSView(_ view: RetainedWorkspaceContainer, context: Context) {
        view.select(selection, content: content)
    }
}

@MainActor
final class RetainedWorkspaceContainer: NSView {
    private var pages: [ProductSection: NSHostingView<AnyView>] = [:]
    private var focus: [ProductSection: WeakWorkspaceFocus] = [:]
    private(set) var selection: ProductSection?

    func select(_ section: ProductSection, content: (ProductSection) -> AnyView) {
        guard selection != section else { return }
        if let previous = selection, let previousView = pages[previous] {
            if let responder = window?.firstResponder as? NSView,
               responder.isDescendant(of: previousView) {
                focus[previous] = WeakWorkspaceFocus(responder)
                window?.makeFirstResponder(nil)
            } else if let editor = window?.firstResponder as? NSTextView, editor.isFieldEditor,
                      let control = editor.delegate as? NSView, control.isDescendant(of: previousView) {
                focus[previous] = WeakWorkspaceFocus(control)
                window?.makeFirstResponder(nil)
            }
            previousView.removeFromSuperview()
        }
        let page: NSHostingView<AnyView>
        if let existing = pages[section] {
            page = existing
        } else {
            page = NSHostingView(rootView: content(section))
            page.sizingOptions = []
            page.identifier = NSUserInterfaceItemIdentifier("workspace-page-\(section.rawValue)")
            pages[section] = page
        }
        selection = section
        page.frame = bounds
        page.autoresizingMask = [.width, .height]
        // Keep the hosting view alive, but remove inactive pages from layout, key traversal and AX.
        addSubview(page)
        window?.recalculateKeyViewLoop()
        if let responder = focus[section]?.view {
            DispatchQueue.main.async { [weak self, weak responder] in
                guard let self, self.selection == section, let responder,
                      responder.isDescendant(of: page) else { return }
                self.window?.makeFirstResponder(responder)
            }
        }
    }
}

private final class WeakWorkspaceFocus {
    weak var view: NSView?
    init(_ view: NSView) { self.view = view }
}
