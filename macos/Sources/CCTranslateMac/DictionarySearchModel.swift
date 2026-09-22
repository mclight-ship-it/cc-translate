import Combine
import Foundation
import CCTranslateSupport

@MainActor
final class DictionarySearchModel: ObservableObject {
    enum Phase: Equatable {
        case idle, searching, hit, miss, disabled, unavailable, ineligible, failed(String)
    }

    @Published var query = ""
    @Published private(set) var submittedQuery = ""
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var output = ""
    @Published private(set) var sources: [DictionarySource] = []
    var send: ((DictionaryRequest, String) -> Bool)?
    private var requestID: String?
    var busy: Bool { requestID != nil }

    func search(language: String) {
        guard !busy else { return }
        let word = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !word.isEmpty else {
            output = ""
            sources = []
            phase = .ineligible
            return
        }
        let id = UUID().uuidString
        submittedQuery = word
        output = ""
        sources = []
        requestID = id
        phase = .searching
        // This page never upgrades a dictionary miss to an AI translation.
        let request = DictionaryRequest.lookup(text: word, appLanguage: language, origin: "text",
                                              useCache: true, recordHistory: false)
        if send?(request, id) != true {
            requestID = nil
            phase = .failed("not_connected")
        }
    }

    @discardableResult
    func handle(_ event: ServerEvent) -> Bool {
        guard event.id == requestID else { return false }
        guard event.isTerminal else { return true }
        requestID = nil
        guard event.type == "completed" else {
            phase = .failed(event.safeFailureCode)
            return true
        }
        do {
            let result = try DictionaryLookupResult(payload: event.payload)
            switch result.status {
            case "hit":
                guard let text = result.result?["text"]?.string else {
                    phase = .failed("invalid_dictionary_response")
                    return true
                }
                output = text
                sources = result.sources
                phase = .hit
            case "miss": phase = .miss
            case "disabled": phase = .disabled
            case "unavailable": phase = .unavailable
            case "ineligible": phase = .ineligible
            default: phase = .failed("invalid_dictionary_response")
            }
        } catch {
            phase = .failed("invalid_dictionary_response")
        }
        return true
    }

    func connectionLost() {
        guard busy else { return }
        requestID = nil
        phase = .failed("connection_lost")
    }

    func message(using model: ProbeModel) -> String {
        switch phase {
        case .idle: return model.text("Look up a word in the local dictionary.", "输入词语，查询本地词典。")
        case .searching: return model.text("Looking up…", "正在查询…")
        case .hit: return model.text("Local dictionary", "本地词典")
        case .miss: return model.text("No entry found for “\(submittedQuery)”.", "未找到“\(submittedQuery)”的词条。")
        case .disabled: return model.text("Turn on the local dictionary below to look up words.", "请在下方开启本地词典后查询。")
        case .unavailable: return model.text("Download or repair the dictionary below.", "请在下方下载或修复词典。")
        case .ineligible: return model.text("Enter a word or short phrase.", "请输入单词或较短的词语。")
        case .failed:
            return model.text("Could not look up this word. Try again.", "无法查询此词，请重试。")
        }
    }
}
