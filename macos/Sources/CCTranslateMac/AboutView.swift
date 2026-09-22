import SwiftUI
import AppKit

@MainActor
struct AboutView: View {
    @ObservedObject var model: AboutModel
    @ObservedObject var presentation: ProbeModel
    let close: () -> Void
    @State private var showingBuildDetails = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker(presentation.text("Section", "页面"), selection: $model.page) {
                    Text(presentation.text("About", "关于")).tag(AboutModel.Page.application)
                    Text(presentation.text("Third-party licenses", "第三方许可")).tag(AboutModel.Page.licenses)
                }
                .pickerStyle(.radioGroup).horizontalRadioGroupLayout().labelsHidden()
                Spacer()
                if model.page == .licenses { supportButton }
            }
            .padding(PearlTheme.spacing)
            Group {
                switch model.page {
                case .application: overview
                case .licenses: licenses
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .pearlCard()
            .padding(.horizontal, PearlTheme.pagePadding)
            HStack {
                Button(presentation.text("Reload resources", "重新读取资源")) { model.reload() }
                    .disabled(model.phase == .loading)
                Button(presentation.text("Copy app information", "复制应用信息")) {
                    model.copyInformation(using: presentation)
                }
                .disabled(model.phase != .loaded)
                if let status = model.copyStatus {
                    Text(status == .copied
                         ? presentation.text("Copied", "已复制")
                         : presentation.text("Could not copy.", "无法复制。"))
                        .font(.caption).foregroundStyle(PearlTheme.secondary)
                }
                Spacer()
                Button(presentation.text("Close", "关闭"), action: close)
            }
            .padding(PearlTheme.spacing)
        }
        .frame(minWidth: 660, minHeight: 520)
        .pearlSurface()
        .preferredColorScheme(presentation.preferredColorScheme)
        .sheet(isPresented: Binding(get: { model.showingSupport }, set: {
            if !$0 { model.dismissSupport() }
        })) {
            AboutSupportView(model: model, presentation: presentation)
        }
    }

    @MainActor
    struct AboutSupportView: View {
        @ObservedObject var model: AboutModel
        @ObservedObject var presentation: ProbeModel

        var body: some View {
            VStack(alignment: .leading, spacing: 16) {
                Text(presentation.text("Buy the author a coffee", "请作者喝杯咖啡"))
                    .font(.title2.bold())
                Text(presentation.text("Scan with Alipay or WeChat Pay.",
                                       "使用支付宝或微信支付扫码。"))
                    .foregroundStyle(PearlTheme.secondary)
                Group {
                    if model.supportBusy {
                        ProgressView(presentation.text("Reading bundled image…", "正在读取随包图片…"))
                    } else if let error = model.supportError {
                        VStack(alignment: .leading, spacing: 12) {
                            Label("support-author.png", systemImage: "exclamationmark.triangle")
                            Text(model.message(for: error, using: presentation)).textSelection(.enabled)
                            Button(presentation.text("Retry reading image", "重试读取图片"), action: model.loadSupport)
                        }
                    } else if let image = model.supportImage {
                        Image(nsImage: image).resizable().interpolation(.none).scaledToFit()
                            .accessibilityLabel(presentation.text(
                                "Author support QR codes for Alipay and WeChat Pay",
                                "支持作者的支付宝和微信支付收款二维码"))
                    }
                }
                .frame(width: 580, height: 338)
                HStack {
                    Spacer()
                    Button(presentation.text("Done", "完成"), action: model.dismissSupport)
                        .keyboardShortcut(.cancelAction)
                        .accessibilityIdentifier("about-support-done")
                }
            }
            .padding(20)
            .frame(width: 620)
            .pearlSurface()
            .preferredColorScheme(presentation.preferredColorScheme)
        }
    }

    private var overview: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PearlTheme.spacing) {
                if !model.overview.issues.isEmpty { issues }
                identitySection
                if model.phase == .loading {
                    ProgressView(presentation.text("Reading bundled resources…", "正在读取随包资源…"))
                } else {
                    VStack(alignment: .leading, spacing: 12) {
                        Button {
                            showingBuildDetails.toggle()
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: showingBuildDetails ? "chevron.down" : "chevron.right")
                                    .font(.caption).accessibilityHidden(true)
                                Text(presentation.text("Package information & recorded build source", "包信息与构建来源"))
                                    .multilineTextAlignment(.leading)
                                Spacer(minLength: 0)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.borderless)
                        .accessibilityIdentifier("about-build-details")
                        .accessibilityValue(showingBuildDetails
                            ? presentation.text("Expanded", "已展开")
                            : presentation.text("Collapsed", "已折叠"))
                        if showingBuildDetails { buildDetails }
                    }
                    .padding(PearlTheme.spacing)
                    .pearlCard(inset: true)
                }
                Text(presentation.text(
                    "The application's own license has not been confirmed. The bundled third-party notices and license texts apply to their respective components.",
                    "应用自身的许可尚未确认。随包的第三方声明和许可原文适用于各自的组件。"))
                    .font(.callout).foregroundStyle(PearlTheme.secondary).textSelection(.enabled)
            }
            .padding(PearlTheme.spacing).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var versionLabel: String {
        let missing = presentation.text("Not supplied in bundle", "包内未提供")
        let version = model.overview.info?.version ?? missing
        let build = model.overview.info?.build ?? missing
        let versionTitle = presentation.text("Version", "版本")
        let buildTitle = presentation.text("Build", "构建号")
        return "\(versionTitle) \(version) · \(buildTitle) \(build)"
    }

    private var identitySection: some View {
        VStack(spacing: 12) {
            PearlAppIcon(size: 64)
            Text("CC Translate").font(.largeTitle.bold())
                .accessibilityAddTraits(.isHeader)
            Text(versionLabel)
                .font(.callout).foregroundStyle(PearlTheme.secondary)
                .textSelection(.enabled)
            supportButton
                .buttonStyle(.bordered).controlSize(.large)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }

    private var supportButton: some View {
        Button(presentation.text("Buy the author a coffee", "请作者喝杯咖啡"), action: model.showSupport)
        .accessibilityIdentifier("about-support-author")
        .disabled(model.phase != .loaded)
    }

    private var buildDetails: some View {
        VStack(alignment: .leading, spacing: PearlTheme.spacing) {
            Text(presentation.text("Package information · Info.plist", "包信息 · Info.plist"))
                .font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                row("Bundle name", "包名称", model.overview.info?.name)
                row("Version", "版本", model.overview.info?.version)
                row("Build", "构建号", model.overview.info?.build)
                row("Bundle identifier", "包标识", model.overview.info?.identifier)
                row("Minimum macOS", "最低 macOS 版本", model.overview.info?.minimumOS)
            }
            Text(presentation.text(
                "Values below are declarations in source-manifest.json, not a live audit. This window does not verify signatures, notarization, source integrity, or installed command-line tools.",
                "下方信息是 source-manifest.json 中的记录，并非实时审计。此窗口不验证签名、公证、源码完整性或已安装的命令行工具。"))
                .font(.callout).foregroundStyle(PearlTheme.secondary)
            Text(presentation.text("Recorded build source", "记录的构建来源"))
                .font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                row("Source commit", "源码提交", model.overview.source?.source_commit)
                row("Uncommitted changes", "存在未提交更改", boolean(model.overview.source?.source_tree_dirty))
                row("Development package", "开发包", boolean(model.overview.source?.development_only))
                row("Signing declaration", "签名声明", model.overview.source?.signing)
                row("Release gate declaration", "发布门槛声明", model.overview.source?.release_gate)
                row("Build toolchain", "构建工具链", model.overview.source?.toolchain?.xcode)
                row("Build SDK", "构建 SDK", model.overview.source?.toolchain?.sdk)
                row("Build architecture", "构建架构", model.overview.source?.toolchain?.architecture)
                row("Bundled Python version", "随包 Python 版本", model.overview.source?.lock?.python_version)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var licenses: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(presentation.text(
                "Choose a bundled file to read its complete UTF-8 text. Text is selectable and shown verbatim; links are not opened automatically.",
                "选择随包文件以阅读完整的 UTF-8 原文。文字可选择，不作改写，也不会自动打开链接。"))
                .font(.callout).foregroundStyle(PearlTheme.secondary)
            if model.phase == .loading {
                ProgressView(presentation.text("Reading bundled resources…", "正在读取随包资源…"))
            } else if model.overview.documents.isEmpty {
                Label(presentation.text("No bundled license documents are available.", "没有可读取的随包许可文档。"),
                      systemImage: "doc.text")
                ScrollView { issues }
            } else {
                Picker(presentation.text("Bundled document", "随包文档"), selection: $model.selectedDocument) {
                    Text(presentation.text("Choose a document", "选择文档")).tag(Optional<String>.none)
                    ForEach(model.overview.documents) { document in
                        Text(verbatim: document.path).tag(Optional(document.id))
                    }
                }
                .disabled(model.phase == .loading)
                if model.documentBusy {
                    ProgressView(presentation.text("Reading document…", "正在读取文档…"))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = model.documentError {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(model.message(for: error, using: presentation), systemImage: "exclamationmark.triangle")
                            .textSelection(.enabled)
                        Button(presentation.text("Retry reading document", "重试读取文档")) { model.loadDocument() }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                } else if let text = model.documentText {
                    NativeResultText(text: text, formatted: false, streaming: false,
                                     label: presentation.text("License text", "许可原文") + ": " + (model.selectedDocument ?? ""),
                                     textScale: presentation.nativeTextScale)
                        .id(model.selectedDocument)
                        .pearlCard(inset: true)
                } else {
                    Text(presentation.text("Select a document above. No license text has been loaded yet.",
                                           "请在上方选择文档，尚未加载许可原文。"))
                        .foregroundStyle(PearlTheme.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                Text(presentation.text(
                    "When available, file checksums are compared with the bundled manifest. This does not authenticate the package or grant an application license.",
                    "清单提供校验值时，会核对文件是否与随包清单一致。这不代表应用程序包的真实性验证，也不授予应用自身的许可。"))
                    .font(.caption).foregroundStyle(PearlTheme.secondary)
                if !model.overview.issues.isEmpty {
                    Text(presentation.text("Some package resources could not be read. See About for details and use Reload resources to retry.",
                                           "部分包资源无法读取。请在“关于”中查看详情，并点击“重新读取资源”重试。"))
                        .font(.caption).foregroundStyle(PearlTheme.secondary)
                }
            }
        }
        .padding(PearlTheme.spacing).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var issues: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(model.overview.issues) { issue in
                VStack(alignment: .leading, spacing: 4) {
                    Label(issue.resource == "Application bundle"
                          ? presentation.text("Application bundle", "应用程序包") : issue.resource,
                          systemImage: "exclamationmark.triangle")
                        .font(.callout.bold())
                    Text(model.message(for: issue.error, using: presentation))
                        .font(.callout).textSelection(.enabled)
                }
            }
        }
    }

    private func row(_ english: String, _ chinese: String, _ value: String?) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Text(presentation.text(english, chinese)).foregroundStyle(PearlTheme.secondary)
                .frame(width: 150, alignment: .leading)
            Text(verbatim: value ?? presentation.text("Not supplied in bundle", "包内未提供"))
                .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.callout).fixedSize(horizontal: false, vertical: true)
    }

    private func boolean(_ value: Bool?) -> String? {
        value.map { $0 ? presentation.text("Yes", "是") : presentation.text("No", "否") }
    }
}
