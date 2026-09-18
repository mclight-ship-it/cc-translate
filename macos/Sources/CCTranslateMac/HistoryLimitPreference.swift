import Foundation

struct HistoryLimitPreference {
    enum Phase: Equatable {
        case idle, saving, readingBack, saved, differentReadback, invalidInput
        case failed(String)
    }

    struct Reduction: Equatable {
        let from: Int64
        let to: Int64
    }

    static let supported: ClosedRange<Int64> = 1...10_000
    private(set) var saved: Int64?
    private(set) var draft = ""
    private(set) var confirmation: Reduction?
    private(set) var phase: Phase = .idle
    private var edited = false
    private var requestID: String?
    private var expected: Int64?

    var proposed: Int64? {
        guard let value = Int64(draft.trimmingCharacters(in: .whitespacesAndNewlines)),
              Self.supported.contains(value) else { return nil }
        return value
    }

    func owns(_ id: String) -> Bool { requestID == id }

    mutating func edit(_ text: String) {
        draft = text
        edited = true
        confirmation = nil
        if phase == .invalidInput { phase = .idle }
    }

    mutating func propose() -> Int64? {
        guard let value = proposed else { phase = .invalidInput; return nil }
        guard let saved, value != saved else { return nil }
        if value < saved {
            confirmation = Reduction(from: saved, to: value)
            return nil
        }
        return value
    }

    mutating func confirm() -> Int64? {
        guard let confirmation, confirmation.from == saved, confirmation.to == proposed else {
            confirmation = nil
            phase = .failed("confirmation_changed")
            return nil
        }
        self.confirmation = nil
        return confirmation.to
    }

    mutating func cancelConfirmation() { confirmation = nil }

    mutating func beginSave(id: String, value: Int64) {
        requestID = id
        expected = value
        phase = .saving
    }

    mutating func beginRead(id: String, afterSave: Bool) {
        requestID = id
        if !afterSave { expected = nil }
        phase = .readingBack
    }

    mutating func loaded(_ value: Int64?, id: String) {
        guard let value else { fail("invalid_history_limit"); return }
        let confirmedSave = owns(id) && expected == value
        phase = owns(id) && expected != nil ? (confirmedSave ? .saved : .differentReadback) : .idle
        if !edited || (confirmedSave && proposed == value) {
            draft = String(value)
            edited = false
        }
        if confirmation?.from != value { confirmation = nil }
        saved = value
        requestID = nil
        expected = nil
    }

    mutating func rejectUnavailable() {
        confirmation = nil
        phase = .failed("settings_unavailable")
    }

    mutating func fail(_ code: String) {
        saved = nil
        confirmation = nil
        requestID = nil
        expected = nil
        phase = .failed(code)
    }

    mutating func connectionLost() {
        if saved != nil || requestID != nil { fail("connection_closed") }
        confirmation = nil
    }
}
