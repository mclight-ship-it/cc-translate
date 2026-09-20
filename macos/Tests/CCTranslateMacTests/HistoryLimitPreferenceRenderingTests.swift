import AppKit
import SwiftUI
import Vision
import XCTest
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private struct HistoryLimitSettingsSurface: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Form {
            Section {
                HistoryLimitSettingsView(model: model)
            } header: {
                Text(model.text("History retention", "历史保留条数"))
            }
        }
        .formStyle(.grouped)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(model.preferredColorScheme)
    }
}

@MainActor
private enum HistoryLimitControls {
    static func views<T: NSView>(_ type: T.Type, in root: NSView) -> [T] {
        (root as? T).map { [$0] } ?? root.subviews.flatMap { views(type, in: $0) }
    }

    static func field(in root: NSView) throws -> NSTextField {
        let fields = views(NSTextField.self, in: root).filter { $0.isEditable }
        XCTAssertEqual(fields.count, 1)
        return try XCTUnwrap(fields.count == 1 ? fields.first : nil)
    }

    static func button(_ id: String, title: String, in root: NSView,
                       kind: NativeRenderedControlKind = .button) throws -> NativeSettingsTestControl {
        try NativeSettingsTestControls.resolve(in: root, identifier: id, label: title, kind: kind)
    }
}

@MainActor
private final class HistoryLimitSettingsHost {
    private let windowFocus = NativeTestWindowFocus()
    let host: NSHostingView<HistoryLimitSettingsSurface>
    let window: NSWindow
    let model: ProbeModel

    init(_ model: ProbeModel) {
        _ = NSApplication.shared
        self.model = model
        host = NSHostingView(rootView: HistoryLimitSettingsSurface(model: model))
        let size = NSSize(width: 640, height: 680)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .titled,
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.frame = NSRect(origin: .zero, size: size)
        window.makeKeyAndOrderFront(nil)
        flush()
    }

    func flush() {
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
    }

    func waitFor(file: StaticString = #filePath, line: UInt = #line,
                 _ condition: @MainActor () -> Bool) async throws {
        try await CaptureProductFixture.waitFor(file: file, line: line, diagnostics: {
            "History saved=\(String(describing: self.model.historyLimit.saved)), " +
                "draft=\(self.model.historyLimit.draft), confirmation=\(String(describing: self.model.historyLimit.confirmation)), " +
                "phase=\(self.model.historyLimit.phase), busy=\(self.model.settingsBusy)"
        }) {
            self.flush()
            return condition()
        }
    }

    func enter(_ text: String) async throws {
        flush()
        let field = try HistoryLimitControls.field(in: host)
        XCTAssertTrue(window.makeFirstResponder(field))
        let editor = try XCTUnwrap(field.currentEditor() as? NSTextView)
        XCTAssertTrue(window.firstResponder === editor)
        editor.selectAll(nil)
        editor.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertTrue(window.makeFirstResponder(nil))
        try await waitFor { self.model.historyLimit.draft == text }
    }

    func button(_ id: String, _ english: String, _ chinese: String,
                kind: NativeRenderedControlKind = .button) async throws -> NativeSettingsTestControl {
        flush()
        return try await NativeSettingsTestControls.resolveWhenReady(
            in: host, identifier: id, label: model.text(english, chinese), kind: kind)
    }

    func close() {
        windowFocus.close(window)
    }
}

