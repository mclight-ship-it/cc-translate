import AppKit
import Combine
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

protocol ScreenOCRRecognizing: Sendable {
    func recognize(_ image: CGImage) throws -> OCRResult
    func cancel()
}

final class OCRJob: ScreenOCRRecognizing, @unchecked Sendable {
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

public enum ScreenCapturePhase: Equatable {
    case idle, capturing, selecting, preview, recognizing, recognized, failed
}

@MainActor
public final class ScreenProbe: ObservableObject {
    @Published public private(set) var preview: NSImage?
    @Published public private(set) var status = "No capture. Grant/capture is explicit."
    @Published public var text = ""
    @Published public private(set) var busy = false
    @Published public private(set) var canConfirm = false
    @Published public private(set) var phase: ScreenCapturePhase = .idle
    @Published public private(set) var frames: [CapturedDisplayFrame] = []
    @Published public private(set) var selectedRegion: RegionCaptureSelection?
    @Published public private(set) var lastError: RegionCaptureError?
    @Published public private(set) var selectedOCRLanguages: [String] = []

    private let source: any RegionCaptureSource
    private let budget: RegionCaptureBudget
    private let makeOCRJob: () -> any ScreenOCRRecognizing
    private let notificationCenter: NotificationCenter
    private var layoutObserver: NSObjectProtocol?
    private var capturedLayout: [CaptureDisplay] = []
    private var pendingFrames: [CapturedDisplayFrame] = []
    private var generation = UUID()
    private var ocrGeneration = UUID()
    var captureTask: Task<Void, Never>?
    var ocrTask: Task<Void, Never>?
    private var captureInFlight = false
    private var ocrInFlight = false
    private var ocrJob: (any ScreenOCRRecognizing)?
    private var pendingOCR: OCRIntent?

    private struct OCRIntent {
        let generation: UUID
        let ocrGeneration: UUID
        let image: CGImage
    }

    public convenience init() {
        self.init(budget: .standard)
    }

    public convenience init(budget: RegionCaptureBudget) {
        self.init(source: ScreenCaptureKitSource(), budget: budget, makeOCRJob: { OCRJob() })
    }

    init(source: any RegionCaptureSource, budget: RegionCaptureBudget = .standard,
         makeOCRJob: @escaping () -> any ScreenOCRRecognizing = { OCRJob() },
         notificationCenter: NotificationCenter = .default) {
        self.source = source
        self.budget = budget
        self.makeOCRJob = makeOCRJob
        self.notificationCenter = notificationCenter
    }

    deinit {
        if let layoutObserver { notificationCenter.removeObserver(layoutObserver) }
        captureTask?.cancel()
        ocrTask?.cancel()
        ocrJob?.cancel()
    }

    public func grantAndCaptureOnce() {
        guard !busy else {
            status = "Capture or OCR is still in progress. Cancel before capturing again."
            return
        }
        beginCapture(mainDisplayOnly: true)
    }

    /// Call before showing selection overlays. Only this explicit action (or the diagnostic capture) requests TCC.
    public func beginRegionCapture() {
        beginCapture(mainDisplayOnly: false)
    }

