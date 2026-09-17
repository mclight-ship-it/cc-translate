import AppKit
import ApplicationServices
import Carbon

public enum PermissionState: String {
    case granted
    case notGranted = "not granted / not yet requested"
}

public struct PermissionSnapshot {
    public let accessibility: PermissionState
    public let inputMonitoring: PermissionState
    public let screenCapture: PermissionState
    public let secureInput: Bool
}

@MainActor
public enum Permissions {
    public static func snapshot() -> PermissionSnapshot {
        PermissionSnapshot(
            accessibility: AXIsProcessTrusted() ? .granted : .notGranted,
            inputMonitoring: CGPreflightListenEventAccess() ? .granted : .notGranted,
            screenCapture: CGPreflightScreenCaptureAccess() ? .granted : .notGranted,
            secureInput: IsSecureEventInputEnabled()
        )
    }

    public static func requestAccessibility() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    public static func requestInputMonitoring() -> Bool { CGRequestListenEventAccess() }
    public static func requestScreenCapture() -> Bool { CGRequestScreenCaptureAccess() }
}

public enum SelectionResult: Equatable {
    case present(String)
    case absent
    case unknown(Reason)

    public enum Reason: String {
        case secureInput, accessibility, focusChanged, unavailable, unsupported, tooLarge
        case inputMonitoring, copyNotObserved, clipboardChanged, clipboardUnsupported, clipboardUnavailable
    }

    public static func evaluate(
        secureInput: Bool, trusted: Bool, sameTarget: Bool, selectedText: String?
    ) -> SelectionResult {
        guard !secureInput else { return .unknown(.secureInput) }
        guard trusted else { return .unknown(.accessibility) }
        guard sameTarget else { return .unknown(.focusChanged) }
        guard let text = selectedText else { return .unknown(.unsupported) }
        guard text.utf8.count <= 8192 else { return .unknown(.tooLarge) }
        return text.isEmpty ? .absent : .present(text)
    }
}

public struct FocusTarget: Equatable {
    public let pid: pid_t
    public init(pid: pid_t) { self.pid = pid }
}

@MainActor
public enum SelectionProbe {
    public static func currentTarget() -> FocusTarget? {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return nil }
        return FocusTarget(pid: app.processIdentifier)
    }

    public static func read(target: FocusTarget?) -> SelectionResult {
        guard !IsSecureEventInputEnabled() else { return .unknown(.secureInput) }
        guard AXIsProcessTrusted() else { return .unknown(.accessibility) }
        guard let target = target, let running = NSRunningApplication(processIdentifier: target.pid),
              !running.isTerminated else { return .unknown(.unavailable) }
        guard currentTarget() == target else { return .unknown(.focusChanged) }
        let application = AXUIElementCreateApplication(target.pid)
        guard AXUIElementSetMessagingTimeout(application, 0.3) == .success else {
            return .unknown(.unavailable)
        }
        var rawElement: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            application, kAXFocusedUIElementAttribute as CFString, &rawElement
        ) == .success, let rawElement = rawElement,
              CFGetTypeID(rawElement) == AXUIElementGetTypeID() else { return .unknown(.unsupported) }
        let element = rawElement as! AXUIElement
        guard AXUIElementSetMessagingTimeout(element, 0.3) == .success else {
            return .unknown(.unavailable)
        }
        var rawText: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            element, kAXSelectedTextAttribute as CFString, &rawText
        ) == .success else { return .unknown(.unsupported) }
        var currentElement: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            application, kAXFocusedUIElementAttribute as CFString, &currentElement
        ) == .success, let currentElement = currentElement,
              CFEqual(rawElement, currentElement) else { return .unknown(.focusChanged) }
        return SelectionResult.evaluate(
            secureInput: IsSecureEventInputEnabled(), trusted: AXIsProcessTrusted(),
            sameTarget: currentTarget() == target && !running.isTerminated,
            selectedText: rawText as? String
        )
    }
}

public struct DoubleCopyState {
    public static let maximumInterval: TimeInterval = 0.5
    private var previous: (time: TimeInterval, pid: pid_t)?
    public init() {}
    public mutating func reset() { previous = nil }

    public mutating func observe(
        time: TimeInterval, pid: pid_t, isCopy: Bool, isRepeat: Bool, secureInput: Bool
    ) -> Bool {
        guard !secureInput, isCopy, !isRepeat else {
            previous = nil
            return false
        }
        guard let previous = previous, previous.pid == pid else {
            self.previous = (time, pid)
            return false
        }
        let interval = time - previous.time
        if interval > 0, interval <= Self.maximumInterval {
            // Consume the pair; a third key press alone must not retrigger.
            self.previous = nil
            return true
        }
        self.previous = (time, pid)
        return false
    }
}

@MainActor
public protocol PassiveSelectionMonitoring: AnyObject {
    var running: Bool { get }
    var onSelection: ((SelectionResult) -> Void)? { get set }
    var onStop: ((String) -> Void)? { get set }
    func setClipboardFallbackEnabled(_ enabled: Bool)
    func start() throws
    func stop()
    func cancelPendingSelection()
}

@MainActor
public final class PassiveCopyMonitor: PassiveSelectionMonitoring {
    private let selection: FreshCopySelection
    private let events: any PassiveCopyEvents
    private let securityFailure: () -> SelectionResult.Reason?
    private let resetSource: () -> Void
    private var registration = UUID()
    public private(set) var running = false
    public var onSelection: ((SelectionResult) -> Void)?
    public var onStop: ((String) -> Void)?

