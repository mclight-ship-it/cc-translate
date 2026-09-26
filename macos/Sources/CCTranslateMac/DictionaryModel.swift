import Combine
import Foundation
import CCTranslateSupport

@MainActor
final class DictionaryModel: ObservableObject {
    enum Phase: Equatable { case idle, checking, preparing, downloading, waitingToInstall, installing, discarding, deleting, unknown }
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var status: DictionaryStatus?
    @Published private(set) var received: Int64 = 0
    @Published private(set) var expected: Int64 = 0
    @Published private(set) var messageEnglish = ""
    @Published private(set) var messageChinese = ""
    var send: ((DictionaryRequest, String) -> Bool)?
    var onConfigurationChanged: (() -> Void)?
    var onSettled: (() -> Void)?
    var canInstall: (() -> Bool)?
    private let downloader: DictionaryDownloading
    private var current: (id: String, request: DictionaryRequest)?
    private var ticket: DictionaryInstallTicket?
    private var cancelled = false
    private var connected = false
    private var wantsStatus = false
    private var stopping: (() -> Void)?
    private var transferGeneration = UUID()
    private var downloadHadError = false
    private var transferConnectionLost = false
    private var preserveMessageOnStatus = false
    var busy: Bool { current != nil || phase == .downloading || phase == .waitingToInstall }
    var committing: Bool { phase == .installing || phase == .deleting }
    var ownsInstallation: Bool { ticket != nil || phase == .preparing || phase == .downloading }
    var canCancel: Bool { phase == .preparing || phase == .downloading || phase == .waitingToInstall }

    init(downloader: DictionaryDownloading) { self.downloader = downloader }

    func report(_ english: String, _ chinese: String) {
        messageEnglish = english
        messageChinese = chinese
    }

    func requestStatusWhenReady() { wantsStatus = true }

    func connectionReady() {
        connected = true
        if phase == .waitingToInstall { installWhenReady(); return }
        if wantsStatus && !busy && stopping == nil {
            wantsStatus = false
            issue(.status, phase: .checking)
        }
    }

    func download() {
        guard connected && !busy && phase != .unknown else {
            report("Refresh dictionary status before downloading.", "请先刷新词典状态，再下载。")
            return
        }
        cancelled = false
        downloadHadError = false
        transferConnectionLost = false
        received = 0
        expected = status?.size ?? 0
        report("Preparing a verified download…", "正在准备经过校验的下载…")
        issue(.prepareInstall, phase: .preparing)
    }

    func delete() {
        guard connected && !busy && phase != .unknown else {
            report("Refresh dictionary status before deleting.", "请先刷新词典状态，再删除。")
            return
        }
        report("Deleting the local dictionary…", "正在删除本地词典…")
        issue(.delete, phase: .deleting)
    }

    func cancel() {
        guard canCancel else { return }
        cancelled = true
        report("Cancelling; waiting for the transfer and staging cleanup…", "正在取消，等待传输结束并清理暂存文件…")
        if phase == .downloading { downloader.cancel() }
        else if phase == .waitingToInstall { discard() }
    }

    func prepareToStop(_ completion: @escaping () -> Void) {
        stopping = completion
        if canCancel { cancel() }
        if !busy { settle() }
    }

    func connectionLost() {
        connected = false
        wantsStatus = true
        let wasTransfer = phase == .downloading
        if current?.request == .status { phase = .idle }
        else if current != nil || wasTransfer || ticket != nil {
            report("Dictionary outcome or cleanup is unknown. Refresh status; installation will not be replayed.",
                   "词典操作结果或清理状态未知。请刷新状态，不会重试安装。")
            if !wasTransfer { phase = .unknown }
        }
        current = nil
        cancelled = true
        if wasTransfer {
            if !transferConnectionLost { downloader.cancel() }
            transferConnectionLost = true
        }
        else {
            ticket = nil
            settle()
        }
    }

