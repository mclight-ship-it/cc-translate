import AppKit
import Combine
import CCTranslateSupport

@MainActor
final class CaptureModel: ObservableObject {
    enum Phase: Equatable { case idle, capturing, selecting, recognizing, ready, empty, failed, cancelled }
    private enum Notice { case none, textRequired, translationBusy, input(TextInputPreflight.Issue) }

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

    var busy: Bool { phase == .capturing || phase == .recognizing }
    var submitting: Bool {
        submitted && translationIntent == translationModel?.translationIntentID &&
            (translationModel?.active == true || translationModel?.preparing == true)
    }
    var showsTranslationStatus: Bool {
        guard let translationModel else { return false }
        if submittedImage && busy && !submitting { return false }
        return submitted && translationIntent == translationModel.translationIntentID &&
            !translationModel.productMessage.isEmpty &&
            (submitting || submittedImage || text == submittedText)
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

    init(screen: ScreenProbe) {
        self.screen = screen
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
        let generation = self.generation
        phase = .capturing
        startingCapture = true
        screen.beginRegionCapture()
        startingCapture = false
        guard generation == self.generation else { return }
        synchronize()
    }

    func select(_ rectangle: CGRect) {
        cancelTranslationIfOwned()
        generation = UUID()
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
            let selected = try screen.selectRegion(rectangle)
            guard selected.rect.width >= RegionSelectionState.minimumSize,
                  selected.rect.height >= RegionSelectionState.minimumSize else {
                try screen.prepareRegionSelection()
                throw RegionCaptureError.invalidSelection
            }
            recognizeSelection()
        } catch let error as RegionCaptureError {
            failSelection(error)
        } catch {
            failSelection(.compositionFailed)
        }
    }

    func recognizeSelection() {
        guard screen.selectedRegion != nil else {
            failSelection(.noSelection)
            return
        }
        selectionError = nil
        generation = UUID()
        appliedRecognition = nil
        screen.confirmOCR()
        synchronize()
    }

    func reselect() {
        guard !screen.frames.isEmpty, phase != .capturing else {
            failure = .notReady
            return
        }
        cancelTranslationIfOwned()
        generation = UUID()
        appliedRecognition = nil
        text = ""
        preview = nil
        failure = nil
        selectionError = nil
        do {
            try screen.prepareRegionSelection()
            synchronize()
        } catch let error as RegionCaptureError {
            failSelection(error)
        } catch {
            failSelection(.compositionFailed)
        }
    }

    func translate(using model: ProbeModel) {
        guard !model.active, !model.preparing else {
            notice = .translationBusy
            objectWillChange.send()
            return
        }
        if let issue = model.inputIssue(for: text) {
            notice = .input(issue)
            objectWillChange.send()
            return
        }
        guard canTranslate else {
            notice = .textRequired
            objectWillChange.send()
            return
        }
        let reviewed = text
        translationModel = model
        model.input = reviewed
        model.translate(origin: "ocr", useCache: false)
        translationIntent = model.translationIntentID
        submittedText = reviewed
        submitted = true
        submittedImage = false
    }

    func translateImage(using model: ProbeModel) {
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
        guard let intent = model.translateImage(image) else { return }
        guard generation == self.generation else {
            if model.translationIntentID == intent { model.cancel() }
            return
        }
        translationModel = model
        translationIntent = intent
        submittedText = nil
        submittedImage = true
        submitted = true
    }

    func cancel() {
        cancelTranslationIfOwned()
        generation = UUID()
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
        if submittedImage && submitting {
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
    }

    private func synchronize() {
        guard phase != .cancelled else { return }
        if startingCapture && screen.phase == .idle { return }
        frames = screen.frames
        failure = screen.lastError ?? selectionError
        preview = screen.preview
        if preview == nil && frames.isEmpty {
            text = ""
            appliedRecognition = nil
        }
        if failure != nil {
            phase = .failed
            return
        }
        switch screen.phase {
        case .idle: phase = .idle
        case .capturing: phase = .capturing
        case .selecting: phase = .selecting
        case .preview:
            phase = .failed
            failure = screen.lastError ?? .notReady
        case .recognizing: phase = .recognizing
        case .recognized:
            if appliedRecognition != generation {
                text = screen.text
                appliedRecognition = generation
            }
            phase = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .empty : .ready
        case .failed: phase = .failed
        }
    }

    func message(using model: ProbeModel) -> String {
        switch notice {
        case .textRequired: return model.text("Review or enter some text before translating.", "请确认或输入文字后再翻译。")
        case .input(let issue): return issue.message(using: model)
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
                return model.text("Local text recognition failed. Retry local OCR, select another region, or type the text.",
                                  "本地文字识别失败。请重试识别、重选区域，或手动输入文字。")
            }
        }
        switch phase {
        case .idle: return model.text("Capture a region to begin.", "截取区域以开始。")
        case .capturing: return model.text("Retaining all display frames locally…", "正在本地保留所有屏幕的画面…")
        case .selecting: return model.text("Select a region by dragging, two clicks, or the keyboard.", "请通过拖动、两次点击或键盘选择区域。")
        case .recognizing: return model.text("Recognizing text from the retained region locally…", "正在本地识别保留区域中的文字…")
        case .ready: return model.text("Review and edit the recognized text. Nothing has been sent automatically.", "请确认并编辑识别文字。未自动发送任何内容。")
        case .empty: return model.text("No readable text. Select another region, or type the text below.", "没有可读文字。请重选区域，或在下方输入文字。")
        case .failed: return model.text("The local operation failed. Retry explicitly.", "本地操作失败，请手动重试。")
        case .cancelled: return model.text("Capture cancelled. Retained images were released.", "已取消截图，并释放保留的图片。")
        }
    }
}
