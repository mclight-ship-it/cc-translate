import XCTest
import AppKit
import Combine
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
private final class AutomaticCaptureFixture {
    let source: CaptureTestSource
    let screen: ScreenProbe
    let capture: CaptureModel
    let notifications = NotificationCenter()

    init(makeOCRJob: @escaping () -> any ScreenOCRRecognizing = { CaptureTestOCR() }) throws {
        source = CaptureTestSource(image: try CaptureProductFixture.image())
        screen = ScreenProbe(source: source, makeOCRJob: makeOCRJob, notificationCenter: notifications)
        capture = CaptureModel(screen: screen)
    }

    func start(using model: ProbeModel, mode: CaptureTranslationMode = .text) async throws {
        capture.startTranslation(using: model, mode: mode)
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
    }

    func select() { capture.select(source.layout[0].frame) }

    func assertReleased(file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(capture.frames.isEmpty, file: file, line: line)
        XCTAssertTrue(screen.frames.isEmpty, file: file, line: line)
        XCTAssertNil(capture.preview, file: file, line: line)
        XCTAssertNil(screen.preview, file: file, line: line)
        XCTAssertNil(screen.selectedRegion, file: file, line: line)
    }

    func flushNotifications() async throws {
        screen.objectWillChange.send()
        try await Task.sleep(nanoseconds: 30_000_000)
    }
}

