import SwiftUI

@MainActor
struct ImageCleanupView: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        if model.imageTranslation.cleanupFailureCount > 0 || model.imageTranslation.cleaning {
            VStack(alignment: .leading, spacing: 6) {
                if model.imageTranslation.cleaning {
                    HStack {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel(model.text("Removing temporary image", "正在删除临时图片"))
                        Text(model.text("Removing temporary image…", "正在删除临时图片…")).font(.callout)
                    }
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        Label(model.text("Temporary image cleanup failed", "临时图片清理失败"),
                              systemImage: "exclamationmark.triangle")
                            .font(.callout).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 8)
                        Button(model.text("Retry image cleanup", "重试清理图片")) {
                            model.imageTranslation.retryCleanup()
                        }
                        .font(.body).controlSize(.large)
                        .fixedSize(horizontal: true, vertical: false)
                        .disabled(model.imageTranslation.working)
                    }
                    Text(model.text("The image may remain on this Mac.", "图片可能仍保留在此 Mac 上。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .textSelection(.enabled)
        }
    }
}
