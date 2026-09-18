import XCTest
import AppKit
import CoreText
@testable import CCTranslateMac
@testable import CCTranslateSupport

enum CaptureFixtureError: Error { case timeout, recognitionFailed }

@MainActor
final class CaptureTestSource: RegionCaptureSource {
    let image: CGImage
    var layout: [CaptureDisplay]
    var permission = true
    var automatic = true
    var onPermission: (() -> Void)?
    private(set) var permissionCalls = 0
    private(set) var layoutCalls = 0
    private(set) var requests: [DisplayCaptureRequest] = []
    var continuation: CheckedContinuation<CGImage, Error>?

    init(image: CGImage) {
        self.image = image
        layout = [CaptureDisplay(id: 17, frame: CGRect(x: -400, y: -100, width: 400, height: 180),
                                 pixelWidth: image.width, pixelHeight: image.height)]
    }
    func requestPermission() -> Bool {
        permissionCalls += 1
        onPermission?()
        return permission
    }
    func currentLayout() throws -> [CaptureDisplay] { layoutCalls += 1; return layout }
    func capture(_ request: DisplayCaptureRequest) async throws -> CGImage {
        requests.append(request)
        if automatic { return image }
        return try await withCheckedThrowingContinuation { continuation = $0 }
    }
    func finishCapture() {
        let pending = continuation
        continuation = nil
        pending?.resume(returning: image)
    }
}

final class CaptureTestOCR: ScreenOCRRecognizing, @unchecked Sendable {
    private let lock = NSLock()
    private var received: CGImage?
    private var cancellations = 0
    private let result: Result<OCRResult, Error>
    let gate: DispatchSemaphore?

    init(text: String = "Captured local words", blocked: Bool = false, fails: Bool = false) {
        gate = blocked ? DispatchSemaphore(value: 0) : nil
        result = fails ? .failure(CaptureFixtureError.recognitionFailed) :
            .success(OCRResult(text: text, supportedLanguages: ["en-US"], selectedLanguages: ["en-US"]))
    }
    var image: CGImage? {
        lock.lock()
        defer { lock.unlock() }
        return received
    }
    var cancelCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return cancellations
    }
    func recognize(_ image: CGImage) throws -> OCRResult {
        lock.lock()
        received = image
        lock.unlock()
        if let gate, gate.wait(timeout: .now() + 5) == .timedOut { throw CaptureFixtureError.timeout }
        // Intentionally permit a late result after cancel to exercise the real Support generation guard.
        return try result.get()
    }
    func cancel() {
        lock.lock()
        cancellations += 1
        lock.unlock()
    }
}

@MainActor
enum CaptureProductFixture {
    static func image() throws -> CGImage {
        let context = try XCTUnwrap(CGContext(
            data: nil, width: 800, height: 360, bitsPerComponent: 8, bytesPerRow: 3200,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 800, height: 360))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 42, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)
        ]
        for (line, y) in [("CAPTURED LOCAL WORDS", 240), ("Review before translation", 140)] {
            context.textPosition = CGPoint(x: 35, y: CGFloat(y))
            CTLineDraw(CTLineCreateWithAttributedString(
                NSAttributedString(string: line, attributes: attributes) as CFAttributedString
            ), context)
        }
        return try XCTUnwrap(context.makeImage())
    }

    static func waitFor(file: StaticString = #filePath, line: UInt = #line,
                        diagnostics: @MainActor () -> String = { "" },
                        _ condition: @MainActor () -> Bool) async throws {
        for _ in 0..<200 {
            if condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        let details = diagnostics()
        if !details.isEmpty { try NativeRenderEvidence.record(details) }
        XCTFail("The synthetic capture lifecycle did not reach the expected state. \(details)", file: file, line: line)
        throw CaptureFixtureError.timeout
    }

    static func recognize(_ capture: CaptureModel, source: CaptureTestSource) async throws {
        capture.start()
        try await waitFor { capture.phase == .selecting }
        capture.select(source.layout[0].frame)
        try await waitFor { capture.phase == .ready || capture.phase == .empty || capture.phase == .failed }
    }
}