    @discardableResult
    func handle(_ event: ServerEvent) -> Bool {
        guard let request = current, request.id == event.id else { return false }
        guard event.isTerminal else { return true }
        current = nil
        guard event.type == "completed" else {
            report("Dictionary operation ended: \(event.safeFailureCode). No automatic retry.",
                   "词典操作已结束：\(event.safeFailureCode)。不会自动重试。")
            if case .install = request.request {
                ticket = nil
                phase = .idle
                wantsStatus = true
                preserveMessageOnStatus = true
                if event.type == "cancelled" {
                    report("Dictionary installation cancelled. Checking the final state; installation will not be replayed.",
                           "词典安装已取消。正在检查最终状态，不会重试安装。")
                }
                if stopping == nil { onConfigurationChanged?() }
                settle()
                return true
            }
            if let ticket, request.request != .discardInstall(ticket: ticket.ticket) {
                discard()
            } else {
                ticket = nil
                phase = .unknown
                settle()
            }
            return true
        }
        do {
            switch request.request {
            case .status:
                status = try DictionaryStatus(payload: event.payload)
                phase = .idle
                if !preserveMessageOnStatus { report("Dictionary status refreshed.", "词典状态已刷新。") }
                preserveMessageOnStatus = false
            case .prepareInstall:
                let prepared = try DictionaryInstallTicket(payload: event.payload)
                ticket = prepared
                expected = prepared.size
                if cancelled || stopping != nil { discard(); return true }
                phase = .downloading
                report("Downloading dictionary…", "正在下载词典…")
                let generation = UUID()
                transferGeneration = generation
                downloader.start(prepared, progress: { [weak self] bytes in
                    guard let self, self.transferGeneration == generation else { return }
                    self.received = bytes
                }, completion: { [weak self] result in
                    guard let self, self.transferGeneration == generation else { return }
                    self.downloadFinished(result)
                })
                return true
            case .install:
                status = try DictionaryStatus(payload: event.payload)
                ticket = nil
                phase = .idle
                report("Dictionary installed and enabled. No model request was made.",
                       "词典已安装并启用，未请求模型。")
                if stopping == nil { onConfigurationChanged?() }
            case .discardInstall:
                ticket = nil
                phase = .idle
                if cancelled && !downloadHadError {
                    report("Download cancelled; staging removed.", "下载已取消，暂存文件已清理。")
                }
            case .delete:
                status = nil
                phase = .idle
                wantsStatus = true
                report("Dictionary deleted and disabled.", "词典已删除并禁用。")
                if stopping == nil { onConfigurationChanged?() }
            case .lookup:
                report("Unexpected dictionary operation.", "出现意外的词典操作。")
                phase = .unknown
            }
        } catch {
            report("Invalid dictionary response. Refresh status; nothing will be replayed.",
                   "词典响应无效。请刷新状态，不会重试操作。")
            phase = .unknown
            if case .install = request.request {
                ticket = nil
                settle()
                return true
            }
            if ticket != nil { discard(); return true }
        }
        settle()
        return true
    }

    private func downloadFinished(_ result: Result<Void, DictionaryDownloadError>) {
        phase = .idle
        guard connected && !transferConnectionLost else {
            ticket = nil
            phase = .unknown
            report("Transfer settled after the connection closed. Refresh status to check cleanup.",
                   "连接关闭后传输已结束，请刷新状态检查清理结果。")
            settle()
            return
        }
        switch result {
        case .success where !cancelled && stopping == nil:
            phase = .waitingToInstall
            installWhenReady()
        case .success: discard()
        case .failure(let error):
            if error != .cancelled {
                downloadHadError = true
                report("Dictionary download failed: \(error.rawValue). No automatic retry.",
                       "词典下载失败：\(error.rawValue)。不会自动重试。")
            }
            discard()
        }
    }

    private func installWhenReady() {
        guard canInstall?() != false else {
            report("Download complete. Waiting for the current settings operation…",
                   "下载已完成，正在等待当前设置操作…")
            return
        }
        guard let ticket else {
            phase = .unknown
            report("The installation ticket is missing; nothing was installed.", "安装凭证丢失，未安装任何内容。")
            settle()
            return
        }
        report("Verifying and installing… Closing waits for the final outcome.",
               "正在校验并安装…关闭窗口时会等待最终结果。")
        issue(.install(ticket: ticket.ticket), phase: .installing)
    }

    private func discard() {
        guard let ticket else { phase = .idle; settle(); return }
        issue(.discardInstall(ticket: ticket.ticket), phase: .discarding)
    }

    private func issue(_ request: DictionaryRequest, phase: Phase) {
        let id = UUID().uuidString
        self.phase = phase
        current = (id, request)
        guard send?(request, id) == true else {
            connectionLost()
            return
        }
    }

    private func settle() {
        guard !busy else { return }
        if let stop = stopping {
            stopping = nil
            stop()
        }
        onSettled?()
    }
}
