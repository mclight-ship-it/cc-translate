import SwiftUI
import CCTranslateSupport

@MainActor
struct ProbeView: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("CC Translate - P0 native probes").font(.title2)
            Text("SYNTHETIC FIXTURE ONLY - NOT TRANSLATION").font(.headline).foregroundStyle(.orange)
            TabView {
                core.tabItem { Text("Bundled core") }
                native.tabItem { Text("Permissions / AX") }
                ScreenView(probe: model.screen).tabItem { Text("Screen / local OCR") }
                cli.tabItem { Text("CLI locator") }
            }
        }
        .padding(16)
        .frame(minWidth: 700, minHeight: 560)
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
                Button("Run synthetic fixture") { model.fixture() }.disabled(!model.ready)
                Button("Cancel latest") { model.cancel() }.disabled(!model.active || !model.ready)
            }
            HStack {
                Button("SQLite / SSL probe (no network)") { model.runtimeProbe(https: false) }
                Button("HTTPS probe (explicit network)") { model.runtimeProbe(https: true) }
            }.disabled(!model.ready)
            Text(model.status).font(.callout).fixedSize(horizontal: false, vertical: true)
            ScrollView {
                Text(model.output.isEmpty ? "No result." : model.output)
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
            Text("Selection: present / absent / unknown. Only selectedText; no clipboard access or simulated copy.")
            HStack {
                Button("Start passive double Cmd+C") { model.startMonitor() }
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
            }.pickerStyle(.segmented).disabled(model.cliBusy)
                .onChange(of: model.cliName) { _, _ in model.locateCLI() }
            HStack {
                Button("Locate known paths") { model.locateCLI() }
                Button("Choose executable...") { model.chooseCLI() }
            }.disabled(model.cliBusy)
            if !model.candidates.isEmpty {
                Picker("Executable", selection: $model.selectedCLI) {
                    Text("None selected").tag("")
                    ForEach(model.candidates) { candidate in
                        Text("\(candidate.executable ? "executable" : "missing/not executable"): \(candidate.url.path)")
                            .tag(candidate.url.path)
                    }
                }
            }
            Text("P0 supervises only the directly launched process. Wrappers that leave background children are unsupported; process-group supervision is P1.")
                .font(.callout).foregroundStyle(.orange)
            HStack {
                Button("Run selected --version (5s limit)") { model.versionCLI() }
                    .disabled(model.cliBusy || model.selectedCLI.isEmpty)
                Button("Cancel version probe") { model.cancelCLI() }.disabled(!model.cliBusy)
            }
            Text("No shell/profile, login, credential copy, or model launch. CLI output is discarded, never displayed or logged.")
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
