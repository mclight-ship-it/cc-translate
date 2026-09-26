import AppKit
import Combine
import CCTranslateSupport

@MainActor
protocol AppImageCleanup: AnyObject, Sendable {
    func cleanup() async throws
}

@MainActor
protocol AppImageAttachment: AppImageCleanup {
    var imagePath: String { get }
    var imageBytes: Int { get }
    var imageSHA256: String { get }
}

struct AppImageCreationFailure: Error, CustomStringConvertible, CustomDebugStringConvertible {
    let cleanup: AppImageCleanup
    var description: String { "AppImageCreationFailure(cleanupFailed)" }
    var debugDescription: String { description }
}

/// Owns files independently of the visible draft, including cancelled preparation and old requests.
@MainActor
final class ImageTranslationState: ObservableObject {
    typealias Factory = @MainActor (CGImage) async throws -> AppImageAttachment

    private final class Entry {
        var attachment: AppImageAttachment?
        var cleanup: AppImageCleanup?
        var creating = true
        var discarded = false
        var requestID: String?
        var connectionID: UUID?
        var sent = false
        var cleaning = false
        var cleanupFailed = false
    }

    let objectWillChange = ObservableObjectPublisher()
    var working: Bool { entries.values.contains { $0.creating || $0.cleaning } }
    var cleaning: Bool { entries.values.contains(where: \.cleaning) }
    var cleanupFailureCount: Int { entries.values.filter(\.cleanupFailed).count }
    private(set) var isShutDown = false
    private var entries: [UUID: Entry] = [:]
    private let factory: Factory
    var onPrepared: ((UUID) -> Void)?
    var onPreparationFailed: ((UUID) -> Void)?
    var onSettled: (() -> Void)?

    init(factory: @escaping Factory) { self.factory = factory }

    func prepare(_ image: CGImage, intent: UUID) {
        guard !isShutDown, entries[intent] == nil else {
            onPreparationFailed?(intent)
            return
        }
        let entry = Entry()
        entries[intent] = entry
        publish()
        Task {
            if entry.discarded || isShutDown {
                entries.removeValue(forKey: intent)
                publish()
                onSettled?()
                return
            }
            do {
                // Do not cancel this task: even a late-created file must acquire an explicit owner.
                let attachment = try await factory(image)
                entry.attachment = attachment
                entry.cleanup = attachment
                entry.creating = false
                if entry.discarded || isShutDown { clean(intent, entry) }
                else {
                    publish()
                    onPrepared?(intent)
                }
            } catch let failure as AppImageCreationFailure {
                let reportFailure = !entry.discarded && !isShutDown
                entry.cleanup = failure.cleanup
                entry.creating = false
                entry.discarded = true
                entry.cleanupFailed = true
                publish()
                if reportFailure { onPreparationFailed?(intent) }
                onSettled?()
            } catch {
                entries.removeValue(forKey: intent)
                publish()
                if !entry.discarded && !isShutDown { onPreparationFailed?(intent) }
                onSettled?()
            }
        }
    }

    func attachment(for intent: UUID) -> AppImageAttachment? {
        guard let entry = entries[intent], !entry.discarded, !entry.cleaning else { return nil }
        return entry.attachment
    }

    func owns(requestID: String?) -> Bool {
        guard let requestID else { return false }
        return entries.values.contains { $0.requestID == requestID }
    }

    func reserve(intent: UUID, requestID: String, connectionID: UUID) -> Bool {
        guard let entry = entries[intent], !entry.discarded, entry.attachment != nil else { return false }
        entry.requestID = requestID
        entry.connectionID = connectionID
        return true
    }

    func markSent(intent: UUID) -> Bool {
        guard let entry = entries[intent], !entry.discarded, entry.requestID != nil else { return false }
        entry.sent = true
        return true
    }

    func discardPending(_ intent: UUID) {
        guard let entry = entries[intent], entry.requestID == nil else { return }
        entry.discarded = true
        if !entry.creating { clean(intent, entry) }
    }

    /// A reserved request can be cancelled by a synchronous UI callback before it reaches transport.
    func cancelUnsent(requestID: String) -> Bool {
        guard let (intent, entry) = entries.first(where: { $0.value.requestID == requestID }),
              !entry.sent else { return false }
        entry.discarded = true
        clean(intent, entry)
        return true
    }

    func terminal(requestID: String) {
        for (intent, entry) in entries where entry.requestID == requestID {
            clean(intent, entry)
        }
    }

    func drained(connectionID: UUID) {
        for (intent, entry) in entries where entry.connectionID == connectionID {
            clean(intent, entry)
        }
    }

    func retryCleanup() {
        for (intent, entry) in entries where entry.cleanupFailed { clean(intent, entry, retry: true) }
    }

    func shutdown() {
        isShutDown = true
        for intent in Array(entries.keys) { discardPending(intent) }
    }

    private func clean(_ intent: UUID, _ entry: Entry, retry: Bool = false) {
        guard !entry.cleaning, !entry.cleanupFailed || retry, let cleanup = entry.cleanup else { return }
        entry.discarded = true
        entry.cleaning = true
        entry.cleanupFailed = false
        publish()
        Task {
            do {
                try await cleanup.cleanup()
                entries.removeValue(forKey: intent)
            } catch {
                entry.cleanupFailed = true
            }
            entry.cleaning = false
            publish()
            onSettled?()
        }
    }

    private func publish() {
        objectWillChange.send()
    }
}
