import AppKit
import Combine
import CCTranslateSupport

@MainActor
final class CaptureModel: ObservableObject {
    enum Phase: Equatable { case idle, capturing, selecting, recognizing, ready, empty, failed, cancelled }
    private enum Notice { case none, textRequired, translationBusy, input }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var frames: [CapturedDisplayFrame] = []
    @Published private(set) var preview: NSImage?
    @Published private(set) var failure: RegionCaptureError?
    @Published var text = "" {
        didSet {
            notice = .none
            if phase == .ready || phase == .empty {
                phase = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .empty : .ready
            }
        }
    }
    @Published private(set) var submitted = false
    @Published private(set) var automaticallyTranslates = false
    private let screen: ScreenProbe
    private var observation: AnyCancellable?
    private var generation = UUID()
    private var appliedRecognition: UUID?
    private var startingCapture = false
    private var selectionError: RegionCaptureError?
    private var notice: Notice = .none
    private weak var translationModel: ProbeModel?
    private var translationIntent: UUID?
    private var submittedText: String?
    private var submittedImage = false
    private weak var automaticModel: ProbeModel?
    private var automaticMode: CaptureTranslationMode = .text
    private var automaticAttempt: UUID?
    private var automaticBaseline: UUID?
    private var automaticObservation: AnyCancellable?
    private var automaticSubmitting = false
    private var releasedCapture = false
    private let latencyClock: () -> TimeInterval
    private var timing: CaptureTimingSnapshot?
    private var timingRecorded = false

    private var timingNow: TimeInterval { automaticModel?.captureTimingNow ?? latencyClock() }

    var submittedIntent: UUID? { submitted ? translationIntent : nil }
    var busy: Bool { phase == .capturing || phase == .recognizing }
    var offersCaptureSettings: Bool {
        automaticallyTranslates && automaticMode == .text && !busy && !submitted &&
            (phase == .empty || failure == .ocrFailed)
    }
    var submitting: Bool {
        submitted && translationIntent == translationModel?.translationIntentID &&
            (translationModel?.active == true || translationModel?.preparing == true)
    }
    var showsTranslationStatus: Bool {
        guard let translationModel else { return false }
        if submittedImage && busy && !submitting { return false }
        return submitted && translationIntent == translationModel.translationIntentID &&
            !translationModel.productMessage.isEmpty &&
            (submitting || submittedImage || submittedText.map { text.utf8.elementsEqual($0.utf8) } == true)
    }
    var canTranslate: Bool {
        preview != nil && !busy && phase != .selecting && !submitting &&
            TextInputPreflight.check(text, limit: nil, requireLimit: false) == nil
    }
    func canTranslate(using model: ProbeModel) -> Bool { canTranslate && model.inputIssue(for: text) == nil }
    var canTranslateImage: Bool {
        preview != nil && screen.selectedRegion != nil && phase != .capturing &&
            phase != .selecting && phase != .cancelled && !submitting
    }

    convenience init() { self.init(screen: ScreenProbe()) }

    init(screen: ScreenProbe, latencyClock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.screen = screen
        self.latencyClock = latencyClock
        screen.onTimingEvent = { [weak self] event in
            guard let self, self.phase != .cancelled, !self.releasedCapture else { return }
            let now = self.timingNow
            switch event {
            case .framesReady: self.timing?.framesReady(now: now)
            case .ocrStarted: self.timing?.mark("ocr_started_ms", now: now)
            case .ocrFinished: self.timing?.mark("ocr_finished_ms", now: now)
            }
        }
        observation = screen.objectWillChange.sink { [weak self] in
            guard let self else { return }
            let generation = self.generation
            DispatchQueue.main.async { [weak self] in
                guard let self, self.generation == generation else { return }
                self.synchronize()
            }
        }
    }

    func start() {
        cancel()
        beginCapture()
    }

    func startTranslation(using model: ProbeModel, mode: CaptureTranslationMode) {
        cancel()
        automaticModel = model
        automaticMode = mode
        automaticBaseline = model.translationIntentID
        automaticallyTranslates = true
        guard !model.active, !model.preparing else {
            releasedCapture = true
            notice = .translationBusy
            phase = .failed
            return
        }
        automaticObservation = model.objectWillChange.sink { [weak self] in
            DispatchQueue.main.async { [weak self] in
                _ = self?.discardSupersededAutomaticCapture()
            }
        }
        model.prepareTranslation(onlyIfConfigured: true)
        beginCapture()
    }

