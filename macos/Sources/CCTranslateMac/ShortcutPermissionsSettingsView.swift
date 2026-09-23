import AppKit
import SwiftUI
import CCTranslateSupport

@MainActor
struct ShortcutPermissionsSettingsView: View {
    @ObservedObject var model: ProbeModel
    let accessibility: PermissionState?
    let inputMonitoring: PermissionState?
    let monitorState: SelectionMonitorState
    let allowAccessibility: () -> Void
    let allowInputMonitoring: () -> Void
    let refresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(shortcutStatus, systemImage: monitorState == .active ? "checkmark.circle" : "keyboard")
                .font(.body.weight(.medium))
                .foregroundStyle(PearlTheme.text)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("selection-shortcut-status")
            permissionRow(model.text("Accessibility", "辅助功能"), state: accessibility,
                          identifier: "selection-accessibility", action: allowAccessibility)
            permissionRow(model.text("Input Monitoring", "输入监控"), state: inputMonitoring,
                          identifier: "selection-input-monitoring", action: allowInputMonitoring)
            NativePermissionAction(title: model.text("Check permissions", "检查权限"),
                                   identifier: "check-selection-permissions", action: refresh)
                .fixedSize()
            if accessibility != .granted || inputMonitoring != .granted {
                Text(model.text(
                    "This shortcut needs both permissions. If access still isn't recognized after allowing it, restart CC Translate.",
                    "此快捷键需要这两项权限。允许后若仍未生效，请重启 CC Translate。"))
                    .font(.caption).foregroundStyle(PearlTheme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .foregroundStyle(PearlTheme.text)
    }

    private var shortcutStatus: String {
        switch monitorState {
        case .off:
            return model.text("Shortcut off", "快捷键已关闭")
        case .active:
            return model.text("Shortcut on", "快捷键已开启")
        case .diagnostic:
            return model.text("Diagnostic mode. Translation shortcut off.", "诊断模式，翻译快捷键未运行。")
        case .secureInput:
            return model.text("Paused during secure input", "安全输入期间已暂停")
        case .requiresPermissions:
            return model.text("Waiting for permissions", "等待授权")
        case .temporarilyUnavailable:
            return model.text("Temporarily unavailable. Will retry automatically.", "暂时不可用，将自动重试。")
        }
    }

    private func permissionRow(_ title: String, state: PermissionState?, identifier: String,
                               action: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            Text(title)
            Spacer(minLength: 8)
            Text(stateLabel(state))
                .foregroundStyle(PearlTheme.secondary)
                .accessibilityIdentifier(identifier + "-status")
            if state != .granted {
                NativePermissionAction(title: model.text("Allow \(title)…", "允许\(title)…"),
                                       identifier: identifier + "-allow", action: action)
                    .fixedSize()
            }
        }
        .font(.callout)
    }

    private func stateLabel(_ state: PermissionState?) -> String {
        switch state {
        case .granted: return model.text("Allowed", "已允许")
        case .notGranted: return model.text("Not allowed", "未允许")
        case nil: return model.text("Not checked", "未检查")
        }
    }
}

@MainActor
private struct NativePermissionAction: NSViewRepresentable {
    let title: String
    let identifier: String
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    func makeNSView(context: Context) -> NativePermissionButton {
        let button = NativePermissionButton(title: title, target: nil, action: nil)
        button.bezelStyle = .rounded
        button.setButtonType(.momentaryPushIn)
        button.target = button
        button.action = #selector(NativePermissionButton.invoke)
        updateNSView(button, context: context)
        return button
    }

    func updateNSView(_ button: NativePermissionButton, context: Context) {
        button.title = title
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.setAccessibilityIdentifier(identifier)
        button.setAccessibilityLabel(title)
        button.isEnabled = isEnabled
        button.onPress = action
    }

    static func dismantleNSView(_ button: NativePermissionButton, coordinator: ()) {
        button.onPress = nil
    }
}

@MainActor
private final class NativePermissionButton: NSButton {
    var onPress: (() -> Void)?
    override var intrinsicContentSize: NSSize {
        let size = super.intrinsicContentSize
        return NSSize(width: size.width, height: max(28, size.height))
    }
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func isAccessibilityElement() -> Bool { true }
    @objc func invoke() { onPress?() }
}
