import AppKit
import ApplicationServices
import Carbon

public enum PermissionState: String {
    case granted
    case notGranted = "not granted / not yet requested"
}

public struct PermissionSnapshot: Equatable {
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

public struct DoubleCopyInterval: Equatable, Sendable {
    public static let standard = DoubleCopyInterval(validatedSeconds: 0.5)
    public let seconds: TimeInterval

    public init?(seconds: TimeInterval) {
        guard seconds.isFinite, seconds > 0 else { return nil }
        self.seconds = seconds
    }

    private init(validatedSeconds: TimeInterval) { seconds = validatedSeconds }
}

public struct DoubleCopyState {
    public let interval: DoubleCopyInterval
    private var previous: (time: TimeInterval, pid: pid_t)?
    public init(interval: DoubleCopyInterval = .standard) { self.interval = interval }
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
        if interval > 0, interval <= self.interval.seconds {
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
    var copyInterval: DoubleCopyInterval { get }
    var onSelection: ((SelectionResult) -> Void)? { get set }
    var onCopyIntent: (() -> Void)? { get set }
    var onTranslationGesture: ((TimeInterval) -> Void)? { get set }
    var onStop: ((String) -> Void)? { get set }
    func setClipboardFallbackEnabled(_ enabled: Bool)
    func setCopyInterval(_ interval: DoubleCopyInterval)
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
    public var onCopyIntent: (() -> Void)?
    public var onTranslationGesture: ((TimeInterval) -> Void)?
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
        selection.onCopyIntent = { [weak self] in
            guard let self, self.running else { return }
            self.onCopyIntent?()
        }
        selection.onTranslationGesture = { [weak self] time in
            guard let self, self.running else { return }
            self.onTranslationGesture?(time)
        }
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
        events.setClipboardFallbackEnabled(enabled)
    }

    public var copyInterval: DoubleCopyInterval { selection.interval }

    public func setCopyInterval(_ interval: DoubleCopyInterval) {
        guard selection.interval != interval else { return }
        selection.setInterval(interval)
        resetSource()
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
            try events.install(observe: { [weak self] event, revisionBeforeCopy in
                guard let self, self.running, self.registration == registration else { return }
                self.observe(event, revisionBeforeCopy: revisionBeforeCopy)
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
                ? "Secure Input enabled; monitoring paused."
                : "Accessibility or Input Monitoring permission lost; monitoring stopped.")
    }

    private func observe(_ event: NSEvent, revisionBeforeCopy: Int?) {
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
            isRepeat: event.isARepeat,
            revisionBeforeCopy: revisionBeforeCopy
        )
    }
}

@MainActor
protocol PassiveCopyEvents {
    func install(observe: @escaping (NSEvent, Int?) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws
    func setClipboardFallbackEnabled(_ enabled: Bool)
    func remove()
}

extension PassiveCopyEvents {
    func setClipboardFallbackEnabled(_ enabled: Bool) {}
}

@MainActor
final class SystemPassiveCopyEvents: PassiveCopyEvents {
    private var token: Any?
    private var localToken: Any?
    private var workspaceTokens: [NSObjectProtocol] = []
    private var secureTimer: Timer?
    private var keyTap: CFMachPort?
    private var keySource: CFRunLoopSource?
    private var keyObserver: ((NSEvent, Int?) -> Void)?
    private var tapInvalidation: (() -> Void)?
    private var tapTick: (() -> Void)?
    private var registration = UUID()
    private var clipboardFallbackEnabled = false

    deinit {
        // The tap's C context is unretained; never leave it registered after its
        // Swift owner goes away, even if a caller omitted stop().
        if let keyTap { CFMachPortInvalidate(keyTap) }
        if let keySource { CFRunLoopRemoveSource(CFRunLoopGetMain(), keySource, .commonModes) }
    }

    func setClipboardFallbackEnabled(_ enabled: Bool) {
        clipboardFallbackEnabled = enabled
    }

    func install(observe: @escaping (NSEvent, Int?) -> Void, invalidate: @escaping () -> Void,
                 tick: @escaping () -> Void) throws {
        registration = UUID()
        keyObserver = observe
        tapInvalidation = invalidate
        tapTick = tick
        // A listen-only/global observer runs AFTER dispatch, so a fast first copy
        // can already be the baseline and a deduplicated second copy never advances
        // it. This tap always returns the identical event. Only a counter is sampled
        // here; AX, payload reads and selection handling are deferred until it returns.
        keyTap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                  options: .defaultTap,
                                  eventsOfInterest: CGEventMask(1) << CGEventType.keyDown.rawValue,
                                  callback: { _, type, event, context in
            if let context {
                let owner = Unmanaged<SystemPassiveCopyEvents>.fromOpaque(context).takeUnretainedValue()
                MainActor.assumeIsolated { owner.receiveKey(type: type, event: event) }
            }
            return Unmanaged.passUnretained(event)
        }, userInfo: Unmanaged.passUnretained(self).toOpaque())
        guard let keyTap, let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, keyTap, 0) else {
            throw ProbeError.permissionDenied
        }
        keySource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: keyTap, enable: true)
        let mouseMask: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown, .otherMouseDown, .scrollWheel]
        token = NSEvent.addGlobalMonitorForEvents(matching: mouseMask) { event in
            // AppKit global event monitors are delivered on the main thread.
            MainActor.assumeIsolated {
                observe(event, nil)
            }
        }
        guard token != nil else { throw ProbeError.permissionDenied }
        localToken = NSEvent.addLocalMonitorForEvents(matching: mouseMask.union(.keyDown),
                                                     handler: Self.localObserver(invalidate: invalidate))
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
        secureTimer = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { _ in
            MainActor.assumeIsolated { tick() }
        }
    }

    func remove() {
        registration = UUID()
        if let keyTap { CFMachPortInvalidate(keyTap) }
        if let keySource { CFRunLoopRemoveSource(CFRunLoopGetMain(), keySource, .commonModes) }
        keyTap = nil
        keySource = nil
        keyObserver = nil
        tapInvalidation = nil
        tapTick = nil
        if let token = token { NSEvent.removeMonitor(token) }
        token = nil
        if let localToken { NSEvent.removeMonitor(localToken) }
        localToken = nil
        for token in workspaceTokens { NSWorkspace.shared.notificationCenter.removeObserver(token) }
        workspaceTokens = []
        secureTimer?.invalidate()
        secureTimer = nil
    }

    private func receiveKey(type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            tapInvalidation?()
            tapTick?()
            if let keyTap { CGEvent.tapEnable(tap: keyTap, enable: true) }
            return
        }
        guard type == .keyDown, let native = NSEvent(cgEvent: event) else { return }
        let flags = native.modifierFlags.intersection(.deviceIndependentFlagsMask).subtracting([.capsLock])
        let isCopy = flags == [.command] && native.charactersIgnoringModifiers?.lowercased() == "c"
        let revision = clipboardFallbackEnabled && isCopy && !native.isARepeat
            ? NSPasteboard.general.changeCount : nil
        let registration = registration
        DispatchQueue.main.async { [weak self] in
            guard let self, self.registration == registration else { return }
            self.keyObserver?(native, revision)
        }
    }

    static func localObserver(invalidate: @escaping () -> Void) -> (NSEvent) -> NSEvent? {
        { event in
            MainActor.assumeIsolated { invalidate() }
            return event
        }
    }
}
