import AppKit
import Combine
import Sparkle

enum AppUpdateChannel: Equatable {
    case unconfigured, invalid, configured

    init(info: [String: Any]) {
        guard info["SUFeedURL"] != nil || info["SUPublicEDKey"] != nil else {
            self = .unconfigured
            return
        }
        guard let feed = info["SUFeedURL"] as? String,
              let url = URLComponents(string: feed), url.scheme?.lowercased() == "https",
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil,
              let key = info["SUPublicEDKey"] as? String,
              let bytes = Data(base64Encoded: key), bytes.count == 32 else {
            self = .invalid
            return
        }
        self = .configured
    }
}

enum AppUpdateServiceError: Error {
    case notReady
}

@MainActor
protocol AppUpdateServing: AnyObject {
    var canCheck: Bool { get }
    var sessionInProgress: Bool { get }
    var onChange: (@MainActor () -> Void)? { get set }
    func check() throws
}

@MainActor
final class SparkleUpdateService: AppUpdateServing {
    var onChange: (@MainActor () -> Void)?
    private var controller: SPUStandardUpdaterController?
    private var observations: [AnyCancellable] = []
    private var started = false

    var canCheck: Bool { !started || controller?.updater.canCheckForUpdates == true }
    var sessionInProgress: Bool { controller?.updater.sessionInProgress == true }

    func check() throws {
        if controller == nil {
            let controller = SPUStandardUpdaterController(
                startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil
            )
            self.controller = controller
            controller.updater.publisher(for: \.canCheckForUpdates)
                .sink { [weak self] _ in self?.onChange?() }.store(in: &observations)
            controller.updater.publisher(for: \.sessionInProgress)
                .sink { [weak self] _ in self?.onChange?() }.store(in: &observations)
        }
        guard let controller else { throw AppUpdateServiceError.notReady }
        if !started {
            try controller.updater.start()
            started = true
        }
        guard controller.updater.canCheckForUpdates else { throw AppUpdateServiceError.notReady }
        controller.checkForUpdates(nil)
        onChange?()
    }
}

@MainActor
final class AppUpdateModel: ObservableObject {
    enum Issue: Equatable {
        case channelUnavailable, notReady, failed(Int), downloadsUnavailable, terminating
    }

    let channel: AppUpdateChannel
    @Published private(set) var canCheck: Bool
    @Published private(set) var sessionInProgress = false
    @Published private(set) var issue: Issue?
    private let factory: @MainActor () -> any AppUpdateServing
    private let openDownloads: @MainActor () -> Bool
    private var service: (any AppUpdateServing)?
    private var terminating = false
    var canOpenDownloads: Bool { !terminating }

    init(info: [String: Any] = Bundle.main.infoDictionary ?? [:],
         factory: @escaping @MainActor () -> any AppUpdateServing = { SparkleUpdateService() },
         openDownloads: @escaping @MainActor () -> Bool = {
             guard let url = URL(string: "https://github.com/mclight-ship-it/cc-translate/blob/agents/cc-translate-macos-native/docs/MACOS_DEVELOPMENT.md#native-translation-user-check") else { return false }
             return NSWorkspace.shared.open(url)
         }) {
        channel = AppUpdateChannel(info: info)
        canCheck = channel == .configured
        self.factory = factory
        self.openDownloads = openDownloads
    }

    @discardableResult
    func check() -> Bool {
        guard !terminating else { issue = .terminating; return false }
        guard channel == .configured else { issue = .channelUnavailable; return false }
        if service == nil {
            let service = factory()
            self.service = service
            service.onChange = { [weak self] in self?.refresh() }
        }
        guard let service, service.canCheck else { issue = .notReady; refresh(); return false }
        issue = nil
        do {
            try service.check()
            refresh()
            return true
        } catch AppUpdateServiceError.notReady {
            issue = .notReady
        } catch {
            issue = .failed((error as NSError).code)
        }
        refresh()
        return false
    }

    func showDownloads() {
        guard !terminating else { issue = .terminating; return }
        issue = openDownloads() ? nil : .downloadsUnavailable
    }

    func prepareToQuit() {
        terminating = true
        canCheck = false
    }

    private func refresh() {
        canCheck = !terminating && channel == .configured && (service?.canCheck ?? true)
        sessionInProgress = service?.sessionInProgress == true
    }
}
