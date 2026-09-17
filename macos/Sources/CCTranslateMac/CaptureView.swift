import AppKit
import SwiftUI
import CCTranslateSupport

@MainActor
struct CaptureView: View {
    @ObservedObject var capture: CaptureModel
    @ObservedObject var model: ProbeModel
    var captureAgain: () -> Void
    var reselect: () -> Void
    var close: () -> Void
    @Environment(\.controlActiveState) private var controlActiveState
    @FocusState private var editorFocused: Bool
    private var statusText: String { capture.showsTranslationStatus ? model.productMessage : capture.message(using: model) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label(model.text("Screenshot translation", "截图翻译"), systemImage: "viewfinder")
                    .font(.title2).accessibilityAddTraits(.isHeader)
                Spacer()
                Button(model.text("Close", "关闭"), action: close)
            }
            Text(model.text("Capture and text recognition stay on this Mac. Only reviewed text is sent when you choose Translate text.",
                            "截图和文字识别仅在此 Mac 上进行。只有点击“翻译文字”后，才会发送你确认的文字。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            HStack(alignment: .top) {
                if capture.busy || capture.submitting {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(statusText)
                }
                Text(statusText)
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if capture.busy || capture.submitting {
                    Button(model.text("Cancel", "取消")) { capture.cancel() }
                }
            }
            if let preview = capture.preview {
                GeometryReader { viewport in
                    ScrollViewReader { scroll in
                        ScrollView {
                            if viewport.size.width >= 670 {
                                HStack(alignment: .top, spacing: 16) {
                                    previewImage(preview).frame(minWidth: 280, maxWidth: .infinity)
                                    editor.id("reviewed-capture-text").frame(minWidth: 350, maxWidth: .infinity)
                                }
                                .frame(height: 290)
                            } else {
                                VStack(spacing: 12) {
                                    previewImage(preview).frame(height: 145)
                                    editor.id("reviewed-capture-text").frame(height: 220)
                                }
                            }
                        }
                        .onChange(of: editorFocused) { _, focused in
                            if focused { scroll.scrollTo("reviewed-capture-text", anchor: .top) }
                        }
                        .onChange(of: viewport.size.width) { _, _ in
                            if editorFocused { scroll.scrollTo("reviewed-capture-text", anchor: .top) }
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: capture.phase == .failed ? "exclamationmark.rectangle" : "viewfinder")
                        .font(.system(size: 36)).foregroundStyle(.secondary).accessibilityHidden(true)
                    Text(model.text("Select a region, review the text, then translate.", "选择区域、确认文字，然后翻译。"))
                        .font(.headline)
                    Text(model.text("No image upload. No clipboard access. No image is saved.",
                                    "不上传图片，不读取剪贴板，不保存图片。"))
                        .font(.callout).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            HStack {
                Button(model.text("Capture again", "重新截图"), action: captureAgain)
                Button(model.text("Reselect retained frame", "在保留帧上重选"), action: reselect)
                    .disabled(capture.frames.isEmpty || capture.phase == .capturing)
                if (capture.failure == .ocrFailed || capture.failure == .notReady) && capture.preview != nil {
                    Button(model.text("Retry local OCR", "重试本地识别")) { capture.recognizeSelection() }
                }
                Spacer(minLength: 0)
            }
            Divider()
            HStack(spacing: 12) {
                DirectionPicker(model: model, selection: $model.direction)
                ModelPicker(model: model, selection: $model.modelProfile)
            }
            .disabled(model.active || model.preparing)
            HStack(alignment: .center) {
                Text(model.text("Uses your Codex CLI and account for text translation only.",
                                "仅文字翻译使用你的 Codex CLI 和账号。"))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 12)
                Button {
                    capture.translate(using: model)
                } label: {
                    Label(model.text("Translate text", "翻译文字"), systemImage: "arrow.right")
                }
                .buttonStyle(.bordered).controlSize(.large)
                .disabled(!capture.canTranslate || model.active || model.preparing)
            }
        }
        .padding(18)
        .frame(minWidth: 620, minHeight: 600)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
        .onAppear {
            if controlActiveState == .key && (capture.phase == .ready || capture.phase == .empty) {
                editorFocused = true
            }
        }
        .onChange(of: capture.phase) { _, phase in
            if controlActiveState == .key && (phase == .ready || phase == .empty) { editorFocused = true }
        }
    }

    private func previewImage(_ image: NSImage) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(model.text("Retained region", "保留的截图区域")).font(.subheadline.bold())
            Image(nsImage: image).resizable().scaledToFit()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(nsColor: .textBackgroundColor))
                .accessibilityLabel(model.text("Screenshot region used for this local recognition",
                                              "此次本地识别使用的截图区域"))
            Text(model.text("Multiple displays are sampled sequentially, not at exactly the same instant.",
                            "多个屏幕逐一采样，并非严格同时曝光。"))
                .font(.caption2).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.text("Recognized text · Editable", "识别文字 · 可编辑"))
                .font(.subheadline.bold()).accessibilityAddTraits(.isHeader)
            TextEditor(text: $capture.text)
                .font(.system(size: 15))
                .focused($editorFocused)
                .disabled(capture.busy)
                .accessibilityLabel(model.text("Reviewed screenshot text to translate", "确认后用于翻译的截图文字"))
                .frame(minHeight: 160, maxHeight: .infinity)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(
                    editorFocused ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: 1))
            Text(model.text("\(capture.text.utf8.count) / 8,192 UTF-8 bytes",
                            "\(capture.text.utf8.count) / 8,192 UTF-8 字节"))
                .font(.caption).monospacedDigit().foregroundStyle(.secondary)
            if capture.text.utf8.count > 8192 {
                Label(model.text("Shorten the text before translating. Nothing is truncated or sent automatically.",
                                 "请缩短文字后再翻译。不会自动截断或发送。"), systemImage: "exclamationmark.circle")
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
