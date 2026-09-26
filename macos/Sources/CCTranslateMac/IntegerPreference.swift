import Foundation

typealias IntegerPreference = NumericPreference<Int64>

struct NumericPreference<Value: Comparable & LosslessStringConvertible> {
    enum Phase: Equatable {
        case idle, saving, readingBack, saved, differentReadback, invalidInput
        case failed(String)
    }

    let supported: ClosedRange<Value>
    let invalidReadbackCode: String
    private(set) var saved: Value?
    private(set) var draft = ""
    private(set) var phase: Phase = .idle
    private var edited = false
    private var requestID: String?
    private var expected: Value?

    init(supported: ClosedRange<Value>, invalidReadbackCode: String) {
        self.supported = supported
        self.invalidReadbackCode = invalidReadbackCode
    }

    var proposed: Value? {
        guard let value = Value(draft.trimmingCharacters(in: .whitespacesAndNewlines)),
              supported.contains(value) else { return nil }
        return value
    }

    func owns(_ id: String) -> Bool { requestID == id }

    mutating func edit(_ text: String) {
        draft = text
        edited = true
        if phase == .invalidInput { phase = .idle }
    }

    mutating func resetDraft() {
        if let saved {
            draft = String(saved)
            edited = false
        }
    }

    mutating func propose() -> Value? {
        guard let value = proposed else { phase = .invalidInput; return nil }
        guard let saved, value != saved else { return nil }
        return value
    }

    mutating func beginSave(id: String, value: Value) {
        requestID = id
        expected = value
        phase = .saving
    }

    mutating func beginRead(id: String, afterSave: Bool) {
        requestID = id
        if !afterSave { expected = nil }
        phase = .readingBack
    }

    mutating func loaded(_ value: Value?, id: String) {
        guard let value else { fail(invalidReadbackCode); return }
        let confirmedSave = owns(id) && expected == value
        phase = owns(id) && expected != nil ? (confirmedSave ? .saved : .differentReadback) : .idle
        if !edited || (confirmedSave && proposed == value) {
            draft = String(value)
            edited = false
        }
        saved = value
        requestID = nil
        expected = nil
    }

    mutating func reject(_ code: String) { phase = .failed(code) }

    mutating func fail(_ code: String) {
        saved = nil
        requestID = nil
        expected = nil
        phase = .failed(code)
    }

    mutating func connectionLost() {
        if saved != nil || requestID != nil { fail("connection_closed") }
    }
}
