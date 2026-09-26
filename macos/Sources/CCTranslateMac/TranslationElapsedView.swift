import SwiftUI

@MainActor
struct TranslationElapsedView: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        if model.translationElapsedSeconds != nil {
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                if let seconds = model.translationElapsedSeconds {
                    Text(model.text("Elapsed: \(seconds)s", "已用时：\(seconds) 秒"))
                        .font(.caption).monospacedDigit()
                        .foregroundStyle(PearlTheme.secondary)
                        .accessibilityIdentifier("translation-elapsed")
                }
            }
        } else if let timing = model.completedTranslationTiming {
            NativeSettingsDisclosure(model.text("Timing details", "耗时详情"), model: model,
                                     identifier: "translation-timing-details") {
                VStack(alignment: .leading, spacing: 4) {
                    if let first = timing.firstReadableText {
                        Text(model.text("First readable text: \(seconds(first))s",
                                        "首段可读文字：\(seconds(first)) 秒"))
                    }
                    if let summary = timing.summaryComplete {
                        Text(model.text("Summary complete: \(seconds(summary))s",
                                        "摘要完成：\(seconds(summary)) 秒"))
                    }
                    Text(model.text("Total: \(seconds(timing.total))s", "总计：\(seconds(timing.total)) 秒"))
                    Text(model.text("Measured from translation preparation in this app, not server processing time.",
                                    "从本应用开始准备翻译时计时，并非服务器处理耗时。"))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.caption).monospacedDigit()
                .foregroundStyle(PearlTheme.secondary)
            }
            .id(timing.intent)
        }
    }

    private func seconds(_ value: TimeInterval) -> String { String(format: "%.1f", value) }
}
