import Combine
import Foundation

@MainActor
final class CaptureShortcutModel: ObservableObject {
    enum Registration: Equatable {
        case off, registering, registered, failed(NativeShortcutRegistrationError)
    }

    static let preferenceKey = "nativeScreenshotShortcutEnabled"
    @Published private(set) var enabled = false
    @Published private(set) var registration: Registration = .off
    private(set) var isShutDown = false
    var canCapture: @MainActor () -> Bool = { false }
    var onCapture: (@MainActor () -> Void)?

    private let preferences: UserDefaults?
    private let persistsPreferences: Bool
    private let registrar: any NativeShortcutRegistering
    private var lease: (any NativeShortcutLease)?
    private var restored = false
    private var registering = false
    private var epoch = UUID()
    private var keyHeld = false
    private var armed = false

    init(preferences: UserDefaults? = nil, persistsPreferences: Bool = true,
         registrar: (any NativeShortcutRegistering)? = nil) {
        self.preferences = preferences
        self.persistsPreferences = persistsPreferences
        self.registrar = registrar ?? CarbonNativeShortcut(binding: .screenshot)
    }

    var canRetry: Bool {
        if case .failed(.releaseFailed(_)) = registration, lease == nil { return false }
        return !isShutDown && !registering && registration != .registered && (enabled || lease != nil)
    }

    func restore() {
        guard !restored, !isShutDown else { return }
        restored = true
        guard persistsPreferences else { return }
        enabled = (preferences ?? .standard).object(forKey: Self.preferenceKey) as? Bool ?? false
        if enabled { register() }
    }

    func choose(_ value: Bool) {
        guard !isShutDown else { return }
        restored = true
        let changed = enabled != value
        enabled = value
        if persistsPreferences { (preferences ?? .standard).set(value, forKey: Self.preferenceKey) }
        guard changed else {
            if canRetry { retry() }
            return
        }
        epoch = UUID()
        cancelPendingTrigger()
        if value {
            if registration != .registered { register() }
        } else {
            _ = releaseLease()
        }
    }

    func retry() {
        guard canRetry else { return }
        epoch = UUID()
        cancelPendingTrigger()
        guard releaseLease() else { return }
        if enabled { register() }
    }

    func cancelPendingTrigger() {
        keyHeld = false
        armed = false
    }

    func shutdown() {
        guard !isShutDown else { return }
        isShutDown = true
        epoch = UUID()
        cancelPendingTrigger()
        _ = releaseLease()
    }

    private func register() {
        guard enabled, !isShutDown, !registering else { return }
        guard releaseLease() else { return }
        registering = true
        registration = .registering
        let attempt = epoch
        let result = registrar.register { [weak self] event in self?.handle(event, epoch: attempt) }
        registering = false
        guard epoch == attempt, enabled, !isShutDown else {
            if case .success(let obsolete) = result {
                if case .failure(let error) = obsolete.release() {
                    lease = obsolete
                    registration = .failed(error)
                } else { registration = .off }
            } else if registration == .registering {
                registration = .off
            }
            return
        }
        switch result {
        case .success(let lease):
            self.lease = lease
            registration = .registered
        case .failure(let error):
            registration = .failed(error)
        }
    }

    @discardableResult
    private func releaseLease() -> Bool {
        guard let lease else {
            if case .failed(.releaseFailed(_)) = registration { return false }
            registration = .off
            return true
        }
        switch lease.release() {
        case .success:
            self.lease = nil
            registration = .off
            return true
        case .failure(let error):
            registration = .failed(error)
            return false
        }
    }

    private func handle(_ event: NativeShortcutKeyEvent, epoch: UUID) {
        guard epoch == self.epoch, enabled, registration == .registered, !isShutDown else { return }
        switch event {
        case .pressed:
            guard !keyHeld else { return }
            keyHeld = true
            armed = canCapture()
        case .released:
            let requested = keyHeld && armed
            cancelPendingTrigger()
            guard requested, canCapture(), epoch == self.epoch, enabled,
                  registration == .registered, !isShutDown else { return }
            onCapture?()
        }
    }
}
