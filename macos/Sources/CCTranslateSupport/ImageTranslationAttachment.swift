import CryptoKit
import Darwin
import Foundation
import ImageIO
import UniformTypeIdentifiers

public enum ImageTranslationAttachmentError: String, Error, LocalizedError {
    case invalidPNG, imageTooLarge, creationFailed, cleanupFailed
    public var errorDescription: String? { rawValue }
}

/// Retain this owner after failed creation until explicit cleanup succeeds.
public final class ImageTranslationCleanupHandle: @unchecked Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    private let lock = NSLock()
    private let cleanupOperation: () throws -> Void

    fileprivate init(cleanupOperation: @escaping () throws -> Void) {
        self.cleanupOperation = cleanupOperation
    }

    public func cleanup() throws {
        lock.lock()
        defer { lock.unlock() }
        try cleanupOperation()
    }

    public var description: String { "ImageTranslationCleanupHandle" }
    public var debugDescription: String { description }
}

public struct ImageTranslationAttachmentCreationError: Error, LocalizedError, CustomStringConvertible, CustomDebugStringConvertible {
    public let creationFailure: ImageTranslationAttachmentError
    public let cleanupHandle: ImageTranslationCleanupHandle

    fileprivate init(creationFailure: ImageTranslationAttachmentError, cleanupHandle: ImageTranslationCleanupHandle) {
        self.creationFailure = creationFailure
        self.cleanupHandle = cleanupHandle
    }

    public var errorDescription: String? { ImageTranslationAttachmentError.cleanupFailed.rawValue }
    public var description: String { "ImageTranslationAttachmentCreationError(cleanupFailed)" }
    public var debugDescription: String { description }
}

/// Creating an attachment is explicit. The caller must retain it until its helper request drains,
/// then call cleanup; releasing this object does not delete the image or guarantee crash cleanup.
public final class ImageTranslationAttachment: @unchecked Sendable {
    public static let maxBytes = 80 * 1024 * 1024
    public let url: URL
    public let byteCount: Int
    public let sha256: String
    private let cleanupHandle: ImageTranslationCleanupHandle

    public convenience init(pngData: Data) throws {
        try self.init(pngData: pngData, temporaryParent: FileManager.default.temporaryDirectory)
    }

    convenience init(pngData: Data, temporaryParent: URL) throws {
        try self.init(pngData: pngData, temporaryParent: temporaryParent, write: Self.writeBytes)
    }

    init(pngData: Data, temporaryParent: URL, write: (Int32, Data) -> Bool) throws {
        try Self.validatePNG(pngData)
        guard temporaryParent.isFileURL, temporaryParent.path.hasPrefix("/"),
              !temporaryParent.path.contains("\0") else { throw ImageTranslationAttachmentError.creationFailed }
        let parent = temporaryParent.resolvingSymlinksInPath()
        let descriptor = Darwin.open(parent.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw ImageTranslationAttachmentError.creationFailed }
        let storage = Storage(parent: FileHandle(fileDescriptor: descriptor, closeOnDealloc: true),
                              name: "cc-translate-image-\(UUID().uuidString)")
        let cleanupHandle = ImageTranslationCleanupHandle { try storage.cleanup() }
        do {
            try storage.create(pngData, write: write)
        } catch let creationFailure as ImageTranslationAttachmentError {
            // Failed rollback must transfer the owner to the caller, not strand a partial image.
            do { try cleanupHandle.cleanup() }
            catch {
                throw ImageTranslationAttachmentCreationError(creationFailure: creationFailure, cleanupHandle: cleanupHandle)
            }
            throw creationFailure
        }
        self.cleanupHandle = cleanupHandle
        url = parent.appendingPathComponent(storage.name, isDirectory: true).appendingPathComponent("region.png")
        byteCount = pngData.count
        sha256 = SHA256.hash(data: pngData).map { String(format: "%02x", $0) }.joined()
    }

    public func cleanup() throws {
        try cleanupHandle.cleanup()
    }