final class HistoryLimitPreferenceInteractionTests: XCTestCase {
    @MainActor
    func testNativeInputApplyReductionCancelConfirmAndReopenInBothLanguagesWithoutCLI() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready(configuration: ProductTestHarness.configuration(history: false, historyLimit: 600))
            f.model.interfaceLanguage = language
            let surface = HistoryLimitSettingsHost(f.model)
            defer { surface.close() }
            let initialOperations = helper.operations
            XCTAssertEqual(try HistoryLimitControls.field(in: surface.host).stringValue, "600")
            try await surface.enter("10001")
            let invalidApply = try await surface.button("apply-history-limit", "Apply limit", "应用条数")
            XCTAssertFalse(invalidApply.isEnabled)
            XCTAssertEqual(helper.operations, initialOperations)
            try await surface.enter("17")
            let apply = try await surface.button("apply-history-limit", "Apply limit", "应用条数")
            XCTAssertTrue(apply.isEnabled)
            try await apply.press()
            try await surface.waitFor {
                f.model.historyLimit.confirmation == HistoryLimitPreference.Reduction(from: 600, to: 17)
            }
            XCTAssertEqual(helper.operations, initialOperations)
            let cancel = try await surface.button("cancel-history-limit-reduction", "Cancel", "取消")
            XCTAssertFalse(cancel.hasDestructiveAction)
            try await cancel.press()
            try await surface.waitFor { f.model.historyLimit.confirmation == nil && apply.isEnabled }
            XCTAssertEqual(helper.operations, initialOperations)
            XCTAssertEqual(f.model.historyLimit.saved, 600)
            XCTAssertEqual(f.model.historyLimit.draft, "17")
            try await apply.press()
            try await surface.waitFor { f.model.historyLimit.confirmation != nil }
            let confirm = try await surface.button("confirm-history-limit-reduction", "Reduce and save", "降低并保存",
                                                   kind: .destructiveButton)
            XCTAssertTrue(confirm.isEnabled)
            XCTAssertTrue(confirm.hasDestructiveAction)
            try await confirm.press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            XCTAssertEqual(helper.configurationSaves.last?.config["history_limit"], .integer(17))
            XCTAssertEqual(f.model.historyLimit.saved, 600)
            try HistoryLimitFixture.finish(f, helper: helper)
            try await surface.waitFor { f.model.historyLimit.saved == 17 && f.model.canEditHistoryLimit }
            XCTAssertEqual(f.model.historyLimit.phase, .saved)
            XCTAssertEqual(try HistoryLimitControls.field(in: surface.host).stringValue, "17")
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.historyClears.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
            XCTAssertTrue(helper.messages.isEmpty)

            let saved = try XCTUnwrap(helper.configurationSaves.last?.config)
            f.model.stopHelper()
            helper.stopped()
            f.model.openProduct()
            let reopened = try f.ready(configuration: saved)
            try await surface.waitFor { f.model.canEditHistoryLimit }
            XCTAssertEqual(try HistoryLimitControls.field(in: surface.host).stringValue, "17")
            XCTAssertTrue(reopened.configurationSaves.isEmpty)
            XCTAssertTrue(reopened.historyLoads.isEmpty)
        }
    }

    @MainActor
    func testNativeFailedSaveReloadAndMismatchedReadbackPreserveDraftWithoutImplicitRetry() async throws {
        for language in ["en", "zh"] {
            let f = try ProductTestHarness(savedCLI: false)
            defer { f.cleanUp() }
            let helper = try f.ready()
            f.model.interfaceLanguage = language
            let surface = HistoryLimitSettingsHost(f.model)
            defer { surface.close() }
            try await surface.enter("600")
            try await surface.button("apply-history-limit", "Apply limit", "应用条数").press()
            try await surface.waitFor { helper.configurationSaves.count == 1 }
            helper.event("failed", id: try XCTUnwrap(helper.configurationSaves.last?.id),
                         payload: ["code": .string("config_io_failed")])
            try await surface.waitFor { f.model.historyLimit.saved == nil && !f.model.settingsBusy }
            let unavailableApply = try await surface.button("apply-history-limit", "Apply limit", "应用条数")
            XCTAssertFalse(unavailableApply.isEnabled)
            let reload = try await surface.button("reload-history-limit", "Reload saved history limit", "重新读取已保存条数")
            XCTAssertTrue(reload.isEnabled)
            try await reload.press()
            try await surface.waitFor { helper.configurationLoads.count == 2 }
            try f.finishConfiguration(on: helper)
            try await surface.waitFor { f.model.canEditHistoryLimit }
            XCTAssertEqual(f.model.historyLimit.saved, 100)
            XCTAssertEqual(try HistoryLimitControls.field(in: surface.host).stringValue, "600")
            XCTAssertEqual(helper.configurationSaves.count, 1)
            try await surface.button("apply-history-limit", "Apply limit", "应用条数").press()
            try await surface.waitFor { helper.configurationSaves.count == 2 }
            helper.event("completed", id: try XCTUnwrap(helper.configurationSaves.last?.id))
            try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(historyLimit: 17))
            try await surface.waitFor { f.model.historyLimit.phase == .differentReadback }
            XCTAssertEqual(f.model.historyLimit.saved, 17)
            XCTAssertEqual(try HistoryLimitControls.field(in: surface.host).stringValue, "600")
            XCTAssertEqual(helper.configurationSaves.count, 2)
            XCTAssertEqual(helper.configurationLoads.count, 3)
            XCTAssertTrue(helper.historyLoads.isEmpty)
            XCTAssertTrue(helper.translations.isEmpty)
        }
    }
}

