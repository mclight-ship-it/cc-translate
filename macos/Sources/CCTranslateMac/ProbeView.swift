import SwiftUI
import CCTranslateSupport

@MainActor
struct ProbeView: View {
    @ObservedObject var model: ProbeModel
    @ObservedObject var translation: ProbeModel

    init(model: ProbeModel, translation: ProbeModel? = nil) {
        self.model = model
        self.translation = translation ?? model
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Diagnostics").font(.title2)
            Text("Optional checks. These do not need to pass before you can translate.")
                .font(.callout).foregroundStyle(.secondary)
            TabView {
                timings.tabItem { Text(translation.text("Translation timing", "翻译耗时")) }
                core.tabItem { Text("Bundled core") }
                native.tabItem { Text("Permissions / AX") }
                ScreenView(probe: model.screen).tabItem { Text("Screen / local OCR") }
                cli.tabItem { Text("CLI locator") }
            }

        }
        .padding(16)
        .frame(minWidth: 700, minHeight: 560)
    }

    private var timings: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(translation.text("Recent translations", "最近的翻译")).font(.headline)
            Text(translation.text(
                "Up to 20 requests, kept only in memory. No original text, results, or account information.",
                "仅在内存中保留最近 20 次请求，不包含原文、译文或账号信息。"))
                .foregroundStyle(.secondary)
            Text(translation.text(
                "Times are milliseconds; hit flags are 0 or 1. First output measures the view-model update, not screen painting.",
                "时间单位为毫秒，命中标记为 0 或 1。首字耗时记录界面数据更新，不代表屏幕绘制完成。"))
                .font(.callout).foregroundStyle(.secondary)
            if let failure = translation.prewarmFailure {
                Text(translation.text("Engine preparation did not finish: ", "翻译引擎预备未完成：") + failure)
                    .font(.callout)
            }
            ScrollView([.horizontal, .vertical]) {
                Text(translation.latency.report.isEmpty
                     ? translation.text("No translation measurements yet.", "尚无翻译耗时记录。")
                     : translation.latency.report)
                    .font(.body.monospaced()).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }.padding()
    }

    private var core: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button("Start bundled helper") { model.startHelper() }.disabled(model.connected)
                Button("Stop helper") { model.stopHelper() }.disabled(!model.connected)
            }
            Text("Input (max 8192 UTF-8 bytes; never passed in process arguments)")
            TextEditor(text: $model.input).font(.body.monospaced()).frame(height: 100)
                .border(.secondary)
            HStack {
                Button("Run synthetic fixture") { model.fixture() }.disabled(!model.ready || model.nativeTranslation)
                Button("Cancel latest") { model.cancel() }.disabled(!model.active || !model.ready)
            }
            HStack {
                Button("SQLite / SSL / config (offline)") { model.runtimeProbe(https: false) }
                    .help("Uses a bundled synthetic CLI only, never your real CLI, account, or model.")
                Button("HTTPS probe (explicit network)") { model.runtimeProbe(https: true) }
                    .help("Includes the same synthetic config probe; HTTPS contacts only the fixed public host.")
            }.disabled(!model.ready || model.nativeTranslation)
            Text(model.status).font(.callout).fixedSize(horizontal: false, vertical: true)
            ScrollView {
                Text(model.nativeTranslation ? "Diagnostics require a separate diagnostic connection." :
                        (model.output.isEmpty ? "No result." : model.output))
                    .font(.body.monospaced()).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }.padding()
    }

    private var native: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(model.permissions).font(.body.monospaced())
            HStack {
                Button("Refresh states") { model.refreshPermissions() }
                Button("Request Accessibility") { model.requestAX() }
                Button("Request Input Monitoring") { model.requestInputMonitoring() }
            }
            Text("Use the menu-bar AX selection action while another app is focused. Result panel does not activate.")
            Text(model.text("Manual AX probes read selectedText only. Starting AX-only monitoring below does not enable clipboard fallback or simulate Copy.",
                            "手动辅助功能探针只读取选中文字。下方的仅辅助功能监听不会开启剪贴板回退或模拟复制。"))
            HStack {
                Button(model.text("Start AX-only double Cmd+C", "启动仅辅助功能双击 Cmd+C")) {
                    model.startMonitor(accessibilityOnly: true)
                }
                Button("Stop monitor") { model.stopMonitor() }
            }
            Text(model.monitorStatus).fixedSize(horizontal: false, vertical: true)
            Text("Secure Input stops monitoring. Permission changes may require a restart. No automatic permission requests.")
                .foregroundStyle(.secondary)
            Spacer()
        }.padding()
    }

    private var cli: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("CLI", selection: $model.cliName) {
                Text("Codex").tag("codex")
                Text("Claude").tag("claude")
            }.pickerStyle(.segmented).disabled(model.cliBusy || model.connected)
                .onChange(of: model.cliName) { _, _ in model.locateCLI() }
            HStack {
                Button("Locate known paths") { model.locateCLI() }
                Button("Choose executable...") { model.chooseCLI() }
            }.disabled(model.cliBusy || model.connected)
            if !model.candidates.isEmpty {
                Picker("Executable", selection: $model.selectedCLI) {
                    Text("None selected").tag("")
                    ForEach(model.candidates) { candidate in
                        Text("\(candidate.executable ? "executable" : "missing/not executable"): \(candidate.url.path)")
                            .tag(candidate.url.path)
                    }
                }.disabled(model.cliBusy || model.connected)
            }
            Text("This version probe supervises only its own process group, including descendants that stay in it. Wrappers that leave the group are unsupported.")
                .font(.callout).foregroundStyle(.orange)
            HStack {
                Button("Run selected --version (5s limit)") { model.versionCLI() }
                    .disabled(model.cliBusy || model.selectedCLI.isEmpty)
                Button("Cancel version probe") { model.cancelCLI() }.disabled(!model.cliBusy)
            }
            Text("No shell/profile, login, credential copy, or model launch. Only a sanitized Codex numeric version and policy are shown. Raw CLI output is discarded, never displayed or logged.")
            ScrollView {
                Text(model.cliStatus).font(.body.monospaced()).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }.padding()
    }
}

@MainActor
private struct ScreenView: View {
    @ObservedObject var probe: ScreenProbe

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button("Grant + capture main display once") { probe.grantAndCaptureOnce() }
                    .disabled(probe.busy)
                Button("Confirm preview: local OCR") { probe.confirmOCR() }
                    .disabled(probe.busy || !probe.canConfirm)
                Button("Cancel / clear") { probe.clear() }
            }
            Text("One display, at most 4096px per side. Preview and Vision use the same captured CGImage.")
                .font(.caption)
            Text(probe.status).font(.callout).lineLimit(4)
            if let preview = probe.preview {
                Image(nsImage: preview).resizable().scaledToFit().frame(maxHeight: 210)
            }
            ScrollView {
                Text(probe.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            }
            Text("No disk save, clipboard access, upload, or automatic translation. Closing this panel clears the capture.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding()
    }
}