    public convenience init() {
        let resolver = PassiveCopySourceResolver()
        let selection = FreshCopySelection(environment: PassiveCopyEnvironment(
            now: { ProcessInfo.processInfo.systemUptime },
            source: { resolver.capture(requireFocusIdentity: $0) },
            securityFailure: { PassiveCopySourceResolver.securityFailure() },
            selection: { SelectionProbe.read(target: $0) }
        ), clipboard: SystemFreshCopyClipboard())
        self.init(selection: selection, events: SystemPassiveCopyEvents(),
                  securityFailure: { PassiveCopySourceResolver.securityFailure() },
                  resetSource: { resolver.reset() })
    }

    init(selection: FreshCopySelection, events: any PassiveCopyEvents,
         securityFailure: @escaping () -> SelectionResult.Reason?, resetSource: @escaping () -> Void = {}) {
        self.selection = selection
        self.events = events
        self.securityFailure = securityFailure
        self.resetSource = resetSource
        selection.onSelection = { [weak self] result in
            guard let self, self.running else { return }
            if case .unknown(let reason) = result,
               reason == .secureInput || reason == .accessibility || reason == .inputMonitoring {
                self.stopForSecurity(reason)
                return
            }
            self.onSelection?(result)
        }
    }

    public func setClipboardFallbackEnabled(_ enabled: Bool) {
        selection.setFallbackEnabled(enabled)
    }

    public func cancelPendingSelection() {
        selection.cancel()
        resetSource()
    }

    public func start() throws {
        guard !running else { return }
        if let failure = securityFailure() {
            throw failure == .secureInput ? ProbeError.secureInput : ProbeError.permissionDenied
        }
        cancelPendingSelection()
        let registration = UUID()
        self.registration = registration
        do {
            try events.install(observe: { [weak self] event in
                guard let self, self.running, self.registration == registration else { return }
                self.observe(event)
            }, invalidate: { [weak self] in
                guard let self, self.running, self.registration == registration else { return }
                self.cancelPendingSelection()
            }, tick: { [weak self] in
                guard let self, self.running, self.registration == registration else { return }
                self.heartbeat()
            })
        } catch {
            stop()
            throw error
        }
        running = true
    }

    public func stop() {
        registration = UUID()
        running = false
        cancelPendingSelection()
        events.remove()
    }

    private func heartbeat() {
        if let failure = securityFailure() {
            stopForSecurity(failure)
        } else {
            selection.poll()
        }
    }

    private func stopForSecurity(_ failure: SelectionResult.Reason) {
        stop()
        onStop?(failure == .secureInput
                ? "Secure Input enabled; monitoring stopped. Restart explicitly."
                : "Accessibility or Input Monitoring permission lost; monitoring stopped.")
    }

    private func observe(_ event: NSEvent) {
        guard running else { return }
        if let failure = securityFailure() {
            stopForSecurity(failure)
            return
        }
        guard event.type == .keyDown else {
            cancelPendingSelection()
            return
        }
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask).subtracting([.capsLock])
        selection.observe(
            time: event.timestamp,
            isCopy: event.charactersIgnoringModifiers?.lowercased() == "c" && flags == [.command],
            isRepeat: event.isARepeat
        )
    }
}

@MainActor
protocol PassiveCopyEvents {
    func install(observe: @escaping (NSEvent) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws
    func remove()
}

@MainActor
final class SystemPassiveCopyEvents: PassiveCopyEvents {
    private var token: Any?
    private var localToken: Any?
    private var workspaceTokens: [NSObjectProtocol] = []
    private var secureTimer: Timer?

    func install(observe: @escaping (NSEvent) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws {
        let mask: NSEvent.EventTypeMask = [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown, .scrollWheel]
        token = NSEvent.addGlobalMonitorForEvents(matching: mask) { event in
            // AppKit global event monitors are delivered on the main thread.
            MainActor.assumeIsolated {
                observe(event)
            }
        }
        guard token != nil else { throw ProbeError.permissionDenied }
        localToken = NSEvent.addLocalMonitorForEvents(matching: mask, handler: Self.localObserver(invalidate: invalidate))
        guard localToken != nil else {
            throw ProbeError.permissionDenied
        }
        for name in [NSWorkspace.didActivateApplicationNotification, NSWorkspace.willSleepNotification,
                     NSWorkspace.didWakeNotification, NSWorkspace.sessionDidResignActiveNotification] {
            workspaceTokens.append(NSWorkspace.shared.notificationCenter.addObserver(
                forName: name, object: nil, queue: .main
            ) { _ in
                MainActor.assumeIsolated { invalidate() }
            })
        }
        secureTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
            MainActor.assumeIsolated { tick() }
        }
    }

    func remove() {
        if let token = token { NSEvent.removeMonitor(token) }
        token = nil
        if let localToken { NSEvent.removeMonitor(localToken) }
        localToken = nil
        for token in workspaceTokens { NSWorkspace.shared.notificationCenter.removeObserver(token) }
        workspaceTokens = []
        secureTimer?.invalidate()
        secureTimer = nil
    }

    static func localObserver(invalidate: @escaping () -> Void) -> (NSEvent) -> NSEvent? {
        { event in
            MainActor.assumeIsolated { invalidate() }
            return event
        }
    }
}