    private func beginCapture() {
        let generation = self.generation
        timing = CaptureTimingSnapshot(now: timingNow)
        timingRecorded = false
        phase = .capturing
        startingCapture = true
        screen.beginRegionCapture()
        startingCapture = false
        guard generation == self.generation else { return }
        synchronize()
    }

    func select(_ rectangle: CGRect) {
        guard !discardSupersededAutomaticCapture(), phase != .cancelled, !releasedCapture else { return }
        cancelTranslationIfOwned()
        generation = UUID()
        automaticAttempt = nil
        appliedRecognition = nil
        preview = nil
        text = ""
        failure = nil
        selectionError = nil
        do {
            guard rectangle.width >= RegionSelectionState.minimumSize,
                  rectangle.height >= RegionSelectionState.minimumSize else {
                try screen.prepareRegionSelection()
                throw RegionCaptureError.invalidSelection
            }
            timing?.beginSelection(now: timingNow)
            timing?.completeSelection(now: timingNow)
            let selected = try screen.selectRegion(rectangle)
            guard selected.rect.width >= RegionSelectionState.minimumSize,
                  selected.rect.height >= RegionSelectionState.minimumSize else {
                try screen.prepareRegionSelection()
                throw RegionCaptureError.invalidSelection
            }
            timing?.mark("selection_composed_ms", now: timingNow)
            if automaticallyTranslates && automaticMode == .image {
                synchronize()
                submitAutomatically()
            } else {
                recognizeSelection()
            }
        } catch let error as RegionCaptureError {
            failSelection(error)
        } catch {
            failSelection(.compositionFailed)
        }
    }

    func recognizeSelection() {
        guard !discardSupersededAutomaticCapture(), phase != .cancelled, !releasedCapture else { return }
        // An image-mode capture never depends on Vision, including explicit retry callbacks.
        if automaticallyTranslates && automaticMode == .image {
            submitAutomatically()
            return
        }
        guard screen.selectedRegion != nil else {
            failSelection(.noSelection)
            return
        }
        selectionError = nil
        generation = UUID()
        automaticAttempt = nil
        appliedRecognition = nil
        if !screen.busy && screen.canConfirm {
            timing?.restartOCR()
            timingRecorded = false
        }
        screen.confirmOCR()
        synchronize()
    }

    func reselect() {
        guard !discardSupersededAutomaticCapture(), phase != .cancelled, !releasedCapture else { return }
        guard !screen.frames.isEmpty, phase != .capturing else {
            failure = .notReady
            return
        }
        cancelTranslationIfOwned()
        generation = UUID()
        automaticAttempt = nil
        appliedRecognition = nil
        text = ""
        preview = nil
        failure = nil
        selectionError = nil
        do {
            try screen.prepareRegionSelection()
            timing?.beginSelection(now: timingNow)
            timingRecorded = false
            synchronize()
        } catch let error as RegionCaptureError {
            failSelection(error)
        } catch {
            failSelection(.compositionFailed)
        }
    }

    func translate(using model: ProbeModel) {
        guard !discardSupersededAutomaticCapture() else { return }
        guard !model.active, !model.preparing else {
            notice = .translationBusy
            objectWillChange.send()
            return
        }
        if model.inputIssue(for: text) != nil {
            notice = .input
            objectWillChange.send()
            return
        }
        guard canTranslate else {
            notice = .textRequired
            objectWillChange.send()
            return
        }
        let reviewed = text
        let generation = self.generation
        model.input = reviewed
        guard generation == self.generation else { return }
        if automaticallyTranslates && automaticBaseline != model.translationIntentID {
            cancel()
            return
        }
        guard !model.active, !model.preparing else {
            notice = .translationBusy
            objectWillChange.send()
            return
        }
        let previousIntent = model.translationIntentID
        var intent: UUID?
        // Remember our intent before synchronous model callbacks can replace it with unrelated work.
        let observation = model.objectWillChange.sink {
            if intent == nil, model.translationIntentID != previousIntent {
                intent = model.translationIntentID
            }
        }
        model.translate(origin: "ocr", useCache: false, captureTiming: timing)
        observation.cancel()
        guard let intent else { return }
        guard generation == self.generation, model.translationIntentID == intent else {
            if model.translationIntentID == intent { model.cancel() }
            return
        }
        translationModel = model
        translationIntent = intent
        submittedText = reviewed
        submitted = true
        submittedImage = false
        timingRecorded = true
    }

