import AppKit
import Combine
import SwiftUI
import CCTranslateSupport

@main
enum CCTranslateApplication {
    @MainActor
    static func main() {
        if let status = ClipboardReadWorker.runIfRequested() { exit(status) }
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let delegate = AppDelegate()
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}

private class ProductPanel: NSPanel {
    override func cancelOperation(_ sender: Any?) { performClose(sender) }
}

private final class ResultPanel: ProductPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSWindowDelegate, NSMenuItemValidation {
    private var statusItem: NSStatusItem?
    private var inputPanel: NSPanel?
    private(set) var resultPanel: NSPanel?
    private var historyPanel: NSPanel?
    private var settingsPanel: NSPanel?
    private var capturePanel: NSPanel?
    private var diagnosticsPanel: NSPanel?
    private(set) var aboutPanel: NSPanel?
    private var selectionOverlay: RegionSelectionOverlay?
    private weak var captureReturnWindow: NSWindow?
    private weak var captureReturnResponder: NSResponder?
    private var captureReturnApplication: NSRunningApplication?
    private var menuTarget: FocusTarget?
    private let model: ProbeModel
    private let capture: CaptureModel
    private let diagnostics: ProbeModel
    private let aboutModel: AboutModel
    private var terminating = false
    private var showTranslationResults = true
    private var announcement: AnyCancellable?
    private var captureObservation: AnyCancellable?
    var imageCleanupQuitChoice: (@MainActor () -> Bool)?
    var terminationReply: @MainActor (Bool) -> Void = { NSApp.reply(toApplicationShouldTerminate: $0) }
    private var reviewingImageCleanup = false
    private var terminationResolved = false

    override convenience init() {
        self.init(model: ProbeModel(), capture: CaptureModel(), diagnostics: ProbeModel(persistsPreferences: false))
    }

    init(model: ProbeModel, capture: CaptureModel, diagnostics: ProbeModel, about: AboutModel? = nil) {
        self.model = model
        self.capture = capture
        self.diagnostics = diagnostics
        self.aboutModel = about ?? AboutModel()
        super.init()
        model.onStopped = { [weak self] in self?.finishTermination() }
        diagnostics.onStopped = { [weak self] in self?.finishTermination() }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        model.loadPresentation()
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "CC"
        item.button?.toolTip = "CC Translate"
        statusItem = item
        configureMenus()
        model.onSelection = { [weak self] result in
            guard let self = self else { return }
            // Reviewing a capture never opts its text into passive translation.
            guard self.selectionOverlay == nil, self.capturePanel?.isKeyWindow != true else { return }
            if self.model.translatePassiveSelections {
                self.model.translateSelection(result)
            }
        }
        model.onTranslationStarted = { [weak self] in
            guard let self, ["selection", "ocr"].contains(self.model.translationOrigin) else { return }
            self.showTranslationResults = true
            self.showResult(reposition: true)
        }
        model.onTranslationResult = { [weak self] _ in
            guard let self = self, !self.terminating, self.showTranslationResults else { return }
            if ["selection", "ocr"].contains(self.model.translationOrigin) { self.showResult() }
        }
        model.onConfigurationRequired = { [weak self] in self?.openSettings() }
        model.onPresentationChanged = { [weak self] in
            self?.configureMenus()
            self?.applyAppearance()
        }
        announcement = model.$productPhase.dropFirst().removeDuplicates().sink { [weak self] phase in
            DispatchQueue.main.async {
                guard let self, self.model.productPhase == phase, phase != .idle,
                      let window = [self.inputPanel, self.resultPanel].compactMap({ $0 })
                        .first(where: { $0.isVisible }), !self.model.productMessage.isEmpty else { return }
                NSAccessibility.post(element: window, notification: .announcementRequested,
                                     userInfo: [.announcement: self.model.productMessage,
                                                .priority: NSAccessibilityPriorityLevel.medium.rawValue])
            }
        }
        captureObservation = capture.$phase.dropFirst().removeDuplicates().sink { [weak self] phase in
            DispatchQueue.main.async {
                guard let self, !self.terminating, self.capture.phase == phase else { return }
                self.updateCapturePresentation()
            }
        }
        // Default-off launch stays inert; an opt-in hint bootstraps only authoritative config loading.
        model.restorePlainPastePreferenceIfNeeded()
    }

