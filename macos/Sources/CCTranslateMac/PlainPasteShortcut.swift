import AppKit
import Carbon

enum NativeShortcutKeyEvent: Equatable, Sendable { case pressed, released }

enum NativeShortcutRegistrationError: Error, Equatable {
    case conflict
    case unavailable(Int32)
    case releaseFailed(Int32)
}

@MainActor
protocol NativeShortcutLease: AnyObject {
    func release() -> Result<Void, NativeShortcutRegistrationError>
}

@MainActor
protocol NativeShortcutRegistering {
    func register(_ handler: @escaping @MainActor (NativeShortcutKeyEvent) -> Void)
        -> Result<any NativeShortcutLease, NativeShortcutRegistrationError>
}

typealias PlainPasteKeyEvent = NativeShortcutKeyEvent
typealias PlainPasteRegistrationError = NativeShortcutRegistrationError
typealias PlainPasteShortcutLease = NativeShortcutLease
typealias PlainPasteShortcutRegistering = NativeShortcutRegistering

struct NativeShortcutBinding: Equatable, Sendable {
    let signature: UInt32
    let keyCode: UInt32
    let modifiers: UInt32

    static let plainPaste = NativeShortcutBinding(
        signature: 0x43435054, keyCode: UInt32(kVK_ANSI_V), modifiers: UInt32(cmdKey | optionKey | shiftKey))
    static let screenshot = NativeShortcutBinding(
        signature: 0x43435343, keyCode: UInt32(kVK_ANSI_X), modifiers: UInt32(cmdKey | optionKey | shiftKey))

    var identity: EventHotKeyID { EventHotKeyID(signature: signature, id: 1) }
    func matches(_ identity: EventHotKeyID) -> Bool { identity.signature == signature && identity.id == 1 }
}

private final class NativeHotKeyContext {
    let binding: NativeShortcutBinding
    let handler: @MainActor (NativeShortcutKeyEvent) -> Void
    private let lock = NSLock()
    private var live = true
    init(binding: NativeShortcutBinding, handler: @escaping @MainActor (NativeShortcutKeyEvent) -> Void) {
        self.binding = binding
        self.handler = handler
    }
    var isLive: Bool { lock.lock(); defer { lock.unlock() }; return live }
    func deactivate() { lock.lock(); live = false; lock.unlock() }
}

// Deinit transfers sole cleanup ownership of these opaque Carbon handles to the main actor.
private struct NativeHotKeyCleanup: @unchecked Sendable {
    let key: EventHotKeyRef?
    let eventHandler: EventHandlerRef?
    let retainedContext: Unmanaged<NativeHotKeyContext>

    @MainActor
    func run() {
        if let key {
            let status = UnregisterEventHotKey(key)
            if status != noErr { NSLog("CCTranslate native hotkey cleanup: %d", status) }
        }
        if let eventHandler {
            let status = RemoveEventHandler(eventHandler)
            // Failed removal leaves Carbon holding this inert context; never free its callback pointer.
            if status == noErr { retainedContext.release() }
            else { NSLog("CCTranslate native hotkey handler cleanup: %d", status) }
        }
    }
}

@MainActor
private final class CarbonNativeShortcutLease: NativeShortcutLease {
    private var key: EventHotKeyRef?
    private var eventHandler: EventHandlerRef?
    private let context: NativeHotKeyContext
    private let retainedContext: Unmanaged<NativeHotKeyContext>

    init(key: EventHotKeyRef?, eventHandler: EventHandlerRef,
         context: NativeHotKeyContext, retainedContext: Unmanaged<NativeHotKeyContext>) {
        self.key = key
        self.eventHandler = eventHandler
        self.context = context
        self.retainedContext = retainedContext
    }

    func release() -> Result<Void, NativeShortcutRegistrationError> {
        context.deactivate()
        var failure: OSStatus?
        if let key {
            let status = UnregisterEventHotKey(key)
            if status == noErr { self.key = nil } else { failure = status }
        }
        if let eventHandler {
            let status = RemoveEventHandler(eventHandler)
            if status == noErr {
                self.eventHandler = nil
                retainedContext.release()
            } else { failure = status }
        }
        if let failure { return .failure(.releaseFailed(failure)) }
        return .success(())
    }

