import AppKit
import SwiftUI
import CCTranslateSupport

@MainActor
struct TranslationInputBudgetView: View {
    @ObservedObject var model: ProbeModel
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(model.inputLimit.saved.map {
                model.text("\(text.unicodeScalars.count) / \($0) Unicode code points",
                           "\(text.unicodeScalars.count) / \($0) Unicode 码点")
            } ?? model.text("\(text.unicodeScalars.count) Unicode code points · Saved limit not loaded",
                            "\(text.unicodeScalars.count) 个 Unicode 码点 · 尚未读取已保存上限"))
                .accessibilityIdentifier("input-code-point-count")
            Text(model.text("\(text.utf8.count) / 8,192 UTF-8 bytes",
                            "\(text.utf8.count) / 8,192 UTF-8 字节"))
                .accessibilityIdentifier("input-byte-count")
            if let issue = model.inputIssue(for: text), issue != .empty {
                Label(issue.message(using: model), systemImage: "exclamationmark.circle")
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("input-length-error")
            }
        }
        .font(.caption).monospacedDigit().foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }
}

@MainActor
struct TranslatorView: View {
    @ObservedObject var model: ProbeModel
    var showHistory: () -> Void
    var showSettings: () -> Void
    var showCapture: () -> Void
    @State private var editorFocused = false