final class AutomaticCaptureTranslationTests: XCTestCase {
    @MainActor
    func testLongSelectionTimingIsHandedOffOnceWithoutPrewarmOrOCRWaitConfusion() async throws {
        var time = 100.0
        let model = try ProductTestHarness(latencyClock: { time })
        defer { model.cleanUp() }
        let helper = try model.ready(capabilities: ["prewarm"])
        let job = CaptureTestOCR(text: "Private captured sentence.", blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        f.source.automatic = false
        f.capture.startTranslation(using: model.model, mode: .text)
        try await CaptureProductFixture.waitFor { f.source.continuation != nil }
        XCTAssertNil(model.model.latency.current, "Capture/prewarm does not create a translation intent.")
        time = 100.5
        f.source.finishCapture()
        try await CaptureProductFixture.waitFor { f.capture.phase == .selecting }
        time = 120.5
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        f.capture.recognizeSelection()
        XCTAssertNil(model.model.latency.current)
        time = 120.8
        job.gate?.signal()
        try await CaptureProductFixture.waitFor { f.capture.submitted }
        let sample = try XCTUnwrap(model.model.latency.current)
        XCTAssertEqual(sample.started, 100)
        XCTAssertEqual(sample.milliseconds["capture_start_ms"], 0)
        XCTAssertEqual(sample.milliseconds["frames_ready_ms"], 500)
        XCTAssertEqual(sample.milliseconds["selection_ready_ms"], 500)
        XCTAssertEqual(sample.milliseconds["selection_completed_ms"], 20_500)
        XCTAssertEqual(sample.milliseconds["user_selection_ms"], 20_000)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["ocr_processing_ms"]), 300, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(sample.milliseconds["dispatch_ms"]), 20_800, accuracy: 0.001)
        XCTAssertEqual(helper.translations.count, 1)
        f.capture.recognizeSelection()
        try await f.flushNotifications()
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertFalse(model.model.latency.report.contains("Private"))
    }

    @MainActor
    func testImageTimingIncludesPreparationButNeverOCRAndLatePreparationCannotMutateTrace() async throws {
        var time = 100.0
        let model = try ImageAppFixture(latencyClock: { time })
        defer { model.cleanUp() }
        model.factory.hold = true
        let client = try model.ready()
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: { jobs += 1; return CaptureTestOCR() })
        defer { f.capture.cancel() }
        try await f.start(using: model.model, mode: .image)
        time = 121
        f.select()
        try await CaptureProductFixture.waitFor { !model.factory.continuations.isEmpty }
        XCTAssertEqual(model.model.latency.current?.source, .image)
        XCTAssertEqual(model.model.latency.current?.milliseconds["user_selection_ms"], 21_000)
        XCTAssertEqual(model.model.latency.current?.milliseconds["image_attachment_started_ms"], 21_000)
        time = 121.4
        model.factory.finish()
        try await CaptureProductFixture.waitFor { client.imageRequests.count == 1 }
        XCTAssertEqual(try XCTUnwrap(model.model.latency.current?.milliseconds["image_attachment_ready_ms"]), 21_400, accuracy: 0.001)
        XCTAssertEqual(try XCTUnwrap(model.model.latency.current?.milliseconds["image_attachment_processing_ms"]), 400, accuracy: 0.001)
        XCTAssertEqual(jobs, 0)
        XCTAssertNil(model.model.latency.current?.milliseconds["ocr_started_ms"])
        XCTAssertNil(model.model.latency.current?.milliseconds["ocr_processing_ms"])
        model.complete(client, request: try XCTUnwrap(client.imageRequests.last))
        try await CaptureProductFixture.waitFor { model.factory.attachments.first?.removed == true }
        f.capture.cancel()
        try await f.start(using: model.model, mode: .image)
        time = 140
        f.select()
        try await CaptureProductFixture.waitFor { !model.factory.continuations.isEmpty }
        time = 141
        f.capture.cancel()
        let count = model.model.latency.recent.count
        time = 150
        let late = model.factory.finish()
        try await CaptureProductFixture.waitFor { late.removed }
        XCTAssertNil(model.model.latency.current)
        XCTAssertEqual(model.model.latency.recent.count, count)
        XCTAssertEqual(model.model.latency.recent.last?.outcome, .cancelled)
        XCTAssertNil(model.model.latency.recent.last?.milliseconds["image_attachment_ready_ms"])
        XCTAssertEqual(client.imageRequests.count, 1)
    }

    @MainActor
    func testCancelledCaptureIsSeparateAndCannotFinishUnrelatedTranslation() async throws {
        var time = 100.0
        let model = try ProductTestHarness(latencyClock: { time })
        defer { model.cleanUp() }
        let helper = try model.ready()
        let f = try AutomaticCaptureFixture()
        try await f.start(using: model.model)
        time = 120
        model.model.input = "An unrelated private sentence."
        model.model.translate()
        let intent = model.model.translationIntentID
        f.capture.cancel()
        f.capture.cancel()
        XCTAssertEqual(model.model.latency.current?.intent, intent)
        XCTAssertEqual(model.model.latency.recent.count, 1)
        XCTAssertEqual(model.model.latency.recent.last?.source, .capture)
        XCTAssertEqual(model.model.latency.recent.last?.outcome, .cancelled)
        XCTAssertEqual(model.model.latency.recent.last?.milliseconds["user_selection_ms"], 20_000)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
        model.model.cancel()
    }

    @MainActor
    func testFailedCaptureIsRecordedOnceWithoutCreatingOrSubmittingTranslation() async throws {
        let model = try ProductTestHarness(latencyClock: { 100 })
        defer { model.cleanUp() }
        let helper = try model.ready()
        let f = try AutomaticCaptureFixture()
        f.source.permission = false
        f.capture.startTranslation(using: model.model, mode: .text)
        XCTAssertEqual(f.capture.phase, .failed)
        try await f.flushNotifications()
        f.capture.cancel()
        XCTAssertNil(model.model.latency.current)
        XCTAssertEqual(model.model.latency.recent.count, 1)
        XCTAssertEqual(model.model.latency.recent.last?.source, .capture)
        XCTAssertEqual(model.model.latency.recent.last?.outcome, .failed)
        XCTAssertNil(model.model.latency.recent.last?.milliseconds["ocr_started_ms"])
        XCTAssertTrue(helper.translations.isEmpty)
    }

    func testModePersistenceValuesAreStableAndContainOnlyTextAndImage() {
        XCTAssertEqual(CaptureTranslationMode.preferenceKey, "screenshotTranslationMode")
        XCTAssertEqual(CaptureTranslationMode.allCases.map(\.rawValue), ["text", "image"])
        XCTAssertNil(CaptureTranslationMode(rawValue: "unknown"))
    }

    @MainActor
    func testTextRecognizesLocallyThenSubmitsExactTextOnceWithoutPreviewConfirmation() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let recognized = "  Captured words\n保留原文  "
        let job = CaptureTestOCR(text: recognized, blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        XCTAssertTrue(f.capture.automaticallyTranslates)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertTrue(helper.translations.isEmpty)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        XCTAssertEqual(f.capture.phase, .recognizing)
        XCTAssertTrue(helper.translations.isEmpty)
        XCTAssertTrue(job.image === f.screen.selectedRegion?.image)
        job.gate?.signal()
        try await CaptureProductFixture.waitFor { f.capture.submitted }
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, recognized)
        XCTAssertEqual(helper.translations.last?.origin, "ocr")
        XCTAssertEqual(helper.translations.last?.useCache, false)
        XCTAssertEqual(f.capture.submittedIntent, model.model.translationIntentID)
        XCTAssertTrue(helper.dictionaryRequests.isEmpty)
        XCTAssertEqual(f.capture.text, recognized)
        XCTAssertEqual(f.capture.phase, .ready)
        XCTAssertTrue(f.capture.submitting)
        f.assertReleased()
        f.select()
        f.capture.recognizeSelection()
        f.capture.reselect()
        try await f.flushNotifications()
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
        XCTAssertEqual(f.source.requests.count, 1)
        XCTAssertEqual(f.source.permissionCalls, 1)
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id),
                     payload: ImageAppFixture.completion)
        XCTAssertEqual(model.model.output, "Translated image text")
        XCTAssertFalse(f.capture.submitting)
        XCTAssertTrue(f.capture.automaticallyTranslates)
        XCTAssertEqual(f.capture.submittedIntent, model.model.translationIntentID)
        XCTAssertTrue(f.capture.showsTranslationStatus)
        f.capture.cancel()
        XCTAssertEqual(model.model.output, "Translated image text")
    }

    @MainActor
    func testImageSendsOnlySelectedPixelsWithoutCreatingAnyOCRJob() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let client = try model.ready()
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: {
            jobs += 1
            return CaptureTestOCR(fails: true)
        })
        defer { f.capture.cancel() }
        try await f.start(using: model.model, mode: .image)
        XCTAssertTrue(client.imageRequests.isEmpty)
        f.capture.select(CGRect(x: -350, y: -80, width: 100, height: 80))
        XCTAssertTrue(f.capture.submitted)
        XCTAssertTrue(f.capture.submitting)
        f.assertReleased()
        try await CaptureProductFixture.waitFor { client.imageRequests.count == 1 }
        let image = try XCTUnwrap(model.factory.images.first)
        XCTAssertEqual(image.width, 200)
        XCTAssertEqual(image.height, 160)
        XCTAssertFalse(image === f.source.image)
        XCTAssertEqual(jobs, 0)
        XCTAssertTrue(f.capture.text.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertTrue(client.base.dictionaryRequests.isEmpty)
        XCTAssertFalse(client.base.messages.contains { $0.type == "cancel" })
        f.select()
        f.capture.recognizeSelection()
        try await f.flushNotifications()
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertEqual(jobs, 0)
        model.complete(client, request: try XCTUnwrap(client.imageRequests.last))
        try await CaptureProductFixture.waitFor { model.factory.attachments.first?.removed == true }
        XCTAssertEqual(model.model.output, "Translated image text")
        XCTAssertTrue(f.capture.showsTranslationStatus)
    }

    @MainActor
    func testTextPreparationReleasesCaptureAndCancellationPreventsLateConfigurationSubmission() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let f = try AutomaticCaptureFixture()
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { f.capture.submitted }
        let helper = try XCTUnwrap(model.helpers.last)
        XCTAssertTrue(model.model.preparing)
        XCTAssertTrue(f.capture.submitting)
        XCTAssertTrue(helper.translations.isEmpty)
        f.assertReleased()
        f.capture.cancelCurrentAction()
        helper.event("ready")
        try model.finishConfiguration(on: helper)
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertFalse(f.capture.automaticallyTranslates)
        XCTAssertTrue(helper.translations.isEmpty)
    }

    @MainActor
    func testImagePreparationCancellationOwnsAndCleansLateAttachmentWithoutSubmitting() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        model.factory.hold = true
        let f = try AutomaticCaptureFixture()
        try await f.start(using: model.model, mode: .image)
        f.select()
        f.assertReleased()
        try await CaptureProductFixture.waitFor { !model.factory.continuations.isEmpty }
        XCTAssertTrue(f.capture.submitting)
        f.capture.cancelCurrentAction()
        let attachment = model.factory.finish()
        try await CaptureProductFixture.waitFor { attachment.removed }
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertEqual(attachment.cleanupCalls, 1)
        XCTAssertTrue(model.clients.isEmpty)
        XCTAssertEqual(model.runtimeCalls, 0)
    }

    @MainActor
    func testBusyAndPreparingTranslationsRejectBothModesBeforeScreenPermission() async throws {
        for mode in CaptureTranslationMode.allCases {
            for active in [false, true] {
                let model = try ProductTestHarness()
                defer { model.cleanUp() }
                if active { _ = try model.ready() }
                model.model.input = "Unrelated work"
                model.model.translate(origin: "ocr", useCache: false)
                let helper = try XCTUnwrap(model.helpers.last)
                let intent = model.model.translationIntentID
                let requests = helper.translations.count
                let messages = helper.messages.count
                let f = try AutomaticCaptureFixture()
                defer { f.capture.cancel() }
                f.capture.startTranslation(using: model.model, mode: mode)
                try await f.flushNotifications()
                XCTAssertEqual(f.capture.phase, .failed)
                XCTAssertTrue(f.capture.message(using: model.model).contains("current translation"))
                XCTAssertEqual(f.source.permissionCalls, 0)
                XCTAssertTrue(f.source.requests.isEmpty)
                XCTAssertFalse(f.capture.submitted)
                XCTAssertEqual(model.model.translationIntentID, intent)
                XCTAssertEqual(helper.translations.count, requests)
                XCTAssertEqual(helper.messages.count, messages)
                f.assertReleased()
            }
        }
    }

    @MainActor
    func testNewerActiveTranslationCancelsPendingOCRWithoutReplacingUnrelatedRequest() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let job = CaptureTestOCR(blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        model.model.input = "Unrelated work"
        model.model.translate(origin: "ocr", useCache: false)
        let intent = model.model.translationIntentID
        job.gate?.signal()
        try await CaptureProductFixture.waitFor { f.capture.phase == .cancelled }
        XCTAssertFalse(f.capture.submitted)
        f.assertReleased()
        XCTAssertEqual(model.model.translationIntentID, intent)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "Unrelated work")
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id),
                     payload: ImageAppFixture.completion)
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
    }

    @MainActor
    func testNewerTranslationBeforeImageSelectionDoesNotEncodeImageOrFallBackToOCR() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let client = try model.ready()
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: {
            jobs += 1
            return CaptureTestOCR()
        })
        defer { f.capture.cancel() }
        try await f.start(using: model.model, mode: .image)
        model.model.input = "Unrelated work"
        model.model.translate(origin: "ocr", useCache: false)
        let intent = model.model.translationIntentID
        f.select()
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertEqual(jobs, 0)
        XCTAssertTrue(model.factory.images.isEmpty)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(client.base.translations.count, 1)
        XCTAssertEqual(model.model.translationIntentID, intent)
        f.assertReleased()
    }

    @MainActor
    func testNewerCompletedTranslationStillDiscardsPendingOCRAndPreservesResult() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let job = CaptureTestOCR(text: "Older captured text", blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        model.model.input = "Newer manual text"
        model.model.translate(useCache: false)
        let intent = model.model.translationIntentID
        var completion = ImageAppFixture.completion
        completion["text"] = .string("Keep the newer completed result")
        completion["kind"] = .string("text")
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id), payload: completion)
        XCTAssertFalse(model.model.active)
        XCTAssertFalse(model.model.preparing)
        job.gate?.signal()
        await work.value
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertNil(f.capture.submittedIntent)
        XCTAssertEqual(model.model.translationIntentID, intent)
        XCTAssertEqual(model.model.output, "Keep the newer completed result")
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "Newer manual text")
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
        f.assertReleased()
    }

    @MainActor
    func testNewerPreparingTranslationCancelsOnlyLocalOCRAndStillSubmitsAfterConfiguration() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let job = CaptureTestOCR(blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        model.model.input = "Newer queued text"
        model.model.translate(useCache: false)
        let intent = model.model.translationIntentID
        XCTAssertTrue(model.model.preparing)
        try await CaptureProductFixture.waitFor { f.capture.phase == .cancelled }
        XCTAssertTrue(model.model.preparing)
        XCTAssertGreaterThan(job.cancelCount, 0)
        job.gate?.signal()
        await work.value
        let helper = try model.ready()
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertEqual(model.model.translationIntentID, intent)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "Newer queued text")
        XCTAssertFalse(helper.messages.contains { $0.type == "cancel" })
        f.assertReleased()
    }

    @MainActor
    func testPermissionFailureInBothModesDoesNotStartHelperOrOCR() async throws {
        for mode in CaptureTranslationMode.allCases {
            let model = try ImageAppFixture()
            defer { model.cleanUp() }
            var jobs = 0
            let f = try AutomaticCaptureFixture(makeOCRJob: {
                jobs += 1
                return CaptureTestOCR()
            })
            defer { f.capture.cancel() }
            f.source.permission = false
            f.capture.startTranslation(using: model.model, mode: mode)
            try await f.flushNotifications()
            XCTAssertEqual(f.capture.phase, .failed)
            XCTAssertEqual(f.capture.failure, .permissionDenied)
            XCTAssertTrue(f.capture.message(using: model.model).contains("permission"))
            XCTAssertFalse(f.capture.offersCaptureSettings)
            XCTAssertEqual(f.source.permissionCalls, 1)
            XCTAssertEqual(f.source.layoutCalls, 0)
            XCTAssertTrue(f.source.requests.isEmpty)
            XCTAssertEqual(jobs, 0)
            XCTAssertTrue(model.clients.isEmpty)
            XCTAssertTrue(model.factory.images.isEmpty)
            f.assertReleased()
        }
    }

    @MainActor
    func testEmptyAndFailedOCRNeverFallBackToImageOrStartHelper() async throws {
        for fails in [false, true] {
            let model = try ImageAppFixture()
            defer { model.cleanUp() }
            let f = try AutomaticCaptureFixture(makeOCRJob: { CaptureTestOCR(text: " \n\t", fails: fails) })
            defer { f.capture.cancel() }
            try await f.start(using: model.model)
            f.select()
            try await CaptureProductFixture.waitFor {
                f.capture.phase == .empty || f.capture.phase == .failed
            }
            try await f.flushNotifications()
            XCTAssertEqual(f.capture.phase, fails ? .failed : .empty)
            XCTAssertEqual(f.capture.failure, fails ? .ocrFailed : nil)
            XCTAssertTrue(f.capture.offersCaptureSettings)
            XCTAssertFalse(f.capture.submitted)
            XCTAssertNotNil(f.capture.preview)
            XCTAssertEqual(f.capture.frames.count, 1)
            XCTAssertTrue(model.clients.isEmpty)
            XCTAssertTrue(model.factory.images.isEmpty)
            XCTAssertTrue(f.capture.message(using: model.model).contains("Nothing was sent."))
            XCTAssertEqual(f.source.requests.count, 1)
            XCTAssertEqual(f.source.permissionCalls, 1)
            f.capture.cancel()
            XCTAssertFalse(f.capture.offersCaptureSettings)
        }
    }

    @MainActor
    func testOversizedOCRIsRetainedWithoutTruncationSubmissionOrImageFallback() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let original = String(repeating: "字", count: 3000)
        let f = try AutomaticCaptureFixture(makeOCRJob: { CaptureTestOCR(text: original) })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { f.capture.phase == .failed }
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .failed)
        XCTAssertEqual(f.capture.text, original)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertNotNil(model.model.inputIssue(for: original))
        XCTAssertNotNil(f.capture.preview)
        XCTAssertTrue(model.clients.isEmpty)
        XCTAssertTrue(model.factory.images.isEmpty)
    }

    @MainActor
    func testCancelledCaptureInBothModesDiscardsLateFrameAndSelectionCallbacks() async throws {
        for mode in CaptureTranslationMode.allCases {
            let model = try ImageAppFixture()
            defer { model.cleanUp() }
            let f = try AutomaticCaptureFixture()
            f.source.automatic = false
            f.capture.startTranslation(using: model.model, mode: mode)
            try await CaptureProductFixture.waitFor { f.source.continuation != nil }
            let work = try XCTUnwrap(f.screen.captureTask)
            f.capture.cancel()
            f.source.finishCapture()
            await work.value
            f.select()
            f.capture.recognizeSelection()
            try await f.flushNotifications()
            XCTAssertEqual(f.capture.phase, .cancelled)
            XCTAssertFalse(f.capture.submitted)
            XCTAssertTrue(model.clients.isEmpty)
            XCTAssertTrue(model.factory.images.isEmpty)
            f.assertReleased()
        }
    }

    @MainActor
    func testCancelledOCRCannotSubmitItsLateRecognition() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let job = CaptureTestOCR(blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        f.capture.cancel()
        job.gate?.signal()
        await work.value
        try await f.flushNotifications()
        XCTAssertGreaterThan(job.cancelCount, 0)
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertTrue(f.capture.text.isEmpty)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertTrue(model.clients.isEmpty)
        XCTAssertTrue(model.factory.images.isEmpty)
        f.assertReleased()
    }

    @MainActor
    func testRepeatedReselectionDuringOCRSubmitsOnlyLatestRetainedSelectionOnce() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let old = CaptureTestOCR(text: "Obsolete OCR", blocked: true)
        let latest = CaptureTestOCR(text: "Latest OCR")
        defer { old.gate?.signal() }
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: {
            jobs += 1
            return jobs == 1 ? old : latest
        })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { old.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        f.capture.reselect()
        f.capture.select(CGRect(x: -350, y: -80, width: 100, height: 80))
        f.capture.reselect()
        f.capture.select(CGRect(x: -250, y: -80, width: 150, height: 90))
        XCTAssertEqual(jobs, 1)
        XCTAssertTrue(helper.translations.isEmpty)
        old.gate?.signal()
        await work.value
        try await CaptureProductFixture.waitFor { f.capture.submitted }
        try await f.flushNotifications()
        XCTAssertEqual(jobs, 2)
        XCTAssertEqual(helper.translations.count, 1)
        XCTAssertEqual(helper.translations.last?.text, "Latest OCR")
        XCTAssertEqual(latest.image?.width, 300)
        XCTAssertEqual(latest.image?.height, 180)
        XCTAssertEqual(f.source.requests.count, 1)
        f.assertReleased()
    }

    @MainActor
    func testInvalidReselectionDiscardsLateOCRWithoutAnyAutomaticRequest() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let job = CaptureTestOCR(blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        f.capture.select(CGRect(x: -350, y: -80, width: 9, height: 80))
        job.gate?.signal()
        await work.value
        try await f.flushNotifications()
        XCTAssertEqual(f.capture.phase, .failed)
        XCTAssertEqual(f.capture.failure, .invalidSelection)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertTrue(f.capture.text.isEmpty)
        XCTAssertNil(f.capture.preview)
        XCTAssertEqual(f.capture.frames.count, 1)
        XCTAssertTrue(model.clients.isEmpty)
        XCTAssertTrue(model.factory.images.isEmpty)
    }

    @MainActor
    func testDisplayChangeDuringOCRReleasesFramesAndPreventsLateSubmission() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let job = CaptureTestOCR(blocked: true)
        defer { job.gate?.signal() }
        let f = try AutomaticCaptureFixture(makeOCRJob: { job })
        defer { f.capture.cancel() }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { job.image != nil }
        let work = try XCTUnwrap(f.screen.ocrTask)
        f.notifications.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        job.gate?.signal()
        await work.value
        try await CaptureProductFixture.waitFor { f.capture.phase == .failed }
        XCTAssertEqual(f.capture.failure, .layoutChanged)
        XCTAssertFalse(f.capture.submitted)
        XCTAssertTrue(model.clients.isEmpty)
        XCTAssertTrue(model.factory.images.isEmpty)
        f.assertReleased()
    }

    @MainActor
    func testReentrantPermissionStartUsesLatestModeAndNeverStartsOlderOCR() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        model.factory.hold = true
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: {
            jobs += 1
            return CaptureTestOCR()
        })
        defer { f.source.onPermission = nil; f.capture.cancel() }
        f.source.onPermission = {
            f.source.onPermission = nil
            f.capture.startTranslation(using: model.model, mode: .image)
        }
        try await f.start(using: model.model, mode: .text)
        f.select()
        try await CaptureProductFixture.waitFor { !model.factory.continuations.isEmpty }
        XCTAssertEqual(jobs, 0)
        XCTAssertTrue(f.capture.submitted)
        XCTAssertEqual(f.source.permissionCalls, 2)
        XCTAssertEqual(f.source.requests.count, 1)
        f.assertReleased()
        f.capture.cancel()
        let attachment = model.factory.finish()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertTrue(model.clients.isEmpty)
    }

    @MainActor
    func testSynchronousTextInputCancellationNeverSubmitsEvenWhenPreparationFinishesLater() async throws {
        for configured in [false, true] {
            let model = try ProductTestHarness(savedCLI: configured)
            defer { model.cleanUp() }
            let f = try AutomaticCaptureFixture()
            let observation = model.model.$input.dropFirst().sink { _ in f.capture.cancel() }
            defer { observation.cancel(); f.capture.cancel() }
            try await f.start(using: model.model)
            XCTAssertEqual(model.helpers.count, configured ? 1 : 0)
            f.select()
            try await CaptureProductFixture.waitFor { f.capture.phase == .cancelled }
            try await f.flushNotifications()
            if configured {
                let helper = try model.ready(capabilities: ["prewarm"])
                for warm in helper.messages where warm.payload["operation"] == .string("prewarm") {
                    helper.event("completed", id: warm.id, payload: ["warmed": .bool(true)])
                }
                XCTAssertTrue(helper.translations.isEmpty)
                XCTAssertTrue(helper.resultActions.isEmpty)
                XCTAssertFalse(helper.messages.contains { $0.payload["operation"] == .string("translate_image") })
                XCTAssertFalse(helper.dictionaryRequests.contains { $0.request.operation == "dictionary_lookup" })
            }
            XCTAssertEqual(model.helpers.count, configured ? 1 : 0)
            XCTAssertFalse(f.capture.submitted)
            XCTAssertFalse(model.model.preparing)
            XCTAssertFalse(model.model.active)
            f.assertReleased()
        }
    }

    @MainActor
    func testSynchronousTranslationCallbackCannotResurrectCaptureOrCancelNewerIntent() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let f = try AutomaticCaptureFixture()
        defer { model.model.onTranslationStarted = nil; f.capture.cancel() }
        model.model.onTranslationStarted = {
            model.model.onTranslationStarted = nil
            f.capture.cancel()
            model.model.input = "New callback request"
            model.model.translate(origin: "ocr", useCache: false)
        }
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { f.capture.phase == .cancelled }
        let request = try XCTUnwrap(helper.translations.first)
        let intent = model.model.translationIntentID
        XCTAssertFalse(f.capture.submitted)
        XCTAssertNil(f.capture.submittedIntent)
        XCTAssertEqual(helper.translations.count, 1)
        helper.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { helper.translations.count == 2 }
        XCTAssertEqual(model.model.translationIntentID, intent)
        XCTAssertEqual(helper.translations.last?.text, "New callback request")
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertFalse(f.capture.submitted)
        f.assertReleased()
    }

    @MainActor
    func testImagePreparationFailureStaysOwnedWithoutOCRFallbackOrAutomaticRetry() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        model.factory.fails = true
        var jobs = 0
        let f = try AutomaticCaptureFixture(makeOCRJob: {
            jobs += 1
            return CaptureTestOCR()
        })
        defer { f.capture.cancel() }
        try await f.start(using: model.model, mode: .image)
        f.select()
        try await CaptureProductFixture.waitFor { model.model.productPhase == .failed }
        try await f.flushNotifications()
        XCTAssertTrue(f.capture.submitted)
        XCTAssertFalse(f.capture.submitting)
        XCTAssertTrue(f.capture.showsTranslationStatus)
        XCTAssertEqual(jobs, 0)
        XCTAssertEqual(model.factory.images.count, 1)
        XCTAssertTrue(model.clients.isEmpty)
        f.assertReleased()
    }

    @MainActor
    func testImageActiveCancellationAfterFrameReleaseCancelsOnlyOwnedRequest() async throws {
        let model = try ImageAppFixture()
        defer { model.cleanUp() }
        let client = try model.ready()
        let f = try AutomaticCaptureFixture()
        try await f.start(using: model.model, mode: .image)
        f.select()
        try await CaptureProductFixture.waitFor { client.imageRequests.count == 1 }
        let request = try XCTUnwrap(client.imageRequests.last)
        f.assertReleased()
        f.capture.cancelCurrentAction()
        XCTAssertEqual(f.capture.phase, .cancelled)
        XCTAssertEqual(client.base.messages.last?.type, "cancel")
        XCTAssertEqual(client.base.messages.last?.payload["request_id"], .string(request.id))
        XCTAssertEqual(model.factory.attachments.first?.cleanupCalls, 0)
        client.base.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { model.factory.attachments.first?.removed == true }
        XCTAssertEqual(client.imageRequests.count, 1)
    }

    @MainActor
    func testCompletedAutomaticCaptureCannotCancelNewerUnrelatedTranslation() async throws {
        let model = try ProductTestHarness()
        defer { model.cleanUp() }
        let helper = try model.ready()
        let f = try AutomaticCaptureFixture()
        try await f.start(using: model.model)
        f.select()
        try await CaptureProductFixture.waitFor { f.capture.submitted }
        helper.event("completed", id: try XCTUnwrap(helper.translations.last?.id),
                     payload: ImageAppFixture.completion)
        model.model.input = "New unrelated request"
        model.model.translate(origin: "ocr", useCache: false)
        let intent = model.model.translationIntentID
        let messages = helper.messages.count
        try await f.flushNotifications()
        XCTAssertTrue(f.capture.automaticallyTranslates)
        XCTAssertNotNil(f.capture.submittedIntent)
        XCTAssertNotEqual(f.capture.submittedIntent, intent)
        XCTAssertFalse(f.capture.submitting)
        f.capture.cancel()
        XCTAssertEqual(helper.messages.count, messages)
        XCTAssertEqual(model.model.translationIntentID, intent)
        XCTAssertTrue(model.model.active)
        XCTAssertEqual(helper.translations.count, 2)
        XCTAssertEqual(helper.translations.last?.text, "New unrelated request")
    }
}
