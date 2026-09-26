import AppKit
import ImageIO
import UniformTypeIdentifiers
import CCTranslateSupport

enum ImageTranslationPNG {
    enum EncodingError: Error { case failed }

    static func encode(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
            throw EncodingError.failed
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw EncodingError.failed }
        return data as Data
    }
}

// CGImage is immutable; this transfers only the composited region, never its retained display frames.
private struct ImageTranslationPixels: @unchecked Sendable {
    let image: CGImage
}

@MainActor
final class NativeImageAttachment: AppImageAttachment {
    private let attachment: ImageTranslationAttachment
    var imagePath: String { attachment.url.path }
    var imageBytes: Int { attachment.byteCount }
    var imageSHA256: String { attachment.sha256 }

    init(_ attachment: ImageTranslationAttachment) { self.attachment = attachment }

    static func make(_ image: CGImage) async throws -> AppImageAttachment {
        let pixels = ImageTranslationPixels(image: image)
        do {
            let attachment = try await Task.detached(priority: .userInitiated) {
                try ImageTranslationAttachment(pngData: ImageTranslationPNG.encode(pixels.image))
            }.value
            return NativeImageAttachment(attachment)
        } catch let failure as ImageTranslationAttachmentCreationError {
            throw AppImageCreationFailure(cleanup: NativeImageCleanup(failure.cleanupHandle))
        }
    }

    func cleanup() async throws {
        let attachment = attachment
        try await Task.detached(priority: .utility) { try attachment.cleanup() }.value
    }
}

@MainActor
private final class NativeImageCleanup: AppImageCleanup {
    private let handle: ImageTranslationCleanupHandle
    init(_ handle: ImageTranslationCleanupHandle) { self.handle = handle }

    func cleanup() async throws {
        let handle = handle
        try await Task.detached(priority: .utility) { try handle.cleanup() }.value
    }
}
