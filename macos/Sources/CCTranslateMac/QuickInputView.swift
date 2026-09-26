import SwiftUI

@MainActor
final class QuickInputDraft: ObservableObject {
    @Published var text = ""
    @Published var attemptedSubmit = false
    @Published var selectionUnavailable = false
}

@MainActor
struct QuickInputView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var draft: QuickInputDraft
    let submit: () -> Void
    let cancel: () -> Void
    @State private var focused = false
    private var selectionHint: String {
        model.text("Couldn't read the selection. Press Command V to paste.",
                   "未能自动读取选中文字，可按 Command V 粘贴。")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(model.text("Quick translate", "快速翻译"))
                .font(.title3.weight(.semibold))
                .accessibilityAddTraits(.isHeader)
            if draft.selectionUnavailable {
                Text(selectionHint)
                    .font(.callout).foregroundStyle(PearlTheme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            NativeTranslationEditor(text: $draft.text, textScale: model.nativeTextScale,
                focused: $focused, label: model.text("Text to translate", "要翻译的文字"),
                hint: draft.selectionUnavailable ? selectionHint :
                    model.text("Type or paste. Command Return translates.",
                               "输入或粘贴文字，按 Command Return 翻译。"),
                placeholder: model.text("Type or paste text here...", "在这里输入或粘贴文字..."),
                identifier: "quick-input-editor")
                .padding(12)
                .pearlCard()
                .frame(minHeight: 130, maxHeight: .infinity)
            TranslationInputBudgetView(model: model, text: draft.text)
            if draft.attemptedSubmit, model.inputIssue(for: draft.text) == .empty {
                Text(model.text("Enter text to translate.", "请输入要翻译的文字。"))
                    .font(.caption).foregroundStyle(PearlTheme.error)
            }
            HStack {
                Button(model.text("Cancel", "取消"), action: cancel)
                    .accessibilityIdentifier("quick-input-cancel")
                Spacer()
                Text("⌘ ↩").font(.caption).foregroundStyle(PearlTheme.secondary)
                    .accessibilityHidden(true)
                Button(model.text("Translate", "翻译"), action: submit)
                    .accessibilityIdentifier("quick-input-submit")
                    .disabled(model.inputIssue(for: draft.text) != nil)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
        }
        .padding(20)
        .pearlSurface()
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear { focused = true }
    }
}
