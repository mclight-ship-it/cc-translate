import CoreGraphics
import CryptoKit
import Darwin
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import CCTranslateSupport

enum ImageTranslationFixture {
    static func png(width: Int = 4, height: Int = 3) throws -> Data {
        var pixels = [UInt8](repeating: 255, count: width * height * 4)
        for offset in stride(from: 0, to: pixels.count, by: 4) {
            pixels[offset] = UInt8((offset / 4) % 251)
            pixels[offset + 1] = 80
            pixels[offset + 2] = 180
        }
        let provider = try XCTUnwrap(CGDataProvider(data: Data(pixels) as CFData))
        let image = try XCTUnwrap(CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent))
        let output = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(output, UTType.png.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw ImageTranslationAttachmentError.invalidPNG }
        return output as Data
    }
}

final class ImageTranslationAttachmentTests: XCTestCase {
    private func parent() throws -> URL {
        let parent = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
            .appendingPathComponent(".image-attachment-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        return parent.resolvingSymlinksInPath()
    }

    private func remove(_ parent: URL) {
        do { try FileManager.default.removeItem(at: parent) }
        catch { XCTFail("Synthetic attachment fixture cleanup failed") }
    }

    func testAttachmentCreatesOnlyExplicitPrivateByteExactPNGAndDigest() throws {
        let parent = try parent()
        defer { remove(parent) }
        let data = try ImageTranslationFixture.png()
        XCTAssertEqual(ImageTranslationAttachment.maxBytes, 83_886_080)
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: parent.path).isEmpty)
        let attachment = try ImageTranslationAttachment(pngData: data, temporaryParent: parent)
        let directory = attachment.url.deletingLastPathComponent()
        XCTAssertEqual(directory.deletingLastPathComponent(), parent)
        XCTAssertEqual(attachment.url.lastPathComponent, "region.png")
        XCTAssertEqual(try Data(contentsOf: attachment.url), data)
        XCTAssertEqual(attachment.byteCount, data.count)
        XCTAssertEqual(attachment.sha256, SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined())
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), ["region.png"])
        let fileAttributes = try FileManager.default.attributesOfItem(atPath: attachment.url.path)
        let directoryAttributes = try FileManager.default.attributesOfItem(atPath: directory.path)
        XCTAssertEqual((fileAttributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertEqual((directoryAttributes[.posixPermissions] as? NSNumber)?.intValue, 0o700)
        XCTAssertEqual(fileAttributes[.type] as? FileAttributeType, .typeRegular)
        XCTAssertEqual(directoryAttributes[.type] as? FileAttributeType, .typeDirectory)
        XCTAssertEqual((fileAttributes[.ownerAccountID] as? NSNumber)?.uint32Value, geteuid())
        try attachment.cleanup()
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
        try attachment.cleanup()
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: parent.path).isEmpty)
    }

    func testAttachmentIndependentInstancesNeverDeleteEachOtherOrNeighbors() throws {
        let parent = try parent()
        defer { remove(parent) }
        let data = try ImageTranslationFixture.png()
        let first = try ImageTranslationAttachment(pngData: data, temporaryParent: parent)
        let second = try ImageTranslationAttachment(pngData: data, temporaryParent: parent)
        let neighbor = parent.appendingPathComponent("keep.txt")
        try Data("synthetic neighbor".utf8).write(to: neighbor)
        XCTAssertNotEqual(first.url.deletingLastPathComponent(), second.url.deletingLastPathComponent())
        try first.cleanup()
        XCTAssertEqual(try Data(contentsOf: second.url), data)
        XCTAssertEqual(try String(contentsOf: neighbor, encoding: .utf8), "synthetic neighbor")
        try second.cleanup()
    }

    func testAttachmentRejectsMalformedTruncatedAndNonPNGDataBeforeWritingAnything() throws {
        let parent = try parent()
        defer { remove(parent) }
        let valid = try ImageTranslationFixture.png()
        let invalid = [Data(), Data("not an image".utf8), Data(valid.prefix(8)), Data(valid.prefix(valid.count / 2)),
                       Data([0xFF, 0xD8, 0xFF, 0xE0])]
        var writes = 0
        for bytes in invalid {
            XCTAssertThrowsError(try ImageTranslationAttachment(pngData: bytes, temporaryParent: parent, write: { _, _ in
                writes += 1
                return true
            })) { error in
                XCTAssertEqual(error as? ImageTranslationAttachmentError, .invalidPNG)
            }
            XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: parent.path).isEmpty)
        }
        XCTAssertEqual(writes, 0)
    }

    func testAttachmentEncodedAndDecodedBudgetsFailBeforeFileCreation() throws {
        let parent = try parent()
        defer { remove(parent) }
        let oversized = Data(repeating: 0, count: ImageTranslationAttachment.maxBytes + 1)
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: oversized, temporaryParent: parent)) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .imageTooLarge)
        }
        let wide = try ImageTranslationFixture.png(width: RegionCaptureBudget.standard.maximumDimension + 1, height: 1)
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: wide, temporaryParent: parent)) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .imageTooLarge)
        }
        let dense = try ImageTranslationFixture.png(width: 4097, height: 4096)
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: dense, temporaryParent: parent)) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .imageTooLarge)
        }
        let boundary = try ImageTranslationAttachment(
            pngData: ImageTranslationFixture.png(width: RegionCaptureBudget.standard.maximumDimension, height: 1),
            temporaryParent: parent)
        try boundary.cleanup()
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: parent.path).isEmpty)
    }

    func testAttachmentPartialWriteFailureRollsBackOnlyItsOwnedFiles() throws {
        let parent = try parent()
        defer { remove(parent) }
        let neighbor = parent.appendingPathComponent("keep.txt")
        try Data("keep".utf8).write(to: neighbor)
        let bytes = try ImageTranslationFixture.png()
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: bytes, temporaryParent: parent, write: { fd, data in
            data.withUnsafeBytes { buffer in
                XCTAssertEqual(Darwin.write(fd, buffer.baseAddress, 8), 8)
            }
            return false
        })) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .creationFailed)
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: parent.path), ["keep.txt"])
        XCTAssertEqual(try String(contentsOf: neighbor, encoding: .utf8), "keep")
    }

    func testAttachmentCreationFailureDoesNotCreateMissingParents() throws {
        let parent = try parent()
        defer { remove(parent) }
        let missing = parent.appendingPathComponent("not-created", isDirectory: true)
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: ImageTranslationFixture.png(), temporaryParent: missing)) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .creationFailed)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: missing.path))
    }

    func testAttachmentFailedCreationTransfersSanitizedOwnedCleanupForExplicitRetry() throws {
        let parent = try parent()
        defer { remove(parent) }
        let outside = parent.appendingPathComponent("keep.txt")
        try Data("outside owner".utf8).write(to: outside)
        let png = try ImageTranslationFixture.png()
        var retained: URL?
        var recovery: ImageTranslationCleanupHandle?
        XCTAssertThrowsError(try ImageTranslationAttachment(pngData: png, temporaryParent: parent, write: { fd, data in
            data.withUnsafeBytes { buffer in
                XCTAssertEqual(Darwin.write(fd, buffer.baseAddress, 8), 8)
            }
            do {
                let directory = try XCTUnwrap(FileManager.default.contentsOfDirectory(
                    at: parent, includingPropertiesForKeys: nil).first {
                        $0.lastPathComponent.hasPrefix("cc-translate-image-")
                    })
                let image = directory.appendingPathComponent("region.png")
                let saved = directory.appendingPathComponent("retained.png")
                try FileManager.default.moveItem(at: image, to: saved)
                try Data("preserve foreign replacement".utf8).write(to: image)
                retained = saved
            } catch { XCTFail("Synthetic rollback failure setup failed") }
            return false
        })) { error in
            guard let failure = error as? ImageTranslationAttachmentCreationError else {
                return XCTFail("Failed rollback must transfer an owned cleanup handle")
            }
            XCTAssertEqual(failure.creationFailure, .creationFailed)
            XCTAssertEqual(failure.errorDescription, ImageTranslationAttachmentError.cleanupFailed.rawValue)
            recovery = failure.cleanupHandle
            for description in [failure.description, failure.debugDescription, failure.localizedDescription,
                                String(describing: failure.cleanupHandle), String(reflecting: failure.cleanupHandle)] {
                XCTAssertFalse(description.contains(parent.path))
                XCTAssertFalse(description.contains(png.base64EncodedString()))
                XCTAssertFalse(description.contains("cc-translate-image-"))
            }
        }
        let handle = try XCTUnwrap(recovery)
        let saved = try XCTUnwrap(retained)
        let directory = saved.deletingLastPathComponent()
        let image = directory.appendingPathComponent("region.png")
        XCTAssertEqual(try Data(contentsOf: saved), Data(png.prefix(8)))
        XCTAssertThrowsError(try handle.cleanup()) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .cleanupFailed)
        }
        XCTAssertEqual(try String(contentsOf: image, encoding: .utf8), "preserve foreign replacement")
        XCTAssertEqual(try Data(contentsOf: saved), Data(png.prefix(8)))
        try FileManager.default.removeItem(at: image)
        try FileManager.default.moveItem(at: saved, to: image)
        try handle.cleanup()
        try handle.cleanup()
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
        XCTAssertEqual(try String(contentsOf: outside, encoding: .utf8), "outside owner")
    }

    func testAttachmentCleanupDoesNotRecursivelyEraseUnexpectedEntriesAndCanRetry() throws {
        let parent = try parent()
        defer { remove(parent) }
        let attachment = try ImageTranslationAttachment(pngData: ImageTranslationFixture.png(), temporaryParent: parent)
        let directory = attachment.url.deletingLastPathComponent()
        let neighbor = directory.appendingPathComponent("unexpected.txt")
        try Data("preserve".utf8).write(to: neighbor)
        XCTAssertThrowsError(try attachment.cleanup()) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .cleanupFailed)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: attachment.url.path))
        XCTAssertEqual(try String(contentsOf: neighbor, encoding: .utf8), "preserve")
        try FileManager.default.removeItem(at: neighbor)
        try attachment.cleanup()
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    func testAttachmentCleanupRefusesReplacedFileAndNeverFollowsSymlink() throws {
        let parent = try parent()
        defer { remove(parent) }
        let attachment = try ImageTranslationAttachment(pngData: ImageTranslationFixture.png(), temporaryParent: parent)
        let saved = attachment.url.deletingLastPathComponent().appendingPathComponent("saved.png")
        let neighbor = parent.appendingPathComponent("neighbor.png")
        let bytes = Data("not owned".utf8)
        try bytes.write(to: neighbor)
        try FileManager.default.moveItem(at: attachment.url, to: saved)
        try FileManager.default.createSymbolicLink(at: attachment.url, withDestinationURL: neighbor)
        XCTAssertThrowsError(try attachment.cleanup()) { error in
            XCTAssertEqual(error as? ImageTranslationAttachmentError, .cleanupFailed)
        }
        XCTAssertEqual(try Data(contentsOf: neighbor), bytes)
        XCTAssertTrue(FileManager.default.fileExists(atPath: saved.path))
        try FileManager.default.removeItem(at: attachment.url)
        try bytes.write(to: attachment.url)
        XCTAssertThrowsError(try attachment.cleanup())
        XCTAssertEqual(try Data(contentsOf: attachment.url), bytes)
        try FileManager.default.removeItem(at: attachment.url)
        try FileManager.default.moveItem(at: saved, to: attachment.url)
        try attachment.cleanup()
    }

    func testAttachmentCleanupRefusesReplacedDirectoryWithoutTouchingNewOwner() throws {
        let parent = try parent()
        defer { remove(parent) }
        let attachment = try ImageTranslationAttachment(pngData: ImageTranslationFixture.png(), temporaryParent: parent)
        let original = attachment.url.deletingLastPathComponent()
        let moved = parent.appendingPathComponent("original-moved", isDirectory: true)
        try FileManager.default.moveItem(at: original, to: moved)
        try FileManager.default.createDirectory(at: original, withIntermediateDirectories: false)
        let foreign = original.appendingPathComponent("region.png")
        try Data("different owner".utf8).write(to: foreign)
        XCTAssertThrowsError(try attachment.cleanup())
        XCTAssertEqual(try String(contentsOf: foreign, encoding: .utf8), "different owner")
        XCTAssertTrue(FileManager.default.fileExists(atPath: moved.appendingPathComponent("region.png").path))
        try FileManager.default.removeItem(at: foreign)
        try FileManager.default.removeItem(at: original)
        try FileManager.default.moveItem(at: moved, to: original)
        try attachment.cleanup()
    }

    func testAttachmentReleaseDoesNotPretendToPerformExplicitCleanup() throws {
        let parent = try parent()
        defer { remove(parent) }
        var attachment: ImageTranslationAttachment? = try ImageTranslationAttachment(
            pngData: ImageTranslationFixture.png(), temporaryParent: parent)
        let url = try XCTUnwrap(attachment?.url)
        attachment = nil
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        try FileManager.default.removeItem(at: url)
        try FileManager.default.removeItem(at: url.deletingLastPathComponent())
    }

    func testAttachmentConcurrentCleanupRemovesOwnedImageExactlyOnce() throws {
        let parent = try parent()
        defer { remove(parent) }
        let attachment = try ImageTranslationAttachment(pngData: ImageTranslationFixture.png(), temporaryParent: parent)
        DispatchQueue.concurrentPerform(iterations: 4) { _ in
            do { try attachment.cleanup() }
            catch { XCTFail("Concurrent owned image cleanup failed") }
        }
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: parent.path).isEmpty)
    }
}
