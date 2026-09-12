import AppKit
import Combine
import ScreenCaptureKit
import Vision

public struct OCRResult: Sendable {
    public let text: String
    public let supportedLanguages: [String]
    public let selectedLanguages: [String]
}

public enum LocalOCR {
    public static func recognize(_ image: CGImage) throws -> OCRResult {
        try OCRJob().recognize(image)
    }
}

private final class OCRJob {
    private let lock = NSLock()
    private var cancelled = false
    private var activeRequest: VNRecognizeTextRequest?

    func cancel() {
        lock.lock()
        cancelled = true
        let request = activeRequest
        lock.unlock()
        request?.cancel()
    }

    func recognize(_ image: CGImage) throws -> OCRResult {
        let request = VNRecognizeTextRequest()
        lock.lock()
        if cancelled {
            lock.unlock()
            throw CancellationError()
        }
        activeRequest = request
        lock.unlock()
        defer {
            lock.lock()
            activeRequest = nil
            lock.unlock()
        }
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let supported = try request.supportedRecognitionLanguages()
        let preferred = ["en-US", "zh-Hans", "zh-Hant"]
        let selected = preferred.filter { supported.contains($0) }
        if !selected.isEmpty { request.recognitionLanguages = selected }
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        lock.lock()
        let wasCancelled = cancelled
        lock.unlock()
        if wasCancelled { throw CancellationError() }
        let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
            .joined(separator: "\n")
        return OCRResult(text: text, supportedLanguages: supported, selectedLanguages: selected)
    }
}

@MainActor
public final class ScreenProbe: ObservableObject {
    @Published public private(set) var preview: NSImage?
    @Published public private(set) var status = "No capture. Grant/capture is explicit."
    @Published public private(set) var text = ""
    @Published public private(set) var busy = false
    @Published public private(set) var canConfirm = false
    private var image: CGImage?
    private var generation = UUID()
    private var task: Task<Void, Never>?
    private var ocrJob: OCRJob?

    public init() {}

    public func grantAndCaptureOnce() {
        guard !busy else { return }
        clear()
        guard Permissions.requestScreenCapture() else {
            status = "Screen capture not granted. System Settings/restart may be required."
            return
        }
        let generation = self.generation
        busy = true
        status = "Capturing one display once..."
        task = Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(
                    false, onScreenWindowsOnly: true
                )
                guard generation == self.generation, !Task.isCancelled else { return }
                guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })
                    ?? content.displays.sorted(by: { $0.displayID < $1.displayID }).first else {
                    throw ProbeError.noDisplay
                }
                let ownWindows = content.windows.filter {
                    $0.owningApplication?.processID == ProcessInfo.processInfo.processIdentifier
                }
                let filter = SCContentFilter(display: display, excludingWindows: ownWindows)
                let configuration = SCStreamConfiguration()
                let scale = min(1.0, 4096.0 / Double(max(display.width, display.height)))
                configuration.width = max(1, Int(Double(display.width) * scale))
                configuration.height = max(1, Int(Double(display.height) * scale))
                configuration.showsCursor = false
                configuration.capturesAudio = false
                let image = try await SCScreenshotManager.captureImage(
                    contentFilter: filter, configuration: configuration
                )
                guard generation == self.generation, !Task.isCancelled else { return }
                self.image = image
                preview = NSImage(cgImage: image, size: NSSize(width: CGFloat(image.width), height: CGFloat(image.height)))
                canConfirm = true
                busy = false
                status = "Display \(display.displayID): \(image.width)x\(image.height). Confirm this frame for local OCR."
            } catch {
                guard generation == self.generation, !Task.isCancelled else { return }
                busy = false
                status = "Capture failed. Check screen permission and retry explicitly."
            }
        }
    }

    public func confirmOCR() {
        guard !busy, canConfirm, let image = image else { return }
        let generation = self.generation
        canConfirm = false
        busy = true
        let job = OCRJob()
        ocrJob = job
        status = "Recognizing the retained preview frame locally..."
        task = Task {
            do {
                let result = try await Task.detached(priority: .userInitiated) {
                    try job.recognize(image)
                }.value
                guard generation == self.generation, !Task.isCancelled else { return }
                ocrJob = nil
                text = result.text
                busy = false
                status = "Local OCR only. Supported: \(result.supportedLanguages.joined(separator: ", ")). No upload."
            } catch {
                guard generation == self.generation, !Task.isCancelled else { return }
                ocrJob = nil
                busy = false
                canConfirm = true
                status = "Vision OCR failed. No text sent anywhere."
            }
        }
    }

    public func clear() {
        generation = UUID()
        ocrJob?.cancel()
        ocrJob = nil
        task?.cancel()
        task = nil
        image = nil
        preview = nil
        text = ""
        canConfirm = false
        busy = false
        status = "Capture and OCR cleared."
    }
}
