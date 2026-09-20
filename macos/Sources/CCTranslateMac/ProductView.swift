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
                model.text("\(text.unicodeScalars.count) / \($0) characters",
                           "\(text.unicodeScalars.count) / \($0) 字符")
            } ?? model.text("\(text.unicodeScalars.count) characters",
                            "\(text.unicodeScalars.count) 字符"))
                .accessibilityIdentifier("input-code-point-count")
                .help(model.text("Counts Unicode code points. \(text.utf8.count) of 8,192 UTF-8 bytes.",
                                 "按 Unicode 码点计数。已使用 \(text.utf8.count) / 8,192 个 UTF-8 字节。"))
            if text.utf8.count > 8192 {
                Text(model.text("\(text.utf8.count) / 8,192 UTF-8 bytes",
                                "\(text.utf8.count) / 8,192 UTF-8 字节"))
                    .accessibilityIdentifier("input-byte-count")
            }
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
                    Text(model.text("The local dictionary works offline. Choose \(model.translationProvider.displayName) for model translation.",
                                    "本地词典可离线使用。模型翻译需要选择 \(model.translationProvider.displayName)。"))
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
            Label(model.translationProvider.displayName, systemImage: "sparkle")
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
                Text(model.text("Translates with your \(model.translationProvider.displayName) account.",
                                "使用你的 \(model.translationProvider.displayName) 账号翻译。"))
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

private struct StatusContentHeight: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

@MainActor
struct ContentSizedStatusScrollView<Content: View>: View {
    let maximumHeight: CGFloat
    @ViewBuilder var content: () -> Content
    @State private var contentHeight: CGFloat = 20