    private func beginCapture(mainDisplayOnly: Bool) {
        clear()
        // ScreenCaptureKit may not abort an in-flight screenshot. Do not accumulate canceled image jobs.
        guard !captureInFlight, !ocrInFlight else {
            fail(.notReady)
            return
        }
        let generation = self.generation
        let granted = source.requestPermission()
        // A permission prompt can re-enter the UI; cancellation or a newer capture owns the resulting state.
        guard generation == self.generation else { return }
        guard granted else {
            fail(.permissionDenied)
            return
        }
        let plan: [DisplayCaptureRequest]
        let captureBudget = mainDisplayOnly
            ? RegionCaptureBudget(retainedPixels: budget.retainedPixels, retainedBytes: budget.retainedBytes,
                                  compositePixels: budget.compositePixels,
                                  maximumDimension: min(budget.maximumDimension, 4096))
            : budget
        do {
            capturedLayout = try source.currentLayout()
            // Validate the complete topology even when the diagnostic only captures its main display.
            _ = try RegionCaptureGeometry.capturePlan(for: capturedLayout, budget: budget)
            let displays: [CaptureDisplay]
            if mainDisplayOnly {
                guard let display = capturedLayout.first(where: { $0.id == CGMainDisplayID() })
                    ?? capturedLayout.sorted(by: { $0.id < $1.id }).first else {
                    throw RegionCaptureError.noDisplays
                }
                displays = [display]
            } else {
                displays = capturedLayout
            }
            plan = try RegionCaptureGeometry.capturePlan(for: displays, budget: captureBudget)
        } catch {
            fail((error as? RegionCaptureError) ?? .captureFailed)
            return
        }
        layoutObserver = notificationCenter.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, generation == self.generation else { return }
                self.invalidateForLayoutNotification()
            }
        }
        busy = true
        phase = .capturing
        status = "Retaining display frames sequentially. Selection must wait until capture finishes."
        captureInFlight = true
        captureTask = Task { [weak self] in
            guard let self else { return }
            defer { captureInFlight = false }
            do {
                for request in plan {
                    guard generation == self.generation, !Task.isCancelled else { return }
                    try requireCurrentLayout()
                    let image = try await source.capture(request)
                    guard generation == self.generation, !Task.isCancelled else { return }
                    try requireCurrentLayout()
                    pendingFrames.append(CapturedDisplayFrame(
                        display: request.display, image: image, capturedAt: ProcessInfo.processInfo.systemUptime
                    ))
                    try RegionCaptureGeometry.validateFrames(pendingFrames, budget: captureBudget)
                }
                frames = pendingFrames
                pendingFrames.removeAll()
                captureTask = nil
                busy = false
                phase = .selecting
                status = "Frames retained. Select a region; preview and OCR reuse these pixels, not a new capture."
                if mainDisplayOnly, let frame = frames.first {
                    // Preserve the diagnostic's explicit preview -> confirm OCR flow.
                    selectedRegion = RegionCaptureSelection(rect: frame.display.frame, image: frame.image,
                                                             fragments: [])
                    preview = NSImage(cgImage: frame.image, size: frame.pixelSize)
                    phase = .preview
                    canConfirm = true
                    status = "Display \(frame.display.id): \(frame.image.width)x\(frame.image.height). Confirm this frame for local OCR."
                }
            } catch {
                guard generation == self.generation, !Task.isCancelled else { return }
                fail((error as? RegionCaptureError) ?? .captureFailed)
            }
        }
    }

    /// Cancel local recognition and return to selection without releasing or recapturing display frames.
    public func prepareRegionSelection() throws {
        do {
            guard !frames.isEmpty, phase != .capturing else { throw RegionCaptureError.notReady }
            try requireCurrentLayout()
            stopOCR()
            selectedRegion = nil
            preview = nil
            text = ""
            canConfirm = false
            busy = false
            lastError = nil
            phase = .selecting
            status = "Select another region from the retained frames. No new capture."
        } catch { throw selectionFailure(error) }
    }

    /// Synchronous bounded composition, with no permission, capture, clipboard, file or helper activity.
    @discardableResult
    public func selectRegion(_ globalRect: CGRect) throws -> RegionCaptureSelection {
        try prepareRegionSelection()
        do {
            let selection = try RegionCaptureGeometry.compose(globalRect, from: frames, budget: budget)
            try requireCurrentLayout()
            selectedRegion = selection
            preview = selection.preview
            lastError = nil
            phase = .preview
            canConfirm = true
            status = "Selected retained pixels. Confirm for local OCR; nothing is sent automatically."
            return selection
        } catch { throw selectionFailure(error) }
    }

    /// Confirm this selection once. A canceled previous Vision job drains before the confirmed job can start.
    public func confirmOCR() {
        guard !busy, canConfirm, let image = selectedRegion?.image else {
            status = "Select a retained region and wait for current work before confirming local OCR."
            return
        }
        do { try requireCurrentLayout() }
        catch {
            fail(.layoutChanged)
            return
        }
        canConfirm = false
        busy = true
        phase = .recognizing
        lastError = nil
        pendingOCR = OCRIntent(generation: generation, ocrGeneration: ocrGeneration, image: image)
        if ocrInFlight {
            status = "Waiting for previous local OCR cancellation before recognizing this selection..."
        } else {
            startPendingOCR()
        }
    }

    private func startPendingOCR() {
        guard !ocrInFlight, let intent = pendingOCR else { return }
        pendingOCR = nil
        guard intent.generation == generation, intent.ocrGeneration == ocrGeneration else { return }
        do { try requireCurrentLayout() }
        catch {
            fail(.layoutChanged)
            return
        }
        let generation = intent.generation
        let ocrGeneration = intent.ocrGeneration
        let image = intent.image
        let job = makeOCRJob()
        ocrJob = job
        status = "Recognizing the retained preview frame locally..."
        ocrInFlight = true
        ocrTask = Task { [weak self] in
            defer {
                self?.ocrInFlight = false
                self?.startPendingOCR()
            }
            do {
                let worker = Task.detached(priority: .userInitiated) {
                    try job.recognize(image)
                }
                let result = try await withTaskCancellationHandler {
                    try await worker.value
                } onCancel: {
                    job.cancel()
                    worker.cancel()
                }
                guard let self, generation == self.generation, ocrGeneration == self.ocrGeneration,
                      !Task.isCancelled else { return }
                try requireCurrentLayout()
                ocrJob = nil
                ocrTask = nil
                text = result.text
                selectedOCRLanguages = result.selectedLanguages
                busy = false
                phase = .recognized
                status = result.text.isEmpty ? "No text found in this retained region. No upload."
                    : "Local OCR only. Supported: \(result.supportedLanguages.joined(separator: ", ")). No upload."
            } catch {
                guard let self, generation == self.generation, ocrGeneration == self.ocrGeneration,
                      !Task.isCancelled else { return }
                if error as? RegionCaptureError == .layoutChanged {
                    fail(.layoutChanged)
                    return
                }
                ocrJob = nil
                ocrTask = nil
                busy = false
                canConfirm = true
                phase = .preview
                lastError = .ocrFailed
                status = "Vision OCR failed. No text sent anywhere."
            }
        }
    }

    /// UI must also release its copies of frames/previews. An in-flight system capture may finish after cancellation.
    public func cancel() { clear() }

    public func clear() {
        generation = UUID()
        stopOCR()
        captureTask?.cancel()
        captureTask = nil
        if let layoutObserver { notificationCenter.removeObserver(layoutObserver) }
        layoutObserver = nil
        capturedLayout.removeAll()
        pendingFrames.removeAll()
        frames.removeAll()
        selectedRegion = nil
        preview = nil
        text = ""
        lastError = nil
        canConfirm = false
        busy = false
        phase = .idle
        status = "Capture and OCR cleared."
    }

    private func stopOCR() {
        ocrGeneration = UUID()
        pendingOCR = nil
        ocrJob?.cancel()
        ocrJob = nil
        ocrTask?.cancel()
        ocrTask = nil
        selectedOCRLanguages = []
    }

    private func selectionFailure(_ error: Error) -> RegionCaptureError {
        let failure = (error as? RegionCaptureError) ?? .compositionFailed
        if failure == .layoutChanged { fail(failure) }
        else {
            lastError = failure
            status = "Region selection failed (\(failure.rawValue)). Select a valid region or capture again explicitly."
        }
        return failure
    }

    private func requireCurrentLayout() throws {
        do {
            guard RegionCaptureGeometry.layoutMatches(capturedLayout, try source.currentLayout()) else {
                throw RegionCaptureError.layoutChanged
            }
        } catch { throw RegionCaptureError.layoutChanged }
    }

    private func invalidateForLayoutNotification() {
        // Invalidate even if a display unplug/replug has already restored the original geometry.
        fail(.layoutChanged)
    }

    private func fail(_ error: RegionCaptureError) {
        clear()
        lastError = error
        phase = .failed
        switch error {
        case .permissionDenied:
            status = "Screen capture not granted. System Settings/restart may be required."
        case .layoutChanged:
            status = "Display layout changed. Retained frames discarded; capture again explicitly."
        case .budgetExceeded:
            status = "Screen image resource budget exceeded. No further capture or OCR is running."
        case .notReady:
            status = "Previous capture or OCR cancellation is still finishing. Capture again explicitly after it finishes."
        default:
            status = "Capture failed (\(error.rawValue)). Check screen permission and retry explicitly."
        }
    }
}
