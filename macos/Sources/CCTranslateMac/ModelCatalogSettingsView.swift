import SwiftUI

@MainActor
struct ModelCatalogSettingsView: View {
    @ObservedObject var model: ProbeModel

    private var refreshTitle: String {
        if case .failed = model.modelCatalog.phase { return model.text("Retry model refresh", "重试模型刷新") }
        return model.text("Refresh models", "刷新模型")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button(refreshTitle) { model.refreshModels() }
                    .disabled(model.modelCatalog.busy || model.catalogShutDown)
                    .accessibilityIdentifier("refresh-codex-models")
                    .help(model.text("Load models from your Codex installation. You can also enter a custom model ID.",
                                     "从已安装的 Codex 加载模型，也可以直接填写自定义模型 ID。"))
                if model.modelCatalog.busy {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(model.text("Loading models", "正在加载模型"))
                    Button(model.text("Cancel refresh", "取消刷新")) { model.cancelModelCatalog() }
                        .disabled(model.modelCatalog.phase == .cancelling)
                }
            }
            status
            if let row = model.discoveredModel(model.modelProfile), !row.description.isEmpty {
                NativeSettingsDisclosure(model.text("Model details", "模型详情"),
                                         model: model, identifier: "model-catalog-details") {
                    Text(verbatim: row.description).font(.caption).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private var status: some View {
        switch model.modelCatalog.phase {
        case .idle: EmptyView()
        case .waiting:
            Text(model.text("Waiting for current work…", "正在等待当前操作完成…")).font(.caption)
        case .connecting:
            Text(model.text("Preparing Codex…", "正在准备 Codex…")).font(.caption)
        case .loading:
            Text(model.text("Loading models…", "正在加载模型…")).font(.caption)
        case .cancelling:
            Text(model.text("Stopping refresh…", "正在停止刷新…")).font(.caption)
        case .cancelled:
            Text(model.text("Refresh cancelled.", "刷新已取消。")).font(.caption).foregroundStyle(.secondary)
        case .loaded:
            Text(model.modelCatalog.models.isEmpty
                 ? model.text("No models returned. Enter an ID or refresh again.", "未返回模型。请直接输入 ID，或再次刷新。")
                 : model.text("\(model.modelCatalog.models.count) models loaded.",
                              "已加载 \(model.modelCatalog.models.count) 个模型。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        case .failed(let failure):
            Label(message(failure), systemImage: "exclamationmark.triangle")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func message(_ failure: ModelCatalogState.Failure) -> String {
        switch failure {
        case .missingCLI: return model.text("Choose a Codex installation below, then refresh.", "请在下方选择 Codex 安装路径，然后刷新。")
        case .unavailable: return model.text("This Codex connection cannot list models. You can still enter an ID.",
                                             "此 Codex 连接无法列出模型，你仍可直接输入 ID。")
        case .discovery, .tooLarge: return model.text("Couldn't load the model list. Try again or enter an ID.",
                                                     "无法加载模型列表。请重试，或直接输入 ID。")
        case .connection: return model.text("The Codex connection is unavailable. Refresh to reconnect.",
                                             "Codex 连接不可用，请刷新以重新连接。")
        case .cleanup: return model.text("Codex couldn't stop the refresh cleanly. Try again.",
                                         "Codex 未能正常结束刷新，请重试。")
        case .cliChanged: return model.text("Codex installation changed. Refresh its model list.",
                                             "Codex 安装路径已更改，请刷新模型列表。")
        }
    }
}
