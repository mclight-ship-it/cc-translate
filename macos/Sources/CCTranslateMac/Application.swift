import AppKit
import Combine
import SwiftUI
import CCTranslateSupport

@main
enum CCTranslateApplication {
    @MainActor
    static func main() {
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
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem?
    private var inputPanel: NSPanel?
    private var resultPanel: NSPanel?
    private var historyPanel: NSPanel?
    private var settingsPanel: NSPanel?
    private var diagnosticsPanel: NSPanel?
    private var menuTarget: FocusTarget?
    private let model = ProbeModel()
    private let diagnostics = ProbeModel(persistsPreferences: false)
    private var terminating = false
    private var showTranslationResults = true
    private var announcement: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        model.loadPresentation()
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "CC"
        item.button?.toolTip = "CC Translate"
        statusItem = item
        configureMenus()
        model.onSelection = { [weak self] result in
            guard let self = self else { return }
            if self.model.translatePassiveSelections {
                self.model.translateSelection(result)
            }
        }
        model.onTranslationStarted = { [weak self] in
            guard let self, self.model.translationOrigin == "selection" else { return }
            self.showTranslationResults = true
            self.showResult()
        }
        model.onTranslationResult = { [weak self] _ in
            guard let self = self, !self.terminating, self.showTranslationResults else { return }
            if self.model.translationOrigin == "selection" { self.showResult() }
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
        model.onStopped = { [weak self] in self?.finishTermination() }
        diagnostics.onStopped = { [weak self] in self?.finishTermination() }
        // Intentionally no window, helper, TCC check, event monitor, or network at launch.
    }

    @discardableResult
    private func add(_ title: String, action: Selector, key: String = "", to menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        menu.addItem(item)
        return item
    }

    private func configureMenus() {
        let menu = NSMenu()
        menu.delegate = self
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), key: "n", to: menu)
        add(model.text("Translate selected text", "翻译选中文字"), action: #selector(translateSelection), to: menu)
        add(model.text("Show last result", "显示上次结果"), action: #selector(recallResult), to: menu)
        menu.addItem(.separator())
        add(model.text("Double ⌘C to translate", "双击 ⌘C 翻译"), action: #selector(toggleMonitor), to: menu).tag = 10
        add(model.text("History…", "历史记录…"), action: #selector(openHistory), key: "y", to: menu)
        add(model.text("Settings…", "设置…"), action: #selector(openSettings), key: ",", to: menu)
        menu.addItem(.separator())
        add(model.text("Diagnostics…", "诊断…"), action: #selector(openDiagnostics), to: menu)
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: menu)
        statusItem?.menu = menu

        let main = NSMenu()
        let application = NSMenu()
        add(model.text("About CC Translate", "关于 CC Translate"), action: #selector(about), to: application)
        add(model.text("Settings…", "设置…"), action: #selector(openSettings), key: ",", to: application)
        application.addItem(.separator())
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: application)
        let appItem = NSMenuItem()
        appItem.submenu = application
        main.addItem(appItem)
        let file = NSMenu(title: model.text("File", "文件"))
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), key: "n", to: file)
        add(model.text("History…", "历史记录…"), action: #selector(openHistory), key: "y", to: file)
        file.addItem(NSMenuItem(title: model.text("Close", "关闭"), action: #selector(NSWindow.performClose(_:)),
                               keyEquivalent: "w"))
        let fileItem = NSMenuItem(title: file.title, action: nil, keyEquivalent: "")
        fileItem.submenu = file
        main.addItem(fileItem)
        let edit = NSMenu(title: model.text("Edit", "编辑"))
        for (english, chinese, selector, key) in [
            ("Undo", "撤销", "undo:", "z"), ("Cut", "剪切", "cut:", "x"),
            ("Copy", "复制", "copy:", "c"), ("Paste", "粘贴", "paste:", "v"),
            ("Select All", "全选", "selectAll:", "a")
        ] {
            edit.addItem(NSMenuItem(title: model.text(english, chinese),
                                   action: Selector(selector), keyEquivalent: key))
        }
        let editItem = NSMenuItem(title: edit.title, action: nil, keyEquivalent: "")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    private func applyAppearance() {
        historyPanel?.title = model.text("History", "历史记录")
        settingsPanel?.title = model.text("Settings", "设置")
        diagnosticsPanel?.title = model.text("Diagnostics", "诊断")
        let appearance: NSAppearance? = model.appearance == "dark" ? NSAppearance(named: .darkAqua) :
            model.appearance == "light" ? NSAppearance(named: .aqua) : nil
        for panel in [inputPanel, resultPanel, settingsPanel, historyPanel] { panel?.appearance = appearance }
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
            panel.contentMinSize = NSSize(width: 660, height: 480)
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.delegate = self
            panel.contentView = NSHostingView(rootView: TranslatorView(model: model,
                showHistory: { [weak self] in self?.openHistory() },
                showSettings: { [weak self] in self?.openSettings() }))
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
                root: TranslationSettingsView(model: model,
                    showDiagnostics: { [weak self] in self?.openDiagnostics() }))
        }
        model.openProduct()
        activate(settingsPanel)
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
        if model.output.isEmpty { openInput() } else { showResult() }
    }
    @objc private func about() { NSApp.orderFrontStandardAboutPanel(nil) }
    @objc private func quit() { NSApp.terminate(nil) }

    private func showResult() {
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
        applyAppearance()
        resultPanel?.orderFrontRegardless()
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        if window === inputPanel {
            if model.translationOrigin == "text" { model.cancel() }
        }
        if window === resultPanel {
            showTranslationResults = false
            if model.translationOrigin == "selection" { model.cancel() }
        }
        if window === diagnosticsPanel {
            diagnostics.stopMonitor()
            diagnostics.closePanel()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        let waitForProcesses = model.hasProcesses || diagnostics.hasProcesses
        model.prepareToQuit()
        diagnostics.prepareToQuit()
        return waitForProcesses ? .terminateLater : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        model.prepareToQuit()
        diagnostics.prepareToQuit()
    }

    private func finishTermination() {
        if terminating && !model.hasProcesses && !diagnostics.hasProcesses {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
    }
}
