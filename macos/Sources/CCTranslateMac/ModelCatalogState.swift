import Foundation

struct DiscoveredCodexModel: Identifiable {
    let id: CodexModelSettings.ChoiceID
    let name: String
    let description: String
}

struct ModelCatalogState {
    enum Failure: Equatable {
        case missingCLI, unavailable, discovery, tooLarge, cleanup, connection, cliChanged
    }
    enum Phase: Equatable {
        case idle, waiting, connecting, loading, cancelling, loaded, cancelled, failed(Failure)
    }
    private(set) var phase: Phase = .idle
    private(set) var models: [DiscoveredCodexModel] = []
    private(set) var scope: CodexModelSettings.ChoiceID?
    private(set) var intent = UUID()
    private(set) var requestID: String?
    private var requestIntent: UUID?
    var busy: Bool { requestID != nil || phase == .waiting || phase == .connecting }
    var pending: Bool { requestID == nil && (phase == .waiting || phase == .connecting) }
    func matches(scope: String) -> Bool { CodexModelSettings.sameID(self.scope?.value, scope) }

    mutating func begin(scope: String) {
        intent = UUID()
        self.scope = scope.isEmpty ? nil : CodexModelSettings.ChoiceID(value: scope)
        models = []
        phase = .waiting
    }

    mutating func bind(scope: String) { self.scope = CodexModelSettings.ChoiceID(value: scope) }
    mutating func connecting() { phase = .connecting }

    mutating func submit(id: String) {
        requestID = id
        requestIntent = intent
        phase = .loading
    }

    @discardableResult
    mutating func cancel() -> String? {
        guard busy, phase != .cancelling else { return nil }
        intent = UUID()
        models = []
        phase = requestID == nil ? .cancelled : .cancelling
        return requestID
    }

    mutating func fail(_ failure: Failure) {
        intent = UUID()
        models = []
        phase = .failed(failure)
    }

    mutating func disconnect() {
        if busy || scope != nil {
            if phase == .cancelling || phase == .cancelled { phase = .cancelled }
            else if case .failed = phase {} else { fail(.connection) }
        }
        models = []
        requestID = nil
        requestIntent = nil
    }

    // A cancelled or superseded request still owns its drain, but never publishes choices.
    mutating func finish(id: String, models: [DiscoveredCodexModel]?,
                         failure: Failure? = nil, cancelled: Bool = false) {
        guard requestID == id else { return }
        let current = requestIntent == intent
        requestID = nil
        requestIntent = nil
        guard current else {
            if phase == .cancelling { phase = failure == .cleanup ? .failed(.cleanup) : .cancelled }
            return
        }
        if let failure { fail(failure) }
        else if cancelled { phase = .cancelled }
        else if let models { self.models = models; phase = .loaded }
    }

    func choices(addingTo values: [String], scope: String) -> [String] {
        guard matches(scope: scope) else { return values }
        var seen = Set(values.map { CodexModelSettings.ChoiceID(value: $0) })
        return values + models.compactMap { row in
            seen.insert(row.id).inserted ? row.id.value : nil
        }
    }
}
