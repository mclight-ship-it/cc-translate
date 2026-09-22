import AppKit
import SwiftUI

@MainActor
struct DictionarySettingsSection: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var dictionary: DictionaryModel
    @State private var confirmDelete = false

    var body: some View {
        Section {
            content
        } header: {
            Text(model.text("Offline dictionary", "离线词典"))
        }
        .confirmationDialog(model.text("Delete the local dictionary?", "删除本地词典？"),
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button(model.text("Delete dictionary", "删除词典"), role: .destructive) { dictionary.delete() }
            Button(model.text("Cancel", "取消"), role: .cancel) {}
        } message: {
            Text(model.text("The dictionary will be disabled and its installed file removed. Saved translations stay intact. Using the dictionary again requires an explicit download.",
                            "词典将被禁用，其安装文件会被移除。已保存的翻译不会删除。再次使用词典需要手动下载。"))
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(model.text("Local dictionary", "本地词典"), systemImage: "books.vertical")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Text(stateLabel).font(.callout).foregroundStyle(PearlTheme.secondary)
            }
            Text(model.text("Dictionary lookups use the downloaded data on this Mac. They do not send an AI request.",
                            "词典查询使用此 Mac 上已下载的数据，不会发送 AI 请求。"))
                .font(.callout).foregroundStyle(PearlTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let status = dictionary.status {
                if status.state == .ready {
                    Toggle(model.text("Use local dictionary first", "优先使用本地词典"), isOn: Binding(
                        get: { status.enabled }, set: { model.setDictionaryEnabled($0) }
                    ))
                    .disabled(!model.settingsReady || model.settingsBusy || dictionary.busy || dictionary.phase == .unknown)
                }
                if status.state != .ready {
                    Text(model.text("Download: \(ByteCountFormatter.string(fromByteCount: status.size, countStyle: .file))",
                                    "下载大小：\(ByteCountFormatter.string(fromByteCount: status.size, countStyle: .file))"))
                        .font(.callout).foregroundStyle(PearlTheme.secondary)
                }
            }
            if dictionary.phase == .downloading {
                ProgressView(value: Double(dictionary.received), total: Double(max(1, dictionary.expected)))
                    .accessibilityLabel(model.text("Dictionary download", "词典下载"))
                Text(model.text("\(ByteCountFormatter.string(fromByteCount: dictionary.received, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: dictionary.expected, countStyle: .file))",
                                "\(ByteCountFormatter.string(fromByteCount: dictionary.received, countStyle: .file)) / \(ByteCountFormatter.string(fromByteCount: dictionary.expected, countStyle: .file))"))
                    .font(.callout).monospacedDigit().foregroundStyle(PearlTheme.secondary)
            } else if dictionary.busy {
                ProgressView().controlSize(.small)
                    .accessibilityLabel(model.text("Dictionary operation in progress", "词典操作进行中"))
            }
            ViewThatFits(in: .horizontal) {
                HStack { controls }
                VStack(alignment: .leading, spacing: 8) { controls }
            }
            if !dictionary.messageEnglish.isEmpty {
                Text(model.text(dictionary.messageEnglish, dictionary.messageChinese))
                    .font(.callout).foregroundStyle(PearlTheme.secondary)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
            DisclosureGroup(model.text("Dictionary information", "词库信息")) {
                if let status = dictionary.status, status.state == .ready {
                    Text(model.text("\(status.entryCount.formatted()) entries · Data \(status.dataVersion)",
                                    "\(status.entryCount.formatted()) 条词条 · 数据版本 \(status.dataVersion)"))
                        .font(.callout).foregroundStyle(PearlTheme.secondary)
                }
                HStack {
                    Button(model.text("Sources & notices…", "来源与声明…")) {
                        openBundled("THIRD_PARTY_NOTICES")
                    }
                    Button(model.text("Dictionary licenses…", "词典许可证…")) {
                        openBundled("dictionary")
                    }
                }
                .padding(PearlTheme.spacing)
                .foregroundStyle(PearlTheme.text)
                .tint(PearlTheme.accent)
                .pearlCard()
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        Button(model.text("Refresh status", "刷新状态")) { model.refreshDictionary() }
            .disabled(dictionary.busy || model.settingsBusy)
        if dictionary.canCancel {
            Button(model.text("Cancel download", "取消下载")) { dictionary.cancel() }
        } else if dictionary.status?.state != .ready {
            Button(model.text("Download dictionary", "下载词典")) { dictionary.download() }
                .buttonStyle(.bordered)
                .tint(PearlTheme.accent)
                .disabled(!model.settingsReady || model.settingsBusy || dictionary.busy || dictionary.phase == .unknown)
        }
        if let status = dictionary.status, status.state != .notInstalled {
            Button(model.text("Delete…", "删除…"), role: .destructive) { confirmDelete = true }
                .disabled(dictionary.busy || !model.settingsReady || model.settingsBusy || dictionary.phase == .unknown)
        }
    }

    private var stateLabel: String {
        if dictionary.phase == .unknown { return model.text("Outcome unknown", "结果未知") }
        guard let status = dictionary.status else { return model.text("Not checked", "尚未检查") }
        switch status.state {
        case .notInstalled: return model.text("Not installed", "未安装")
        case .invalid: return model.text("Needs replacement", "需要重新安装")
        case .ready: return status.enabled ? model.text("Enabled", "已启用") : model.text("Disabled", "已禁用")
        }
    }

    private func openBundled(_ name: String) {
        guard let resources = Bundle.main.resourceURL else {
            dictionary.report("Bundled notices could not be located.", "无法找到随包声明文件。")
            return
        }
        let url = resources.appendingPathComponent("Licenses").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path), NSWorkspace.shared.open(url) else {
            dictionary.report("Bundled notices could not be opened. Check the application package.",
                              "无法打开随包声明文件，请检查应用程序包。")
            return
        }
    }
}
