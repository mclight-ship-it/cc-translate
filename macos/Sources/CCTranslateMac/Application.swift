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
        configureNormalApplication(application)
        let delegate = AppDelegate()
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }

    @MainActor
    static func configureNormalApplication(_ application: NSApplication) {
        // Product windows promote the normal app later; the clipboard worker never does.
        application.setActivationPolicy(.accessory)
    }
}

private class ProductPanel: NSPanel {
    override var canBecomeMain: Bool { true }
    override func cancelOperation(_ sender: Any?) { performClose(sender) }
}

private final class ResultPanel: ProductPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

private final class TextMenuItem: NSMenuItem {
    // Tahoe can supply an action image even when none was assigned to the item.
    override var image: NSImage? {
        get { nil }
        set { super.image = nil }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSWindowDelegate, NSMenuItemValidation {
    private(set) var statusItem: NSStatusItem?
    private(set) var inputPanel: NSPanel?
    private(set) var resultPanel: NSPanel?
    private(set) var quickInputPanel: NSPanel?
    var historyPanel: NSPanel? { activeWorkspacePanel(.history) }
    var dictionaryPanel: NSPanel? { activeWorkspacePanel(.dictionary) }
    var settingsPanel: NSPanel? { activeWorkspacePanel(.settings) }
    private(set) var capturePanel: NSPanel?
    private var diagnosticsPanel: NSPanel?
    var aboutPanel: NSPanel? { activeWorkspacePanel(.about) }
    private(set) var updatesPanel: NSPanel?
    private var selectionOverlay: RegionSelectionOverlay?
    private weak var captureReturnWindow: NSWindow?
    private weak var captureReturnResponder: NSResponder?
    private var captureReturnApplication: NSRunningApplication?
    private var captureReturnIntent: UUID?
    private var menuTarget: FocusTarget?
    private let model: ProbeModel
    private let capture: CaptureModel
    private let diagnostics: ProbeModel
    private let aboutModel: AboutModel
    private let loginItems: LoginItemModel
    private let updates: AppUpdateModel
    private let uninstaller: any AppUninstallServing
    private var uninstallPreparing = false
    private var pendingUninstall: (locations: AppUninstallLocations, includingData: Bool)?
    var terminateApplication: @MainActor () -> Void = { NSApp.terminate(nil) }
    var uninstallNotice: (@MainActor (String) -> Void)?
    var uninstallOutcome: (@MainActor (AppUninstallOutcome) -> Void)?
    private var terminating = false
    private var showTranslationResults = true
    private var announcement: AnyCancellable?
    private var dictionaryAnnouncement: AnyCancellable?
    private var captureObservation: AnyCancellable?
    var imageCleanupQuitChoice: (@MainActor () -> Bool)?
    var terminationReply: @MainActor (Bool) -> Void = { NSApp.reply(toApplicationShouldTerminate: $0) }
    private var reviewingImageCleanup = false
    private var terminationResolved = false
    private var resultScreenObserver: NSObjectProtocol?
    private var fittingResultPanel = false
    private var openProductWindows: [NSPanel] = []
    private weak var lastFocusedProductWindow: NSWindow?
    private let settingsNavigation = SettingsNavigation()
    private let workspaceNavigation = WorkspaceNavigation()
    private let quickInputDraft = QuickInputDraft()
    private var quickInputIntent: UUID?
    private var submittingQuickInput = false
    private var presentedCaptureIntent: UUID?
    private var announcedCaptureStatus: String?

    var desiredActivationPolicy: NSApplication.ActivationPolicy {
        openProductWindows.isEmpty ? .accessory : .regular
    }

    var workspaceSection: ProductSection { workspaceNavigation.section }

    private var translationUsesResultPanel: Bool {
        ["selection", "ocr"].contains(model.translationOrigin) ||
            quickInputIntent == model.translationIntentID || submittingQuickInput
    }

    private func activeWorkspacePanel(_ section: ProductSection) -> NSPanel? {
        guard workspaceSection == section,
              openProductWindows.contains(where: { $0 === inputPanel }) else { return nil }
        return inputPanel
    }

    deinit {
        if let resultScreenObserver { NotificationCenter.default.removeObserver(resultScreenObserver) }
    }

    override convenience init() {
        self.init(model: ProbeModel(), capture: CaptureModel(), diagnostics: ProbeModel(persistsPreferences: false))
    }

    init(model: ProbeModel, capture: CaptureModel, diagnostics: ProbeModel, about: AboutModel? = nil,
         loginItems: LoginItemModel? = nil, updates: AppUpdateModel? = nil,
         uninstaller: (any AppUninstallServing)? = nil) {
        self.model = model
        self.capture = capture
        self.diagnostics = diagnostics
        self.aboutModel = about ?? AboutModel()
        self.loginItems = loginItems ?? LoginItemModel()
        self.updates = updates ?? AppUpdateModel()
        self.uninstaller = uninstaller ?? AppUninstallService()
        super.init()
        dictionaryAnnouncement = model.dictionarySearch.$phase
            .removeDuplicates()
            .dropFirst()
            .receive(on: RunLoop.main)
            .sink { [weak self] phase in
                guard let self, self.model.dictionarySearch.phase == phase,
                      let panel = self.dictionaryPanel, panel.isKeyWindow, panel.isVisible else { return }
                NSAccessibility.post(element: panel, notification: .announcementRequested,
                    userInfo: [.announcement: self.model.dictionarySearch.message(using: self.model),
                               .priority: NSAccessibilityPriorityLevel.medium.rawValue])
            }
        model.captureShortcut.canCapture = { [weak self] in
            guard let self, !self.terminating, self.selectionOverlay == nil else { return false }
            switch self.capture.phase {
            case .capturing, .selecting, .recognizing: return false
            case .idle, .ready, .empty, .failed, .cancelled: return true
            }
        }
        model.captureShortcut.onCapture = { [weak self] in self?.startCapture() }
        model.onQuickInputRequested = { [weak self] in self?.showQuickInput() }
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
                self.handleSelection(result)
            }
        }
        model.onTranslationStarted = { [weak self] in
            guard let self, !self.terminating,
                  self.translationUsesResultPanel else { return }
            if self.capture.automaticallyTranslates && self.model.translationOrigin == "ocr" {
                self.presentedCaptureIntent = self.model.translationIntentID
                if NSApp.isHidden {
                    self.showTranslationResults = false
                    return
                }
            }
            self.showTranslationResults = true
            self.showResult(reposition: true)
        }
        model.onTranslationResult = { [weak self] _ in
            guard let self = self, !self.terminating, self.showTranslationResults else { return }
            if self.translationUsesResultPanel { self.showResult() }
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
        captureObservation = capture.objectWillChange.sink { [weak self] in
            DispatchQueue.main.async {
                guard let self, !self.terminating else { return }
                self.updateCapturePresentation()
            }
        }
        // Default-off launch stays inert; an opt-in hint bootstraps only authoritative config loading.
        model.restorePlainPastePreferenceIfNeeded()
    }

    @discardableResult
    private func add(_ title: String, action: Selector, key: String = "", to menu: NSMenu) -> NSMenuItem {
        let item = TextMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        menu.addItem(item)
        return item
    }

    func configureMenus() {
        let menu = NSMenu()
        menu.delegate = self
        add(model.text("Quick translate…", "快速翻译…"), action: #selector(showQuickInput), to: menu)
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), to: menu)
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
        add(model.text("Check for Updates…", "检查更新…"), action: #selector(checkForUpdates), to: menu)
        add(model.text("Uninstall CC Translate…", "卸载 CC Translate…"), action: #selector(openUninstall), to: menu)
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: menu)
        statusItem?.menu = menu

        let main = NSMenu()
        let application = NSMenu()
        add(model.text("About CC Translate", "关于 CC Translate"), action: #selector(openAbout), to: application)
        add(model.text("Check for Updates…", "检查更新…"), action: #selector(checkForUpdates), to: application)
        add(model.text("Settings…", "设置…"), action: #selector(openSettings), key: ",", to: application)
        application.addItem(.separator())
        add(model.text("Uninstall CC Translate…", "卸载 CC Translate…"), action: #selector(openUninstall), to: application)
        application.addItem(.separator())
        let hide = NSMenuItem(title: model.text("Hide CC Translate", "隐藏 CC Translate"),
                              action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        hide.target = NSApp
        application.addItem(hide)
        let hideOthers = NSMenuItem(title: model.text("Hide Others", "隐藏其他"),
                                    action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        hideOthers.target = NSApp
        application.addItem(hideOthers)
        let showAll = NSMenuItem(title: model.text("Show All", "全部显示"),
                                 action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        showAll.target = NSApp
        application.addItem(showAll)
        application.addItem(.separator())
        add(model.text("Quit CC Translate", "退出 CC Translate"), action: #selector(quit), key: "q", to: application)
        let appItem = NSMenuItem()
        appItem.submenu = application
        main.addItem(appItem)
        let file = NSMenu(title: model.text("File", "文件"))
        add(model.text("Quick translate…", "快速翻译…"), action: #selector(showQuickInput), to: file)
        add(model.text("Translate…", "翻译…"), action: #selector(openInput), to: file)
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
        let window = NSMenu(title: model.text("Window", "窗口"))
        window.addItem(NSMenuItem(title: model.text("Minimize", "最小化"),
                                  action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        window.addItem(NSMenuItem(title: model.text("Zoom", "缩放"),
                                  action: #selector(NSWindow.performZoom(_:)), keyEquivalent: ""))
        window.addItem(.separator())
        let bringAll = NSMenuItem(title: model.text("Bring All to Front", "前置全部窗口"),
                                  action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        bringAll.target = NSApp
        window.addItem(bringAll)
        let windowItem = NSMenuItem(title: window.title, action: nil, keyEquivalent: "")
        windowItem.submenu = window
        main.addItem(windowItem)
        NSApp.mainMenu = main
        NSApp.windowsMenu = window
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
        inputPanel?.title = workspaceSection == .translator ? "CC Translate" :
            "\(workspaceSection.title(using: model)) — CC Translate"
        quickInputPanel?.title = model.text("Quick translate", "快速翻译")
        diagnosticsPanel?.title = model.text("Diagnostics", "诊断")
        capturePanel?.title = model.text("Screenshot translation", "截图翻译")
        updatesPanel?.title = model.text("Software updates", "软件更新")
        let appearance: NSAppearance? = model.appearance == "dark" ? NSAppearance(named: .darkAqua) :
            model.appearance == "light" ? NSAppearance(named: .aqua) : nil
        for panel in [inputPanel, resultPanel, quickInputPanel, capturePanel, updatesPanel] {
            panel?.appearance = appearance
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuTarget = SelectionProbe.currentTarget()
        menu.item(withTag: 10)?.state = model.monitorRequestedEnabled ? .on : .off
    }

    @objc private func openInput() {
        presentWorkspace(.translator)
    }

    private func presentWorkspace(_ section: ProductSection) {
        guard canReopenProductWindow else { return }
        if inputPanel?.attachedSheet != nil {
            activate(inputPanel)
            return
        }
        workspaceNavigation.section = section
        if inputPanel == nil {
            let panel = makePanel(title: "CC Translate", width: 1120, height: 720,
                minimum: NSSize(width: 717, height: 600),
                root: MainWorkspaceView(model: model, navigation: workspaceNavigation,
                    about: aboutModel, loginItems: loginItems, updates: updates,
                    settingsNavigation: settingsNavigation,
                    navigate: { [weak self] in self?.navigate(to: $0) },
                    showDiagnostics: { [weak self] in self?.openDiagnostics() },
                    close: { [weak self] in self?.inputPanel?.performClose(nil) }))
            panel.isOpaque = false
            inputPanel = panel
        }
        switch section {
        case .translator: model.openProduct()
        case .history: model.loadHistory()
        case .dictionary: model.refreshDictionary()
        case .settings:
            model.openProduct()
            model.refreshPermissions()
            loginItems.refresh()
        case .about:
            if aboutModel.phase == .idle { aboutModel.openResources() }
        case .capture:
            preconditionFailure("Screenshot selection must not replace the workspace page.")
        }
        activate(inputPanel)
    }

    @objc func showQuickInput() {
        presentQuickInput(selectionUnavailable: false)
    }

    private func presentQuickInput(selectionUnavailable: Bool) {
        guard canReopenProductWindow else { return }
        if inputPanel?.attachedSheet != nil {
            activate(inputPanel)
            return
        }
        if quickInputPanel == nil {
            quickInputPanel = makePanel(title: model.text("Quick translate", "快速翻译"),
                width: 520, height: 360, minimum: NSSize(width: 420, height: 340),
                root: QuickInputView(model: model, draft: quickInputDraft,
                    submit: { [weak self] in self?.submitQuickInput() },
                    cancel: { [weak self] in self?.quickInputPanel?.performClose(nil) }))
        }
        quickInputDraft.attemptedSubmit = false
        quickInputDraft.selectionUnavailable = selectionUnavailable
        model.loadPresentation()
        activate(quickInputPanel)
    }

    func submitQuickInput() {
        quickInputDraft.attemptedSubmit = true
        guard model.inputIssue(for: quickInputDraft.text) == nil,
              (quickInputPanel?.firstResponder as? NSTextView)?.hasMarkedText() != true else { return }
        model.input = quickInputDraft.text
        submittingQuickInput = true
        model.translate()
        quickInputIntent = model.translationIntentID
        submittingQuickInput = false
        showTranslationResults = true
        showResult(reposition: true)
        quickInputPanel?.performClose(nil)
    }

    private var canReopenProductWindow: Bool {
        !terminating && !uninstallPreparing && pendingUninstall == nil &&
            selectionOverlay == nil && capture.phase != .selecting && NSApp.modalWindow == nil
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard canReopenProductWindow else { return false }
        let focused = openProductWindows.first { $0 === lastFocusedProductWindow }
        if let panel = focused ?? openProductWindows.last {
            // Restoring an existing editor must not reconnect, resubmit, or reset its draft.
            activate(panel)
        } else {
            openInput()
        }
        return false
    }

    @objc private func translateSelection() {
        handleSelection(SelectionProbe.read(target: menuTarget))
    }

    func handleSelection(_ selection: SelectionResult) {
        guard !terminating, !uninstallPreparing else { return }
        switch selection {
        case .present(let text):
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                showQuickInput()
                return
            }
            model.translateSelection(selection)
            if model.productPhase == .failed { showResult(reposition: true) }
        case .absent, .unknown(.copyNotObserved):
            showQuickInput()
        case .unknown:
            presentQuickInput(selectionUnavailable: true)
        }
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if terminating || uninstallPreparing { return menuItem.action == #selector(quit) }
        if menuItem.action == #selector(openUninstall) { return !updates.sessionInProgress && !loginItems.busy }
        if selectionOverlay != nil { return menuItem.action == #selector(quit) }
        if menuItem.action == #selector(checkForUpdates) {
            return !terminating && (updates.channel != .configured || updates.canCheck)
        }
        if menuItem.action == #selector(submitInput) {
            if quickInputPanel?.isKeyWindow == true {
                let composing = (quickInputPanel?.firstResponder as? NSTextView)?.hasMarkedText() ?? false
                return !composing && model.inputIssue(for: quickInputDraft.text) == nil
            }
            if capturePanel?.isKeyWindow == true {
                let composing = (capturePanel?.firstResponder as? NSTextView)?.hasMarkedText() ?? false
                return !composing && capture.canTranslate && !model.active && !model.preparing
            }
            let composing = (inputPanel?.firstResponder as? NSTextView)?.hasMarkedText() ?? false
            return workspaceSection == .translator && inputPanel?.isKeyWindow == true &&
                !composing && !model.active && !model.preparing &&
                !model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
                model.input.utf8.count <= 8192
        }
        return true
    }

    @objc private func submitInput() {
        if quickInputPanel?.isKeyWindow == true {
            submitQuickInput()
        } else if capturePanel?.isKeyWindow == true {
            guard (capturePanel?.firstResponder as? NSTextView)?.hasMarkedText() != true else { return }
            capture.translate(using: model)
        } else if workspaceSection == .translator && inputPanel?.isKeyWindow == true {
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
        selectionOverlay = nil
        discardCapturePanel()
        presentedCaptureIntent = nil
        announcedCaptureStatus = nil
        model.loadPresentation()
        capture.startTranslation(using: model, mode: model.captureTranslationMode)
        captureReturnIntent = model.translationIntentID
    }

    private func ensureCapturePanel() {
        guard capturePanel == nil else { return }
        if capture.automaticallyTranslates {
            capturePanel = makePanel(title: model.text("Screenshot translation", "截图翻译"),
                width: 520, height: 190, minimum: NSSize(width: 420, height: 170),
                root: CaptureStatusView(capture: capture, model: model,
                    captureAgain: { [weak self] in self?.startCapture() },
                    close: { [weak self] in self?.capturePanel?.performClose(nil) }))
        } else {
            capturePanel = makePanel(title: model.text("Screenshot translation", "截图翻译"),
                width: 860, height: 680, minimum: NSSize(width: 620, height: 600),
                root: CaptureView(capture: capture, model: model,
                    captureAgain: { [weak self] in self?.startCapture() },
                    reselect: { [weak self] in self?.capture.reselect() },
                    close: { [weak self] in self?.capturePanel?.performClose(nil) }))
        }
    }

    private func updateCapturePresentation() {
        if capture.automaticallyTranslates && capture.submitted {
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            if let intent = capture.submittedIntent, intent == model.translationIntentID,
               presentedCaptureIntent != intent {
                presentedCaptureIntent = intent
                showTranslationResults = !NSApp.isHidden
                if showTranslationResults { showResult(reposition: true) }
            }
            discardCapturePanel()
            return
        }
        switch capture.phase {
        case .selecting:
            guard selectionOverlay == nil else { return }
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
            announcedCaptureStatus = nil
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            discardCapturePanel()
            restoreCaptureFocus()
        case .capturing, .recognizing, .ready, .empty, .failed:
            if capture.automaticallyTranslates && capture.phase == .capturing { return }
            selectionOverlay?.dismiss()
            selectionOverlay = nil
            let shouldActivate = capturePanel == nil ||
                (capturePanel?.isKeyWindow == true && NSApp.isActive && !NSApp.isHidden)
            ensureCapturePanel()
            if shouldActivate { activate(capturePanel) }
            else { applyAppearance() }
            if capture.phase == .ready || capture.phase == .empty || capture.phase == .failed,
               let capturePanel, capturePanel.isKeyWindow {
                let message = capture.message(using: model)
                if announcedCaptureStatus != message {
                    announcedCaptureStatus = message
                    NSAccessibility.post(element: capturePanel, notification: .announcementRequested,
                        userInfo: [.announcement: message,
                                   .priority: NSAccessibilityPriorityLevel.medium.rawValue])
                }
            }
        }
    }

    private func restoreCaptureFocus() {
        guard captureReturnIntent == nil || captureReturnIntent == model.translationIntentID else { return }
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
        openProductWindows.removeAll { $0 === panel }
        panel?.delegate = nil
        panel?.contentView = nil
        panel?.orderOut(nil)
        panel?.close()
        updateDockPresence()
    }

    @objc private func toggleMonitor() {
        if model.monitorRequestedEnabled {
            model.stopMonitor()
            return
        }
        model.translatePassiveSelections = true
        model.startMonitor()
        if !model.monitorEnabled { showSettings(pane: .shortcuts) }
    }

    @objc private func openHistory() {
        presentWorkspace(.history)
    }

    @objc private func openSettings() {
        showSettings()
    }

    func showSettings(pane: SettingsPane? = nil) {
        guard canReopenProductWindow else { return }
        if inputPanel?.attachedSheet != nil {
            activate(inputPanel)
            return
        }
        if let pane { settingsNavigation.pane = pane }
        presentWorkspace(.settings)
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        if model.monitorRequestedEnabled { model.refreshPermissions() }
        if settingsPanel?.isVisible == true { loginItems.refresh() }
    }

    func applicationWillHide(_ notification: Notification) {
        guard selectionOverlay != nil || capture.phase == .selecting else { return }
        // Hide must not leave an invisible modal selection blocking the next Dock click.
        selectionOverlay?.dismiss()
        selectionOverlay = nil
        captureReturnWindow = nil
        captureReturnResponder = nil
        captureReturnApplication = nil
        capture.cancel()
        discardCapturePanel()
    }

    func settingsContent(pane: SettingsPane? = nil) -> TranslationSettingsView {
        if let pane { settingsNavigation.pane = pane }
        return TranslationSettingsView(model: model,
            showDiagnostics: { [weak self] in self?.openDiagnostics() },
            showAbout: { [weak self] in self?.openAbout() }, loginItems: loginItems, updates: updates,
            navigation: settingsNavigation)
    }

    func navigate(to section: ProductSection) {
        guard canReopenProductWindow else { return }
        if inputPanel?.attachedSheet != nil {
            activate(inputPanel)
            return
        }
        switch section {
        case .translator: openInput()
        case .capture: startCapture()
        case .history: openHistory()
        case .dictionary: openDictionary()
        case .settings: openSettings()
        case .about: openAbout()
        }
    }

    private func openDictionary() {
        presentWorkspace(.dictionary)
    }

    @objc private func checkForUpdates() {
        if terminating {
            updates.check()
            return
        }
        if updates.check() {
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        if updatesPanel == nil {
            updatesPanel = makePanel(title: model.text("Software updates", "软件更新"),
                width: 580, height: 300, minimum: NSSize(width: 500, height: 270),
                root: AppUpdatePanelView(model: model, updates: updates))
        }
        activate(updatesPanel)
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
                            styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        panel.contentMinSize = minimum
        panel.title = title
        configureProductChrome(panel)
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.isExcludedFromWindowsMenu = false
        panel.delegate = self
        // Keep the minimum outside the responsive workspace and any sheet presentation host.
        let host = NSHostingView(rootView: root.frame(minWidth: minimum.width, minHeight: minimum.height))
        host.sizingOptions = [.minSize]
        panel.contentView = host
        panel.contentMinSize = minimum
        panel.center()
        return panel
    }

    private func configureProductChrome(_ panel: NSPanel) {
        panel.titlebarAppearsTransparent = true
        panel.titlebarSeparatorStyle = .none
        panel.backgroundColor = NSColor(PearlTheme.surface)
    }

    private func activate(_ panel: NSPanel?) {
        guard !terminating, !uninstallPreparing, pendingUninstall == nil, let panel else { return }
        rememberOpenWindow(panel)
        applyAppearance()
        if NSApp.isHidden { NSApp.unhide(nil) }
        NSApp.activate(ignoringOtherApps: true)
        if panel.isMiniaturized { panel.deminiaturize(nil) }
        panel.makeKeyAndOrderFront(nil)
        lastFocusedProductWindow = panel
    }

    private func rememberOpenWindow(_ panel: NSPanel) {
        if !openProductWindows.contains(where: { $0 === panel }) { openProductWindows.append(panel) }
        updateDockPresence()
    }

    private func updateDockPresence() {
        // Only the installed delegate owns process-wide application identity.
        guard NSApp.delegate === self, NSApp.activationPolicy() != desiredActivationPolicy else { return }
        if !NSApp.setActivationPolicy(desiredActivationPolicy) {
            NSLog("CC Translate could not update its Dock visibility.")
        }
    }

    func windowDidBecomeKey(_ notification: Notification) {
        guard let window = notification.object as? NSWindow,
              openProductWindows.contains(where: { $0 === window }) else { return }
        lastFocusedProductWindow = window
    }

    @objc private func recallResult() {
        if model.output.isEmpty { openInput() } else { showResult(reposition: true) }
    }
    @objc func openAbout() {
        presentWorkspace(.about)
    }
    @objc private func quit() { NSApp.terminate(nil) }

    @objc func openUninstall() {
        guard !terminating, !uninstallPreparing, !updates.sessionInProgress, !loginItems.busy else {
            showUninstallNotice(model.text("Finish the current update or login-item change before uninstalling.",
                                           "请先完成当前更新或登录项更改，再卸载应用。"))
            return
        }
        do {
            let locations = try uninstaller.preview()
            let alert = uninstallConfirmation(locations)
            guard alert.runModal() == .alertSecondButtonReturn else { return }
            let includingData = (alert.accessoryView as? NSButton)?.state == .on
            Task { await requestUninstall(locations, includingData: includingData) }
        } catch {
            showUninstallNotice(model.text(
                "Could not prepare this app for removal (error \((error as NSError).code)). No files were removed. You can quit and move the app to Trash in Finder.",
                "未能准备卸载此应用（错误 \((error as NSError).code)）。未移除任何文件。你可以退出应用，再在 Finder 中将其移到废纸篓。"))
        }
    }

    func uninstallConfirmation(_ locations: AppUninstallLocations) -> NSAlert {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = model.text("Move CC Translate to Trash?", "将 CC Translate 移到废纸篓？")
        alert.informativeText = model.text(
            "The app will stop its work, remove its login item, and quit. Your data is kept unless you select the option below. CLI installations, shared accounts, Node, and dictionaries outside this app's data folder are not removed.",
            "应用将停止当前工作、移除自身登录项并退出。默认保留你的数据，除非选中下面的选项。不会移除 CLI、共享账号、Node 或应用数据目录之外的词典。")
            + "\n\n" + locations.application.path
        alert.addButton(withTitle: model.text("Cancel", "取消"))
        alert.addButton(withTitle: model.text("Move App to Trash", "将应用移到废纸篓"))
        let option = NSButton(checkboxWithTitle: model.text(
            "Also trash this app's data and cache, and erase its preferences",
            "同时将此应用的数据和缓存移到废纸篓，并清除其偏好设置"), target: nil, action: nil)
        option.state = .off
        option.setAccessibilityIdentifier("uninstall-own-data")
        option.sizeToFit()
        alert.accessoryView = option
        return alert
    }

    func requestUninstall(_ locations: AppUninstallLocations, includingData: Bool) async {
        guard !terminating, !uninstallPreparing, !updates.sessionInProgress, !loginItems.busy else {
            showUninstallNotice(model.text("Uninstall is unavailable while another lifecycle operation is running.",
                                           "其他生命周期操作正在进行，暂时无法卸载。"))
            return
        }
        uninstallPreparing = true
        defer { uninstallPreparing = false }
        guard await loginItems.removeForUninstall() else {
            showUninstallNotice(model.text("The login item was not confirmed removed. No files were removed. Check Login Items in System Settings, then try again.",
                                           "尚未确认移除登录项。未移除任何文件。请检查系统设置中的登录项，再重试。"))
            return
        }
        guard !terminating else { return }
        guard !updates.sessionInProgress else {
            showUninstallNotice(model.text("An update started before uninstall. The login item was removed, but no files were removed. Finish the update first.",
                                           "卸载前有更新开始。登录项已移除，但未移除任何文件，请先完成更新。"))
            return
        }
        pendingUninstall = (locations, includingData)
        terminateApplication()
    }

    private func finishUninstallIfRequested() {
        guard let request = pendingUninstall else { return }
        pendingUninstall = nil
        model.stopPersistingPreferencesForUninstall()
        diagnostics.stopPersistingPreferencesForUninstall()
        hideWindowsForUninstall()
        let outcome = uninstaller.remove(request.locations, includingData: request.includingData)
        uninstallOutcome?(outcome)
        if let failure = outcome.failure {
            let name: String
            switch failure.step {
            case .application: name = model.text("the application", "应用")
            case .support: name = model.text("application data", "应用数据")
            case .cache: name = model.text("the cache", "缓存")
            case .preferences: name = model.text("native preferences", "原生偏好设置")
            }
            showUninstallNotice(model.text(
                "Could not remove \(name) (error \(failure.code)). Removal stopped; earlier completed items were not restored. Files already moved can be recovered from Trash. The app will now quit. You can reopen it or remove remaining items in Finder.",
                "未能移除\(name)（错误 \(failure.code)）。已停止移除，先前完成的项目未还原。已移动的文件可从废纸篓恢复。应用即将退出，你可以重新打开应用，或在 Finder 中处理剩余项目。"))
        }
    }

    private func hideWindowsForUninstall() {
        for panel in [inputPanel, resultPanel, quickInputPanel, capturePanel, diagnosticsPanel, updatesPanel] {
            panel?.orderOut(nil)
        }
    }

    private func showUninstallNotice(_ message: String) {
        if let uninstallNotice { uninstallNotice(message); return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = model.text("CC Translate uninstall", "卸载 CC Translate")
        alert.informativeText = message
        alert.addButton(withTitle: terminating ? model.text("Quit", "退出") : model.text("OK", "好"))
        alert.runModal()
    }

    func showResult(reposition: Bool = false) {
        guard !terminating else { return }
        let opening = resultPanel == nil || reposition
        if resultPanel == nil {
            let panel = ResultPanel(
                contentRect: NSRect(x: 0, y: 0, width: 590, height: 400),
                styleMask: [.nonactivatingPanel, .titled, .closable, .resizable],
                backing: .buffered, defer: false
            )
            panel.title = "CC Translate"
            panel.contentMinSize = NSSize(width: 420, height: 300)
            configureProductChrome(panel)
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.isExcludedFromWindowsMenu = false
            panel.isFloatingPanel = true
            panel.becomesKeyOnlyIfNeeded = true
            panel.level = model.resultPinned ? .floating : .normal
            panel.delegate = self
            let host = NSHostingView(rootView: ResultWindowContent(
                model: model, openInWindow: { [weak self] in self?.openInput() },
                togglePinned: { [weak self] in self?.toggleResultPin() }))
            // The panel owns its viewport size, not SwiftUI's unbounded ideal size.
            host.sizingOptions = []
            panel.contentView = host
            panel.center()
            resultPanel = panel
            resultScreenObserver = NotificationCenter.default.addObserver(
                forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated {
                    self?.fitResultPanel(reposition: false, screens: NSScreen.screens.map(\.visibleFrame))
                }

            }
        }
        fitResultPanel(reposition: opening,
                       screens: NSScreen.screens.map(\.visibleFrame))
        applyAppearance()
        if opening, let resultPanel {
            rememberOpenWindow(resultPanel)
            resultPanel.orderFrontRegardless()
        }
    }

    func toggleResultPin() {
        model.resultPinned.toggle()
        resultPanel?.level = model.resultPinned ? .floating : .normal
    }

    func fitResultPanel(reposition: Bool, screens: [NSRect]) {
        guard let panel = resultPanel, !fittingResultPanel else { return }
        let placement: NativeResultPlacement = reposition ? model.resultPlacement : .remembered
        let minimumFrame = panel.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 420, height: 300)).size
        var current = panel.frame
        current.size.width = max(current.width, minimumFrame.width)
        current.size.height = max(current.height, minimumFrame.height)
        guard let frame = placement.frame(
            current: current, remembered: reposition ? model.rememberedResultFrame : panel.frame,
            pointer: NSEvent.mouseLocation, visibleScreens: screens),
              let screen = screens.first(where: { $0.contains(frame) }) else { return }
        fittingResultPanel = true
        defer { fittingResultPanel = false }
        let content = panel.contentRect(forFrameRect: screen).size
        let minimum = NSSize(width: min(420, content.width), height: min(300, content.height))
        if panel.contentMinSize != minimum { panel.contentMinSize = minimum }
        if panel.maxSize != screen.size { panel.maxSize = screen.size }
        if panel.frame != frame { panel.setFrame(frame, display: true) }
        if model.rememberedResultFrame != panel.frame { model.rememberResultFrame(panel.frame) }
    }

    func windowDidMove(_ notification: Notification) {
        rememberResultWindow(notification)
    }

    func windowDidResize(_ notification: Notification) {
        if let window = notification.object as? NSWindow, window === resultPanel, !window.inLiveResize {
            fitResultPanel(reposition: false, screens: NSScreen.screens.map(\.visibleFrame))
        }
        rememberResultWindow(notification)
    }

    func windowDidEndLiveResize(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === resultPanel else { return }
        fitResultPanel(reposition: false, screens: NSScreen.screens.map(\.visibleFrame))
    }

    func windowDidChangeScreen(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === resultPanel else { return }
        // A drag can straddle screens. Update the size ceiling without snapping its position.
        if let screen = window.screen { window.maxSize = screen.visibleFrame.size }
    }

    private func rememberResultWindow(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === resultPanel else { return }
        model.rememberResultFrame(window.frame)
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        openProductWindows.removeAll { $0 === window }
        if lastFocusedProductWindow === window { lastFocusedProductWindow = nil }
        if window === inputPanel {
            aboutModel.close()
            if workspaceSection == .translator && !translationUsesResultPanel { model.cancel() }
        }
        if window === quickInputPanel {
            quickInputDraft.text = ""
            quickInputDraft.attemptedSubmit = false
            quickInputDraft.selectionUnavailable = false
        }
        if window === resultPanel {
            model.rememberResultFrame(window.frame)
            showTranslationResults = false
            if translationUsesResultPanel { model.cancel() }
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
        updateDockPresence()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        if pendingUninstall != nil { hideWindowsForUninstall() }
        updates.prepareToQuit()
        model.captureShortcut.cancelPendingTrigger()
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
        finishUninstallIfRequested()
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        terminating = true
        if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
        statusItem = nil
        updates.prepareToQuit()
        model.captureShortcut.shutdown()
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
                finishUninstallIfRequested()
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