extension ProductRenderingTests {
    @MainActor
    func testHistoryRetentionRendersEnglishReductionAndChineseUnsupportedSavedValueWithDeletionDisclosure() throws {
        let f = try ProductTestHarness(savedCLI: false)
        defer { f.cleanUp() }
        let helper = try f.ready(configuration: ProductTestHarness.configuration(historyLimit: 600))
        f.model.interfaceLanguage = "en"
        f.model.editHistoryLimit("17")
        f.model.applyHistoryLimit()
        let english = try render(HistoryLimitSettingsSurface(model: f.model),
                                 named: "history-limit-reduction-en-light",
                                 size: NSSize(width: 640, height: 680), scheme: .light, inspect: { host in
            let confirm = try HistoryLimitControls.button("confirm-history-limit-reduction", title: "Reduce and save",
                                                          in: host, kind: .destructiveButton)
            let cancel = try HistoryLimitControls.button("cancel-history-limit-reduction", title: "Cancel", in: host)
            XCTAssertTrue(confirm.isEnabled)
            XCTAssertTrue(confirm.hasDestructiveAction)
            XCTAssertTrue(cancel.isEnabled)
            XCTAssertFalse(cancel.hasDestructiveAction)
            XCTAssertGreaterThan(confirm.visibleRect.height, 0)
            XCTAssertGreaterThan(cancel.visibleRect.height, 0)
            XCTAssertEqual(confirm.visibleRect.height, confirm.frame.height, accuracy: 1)
            XCTAssertEqual(cancel.visibleRect.height, cancel.frame.height, accuracy: 1)
        })
        let words = try historyLimitWords(english, chinese: false)
        for expected in ["600", "17", "olderrecords", "nextactualhistoryaddition", "notwhenyouconfirm", "doesnotrestore"] {
            XCTAssertTrue(words.contains(expected), words)
        }
        f.model.cancelHistoryLimitReduction()
        f.model.reloadHistoryLimit()
        try f.finishConfiguration(on: helper, configuration: ProductTestHarness.configuration(historyLimit: 10_001))
        f.model.interfaceLanguage = "zh"
        f.model.appearance = "dark"
        let chinese = try render(HistoryLimitSettingsSurface(model: f.model),
                                 named: "history-limit-unsupported-zh-dark",
                                 size: NSSize(width: 640, height: 680), scheme: .dark, inspect: { host in
            let field = try HistoryLimitControls.field(in: host)
            XCTAssertTrue(field.isEnabled)
            XCTAssertEqual(field.stringValue, "17", "An unrelated readback preserves the unsaved entry.")
            let apply = try HistoryLimitControls.button("apply-history-limit", title: "应用条数", in: host)
            XCTAssertTrue(apply.isEnabled)
            XCTAssertGreaterThan(apply.visibleRect.height, 0)
            XCTAssertEqual(apply.visibleRect.height, apply.frame.height, accuracy: 1)
        })
        let chineseWords = try historyLimitWords(chinese, chinese: true)
        for expected in ["10001", "尚未改动", "保存下一条翻译记录", "移除超出条数", "不会恢复"] {
            XCTAssertTrue(chineseWords.contains(expected), chineseWords)
        }
        XCTAssertTrue(helper.configurationSaves.isEmpty)
        XCTAssertTrue(helper.historyLoads.isEmpty)
        XCTAssertTrue(helper.historyClears.isEmpty)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    private func historyLimitWords(_ png: Data, chinese: Bool) throws -> String {
        let image = try XCTUnwrap(NSBitmapImageRep(data: png)?.cgImage)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = chinese ? ["zh-Hans", "en-US"] : ["en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([request])
        return try XCTUnwrap(request.results).compactMap { $0.topCandidates(1).first?.string }
            .joined().lowercased().filter { !$0.isWhitespace }
    }
}
