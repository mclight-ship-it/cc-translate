import Combine
import CoreGraphics
import Foundation
import CCTranslateSupport

struct PlainPasteServiceState {
    var enabled: Bool
    var busy: Bool
    var status: PlainTextPasteStatus
}

@MainActor
protocol PlainPasteServicing: AnyObject {
    var state: PlainPasteServiceState { get }
    var updates: AnyPublisher<PlainPasteServiceState, Never> { get }
    func setEnabled(_ enabled: Bool)
    func requestPaste(releasing: [CGKeyCode]) -> PlainTextPasteAdmission
    func cancel()
    func shutdown()
}

@MainActor
final class NativePlainPasteService: PlainPasteServicing {
    private let service: PlainTextPasteService
    init(service: PlainTextPasteService? = nil) { self.service = service ?? PlainTextPasteService(enabled: false) }
    var state: PlainPasteServiceState {
        PlainPasteServiceState(enabled: service.isEnabled, busy: service.isBusy, status: service.status)
    }
    var updates: AnyPublisher<PlainPasteServiceState, Never> {
        service.$isEnabled.combineLatest(service.$isBusy, service.$status)
            .map { PlainPasteServiceState(enabled: $0, busy: $1, status: $2) }.eraseToAnyPublisher()
    }
    func setEnabled(_ enabled: Bool) { service.setEnabled(enabled) }
    func requestPaste(releasing: [CGKeyCode]) -> PlainTextPasteAdmission { service.requestPaste(releasing: releasing) }
    func cancel() { service.cancel() }
    func shutdown() { service.shutdown() }
}

@MainActor
final class PlainPasteModel: ObservableObject {
    enum Registration: Equatable {
        case off, registering, registered, failed(PlainPasteRegistrationError)
    }
    @Published private(set) var preference = PlainPastePreference()
    @Published private(set) var registration: Registration = .off
    @Published private(set) var serviceState: PlainPasteServiceState
    @Published private(set) var stoppingAction = false
    @Published private(set) var lastAdmission: PlainTextPasteAdmission?
    @Published private(set) var lastRoute: PlainPasteRoute?
    private(set) var isShutDown = false
    var onDrained: (() -> Void)?

    private let service: any PlainPasteServicing
    private let registrar: any PlainPasteShortcutRegistering
    private let routing: any PlainPasteRouting
    private var lease: (any PlainPasteShortcutLease)?
    private var observation: AnyCancellable?
    private var epoch = UUID()
    private var keyHeld = false
    private var attemptedRegistration = false
    private var registering = false
    var canRetryRegistration: Bool {
        if case .failed(.releaseFailed(_)) = registration, lease == nil { return false }
        return !isShutDown && !registering && registration != .registered &&
            (preference.authorized || lease != nil)
    }

    init(service: (any PlainPasteServicing)? = nil,
         registrar: (any PlainPasteShortcutRegistering)? = nil,
         routing: (any PlainPasteRouting)? = nil) {
        let service = service ?? NativePlainPasteService()
        self.service = service
        self.registrar = registrar ?? CarbonPlainPasteShortcut()
        self.routing = routing ?? NativePlainPasteRouting()
        serviceState = service.state
        observation = service.updates.sink { [weak self] state in
            guard let self else { return }
            let wasBusy = self.serviceState.busy
            self.serviceState = state
            if !state.busy {
                self.stoppingAction = false
                if wasBusy { self.onDrained?() }
            }
        }
    }

    func choose(_ enabled: Bool) {
        guard !isShutDown else { return }
        preference.choose(enabled)
        suspend()
    }

    func beginRestore() {
        guard !isShutDown else { return }
        preference.beginRestore()
    }

    func beginSave(id: String) -> Bool? {
        guard !isShutDown else { return nil }
        return preference.beginSave(id: id)
    }

    func saved(id: String) { preference.saved(id: id) }

    func beginRead(id: String, reconcile: Bool) { preference.beginRead(id: id, reconcile: reconcile) }

    func loaded(id: String, enabled: Bool?) {
        guard !isShutDown else { return }
        preference.loaded(id: id, enabled: enabled)
        if preference.authorized { registerIfNeeded() } else { suspend() }
    }

    func failed(id: String, code: String) {
        preference.failed(id: id, code: code)
        if !preference.authorized { suspend() }
    }

    func connectionLost(preservingQueuedChoice: Bool = false) {
        preference.connectionLost(preservingQueuedChoice: preservingQueuedChoice)
        suspend()
    }

    func retryRegistration() {
        guard canRetryRegistration else { return }
        epoch = UUID()
        keyHeld = false
        if !releaseLease() { return }
        attemptedRegistration = false
        if preference.authorized { registerIfNeeded() }
    }

    func cancelAction() {
        guard !isShutDown, service.state.busy else { return }
        stoppingAction = true
        service.cancel()
    }

    func shutdown() {
        guard !isShutDown else { return }
        isShutDown = true
        epoch = UUID()
        keyHeld = false
        if service.state.busy { stoppingAction = true }
        _ = releaseLease()
        service.shutdown()
    }

    private func suspend() {
        epoch = UUID()
        keyHeld = false
        attemptedRegistration = false
        if service.state.busy { stoppingAction = true }
        if service.state.enabled || service.state.busy { service.setEnabled(false) }
        _ = releaseLease()
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

    private func registerIfNeeded() {
        guard preference.authorized, !isShutDown, !attemptedRegistration, !registering else { return }
        guard releaseLease() else { return }
        attemptedRegistration = true
        registering = true
        registration = .registering
        let attempt = epoch
        let result = registrar.register { [weak self] event in self?.handle(event, epoch: attempt) }
        registering = false
        guard attempt == epoch, preference.authorized, !isShutDown else {
            if case .success(let obsolete) = result {
                if case .failure(let failure) = obsolete.release() {
                    lease = obsolete
                    registration = .failed(failure)
                } else { registration = .off }
            } else if registration == .registering {
                registration = .off
            }
            return
        }
        switch result {
        case .failure(let error): registration = .failed(error)
        case .success(let lease):
            self.lease = lease
            registration = .registered
            service.setEnabled(true)
        }
    }

    private func handle(_ event: PlainPasteKeyEvent, epoch: UUID) {
        guard epoch == self.epoch, preference.authorized, registration == .registered, !isShutDown else { return }
        switch event {
        case .released: keyHeld = false
        case .pressed:
            guard !keyHeld else { return }
            keyHeld = true
            let destination = routing.destination()
            guard epoch == self.epoch, preference.authorized, registration == .registered, !isShutDown else { return }
            switch destination {
            case .ownApplication:
                let dispatched = routing.pasteInOwnApplication()
                lastRoute = .ownApplication(dispatched: dispatched)
            case .externalApplication:
                let admission = service.requestPaste(releasing: CarbonPlainPasteShortcut.releasingKeys)
                lastAdmission = admission
                lastRoute = .externalApplication
            case .unavailable:
                lastRoute = .unavailable
            }
        }
    }
}
