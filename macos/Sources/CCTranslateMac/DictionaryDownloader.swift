import Foundation
import CCTranslateSupport

enum DictionaryDownloadError: String, Error {
    case cancelled, invalidResponse, insecureRedirect, sizeMismatch, tooLarge
    case network, fileIO, cleanupFailed, alreadyRunning
}

@MainActor
protocol DictionaryDownloading: AnyObject {
    func start(_ ticket: DictionaryInstallTicket, progress: @escaping (Int64) -> Void,
               completion: @escaping (Result<Void, DictionaryDownloadError>) -> Void)
    func cancel()
}

// Delegate callbacks and cancellation share the main queue; no completion can race a later file move.
@MainActor
final class DictionaryDownloader: NSObject, DictionaryDownloading, URLSessionDataDelegate {
    private let configuration: URLSessionConfiguration
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var ticket: DictionaryInstallTicket?
    private var temporaryURL: URL?
    private var file: FileHandle?
    private var received: Int64 = 0
    private var failure: DictionaryDownloadError?
    private var progress: ((Int64) -> Void)?
    private var completion: ((Result<Void, DictionaryDownloadError>) -> Void)?

    init(configuration: URLSessionConfiguration = .ephemeral) {
        self.configuration = configuration
        super.init()
    }

    func start(_ ticket: DictionaryInstallTicket, progress: @escaping (Int64) -> Void,
               completion: @escaping (Result<Void, DictionaryDownloadError>) -> Void) {
        guard self.completion == nil else { completion(.failure(.alreadyRunning)); return }
        self.ticket = ticket
        self.progress = progress
        self.completion = completion
        failure = nil
        received = 0
        guard ticket.url.scheme == "https", ticket.url.user == nil, ticket.url.password == nil else {
            finish(.failure(.insecureRedirect))
            return
        }
        do {
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent("cc-dictionary-\(UUID().uuidString).download")
            try Data().write(to: temp, options: .withoutOverwriting)
            temporaryURL = temp
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temp.path)
            file = try FileHandle(forWritingTo: temp)
        } catch {
            finish(.failure(.fileIO))
            return
        }
        configuration.urlCache = nil
        configuration.urlCredentialStorage = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 1200
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
        self.session = session
        var request = URLRequest(url: ticket.url)
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        let task = session.dataTask(with: request)
        self.task = task
        task.resume()
    }

    func cancel() {
        guard completion != nil else { return }
        failure = .cancelled
        task?.cancel()
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
                               willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                               completionHandler: @escaping (URLRequest?) -> Void) {
        MainActor.assumeIsolated {
            guard let url = request.url, url.scheme == "https", url.user == nil, url.password == nil else {
                failure = .insecureRedirect
                completionHandler(nil)
                task.cancel()
                return
            }
            completionHandler(failure == nil ? request : nil)
        }
    }

    nonisolated func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                               didReceive response: URLResponse,
                               completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        MainActor.assumeIsolated {
            guard failure == nil, let ticket, let http = response as? HTTPURLResponse,
                  http.statusCode == 200, http.url?.scheme == "https" else {
                if failure == nil { failure = .invalidResponse }
                completionHandler(.cancel)
                return
            }
            if response.expectedContentLength > ticket.size {
                failure = .tooLarge
            } else if response.expectedContentLength >= 0 && response.expectedContentLength != ticket.size {
                failure = .sizeMismatch
            }
            completionHandler(failure == nil ? .allow : .cancel)
        }
    }

    nonisolated func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        MainActor.assumeIsolated {
            guard failure == nil, let ticket, let file else { return }
            guard Int64(data.count) <= ticket.size - received else {
                failure = .tooLarge
                dataTask.cancel()
                return
            }
            do {
                try file.write(contentsOf: data)
                received += Int64(data.count)
                progress?(received)
            } catch {
                failure = .fileIO
                dataTask.cancel()
            }
        }
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        MainActor.assumeIsolated {
            guard completion != nil else { return }
            if let failure { finish(.failure(failure)); return }
            guard error == nil else { finish(.failure(.network)); return }
            guard let ticket, received == ticket.size, let temporaryURL else {
                finish(.failure(.sizeMismatch))
                return
            }
            do {
                try file?.close()
                file = nil
                // The backend issued this absent path. moveItem refuses to overwrite an existing file.
                try FileManager.default.moveItem(at: temporaryURL, to: ticket.path)
                self.temporaryURL = nil
                finish(.success(()))
            } catch {
                finish(.failure(.fileIO))
            }
        }
    }

    private func finish(_ result: Result<Void, DictionaryDownloadError>) {
        var result = result
        do {
            try file?.close()
        } catch {
            result = .failure(.cleanupFailed)
        }
        file = nil
        if let temporaryURL {
            do { try FileManager.default.removeItem(at: temporaryURL) }
            catch { result = .failure(.cleanupFailed) }
        }
        temporaryURL = nil
        session?.invalidateAndCancel()
        session = nil
        task = nil
        ticket = nil
        progress = nil
        let callback = completion
        completion = nil
        callback?(result)
    }
}
