import SwiftUI
import AppKit

@MainActor
struct AboutView: View {
    @ObservedObject var model: AboutModel
    @ObservedObject var presentation: ProbeModel
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "character.bubble")
                    .font(.system(size: 32)).foregroundStyle(.secondary).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text("CC Translate").font(.title2.bold())
                    Text(presentation.text("Native macOS edition", "原生 macOS 版本"))
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(20)
            Picker(presentation.text("Section", "页面"), selection: $model.page) {
                Text(presentation.text("About", "关于")).tag(AboutModel.Page.application)
                Text(presentation.text("Third-party licenses", "第三方许可")).tag(AboutModel.Page.licenses)
            }
            .pickerStyle(.radioGroup).horizontalRadioGroupLayout().labelsHidden()
            .padding(.bottom, 12)
            Group {
                switch model.page {
                case .application: overview
                case .licenses: licenses
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .controlBackgroundColor))
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color(nsColor: .separatorColor)))
            .padding(.horizontal, 12)
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
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(presentation.text("Close", "关闭"), action: close)
            }
            .padding(12)
        }
        .frame(minWidth: 660, minHeight: 520)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(presentation.preferredColorScheme)
    }

    private var overview: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !model.overview.issues.isEmpty { issues }
                if model.phase == .loading {
                    ProgressView(presentation.text("Reading bundled resources…", "正在读取随包资源…"))
                } else {
                    GroupBox(presentation.text("Package information · Info.plist", "包信息 · Info.plist")) {
                        VStack(alignment: .leading, spacing: 10) {
                            row("Bundle name", "包名称", model.overview.info?.name)
                            row("Version", "版本", model.overview.info?.version)
                            row("Build", "构建号", model.overview.info?.build)
                            row("Bundle identifier", "包标识", model.overview.info?.identifier)
                            row("Minimum macOS", "最低 macOS 版本", model.overview.info?.minimumOS)
                        }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Text(presentation.text(
                        "Values below are declarations in source-manifest.json, not a live audit. This window does not verify signatures, notarization, source integrity, or installed command-line tools.",
                        "下方信息是 source-manifest.json 中的记录，并非实时审计。此窗口不验证签名、公证、源码完整性或已安装的命令行工具。"))
                        .font(.callout).foregroundStyle(.secondary)
                    GroupBox(presentation.text("Recorded build source", "记录的构建来源")) {
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
                        }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                Text(presentation.text(
                    "The application's own license has not been confirmed. The bundled third-party notices and license texts apply to their respective components.",
                    "应用自身的许可尚未确认。随包的第三方声明和许可原文适用于各自的组件。"))
                    .font(.callout).textSelection(.enabled)
            }
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var licenses: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(presentation.text(
                "Choose a bundled file to read its complete UTF-8 text. Text is selectable and shown verbatim; links are not opened automatically.",
                "选择随包文件以阅读完整的 UTF-8 原文。文字可选择，不作改写，也不会自动打开链接。"))
                .font(.callout).foregroundStyle(.secondary)
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
                                     label: presentation.text("License text", "许可原文") + ": " + (model.selectedDocument ?? ""))
                        .id(model.selectedDocument)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                } else {
                    Text(presentation.text("Select a document above. No license text has been loaded yet.",
                                           "请在上方选择文档，尚未加载许可原文。"))
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                Text(presentation.text(
                    "When available, file checksums are compared with the bundled manifest. This does not authenticate the package or grant an application license.",
                    "清单提供校验值时，会核对文件是否与随包清单一致。这不代表应用程序包的真实性验证，也不授予应用自身的许可。"))
                    .font(.caption).foregroundStyle(.secondary)
                if !model.overview.issues.isEmpty {
                    Text(presentation.text("Some package resources could not be read. See About for details and use Reload resources to retry.",
                                           "部分包资源无法读取。请在“关于”中查看详情，并点击“重新读取资源”重试。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
            Text(presentation.text(english, chinese)).foregroundStyle(.secondary)
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