    private static func validatePNG(_ data: Data) throws {
        guard data.count <= maxBytes else { throw ImageTranslationAttachmentError.imageTooLarge }
        guard data.starts(with: [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]),
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              let type = CGImageSourceGetType(source), type as String == UTType.png.identifier,
              CGImageSourceGetCount(source) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
              let width = properties[kCGImagePropertyPixelWidth as String] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight as String] as? NSNumber,
              width.doubleValue > 0, height.doubleValue > 0 else {
            throw ImageTranslationAttachmentError.invalidPNG
        }
        // Reuse the selected-region decode budget so a small compressed input cannot allocate
        // an arbitrarily large bitmap merely to validate its PNG contents.
        let budget = RegionCaptureBudget.standard
        guard width.doubleValue <= Double(budget.maximumDimension),
              height.doubleValue <= Double(budget.maximumDimension),
              width.doubleValue * height.doubleValue <= Double(budget.compositePixels) else {
            throw ImageTranslationAttachmentError.imageTooLarge
        }
        guard CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary) != nil,
              CGImageSourceGetStatus(source) == .statusComplete,
              CGImageSourceGetStatusAtIndex(source, 0) == .statusComplete else {
            throw ImageTranslationAttachmentError.invalidPNG
        }
    }

    private static func writeBytes(_ descriptor: Int32, _ data: Data) -> Bool {
        data.withUnsafeBytes { buffer in
            guard let base = buffer.baseAddress else { return false }
            var written = 0
            while written < buffer.count {
                let result = Darwin.write(descriptor, base.advanced(by: written), buffer.count - written)
                if result < 0, errno == EINTR { continue }
                guard result > 0 else { return false }
                written += result
            }
            return fsync(descriptor) == 0
        }
    }

    private struct Identity {
        let device: dev_t
        let inode: ino_t
        init(_ value: stat) { device = value.st_dev; inode = value.st_ino }
        func matches(_ value: stat) -> Bool { device == value.st_dev && inode == value.st_ino }
    }

    private final class Storage {
        let name: String
        private var parent: FileHandle?
        private var directory: FileHandle?
        private var file: FileHandle?
        private var directoryIdentity: Identity?
        private var fileIdentity: Identity?
        private var directoryCreated = false
        private var fileCreated = false

        init(parent: FileHandle, name: String) { self.parent = parent; self.name = name }

        func create(_ data: Data, write: (Int32, Data) -> Bool) throws {
            guard let parent, mkdirat(parent.fileDescriptor, name, 0o700) == 0 else {
                throw ImageTranslationAttachmentError.creationFailed
            }
            directoryCreated = true
            let directoryFD = openat(parent.fileDescriptor, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard directoryFD >= 0 else { throw ImageTranslationAttachmentError.creationFailed }
            directory = FileHandle(fileDescriptor: directoryFD, closeOnDealloc: true)
            var metadata = stat()
            guard fstat(directoryFD, &metadata) == 0 else { throw ImageTranslationAttachmentError.creationFailed }
            directoryIdentity = Identity(metadata)
            guard metadata.st_mode & S_IFMT == S_IFDIR, metadata.st_uid == geteuid(),
                  fchmod(directoryFD, 0o700) == 0 else { throw ImageTranslationAttachmentError.creationFailed }
            let fileFD = openat(directoryFD, "region.png", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
            guard fileFD >= 0 else { throw ImageTranslationAttachmentError.creationFailed }
            fileCreated = true
            file = FileHandle(fileDescriptor: fileFD, closeOnDealloc: true)
            guard fstat(fileFD, &metadata) == 0 else { throw ImageTranslationAttachmentError.creationFailed }
            fileIdentity = Identity(metadata)
            guard metadata.st_mode & S_IFMT == S_IFREG, metadata.st_uid == geteuid(), metadata.st_nlink == 1,
                  fchmod(fileFD, 0o600) == 0, write(fileFD, data),
                  fstat(fileFD, &metadata) == 0, metadata.st_size == off_t(data.count) else {
                throw ImageTranslationAttachmentError.creationFailed
            }
        }

        func cleanup() throws {
            if directoryCreated {
                guard let parent, let directory, let directoryIdentity else {
                    throw ImageTranslationAttachmentError.cleanupFailed
                }
                var metadata = stat()
                guard fstatat(parent.fileDescriptor, name, &metadata, AT_SYMLINK_NOFOLLOW) == 0,
                      metadata.st_mode & S_IFMT == S_IFDIR, directoryIdentity.matches(metadata) else {
                    throw ImageTranslationAttachmentError.cleanupFailed
                }
                if fileCreated {
                    guard let fileIdentity,
                          fstatat(directory.fileDescriptor, "region.png", &metadata, AT_SYMLINK_NOFOLLOW) == 0,
                          metadata.st_mode & S_IFMT == S_IFREG, fileIdentity.matches(metadata),
                          unlinkat(directory.fileDescriptor, "region.png", 0) == 0 else {
                        throw ImageTranslationAttachmentError.cleanupFailed
                    }
                    fileCreated = false
                }
                // Never recursively remove a directory or erase unexpected neighboring entries.
                guard unlinkat(parent.fileDescriptor, name, AT_REMOVEDIR) == 0 else {
                    throw ImageTranslationAttachmentError.cleanupFailed
                }
                directoryCreated = false
            }
            try file?.close()
            file = nil
            try directory?.close()
            directory = nil
            try parent?.close()
            parent = nil
        }
    }
}
