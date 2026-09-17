import SwiftUI
import CCTranslateSupport

@MainActor
struct PlainPasteSettingsSection: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var paste: PlainPasteModel

    var body: some View {
        Section {
            Toggle(model.text("Enable plain-text paste · ⌥⇧⌘V", "开启纯文本粘贴 · ⌥⇧⌘V"), isOn: Binding(
                get: { paste.preference.toggleValue }, set: { model.setPlainPasteEnabled($0) }))
                .disabled(paste.isShutDown)
                .accessibilityIdentifier("plain-paste-enabled")
            Text(model.text(
                "Reserves Option–Shift–Command–V exclusively while enabled. In other apps, waits for key release, removes clipboard formatting and sends one paste action. In CC Translate, uses the native Paste and Match Style command without the external paste service or Accessibility permission. It does not translate or use a model.",
                "开启后独占 Option–Shift–Command–V。在其他应用中，等待松开按键后移除剪贴板格式并发送一次粘贴操作。在 CC Translate 内使用原生“粘贴并匹配样式”，不调用外部粘贴服务，也不需要辅助功能权限。不翻译，也不使用模型。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            preferenceStatus
            registrationStatus
            if paste.serviceState.busy {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(paste.stoppingAction
                         ? model.text("Stopping; waiting for the clipboard worker to finish.", "正在停止，等待剪贴板任务结束。")
                         : serviceProgress)
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    Button(model.text("Cancel paste action", "取消粘贴操作")) { paste.cancelAction() }
                        .disabled(paste.stoppingAction)
                        .accessibilityIdentifier("plain-paste-cancel")
                }
            }
            if paste.lastRoute == .externalApplication, paste.lastAdmission == .busy {
                Text(model.text("Another action is still finishing. No additional paste was queued.",
                                "上一次操作仍在结束中，未排队执行额外粘贴。"))
                    .font(.caption).foregroundStyle(.secondary)
            } else if paste.lastRoute == .externalApplication,
                      paste.lastAdmission == .disabled || paste.lastAdmission == .shutDown {
                Text(model.text("The shortcut request was not accepted; the paste service is off.",
                                "未接受此次快捷键请求，粘贴服务已关闭。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if case .ownApplication(let dispatched)? = paste.lastRoute {
                Text(dispatched
                     ? model.text("Native Paste and Match Style was dispatched in CC Translate.",
                                  "已在 CC Translate 内分派原生“粘贴并匹配样式”命令。")
                     : model.text("No native editor could handle Paste and Match Style. No external paste was requested.",
                                  "没有原生编辑器可处理“粘贴并匹配样式”，未请求外部粘贴。"))
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
            } else if paste.lastRoute == .unavailable {
                Text(model.text("The foreground app could not be identified. No paste was requested.",
                                "无法确定前台应用，未请求粘贴。"))
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
            }
            if case .finished(let outcome) = paste.serviceState.status {
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.text("Last external action report", "上次外部操作报告")).fontWeight(.semibold)
                    Label(reason(outcome.reason), systemImage: "info.circle")
                    Text(clipboardEffect(outcome.clipboard))
                    Text(eventEffect(outcome.events))
                    if outcome.reason == .accessibilityUnavailable {
                        Button(model.text("Accessibility…", "辅助功能…")) { model.requestAX() }
                    }
                }
                .font(.caption).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            ViewThatFits(in: .horizontal) {
                HStack { recoveryButtons }
                VStack(alignment: .leading, spacing: 8) { recoveryButtons }
            }
            Text(model.text(
                "For external apps, file items, including promised files or files mixed with text, are left untouched. Otherwise, plain text or supported RTF is used even when an alternative image format is available; no image data is read. Image-only, HTML-only and RTFD-only content is not converted. Permissions are checked only on an external paste request; enabling does not prompt. Disabling stops external actions immediately, even while saving. CC Translate keeps its native editing command when this feature is off.",
                "外部粘贴不会改动包含文件的内容，包括延迟提供的文件或文件与文字混合的内容。其他情况下，即使附带备用图片格式，仍使用可用的纯文本或受支持的 RTF，不读取图片数据。不转换仅图片、HTML 或 RTFD 的内容。只有实际请求外部粘贴时才检查权限，开启不会弹出授权请求。关闭会立即停止外部操作，即使偏好仍在保存中。关闭此功能后，CC Translate 内仍保留原生编辑命令。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        } header: {
            Text(model.text("Plain-text paste", "纯文本粘贴"))
        }
    }

    @ViewBuilder
    private var recoveryButtons: some View {
        Button(model.text("Reload paste preference", "重新读取粘贴偏好")) { model.reloadPlainPastePreference() }
            .disabled(model.settingsBusy || paste.isShutDown)
        if case .failed = paste.preference.phase, paste.preference.desired != nil {
            Button(model.text("Apply preference again", "再次应用偏好")) {
                model.setPlainPasteEnabled(paste.preference.toggleValue)
            }
            .disabled(!model.ready || model.settingsBusy || paste.isShutDown)
        }
        if paste.canRetryRegistration {
            Button(model.text("Retry shortcut registration", "重试快捷键注册")) { paste.retryRegistration() }
                .accessibilityIdentifier("plain-paste-retry")
        }
    }

    @ViewBuilder
    private var preferenceStatus: some View {
        switch paste.preference.phase {
        case .unloaded:
            Text(model.text("Off until an enabled preference has been confirmed.", "确认已保存的开启偏好前保持关闭。"))
                .font(.caption).foregroundStyle(.secondary)
        case .waiting:
            Text(model.text("Preference change is waiting for the settings connection or current save.",
                            "偏好更改正在等待设置连接或当前保存操作完成。")).font(.caption)
        case .saving:
            ProgressView(model.text("Saving paste preference…", "正在保存粘贴偏好…")).controlSize(.small)
        case .reading:
            ProgressView(model.text("Confirming saved paste preference…", "正在确认已保存的粘贴偏好…")).controlSize(.small)
        case .confirmed:
            Text(paste.preference.stored == true
                 ? model.text("Saved preference: enabled.", "已保存偏好：开启。")
                 : model.text("Saved preference: disabled.", "已保存偏好：关闭。"))
                .font(.caption).foregroundStyle(.secondary)
        case .failed(let failure):
            Label(preferenceFailure(failure), systemImage: "exclamationmark.triangle")
                .font(.caption).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var registrationStatus: some View {
        switch paste.registration {
        case .off: Text(model.text("Shortcut not registered.", "快捷键未注册。")).font(.caption).foregroundStyle(.secondary)
        case .registering: ProgressView(model.text("Reserving shortcut…", "正在注册独占快捷键…")).controlSize(.small)
        case .registered:
            Label(model.text("Shortcut reserved · ⌥⇧⌘V", "快捷键已独占注册 · ⌥⇧⌘V"), systemImage: "keyboard")
                .font(.caption)
        case .failed(let error):
            Label(registrationFailure(error), systemImage: "exclamationmark.triangle")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var serviceProgress: String {
        switch paste.serviceState.status {
        case .reading: return model.text("Reading clipboard text…", "正在读取剪贴板文字…")
        case .waitingForKeys: return model.text("Release the shortcut keys to continue.", "请松开快捷键以继续。")
        case .writing: return model.text("Removing clipboard formatting…", "正在移除剪贴板格式…")
        case .verifying: return model.text("Checking clipboard ownership and destination…", "正在检查剪贴板所有权与目标…")
        case .idle, .finished: return model.text("Finishing the current action…", "正在结束当前操作…")
        }
    }

    private func preferenceFailure(_ failure: PlainPastePreference.Failure) -> String {
        switch failure {
        case .missingFlag: return model.text("The saved paste preference is missing or invalid. The shortcut remains off; reload settings.",
                                             "已保存的粘贴偏好缺失或无效。快捷键保持关闭，请重新读取设置。")
        case .operation(let code): return model.text("Paste preference could not be confirmed (\(code)). Reload before retrying; no write is replayed automatically.",
                                                     "无法确认粘贴偏好（\(code)）。请重新读取后再重试，不会自动重放写入。")
        case .interrupted: return model.text("The settings connection closed. The shortcut is off until the saved preference is confirmed again.",
                                             "设置连接已关闭，重新确认已保存偏好前快捷键保持关闭。")
        case .differentReadback:
            return model.text("The saved preference differs from your choice. The shortcut remains off. Reload or explicitly apply again.",
                              "已保存偏好与所选值不同，快捷键保持关闭。请重新读取或手动再次应用。")
        }
    }

    private func registrationFailure(_ failure: PlainPasteRegistrationError) -> String {
        switch failure {
        case .conflict: return model.text("This shortcut is already reserved. Release it in the other app and retry. No alternate shortcut was chosen.",
                                          "此快捷键已被占用。请在其他应用中解除占用后重试，未选择备用快捷键。")
        case .unavailable(let code): return model.text("Shortcut registration is unavailable (system code \(code)). No paste was started.",
                                                      "无法注册快捷键（系统代码 \(code)），未开始粘贴。")
        case .releaseFailed(let code): return model.text("Could not release the shortcut (system code \(code)). Paste is disabled. Retry releasing it, or quit this app to release its resources.",
                                                        "无法释放快捷键（系统代码 \(code)）。粘贴已停用。请重试释放，或退出本应用以释放资源。")
        }
    }

    private func reason(_ value: PlainTextPasteReason) -> String {
        switch value {
        case .eventsSubmitted: return model.text("Paste key events were submitted; insertion is not confirmed.", "已提交粘贴按键事件，尚未确认内容插入。")
        case .noText: return model.text("No supported clipboard text was available.", "剪贴板中没有受支持的文字。")
        case .unsupportedRepresentation: return model.text("This clipboard representation is not supported.", "不支持此剪贴板内容格式。")
        case .unavailableData: return model.text("Clipboard data could not be read.", "无法读取剪贴板数据。")
        case .invalidRichText: return model.text("The rich-text data could not be converted.", "无法转换富文本数据。")
        case .clipboardChanged: return model.text("Clipboard ownership changed. The action stopped.", "剪贴板所有权已更改，操作已停止。")
        case .clipboardTimedOut: return model.text("Clipboard access timed out.", "访问剪贴板超时。")
        case .writeFailed: return model.text("The clipboard write failed.", "剪贴板写入失败。")
        case .targetUnavailable: return model.text("No eligible external destination was found.", "未找到可用的外部目标。")
        case .targetChanged: return model.text("The destination or focus changed. The action stopped.", "目标或焦点已改变，操作已停止。")
        case .accessibilityUnavailable: return model.text("Accessibility permission is needed for this action.", "此操作需要辅助功能权限。")
        case .secureInput: return model.text("Secure Input prevented the action.", "安全输入模式阻止了此操作。")
        case .keysStillPressed, .keyReleaseTimedOut: return model.text("The required keys were not released in time.", "未及时松开所需按键。")
        case .eventCreationFailed: return model.text("Paste key events could not be created.", "无法创建粘贴按键事件。")
        case .eventPostingFailed: return model.text("Paste key events could not be fully submitted.", "无法完整提交粘贴按键事件。")
        case .cancelled: return model.text("The action was cancelled.", "操作已取消。")
        case .disabled: return model.text("The action was stopped because the feature was disabled.", "功能已关闭，操作已停止。")
        case .shutDown: return model.text("The action was stopped during shutdown.", "操作因退出而停止。")
        }
    }

    private func clipboardEffect(_ effect: PlainTextPasteOutcome.ClipboardEffect) -> String {
        switch effect {
        case .unchanged: return model.text("This action did not change the clipboard.", "此操作未更改剪贴板。")
        case .mayHaveChanged: return model.text("Clipboard contents or formatting may have changed.", "剪贴板内容或格式可能已改变。")
        case .cleared: return model.text("The clipboard was cleared before the action ended.", "操作结束前剪贴板已被清空。")
        case .plainTextWritten: return model.text("Plain text was written to the clipboard.", "已向剪贴板写入纯文本。")
        }
    }

    private func eventEffect(_ effect: PlainTextPasteOutcome.EventEffect) -> String {
        switch effect {
        case .notPosted: return model.text("No paste key events were posted.", "未发送粘贴按键事件。")
        case .mayHavePosted: return model.text("Some key events may have been posted. Insertion is not confirmed; do not retry automatically.",
                                               "部分按键事件可能已发送。尚未确认内容插入，请勿自动重试。")
        case .submittedUnconfirmed: return model.text("The destination has not confirmed insertion. No automatic retry.", "目标未确认内容插入，不会自动重试。")
        }
    }
}
