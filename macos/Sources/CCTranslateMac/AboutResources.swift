import Foundation
import CryptoKit

enum AboutResourceError: Error, Equatable, Sendable {
    case unavailable, missing, unreadable, linkedResource, invalidLocation
    case invalidMetadata, unsupportedSchema, invalidEncoding, invalidImage, emptyDocument, tooLarge, checksumMismatch
}

struct AboutResourceIssue: Identifiable, Equatable, Sendable {
    let resource: String
    let error: AboutResourceError
    var id: String { resource }
}

struct AboutLicenseDocument: Identifiable, Equatable, Sendable {
    let path: String
    var expectedSHA256: String? = nil
    var id: String { path }
}

struct AboutBundleInfo: Sendable {
    let name: String?
    let version: String?
    let build: String?
    let identifier: String?
    let minimumOS: String?
}

struct AboutBuildSource: Decodable, Sendable {
    struct Toolchain: Decodable, Sendable {
        let xcode: String?
        let sdk: String?
        let architecture: String?
    }
    struct RuntimeLock: Decodable, Sendable {
        let python_version: String?
    }
    let schema: Int
    let source_commit: String?
    let source_tree_dirty: Bool?
    let development_only: Bool?
    let signing: String?
    let release_gate: String?
    let toolchain: Toolchain?
    let lock: RuntimeLock?
    let resource_hashes: [String: String]?
}

struct AboutOverview: Sendable {
    var info: AboutBundleInfo?
    var source: AboutBuildSource?
    var documents: [AboutLicenseDocument] = []
    var issues: [AboutResourceIssue] = []
}

protocol AboutResourceReading: Sendable {
    func overview() -> AboutOverview
    func license(_ document: AboutLicenseDocument) -> Result<String, AboutResourceError>
    func supportImage(expectedSHA256: String?) -> Result<Data, AboutResourceError>
}

struct AboutBundleResources: AboutResourceReading {
    static let supportImagePath = "Resources/support-author.png"
    private let location: @Sendable () -> URL?
    private let textLimit = 16 * 1024 * 1024

    init(location: @escaping @Sendable () -> URL? = { Bundle.main.bundleURL }) {
        self.location = location
    }