    deinit {
        context.deactivate()
        let cleanup = NativeHotKeyCleanup(key: key, eventHandler: eventHandler, retainedContext: retainedContext)
        if Thread.isMainThread { MainActor.assumeIsolated { cleanup.run() } }
        else { DispatchQueue.main.async { cleanup.run() } }
    }
}

@MainActor
struct CarbonPlainPasteShortcut: PlainPasteShortcutRegistering {
    static let signature: OSType = NativeShortcutBinding.plainPaste.signature
    static let keyCode = NativeShortcutBinding.plainPaste.keyCode
    static let modifiers = NativeShortcutBinding.plainPaste.modifiers
    static let options = CarbonNativeShortcut.options
    static let releasingKeys: [CGKeyCode] = [
        CGKeyCode(kVK_ANSI_V), CGKeyCode(kVK_Command), CGKeyCode(kVK_RightCommand),
        CGKeyCode(kVK_Shift), CGKeyCode(kVK_RightShift),
        CGKeyCode(kVK_Option), CGKeyCode(kVK_RightOption),
        CGKeyCode(kVK_Control), CGKeyCode(kVK_RightControl)
    ]

    func register(_ callback: @escaping @MainActor (PlainPasteKeyEvent) -> Void)
        -> Result<any PlainPasteShortcutLease, PlainPasteRegistrationError> {
        CarbonNativeShortcut(binding: .plainPaste).register(callback)
    }
}

@MainActor
struct CarbonNativeShortcut: NativeShortcutRegistering {
    static let options = UInt32(kEventHotKeyExclusive)
    let binding: NativeShortcutBinding

    func register(_ callback: @escaping @MainActor (NativeShortcutKeyEvent) -> Void)
        -> Result<any NativeShortcutLease, NativeShortcutRegistrationError> {
        let context = NativeHotKeyContext(binding: binding, handler: callback)
        let retained = Unmanaged.passRetained(context)
        var types = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))
        ]
        var eventHandler: EventHandlerRef?
        let installed = InstallEventHandler(GetApplicationEventTarget(), { _, event, pointer in
            guard let event, let pointer, Thread.isMainThread else { return OSStatus(eventNotHandledErr) }
            var identity = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                    nil, MemoryLayout<EventHotKeyID>.size, nil, &identity) == noErr else {
                return OSStatus(eventNotHandledErr)
            }
            let box = Unmanaged<NativeHotKeyContext>.fromOpaque(pointer).takeUnretainedValue()
            guard box.binding.matches(identity) else { return OSStatus(eventNotHandledErr) }
            guard box.isLive else { return noErr }
            let kind = GetEventKind(event)
            guard kind == UInt32(kEventHotKeyPressed) || kind == UInt32(kEventHotKeyReleased) else {
                return OSStatus(eventNotHandledErr)
            }
            MainActor.assumeIsolated {
                box.handler(kind == UInt32(kEventHotKeyPressed) ? .pressed : .released)
            }
            return noErr
        }, types.count, &types, retained.toOpaque(), &eventHandler)
        guard installed == noErr, let eventHandler else {
            retained.release()
            return .failure(.unavailable(installed == noErr ? OSStatus(paramErr) : installed))
        }
        var key: EventHotKeyRef?
        let result = RegisterEventHotKey(binding.keyCode, binding.modifiers, binding.identity,
                                         GetApplicationEventTarget(), Self.options, &key)
        let lease = CarbonNativeShortcutLease(key: key, eventHandler: eventHandler,
                                         context: context, retainedContext: retained)
        guard result == noErr, key != nil else {
            if case .failure(let cleanup) = lease.release() { return .failure(cleanup) }
            return .failure(result == OSStatus(eventHotKeyExistsErr)
                            ? .conflict : .unavailable(result == noErr ? OSStatus(paramErr) : result))
        }
        return .success(lease)
    }
}