    @discardableResult
    private func add(_ title: String, action: Selector, key: String = "", to menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        menu.addItem(item)
        return item
    }

    func configureMenus() {
        let menu = NSMenu()
        menu.delegate = self
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), key: "n", to: menu)
        add(model.text("Translate selected text", "翻译选中文字"), action: #selector(translateSelection), to: menu)
        add(model.text("Screenshot translation…", "截图翻译…"), action: #selector(startCapture), to: menu)
        add(model.text("Show last result", "显示上次结果"), action: #selector(recallResult), to: menu)
        menu.addItem(.separator())
        add(model.text("Double ⌘C to translate", "双击 ⌘C 翻译"), action: #selector(toggleMonitor), to: menu).tag = 10
        add(model.text("History…", "历史记录…"), action: #selector(openHistory), key: "y", to: menu)
        add(model.text("Settings…", "设置…"), action: #selector(openSettings), key: ",", to: menu)
        menu.addItem(.separator())
        add(model.text("Diagnostics…", "诊断…"), action: #selector(openDiagnostics), to: menu)
        add(model.text("About CC Translate", "关于 CC Translate"), action: #selector(openAbout), to: menu)
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: menu)
        statusItem?.menu = menu

        let main = NSMenu()
        let application = NSMenu()
        add(model.text("About CC Translate", "关于 CC Translate"), action: #selector(openAbout), to: application)
        add(model.text("Settings…", "设置…"), action: #selector(openSettings), key: ",", to: application)
        application.addItem(.separator())
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: application)
        let appItem = NSMenuItem()
        appItem.submenu = application
        main.addItem(appItem)
        let file = NSMenu(title: model.text("File", "文件"))
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), key: "n", to: file)
        add(model.text("Translate text", "翻译当前文字"), action: #selector(submitInput), key: "\r", to: file)
        add(model.text("Screenshot translation…", "截图翻译…"), action: #selector(startCapture), to: file)
        add(model.text("History…", "历史记录…"), action: #selector(openHistory), key: "y", to: file)
        file.addItem(NSMenuItem(title: model.text("Close", "关闭"), action: #selector(NSWindow.performClose(_:)),
                               keyEquivalent: "w"))
        let fileItem = NSMenuItem(title: file.title, action: nil, keyEquivalent: "")
        fileItem.submenu = file
        main.addItem(fileItem)
        let edit = makeEditMenu()
        let editItem = NSMenuItem(title: edit.title, action: nil, keyEquivalent: "")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    func makeEditMenu() -> NSMenu {
        let edit = NSMenu(title: model.text("Edit", "编辑"))
        for (english, chinese, selector, key) in [
            ("Undo", "撤销", "undo:", "z"), ("Cut", "剪切", "cut:", "x"),
            ("Copy", "复制", "copy:", "c"), ("Paste", "粘贴", "paste:", "v"),
            ("Paste and Match Style", "粘贴并匹配样式", "pasteAsPlainText:", "v"),
            ("Select All", "全选", "selectAll:", "a")
        ] {
            let item = NSMenuItem(title: model.text(english, chinese),
                                  action: Selector(selector), keyEquivalent: key)
            if item.action == NativePlainPasteRouting.action {
                item.keyEquivalentModifierMask = [.command, .option, .shift]
            }
            edit.addItem(item)
        }
        return edit
    }

    private func applyAppearance() {
        historyPanel?.title = model.text("History", "历史记录")
        settingsPanel?.title = model.text("Settings", "设置")
        diagnosticsPanel?.title = model.text("Diagnostics", "诊断")
        capturePanel?.title = model.text("Screenshot translation", "截图翻译")
        aboutPanel?.title = model.text("About CC Translate", "关于 CC Translate")
        let appearance: NSAppearance? = model.appearance == "dark" ? NSAppearance(named: .darkAqua) :
            model.appearance == "light" ? NSAppearance(named: .aqua) : nil
        for panel in [inputPanel, resultPanel, settingsPanel, historyPanel, capturePanel, aboutPanel] { panel?.appearance = appearance }
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuTarget = SelectionProbe.currentTarget()
        menu.item(withTag: 10)?.state = model.monitorEnabled ? .on : .off
    }

    @objc private func openInput() {
        if inputPanel == nil {
            let panel = ProductPanel(
                contentRect: NSRect(x: 0, y: 0, width: 940, height: 660),
                styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false
            )
            panel.title = "CC Translate"
            panel.contentMinSize = NSSize(width: 660, height: 540)
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.delegate = self
            panel.contentView = NSHostingView(rootView: TranslatorView(model: model,
                showHistory: { [weak self] in self?.openHistory() },
                showSettings: { [weak self] in self?.openSettings() },
                showCapture: { [weak self] in self?.startCapture() }))
            panel.center()
            inputPanel = panel
        }
        model.openProduct()
        applyAppearance()
        NSApp.activate(ignoringOtherApps: true)
        inputPanel?.makeKeyAndOrderFront(nil)
    }

    @objc private func translateSelection() {
        model.translateSelection(SelectionProbe.read(target: menuTarget))
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if selectionOverlay != nil { return menuItem.action == #selector(quit) }
        if menuItem.action == #selector(submitInput) {
            if capturePanel?.isKeyWindow == true {
                let composing = (capturePanel?.firstResponder as? NSTextView)?.hasMarkedText() ?? false
                return !composing && capture.canTranslate && !model.active && !model.preparing
            }
            let composing = (inputPanel?.firstResponder as? NSTextView)?.hasMarkedText() ?? false
            return inputPanel?.isKeyWindow == true && !composing && !model.active && !model.preparing &&
                !model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
                model.input.utf8.count <= 8192
        }
        return true
    }

    @objc private func submitInput() {
        if capturePanel?.isKeyWindow == true {
            guard (capturePanel?.firstResponder as? NSTextView)?.hasMarkedText() != true else { return }
            capture.translate(using: model)
        } else if inputPanel?.isKeyWindow == true {
            guard (inputPanel?.firstResponder as? NSTextView)?.hasMarkedText() != true else { return }
            model.translate()
        }
    }

    @objc private func startCapture() {
        if (capturePanel == nil || NSApp.keyWindow !== capturePanel) && selectionOverlay == nil {
            captureReturnWindow = NSApp.keyWindow
            captureReturnResponder = NSApp.keyWindow?.firstResponder
            let application = NSWorkspace.shared.frontmostApplication
            captureReturnApplication = application?.processIdentifier == ProcessInfo.processInfo.processIdentifier
                ? nil : application
        }
        selectionOverlay?.dismiss()
        discardCapturePanel()
        capture.start()
        ensureCapturePanel()
        activate(capturePanel)
    }

    private func ensureCapturePanel() {
        guard capturePanel == nil else { return }
        capturePanel = makePanel(title: model.text("Screenshot translation", "截图翻译"),
            width: 860, height: 680, minimum: NSSize(width: 620, height: 600),
            root: CaptureView(capture: capture, model: model,
                captureAgain: { [weak self] in self?.startCapture() },
                reselect: { [weak self] in self?.capture.reselect() },
                close: { [weak self] in self?.capturePanel?.performClose(nil) }))
    }

    private func updateCapturePresentation() {
        switch capture.phase {
        case .selecting:
            let appearance = capturePanel?.appearance
            discardCapturePanel()
            let overlay = RegionSelectionOverlay(text: { [weak self] english, chinese in
                self?.model.text(english, chinese) ?? english
            })
            selectionOverlay?.dismiss()
            selectionOverlay = overlay
            overlay.present(frames: capture.frames, appearance: appearance) { [weak self] outcome in
                guard let self else { return }
                self.selectionOverlay = nil
                switch outcome {
                case .selected(let rectangle): self.capture.select(rectangle)
                case .cancelled: self.capture.cancel()
                case .pending, .tooSmall: break
                }
            }
        case .cancelled, .idle:
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            discardCapturePanel()
            restoreCaptureFocus()
        case .capturing, .recognizing, .ready, .empty, .failed:
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            let shouldActivate = capturePanel?.isVisible != true || capturePanel?.isKeyWindow == true
            ensureCapturePanel()
            if shouldActivate { activate(capturePanel) }
            else { applyAppearance() }
            if capture.phase == .ready || capture.phase == .empty || capture.phase == .failed,
               let capturePanel, capturePanel.isKeyWindow {
                NSAccessibility.post(element: capturePanel, notification: .announcementRequested,
                    userInfo: [.announcement: capture.message(using: model),
                               .priority: NSAccessibilityPriorityLevel.medium.rawValue])
            }
        }
    }

    private func restoreCaptureFocus() {
        if let application = captureReturnApplication, !application.isTerminated {
            application.activate(options: [.activateIgnoringOtherApps])
            return
        }
        guard let window = captureReturnWindow, window.isVisible else { return }
        window.makeKeyAndOrderFront(nil)
        if let responder = captureReturnResponder { window.makeFirstResponder(responder) }
    }

    private func discardCapturePanel() {
        let panel = capturePanel
        capturePanel = nil
        panel?.delegate = nil
        panel?.contentView = nil
        panel?.orderOut(nil)
        panel?.close()
    }

    @objc private func toggleMonitor() {
        if model.monitorEnabled {
            model.stopMonitor()
            return
        }
        model.translatePassiveSelections = true
        model.startMonitor()
        if !model.monitorEnabled { openSettings() }
    }

    @objc private func openHistory() {
        if historyPanel == nil {
            historyPanel = makePanel(title: model.text("History", "历史记录"), width: 900, height: 600,
                minimum: NSSize(width: 640, height: 440),
                root: TranslationHistoryView(model: model, useEntry: { [weak self] in self?.openInput() }))
        }
        model.loadHistory()
        activate(historyPanel)
    }

    @objc private func openSettings() {
        if settingsPanel == nil {
            settingsPanel = makePanel(title: model.text("Settings", "设置"), width: 660, height: 650,
                minimum: NSSize(width: 530, height: 460),
                root: settingsContent())
        }
        model.openProduct()
        activate(settingsPanel)
    }

    func settingsContent() -> TranslationSettingsView {
        TranslationSettingsView(model: model,
            showDiagnostics: { [weak self] in self?.openDiagnostics() },
            showAbout: { [weak self] in self?.openAbout() })
    }

    @objc private func openDiagnostics() {
        if diagnosticsPanel == nil {
            diagnosticsPanel = makePanel(title: model.text("Diagnostics", "诊断"), width: 760, height: 600,
                                         minimum: NSSize(width: 700, height: 560),
                                         root: ProbeView(model: diagnostics))
        }
        diagnostics.refreshPermissions()
        activate(diagnosticsPanel)
    }

    private func makePanel<Content: View>(title: String, width: CGFloat, height: CGFloat,
                                          minimum: NSSize, root: Content) -> NSPanel {
        let panel = ProductPanel(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        panel.contentMinSize = minimum
        panel.title = title
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.delegate = self
        panel.contentView = NSHostingView(rootView: root)
        panel.center()
        return panel
    }

    private func activate(_ panel: NSPanel?) {
        applyAppearance()
        NSApp.activate(ignoringOtherApps: true)
        panel?.makeKeyAndOrderFront(nil)
    }

    @objc private func recallResult() {
        if model.output.isEmpty { openInput() } else { showResult(reposition: true) }
    }
    @objc func openAbout() {
        if aboutPanel == nil {
            aboutPanel = makePanel(title: model.text("About CC Translate", "关于 CC Translate"),
                width: 760, height: 660, minimum: NSSize(width: 660, height: 520),
                root: AboutView(model: aboutModel, presentation: model,
                                close: { [weak self] in self?.aboutPanel?.performClose(nil) }))
            aboutModel.openResources()
        }
        activate(aboutPanel)
    }
    @objc private func quit() { NSApp.terminate(nil) }

    func showResult(reposition: Bool = false) {
        if resultPanel == nil {
            let panel = ResultPanel(
                contentRect: NSRect(x: 0, y: 0, width: 590, height: 400),
                styleMask: [.nonactivatingPanel, .titled, .closable, .resizable],
                backing: .buffered, defer: false
            )
            panel.title = "CC Translate"
            panel.contentMinSize = NSSize(width: 420, height: 300)
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.isFloatingPanel = true
            panel.becomesKeyOnlyIfNeeded = true
            panel.level = .floating
            panel.delegate = self
            panel.contentView = NSHostingView(rootView: TranslationResultView(model: model, compact: true))
            panel.center()
            resultPanel = panel
        }
        if let panel = resultPanel, reposition || !panel.isVisible {
            if let frame = model.resultPlacement.frame(
                current: panel.frame, remembered: model.rememberedResultFrame, pointer: NSEvent.mouseLocation,
                visibleScreens: NSScreen.screens.map(\.visibleFrame)) {
                panel.setFrame(frame, display: false)
            }
        }
        applyAppearance()
        resultPanel?.orderFrontRegardless()
    }

    func windowDidMove(_ notification: Notification) {
        rememberResultWindow(notification)
    }

    func windowDidResize(_ notification: Notification) {
        rememberResultWindow(notification)
    }

    private func rememberResultWindow(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === resultPanel else { return }
        model.rememberResultFrame(window.frame)
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        if window === aboutPanel {
            aboutModel.close()
            aboutPanel?.contentView = nil
            aboutPanel = nil
        }
        if window === inputPanel {
            if model.translationOrigin == "text" { model.cancel() }
        }
        if window === resultPanel {
            model.rememberResultFrame(window.frame)
            showTranslationResults = false
            if ["selection", "ocr"].contains(model.translationOrigin) { model.cancel() }
        }
        if window === capturePanel {
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            capture.cancel()
            capturePanel?.contentView = nil
            capturePanel = nil
        }
        if window === diagnosticsPanel {
            diagnostics.stopMonitor()
            diagnostics.closePanel()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        terminationResolved = false
        aboutModel.close()
        selectionOverlay?.dismiss()
        selectionOverlay = nil
        capture.cancel()
        discardCapturePanel()
        let waitForProcesses = model.hasProcesses || diagnostics.hasProcesses
        model.prepareToQuit()
        diagnostics.prepareToQuit()
        if waitForProcesses || model.hasProcesses || diagnostics.hasProcesses { return .terminateLater }
        if !allowQuitAfterImageCleanup() { return .terminateLater }
        terminationResolved = true
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        aboutModel.close()
        selectionOverlay?.dismiss()
        capture.cancel()
        discardCapturePanel()
        model.prepareToQuit()
        diagnostics.prepareToQuit()
    }

    private func finishTermination() {
        if terminating && !terminationResolved && !model.hasProcesses &&
            !diagnostics.hasProcesses && !reviewingImageCleanup {
            if allowQuitAfterImageCleanup() {
                terminationResolved = true
                terminationReply(true)
            }
        }
    }

    private func allowQuitAfterImageCleanup() -> Bool {
        guard model.imageTranslation.cleanupFailureCount > 0 else { return true }
        reviewingImageCleanup = true
        let quit: Bool
        if let imageCleanupQuitChoice {
            quit = imageCleanupQuitChoice()
        } else {
            let alert = NSAlert()
            alert.messageText = model.text("A temporary image could not be removed", "无法删除临时图片")
            alert.informativeText = model.text("Retry removal, or quit knowing the image may remain on this Mac.",
                                               "请重试删除，或确认图片可能仍保留在此 Mac 上后退出。")
            alert.addButton(withTitle: model.text("Retry cleanup", "重试清理"))
            alert.addButton(withTitle: model.text("Quit anyway", "仍然退出"))
            quit = alert.runModal() == .alertSecondButtonReturn
        }
        reviewingImageCleanup = false
        if !quit { model.imageTranslation.retryCleanup() }
        return quit
    }
}
