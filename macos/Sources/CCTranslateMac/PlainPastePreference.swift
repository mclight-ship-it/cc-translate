import Foundation

struct PlainPastePreference {
    enum Failure: Equatable {
        case missingFlag, operation(String), interrupted
        case differentReadback(expected: Bool, actual: Bool)
    }
    enum Phase: Equatable {
        case unloaded, waiting, saving, reading, confirmed, failed(Failure)
    }
    struct Intent {
        let revision: UUID
        let enabled: Bool
    }
    private struct Save {
        let id: String
        let intent: Intent
        var acknowledged = false
        var readID: String?
    }
    private struct Read {
        let id: String
        let revision: UUID
        let reconcile: Bool
        let previousPhase: Phase
    }

    private(set) var desired: Bool?
    private(set) var stored: Bool?
    private(set) var authorized = false
    private(set) var phase: Phase = .unloaded
    private(set) var queued: Intent?
    private var revision = UUID()
    private var save: Save?
    private var read: Read?
    var toggleValue: Bool { desired ?? stored ?? false }
    var canWrite: Bool { queued != nil && save == nil && phase == .waiting }
    func ownsSave(_ id: String) -> Bool { save?.id == id || save?.readID == id }

    mutating func choose(_ enabled: Bool) {
        revision = UUID()
        desired = enabled
        authorized = false
        queued = Intent(revision: revision, enabled: enabled)
        phase = .waiting
    }

    mutating func beginRestore() { phase = .reading }

    mutating func beginSave(id: String) -> Bool? {
        guard let intent = queued, save == nil else { return nil }
        queued = nil
        save = Save(id: id, intent: intent)
        phase = .saving
        return intent.enabled
    }

    mutating func saved(id: String) {
        guard save?.id == id else { return }
        save?.acknowledged = true
        if save?.intent.revision == revision { phase = .reading }
    }

    mutating func beginRead(id: String, reconcile: Bool) {
        read = Read(id: id, revision: revision, reconcile: reconcile, previousPhase: phase)
        if save?.acknowledged == true { save?.readID = id }
        if desired == nil || queued == nil { phase = .reading }
    }

    mutating func loaded(id: String, enabled: Bool?) {
        guard let read, read.id == id else { return }
        self.read = nil
        let completed = save?.readID == id ? save : nil
        if completed != nil { save = nil }
        stored = enabled
        guard let enabled else {
            authorized = false
            phase = .failed(.missingFlag)
            return
        }
        if !enabled { authorized = false }
        guard read.revision == revision,
              completed == nil || completed?.intent.revision == revision else { return }
        if let completed {
            confirm(enabled, expected: completed.intent.enabled)
        } else if desired == nil {
            authorized = enabled
            phase = .confirmed
        } else if read.reconcile, queued == nil, save == nil, let desired {
            confirm(enabled, expected: desired)
        } else if queued != nil {
            phase = .waiting
        } else {
            phase = read.previousPhase
        }
    }

    private mutating func confirm(_ enabled: Bool, expected: Bool) {
        guard enabled == expected else {
            authorized = false
            phase = .failed(.differentReadback(expected: expected, actual: enabled))
            return
        }
        desired = nil
        authorized = enabled
        phase = .confirmed
    }

    mutating func failed(id: String, code: String) {
        let ownsSave = save?.id == id || save?.readID == id
        let ownsRead = read?.id == id
        guard ownsSave || ownsRead else { return }
        let failedRevision = ownsSave ? save?.intent.revision : read?.revision
        if ownsSave { save = nil }
        if ownsRead { read = nil }
        authorized = false
        phase = queued != nil && failedRevision != revision ? .waiting : .failed(.operation(code))
    }

    mutating func connectionLost(preservingQueuedChoice: Bool = false) {
        authorized = false
        stored = nil
        save = nil
        read = nil
        if !preservingQueuedChoice { queued = nil }
        phase = queued != nil ? .waiting : .failed(.interrupted)
    }
}
