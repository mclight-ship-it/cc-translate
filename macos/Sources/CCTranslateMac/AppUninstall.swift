import AppKit
import CCTranslateSupport

struct AppUninstallLocations: Equatable {
    let application: URL
    let identifier: String
    let support: URL
    let cache: URL
}

enum AppUninstallStep: Equatable {
    case application, support, cache, preferences
}

struct AppUninstallOutcome: Equatable {
    struct Failure: Equatable {
        let step: AppUninstallStep
        let code: Int
    }
    var completed: [AppUninstallStep] = []
    var failure: Failure?
}

enum AppUninstallError: Error {
    case notAnApplication, redirectedParent, preferencesNotSaved
}

@MainActor
protocol AppUninstallServing {
    func preview() throws -> AppUninstallLocations
    func remove(_ locations: AppUninstallLocations, includingData: Bool) -> AppUninstallOutcome
}

@MainActor
final class AppUninstallService: AppUninstallServing {
    private let application: URL
    private let home: URL
    private let defaults: UserDefaults
    private let trash: (URL) throws -> Void

    init(application: URL = Bundle.main.bundleURL,
         home: URL = FileManager.default.homeDirectoryForCurrentUser,
         defaults: UserDefaults = .standard,
         trash: @escaping (URL) throws -> Void = {
             try FileManager.default.trashItem(at: $0, resultingItemURL: nil)
         }) {
        self.application = application.standardizedFileURL
        self.home = home.standardizedFileURL
        self.defaults = defaults
        self.trash = trash
    }

    func preview() throws -> AppUninstallLocations {
        let home = home.resolvingSymlinksInPath()
        guard application.pathExtension == "app",
              application != home, !home.path.hasPrefix(application.path + "/"),
              try FileManager.default.attributesOfItem(atPath: application.path)[.type] as? FileAttributeType
                == .typeDirectory else { throw AppUninstallError.notAnApplication }
        let identifier = try BundleRuntime(appURL: application).configurationApplicationIdentifier()
        let library = home.appendingPathComponent("Library", isDirectory: true)
        return AppUninstallLocations(
            application: application, identifier: identifier,
            support: library.appendingPathComponent("Application Support", isDirectory: true)
                .appendingPathComponent(identifier, isDirectory: true),
            cache: library.appendingPathComponent("Caches", isDirectory: true)
                .appendingPathComponent(identifier, isDirectory: true))
    }

    func remove(_ locations: AppUninstallLocations, includingData: Bool) -> AppUninstallOutcome {
        var outcome = AppUninstallOutcome()
        var step = AppUninstallStep.application
        do {
            guard try preview() == locations else { throw AppUninstallError.notAnApplication }
            if includingData {
                // Do not follow a redirected parent into another app's or shared data.
                for (item, url) in [(AppUninstallStep.support, locations.support), (.cache, locations.cache)] {
                    step = item
                    let parent = url.deletingLastPathComponent()
                    guard parent.resolvingSymlinksInPath().path == parent.path else {
                        throw AppUninstallError.redirectedParent
                    }
                }
                for (item, url) in [(AppUninstallStep.support, locations.support), (.cache, locations.cache)] {
                    step = item
                    if try exists(url) { try trash(url) }
                    outcome.completed.append(item)
                }
                step = .preferences
                defaults.removePersistentDomain(forName: locations.identifier)
                guard defaults.synchronize() else { throw AppUninstallError.preferencesNotSaved }
                outcome.completed.append(.preferences)
            }
            step = .application
            try trash(locations.application)
            outcome.completed.append(.application)
        } catch {
            outcome.failure = .init(step: step, code: (error as NSError).code)
        }
        return outcome
    }

    private func exists(_ url: URL) throws -> Bool {
        do {
            _ = try FileManager.default.attributesOfItem(atPath: url.path)
            return true
        } catch let error as CocoaError where [.fileReadNoSuchFile, .fileNoSuchFile].contains(error.code) {
            return false
        }
    }
}