    private var busy: Bool { model.preparing || model.active }
    private var canTranslate: Bool {
        !busy && model.inputIssue(for: model.input) == nil
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            options
            if model.needsCLI {
                HStack(spacing: 10) {
                    Image(systemName: "terminal").accessibilityHidden(true)
                    Text(model.text("The local dictionary works offline. Choose Codex for model translation.",
                                    "本地词典可离线使用。模型翻译需要选择 Codex。"))
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
            Button(action: showCapture) {
                Label(model.text("Screenshot", "截图"), systemImage: "viewfinder")
            }
            .help(model.text("Capture a region and recognize its text locally", "截取区域并在本机识别文字"))
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
                .labelsHidden().frame(minWidth: 180, idealWidth: 280, maxWidth: 340)
                .layoutPriority(1)
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
            NativeTranslationEditor(
                text: $model.input, textScale: model.nativeTextScale, focused: $editorFocused,
                label: model.text("Text to translate", "要翻译的文字"),
                hint: model.text("Type or paste. Command Return translates.", "输入或粘贴文字，按 Command Return 翻译。"),
                placeholder: model.text("Type or paste text here…", "在这里输入或粘贴文字…")
            )
            .padding(8)
            .background(Color(nsColor: .textBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(editorFocused ? Color.accentColor :
                                  Color(nsColor: .separatorColor), lineWidth: 1)
                    .allowsHitTesting(false)
            }
            .frame(minHeight: 180)
            HStack(alignment: .top) {
                TranslationInputBudgetView(model: model, text: model.input)
                Spacer(minLength: 0)
                Text("⌘ ↩").font(.caption).foregroundStyle(.secondary)
                    .accessibilityHidden(true)
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
                    .accessibilityIdentifier("translate-input-text")
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
        !busy && model.resultHasOriginalInput && !model.output.isEmpty && model.inputIssue(for: model.resultInput) == nil
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
                                 label: model.text("Translation result", "翻译结果"),
                                 verbatimPrefix: model.isLocalDictionaryResult ? model.primaryResult : nil,
                                 textScale: model.nativeTextScale)
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
            ImageCleanupView(model: model)
            if !model.resultHasOriginalInput && !model.output.isEmpty {
                Text(model.resultKind == "ocr"
                     ? model.text("Image result · No original text retained. To send the image again, use the capture preview.",
                                  "图片结果 · 未保留原文。如需再次发送图片，请使用截图预览。")
                     : model.text("No original text is stored for this result.", "此结果未保存原文。"))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
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

    private var sourcesLabels: DictionarySourcesLabels {
        .init(title: model.text("Sources & licenses", "来源与许可"),
              explanation: model.text("Sources for the local dictionary entry only, not model-generated additions.",
                                      "仅显示本地词典词条的来源，不包含模型生成的补充内容。"),
              sourceID: model.text("Source ID", "来源标识"),
              version: model.text("Version", "版本"),
              license: model.text("License", "许可"),
              missing: model.text("Not provided", "未提供"),
              close: model.text("Close", "关闭"))
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
        if busy {
            return model.translatingImage ? model.text("Working on your image", "正在处理你的图片") :
                model.text("Working on your words", "正在处理你的文字")
        }
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
            if !model.resultSources.isEmpty {
                DictionarySourcesControl(sources: model.resultSources, labels: sourcesLabels)
                    .fixedSize()
            }
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
            .disabled(model.output.isEmpty || !model.resultHasOriginalInput)
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
                    .disabled(!model.resultHasOriginalInput)
                Button(model.resultActionTitle(.asText)) { model.performResultAction(.asText) }
                    .disabled(!model.resultHasOriginalInput)
                Menu(model.resultActionTitle(.retranslate)) {
                    ForEach(ProbeModel.targetLanguages.indices, id: \.self) { index in
                        let language = ProbeModel.targetLanguages[index]
                        Button(model.text(language.1, language.2)) {
                            model.performResultAction(.retranslate, targetLanguage: language.0)
                        }
                        .disabled(!model.resultHasOriginalInput)
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
    @FocusState private var searchFocused: Bool

    private var selectedRow: ProbeModel.HistoryRow? {
        model.historyPage.first { $0.id == selectedID }
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
                .disabled(model.historyBusy || !model.settingsReady || model.settingsBusy)
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
        .background {
            HistoryWindowCloseObserver(onClose: { model.closeHistorySearch() })
                .frame(width: 0, height: 0).accessibilityHidden(true)
        }
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            model.openProduct()
            if model.historyPhase == .idle && model.historyTotal == nil &&
                model.historyPage.isEmpty && !model.historyBusy { model.loadHistory() }
        }
        .onChange(of: model.historyPage.map(\.id)) { _, ids in
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
                TextField(model.text("Search all history", "搜索全部历史记录"),
                          text: $model.historySearch)
                    .textFieldStyle(.plain)
                    .focused($searchFocused)
                    .onSubmit { model.submitHistorySearch() }
                    .accessibilityLabel(model.text("Search all history", "搜索全部历史记录"))
                    .accessibilityHint(model.text("Search originals, translations, or dates. Press Return to search now.",
                                                 "搜索原文、译文或日期。按 Return 立即搜索。"))
                if !model.historySearch.isEmpty {
                    Button {
                        model.historySearch = ""
                        model.submitHistorySearch()
                        searchFocused = true
                    } label: {
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
                ForEach(ProbeModel.historyKinds, id: \.self) { kind in
                    Text(resultKindName(kind, model: model)).tag(kind)
                }
            }
            if let total = model.historyTotal {
                Text(model.text("\(model.historyPage.count) of \(total) matching records loaded",
                                "已加载 \(model.historyPage.count) 条，共 \(total) 条匹配记录"))
                    .font(.caption).foregroundStyle(.secondary)
            } else if !model.historyPage.isEmpty {
                Text(model.text("\(model.historyPage.count) previously loaded · Total not current",
                                "此前加载 \(model.historyPage.count) 条 · 总数尚未更新"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            List(selection: $selectedID) {
                ForEach(model.historyPage) { row in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(row.hasOriginalInput ? row.input : row.kind == "ocr"
                             ? model.text("Image translation", "图片翻译") : model.text("Translation", "翻译结果"))
                            .font(model.nativeTextScale.bodyFont).lineLimit(2)
                        Text(row.output).font(model.nativeTextScale.captionFont).foregroundStyle(.secondary).lineLimit(2)
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
                if model.historyPage.isEmpty {
                    Text(emptyHistoryTitle)
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).padding()
                        .allowsHitTesting(false)
                }
            }
            HStack {
                if model.historyPhase == .waiting || model.historyPhase == .loading || model.historyPhase == .clearing {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(model.text("Loading history", "正在加载历史记录"))
                }
                Text(model.historyStatus).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if model.hasNextHistoryPage {
                Button(model.text("Load more", "加载更多")) { model.loadHistory(next: true) }
                    .disabled(model.historyBusy || model.settingsBusy || model.historyPhase != .loaded)
                    .frame(maxWidth: .infinity)
            }
        }
        .padding(14)
    }

    private var emptyHistoryTitle: String {
        switch model.historyPhase {
        case .waiting, .loading: return model.text("Searching all history…", "正在搜索全部历史记录…")
        case .clearing: return model.text("Clearing all history…", "正在清除所有历史记录…")
        case .failed: return model.text("History could not be loaded. Refresh to try again.", "无法加载历史记录，请刷新重试。")
        case .stale: return model.text("Saved history changed. Refresh to read the current records.",
                                      "已保存的历史记录已更改，请刷新读取最新记录。")
        case .idle, .loaded:
            if model.historyTotal == 0 {
                return model.historySearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && model.historyFilter == "all"
                    ? model.text("No saved translations.", "尚无已保存的翻译。")
                    : model.text("No matching translations. Try another search or type.", "没有匹配的翻译，请尝试其他搜索文字或类型。")
            }
            return model.text("Search or refresh to read saved history.", "搜索或刷新以读取已保存的历史记录。")
        }
    }

    @ViewBuilder
    private var historyDetail: some View {
        if let row = selectedRow {
            HistoryTranslationDetail(model: model, row: row, useEntry: useEntry)
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
}

@MainActor
struct HistoryTranslationDetail: View {
    @ObservedObject var model: ProbeModel
    let row: ProbeModel.HistoryRow
    var useEntry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(resultKindName(row.kind, model: model)).font(.headline)
                Spacer()
                Text(historyDate(row.timestamp)).font(.caption).foregroundStyle(.secondary)
            }
            Text(model.text("Original", "原文")).font(.subheadline.bold())
                .accessibilityAddTraits(.isHeader)
            if row.hasOriginalInput {
                NativeResultText(text: row.input, formatted: false, streaming: false,
                                 label: model.text("Saved original text", "已保存的原文"),
                                 textScale: model.nativeTextScale)
                    .frame(minHeight: 80, maxHeight: 150)
            } else {
                Text(model.text("Original text is not stored in this record.", "此记录未保存原文。"))
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            Text(model.text("Translation", "翻译结果")).font(.subheadline.bold())
                .accessibilityAddTraits(.isHeader)
            NativeResultText(text: row.output, formatted: !row.isLocalDictionary, streaming: false,
                             label: model.text("Saved translation", "已保存的翻译"),
                             textScale: model.nativeTextScale)
                .frame(minHeight: 110)
            ViewThatFits(in: .horizontal) {
                HStack { historyActions }
                VStack(alignment: .leading, spacing: 8) { historyActions }
            }
        }
        .padding(18)
    }

    @ViewBuilder
    private var historyActions: some View {
        Button(model.text("Copy result", "复制结果")) { model.copyText(row.output) }
            .disabled(row.output.isEmpty)
        Button(model.text("Copy bilingual", "复制双语")) {
            model.copyText(row.input + "\n\n" + row.output)
        }
        .disabled(!row.hasOriginalInput)
        Button(row.hasOriginalInput ? model.text("Reuse original", "复用原文") : model.text("Open result", "打开结果")) {
            model.reuseHistory(row)
            useEntry()
        }
        .buttonStyle(.bordered)
        .help(model.text("Open this record in the translator without sending it.",
                         "在翻译窗口打开此记录，不会立即发送。"))
    }
}

@MainActor
struct InputLimitSettingsView: View {
    @ObservedObject var model: ProbeModel
    private var preference: IntegerPreference { model.inputLimit }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(preference.saved.map {
                model.text("Saved input limit: \($0) Unicode code points", "已保存输入上限：\($0) 个 Unicode 码点")
            } ?? model.text("Saved input limit: Not confirmed", "已保存输入上限：尚未确认"))
                .textSelection(.enabled)
            HStack(alignment: .firstTextBaseline) {
                TextField(model.text("Input limit", "输入上限"), text: Binding(
                    get: { preference.draft }, set: { model.editInputLimit($0) }
                ))
                .textFieldStyle(.roundedBorder).frame(maxWidth: 220)
                .disabled(!model.canEditInputLimit)
                .accessibilityIdentifier("input-limit-input")
                .accessibilityLabel(model.text("Maximum input Unicode code points", "输入 Unicode 码点上限"))
                Button(model.text("Apply input limit", "应用输入上限")) { model.applyInputLimit() }
                    .disabled(!model.canEditInputLimit || preference.proposed == nil ||
                              preference.proposed == preference.saved)
                    .accessibilityIdentifier("apply-input-limit")
            }
            Text(model.text("Enter a positive whole number, up to \(ConfigurationDocument.maxNumber).",
                            "请输入不超过 \(ConfigurationDocument.maxNumber) 的正整数。"))
                .font(.caption).foregroundStyle(.secondary)
            if let saved = preference.saved, !preference.supported.contains(saved) {
                Label(model.text("The saved value is invalid for text translation. It has not been changed; enter a positive value to correct it.",
                                 "已保存的值不适用于文字翻译，尚未改动。请输入正整数并明确保存以纠正。"),
                      systemImage: "exclamationmark.triangle")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            Text(model.text("Counts Unicode code points, including spaces and line breaks; combining marks count separately. Typed, selected, OCR and dictionary text must also fit 8192 UTF-8 bytes. Raising this setting does not increase that byte budget. Text is never shortened automatically.",
                            "按 Unicode 码点计数，空格、换行和组合标记均计入。输入、选中、OCR 和词典文字还必须满足 8192 个 UTF-8 字节上限。提高此设置不会增加字节预算，也不会自动缩短原文。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(model.text("Saving does not cancel or replay a submitted request or change saved history. A request uses the setting when the helper captures its configuration. Image translation and result actions keep their own limits.",
                            "保存不会取消或重放已提交请求，也不更改已保存的历史。请求以助手捕获配置时的设置为准。图片翻译和结果操作沿用各自限制。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !message.isEmpty {
                Text(message).font(.callout).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if case .failed = preference.phase {
                Button(model.text("Reload saved input limit", "重新读取已保存输入上限")) { model.reloadInputLimit() }
                    .disabled(!model.canReloadInputLimit)
                    .accessibilityIdentifier("reload-input-limit")
            }
        }
    }

    private var message: String {
        switch preference.phase {
        case .idle: return ""
        case .invalidInput:
            return model.text("Enter a positive whole number within the range above. Nothing was saved.",
                              "请输入上述范围内的正整数，尚未保存。")
        case .saving:
            return model.text("Saving input limit… The saved limit above is the last confirmed value.",
                              "正在保存输入上限… 上方显示上次确认的已保存值。")
        case .readingBack:
            return model.text("Reading back the saved input limit…", "正在回读已保存输入上限…")
        case .saved:
            return model.text("Input limit saved and read back.", "输入上限已保存并回读确认。")
        case .differentReadback:
            return model.text("The saved limit differs from your entry. The actual value is shown above; your entry is retained. No write was retried.",
                              "已保存上限与你的输入不同。上方显示实际回读值，并保留你的输入，未重试写入。")
        case .failed(let code):
            return model.text("Input limit could not be confirmed (\(code)). Reload the saved setting; no write was retried.",
                              "无法确认输入上限（\(code)）。请重新读取已保存设置，未重试写入。")
        }
    }
}

@MainActor
struct HistoryLimitSettingsView: View {
    @ObservedObject var model: ProbeModel
    private enum Control: Hashable { case input, confirmation }
    @FocusState private var focused: Control?

    private var preference: HistoryLimitPreference { model.historyLimit }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(preference.saved.map {
                model.text("Saved limit: \($0) records", "已保存条数：\($0) 条")
            } ?? model.text("Saved limit: Not confirmed", "已保存条数：尚未确认"))
                .textSelection(.enabled)
            HStack(alignment: .firstTextBaseline) {
                TextField(model.text("Number of records", "保留条数"), text: Binding(
                    get: { preference.draft }, set: { model.editHistoryLimit($0) }
                ))
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 220)
                .disabled(!model.canEditHistoryLimit || preference.confirmation != nil)
                .focused($focused, equals: .input)
                .accessibilityIdentifier("history-limit-input")
                .accessibilityLabel(model.text("Number of history records to keep", "要保留的历史记录条数"))
                Button(model.text("Apply limit", "应用条数")) { model.applyHistoryLimit() }
                    .disabled(!model.canEditHistoryLimit || preference.proposed == nil ||
                              preference.proposed == preference.saved || preference.confirmation != nil)
                    .accessibilityIdentifier("apply-history-limit")
            }
            Text(model.text("Enter a whole number from 1 to 10000.", "请输入 1 到 10000 之间的整数。"))
                .font(.caption).foregroundStyle(.secondary)
            if let saved = preference.saved, !HistoryLimitPreference.supported.contains(saved) {
                Label(model.text("The saved value is not supported by Mac translation. It has not been changed. Enter a supported value to correct it.",
                                 "已保存的值不受 Mac 翻译支持，尚未改动。可输入支持的条数并明确保存以纠正。"),
                      systemImage: "exclamationmark.triangle")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            Text(model.text("Saving does not delete records immediately. The next time a history record is added, only the newest N records are kept, including the new record; older records are deleted. A translation already in progress may use the new limit when it finishes. Increasing the limit does not restore deleted records.",
                            "保存设置不会立即删除记录。下次实际新增历史记录时，仅保留最新 N 条（包含新记录），更早记录会被删除。正在进行的翻译完成后也可能按新条数修剪。提高条数不会恢复已删除记录。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let reduction = preference.confirmation {
                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(model.text("Reduce saved limit from \(reduction.from) to \(reduction.to)?",
                                        "将已保存条数从 \(reduction.from) 降为 \(reduction.to)？"))
                            .font(.headline).accessibilityAddTraits(.isHeader)
                        Text(model.text("Older records will be deleted on the next actual history addition, not when you confirm.",
                                        "确认时不会删除记录；下次实际新增历史记录时将删除超出条数的旧记录。"))
                            .font(.callout).fixedSize(horizontal: false, vertical: true)
                        HStack {
                            Button(model.text("Cancel", "取消")) { model.cancelHistoryLimitReduction() }
                                .focused($focused, equals: .confirmation)
                                .accessibilityIdentifier("cancel-history-limit-reduction")
                            Button(model.text("Reduce and save", "降低并保存"), role: .destructive) {
                                model.confirmHistoryLimitReduction()
                            }
                            .disabled(!model.canEditHistoryLimit)
                            .accessibilityIdentifier("confirm-history-limit-reduction")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !message.isEmpty {
                Text(message).font(.callout).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if case .failed = preference.phase {
                Button(model.text("Reload saved history limit", "重新读取已保存条数")) { model.reloadHistoryLimit() }
                    .disabled(!model.canReloadHistoryLimit)
                    .accessibilityIdentifier("reload-history-limit")
            }
        }
        .onChange(of: preference.confirmation) { _, value in
            if value != nil { focused = .confirmation }
            else if model.canEditHistoryLimit { focused = .input }
        }
    }

    private var message: String {
        switch preference.phase {
        case .idle: return ""
        case .invalidInput: return model.text("Enter a whole number from 1 to 10000. Nothing was saved.",
                                              "请输入 1 到 10000 之间的整数，尚未保存。")
        case .saving: return model.text("Saving history limit… The saved limit above is the last confirmed value.",
                                       "正在保存条数… 上方显示的是上次确认的已保存值。")
        case .readingBack: return model.text("Reading back the saved history limit…", "正在回读已保存的条数…")
        case .saved: return model.text("History limit saved and read back. No history was deleted by this setting change.",
                                      "条数已保存并回读确认。此次设置更改未删除历史记录。")
        case .differentReadback: return model.text("The saved limit differs from your entry. The actual saved value is shown above; your entry is retained. No write was retried.",
                                                  "已保存条数与你的输入不同。上方显示实际回读值，并保留你的输入，未重试写入。")
        case .failed(let code): return model.text("History limit could not be confirmed (\(code)). Reload the saved setting; no write was retried.",
                                                  "无法确认条数（\(code)）。请重新读取已保存设置，未重试写入。")
        }
    }
}

@MainActor
struct TranslationSettingsView: View {
    @ObservedObject var model: ProbeModel
    var showDiagnostics: () -> Void
    var showAbout: () -> Void

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
            Section {
                InputLimitSettingsView(model: model)
            } header: {
                Text(model.text("Input length", "输入长度"))
            }
            Section {
                HistoryLimitSettingsView(model: model)
            } header: {
                Text(model.text("History retention", "历史保留条数"))
            }
            DictionarySettingsSection(model: model, dictionary: model.dictionary)
            codexSection
            PlainPasteSettingsSection(model: model, paste: model.plainPaste)
            shortcutSection
            aboutSection
        }
        .formStyle(.grouped)
        .frame(minWidth: 530, minHeight: 460)
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear { model.refreshDictionary() }
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
            NativeTextScalePicker(model: model)
        } header: {
            Text(model.text("General", "通用"))
        }
    }

    var translationSection: some View {
        Section {
            DirectionPicker(model: model, selection: Binding(
                get: { model.direction },
                set: { model.direction = $0; model.saveSettings() }
            ))
            .disabled(model.settingsBusy || busy || model.dictionary.committing)
            CodexModelSettingsView(model: model)
            Toggle(model.text("Automatic long-text summary", "长文自动摘要"), isOn: Binding(
                get: { model.summaryEnabled ?? false },
                set: { model.saveSummaryPreference($0) }
            ))
            .toggleStyle(.checkbox)
            .disabled(!model.canSaveSummaryPreference)
            .accessibilityIdentifier("automatic-long-text-summary")
            .accessibilityValue(model.summaryEnabled.map {
                $0 ? model.text("On", "已开启") : model.text("Off", "已关闭")
            } ?? model.text("Not confirmed", "尚未确认"))
            Text(model.text("For eligible prose of 400 or more characters, include a brief summary before the full translation. Applies to future translations, not an in-progress request. Short text, pure code, and screenshots are unaffected. The result's Summarize action remains available.",
                            "翻译符合条件的 400 字符及以上自然语言长文时，先给出简短摘要，再显示完整译文。仅影响后续翻译，不改变正在进行的请求；短文、纯代码和截图不受影响。结果中的“生成摘要”操作仍可使用。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.summaryPreferenceMessage.isEmpty {
                Text(model.summaryPreferenceMessage)
                    .font(.caption).foregroundStyle(.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            if case .failed = model.summaryPreferencePhase {
                Button(model.text("Reload saved summary setting", "重新读取已保存的摘要设置")) {
                    model.reloadSummaryPreference()
                }
                .disabled(!model.canReloadSummaryPreference)
                .accessibilityIdentifier("reload-summary-setting")
            }
            if model.settingsReady {
                Toggle(model.text("Save translation history", "保存翻译历史记录"), isOn: Binding(
                    get: { model.historyEnabled },
                    set: { model.saveSettings(history: $0) }
                ))
                .disabled(model.settingsBusy || model.dictionary.committing)
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
            if model.cliChangeDeferred {
                Label(model.text("The selected Codex will apply after the dictionary operation finishes.",
                                 "词典操作完成后会应用所选 Codex。"),
                      systemImage: "clock")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
            Text(model.text("Off until you enable it. Accessibility is tried first. After an explicit double ⌘C, unsupported selections may use only a new plain-text copy correlated with the same foreground source and focus. Old or uncorrelated clipboard contents are not used; Copy is never simulated or blocked. Secure Input stops monitoring.",
                            "主动开启后才会生效。优先通过辅助功能读取选区。明确双击 ⌘C 后，无法读取的选区仅可回退至与同一前台来源及焦点关联的新纯文本复制。不使用旧或无法关联的剪贴板内容，不模拟或阻止复制。安全输入模式会停止监听。"))
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
            Button(model.text("About CC Translate & third-party licenses…", "关于 CC Translate 与第三方许可…"),
                   action: showAbout)
            Text(model.text("Native macOS edition · SwiftUI & AppKit", "原生 macOS 版本 · SwiftUI 与 AppKit"))
                .font(.callout)
            Text(model.text("Available: text translation, local screenshot OCR, explicit image translation, local dictionary, result actions, history, and Codex model discovery and custom model settings. Login items and app updates are not yet implemented.",
                            "已支持文字翻译、本地截图文字识别、明确发送图片翻译、本地词典、结果操作、历史记录，以及 Codex 模型发现和自定义模型设置。登录项和应用更新尚未实现。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button(model.text("Open diagnostics…", "打开诊断…"), action: showDiagnostics)
            Text(model.text("Optional technical checks are separate from translation. Screenshot translation does not require diagnostics: capture and text recognition run locally, and only reviewed text is sent when you choose Translate text.",
                            "可选的技术检查独立于翻译，无需先运行诊断即可使用截图翻译。截图与文字识别在本机完成，只有点击“翻译文字”时才发送编辑后的文字。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("About & diagnostics", "关于与诊断"))
        }
    }
}

@MainActor
struct DirectionPicker: View {
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
struct ModelPicker: View {
    @ObservedObject var model: ProbeModel
    @Binding var selection: String

    var body: some View {
        Picker(model.text("Model", "模型"), selection: Binding(
            get: { CodexModelSettings.ChoiceID(value: selection) },
            set: { selection = $0.value }
        )) {
            ForEach(model.modelChoices(selection: selection).map {
                CodexModelSettings.ChoiceID(value: $0)
            }, id: \.self) { profile in
                Text(verbatim: label(profile.value)).tag(profile)
                    .disabled(!CodexModelSettings.isPreset(profile.value) &&
                              CodexModelSettings.validateCustom(profile.value) != nil)
            }
        }
        .accessibilityLabel(model.text("Codex model profile", "Codex 模型配置"))
        .accessibilityValue(label(selection))
        .help(label(selection) + model.text(" · Refresh models or enter an ID in Settings.", " · 在设置中刷新模型或输入 ID。"))
        .disabled(model.active || model.preparing || model.settingsBusy || model.dictionary.committing)
    }

    private func label(_ value: String) -> String {
        switch value {
        case "auto-fast": return model.text("Fast profile", "快速模式")
        case "auto": return model.text("Codex default", "Codex 默认")
        case "": return model.text("Empty model ID", "模型 ID 为空")
        default:
            if let row = model.discoveredModel(value), !row.name.isEmpty,
               !CodexModelSettings.sameID(row.name, value) {
                return row.name + " — " + value
            }
            return value
        }
    }
}

@MainActor
struct CodexModelSettingsView: View {
    @ObservedObject var model: ProbeModel

    private var validation: CodexModelSettings.Validation? {
        CodexModelSettings.validateCustom(model.modelSettings.draft)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ModelPicker(model: model, selection: Binding(
                get: { model.modelProfile }, set: { model.applyModelProfile($0) }))
                .disabled(!model.canApplyModelSetting)
            Text(model.text("Custom model ID", "自定义模型 ID")).font(.subheadline)
            TextField(model.text("Enter the exact model ID", "输入完整的模型 ID"), text: Binding(
                get: { model.modelSettings.draft }, set: { model.editCustomModelID($0) }),
                onEditingChanged: { model.setCustomModelEditing($0) })
                .textFieldStyle(.roundedBorder)
                .disableAutocorrection(true)
                .accessibilityLabel(model.text("Custom model ID", "自定义模型 ID"))
                .accessibilityIdentifier("custom-codex-model-id")
            HStack {
                Button(model.text("Apply model", "应用模型")) { model.applyCustomModelID() }
                    .disabled(!model.canApplyModelSetting || validation != nil)
                Button(model.text("Reset draft", "重置草稿")) { model.resetCustomModelDraft() }
                Spacer(minLength: 8)
                Text(model.text("\(model.modelSettings.draft.utf8.count)/256 UTF-8 bytes",
                                "\(model.modelSettings.draft.utf8.count)/256 UTF-8 字节"))
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            if let validation, !model.modelSettings.draft.isEmpty {
                Label(validationMessage(validation), systemImage: "exclamationmark.circle")
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
            } else if model.modelSettings.draftEdited && !model.modelSettings.draft.isEmpty &&
                        !CodexModelSettings.sameID(model.modelSettings.draft, model.modelSettings.savedProfile) {
                Text(model.text("Unapplied draft. Typing does not change the model used for translation.",
                                "草稿尚未应用。输入不会更改翻译使用的模型。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            status
            HStack(alignment: .top) {
                Text(model.text("Last read setting:", "上次读取的设置：")).foregroundStyle(.secondary)
                Text(verbatim: model.modelSettings.savedProfile ?? model.text("Not loaded", "尚未加载"))
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            }.font(.caption)
            if let saved = model.modelSettings.savedProfile, !CodexModelSettings.sameID(saved, model.modelProfile) {
                Text(model.text("The current selection is not confirmed as saved. A new translation applies its selected profile before sending.",
                                "当前选择尚未确认保存。新的翻译会在发送前应用其所选模型配置。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button(model.text("Reload saved setting", "重新读取已保存设置")) { model.reloadModelSetting() }
                .disabled(!model.ready || model.settingsBusy || model.active || model.preparing || model.dictionary.committing)
            ModelCatalogSettingsView(model: model)
            if !model.ready && !model.modelCatalog.busy {
                Text(model.text("Open Settings again to reconnect before applying. Your draft stays editable.",
                                "请再次打开设置以连接后应用。你仍可编辑草稿。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(model.text(
                "IDs are case-sensitive and are not trimmed. Apply saves and reads back settings, without requesting a model or checking account access.",
                "ID 区分大小写，不会自动去除空格。“应用”会保存并重新读取设置，不请求模型，也不检查账号权限。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var status: some View {
        switch model.modelSettings.phase {
        case .idle:
            if model.settingsBusy {
                ProgressView(model.text("Loading saved setting…", "正在读取已保存设置…")).controlSize(.small)
            }
        case .saving(let profile):
            ProgressView(model.text("Saving \(profile)…", "正在保存 \(profile)…")).controlSize(.small)
        case .reading:
            ProgressView(model.text("Reading back saved setting…", "正在重新读取已保存设置…")).controlSize(.small)
        case .applied(let profile):
            Label(model.text("Saved and read back: \(profile)", "已保存并重新读取：\(profile)"),
                  systemImage: "checkmark.circle").font(.caption).textSelection(.enabled)
        case .failed(let failure):
            Label(failureMessage(failure), systemImage: "exclamationmark.triangle")
                .font(.caption).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func validationMessage(_ validation: CodexModelSettings.Validation) -> String {
        switch validation {
        case .empty: return model.text("Enter a custom model ID.", "请输入自定义模型 ID。")
        case .whitespace: return model.text("Remove whitespace and control characters from the ID; nothing is trimmed automatically.",
                                           "请移除 ID 中的空白和控制字符，不会自动去除这些字符。")
        case .tooLong: return model.text("The ID exceeds 256 UTF-8 bytes. Shorten it before applying.",
                                        "ID 超过 256 UTF-8 字节，请缩短后再应用。")
        case .preset: return model.text("Use the Model picker for Fast profile or Codex default.",
                                       "请通过“模型”选择快速模式或 Codex 默认。")
        }
    }

    private func failureMessage(_ failure: CodexModelSettings.Failure) -> String {
        switch failure {
        case .invalidID(let reason): return validationMessage(reason)
        case .busy: return model.text("Wait for the current request or settings operation. Your draft is retained.",
                                     "请等待当前请求或设置操作完成，草稿已保留。")
        case .unavailable: return model.text("Load settings before applying a model. Your draft is retained.",
                                            "请先加载设置再应用模型，草稿已保留。")
        case .operation(let code): return model.text("Settings operation failed (\(code)). No automatic retry; reload the saved setting before retrying.",
                                                    "设置操作失败（\(code)）。不会自动重试，请先重新读取已保存设置再重试。")
        case .invalidReadback: return model.text("The settings readback was invalid. Reconnect and reload; the model was not confirmed.",
                                                "设置回读无效。请重新连接并读取，模型尚未确认。")
        case .interrupted: return model.text("The connection closed before the model could be confirmed. Reload settings; the write is not replayed.",
                                            "模型确认前连接已关闭。请重新读取设置，不会重放写入。")
        case .differentReadback(let expected, let actual):
            return model.text("Requested \(expected), but read back \(actual). No model substitution was accepted. Review the saved setting.",
                              "请求的是 \(expected)，但回读为 \(actual)。未接受模型替换，请检查已保存设置。")
        }
    }
}

@MainActor
private func resultKindName(_ kind: String, model: ProbeModel) -> String {
    switch kind {
    case "text": return model.text("Text", "文字")
    case "code": return model.text("Code", "代码")
    case "ocr": return model.text("OCR", "文字识别")
    case "mixed": return model.text("Mixed", "混合")
    case "dict", "dictionary": return model.text("Dictionary", "词典")
    case "summary": return model.text("Summary", "摘要")
    default: return kind
    }
}

@MainActor
private struct HistoryWindowCloseObserver: NSViewRepresentable {
    var onClose: () -> Void

    // Retained panels may keep their SwiftUI roots mounted after closing.
    final class ObserverView: NSView {
        var onClose: (() -> Void)?
        private var observation: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let observation { NotificationCenter.default.removeObserver(observation) }
            observation = nil
            if let window {
                observation = NotificationCenter.default.addObserver(
                    forName: NSWindow.willCloseNotification, object: window, queue: .main
                ) { [weak self] _ in
                    MainActor.assumeIsolated { self?.onClose?() }
                }
            }
        }

        deinit {
            if let observation { NotificationCenter.default.removeObserver(observation) }
        }
    }

    func makeNSView(context: Context) -> ObserverView {
        let view = ObserverView(frame: .zero)
        view.onClose = onClose
        return view
    }

    func updateNSView(_ view: ObserverView, context: Context) { view.onClose = onClose }
}

private func historyDate(_ raw: String) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = formatter.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
    return date?.formatted(date: .abbreviated, time: .shortened) ?? raw
}

// Keep the native text view, selection, and scroll position alive across streamed deltas.
@MainActor
struct NativeResultText: NSViewRepresentable {
    var text: String
    var formatted: Bool
    var streaming: Bool
    var label: String
    var verbatimPrefix: String? = nil
    var textScale: NativeTextScale = .standard

    final class Coordinator {
        var source = ""
        var renderedRich = false
        var requestedRich = true
        var verbatimPrefix: String?
        var textScale: NativeTextScale = .standard
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
        let contentChanged = context.coordinator.source != text || context.coordinator.renderedRich != rich ||
            context.coordinator.verbatimPrefix != verbatimPrefix
        let scaleChanged = context.coordinator.textScale != textScale
        guard contentChanged || scaleChanged else { return }
        let origin = scroll.contentView.bounds.origin
        let viewport = scaleChanged && context.coordinator.renderedRich == rich &&
            context.coordinator.verbatimPrefix == verbatimPrefix
            ? NativeTextViewport(view: view, scroll: scroll) : nil
        let atBottom = view.bounds.height - scroll.contentView.bounds.maxY <= 28
        let continuing = !context.coordinator.source.isEmpty && text.hasPrefix(context.coordinator.source)
        if !contentChanged {
            textScale.apply(to: storage, replacing: context.coordinator.textScale)
        } else if continuing && !rich && !context.coordinator.renderedRich {
            textScale.apply(to: storage, replacing: context.coordinator.textScale)
            let suffix = String(text.dropFirst(context.coordinator.source.count))
            if !suffix.isEmpty { storage.append(scaled(Self.plain(suffix))) }
        } else {
            if rich, let verbatimPrefix, text.hasPrefix(verbatimPrefix) {
                let content = NSMutableAttributedString(attributedString: Self.plain(verbatimPrefix))
                content.append(Self.styled(String(text.dropFirst(verbatimPrefix.count))))
                storage.setAttributedString(scaled(content))
            } else {
                storage.setAttributedString(scaled(rich ? Self.styled(text) : Self.plain(text)))
            }
        }
        context.coordinator.source = text
        context.coordinator.renderedRich = rich
        context.coordinator.verbatimPrefix = verbatimPrefix
        context.coordinator.textScale = textScale
        let location = min(selected.location, storage.length)
        let preservedSelection = NSRange(location: location, length: min(selected.length, storage.length - location))
        if !NSEqualRanges(view.selectedRange(), preservedSelection) {
            view.setSelectedRange(preservedSelection)
        }
        if let container = view.textContainer { view.layoutManager?.ensureLayout(for: container) }
        if continuing && atBottom && selected.length == 0 {
            view.scrollRangeToVisible(NSRange(location: storage.length, length: 0))
        } else if continuing {
            if let viewport {
                viewport.restore(view: view, scroll: scroll)
            } else {
                scroll.contentView.scroll(to: origin)
                scroll.reflectScrolledClipView(scroll.contentView)
            }
        } else {
            scroll.contentView.scroll(to: .zero)
            scroll.reflectScrolledClipView(scroll.contentView)
        }
    }

    private func scaled(_ content: NSAttributedString) -> NSAttributedString {
        guard textScale != .standard else { return content }
        let result = NSMutableAttributedString(attributedString: content)
        textScale.apply(to: result)
        return result
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
