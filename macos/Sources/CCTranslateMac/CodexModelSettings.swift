import Foundation

struct CodexModelSettings {
    enum Validation: Equatable {
        case empty, whitespace, tooLong, preset
    }
    enum Failure: Equatable {
        case unavailable, busy, operation(String), invalidReadback, interrupted
        case invalidID(Validation)
        case differentReadback(expected: String, actual: String)
    }
    enum Phase: Equatable {
        case idle, saving(String), reading, applied(String), failed(Failure)
    }
    private(set) var draft = ""
    private(set) var draftEdited = false
    private(set) var editing = false
    private(set) var rememberedCustom: String?
    private(set) var savedProfile: String?
    private(set) var phase: Phase = .idle
    private(set) var requestID: String?
    private var requestedProfile: String?

    static func isPreset(_ value: String) -> Bool { value == "auto" || value == "auto-fast" }

    static func sameID(_ lhs: String?, _ rhs: String?) -> Bool {
        switch (lhs, rhs) {
        case let (lhs?, rhs?): return lhs.utf8.elementsEqual(rhs.utf8)
        case (nil, nil): return true
        default: return false
        }
    }

    static func validateCustom(_ value: String) -> Validation? {
        if value.isEmpty { return .empty }
        if value.utf8.count > 256 { return .tooLong }
        if value.unicodeScalars.contains(where: {
            CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0)
        }) { return .whitespace }
        if isPreset(value) { return .preset }
        return nil
    }

    func choices(selection: String) -> [String] {
        var values = ["auto-fast", "auto"]
        if let rememberedCustom, !Self.isPreset(rememberedCustom), rememberedCustom != selection {
            values.append(rememberedCustom)
        }
        if !Self.isPreset(selection) { values.append(selection) }
        return values
    }

    mutating func edit(_ value: String) {
        draft = value
        draftEdited = true
        if case .failed(.invalidID(_)) = phase { phase = .idle }
    }

    mutating func setEditing(_ value: Bool) {
        editing = value
        if !value, Self.sameID(draft, savedProfile) { draftEdited = false }
    }

    mutating func resetDraft(selection: String) {
        draft = Self.isPreset(selection) ? (rememberedCustom ?? "") : selection
        draftEdited = false
        if case .failed(.invalidID(_)) = phase { phase = .idle }
    }

    mutating func restoreCustom(_ value: String) {
        guard Self.validateCustom(value) == nil else { return }
        rememberedCustom = value
        if !draftEdited && !editing { draft = value }
    }

    mutating func beginSave(id: String, profile: String) {
        requestID = id
        requestedProfile = profile
        phase = .saving(profile)
    }

    mutating func beginRead(id: String, afterSave: Bool) {
        requestID = id
        if !afterSave { requestedProfile = nil }
        phase = .reading
    }

    // Every normalized read records the actual saved scalar. Only the matching operation
    // may complete an Apply; an older read must never replace a newer editor draft.
    @discardableResult
    mutating func loaded(profile: String, id: String) -> Failure? {
        if let requestID, requestID != id { return nil }
        savedProfile = profile
        if !Self.isPreset(profile) {
            rememberedCustom = profile
            if !draftEdited && !editing { draft = profile }
        }
        guard requestID == id else {
            if requestID == nil, case .applied = phase { phase = .idle }
            return nil
        }
        let requested = requestedProfile
        requestID = nil
        requestedProfile = nil
        if let requested, !Self.sameID(requested, profile) {
            let failure = Failure.differentReadback(expected: requested, actual: profile)
            phase = .failed(failure)
            return failure
        }
        phase = .applied(profile)
        if !editing && Self.sameID(draft, profile) { draftEdited = false }
        return nil
    }

    mutating func fail(id: String, failure: Failure) {
        guard requestID == id else { return }
        requestID = nil
        requestedProfile = nil
        phase = .failed(failure)
    }

    mutating func reject(_ failure: Failure) {
        guard requestID == nil else { return }
        phase = .failed(failure)
    }

    mutating func connectionLost() {
        if requestID != nil { phase = .failed(.interrupted) }
        requestID = nil
        requestedProfile = nil
        savedProfile = nil
    }
}
