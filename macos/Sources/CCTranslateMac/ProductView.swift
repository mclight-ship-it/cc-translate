import AppKit
import SwiftUI
import CCTranslateSupport

@MainActor
struct TranslatorView: View {
    @ObservedObject var model: ProbeModel
    var showHistory: () -> Void
    var showSettings: () -> Void
    @FocusState private var editorFocused: Bool

    private var busy: Bool { model.preparing || model.active }
    private var byteCount: Int { model.input.utf8.count }
    private var canTranslate: Bool {
        !busy && byteCount <= 8192 &&
        !model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            options
            if model.needsCLI {
                HStack(spacing: 10) {
                    Image(systemName: "terminal").accessibilityHidden(true)
                    Text(model.text("Choose Codex once to start translating.",
                                    "选择 Codex 后即可开始翻译。"))
                    Spacer(minLength: 8)
                    Button(model.text("Open Settings", "打开设置"), action: showSettings)
                }
                .font(.callout)
                .padding(12)
                .background(Color(nsColor: .controlBackgroundColor))
            }
            HStack(spacing: 0) {
                editor.frame(minWidth: 270, maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                TranslationResultView(model: model)
                    .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 660, minHeight: 540)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            model.openProduct()
            editorFocused = true
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "character.bubble.fill")
                .font(.title2)
                .foregroundStyle(Color.accentColor)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("CC Translate").font(.headline)
                Text(model.text("Your words, clearly translated.", "让每一句话，清晰传达。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button(action: showHistory) {
                Label(model.text("History", "历史记录"), systemImage: "clock.arrow.circlepath")
            }
            .keyboardShortcut("y", modifiers: .command)
            .help(model.text("Browse saved translations", "浏览已保存的翻译"))
            Button(action: showSettings) {
                Label(model.text("Settings", "设置"), systemImage: "gearshape")
            }
            .keyboardShortcut(",", modifiers: .command)
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
    }

    private var options: some View {
        HStack(spacing: 16) {
            DirectionPicker(model: model, selection: $model.direction)
                .frame(maxWidth: 340)
            Spacer(minLength: 8)
            Label(model.text("Codex", "Codex"), systemImage: "sparkle")
                .foregroundStyle(.secondary)
            ModelPicker(model: model, selection: $model.modelProfile)
                .labelsHidden().frame(width: 180)
        }
        .disabled(busy || model.settingsBusy)
        .padding(.horizontal, 18).padding(.vertical, 12)
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(model.text("Original", "原文")).font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(model.text("Clear", "清空")) {
                    model.clearTranslation()
                    editorFocused = true
                }
                .disabled(model.input.isEmpty && model.output.isEmpty)
                .help(model.text("Clear the input and result; cancel any translation.",
                                 "清空原文和结果，并取消正在进行的翻译。"))
            }
            ZStack(alignment: .topLeading) {
                TextEditor(text: $model.input)
                    .font(.system(size: 15))
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .focused($editorFocused)
                    .accessibilityLabel(model.text("Text to translate", "要翻译的文字"))
                    .accessibilityHint(model.text("Type or paste. Command Return translates.",
                                                 "输入或粘贴文字，按 Command Return 翻译。"))
                if model.input.isEmpty {
                    Text(model.text("Type or paste text here…", "在这里输入或粘贴文字…"))
                        .font(.system(size: 15))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 13).padding(.vertical, 16)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .background(Color(nsColor: .textBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(editorFocused ? Color.accentColor :
                                  Color(nsColor: .separatorColor), lineWidth: 1)
                    .allowsHitTesting(false)
            }
            .frame(minHeight: 180)
            HStack(alignment: .firstTextBaseline) {
                Text(model.text("\(byteCount) / 8,192 UTF-8 bytes",
                                "\(byteCount) / 8,192 UTF-8 字节"))
                    .font(.caption).monospacedDigit()
                    .foregroundStyle(byteCount > 8192 ? Color.red : Color.secondary)
                Spacer()
                Text("⌘ ↩").font(.caption).foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
            if byteCount > 8192 {
                Label(model.text("Shorten the text to translate.", "请缩短文字后再翻译。"),
                      systemImage: "exclamationmark.circle")
                    .font(.callout).foregroundStyle(.red)
            }
            HStack {
                Text(model.text("Uses your Codex CLI and account.", "使用你的 Codex CLI 和账号。"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if busy {
                    Button(model.text("Cancel", "取消")) { model.cancel() }
                        .keyboardShortcut(".", modifiers: .command)
                } else {
                    Button { model.translate() } label: {
                        Label(model.text("Translate", "翻译"), systemImage: "arrow.right")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .disabled(!canTranslate)
                }
            }
        }
        .padding(18)
    }
}

@MainActor
struct TranslationResultView: View {
    @ObservedObject var model: ProbeModel
    var compact = false
    @State private var formatted = true

    private var busy: Bool { model.preparing || model.active }
    private var canRetranslate: Bool {
        !busy && !model.output.isEmpty && model.resultInput.utf8.count <= 8192 &&
        !model.resultInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(model.text("Translation", "翻译结果")).font(.headline)
                    .accessibilityAddTraits(.isHeader)
                if !model.output.isEmpty {
                    Text(resultKindName(model.resultKind, model: model))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                Picker(model.text("Result format", "结果格式"), selection: $formatted) {
                    Text(model.text("Formatted", "格式化")).tag(true)
                    Text(model.text("Plain", "纯文本")).tag(false)
                }
                .labelsHidden().pickerStyle(.segmented)
                .frame(width: compact ? 154 : 164)
                .help(model.text("Formatting is applied when streaming finishes.",
                                 "流式输出结束后应用格式。"))
            }
            ZStack {
                NativeResultText(text: model.output, formatted: formatted, streaming: busy,
                                 label: model.text("Translation result", "翻译结果"))
                if model.output.isEmpty {
                    emptyState
                }
            }
            .background(Color(nsColor: .textBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 10))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
                    .allowsHitTesting(false)
            }
            .frame(minHeight: compact ? 120 : 180)
            phaseStatus
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    copyButtons
                    Spacer(minLength: 8)
                    resultActions
                }
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) { copyButtons }
                    resultActions
                }
            }
        }
        .padding(compact ? 14 : 18)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: emptySymbol)
                .font(.system(size: 28, weight: .light))
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            Text(emptyTitle).font(.headline)
            Text(emptyDescription)
                .font(.callout).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }

    private var emptySymbol: String {
        if busy { return "ellipsis.bubble" }
        switch model.productPhase {
        case .failed: return "exclamationmark.bubble"
        case .cancelled: return "stop.circle"
        default: return "text.bubble"
        }
    }

    private var emptyTitle: String {
        if busy { return model.text("Working on your words", "正在处理你的文字") }
        switch model.productPhase {
        case .failed: return model.text("Translation needs attention", "翻译遇到问题")
        case .cancelled: return model.text("Translation cancelled", "翻译已取消")
        default: return model.text("A little more understanding", "让理解更进一步")
        }
    }

    private var emptyDescription: String {
        if busy { return model.text("The result will appear here as it arrives.",
                                    "结果会在这里逐步显示。") }
        switch model.productPhase {
        case .failed: return model.text("Check the message below, then try translating again.",
                                       "请查看下方提示，然后重试翻译。")
        case .cancelled: return model.text("Your original text is still available to translate.",
                                          "原文仍然保留，可以重新翻译。")
        default: return model.text("Enter text and choose Translate to see the result here.",
                                  "输入文字并点击“翻译”，即可在这里查看结果。")
        }
    }

    private var phaseStatus: some View {
        HStack(alignment: .top, spacing: 8) {
            if busy {
                ProgressView().controlSize(.small)
                    .accessibilityLabel(model.text("Request in progress", "正在处理请求"))
            } else if model.productPhase == .failed {
                Image(systemName: "exclamationmark.circle").foregroundStyle(.red)
                    .accessibilityHidden(true)
            } else if model.productPhase == .completed {
                Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
            Text(model.productMessage.isEmpty ?
                 model.text("Ready when you are.", "随时可以开始。") : model.productMessage)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private var copyButtons: some View {
        Button { model.copyResult() } label: {
            Label(model.text("Copy", "复制"), systemImage: "doc.on.doc")
        }
        .disabled(model.output.isEmpty)
        .help(model.text("Copy the current result as plain text", "以纯文本复制当前结果"))
        Button(model.text("Copy bilingual", "复制双语")) { model.copyBilingual() }
            .disabled(model.output.isEmpty)
            .help(model.text("Copy the original text and its result", "复制原文及其翻译结果"))
    }

    @ViewBuilder
    private var resultActions: some View {
        HStack(spacing: 8) {
            Menu(model.text("Actions", "结果操作")) {
                ForEach([ResultAction.concise, .formal, .summary], id: \.self) { action in
                    Button(model.resultActionTitle(action)) { model.performResultAction(action) }
                }
                Divider()
                Button(model.resultActionTitle(.explainCode)) { model.performResultAction(.explainCode) }
                Button(model.resultActionTitle(.asText)) { model.performResultAction(.asText) }
                Menu(model.resultActionTitle(.retranslate)) {
                    ForEach(ProbeModel.targetLanguages.indices, id: \.self) { index in
                        let language = ProbeModel.targetLanguages[index]
                        Button(model.text(language.1, language.2)) {
                            model.performResultAction(.retranslate, targetLanguage: language.0)
                        }
                    }
                }
            }
            .disabled(!model.canRunResultAction)
            .help(model.text("Add a result section without replacing the original or saving another history entry.",
                             "追加结果区块，不替换原结果，也不新增历史记录。"))
            translationAction
        }
    }

    @ViewBuilder
    private var translationAction: some View {
        if busy {
            Button(model.text("Cancel", "取消")) { model.cancel() }
                .keyboardShortcut(".", modifiers: .command)
        } else {
            Button {
                model.input = model.resultInput
                model.translate(origin: model.translationOrigin, useCache: false)
            } label: {
                Label(model.text("Retranslate", "重新翻译"), systemImage: "arrow.clockwise")
            }
            .disabled(!canRetranslate)
            .help(model.text("Reload this result's original text and translate again without using cached results.",
                             "重新载入此结果的原文并翻译，不使用缓存结果。"))
        }
    }
}

@MainActor
struct TranslationHistoryView: View {
    @ObservedObject var model: ProbeModel
    var useEntry: () -> Void
    @State private var selectedID: String?
    @State private var confirmClear = false

    private var selectedRow: ProbeModel.HistoryRow? {
        model.filteredHistory.first { $0.id == selectedID }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Label(model.text("History", "历史记录"), systemImage: "clock.arrow.circlepath")
                    .font(.title2).accessibilityAddTraits(.isHeader)
                Spacer()
                Button { model.loadHistory() } label: {
                    Label(model.text("Refresh", "刷新"), systemImage: "arrow.clockwise")
                }
                .disabled(model.historyBusy)
                Button(model.text("Clear history…", "清除历史记录…"), role: .destructive) {
                    confirmClear = true
                }
                .disabled(model.historyBusy || !model.settingsReady)
            }
            .padding(18)
            Divider()
            HSplitView {
                historyList.frame(minWidth: 255, idealWidth: 310, maxWidth: 390)
                historyDetail.frame(minWidth: 310, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 640, minHeight: 440)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            model.openProduct()
            if model.historyPage.isEmpty && !model.historyBusy { model.loadHistory() }
        }
        .onChange(of: model.filteredHistory.map(\.id)) { _, ids in
            if let selectedID, !ids.contains(selectedID) { self.selectedID = nil }
        }
        .confirmationDialog(model.text("Clear all saved history?", "清除所有已保存的历史记录？"),
                            isPresented: $confirmClear, titleVisibility: .visible) {
            Button(model.text("Clear all history", "清除所有历史记录"), role: .destructive) {
                selectedID = nil
                model.clearHistory()
            }
            Button(model.text("Cancel", "取消"), role: .cancel) {}
        } message: {
            Text(model.text("This permanently removes every saved record, including records not loaded here, and cancels any active translation. It cannot be undone.",
                            "这将永久删除所有已保存的记录，包括尚未加载的记录，并取消正在进行的翻译。此操作无法撤销。"))
        }
    }

    private var historyList: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    .accessibilityHidden(true)
                TextField(model.text("Search loaded history", "搜索已加载的历史记录"),
                          text: $model.historySearch)
                    .textFieldStyle(.plain)
                    .accessibilityLabel(model.text("Search loaded history", "搜索已加载的历史记录"))
                if !model.historySearch.isEmpty {
                    Button { model.historySearch = "" } label: {
                        Image(systemName: "xmark.circle.fill")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(model.text("Clear search", "清除搜索"))
                }
            }
            .padding(8).background(Color(nsColor: .textBackgroundColor),
                                   in: RoundedRectangle(cornerRadius: 7))
            Picker(model.text("Type", "类型"), selection: $model.historyFilter) {
                Text(model.text("All types", "所有类型")).tag("all")
                ForEach(historyKinds, id: \.self) { kind in
                    Text(resultKindName(kind, model: model)).tag(kind)
                }
            }
            Text(model.text("\(model.filteredHistory.count) shown · \(model.historyPage.count) loaded",
                            "显示 \(model.filteredHistory.count) 条 · 已加载 \(model.historyPage.count) 条"))
                .font(.caption).foregroundStyle(.secondary)
            if model.hasNextHistoryPage {
                Text(model.text("Search and filters cover loaded records only. Load more to include older records.",
                                "搜索和筛选仅覆盖已加载的记录。加载更多可包含较早的记录。"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            List(selection: $selectedID) {
                ForEach(model.filteredHistory) { row in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(row.input).font(.body).lineLimit(2)
                        Text(row.output).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        HStack {
                            Text(resultKindName(row.kind, model: model))
                            Spacer(minLength: 4)
                            Text(historyDate(row.timestamp))
                        }
                        .font(.caption2).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 5)
                    .tag(row.id)
                    .accessibilityElement(children: .combine)
                }
            }
            .listStyle(.inset)
            .overlay {
                if model.filteredHistory.isEmpty {
                    Text(model.historyBusy ? model.text("Loading…", "正在加载…") :
                         model.historyPage.isEmpty ? model.text("No records loaded.", "尚未加载任何记录。") :
                         model.text("No matches in loaded records.", "已加载的记录中没有匹配项。"))
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).padding()
                        .allowsHitTesting(false)
                }
            }
            HStack {
                if model.historyBusy {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(model.text("Loading history", "正在加载历史记录"))
                }
                Text(model.historyStatus).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if model.hasNextHistoryPage {
                Button(model.text("Load more", "加载更多")) { model.loadHistory(next: true) }
                    .disabled(model.historyBusy)
                    .frame(maxWidth: .infinity)
            }
        }
        .padding(14)
    }

    private var historyKinds: [String] {
        Array(Set(["text", "code", "mixed"] + model.historyPage.map(\.kind) +
                  (model.historyFilter == "all" ? [] : [model.historyFilter]))).sorted()
    }

    @ViewBuilder
    private var historyDetail: some View {
        if let row = selectedRow {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(resultKindName(row.kind, model: model)).font(.headline)
                    Spacer()
                    Text(historyDate(row.timestamp)).font(.caption).foregroundStyle(.secondary)
                }
                Text(model.text("Original", "原文")).font(.subheadline.bold())
                    .accessibilityAddTraits(.isHeader)
                NativeResultText(text: row.input, formatted: false, streaming: false,
                                 label: model.text("Saved original text", "已保存的原文"))
                    .frame(minHeight: 80, maxHeight: 150)
                Divider()
                Text(model.text("Translation", "翻译结果")).font(.subheadline.bold())
                    .accessibilityAddTraits(.isHeader)
                NativeResultText(text: row.output, formatted: true, streaming: false,
                                 label: model.text("Saved translation", "已保存的翻译"))
                    .frame(minHeight: 110)
                ViewThatFits(in: .horizontal) {
                    HStack { historyActions(row) }
                    VStack(alignment: .leading, spacing: 8) { historyActions(row) }
                }
            }
            .padding(18)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "clock").font(.largeTitle).foregroundStyle(.secondary)
                    .accessibilityHidden(true)
                Text(model.text("Select a translation", "选择一条翻译")).font(.headline)
                Text(model.text("Read, copy, or reuse a saved original.", "阅读、复制或复用已保存的原文。"))
                    .font(.callout).foregroundStyle(.secondary)
            }
            .padding(24)
        }
    }

    @ViewBuilder
    private func historyActions(_ row: ProbeModel.HistoryRow) -> some View {
        Button(model.text("Copy result", "复制结果")) { model.copyText(row.output) }
            .disabled(row.output.isEmpty)
        Button(model.text("Copy bilingual", "复制双语")) {
            model.copyText(row.input + "\n\n" + row.output)
        }
        Button(model.text("Reuse original", "复用原文")) {
            model.reuseHistory(row)
            useEntry()
        }
        .buttonStyle(.bordered)
        .help(model.text("Open this record in the translator without sending it.",
                         "在翻译窗口打开此记录，不会立即发送。"))
    }
}

