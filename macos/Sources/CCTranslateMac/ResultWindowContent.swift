import SwiftUI

@MainActor
struct ResultWindowContent: View {
    @ObservedObject var model: ProbeModel
    let openInWindow: () -> Void
    let togglePinned: () -> Void

    var body: some View {
        TranslationResultView(model: model, compact: true, openInWindow: openInWindow,
                              togglePinned: togglePinned, pinned: model.resultPinned)
    }
}
