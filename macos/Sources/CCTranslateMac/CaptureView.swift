import AppKit
import SwiftUI
import CCTranslateSupport

@MainActor
struct CaptureStatusView: View {
    @ObservedObject var capture: CaptureModel
    @ObservedObject var model: ProbeModel
    var captureAgain: () -> Void
    var close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                if capture.busy {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(model.text("Recognizing text", "正在识别文字"))
                }
                ScrollView {
                    Text(capture.message(using: model))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
            Spacer(minLength: 0)
            HStack {
                Spacer()
                if !capture.busy {
                    Button(model.text("Capture again", "重新截图"), action: captureAgain)
                        .accessibilityIdentifier("automatic-capture-retry")
                }
                Button(model.text(capture.busy ? "Cancel" : "Close", capture.busy ? "取消" : "关闭"),
                       action: close)
                    .keyboardShortcut(.cancelAction)
                    .accessibilityIdentifier("automatic-capture-close")
            }
        }
        .padding(20)
        .pearlSurface()
        .preferredColorScheme(model.preferredColorScheme)
    }
}

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
        VStack(alignment: .leading, spacing: PearlTheme.spacing) {
            HStack {
                Label(model.text("Screenshot translation", "截图翻译"), systemImage: "viewfinder")
                    .font(.title2.bold()).accessibilityAddTraits(.isHeader)
                Spacer()
                Button(model.text("Close", "关闭"), action: close)
            }
            Text(model.text("Text recognition stays on this Mac. Choose whether to send the text or this image to \(model.translationProvider.displayName).",
                            "文字识别在本机完成。你可以选择向 \(model.translationProvider.displayName) 发送文字或这张图片。"))
                .font(.callout).foregroundStyle(PearlTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                ImageCleanupView(model: model)
                HStack(alignment: .top) {
                    if capture.busy || capture.submitting {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel(statusText)
                    }
                    Text(statusText)
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    if capture.busy || capture.submitting {
                        Button(model.text("Cancel", "取消")) { capture.cancelCurrentAction() }
                    }
                }
            }
            .padding(12)
            .pearlCard(inset: true)
            if let preview = capture.preview {
                GeometryReader { viewport in
                    ScrollViewReader { scroll in
                        ScrollView {
                            if viewport.size.width >= 670 {
                                HStack(alignment: .top, spacing: PearlTheme.spacing) {
                                    previewImage(preview).frame(minWidth: 280, maxWidth: .infinity)
                                    editor.id("reviewed-capture-text").frame(minWidth: 350, maxWidth: .infinity)
                                }
                                .frame(height: 320)
                            } else {
                                VStack(spacing: 12) {
                                    previewImage(preview).frame(height: 170)
                                    editor.id("reviewed-capture-text").frame(height: 260)
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
                        .font(.system(size: 36)).foregroundStyle(PearlTheme.secondary).accessibilityHidden(true)
                    Text(model.text("Select a region, review the text, then translate.", "选择区域、确认文字，然后翻译。"))
                        .font(.headline)
                    Text(model.text("Nothing is sent automatically. No clipboard access.",
                                    "不会自动发送内容，也不会读取剪贴板。"))
                        .font(.callout).foregroundStyle(PearlTheme.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .pearlCard(inset: true)
            }
            HStack {
                Button(model.text("Capture again", "重新截图"), action: captureAgain)
                Button(model.text("Reselect region", "重选区域"), action: reselect)
                    .disabled(capture.frames.isEmpty || capture.phase == .capturing)
                    .help(model.text("Choose another region from the same capture.", "在同一次截图中选择其他区域。"))
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
            Text(model.text("History saves translated text, not screenshots. Temporary images are removed after use.",
                            "历史记录只保存译文，不保存截图；临时图片会在使用后删除。"))
                .font(.caption).foregroundStyle(PearlTheme.secondary).fixedSize(horizontal: false, vertical: true)
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
        .padding(PearlTheme.pagePadding)
        .frame(minWidth: 620, minHeight: 600)
        .pearlSurface()
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
                .accessibilityAddTraits(.isHeader)
            Image(nsImage: image).resizable().scaledToFit()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(model.text("Selected screenshot region. Only this image is sent by Send image for translation.",
                                              "所选截图区域。“发送图片翻译”仅发送此图片。"))
                .help(model.text("Multiple displays are captured one after another.", "多个屏幕会依次截图。"))
        }
        .padding(12)
        .pearlCard(inset: true)
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
                .clipShape(RoundedRectangle(cornerRadius: PearlTheme.controlRadius))
                .overlay(RoundedRectangle(cornerRadius: PearlTheme.controlRadius).strokeBorder(
                    editorFocused ? PearlTheme.accent : PearlTheme.border, lineWidth: editorFocused ? 2 : 1))
            TranslationInputBudgetView(model: model, text: capture.text)
        }
        .padding(12)
        .pearlCard()
    }
}