@MainActor
struct TranslationSettingsView: View {
    @ObservedObject var model: ProbeModel
    var showDiagnostics: () -> Void

    private var busy: Bool { model.active || model.preparing }

    var body: some View {
        Form {
            generalSection
            if model.productPhase == .failed && !model.productMessage.isEmpty {
                Section {
                    Label(model.productMessage, systemImage: "exclamationmark.circle")
                        .font(.callout).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                } header: {
                    Text(model.text("Needs attention", "需要处理"))
                }
            }
            translationSection
            codexSection
            shortcutSection
            aboutSection
        }
        .formStyle(.grouped)
        .frame(minWidth: 530, minHeight: 460)
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear { model.openProduct() }
        .onChange(of: model.interfaceLanguage) { _, _ in model.persistPresentation() }
        .onChange(of: model.appearance) { _, _ in model.persistPresentation() }
    }

    private var generalSection: some View {
        Section {
            Picker(model.text("Appearance", "外观"), selection: $model.appearance) {
                Text(model.text("System", "跟随系统")).tag("system")
                Text(model.text("Light", "浅色")).tag("light")
                Text(model.text("Dark", "深色")).tag("dark")
            }
            Picker(model.text("Interface language", "界面语言"), selection: $model.interfaceLanguage) {
                Text(model.text("System", "跟随系统")).tag("system")
                Text(model.text("English", "英语")).tag("en")
                Text(model.text("Chinese", "中文")).tag("zh")
            }
        } header: {
            Text(model.text("General", "通用"))
        }
    }

