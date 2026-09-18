import AppKit
import ServiceManagement
import SwiftUI

enum LoginItemStatus: Equatable {
    case notChecked, notRegistered, enabled, requiresApproval, notFound, unknown
}

@MainActor
protocol LoginItemServing: AnyObject {
    func readStatus() -> LoginItemStatus
    func register() throws
    func unregister() async throws
    func openSystemSettings()
}

@MainActor
final class SystemLoginItemService: LoginItemServing {
    func readStatus() -> LoginItemStatus {
        switch SMAppService.mainApp.status {
        case .notRegistered: return .notRegistered
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notFound: return .notFound
        @unknown default: return .unknown
        }
    }

    func register() throws { try SMAppService.mainApp.register() }
    func unregister() async throws { try await SMAppService.mainApp.unregister() }
    func openSystemSettings() { SMAppService.openSystemSettingsLoginItems() }
}

@MainActor
final class LoginItemModel: ObservableObject {
    enum Issue: Equatable {
        case operationFailed(Int), notConfirmed
    }

    @Published private(set) var status: LoginItemStatus = .notChecked
    @Published private(set) var busy = false
    @Published private(set) var issue: Issue?
    private let service: any LoginItemServing

    init(service: (any LoginItemServing)? = nil) {
        self.service = service ?? SystemLoginItemService()
    }

    var enabled: Bool { status == .enabled }

    func refresh() {
        guard !busy else { return }
        status = service.readStatus()
    }

    func openSystemSettings() {
        service.openSystemSettings()
    }

    func setEnabled(_ enabled: Bool) {
        guard !busy else { return }
        issue = nil
        refresh()
        if enabled && status == .requiresApproval {
            // Registration already exists. Only the user can approve it in System Settings.
            openSystemSettings()
            return
        }
        if (enabled && status == .enabled) || (!enabled && status == .notRegistered) { return }
        busy = true
        Task {
            do {
                if enabled { try service.register() }
                else { try await service.unregister() }
                status = service.readStatus()
                let confirmed = enabled
                    ? status == .enabled || status == .requiresApproval
                    : status == .notRegistered
                if !confirmed { issue = .notConfirmed }
            } catch {
                status = service.readStatus()
                issue = .operationFailed((error as NSError).code)
            }
            busy = false
        }
    }
}

@MainActor
struct LoginItemSettingsView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var loginItems: LoginItemModel

    var body: some View {
        Toggle(model.text("Launch at login", "登录时启动"), isOn: Binding(
            get: { loginItems.enabled }, set: { loginItems.setEnabled($0) }))
            .toggleStyle(.checkbox)
            .disabled(loginItems.busy)
            .accessibilityIdentifier("launch-at-login")
            .accessibilityHint(statusMessage)
        Text(statusMessage)
            .font(.callout)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("login-item-status")
        if loginItems.busy {
            ProgressView().controlSize(.small)
                .accessibilityLabel(model.text("Updating login item", "正在更新登录项"))
        }
        if let issue = loginItems.issue {
            Text(issueMessage(issue))
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("login-item-error")
        }
        HStack {
            Button(model.text("Refresh status", "刷新状态")) { loginItems.refresh() }
                .accessibilityIdentifier("refresh-login-item")
            Button(model.text("Open Login Items…", "打开登录项设置…")) { loginItems.openSystemSettings() }
                .accessibilityIdentifier("open-login-items")
        }
        .disabled(loginItems.busy)
        if loginItems.status == .requiresApproval {
            Button(model.text("Remove pending login item", "移除待批准的登录项")) {
                loginItems.setEnabled(false)
            }
            .disabled(loginItems.busy)
            .accessibilityIdentifier("remove-pending-login-item")
        }
        Text(model.text("macOS manages this setting. It starts CC Translate in the menu bar, not a translation or CLI request. Restoring translation defaults does not change this system setting.",
                        "此设置由 macOS 管理。登录时只启动菜单栏中的 CC Translate，不发起翻译或 CLI 请求。恢复翻译默认设置不会改变此系统设置。"))
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var statusMessage: String {
        switch loginItems.status {
        case .notChecked:
            return model.text("Login status has not been checked.", "尚未读取登录项状态。")
        case .notRegistered:
            return model.text("Off · This app is not registered to launch at login.", "已关闭 · 此应用尚未注册为登录项。")
        case .enabled:
            return model.text("On · macOS has enabled this app at login.", "已开启 · macOS 已允许此应用在登录时启动。")
        case .requiresApproval:
            return model.text("Not enabled · Allow CC Translate in System Settings > Login Items to finish.",
                              "尚未开启 · 请在系统设置的登录项中允许 CC Translate。")
        case .notFound:
            return model.text("macOS could not find this app's login item. Reopen the app from its installed location, then refresh.",
                              "macOS 未找到此应用的登录项。请从安装位置重新打开应用，再刷新状态。")
        case .unknown:
            return model.text("macOS returned an unknown login status. Check Login Items in System Settings.",
                              "macOS 返回了未知的登录项状态，请在系统设置中检查。")
        }
    }

    private func issueMessage(_ issue: LoginItemModel.Issue) -> String {
        switch issue {
        case .operationFailed(let code):
            return model.text("The login item could not be changed (system error \(code)). Check System Settings; no automatic retry was made.",
                              "未能更改登录项（系统错误 \(code)）。请检查系统设置，应用未自动重试。")
        case .notConfirmed:
            return model.text("macOS has not confirmed the requested change. The actual system status is shown.",
                              "macOS 尚未确认此次更改，当前显示的是系统实际状态。")
        }
    }
}
