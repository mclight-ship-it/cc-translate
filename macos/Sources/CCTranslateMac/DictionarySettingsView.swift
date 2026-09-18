import AppKit
import SwiftUI

@MainActor
struct DictionarySettingsSection: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var dictionary: DictionaryModel
    @State private var confirmDelete = false

    var body: some View {
        Section {
            HStack {
                Label(model.text("Local dictionary", "本地词典"), systemImage: "books.vertical")
                    .font(.headline)
                Spacer()
                Text(stateLabel).font(.callout).foregroundStyle(.secondary)
            }
            Text(model.text("Installed, enabled dictionary entries appear before any CLI requirement, with full senses, pronunciation, and source attribution. Local hits never request AI automatically. Retranslate and result actions use your selected service only when you choose them.",
                            "已安装并启用的词典会先于 CLI 查询，显示完整释义、发音和来源标注。本地命中后不会自动请求 AI；只有主动选择重译或结果操作时才会使用所选服务。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let status = dictionary.status {
                if status.state == .ready {
                    Toggle(model.text("Use local dictionary first", "优先使用本地词典"), isOn: Binding(
                        get: { status.enabled }, set: { model.setDictionaryEnabled($0) }
                    ))
                    .disabled(!model.settingsReady || model.settingsBusy || dictionary.busy || dictionary.phase == .unknown)
                    Text(model.text("\(status.entryCount.formatted()) entries · Data \(status.dataVersion)",
                                    "\(status.entryCount.formatted()) 条词条 · 数据版本 \(status.dataVersion)"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(model.text("Download size: \(ByteCountFormatter.string(fromByteCount: status.size, countStyle: .file)). The bundled core verifies the pinned SHA-256, version, and schema before enabling it.",
                                "下载大小：\(ByteCountFormatter.string(fromByteCount: status.size, countStyle: .file))。内置核心会校验固定的 SHA-256、版本和格式，验证成功后才会启用。"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if dictionary.phase == .downloading {
                ProgressView(value: Double(dictionary.received), total: Double(max(1, dictionary.expected)))
                    .accessibilityLabel(model.text("Dictionary download", "词典下载"))
                Text(model.text("\(ByteCountFormatter.string(fromByteCount: dictionary.received, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: dictionary.expected, countStyle: .file))",
                                "\(ByteCountFormatter.string(fromByteCount: dictionary.received, countStyle: .file)) / \(ByteCountFormatter.string(fromByteCount: dictionary.expected, countStyle: .file))"))
                    .font(.caption).monospacedDigit().foregroundStyle(.secondary)
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
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
            Text(model.text("Disabling keeps the file. Delete removes only the installed dictionary, not translation history. Downloads start only when you choose Download; no account is needed.",
                            "禁用会保留文件。删除仅移除已安装的词典，不会删除翻译历史记录。只有点击“下载”才会联网，无需账号。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(model.text("Sources & notices…", "来源与声明…")) {
                    openBundled("THIRD_PARTY_NOTICES")
                }
                Button(model.text("Dictionary licenses…", "词典许可证…")) {
                    openBundled("dictionary")
                }
            }
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

    @ViewBuilder
    private var controls: some View {
        Button(model.text("Refresh status", "刷新状态")) { model.refreshDictionary() }
            .disabled(dictionary.busy || model.settingsBusy)
        if dictionary.canCancel {
            Button(model.text("Cancel download", "取消下载")) { dictionary.cancel() }
        } else if dictionary.status?.state != .ready {
            Button(model.text("Download dictionary", "下载词典")) { dictionary.download() }
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
