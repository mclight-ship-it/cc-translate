import SwiftUI

@MainActor
struct SettingsDefaultsView: View {
    @ObservedObject var model: ProbeModel
    private enum Focus: Hashable { case restore, cancel }
    @FocusState private var focused: Focus?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(model.text("Restore default settings…", "恢复默认设置…")) {
                model.prepareDefaultsRestore()
            }
            .disabled(!model.canRestoreDefaults)
            .focused($focused, equals: .restore)
            .accessibilityIdentifier("restore-default-settings")
            Text(model.text("Review before restoring. Your CLI installations, accounts, system permissions and saved data are kept.",
                            "确认后才恢复。保留 CLI 安装、账号、系统权限和已保存的数据。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if model.defaultsPhase == .confirming {
                Divider()
                Text(model.text("Restore default settings?", "恢复默认设置？"))
                    .font(.headline).accessibilityAddTraits(.isHeader)
                Text(model.defaultsConfirmationMessage).font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(model.text("Cancel", "取消")) { model.cancelDefaultsRestore() }
                        .focused($focused, equals: .cancel)
                        .accessibilityIdentifier("cancel-default-settings")
                    Button(model.text("Restore defaults", "恢复默认"), role: .destructive) {
                        model.confirmDefaultsRestore()
                    }
                    .disabled(!model.canConfirmDefaultsRestore)
                    .accessibilityIdentifier("confirm-default-settings")
                }
            }
            if !model.defaultsMessage.isEmpty {
                Text(model.defaultsMessage).font(.caption).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if needsReload {
                Button(model.text("Reload saved settings", "重新读取已保存设置")) { model.reloadDefaultsSettings() }
                    .disabled(!model.canReloadDefaults)
                    .accessibilityIdentifier("reload-default-settings")
            }
        }
        .onChange(of: model.defaultsPhase) { _, phase in
            if phase == .confirming { focused = .cancel }
            else if model.canRestoreDefaults { focused = .restore }
        }
        .onExitCommand {
            if model.defaultsPhase == .confirming { model.cancelDefaultsRestore() }
        }
    }

    private var needsReload: Bool {
        switch model.defaultsPhase {
        case .failed, .differentReadback: return true
        default: return false
        }
    }
}