    func overview() -> AboutOverview {
        var result = AboutOverview()
        guard let root = location(), root.isFileURL else {
            result.issues = [.init(resource: "Application bundle", error: .unavailable)]
            return result
        }
        do {
            let data = try read(root, path: "Contents/Info.plist", limit: 1_048_576)
            let raw: Any
            do { raw = try PropertyListSerialization.propertyList(from: data, format: nil) }
            catch { throw AboutResourceError.invalidMetadata }
            guard let info = raw as? [String: Any] else { throw AboutResourceError.invalidMetadata }
            func field(_ key: String) throws -> String? {
                guard let raw = info[key] else { return nil }
                guard let value = raw as? String,
                      !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw AboutResourceError.invalidMetadata
                }
                return value
            }
            result.info = try AboutBundleInfo(
                name: field("CFBundleDisplayName") ?? field("CFBundleName"),
                version: field("CFBundleShortVersionString"), build: field("CFBundleVersion"),
                identifier: field("CFBundleIdentifier"), minimumOS: field("LSMinimumSystemVersion"))
        } catch {
            result.issues.append(.init(resource: "Info.plist", error: classify(error)))
        }
        do {
            let data = try read(root, path: "Contents/Resources/source-manifest.json", limit: 4_194_304)
            let source: AboutBuildSource
            do { source = try JSONDecoder().decode(AboutBuildSource.self, from: data) }
            catch { throw AboutResourceError.invalidMetadata }
            guard source.schema == 1 else { throw AboutResourceError.unsupportedSchema }
            let labels = [source.signing, source.release_gate, source.toolchain?.xcode,
                          source.toolchain?.sdk, source.toolchain?.architecture, source.lock?.python_version]
            guard labels.compactMap({ $0 }).allSatisfy({
                !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }) else { throw AboutResourceError.invalidMetadata }
            if let hashes = source.resource_hashes {
                guard hashes.count <= 8192 else { throw AboutResourceError.tooLarge }
                guard hashes.values.allSatisfy({ isHash($0, lengths: [64]) }) else {
                    throw AboutResourceError.invalidMetadata
                }
            }
            if let commit = source.source_commit {
                guard isHash(commit, lengths: [40, 64]) else {
                    throw AboutResourceError.invalidMetadata
                }
            }
            result.source = source
        } catch {
            result.issues.append(.init(resource: "source-manifest.json", error: classify(error)))
        }
        do {
            let directory = try checkedURL(root, path: "Contents/Resources/Licenses", directory: true)
            try collect(directory, relative: "", documents: &result.documents, issues: &result.issues)
            if !result.documents.contains(where: { $0.path == "THIRD_PARTY_NOTICES" }),
               !result.issues.contains(where: { $0.resource == "Licenses/THIRD_PARTY_NOTICES" }) {
                result.issues.append(.init(resource: "Licenses/THIRD_PARTY_NOTICES", error: .missing))
            }
        } catch {
            result.issues.append(.init(resource: "Licenses", error: classify(error)))
        }
        let prefix = "Resources/Licenses/"
        for (path, hash) in result.source?.resource_hashes ?? [:] where path.hasPrefix(prefix) {
            let relative = String(path.dropFirst(prefix.count))
            if relative == "Python/PYTHON.json" { continue }
            if let index = result.documents.firstIndex(where: { $0.path == relative }) {
                result.documents[index].expectedSHA256 = hash
            } else if !result.issues.contains(where: { $0.resource == "Licenses/" + relative }) {
                result.issues.append(.init(resource: "Licenses/" + relative, error: .missing))
            }
        }
        result.issues.sort { $0.resource < $1.resource }
        result.documents.sort {
            if $0.path == "THIRD_PARTY_NOTICES" { return $1.path != "THIRD_PARTY_NOTICES" }
            if $1.path == "THIRD_PARTY_NOTICES" { return false }
            return $0.path < $1.path
        }
        return result
    }

    func license(_ document: AboutLicenseDocument) -> Result<String, AboutResourceError> {
        guard let root = location(), root.isFileURL else { return .failure(.unavailable) }
        do {
            let data = try read(root, path: "Contents/Resources/Licenses/" + document.path, limit: textLimit)
            if let expected = document.expectedSHA256 {
                let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                guard actual == expected else { throw AboutResourceError.checksumMismatch }
            }
            guard let text = String(data: data, encoding: .utf8) else { throw AboutResourceError.invalidEncoding }
            guard !text.isEmpty else { throw AboutResourceError.emptyDocument }
            return .success(text)
        } catch { return .failure(classify(error)) }
    }

    func supportImage(expectedSHA256: String?) -> Result<Data, AboutResourceError> {
        guard let root = location(), root.isFileURL else { return .failure(.unavailable) }
        do {
            let data = try read(root, path: "Contents/" + Self.supportImagePath, limit: 2 * 1024 * 1024)
            if let expectedSHA256 {
                let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                guard actual == expectedSHA256 else { throw AboutResourceError.checksumMismatch }
            }
            guard data.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]) else {
                throw AboutResourceError.invalidImage
            }
            return .success(data)
        } catch { return .failure(classify(error)) }
    }

    private func collect(_ directory: URL, relative: String, documents: inout [AboutLicenseDocument],
                         issues: inout [AboutResourceIssue], depth: Int = 0) throws {
        guard depth < 16, documents.count + issues.count < 4096 else { throw AboutResourceError.tooLarge }
        let files = try FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.isSymbolicLinkKey, .isDirectoryKey, .isRegularFileKey],
            options: [])
        for file in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            try Task.checkCancellation()
            guard documents.count + issues.count < 4096 else { throw AboutResourceError.tooLarge }
            let path = relative + file.lastPathComponent
            if path == "Python/PYTHON.json" { continue }
            do {
                let values = try file.resourceValues(forKeys: [.isSymbolicLinkKey, .isDirectoryKey, .isRegularFileKey])
                guard values.isSymbolicLink != true else { throw AboutResourceError.linkedResource }
                if values.isDirectory == true {
                    try collect(file, relative: path + "/", documents: &documents, issues: &issues, depth: depth + 1)
                } else {
                    guard values.isRegularFile == true else { throw AboutResourceError.invalidLocation }
                    documents.append(.init(path: path))
                }
            } catch {
                issues.append(.init(resource: "Licenses/" + path, error: classify(error)))
            }
        }
    }

    private func checkedURL(_ root: URL, path: String, directory: Bool = false) throws -> URL {
        guard root.isFileURL, !path.contains("\\"), !path.contains("\0") else {
            throw AboutResourceError.invalidLocation
        }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        guard !parts.isEmpty, parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
            throw AboutResourceError.invalidLocation
        }
        var url = root
        for index in 0...parts.count {
            let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey, .isDirectoryKey, .isRegularFileKey])
            guard values.isSymbolicLink != true else { throw AboutResourceError.linkedResource }
            if index < parts.count || directory {
                guard values.isDirectory == true else { throw AboutResourceError.invalidLocation }
            } else {
                guard values.isRegularFile == true else { throw AboutResourceError.invalidLocation }
            }
            if index < parts.count { url.appendPathComponent(String(parts[index])) }
        }
        return url
    }

    private func read(_ root: URL, path: String, limit: Int) throws -> Data {
        try Task.checkCancellation()
        let url = try checkedURL(root, path: path)
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize
        guard let size, size <= limit else { throw AboutResourceError.tooLarge }
        let handle = try FileHandle(forReadingFrom: url)
        let data: Data
        do {
            var accumulated = Data()
            while let chunk = try handle.read(upToCount: min(65_536, limit + 1 - accumulated.count)),
                  !chunk.isEmpty {
                accumulated.append(chunk)
                try Task.checkCancellation()
                guard accumulated.count <= limit else { throw AboutResourceError.tooLarge }
            }
            data = accumulated
        } catch {
            try handle.close()
            throw error
        }
        try handle.close()
        return data
    }

    private func classify(_ error: Error) -> AboutResourceError {
        if let error = error as? AboutResourceError { return error }
        let cocoa = error as NSError
        return cocoa.domain == NSCocoaErrorDomain && cocoa.code == NSFileReadNoSuchFileError ? .missing : .unreadable
    }

    private func isHash(_ value: String, lengths: [Int]) -> Bool {
        lengths.contains(value.utf8.count) &&
            value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