    private var translationSection: some View {
        Section {
            DirectionPicker(model: model, selection: Binding(
                get: { model.direction },
                set: { model.direction = $0; model.saveSettings() }
            ))
            .disabled(model.settingsBusy || busy)
            ModelPicker(model: model, selection: Binding(
                get: { model.modelProfile },
                set: { model.modelProfile = $0; model.saveSettings() }
            ))
            .disabled(model.settingsBusy || busy)
            if model.settingsReady {
                Toggle(model.text("Save translation history", "保存翻译历史记录"), isOn: Binding(
                    get: { model.historyEnabled },
                    set: { model.saveSettings(history: $0) }
                ))
                .disabled(model.settingsBusy)
                .help(model.text("Turning this off cancels an active translation but does not delete existing history.",
                                 "关闭此选项会取消正在进行的翻译，但不会删除已有历史记录。"))
            } else {
                HStack {
                    if model.connected {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel(model.text("Loading settings", "正在加载设置"))
                    }
                    Text(model.text("History preference is available when settings finish loading.",
                                    "设置加载完成后即可更改历史记录偏好。"))
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            if model.settingsBusy {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(model.text("Saving settings…", "正在保存设置…")).font(.caption)
                }
            }
            Text(model.text("Direction and model changes also apply automatically on your next translation. History is stored on this Mac. Turning history off cancels any active translation, but keeps saved records.",
                            "下次翻译时也会自动应用方向和模型更改。历史记录保存在此 Mac 上。关闭保存会取消正在进行的翻译，但会保留已有记录。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("Translation", "翻译"))
        }
    }

    private var codexSection: some View {
        Section {
            LabeledContent(model.text("Provider", "服务")) { Text("Codex CLI") }
            VStack(alignment: .leading, spacing: 6) {
                Text(model.text("Current executable", "当前可执行文件")).font(.subheadline)
                Text(model.selectedCLI.isEmpty ?
                     model.text("Not found — choose your Codex executable.", "未找到，请选择 Codex 可执行文件。") :
                     model.selectedCLI)
                    .font(.caption.monospaced()).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button(model.text("Detect automatically", "自动查找")) { model.locateCLI() }
                Button(model.text("Choose…", "选择…")) { model.chooseCLI() }
            }
            .disabled(model.cliBusy || busy)
            if !model.candidates.filter(\.executable).isEmpty {
                Picker(model.text("Detected executables", "找到的可执行文件"), selection: $model.selectedCLI) {
                    ForEach(model.candidates.filter(\.executable)) { candidate in
                        Text(candidate.url.path).tag(candidate.url.path)
                    }
                }
                .disabled(model.cliBusy || busy)
                .onChange(of: model.selectedCLI) { _, _ in model.persistPresentation() }
            }
            Text(model.text("The selected path is remembered on this Mac. Detection only checks paths; it does not install Codex, sign in, or run a model. Use your existing Codex installation and account.",
                            "所选路径会保存在此 Mac 上。查找只检查路径，不会安装 Codex、登录或运行模型。请使用已有的 Codex 安装和账号。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            DisclosureGroup(model.text("Optional version check", "可选的版本检查")) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Button(model.text("Check version", "检查版本")) { model.versionCLI() }
                            .disabled(model.cliBusy || model.selectedCLI.isEmpty || busy)
                        if model.cliBusy {
                            ProgressView().controlSize(.small)
                            Button(model.text("Cancel check", "取消检查")) { model.cancelCLI() }
                        }
                    }
                    Text(model.text("Runs the selected executable with --version, with a 5-second limit. This is not an authentication test and is not required to translate.",
                                    "使用 --version 运行所选程序，限时 5 秒。这不是认证测试，翻译前也无需执行。"))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(model.cliStatus).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
            }
        } header: {
            Text(model.text("Codex connection", "Codex 连接"))
        }
    }

    private var shortcutSection: some View {
        Section {
            Toggle(model.text("Double ⌘C to translate selected text", "双击 ⌘C 翻译选中文字"), isOn: Binding(
                get: { model.monitorEnabled && model.translatePassiveSelections },
                set: { enabled in
                    model.translatePassiveSelections = enabled
                    if enabled { model.startMonitor() } else { model.stopMonitor() }
                }
            ))
            Text(model.text("Off until you enable it. Reads the selected text through Accessibility; it does not read your clipboard or simulate Copy. Secure Input stops monitoring.",
                            "主动开启后才会生效。通过辅助功能读取选中文字，不会读取剪贴板或模拟复制。安全输入模式会停止监听。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ViewThatFits(in: .horizontal) {
                HStack {
                    permissionButtons
                }
                VStack(alignment: .leading, spacing: 8) {
                    permissionButtons
                }
            }
            Text(model.permissions).font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            Text(model.monitorStatus).font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            Text(model.text("Permissions are requested only when you choose a permission action. After changing system permissions, you may need to restart the app and enable the shortcut again.",
                            "仅在你点击权限操作时请求授权。更改系统权限后，可能需要重启应用并重新开启快捷键。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("Selection shortcut & permissions", "划词快捷键与权限"))
        }
    }

    @ViewBuilder
    private var permissionButtons: some View {
        Button(model.text("Accessibility…", "辅助功能…")) { model.requestAX() }
        Button(model.text("Input Monitoring…", "输入监控…")) { model.requestInputMonitoring() }
        Button(model.text("Refresh permissions", "刷新权限")) { model.refreshPermissions() }
    }

    private var aboutSection: some View {
        Section {
            LabeledContent("CC Translate") {
                Text(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ??
                     model.text("Development build", "开发版本"))
                    .foregroundStyle(.secondary)
            }
            Text(model.text("Native macOS edition · SwiftUI & AppKit", "原生 macOS 版本 · SwiftUI 与 AppKit"))
                .font(.callout)
            Text(model.text("Text translation, result actions, streaming, cancellation, history, and Codex settings are available here. The full dictionary, screenshot translation, model management, login items, and app updates are not yet implemented in the product interface.",
                            "此界面已支持文字翻译、结果操作、流式输出、取消、历史记录和 Codex 设置。完整词典、截图翻译、模型管理、登录项和应用更新尚未在产品界面实现。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button(model.text("Open diagnostics…", "打开诊断…"), action: showDiagnostics)
            Text(model.text("Optional technical checks are separate from translation. Screen capture and local OCR are diagnostic probes, not a finished screenshot translation workflow.",
                            "可选的技术检查独立于翻译。屏幕捕获和本地 OCR 仍是诊断探针，并非完整的截图翻译流程。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("About & diagnostics", "关于与诊断"))
        }
    }
}

@MainActor
private struct DirectionPicker: View {
    @ObservedObject var model: ProbeModel
    @Binding var selection: String

    private let languages = ProbeModel.targetLanguages

    var body: some View {
        Picker(model.text("Translate to", "翻译为"), selection: $selection) {
            Text(model.text("Auto", "自动")).tag("auto")
            ForEach(languages.indices, id: \.self) { index in
                let language = languages[index]
                Text(model.text(language.1, language.2)).tag("to_" + language.0)
            }
            if selection != "auto" && !languages.contains(where: { "to_" + $0.0 == selection }) {
                Text(selection).tag(selection)
            }
        }
        .accessibilityLabel(model.text("Translation direction", "翻译方向"))
        .help(model.text("Auto: English to Chinese; other languages to English.",
                         "自动模式：中文译为英文，其他语言译为中文。"))
    }
}

@MainActor
private struct ModelPicker: View {
    @ObservedObject var model: ProbeModel
    @Binding var selection: String

    var body: some View {
        Picker(model.text("Model", "模型"), selection: $selection) {
            Text(model.text("Fast profile", "快速模式")).tag("auto-fast")
            Text(model.text("Codex default", "Codex 默认")).tag("auto")
            if selection != "auto-fast" && selection != "auto" {
                Text(selection).tag(selection)
            }
        }
        .accessibilityLabel(model.text("Codex model profile", "Codex 模型配置"))
    }
}

@MainActor
private func resultKindName(_ kind: String, model: ProbeModel) -> String {
    switch kind {
    case "text": return model.text("Text", "文字")
    case "code": return model.text("Code", "代码")
    case "mixed": return model.text("Mixed", "混合")
    case "dict", "dictionary": return model.text("Dictionary", "词典")
    case "summary": return model.text("Summary", "摘要")
    default: return kind
    }
}

private func historyDate(_ raw: String) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = formatter.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
    return date?.formatted(date: .abbreviated, time: .shortened) ?? raw
}

// Keep the native text view, selection, and scroll position alive across streamed deltas.
@MainActor
private struct NativeResultText: NSViewRepresentable {
    var text: String
    var formatted: Bool
    var streaming: Bool
    var label: String

    final class Coordinator {
        var source = ""
        var renderedRich = false
        var requestedRich = true
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        let view = NSTextView(frame: .zero)
        view.isEditable = false
        view.isSelectable = true
        view.isRichText = true
        view.isAutomaticLinkDetectionEnabled = false
        view.isAutomaticDataDetectionEnabled = false
        view.drawsBackground = false
        view.isVerticallyResizable = true
        view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.textContainerInset = NSSize(width: 12, height: 12)
        view.textContainer?.widthTracksTextView = true
        view.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        view.minSize = .zero
        view.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                             height: CGFloat.greatestFiniteMagnitude)
        view.setAccessibilityLabel(label)
        scroll.documentView = view
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSTextView, let storage = view.textStorage else { return }
        view.setAccessibilityLabel(label)
        let selected = view.selectedRange()
        let modeChanged = formatted != context.coordinator.requestedRich
        let rich = formatted && !streaming &&
            (context.coordinator.renderedRich || selected.length == 0 || modeChanged)
        context.coordinator.requestedRich = formatted
        guard context.coordinator.source != text || context.coordinator.renderedRich != rich else { return }
        let origin = scroll.contentView.bounds.origin
        let atBottom = view.bounds.height - scroll.contentView.bounds.maxY <= 28
        let continuing = !context.coordinator.source.isEmpty && text.hasPrefix(context.coordinator.source)
        if continuing && !rich && !context.coordinator.renderedRich {
            let suffix = String(text.dropFirst(context.coordinator.source.count))
            storage.append(Self.plain(suffix))
        } else {
            storage.setAttributedString(rich ? Self.styled(text) : Self.plain(text))
        }
        context.coordinator.source = text
        context.coordinator.renderedRich = rich
        let location = min(selected.location, storage.length)
        let preservedSelection = NSRange(location: location, length: min(selected.length, storage.length - location))
        if !NSEqualRanges(view.selectedRange(), preservedSelection) {
            view.setSelectedRange(preservedSelection)
        }
        if let container = view.textContainer { view.layoutManager?.ensureLayout(for: container) }
        if continuing && atBottom && selected.length == 0 {
            view.scrollRangeToVisible(NSRange(location: storage.length, length: 0))
        } else if continuing {
            scroll.contentView.scroll(to: origin)
            scroll.reflectScrolledClipView(scroll.contentView)
        } else {
            scroll.contentView.scroll(to: .zero)
            scroll.reflectScrolledClipView(scroll.contentView)
        }
    }

    private static func plain(_ source: String) -> NSAttributedString {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 4
        return NSAttributedString(string: source, attributes: [
            .font: NSFont.systemFont(ofSize: 15),
            .foregroundColor: NSColor.labelColor,
            .paragraphStyle: paragraph
        ])
    }

    private static func styled(_ source: String) -> NSAttributedString {
        let result = NSMutableAttributedString(string: "")
        let lines = source.components(separatedBy: "\n")
        var fence: String?
        for (index, line) in lines.enumerated() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let marker = trimmed.hasPrefix("```") ? "```" : trimmed.hasPrefix("~~~") ? "~~~" : nil
            if let marker, fence == nil || fence == marker {
                fence = fence == nil ? marker : nil
                continue
            }
            let newline = index == lines.count - 1 ? "" : "\n"
            let block: NSMutableAttributedString
            if fence != nil {
                block = NSMutableAttributedString(attributedString: plain(line + newline))
                block.addAttribute(.font, value: NSFont.monospacedSystemFont(ofSize: 14, weight: .regular),
                                   range: NSRange(location: 0, length: block.length))
            } else {
                let heading = line.prefix { $0 == "#" }.count
                if (1...6).contains(heading), line.dropFirst(heading).hasPrefix(" ") {
                    block = NSMutableAttributedString(attributedString:
                        inlineStyled(String(line.dropFirst(heading + 1)) + newline))
                    block.addAttribute(.font, value: NSFont.systemFont(ofSize: heading <= 2 ? 18 : 16, weight: .semibold),
                                       range: NSRange(location: 0, length: block.length))
                } else {
                    block = NSMutableAttributedString(attributedString: inlineStyled(line + newline))
                }
            }
            result.append(block)
        }
        return result
    }

    private static func inlineStyled(_ source: String) -> NSAttributedString {
        guard let parsed = try? AttributedString(markdown: source,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) else { return plain(source) }
        let result = NSMutableAttributedString(string: "")
        for run in parsed.runs {
            let segment = String(parsed[run.range].characters)
            let attributed = NSMutableAttributedString(attributedString: plain(segment))
            let range = NSRange(location: 0, length: attributed.length)
            var font = NSFont.systemFont(ofSize: 15)
            if let intent = run.inlinePresentationIntent {
                if intent.contains(.code) { font = NSFont.monospacedSystemFont(ofSize: 14, weight: .regular) }
                if intent.contains(.stronglyEmphasized) { font = NSFontManager.shared.convert(font, toHaveTrait: .boldFontMask) }
                if intent.contains(.emphasized) { font = NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask) }
                if intent.contains(.strikethrough) {
                    attributed.addAttribute(.strikethroughStyle, value: NSUnderlineStyle.single.rawValue, range: range)
                }
            }
            attributed.addAttribute(.font, value: font, range: range)
            if let link = run.link, ["http", "https"].contains(link.scheme?.lowercased() ?? "") {
                attributed.addAttribute(.link, value: link, range: range)
                attributed.addAttribute(.foregroundColor, value: NSColor.linkColor, range: range)
            }
            result.append(attributed)
        }
        return result
    }
}
