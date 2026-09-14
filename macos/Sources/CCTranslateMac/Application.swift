import AppKit
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

private final class ResultPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem?
    private var inputPanel: NSPanel?
    private var resultPanel: NSPanel?
    private var menuTarget: FocusTarget?
    private let model = ProbeModel()
    private var terminating = false
    private var showTranslationResults = true

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "CC Dev"
        item.button?.toolTip = "CC Translate native development build"
        let menu = NSMenu()
        menu.delegate = self
        add("Open input / diagnostics...", action: #selector(openInput), to: menu)
        add("Read current AX selection (local only)", action: #selector(readSelection), to: menu)
        add("Translate current AX selection (native Codex)", action: #selector(translateSelection), to: menu)
        menu.addItem(.separator())
        add("Start passive double Cmd+C", action: #selector(startMonitor), to: menu)
        add("Stop passive monitor", action: #selector(stopMonitor), to: menu)
        menu.addItem(.separator())
        add("Quit CC Translate", action: #selector(quit), to: menu)
        item.menu = menu
        statusItem = item
        model.onSelection = { [weak self] result in
            guard let self = self else { return }
            if self.model.translatePassiveSelections, self.model.nativeTranslation, self.model.ready {
                self.model.translateSelection(result)
            } else {
                self.showSelection(result)
            }
        }
        model.onTranslationStarted = { [weak self] in self?.showTranslationResults = true }
        model.onTranslationResult = { [weak self] text in
            guard let self = self, !self.terminating, self.showTranslationResults else { return }
            self.showLocalResult(text)
        }
        model.onStopped = { [weak self] in
            guard self?.terminating == true else { return }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        // Intentionally no window, helper, TCC check, event monitor, or network at launch.
    }

    private func add(_ title: String, action: Selector, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuTarget = SelectionProbe.currentTarget()
        statusItem?.button?.toolTip = model.monitorStatus
    }

    @objc private func openInput() {
        if inputPanel == nil {
            let panel = NSPanel(
                contentRect: NSRect(x: 0, y: 0, width: 760, height: 600),
                styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false
            )
            panel.title = "CC Translate - opt-in native Codex / diagnostics"
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.delegate = self
            panel.contentView = NSHostingView(rootView: ProbeView(model: model))
            panel.center()
            inputPanel = panel
        }
        model.refreshPermissions()
        NSApp.activate(ignoringOtherApps: true)
        inputPanel?.makeKeyAndOrderFront(nil)
    }

    @objc private func readSelection() {
        showSelection(SelectionProbe.read(target: menuTarget))
    }

    @objc private func translateSelection() {
        model.translateSelection(SelectionProbe.read(target: menuTarget))
    }

    @objc private func startMonitor() {
        model.startMonitor()
        showLocalResult(model.monitorStatus)
    }
    @objc private func stopMonitor() {
        model.stopMonitor()
        showLocalResult(model.monitorStatus)
    }
    @objc private func quit() { NSApp.terminate(nil) }

    private func showSelection(_ result: SelectionResult) {
        let message: String
        switch result {
        case .present(let text): message = "AX selection: PRESENT (local probe, not translation)\n\n\(text)"
        case .absent: message = "AX selection: ABSENT (selectedText is empty)."
        case .unknown(let reason):
            message = "AX selection: UNKNOWN (\(reason.rawValue)).\nNo clipboard fallback. Use explicit input."
        }
        showLocalResult(message)
    }

    private func showLocalResult(_ message: String) {
        if resultPanel == nil {
            let panel = ResultPanel(
                contentRect: NSRect(x: 0, y: 0, width: 580, height: 280),
                styleMask: [.nonactivatingPanel, .titled, .closable, .resizable],
                backing: .buffered, defer: false
            )
            panel.title = "CC Translate - selection / result"
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.isFloatingPanel = true
            panel.becomesKeyOnlyIfNeeded = true
            panel.level = .floating
            panel.delegate = self
            panel.center()
            resultPanel = panel
        }
        resultPanel?.contentView = NSHostingView(rootView:
            ScrollView {
                Text(message).frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
        )
        resultPanel?.orderFrontRegardless()
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        if window === inputPanel {
            showTranslationResults = false
            model.closePanel()
            resultPanel?.contentView = nil
            resultPanel?.orderOut(nil)
        }
        if window === resultPanel {
            showTranslationResults = false
            if model.nativeTranslation { model.cancel() }
            resultPanel?.contentView = nil
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        let waitForProcesses = model.hasProcesses
        model.prepareToQuit()
        resultPanel?.contentView = nil
        return waitForProcesses ? .terminateLater : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        model.prepareToQuit()
    }
}