    func translateImage(using model: ProbeModel) {
        guard !discardSupersededAutomaticCapture() else { return }
        guard !model.active, !model.preparing else {
            notice = .translationBusy
            objectWillChange.send()
            return
        }
        guard canTranslateImage, let image = screen.selectedRegion?.image else {
            failure = .noSelection
            return
        }
        let generation = self.generation
        guard let intent = model.translateImage(image, captureTiming: timing) else { return }
        guard generation == self.generation else {
            if model.translationIntentID == intent { model.cancel() }
            return
        }
        translationModel = model
        translationIntent = intent
        submittedText = nil
        submittedImage = true
        submitted = true
        timingRecorded = true
    }

    func cancel() {
        recordCaptureOutcome(.cancelled)
        cancelTranslationIfOwned()
        generation = UUID()
        automaticallyTranslates = false
        automaticModel = nil
        automaticAttempt = nil
        automaticBaseline = nil
        automaticObservation = nil
        automaticSubmitting = false
        releasedCapture = false
        timing = nil
        appliedRecognition = nil
        screen.cancel()
        frames = []
        preview = nil
        text = ""
        failure = nil
        selectionError = nil
        phase = .cancelled
    }

    func cancelCurrentAction() {
        if !automaticallyTranslates && submittedImage && submitting {
            translationModel?.cancel()
        } else {
            cancel()
        }
    }

    private func cancelTranslationIfOwned() {
        if submitting { translationModel?.cancel() }
        translationModel = nil
        translationIntent = nil
        submittedText = nil
        submittedImage = false
        submitted = false
    }

    private func failSelection(_ error: RegionCaptureError) {
        selectionError = error
        synchronize()
        if !screen.frames.isEmpty { timing?.beginSelection(now: timingNow) }
    }

    private func recordCaptureOutcome(_ outcome: TranslationLatency.Outcome) {
        guard !timingRecorded, let timing else { return }
        timingRecorded = true
        automaticModel?.recordCaptureLatency(timing, outcome: outcome)
    }

    private func discardSupersededAutomaticCapture() -> Bool {
        guard automaticallyTranslates, !submitted, !automaticSubmitting,
              let model = automaticModel, let baseline = automaticBaseline,
              model.translationIntentID != baseline else { return false }
        // A newer manual/selection/history intent owns the result, even if it already completed.
        // This capture has not submitted anything, so cancellation only releases local work.
        cancel()
        return true
    }

    private func synchronize() {
        guard !discardSupersededAutomaticCapture(), !automaticSubmitting,
              phase != .cancelled, !releasedCapture else { return }
        if startingCapture && screen.phase == .idle { return }
        frames = screen.frames
        failure = screen.lastError ?? selectionError
        preview = screen.preview
        if preview == nil && frames.isEmpty {
            text = ""
            appliedRecognition = nil
        }
        if failure != nil {
            recordCaptureOutcome(.failed)
            phase = .failed
            return
        }
        if automaticallyTranslates && automaticAttempt == generation && !submitted {
            recordCaptureOutcome(.failed)
            phase = .failed
            return
        }
        switch screen.phase {
        case .idle: phase = .idle
        case .capturing: phase = .capturing
        case .selecting: phase = .selecting
        case .preview:
            if automaticallyTranslates && automaticMode == .image {
                phase = .ready
            } else {
                phase = .failed
                failure = screen.lastError ?? .notReady
            }
        case .recognizing: phase = .recognizing
        case .recognized:
            if appliedRecognition != generation {
                text = screen.text
                appliedRecognition = generation
            }
            phase = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .empty : .ready
            if phase == .empty { recordCaptureOutcome(.failed) }
            if phase == .ready { submitAutomatically() }
        case .failed:
            recordCaptureOutcome(.failed)
            phase = .failed
        }
    }

    private func submitAutomatically() {
        guard !discardSupersededAutomaticCapture(), automaticallyTranslates, !releasedCapture, !submitted,
              automaticAttempt != generation, let model = automaticModel else { return }
        guard (automaticMode == .text && screen.phase == .recognized && phase == .ready) ||
              (automaticMode == .image && screen.phase == .preview && failure == nil) else { return }
        automaticAttempt = generation
        let generation = self.generation
        automaticSubmitting = true
        defer { automaticSubmitting = false }
        if automaticMode == .image { translateImage(using: model) }
        else { translate(using: model) }
        guard generation == self.generation else { return }
        guard submitted else {
            recordCaptureOutcome(.failed)
            phase = .failed
            return
        }
        // The translation owns its frozen text/image now. Clear only local capture resources,
        // not the owned translation intent; queued ScreenProbe notifications must not restore it.
        releasedCapture = true
        automaticObservation = nil
        self.generation = UUID()
        screen.clear()
        frames = []
        preview = nil
    }

