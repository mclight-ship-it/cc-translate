import AppKit
import Carbon

enum PlainPasteKeyEvent { case pressed, released }

enum PlainPasteRegistrationError: Error, Equatable {
    case conflict
    case unavailable(Int32)
    case releaseFailed(Int32)
}

@MainActor
protocol PlainPasteShortcutLease: AnyObject {
    func release() -> Result<Void, PlainPasteRegistrationError>
}

@MainActor
protocol PlainPasteShortcutRegistering {
    func register(_ handler: @escaping @MainActor (PlainPasteKeyEvent) -> Void)
        -> Result<any PlainPasteShortcutLease, PlainPasteRegistrationError>
}

private final class PlainPasteHotKeyContext {
    let handler: @MainActor (PlainPasteKeyEvent) -> Void
    private let lock = NSLock()
    private var live = true
    init(_ handler: @escaping @MainActor (PlainPasteKeyEvent) -> Void) { self.handler = handler }
    var isLive: Bool { lock.lock(); defer { lock.unlock() }; return live }
    func deactivate() { lock.lock(); live = false; lock.unlock() }
}

// Deinit transfers sole cleanup ownership of these opaque Carbon handles to the main actor.
private struct PlainPasteHotKeyCleanup: @unchecked Sendable {
    let key: EventHotKeyRef?
    let eventHandler: EventHandlerRef?
    let retainedContext: Unmanaged<PlainPasteHotKeyContext>

    @MainActor
    func run() {
        if let key {
            let status = UnregisterEventHotKey(key)
            if status != noErr { NSLog("CCTranslate plain-paste hotkey cleanup: %d", status) }
        }
        if let eventHandler {
            let status = RemoveEventHandler(eventHandler)
            // Failed removal leaves Carbon holding this inert context; never free its callback pointer.
            if status == noErr { retainedContext.release() }
            else { NSLog("CCTranslate plain-paste handler cleanup: %d", status) }
        }
    }
}

@MainActor
private final class CarbonPlainPasteLease: PlainPasteShortcutLease {
    private var key: EventHotKeyRef?
    private var eventHandler: EventHandlerRef?
    private let context: PlainPasteHotKeyContext
    private let retainedContext: Unmanaged<PlainPasteHotKeyContext>

    init(key: EventHotKeyRef?, eventHandler: EventHandlerRef,
         context: PlainPasteHotKeyContext, retainedContext: Unmanaged<PlainPasteHotKeyContext>) {
        self.key = key
        self.eventHandler = eventHandler
        self.context = context
        self.retainedContext = retainedContext
    }

    func release() -> Result<Void, PlainPasteRegistrationError> {
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
        let cleanup = PlainPasteHotKeyCleanup(key: key, eventHandler: eventHandler, retainedContext: retainedContext)
        if Thread.isMainThread { MainActor.assumeIsolated { cleanup.run() } }
        else { DispatchQueue.main.async { cleanup.run() } }
    }
}

@MainActor
struct CarbonPlainPasteShortcut: PlainPasteShortcutRegistering {
    static let signature: OSType = 0x43435054
    static let keyCode = UInt32(kVK_ANSI_V)
    static let modifiers = UInt32(cmdKey | optionKey | shiftKey)
    static let options = UInt32(kEventHotKeyExclusive)
    static let releasingKeys: [CGKeyCode] = [
        CGKeyCode(kVK_ANSI_V), CGKeyCode(kVK_Command), CGKeyCode(kVK_RightCommand),
        CGKeyCode(kVK_Shift), CGKeyCode(kVK_RightShift),
        CGKeyCode(kVK_Option), CGKeyCode(kVK_RightOption),
        CGKeyCode(kVK_Control), CGKeyCode(kVK_RightControl)
    ]

    func register(_ callback: @escaping @MainActor (PlainPasteKeyEvent) -> Void)
        -> Result<any PlainPasteShortcutLease, PlainPasteRegistrationError> {
        let context = PlainPasteHotKeyContext(callback)
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
                                    nil, MemoryLayout<EventHotKeyID>.size, nil, &identity) == noErr,
                  identity.signature == 0x43435054, identity.id == 1 else {
                return OSStatus(eventNotHandledErr)
            }
            let box = Unmanaged<PlainPasteHotKeyContext>.fromOpaque(pointer).takeUnretainedValue()
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
        let result = RegisterEventHotKey(Self.keyCode, Self.modifiers,
                                         EventHotKeyID(signature: Self.signature, id: 1),
                                         GetApplicationEventTarget(), Self.options, &key)
        let lease = CarbonPlainPasteLease(key: key, eventHandler: eventHandler,
                                         context: context, retainedContext: retained)
        guard result == noErr, key != nil else {
            if case .failure(let cleanup) = lease.release() { return .failure(cleanup) }
            return .failure(result == OSStatus(eventHotKeyExistsErr)
                            ? .conflict : .unavailable(result == noErr ? OSStatus(paramErr) : result))
        }
        return .success(lease)
    }
}