final class CaptureModelTests: XCTestCase {
    @MainActor
    func testConstructionAndLocalCaptureDoNotStartHelperReadClipboardOrRequestPermissionEarly() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let job = CaptureTestOCR()
        let probe = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(source.requests.isEmpty)
        XCTAssertTrue(fixture.helpers.isEmpty)
        try await CaptureProductFixture.recognize(capture, source: source)
        XCTAssertEqual(capture.phase, .ready)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(capture.text, "Captured local words")
        XCTAssertTrue(fixture.helpers.isEmpty)
        XCTAssertEqual(fixture.locatorRequests, 0)
        XCTAssertTrue(fixture.copiedText.isEmpty)
        capture.translate(using: fixture.model)
        let helper = try fixture.ready()
        XCTAssertTrue(fixture.model.needsCLI)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertTrue(capture.showsTranslationStatus)
        XCTAssertTrue(fixture.model.productMessage.contains("Codex"))
        XCTAssertNotNil(capture.preview)
    }

    @MainActor
    func testPreviewAndVisionReceiveExactlyTheSameComposedPixelsAcrossDisplays() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.layout.append(CaptureDisplay(id: 18, frame: CGRect(x: 0, y: -100, width: 400, height: 180),
                                           pixelWidth: 800, pixelHeight: 360))
        let job = CaptureTestOCR()
        let probe = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertEqual(capture.frames.count, 2)
        XCTAssertEqual(source.requests.count, 2)
        capture.select(CGRect(x: -100, y: -60, width: 200, height: 100))
        try await CaptureProductFixture.waitFor { capture.phase == .ready }
        let selection = try XCTUnwrap(probe.selectedRegion)
        XCTAssertEqual(selection.fragments.count, 2)
        XCTAssertTrue(job.image === selection.image)
        XCTAssertTrue(capture.preview === probe.preview)
        XCTAssertFalse(job.image === source.image)
        XCTAssertEqual(source.requests.count, 2)
    }

    @MainActor
    func testSmallAndOffDisplaySelectionsNeverStartOCR() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let job = CaptureTestOCR()
        let probe = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        capture.select(CGRect(x: -200, y: 0, width: 9, height: 30))
        XCTAssertEqual(capture.failure, .invalidSelection)
        XCTAssertNil(job.image)
        XCTAssertFalse(capture.canTranslate)
        capture.select(CGRect(x: -1, y: 0, width: 20, height: 30))
        XCTAssertEqual(capture.failure, .invalidSelection, "The actual clipped region is only one point wide.")
        XCTAssertNil(job.image)
        XCTAssertNil(capture.preview)
    }

    @MainActor
    func testInvalidSelectionCancelsOldOCRAndKeepsFailureUntilExplicitReselection() async throws {
        for rectangle in [CGRect(x: -200, y: 0, width: 9, height: 30),
                          CGRect(x: -1, y: 0, width: 20, height: 30)] {
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let job = CaptureTestOCR(text: "Stale selection", blocked: true)
            defer { job.gate?.signal() }
            let probe = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
            let capture = CaptureModel(screen: probe)
            defer { capture.cancel() }
            capture.start()
            try await CaptureProductFixture.waitFor { capture.phase == .selecting }
            capture.select(source.layout[0].frame)
            try await CaptureProductFixture.waitFor { job.image != nil }
            let work = try XCTUnwrap(probe.ocrTask)
            capture.select(rectangle)
            job.gate?.signal()
            await work.value
            try await Task.sleep(nanoseconds: 30_000_000)
            XCTAssertGreaterThan(job.cancelCount, 0)
            XCTAssertEqual(capture.phase, .failed)
            XCTAssertEqual(capture.failure, .invalidSelection)
            XCTAssertNil(capture.preview)
            XCTAssertTrue(capture.text.isEmpty)
            XCTAssertFalse(capture.canTranslate)
            XCTAssertEqual(capture.frames.count, 1)
            capture.reselect()
            XCTAssertEqual(capture.phase, .selecting)
            XCTAssertNil(capture.failure)
            XCTAssertEqual(source.requests.count, 1)
            XCTAssertEqual(source.permissionCalls, 1)
        }
    }

    @MainActor
    func testPermissionDeniedHasRecoverableStateWithoutLayoutOrCaptureOrHelper() throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.permission = false
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        capture.start()
        XCTAssertEqual(capture.phase, .failed)
        XCTAssertEqual(capture.failure, .permissionDenied)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(source.requests.isEmpty)
        XCTAssertTrue(capture.frames.isEmpty)
        XCTAssertNil(capture.preview)
        XCTAssertFalse(capture.canTranslate)
    }

    @MainActor
    func testEmptyOCRCanBeEditedAndOversizedTextIsNeverTruncatedOrSent() async throws {
        let fixture = try ProductTestHarness(savedCLI: false)
        defer { fixture.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(text: "") },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        XCTAssertEqual(capture.phase, .empty)
        XCTAssertFalse(capture.canTranslate)
        capture.translate(using: fixture.model)
        XCTAssertTrue(fixture.helpers.isEmpty)
        let longText = String(repeating: "字", count: 3000)
        capture.text = longText
        capture.translate(using: fixture.model)
        XCTAssertEqual(capture.text, longText)
        XCTAssertTrue(fixture.helpers.isEmpty)
        capture.text = "Manually reviewed"
        XCTAssertTrue(capture.canTranslate)
        XCTAssertEqual(capture.phase, .ready)
    }

    @MainActor
    func testEditsPersistAndOnlyExplicitTranslateSubmitsFrozenOCRText() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        capture.text = "My reviewed text"
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(capture.text, "My reviewed text")
        XCTAssertTrue(fixture.helpers.isEmpty)
        capture.translate(using: fixture.model)
        capture.text = "Unsent later edit"
        let helper = try fixture.ready()
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "My reviewed text")
        XCTAssertEqual(helper.translations.last?.origin, "ocr")
        XCTAssertEqual(helper.translations.last?.useCache, false)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(capture.text, "Unsent later edit")
    }

    @MainActor
    func testCancelledCaptureDiscardsLateScreenCaptureCompletion() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        source.automatic = false
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        capture.start()
        try await CaptureProductFixture.waitFor { source.continuation != nil }
        let work = try XCTUnwrap(probe.captureTask)
        capture.cancel()
        source.finishCapture()
        await work.value
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(capture.phase, .cancelled)
        XCTAssertTrue(capture.frames.isEmpty)
        XCTAssertNil(capture.preview)
        XCTAssertTrue(capture.text.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testCancelledOCRDiscardsLateVisionTextAndReleasesPreview() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let job = CaptureTestOCR(text: "Must never appear", blocked: true)
        defer { job.gate?.signal() }
        let probe = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        capture.select(source.layout[0].frame)
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(probe.ocrTask)
        capture.cancel()
        job.gate?.signal()
        await work.value
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertGreaterThan(job.cancelCount, 0)
        XCTAssertEqual(capture.phase, .cancelled)
        XCTAssertTrue(capture.frames.isEmpty)
        XCTAssertNil(capture.preview)
        XCTAssertTrue(capture.text.isEmpty)
    }

    @MainActor
    func testReselectionUsesRetainedFramesWithoutAnotherPermissionOrCapture() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        let image = capture.frames.first?.image
        capture.reselect()
        XCTAssertEqual(capture.phase, .selecting)
        XCTAssertNil(capture.preview)
        XCTAssertTrue(capture.text.isEmpty)
        capture.select(CGRect(x: -350, y: -50, width: 200, height: 100))
        try await CaptureProductFixture.waitFor { capture.phase == .ready }
        XCTAssertTrue(capture.frames.first?.image === image)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testReselectDuringOCRDrainsOldWorkerAndRecognizesOnlyLatestSelectionAutomatically() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let old = CaptureTestOCR(text: "Obsolete recognition", blocked: true)
        let latest = CaptureTestOCR(text: "Latest retained selection")
        defer { old.gate?.signal() }
        var jobs = 0
        let probe = ScreenProbe(source: source, makeOCRJob: {
            jobs += 1
            return jobs == 1 ? old : latest
        }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        capture.select(source.layout[0].frame)
        try await CaptureProductFixture.waitFor { old.image != nil }
        let oldTask = try XCTUnwrap(probe.ocrTask)
        capture.reselect()
        XCTAssertEqual(capture.phase, .selecting)
        XCTAssertNil(capture.preview)
        capture.select(CGRect(x: -350, y: -50, width: 100, height: 100))
        XCTAssertEqual(capture.phase, .recognizing)
        capture.reselect()
        capture.select(CGRect(x: -220, y: -50, width: 120, height: 100))
        XCTAssertEqual(jobs, 1)
        XCTAssertNil(latest.image)
        old.gate?.signal()
        await oldTask.value
        try await CaptureProductFixture.waitFor { capture.phase == .ready }
        XCTAssertEqual(capture.text, "Latest retained selection")
        XCTAssertEqual(jobs, 2)
        XCTAssertTrue(latest.image === probe.selectedRegion?.image)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(source.permissionCalls, 1)
    }

    @MainActor
    func testDisplayLayoutChangeDropsRetainedStateWithoutRecapturing() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let center = NotificationCenter()
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() }, notificationCenter: center)
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        source.layout = []
        center.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        try await CaptureProductFixture.waitFor { capture.phase == .failed }
        XCTAssertEqual(capture.failure, .layoutChanged)
        XCTAssertTrue(capture.frames.isEmpty)
        XCTAssertNil(capture.preview)
        XCTAssertTrue(capture.text.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(source.permissionCalls, 1)
    }

    @MainActor
    func testLocalOCRFailurePreservesPreviewForEditingOrRetryWithoutModelUse() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(fails: true) },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        XCTAssertEqual(capture.phase, .failed)
        XCTAssertEqual(capture.failure, .ocrFailed)
        XCTAssertNotNil(capture.preview)
        capture.text = "Manually corrected"
        XCTAssertTrue(capture.canTranslate)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testClosingCapturePreservesCompletedResultAndCannotCancelANewerUnrelatedTranslation() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        try await CaptureProductFixture.recognize(capture, source: source)
        capture.translate(using: fixture.model)
        let request = try XCTUnwrap(helper.translations.last)
        helper.event("completed", id: request.id, payload: [
            "text": .string("Saved OCR result"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("ocr"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("recorded"), "history_error": .null
        ])
        capture.cancel()
        XCTAssertEqual(fixture.model.output, "Saved OCR result")
        try await CaptureProductFixture.recognize(capture, source: source)
        capture.translate(using: fixture.model)
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id), payload: [
            "text": .string("Second OCR result"), "submitted": .bool(true), "cached": .bool(false),
            "kind": .string("ocr"), "target_lang": .null, "summarize": .bool(false),
            "history": .string("recorded"), "history_error": .null
        ])
        fixture.model.performResultAction(.summary)
        let before = helper.messages.count
        capture.cancel()
        XCTAssertEqual(helper.messages.count, before)
        XCTAssertTrue(fixture.model.active)
        XCTAssertEqual(fixture.model.primaryResult, "Second OCR result")
    }

    @MainActor
    func testUnknownTranslationOutcomeStaysVisibleWithoutCaptureOrRequestReplay() async throws {
        let fixture = try ProductTestHarness()
        defer { fixture.cleanUp() }
        let helper = try fixture.ready()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer { capture.cancel() }
        try await CaptureProductFixture.recognize(capture, source: source)
        capture.translate(using: fixture.model)
        helper.failure(.translationOutcomeUnknown)
        helper.stopped()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(capture.showsTranslationStatus)
        XCTAssertFalse(capture.submitting)
        XCTAssertTrue(fixture.model.productMessage.contains("unknown"))
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
        capture.text = "Unsent edited text"
        XCTAssertFalse(capture.showsTranslationStatus)
        XCTAssertEqual(helper.translations.count, 1)
    }

    @MainActor
    func testCaptureCancelCancelsItsOwnPreparationOrActiveRequestWithoutReplaying() async throws {
        for readyBeforeSubmission in [false, true] {
            let fixture = try ProductTestHarness()
            defer { fixture.cleanUp() }
            if readyBeforeSubmission { _ = try fixture.ready() }
            let source = CaptureTestSource(image: try CaptureProductFixture.image())
            let probe = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR() },
                                    notificationCenter: NotificationCenter())
            let capture = CaptureModel(screen: probe)
            try await CaptureProductFixture.recognize(capture, source: source)
            capture.translate(using: fixture.model)
            let helper = try XCTUnwrap(fixture.helpers.last)
            if !readyBeforeSubmission { helper.event("ready") }
            XCTAssertTrue(capture.submitting)
            capture.cancel()
            if readyBeforeSubmission {
                XCTAssertEqual(helper.messages.last?.type, "cancel")
                XCTAssertEqual(helper.messages.last?.payload["request_id"],
                               .string(try XCTUnwrap(helper.translations.last?.id)))
                XCTAssertEqual(helper.translations.count, 1)
            } else {
                try fixture.finishConfiguration(on: helper)
                XCTAssertTrue(helper.translations.isEmpty)
            }
            XCTAssertEqual(capture.phase, .cancelled)
            XCTAssertTrue(capture.frames.isEmpty)
            XCTAssertNil(capture.preview)
            XCTAssertFalse(capture.submitting)
            XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        }
    }

    @MainActor
    func testNewCaptureDuringPermissionPromptSurvivesOlderStartReturning() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        defer {
            source.onPermission = nil
            capture.cancel()
        }
        source.onPermission = {
            source.onPermission = nil
            capture.start()
        }
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        XCTAssertNil(capture.failure)
        XCTAssertEqual(capture.frames.count, 1)
        XCTAssertEqual(source.permissionCalls, 2)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testCancellationDuringPermissionResponseDoesNotStartALateCapture() async throws {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let probe = ScreenProbe(source: source, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: probe)
        source.onPermission = { [weak capture] in capture?.cancel() }
        capture.start()
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(capture.phase, .cancelled)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertTrue(source.requests.isEmpty)
        XCTAssertTrue(capture.frames.isEmpty)
        XCTAssertNil(capture.preview)
    }
}
