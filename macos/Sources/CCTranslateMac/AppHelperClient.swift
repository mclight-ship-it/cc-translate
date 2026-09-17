import Foundation
import CCTranslateSupport

protocol AppHelperClient: AnyObject {
    func start(runtime: BundleRuntime)
    func startConfiguration(runtime: BundleRuntime, home: URL)
    func startTranslation(runtime: BundleRuntime, home: URL, codexCommand: URL, environment: [String: String])
    func send(_ message: ClientMessage, timeout: TimeInterval)
    func translate(text: String, appLanguage: String, origin: String, useCache: Bool,
                   recordHistory: Bool, id: String, timeout: TimeInterval) -> String
    func translateImage(imagePath: String, imageBytes: Int, imageSHA256: String,
                        appLanguage: String, recordHistory: Bool, id: String, timeout: TimeInterval) -> String
    func resultAction(_ action: ResultAction, text: String, appLanguage: String,
                      targetLanguage: String?, id: String, timeout: TimeInterval) -> String
    func modelCatalog(id: String, timeout: TimeInterval) -> String
    func dictionary(_ request: DictionaryRequest, id: String, timeout: TimeInterval) -> String
    func loadConfiguration(id: String, timeout: TimeInterval) -> String
    func saveConfiguration(_ config: [String: JSONValue], id: String, timeout: TimeInterval) -> String
    func loadHistory(pageSize: Int, cursor: JSONValue, query: String, kind: String,
                     id: String, timeout: TimeInterval) -> String
    func clearHistory(id: String, timeout: TimeInterval) -> String
    func stop()
}

extension HelperConnection: AppHelperClient {}

extension AppHelperClient {
    func send(_ message: ClientMessage) { send(message, timeout: 25) }
    @discardableResult
    func translateImage(imagePath: String, imageBytes: Int, imageSHA256: String,
                        appLanguage: String, recordHistory: Bool, id: String, timeout: TimeInterval) -> String {
        send(ClientMessage(id: id, type: "request", payload: [
            "operation": .string("translate_image"), "image_path": .string(imagePath),
            "image_bytes": .integer(Int64(imageBytes)), "image_sha256": .string(imageSHA256),
            "app_language": .string(appLanguage), "record_history": .bool(recordHistory)
        ]), timeout: timeout)
        return id
    }
    @discardableResult
    func modelCatalog(id: String) -> String { modelCatalog(id: id, timeout: 40) }
    @discardableResult
    func modelCatalog(id: String, timeout: TimeInterval) -> String {
        send(ClientMessage(id: id, type: "request", payload: ["operation": .string("model_catalog")]),
             timeout: timeout)
        return id
    }
    @discardableResult
    func loadConfiguration(id: String) -> String { loadConfiguration(id: id, timeout: 20) }
    @discardableResult
    func saveConfiguration(_ config: [String: JSONValue], id: String) -> String {
        saveConfiguration(config, id: id, timeout: 20)
    }
    @discardableResult
    func loadHistory(pageSize: Int, cursor: JSONValue, id: String) -> String {
        loadHistory(pageSize: pageSize, cursor: cursor, query: "", kind: "all", id: id, timeout: 20)
    }
    @discardableResult
    func loadHistory(pageSize: Int, cursor: JSONValue, id: String, timeout: TimeInterval) -> String {
        loadHistory(pageSize: pageSize, cursor: cursor, query: "", kind: "all", id: id, timeout: timeout)
    }
    @discardableResult
    func loadHistory(pageSize: Int, cursor: JSONValue, query: String, kind: String, id: String) -> String {
        loadHistory(pageSize: pageSize, cursor: cursor, query: query, kind: kind, id: id, timeout: 20)
    }
    @discardableResult
    func clearHistory(id: String) -> String { clearHistory(id: id, timeout: 20) }
}
