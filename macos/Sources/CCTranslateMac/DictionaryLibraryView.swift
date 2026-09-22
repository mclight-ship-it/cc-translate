import SwiftUI

@MainActor
struct DictionaryLibraryView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var search: DictionarySearchModel
    @FocusState private var queryFocused: Bool
    @State private var copyMessage = ""

    private var canSearch: Bool {
        model.settingsReady && !model.settingsBusy && !model.dictionary.committing &&
            !search.busy && !search.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "book.closed").foregroundStyle(PearlTheme.accent)
                    .accessibilityHidden(true)
                Text(model.text("Local dictionary", "本地词典"))
                    .font(.system(size: 22, weight: .semibold))
                    .accessibilityAddTraits(.isHeader)
            }
            HStack(spacing: 12) {
                TextField(model.text("Word or phrase", "单词或词语"), text: $search.query)
                    .textFieldStyle(.plain)
                    .padding(12)
                    .pearlCard(inset: true)
                    .focused($queryFocused)
                    .onSubmit { if canSearch { lookUp() } }
                    .accessibilityIdentifier("dictionary-search-word")
                Button(model.text("Look up", "查词"), action: lookUp)
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .foregroundStyle(PearlTheme.onAccent)
                    .disabled(!canSearch)
                    .accessibilityIdentifier("dictionary-search-submit")
            }
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    if search.busy { ProgressView().controlSize(.small).accessibilityHidden(true) }
                    Text(search.message(using: model))
                        .font(.callout)
                        .foregroundStyle(PearlTheme.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    if search.phase == .hit {
                        Button {
                            copyMessage = model.copyText(search.output)
                                ? model.text("Copied", "已复制")
                                : model.text("Could not copy. Try again.", "未能复制，请重试。")
                        } label: {
                            Label(model.text("Copy", "复制"), systemImage: "doc.on.doc")
                        }
                    }
                }
                if search.phase == .hit {
                    NativeResultText(text: search.output, formatted: false, streaming: false,
                                     label: model.text("Dictionary entry", "词典释义"),
                                     textScale: model.nativeTextScale)
                } else {
                    Spacer(minLength: 0)
                }
                if !search.sources.isEmpty {
                    DictionarySourcesView(sources: search.sources, model: model)
                }
                if !copyMessage.isEmpty {
                    Text(copyMessage).font(.caption).foregroundStyle(PearlTheme.secondary)
                }
            }
            .padding(16)
            .frame(minHeight: 140, maxHeight: .infinity)
            .pearlCard()
            Form {
                DictionarySettingsSection(model: model, dictionary: model.dictionary)
            }
            .formStyle(.grouped)
            .scrollContentBackground(.hidden)
            .frame(minHeight: 180, idealHeight: 240, maxHeight: 300)
            .pearlCard()
        }
        .padding(24)
        .frame(minWidth: 580, minHeight: 600)
        .pearlSurface()
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            model.refreshDictionary()
            queryFocused = true
        }
    }

    private func lookUp() {
        copyMessage = ""
        search.search(language: model.usesChinese ? "zh_CN" : "en_US")
    }
}
