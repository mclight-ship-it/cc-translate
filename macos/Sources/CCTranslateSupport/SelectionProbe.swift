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
        if interval > 0, interval <= 0.5 {
            // Consume the pair; a third key press alone must not retrigger.
            self.previous = nil
            return true
        }
        self.previous = (time, pid)
        return false
    }
}

@MainActor
public final class PassiveCopyMonitor {
    private var token: Any?
    private var secureTimer: Timer?
    private var state = DoubleCopyState()
    public private(set) var running = false
    public var onSelection: ((SelectionResult) -> Void)?
    public var onStop: ((String) -> Void)?

    public init() {}

    public func start() throws {
        guard !running else { return }
        guard !IsSecureEventInputEnabled() else { throw ProbeError.secureInput }
        guard AXIsProcessTrusted(), CGPreflightListenEventAccess() else { throw ProbeError.permissionDenied }
        state.reset()
        token = NSEvent.addGlobalMonitorForEvents(matching: [.keyDown]) { [weak self] event in
            // AppKit global event monitors are delivered on the main thread.
            MainActor.assumeIsolated {
                self?.observe(event)
            }
        }
        guard token != nil else { throw ProbeError.permissionDenied }
        running = true
        secureTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                if IsSecureEventInputEnabled() {
                    self?.stop()
                    self?.onStop?("Secure Input enabled; monitoring stopped. Restart explicitly.")
                } else if !AXIsProcessTrusted() || !CGPreflightListenEventAccess() {
                    self?.stop()
                    self?.onStop?("Accessibility or Input Monitoring permission lost; monitoring stopped.")
                }
            }
        }
    }

    public func stop() {
        if let token = token { NSEvent.removeMonitor(token) }
        token = nil
        secureTimer?.invalidate()
        secureTimer = nil
        state.reset()
        running = false
    }

    private func observe(_ event: NSEvent) {
        if IsSecureEventInputEnabled() {
            stop()
            onStop?("Secure Input enabled; monitoring stopped. Restart explicitly.")
            return
        }
        guard let target = SelectionProbe.currentTarget() else {
            state.reset()
            return
        }
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask).subtracting([.capsLock])
        if state.observe(
            time: event.timestamp, pid: target.pid,
            isCopy: event.charactersIgnoringModifiers?.lowercased() == "c" && flags == [.command],
            isRepeat: event.isARepeat, secureInput: false
        ) {
            onSelection?(SelectionProbe.read(target: target))
        }
    }
}
