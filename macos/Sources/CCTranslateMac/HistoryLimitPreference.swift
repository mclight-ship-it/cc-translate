import Foundation

struct HistoryLimitPreference {
    typealias Phase = IntegerPreference.Phase

    struct Reduction: Equatable {
        let from: Int64
        let to: Int64
    }

    static let supported: ClosedRange<Int64> = 1...10_000
    private var value = IntegerPreference(supported: Self.supported, invalidReadbackCode: "invalid_history_limit")
    var saved: Int64? { value.saved }
    var draft: String { value.draft }
    var phase: Phase { value.phase }
    private(set) var confirmation: Reduction?

    var proposed: Int64? { value.proposed }

    func owns(_ id: String) -> Bool { value.owns(id) }

    mutating func edit(_ text: String) {
        // A native field can recommit the same draft while focus moves to confirmation.
        guard !text.utf8.elementsEqual(value.draft.utf8) else { return }
        value.edit(text)
        confirmation = nil
    }

    mutating func propose() -> Int64? {
        guard let proposed = value.propose(), let saved else { return nil }
        if proposed < saved {
            confirmation = Reduction(from: saved, to: proposed)
            return nil
        }
        return proposed
    }

    mutating func confirm() -> Int64? {
        guard let confirmation, confirmation.from == saved, confirmation.to == proposed else {
            confirmation = nil
            value.reject("confirmation_changed")
            return nil
        }
        self.confirmation = nil
        return confirmation.to
    }

    mutating func cancelConfirmation() { confirmation = nil }

    mutating func beginSave(id: String, value: Int64) {
        self.value.beginSave(id: id, value: value)
    }

    mutating func beginRead(id: String, afterSave: Bool) {
        value.beginRead(id: id, afterSave: afterSave)
    }

    mutating func loaded(_ value: Int64?, id: String) {
        self.value.loaded(value, id: id)
        if confirmation?.from != value { confirmation = nil }
    }

    mutating func rejectUnavailable() {
        confirmation = nil
        value.reject("settings_unavailable")
    }

    mutating func fail(_ code: String) {
        confirmation = nil
        value.fail(code)
    }

    mutating func connectionLost() {
        value.connectionLost()
        confirmation = nil
    }
}