    func message(using model: ProbeModel) -> String {
        switch notice {
        case .textRequired:
            if automaticallyTranslates {
                return model.text("No readable text found. Capture another region.", "未识别到文字。请截取其他区域。")
            }
            return model.text("Review or enter some text before translating.", "请确认或输入文字后再翻译。")
        case .input:
            if let issue = model.inputIssue(for: text) { return issue.message(using: model) }
        case .translationBusy: return model.text("Finish or cancel the current translation first.", "请先完成或取消当前翻译。")
        case .none: break
        }
        if let failure {
            switch failure {
            case .permissionDenied:
                return model.text("Screen Recording permission was denied. Enable CC Translate in System Settings, then capture again. A restart may be needed.",
                                  "屏幕录制权限被拒绝。请在系统设置中允许 CC Translate 后重新截图；可能需要重启应用。")
            case .layoutChanged:
                return model.text("The display layout changed. Retained images were released; capture again explicitly.",
                                  "屏幕布局已更改。保留的图片已释放，请重新截图。")
            case .noDisplays, .invalidLayout:
                return model.text("No usable display layout was found. Check connected displays and capture again.",
                                  "未找到可用屏幕布局。请检查已连接的屏幕后重新截图。")
            case .invalidSelection, .noSelection:
                return model.text("Select a visible region at least 10 × 10 points, or use an entire display.",
                                  "请选择至少 10 × 10 点的可见区域，或使用整个屏幕。")
            case .notReady:
                return model.text("The previous local operation is still finishing. Wait briefly, then retry.",
                                  "上一次本地操作仍在结束。请稍候再重试。")
            case .budgetExceeded:
                return model.text("The image exceeds the local memory budget. Try a smaller region or lower display resolution.",
                                  "图片超出本地内存预算。请尝试较小选区或较低屏幕分辨率。")
            case .captureFailed:
                return model.text("Screen capture failed. Check screen permission and capture again explicitly.",
                                  "截图失败。请检查屏幕权限后重新截图。")
            case .compositionFailed:
                return model.text("The retained region could not be composed. Reselect the region or capture again.",
                                  "无法合成保留的截图区域。请重选区域或重新截图。")
            case .ocrFailed:
                if automaticallyTranslates {
                    return model.text("Text recognition failed. Capture again or open capture settings to review image mode. Nothing was sent.",
                                      "文字识别失败。请重新截图，或打开截图设置查看图片模式。未发送任何内容。")
                }
                return model.text("Local text recognition failed. Retry local OCR, select another region, or type the text.",
                                  "本地文字识别失败。请重试识别、重选区域，或手动输入文字。")
            }
        }
        switch phase {
        case .idle: return model.text("Capture a region to begin.", "截取区域以开始。")
        case .capturing: return model.text("Retaining all display frames locally…", "正在本地保留所有屏幕的画面…")
        case .selecting: return model.text("Select a region by dragging, two clicks, or the keyboard.", "请通过拖动、两次点击或键盘选择区域。")
        case .recognizing: return model.text("Recognizing text from the retained region locally…", "正在本地识别保留区域中的文字…")
        case .ready:
            if automaticallyTranslates {
                if showsTranslationStatus { return model.productMessage }
                return model.text("Sending the selected region for translation…", "正在翻译所选区域…")
            }
            return model.text("Review and edit the recognized text. Nothing has been sent automatically.", "请确认并编辑识别文字。未自动发送任何内容。")
        case .empty:
            if automaticallyTranslates {
                return model.text("No readable text found. Capture again or open capture settings to review image mode. Nothing was sent.",
                                  "未识别到文字。请重新截图，或打开截图设置查看图片模式。未发送任何内容。")
            }
            return model.text("No readable text. Select another region, or type the text below.", "没有可读文字。请重选区域，或在下方输入文字。")
        case .failed: return model.text("The local operation failed. Retry explicitly.", "本地操作失败，请手动重试。")
        case .cancelled: return model.text("Capture cancelled. Retained images were released.", "已取消截图，并释放保留的图片。")
        }
    }
}