    var body: some View {
        ScrollView {
            content()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background {
                    GeometryReader { geometry in
                        Color.clear.preference(key: StatusContentHeight.self, value: geometry.size.height)
                    }
                }
        }
        .frame(height: min(contentHeight, maximumHeight))
        .onPreferenceChange(StatusContentHeight.self) { contentHeight = $0.rounded(.up) }
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
            NativeResultText(text: model.output, formatted: formatted, streaming: busy,
                             label: model.text("Translation result", "翻译结果"),
                             verbatimPrefix: model.isLocalDictionaryResult ? model.primaryResult : nil,
                             textScale: model.nativeTextScale)
            .frame(minHeight: compact ? 0 : 180, maxHeight: .infinity)
            .overlay {
                if model.output.isEmpty {
                    GeometryReader { viewport in
                        ScrollView {
                            emptyState.frame(minHeight: viewport.size.height)
                        }
                    }
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
            ContentSizedStatusScrollView(maximumHeight: compact ? 90 : 140) {
                phaseStatus
            }
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
        .frame(maxWidth: .infinity)
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
                .font(model.productPhase == .failed ? .callout : .caption)
                .foregroundStyle(model.productPhase == .failed ? .primary : .secondary)
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
        .fixedSize(horizontal: true, vertical: false)
        .disabled(model.output.isEmpty)
        .help(model.text("Copy the current result as plain text", "以纯文本复制当前结果"))
        Button(model.text("Copy bilingual", "复制双语")) { model.copyBilingual() }
            .fixedSize(horizontal: true, vertical: false)
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
                .fixedSize(horizontal: true, vertical: false)
                .keyboardShortcut(".", modifiers: .command)
        } else {
            Button {
                model.input = model.resultInput
                model.translate(origin: model.translationOrigin, useCache: false)
            } label: {
                Label(model.text("Retranslate", "重新翻译"), systemImage: "arrow.clockwise")
            }
            .fixedSize(horizontal: true, vertical: false)
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
struct CaptureShortcutSettingsSection: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var shortcut: CaptureShortcutModel

    var toggleTitle: String { model.text("Global screenshot shortcut", "全局截图快捷键") }
    var retryTitle: String {
        shortcut.enabled ? model.text("Retry shortcut registration", "重试快捷键注册")
            : model.text("Retry releasing shortcut", "重试释放快捷键")
    }

    var body: some View {
        Section {
            Toggle(toggleTitle, isOn: Binding(get: { shortcut.enabled }, set: { shortcut.choose($0) }))
                .toggleStyle(.checkbox)
                .disabled(shortcut.isShutDown || shortcut.registration == .registering)
                .accessibilityIdentifier("screenshot-shortcut")
                .accessibilityHint(status)
            Text(model.text("Press ⌘⌥⇧X to capture a region. macOS asks for screen recording permission when needed.",
                            "按 ⌘⌥⇧X 截取区域，需要时由 macOS 请求屏幕录制权限。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Label(status, systemImage: statusIcon)
                .font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            if shortcut.canRetry {
                Button(retryTitle) { shortcut.retry() }
                    .accessibilityIdentifier("retry-screenshot-shortcut")
            }
            Text(model.text("Review the capture before choosing what to translate.",
                            "截图后先预览，再选择要翻译的内容。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("Screenshot shortcut", "截图快捷键"))
        }
    }

    private var statusIcon: String {
        switch shortcut.registration {
        case .registered: return "checkmark.circle"
        case .failed: return "exclamationmark.triangle"
        case .off, .registering: return "keyboard"
        }
    }

    private var status: String {
        switch shortcut.registration {
        case .off:
            return shortcut.enabled
                ? model.text("The shortcut is requested but not registered. Retry to enable it.",
                             "已选择开启，但快捷键尚未注册。请重试以启用。")
                : model.text("Global screenshot shortcut is off.", "全局截图快捷键已关闭。")
        case .registering:
            return model.text("Reserving the screenshot shortcut…", "正在注册截图快捷键…")
        case .registered:
            return model.text("Ready: ⌘⌥⇧X. Finish the current region selection or recognition before starting another capture.",
                              "已就绪：⌘⌥⇧X。请先完成当前区域选择或识别，再开始下一次截图。")
        case .failed(.conflict):
            return model.text("The shortcut is already reserved by another app. Release it there, then retry. No alternate shortcut was selected.",
                              "快捷键已被其他应用占用。请先在该应用中释放，再重试。未自动选择其他快捷键。")
        case .failed(.unavailable(let code)):
            return model.text("Could not register the shortcut (system code \(code)). Capture has not started.",
                              "无法注册快捷键（系统代码 \(code)），未开始截图。")
        case .failed(.releaseFailed(let code)):
            return model.text("Could not release the shortcut (system code \(code)). Its capture action is disabled. Retry release if available, or quit the app to release its resources.",
                              "无法释放快捷键（系统代码 \(code)）。该快捷键已不能触发截图。可用时请重试释放，或退出应用以释放资源。")
        }
    }
}

@MainActor
struct CopyIntervalSettingsView: View {
    @ObservedObject var model: ProbeModel
    private var preference: NumericPreference<Double> { model.copyInterval }

    var applyTitle: String { model.text("Apply interval", "应用间隔") }
    var reloadTitle: String { model.text("Reload saved interval", "重新读取已保存间隔") }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(model.text("Active double ⌘C interval: \(model.activeCopyInterval.seconds) seconds",
                            "当前双击 ⌘C 间隔：\(model.activeCopyInterval.seconds) 秒"))
                .textSelection(.enabled)
            HStack(alignment: .firstTextBaseline) {
                TextField(model.text("Interval in seconds", "间隔（秒）"), text: Binding(
                    get: { preference.draft }, set: { model.editCopyInterval($0) }
                ))
                .textFieldStyle(.roundedBorder).frame(maxWidth: 220)
                .disabled(!model.canEditCopyInterval)
                .accessibilityIdentifier("copy-interval-input")
                .accessibilityLabel(model.text("Double Command C interval in seconds", "双击 Command C 间隔（秒）"))
                Button(applyTitle) { model.saveCopyInterval() }
                    .disabled(!model.canEditCopyInterval || preference.proposed == nil ||
                              preference.proposed == preference.saved)
                    .accessibilityIdentifier("apply-copy-interval")
            }
            Text(model.text("Try 0.75 seconds if you prefer a slower double press.",
                            "如果双击较慢，可以设为 0.75 秒。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let saved = preference.saved, !preference.supported.contains(saved) {
                Label(model.text("Saved interval \(saved) is invalid. The active interval above is unchanged; enter a positive value to correct the saved setting.",
                                 "已保存间隔 \(saved) 无效。仍使用上方显示的当前间隔，请输入正数并保存以纠正。"),
                      systemImage: "exclamationmark.triangle")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            if !message.isEmpty {
                Text(message).font(.callout).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if case .failed = preference.phase {
                Button(reloadTitle) { model.reloadCopyInterval() }
                    .disabled(!model.canReloadCopyInterval)
                    .accessibilityIdentifier("reload-copy-interval")
            }
        }
    }

    private var message: String {
        switch preference.phase {
        case .idle:
            return preference.saved == nil ? model.text("Saved interval: Not confirmed", "已保存间隔：尚未确认") : ""
        case .invalidInput:
            return model.text("Enter a positive number no greater than \(ConfigurationDocument.maxNumber). Nothing was saved.",
                              "请输入不超过 \(ConfigurationDocument.maxNumber) 的正数，尚未保存。")
        case .saving: return model.text("Saving interval…", "正在保存间隔…")
        case .readingBack: return model.text("Confirming interval…", "正在确认间隔…")
        case .saved: return model.text("Interval saved and applied.", "间隔已保存并应用。")
        case .differentReadback:
            return model.text("The saved interval differs from your entry. The current active interval is shown above; your entry is retained. No write was retried.",
                              "已保存间隔与你的输入不同。上方显示当前实际使用的间隔，并保留你的输入，未重试写入。")
        case .failed(let code):
            return model.text("Could not confirm the saved interval (\(code)). The active interval is unchanged. Reload to check; no write was retried.",
                              "无法确认已保存间隔（\(code)）。当前间隔未变，请重新读取以核对，未重试写入。")
        }
    }
}

@MainActor
struct InputLimitSettingsView: View {
    @ObservedObject var model: ProbeModel
    private var preference: IntegerPreference { model.inputLimit }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(preference.saved.map {
                model.text("Current limit: \($0) characters", "当前上限：\($0) 字符")
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
            Text(model.text("Enter a positive whole number. Long text may also reach the translation size limit.",
                            "请输入正整数。较长的文字还可能达到翻译容量上限。"))
                .font(.caption).foregroundStyle(.secondary)
            if let saved = preference.saved, !preference.supported.contains(saved) {
                Label(model.text("The saved value is invalid for text translation. It has not been changed; enter a positive value to correct it.",
                                 "已保存的值不适用于文字翻译，尚未改动。请输入正整数并明确保存以纠正。"),
                      systemImage: "exclamationmark.triangle")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            DisclosureGroup(model.text("How text length is counted", "字数如何计算")) {
                Text(model.text("Spaces, line breaks and combining marks count separately. Text must also fit 8,192 UTF-8 bytes and is never shortened automatically. The largest setting is \(ConfigurationDocument.maxNumber).",
                                "空格、换行和组合标记均计入。文字还须满足 8,192 个 UTF-8 字节上限，不会自动截短。设置最大值为 \(ConfigurationDocument.maxNumber)。"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
            return model.text("Enter a positive whole number no larger than \(ConfigurationDocument.maxNumber). Nothing was saved.",
                              "请输入不超过 \(ConfigurationDocument.maxNumber) 的正整数，尚未保存。")
        case .saving:
            return model.text("Saving input limit…", "正在保存输入上限…")
        case .readingBack:
            return model.text("Confirming input limit…", "正在确认输入上限…")
        case .saved:
            return model.text("Input limit saved.", "输入上限已保存。")
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
            Text(model.text("Older records are removed when the next translation is saved. Increasing this limit does not restore deleted records.",
                            "保存下一条翻译记录时会移除超出条数的旧记录。提高条数不会恢复已删除的记录。"))
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
        case .saving: return model.text("Saving history limit…", "正在保存条数…")
        case .readingBack: return model.text("Confirming history limit…", "正在确认条数…")
        case .saved: return model.text("History limit saved. Existing records are unchanged.",
                                      "条数已保存，现有记录未删除。")
        case .differentReadback: return model.text("The saved limit differs from your entry. The actual saved value is shown above; your entry is retained. No write was retried.",
                                                  "已保存条数与你的输入不同。上方显示实际回读值，并保留你的输入，未重试写入。")
        case .failed(let code): return model.text("History limit could not be confirmed (\(code)). Reload the saved setting; no write was retried.",
                                                  "无法确认条数（\(code)）。请重新读取已保存设置，未重试写入。")
        }
    }
}

@MainActor
struct AppUpdateSettingsView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var updates: AppUpdateModel

    var body: some View {
        Text(statusMessage)
            .font(.callout)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("software-update-status")
        if updates.sessionInProgress {
            ProgressView().controlSize(.small)
                .accessibilityLabel(model.text("Update in progress", "正在处理更新"))
        }
        if let issue = updates.issue, issue != .channelUnavailable {
            Text(issueMessage(issue))
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("software-update-error")
        }
        HStack {
            Button(model.text("Check for Updates…", "检查更新…")) { updates.check() }
                .disabled(!updates.canCheck)
                .accessibilityIdentifier("check-software-update")
            Button(model.text("View verified downloads", "查看已验证下载")) { updates.showDownloads() }
                .disabled(!updates.canOpenDownloads)
                .accessibilityIdentifier("open-verified-downloads")
        }
        Text(model.text("Updates are checked only when you choose to.",
                        "只在你主动检查时查找更新。"))
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var statusMessage: String {
        switch updates.channel {
        case .unconfigured:
            return model.text("This development build uses verified downloads. An automatic update channel has not been published yet.",
                              "此开发包使用已验证的下载，自动更新渠道尚未发布。")
        case .invalid:
            return model.text("This build's update settings are incomplete. Use a verified download instead.",
                              "此构建的更新配置不完整，请使用已验证的下载。")
        case .configured:
            return updates.sessionInProgress
                ? model.text("An update is in progress. Continue in the update window.", "正在处理更新，请在更新窗口中继续。")
                : model.text("Check for a new version. The update window guides you through downloading and installing.",
                             "检查新版本，随后可在更新窗口中下载和安装。")
        }
    }

    private func issueMessage(_ issue: AppUpdateModel.Issue) -> String {
        switch issue {
        case .channelUnavailable:
            return statusMessage
        case .notReady:
            return model.text("An update is already being processed. Follow the update window, then try again.",
                              "已有更新正在处理，请在更新窗口中完成后再试。")
        case .failed(let code):
            return model.text("The update check could not start (error \(code)). Try again or use a verified download.",
                              "未能开始检查更新（错误 \(code)）。请重试或使用已验证的下载。")
        case .downloadsUnavailable:
            return model.text("The downloads page could not be opened. Check your default browser and try again.",
                              "无法打开下载页面，请检查默认浏览器后重试。")
        case .terminating:
            return model.text("CC Translate is closing. Reopen the app before checking for updates.",
                              "CC Translate 正在退出，请重新打开应用后再检查更新。")
        }
    }
}

@MainActor
struct AppUpdatePanelView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var updates: AppUpdateModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(model.text("Software updates", "软件更新")).font(.headline)
                AppUpdateSettingsView(model: model, updates: updates)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

enum SettingsPane: String, CaseIterable {
    case translation, shortcuts, appearance, more

    @MainActor
    func title(using model: ProbeModel) -> String {
        switch self {
        case .translation: return model.text("Translation", "翻译")
        case .shortcuts: return model.text("Shortcuts", "快捷键")
        case .appearance: return model.text("Appearance", "外观")
        case .more: return model.text("More", "更多")
        }
    }
}

@MainActor
struct TranslationSettingsView: View {
    @ObservedObject var model: ProbeModel
    var showDiagnostics: () -> Void
    var showAbout: () -> Void
    var loginItems: LoginItemModel? = nil
    var updates: AppUpdateModel? = nil
    @State var pane: SettingsPane = .translation
    @State private var installationExpanded = false

    init(model: ProbeModel, showDiagnostics: @escaping () -> Void, showAbout: @escaping () -> Void,
         loginItems: LoginItemModel? = nil, updates: AppUpdateModel? = nil,
         pane: SettingsPane = .translation) {
        self.model = model
        self.showDiagnostics = showDiagnostics
        self.showAbout = showAbout
        self.loginItems = loginItems
        self.updates = updates
        _pane = State(initialValue: pane)
        _installationExpanded = State(initialValue: model.selectedCLI.isEmpty)
    }

    private var busy: Bool { model.active || model.preparing }

    var body: some View {
        VStack(spacing: 0) {
            Picker(model.text("Settings category", "设置分类"), selection: $pane) {
                ForEach(SettingsPane.allCases, id: \.self) { category in
                    Text(category.title(using: model)).tag(category)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .accessibilityIdentifier("settings-category")
            .padding(16)
            Divider()
            settingsForm
        }
        .disabled(model.defaultsPhase.busy)
        .frame(minWidth: 530, minHeight: 460)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            model.refreshDictionary()
            installationExpanded = model.selectedCLI.isEmpty
        }
        .onChange(of: model.interfaceLanguage) { _, _ in model.persistPresentation() }
        .onChange(of: model.appearance) { _, _ in model.persistPresentation() }
        .onChange(of: model.selectedCLI) { _, path in
            if path.isEmpty { installationExpanded = true }
        }
    }

    private var settingsForm: some View {
        Form {
            if model.productPhase == .failed && !model.productMessage.isEmpty {
                Section {
                    Label(model.productMessage, systemImage: "exclamationmark.circle")
                        .font(.callout).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                } header: {
                    Text(model.text("Needs attention", "需要处理"))
                }
            }
            switch pane {
            case .translation:
                translationSection
                providerSection
                DictionarySettingsSection(model: model, dictionary: model.dictionary)
            case .shortcuts:
                shortcutSection
                CaptureShortcutSettingsSection(model: model, shortcut: model.captureShortcut)
                PlainPasteSettingsSection(model: model, paste: model.plainPaste)
            case .appearance:
                generalSection
                if let loginItems {
                    Section {
                        LoginItemSettingsView(model: model, loginItems: loginItems)
                    } header: {
                        Text(model.text("Startup", "启动"))
                    }
                }
            case .more:
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
                if let updates {
                    Section {
                        AppUpdateSettingsView(model: model, updates: updates)
                    } header: {
                        Text(model.text("Software updates", "软件更新"))
                    }
                }
                aboutSection
                Section {
                    SettingsDefaultsView(model: model)
                } header: {
                    Text(model.text("Restore settings", "恢复设置"))
                }
            }
        }
        .formStyle(.grouped)
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
            NativeResultPlacementPicker(model: model)
        } header: {
            Text(model.text("General", "通用"))
        }
    }

    var translationSection: some View {
        Section {
            Picker(model.text("Translation service", "翻译服务"), selection: Binding(
                get: { model.pendingProvider ?? model.translationProvider },
                set: { model.selectTranslationProvider($0) }
            )) {
                ForEach(TranslationProvider.allCases, id: \.self) { provider in
                    Text(provider.displayName).tag(provider)
                }
            }
            .disabled(!model.canChangeProvider)
            .accessibilityIdentifier("translation-service")
            if !model.providerMessage.isEmpty {
                Text(model.providerMessage).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
            .help(model.text("For long prose, show a brief summary before the full translation.",
                             "翻译长文时，在完整译文前显示简短摘要。"))
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
            Text(model.text("History stays on this Mac. Turning it off stops an active translation and keeps saved records.",
                            "历史记录仅保存在本机。关闭保存会停止正在进行的翻译，已有记录仍保留。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("Translation", "翻译"))
        }
    }

    private var providerSection: some View {
        Section {
            DisclosureGroup(model.text("\(model.translationProvider.displayName) installation",
                                       "\(model.translationProvider.displayName) 安装位置"),
                            isExpanded: $installationExpanded) {
                providerControls
            }
            .accessibilityIdentifier("provider-installation-details")
        }
    }

    private var providerControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.cliChangeDeferred {
                Label(model.text("The selected service will apply after the dictionary operation finishes.",
                                 "词典操作完成后会应用所选服务。"),
                      systemImage: "clock")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(model.selectedCLI.isEmpty ?
                 model.text("Choose your \(model.translationProvider.displayName) installation to use AI translation.",
                            "选择已安装的 \(model.translationProvider.displayName)，即可使用 AI 翻译。") :
                 model.text("\(model.translationProvider.displayName) installation selected",
                            "已选择 \(model.translationProvider.displayName) 安装位置"))
                .font(.callout)
            HStack {
                Button(model.text("Detect automatically", "自动查找")) { model.locateCLI() }
                Button(model.text("Choose…", "选择…")) { model.chooseCLI() }
            }
            .disabled(model.cliBusy || busy)
            Text(model.selectedCLI.isEmpty ? model.text("Not selected", "尚未选择") : model.selectedCLI)
                .font(.caption.monospaced()).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if !model.candidates.filter(\.executable).isEmpty {
                Picker(model.text("Detected executables", "找到的可执行文件"), selection: $model.selectedCLI) {
                    ForEach(model.candidates.filter(\.executable)) { candidate in
                        Text(candidate.url.path).tag(candidate.url.path)
                    }
                }
                .disabled(model.cliBusy || busy)
                .onChange(of: model.selectedCLI) { _, _ in model.persistPresentation() }
            }
            DisclosureGroup(model.text("Version check", "版本检查")) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Button(model.text("Check version", "检查版本")) { model.versionCLI() }
                            .disabled(model.cliBusy || model.selectedCLI.isEmpty || busy)
                        if model.cliBusy {
                            ProgressView().controlSize(.small)
                            Button(model.text("Cancel check", "取消检查")) { model.cancelCLI() }
                        }
                    }
                    Text(model.text("Version checking is optional and does not check your account.",
                                    "版本检查是可选操作，不检查账号。"))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(model.cliStatus).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
            }
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
            Text(model.text("Select text in another app, then press ⌘C twice to translate it.",
                            "在其他应用中选中文字，连续按两次 ⌘C 即可翻译。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            DisclosureGroup(model.text("Double-copy timing", "双击间隔")) {
                CopyIntervalSettingsView(model: model)
            }
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
            Text(model.text("Allow Accessibility and Input Monitoring if prompted. Restart the app if a permission change hasn't taken effect.",
                            "按提示允许辅助功能和输入监控。权限更改未生效时，请重启应用。"))
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
            Button(model.text("Open diagnostics…", "打开诊断…"), action: showDiagnostics)
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
                    .disabled(!model.modelSettings.isPreset(profile.value) &&
                              model.modelSettings.validateCustom(profile.value) != nil)
            }
        }
        .accessibilityLabel(model.text("\(model.translationProvider.displayName) model profile",
                                       "\(model.translationProvider.displayName) 模型配置"))
        .accessibilityValue(label(selection))
        .help(label(selection) + model.text(" · Choose a model or enter an ID in Settings.", " · 在设置中选择模型或输入 ID。"))
        .disabled(model.active || model.preparing || model.settingsBusy || model.dictionary.committing)
    }

    private func label(_ value: String) -> String {
        if model.translationProvider == .claude {
            switch value {
            case "haiku": return "Haiku"
            case "sonnet": return "Sonnet"
            case "opus": return "Opus"
            default: return value.isEmpty ? model.text("Empty model ID", "模型 ID 为空") : value
            }
        }
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
    @State private var customExpanded = false

    init(model: ProbeModel) {
        self.model = model
        _customExpanded = State(initialValue: model.modelSettings.draftEdited)
    }

    private var validation: CodexModelSettings.Validation? {
        model.modelSettings.validateCustom(model.modelSettings.draft)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ModelPicker(model: model, selection: Binding(
                get: { model.modelProfile }, set: { model.applyModelProfile($0) }))
                .disabled(!model.canApplyModelSetting)
            status
            if model.translationProvider == .codex {
                ModelCatalogSettingsView(model: model)
            }
            DisclosureGroup(model.text("Custom model", "自定义模型"), isExpanded: $customExpanded) {
                customEditor
            }
            .accessibilityIdentifier("custom-model-details")
            if !model.ready && !model.modelCatalog.busy {
                Text(model.text("Reopen Settings to reconnect. Your draft is kept.",
                                "重新打开设置即可重新连接，草稿会保留。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .onAppear { if model.modelSettings.draftEdited { customExpanded = true } }
        .onChange(of: model.modelSettings.draftEdited) { _, edited in
            if edited { customExpanded = true }
        }
    }

    private var customEditor: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.text("Custom model ID", "自定义模型 ID")).font(.subheadline)
            TextField(model.text("Enter the exact model ID", "输入完整的模型 ID"), text: Binding(
                get: { model.modelSettings.draft }, set: { model.editCustomModelID($0) }),
                onEditingChanged: { model.setCustomModelEditing($0) })
                .textFieldStyle(.roundedBorder)
                .disableAutocorrection(true)
                .accessibilityLabel(model.text("Custom model ID", "自定义模型 ID"))
                .accessibilityIdentifier("custom-\(model.translationProvider.cliName)-model-id")
            HStack {
                Button(model.text("Apply model", "应用模型")) { model.applyCustomModelID() }
                    .disabled(!model.canApplyModelSetting || validation != nil)
                Button(model.text("Reset draft", "重置草稿")) { model.resetCustomModelDraft() }
                Spacer(minLength: 8)
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
            HStack(alignment: .top) {
                Text(model.text("Saved model:", "已保存模型：")).foregroundStyle(.secondary)
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
            Text(model.text(
                "Use the exact model ID from your provider. Available models depend on your account.",
                "请填写服务提供方的完整模型 ID，可用模型取决于你的账号。"))
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
            ProgressView(model.text("Confirming model…", "正在确认模型…")).controlSize(.small)
        case .applied(let profile):
            Label(model.text("Model saved: \(profile)", "模型已保存：\(profile)"),
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
        case .preset: return model.text("Choose this preset in the Model picker.",
                                       "请在模型选择器中选择此预设。")
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
