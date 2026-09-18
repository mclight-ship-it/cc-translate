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
    @State private var editorFocused = false
    private var statusText: String { capture.showsTranslationStatus ? model.productMessage : capture.message(using: model) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label(model.text("Screenshot translation", "截图翻译"), systemImage: "viewfinder")
                    .font(.title2).accessibilityAddTraits(.isHeader)
                Spacer()
                Button(model.text("Close", "关闭"), action: close)
            }
            Text(model.text("Capture and OCR are local. Translate text sends reviewed text. Send image sends only this region using the selected \(model.translationProvider.displayName) model and account.",
                            "截图和识别在本地进行。“翻译文字”发送确认后的文字。“发送图片翻译”仅通过所选 \(model.translationProvider.displayName) 模型和账号发送此区域。"))
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            HStack(alignment: .top) {
                if capture.busy || capture.submitting {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(statusText)
                }
                ImageCleanupView(model: model)
                Text(statusText)
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if capture.busy || capture.submitting {
                    Button(model.text("Cancel", "取消")) { capture.cancelCurrentAction() }
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
                    Text(model.text("Nothing is sent automatically. No clipboard access.",
                                    "不会自动发送内容，也不会读取剪贴板。"))
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
            Text(model.text("Image translation creates a private temporary PNG of this region. It is removed after the request finishes or drains. History may keep the translated text, not the image.",
                            "图片翻译会为此区域创建私有临时 PNG，并在请求结束或排空后删除。历史记录可能保留翻译文字，但不会保留图片。"))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Button {
                    capture.translate(using: model)
                } label: {
                    Label(model.text("Translate text", "翻译文字"), systemImage: "arrow.right")
                }
                .buttonStyle(.bordered).controlSize(.large)
                .disabled(!capture.canTranslate(using: model) || model.active || model.preparing)
                .accessibilityIdentifier("translate-capture-text")
                Button {
                    capture.translateImage(using: model)
                } label: {
                    Label(model.text("Send image for translation", "发送图片翻译"), systemImage: "photo")
                }
                .buttonStyle(.bordered).controlSize(.large)
                .disabled(!capture.canTranslateImage || model.active || model.preparing)
                .help(model.text("Send the displayed region even if local text recognition found no text or failed.",
                                 "即使本地文字识别没有结果或失败，也可发送显示的截图区域。"))
                Spacer(minLength: 0)
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
                .accessibilityLabel(model.text("Selected screenshot region. Only this image is sent by Send image for translation.",
                                              "所选截图区域。“发送图片翻译”仅发送此图片。"))
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
            NativeTranslationEditor(
                text: $capture.text, textScale: model.nativeTextScale, focused: $editorFocused,
                label: model.text("Reviewed screenshot text to translate", "确认后用于翻译的截图文字"),
                drawsBackground: true
            )
                .disabled(capture.busy)
                .frame(minHeight: 160, maxHeight: .infinity)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(
                    editorFocused ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: 1))
            TranslationInputBudgetView(model: model, text: capture.text)
        }
    }
}
