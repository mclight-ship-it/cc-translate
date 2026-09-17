import XCTest
import AppKit
import Combine
import ImageIO
import CryptoKit
@testable import CCTranslateMac
@testable import CCTranslateSupport

@MainActor
final class ImageTestAttachment: AppImageAttachment {
    let imagePath = "/synthetic-private-image/\(UUID().uuidString)/region.png"
    let imageBytes = 2048
    let imageSHA256 = String(repeating: "a", count: 64)
    var failsCleanup = false
    var holdCleanup = false
    private(set) var cleanupCalls = 0
    private(set) var removed = false
    var cleanupContinuation: CheckedContinuation<Void, Error>?

    func cleanup() async throws {
        cleanupCalls += 1
        if holdCleanup {
            try await withCheckedThrowingContinuation { cleanupContinuation = $0 }
        }
        if failsCleanup { throw ImageTranslationAttachmentError.cleanupFailed }
        removed = true
    }
    func finishCleanup() {
        let continuation = cleanupContinuation
        cleanupContinuation = nil
        continuation?.resume()
    }
}

@MainActor
final class ImageTestFactory {
    var hold = false
    var fails = false
    var rollbackFailure: AppImageCleanup?
    private(set) var images: [CGImage] = []
    private(set) var attachments: [ImageTestAttachment] = []
    var continuations: [CheckedContinuation<AppImageAttachment, Error>] = []

    func make(_ image: CGImage) async throws -> AppImageAttachment {
        images.append(image)
        if let rollbackFailure { throw AppImageCreationFailure(cleanup: rollbackFailure) }
        if fails { throw ImageTranslationAttachmentError.creationFailed }
        if hold { return try await withCheckedThrowingContinuation { continuations.append($0) } }
        let attachment = ImageTestAttachment()
        attachments.append(attachment)
        return attachment
    }
    @discardableResult
    func finish() -> ImageTestAttachment {
        let attachment = ImageTestAttachment()
        attachments.append(attachment)
        continuations.removeFirst().resume(returning: attachment)
        return attachment
    }

    @discardableResult
    func finishWithCleanupFailure() -> ImageTestAttachment {
        let attachment = ImageTestAttachment()
        attachments.append(attachment)
        continuations.removeFirst().resume(throwing: AppImageCreationFailure(cleanup: attachment))
        return attachment
    }
}

final class ImageTestClient: AppHelperClient {
    let base: ProductTestHelper
    var onImage: (@MainActor (ClientMessage) -> Void)?
    private(set) var imageTimeouts: [TimeInterval] = []
    var imageRequests: [ClientMessage] {
        base.messages.filter { $0.payload["operation"] == .string("translate_image") }
    }
    init(notice: @escaping (HelperNotice) -> Void) { base = ProductTestHelper(notice: notice) }
    func start(runtime: BundleRuntime) { base.start(runtime: runtime) }
    func startConfiguration(runtime: BundleRuntime, home: URL) { base.startConfiguration(runtime: runtime, home: home) }
    func startTranslation(runtime: BundleRuntime, home: URL, codexCommand: URL, environment: [String: String]) {
        base.startTranslation(runtime: runtime, home: home, codexCommand: codexCommand, environment: environment)
    }
    func send(_ message: ClientMessage, timeout: TimeInterval) {
        base.send(message, timeout: timeout)
        if message.payload["operation"] == .string("translate_image") {
            imageTimeouts.append(timeout)
            MainActor.assumeIsolated { onImage?(message) }
        }
    }
    func translate(text: String, appLanguage: String, origin: String, useCache: Bool,
                   recordHistory: Bool, id: String, timeout: TimeInterval) -> String {
        base.translate(text: text, appLanguage: appLanguage, origin: origin, useCache: useCache,
                       recordHistory: recordHistory, id: id, timeout: timeout)
    }
    func resultAction(_ action: ResultAction, text: String, appLanguage: String,
                      targetLanguage: String?, id: String, timeout: TimeInterval) -> String {
        base.resultAction(action, text: text, appLanguage: appLanguage, targetLanguage: targetLanguage,
                          id: id, timeout: timeout)
    }
    func dictionary(_ request: DictionaryRequest, id: String, timeout: TimeInterval) -> String {
        base.dictionary(request, id: id, timeout: timeout)
    }
    func loadConfiguration(id: String, timeout: TimeInterval) -> String { base.loadConfiguration(id: id, timeout: timeout) }
    func saveConfiguration(_ config: [String: JSONValue], id: String, timeout: TimeInterval) -> String {
        base.saveConfiguration(config, id: id, timeout: timeout)
    }
    func loadHistory(pageSize: Int, cursor: JSONValue, query: String, kind: String,
                     id: String, timeout: TimeInterval) -> String {
        base.loadHistory(pageSize: pageSize, cursor: cursor, query: query, kind: kind, id: id, timeout: timeout)
    }
    func clearHistory(id: String, timeout: TimeInterval) -> String { base.clearHistory(id: id, timeout: timeout) }
    func stop() { base.stop() }
}

@MainActor
final class ImageAppFixture {
    let base: ProductTestHarness
    let factory: ImageTestFactory
    let resources: ImageTranslationState
    private(set) var clients: [ImageTestClient] = []
    private(set) var copied: [String] = []
    private(set) var runtimeCalls = 0
    private(set) var locateCalls = 0
    var cliAvailable = true
    var onRuntime: (() -> Void)?
    var onMake: (() -> Void)?
    var model: ProbeModel { base.model }

    init() throws {
        base = try ProductTestHarness(savedCLI: false)
        let factory = ImageTestFactory()
        self.factory = factory
        resources = ImageTranslationState(factory: factory.make)
        base.model = ProbeModel(preferences: base.preferences, makeConnection: { [weak self] notice in
            let client = ImageTestClient(notice: notice)
            self?.clients.append(client)
            self?.onMake?()
            return client
        }, runtimeProvider: { [weak self] in
            guard let self else { throw ProbeError.launchFailed }
            self.runtimeCalls += 1
            self.onRuntime?()
            return self.base.runtime
        }, locateCandidates: { [weak self] _, _ in
            guard let self else { return [] }
            self.locateCalls += 1
            return self.cliAvailable ? [
                CLICandidate(url: self.base.executable, executable: true),
                CLICandidate(url: self.base.alternateExecutable, executable: true)
            ] : []
        }, writeClipboard: { [weak self] in self?.copied.append($0); return true },
           homeDirectory: base.root, imageTranslation: resources)
    }
    @discardableResult
    func ready(config: [String: JSONValue] = ProductTestHarness.configuration(),
               supported: Bool = true) throws -> ImageTestClient {
        if !model.connected { model.openProduct() }
        let client = try XCTUnwrap(clients.last)
        client.base.event("ready", payload: [
            "capabilities": .array(supported ? [.string("translate_image"), .string("model_catalog")] : [])
        ])
        try readConfig(client, config: config)
        return client
    }
    func readConfig(_ client: ImageTestClient,
                    config: [String: JSONValue] = ProductTestHarness.configuration()) throws {
        client.base.event("completed", id: try XCTUnwrap(client.base.configurationLoads.last),
                          payload: ["config": .object(config)])
    }
    func finishSave(_ client: ImageTestClient) throws {
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        client.base.event("completed", id: save.id, payload: [:])
        try readConfig(client, config: save.config)
    }
    @discardableResult
    func send(_ client: ImageTestClient) async throws -> ClientMessage {
        let count = client.imageRequests.count
        model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { client.imageRequests.count > count }
        return try XCTUnwrap(client.imageRequests.last)
    }
    nonisolated static var completion: [String: JSONValue] {
        ["text": .string("Translated image text"), "kind": .string("ocr"), "cached": .bool(false),
         "submitted": .bool(true), "history": .string("disabled"), "history_error": .null,
         "summarize": .bool(false), "target_lang": .null]
    }
    func complete(_ client: ImageTestClient, request: ClientMessage) {
        client.base.event("completed", id: request.id, payload: Self.completion)
    }
    func capture(text: String = "", fails: Bool = false) async throws -> (CaptureModel, ScreenProbe, CaptureTestSource) {
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let screen = ScreenProbe(source: source, makeOCRJob: { CaptureTestOCR(text: text, fails: fails) },
                                 notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        try await CaptureProductFixture.recognize(capture, source: source)
        return (capture, screen, source)
    }
    func cleanUp() {
        onRuntime = nil
        onMake = nil
        clients.forEach { $0.onImage = nil }
        model.prepareToQuit()
        clients.last?.base.stopped()
        while !factory.continuations.isEmpty { factory.finish() }
        factory.attachments.forEach { $0.failsCleanup = false; $0.finishCleanup() }
        resources.retryCleanup()
        base.cleanUp()
    }
}

final class ImageTranslationAppTests: XCTestCase {
    @MainActor
    func testCaptureImageCancelRetainsPreviewAndDoesNotCancelConcurrentLocalOCR() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let source = CaptureTestSource(image: try CaptureProductFixture.image())
        let job = CaptureTestOCR(blocked: true)
        let screen = ScreenProbe(source: source, makeOCRJob: { job }, notificationCenter: NotificationCenter())
        let capture = CaptureModel(screen: screen)
        defer { job.gate?.signal(); capture.cancel() }
        capture.start()
        try await CaptureProductFixture.waitFor { capture.phase == .selecting }
        capture.select(source.layout[0].frame)
        try await CaptureProductFixture.waitFor { capture.phase == .recognizing }
        let preview = capture.preview
        XCTAssertTrue(capture.canTranslateImage)
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        let request = try XCTUnwrap(client.imageRequests.last)
        capture.cancelCurrentAction()
        XCTAssertTrue(capture.preview === preview)
        XCTAssertEqual(capture.phase, .recognizing)
        XCTAssertNotNil(screen.ocrTask)
        XCTAssertEqual(job.cancelCount, 0)
        XCTAssertEqual(client.base.messages.last?.payload["request_id"], .string(request.id))
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 0)
        client.base.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        XCTAssertTrue(capture.preview === preview)
    }

    @MainActor
    func testOldImageConnectionFailureCannotReplaceANewerDisplayedHistoryResult() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        _ = try await f.send(client)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        f.model.reuseHistory(.init(id: "new-history", input: "Saved original", output: "Saved result"))
        let message = f.model.productMessage
        client.base.failure(.translationOutcomeUnknown)
        XCTAssertEqual(f.model.output, "Saved result")
        XCTAssertEqual(f.model.resultInput, "Saved original")
        XCTAssertEqual(f.model.productPhase, .completed)
        XCTAssertEqual(f.model.productMessage, message)
        XCTAssertEqual(attachment.cleanupCalls, 0)
        client.base.stopped()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(f.model.output, "Saved result")
    }

    @MainActor
    func testCancellationFromPublishedImageStatusOccursBeforeAnyTransportRequest() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let observation = f.model.$status.sink { [weak f] status in
            if status == "Image translation requested. No automatic retry." { f?.model.cancel() }
        }
        defer { observation.cancel() }
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.model.productPhase == .cancelled && !f.resources.working }
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertFalse(client.base.messages.contains { $0.type == "cancel" })
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
        XCTAssertFalse(f.model.active)
    }

    @MainActor
    func testReentrantNewTextDuringImagePublicationQueuesUntilOldReservationIsDiscarded() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        var replaced = false
        let observation = f.model.$active.sink { [weak f] active in
            guard let f, active, !replaced else { return }
            replaced = true
            f.model.input = "New explicit text"
            f.model.translate(useCache: false)
        }
        defer { observation.cancel() }
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !client.base.translations.isEmpty && !f.resources.working }
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(client.base.translations.count, 1)
        XCTAssertEqual(client.base.translations.last?.text, "New explicit text")
        XCTAssertEqual(f.model.resultInput, "New explicit text")
        XCTAssertTrue(f.model.resultHasOriginalInput)
        XCTAssertTrue(f.model.active)
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
    }

    @MainActor
    func testPartialCreationRollbackFailureRetainsOnlyCleanupOwnerUntilExplicitRetry() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let owner = ImageTestAttachment()
        f.factory.rollbackFailure = owner
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertFalse(f.resources.working)
        XCTAssertEqual(owner.cleanupCalls, 0, "Support already attempted rollback; the App must wait for explicit retry.")
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(f.runtimeCalls, 0)
        f.resources.retryCleanup()
        try await CaptureProductFixture.waitFor { owner.removed }
        XCTAssertEqual(owner.cleanupCalls, 1)
        XCTAssertEqual(f.resources.cleanupFailureCount, 0)
        XCTAssertTrue(f.clients.isEmpty)
    }

    @MainActor
    func testLateCreationRollbackFailureRemainsVisibleWithoutReplacingNewerResult() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.factory.hold = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        f.model.cancel()
        f.model.reuseHistory(.init(id: "new-result", input: "Different original", output: "Keep this result"))
        let owner = f.factory.finishWithCleanupFailure()
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        XCTAssertEqual(f.model.output, "Keep this result")
        XCTAssertEqual(f.model.resultInput, "Different original")
        XCTAssertEqual(f.model.productPhase, .completed)
        XCTAssertEqual(owner.cleanupCalls, 0)
        XCTAssertTrue(f.clients.isEmpty)
        f.resources.retryCleanup()
        try await CaptureProductFixture.waitFor { owner.removed }
        XCTAssertEqual(f.model.output, "Keep this result")
    }

    @MainActor
    func testQuitRetainsLatePartialCreationOwnerAndAsksBeforeRetryingCleanup() async throws {
        _ = NSApplication.shared
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.factory.hold = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        let app = AppDelegate(model: f.model, capture: CaptureModel(),
                              diagnostics: ProbeModel(persistsPreferences: false))
        var choices = 0
        var replies: [Bool] = []
        app.imageCleanupQuitChoice = { choices += 1; return false }
        app.terminationReply = { replies.append($0) }
        XCTAssertEqual(app.applicationShouldTerminate(NSApp), .terminateLater)
        let owner = f.factory.finishWithCleanupFailure()
        try await CaptureProductFixture.waitFor { !replies.isEmpty }
        XCTAssertEqual(choices, 1)
        XCTAssertEqual(replies, [true])
        XCTAssertTrue(owner.removed)
        XCTAssertEqual(owner.cleanupCalls, 1)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(f.factory.images.count, 1)
    }

    @MainActor
    func testDefaultAndLocalOCRNeverCreateAttachmentsOrStartHelper() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        XCTAssertFalse(f.resources.working)
        XCTAssertEqual(f.runtimeCalls, 0)
        XCTAssertEqual(f.locateCalls, 0)
        let (capture, _, _) = try await f.capture()
        defer { capture.cancel() }
        XCTAssertTrue(capture.canTranslateImage)
        XCTAssertFalse(capture.canTranslate)
        XCTAssertTrue(f.factory.images.isEmpty)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertTrue(f.copied.isEmpty)
        XCTAssertEqual(f.model.permissions, "Not checked.")
    }

    @MainActor
    func testPNGUsesCompositedRegionPixelsNotDisplayOrPreviewPointSize() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let (capture, screen, _) = try await f.capture()
        defer { capture.cancel() }
        capture.select(CGRect(x: -380, y: -80, width: 100, height: 40))
        let selected = try XCTUnwrap(screen.selectedRegion)
        let png = try ImageTranslationPNG.encode(selected.image)
        let source = try XCTUnwrap(CGImageSourceCreateWithData(png as CFData, nil))
        let decoded = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        XCTAssertEqual(decoded.width, 200)
        XCTAssertEqual(decoded.height, 80)
        XCTAssertNotEqual(decoded.width, screen.frames[0].image.width)
        XCTAssertNotEqual(CGFloat(decoded.width), capture.preview?.size.width)
        XCTAssertTrue(f.factory.images.isEmpty)
        XCTAssertTrue(f.clients.isEmpty)
    }

    @MainActor
    func testExplicitBlankOCRUsesImageWireAndLeavesTypedInputAlone() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, screen, _) = try await f.capture()
        defer { capture.cancel() }
        f.model.input = "Unsent editor text"
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        let request = try XCTUnwrap(client.imageRequests.last)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        XCTAssertEqual(Set(request.payload.keys), ["operation", "image_path", "image_bytes",
                                                  "image_sha256", "app_language", "record_history"])
        XCTAssertEqual(request.payload["operation"], .string("translate_image"))
        XCTAssertEqual(request.payload["image_path"], .string(attachment.imagePath))
        XCTAssertEqual(request.payload["image_bytes"], .integer(Int64(attachment.imageBytes)))
        XCTAssertEqual(request.payload["image_sha256"], .string(attachment.imageSHA256))
        XCTAssertEqual(client.imageTimeouts, [110])
        XCTAssertTrue(f.factory.images.last === screen.selectedRegion?.image)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertTrue(client.base.dictionaryRequests.allSatisfy { $0.request.operation != "dictionary_lookup" })
        XCTAssertEqual(f.model.input, "Unsent editor text")
        XCTAssertEqual(f.model.resultInput, "")
        XCTAssertFalse(f.model.resultHasOriginalInput)
        XCTAssertTrue(capture.submitting)
        f.complete(client, request: request)
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(f.model.output, "Translated image text")
        XCTAssertFalse(f.model.output.contains(attachment.imagePath))
    }

    @MainActor
    func testFailedOCRDoesNotAutoSendButImageActionStillWorks() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture(fails: true)
        defer { capture.cancel() }
        XCTAssertEqual(capture.phase, .failed)
        XCTAssertEqual(capture.failure, .ocrFailed)
        XCTAssertTrue(capture.canTranslateImage)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertTrue(f.factory.images.isEmpty)
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { client.imageRequests.count == 1 }
        XCTAssertTrue(capture.showsTranslationStatus)
    }

    @MainActor
    func testImageDraftFreezesExactModelDirectionLanguageBeforeEncoding() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.factory.hold = true
        let frozenID = "fixture/e\u{301}"
        f.model.modelProfile = frozenID
        f.model.direction = "to_zh"
        f.model.interfaceLanguage = "zh"
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        f.model.modelProfile = "fixture/é"
        f.model.direction = "to_en"
        f.model.interfaceLanguage = "en"
        let attachment = f.factory.finish()
        try await CaptureProductFixture.waitFor { !client.base.configurationSaves.isEmpty }
        let save = try XCTUnwrap(client.base.configurationSaves.last)
        XCTAssertEqual(Array(try XCTUnwrap(save.config["codex_model"]?.string).utf8), Array(frozenID.utf8))
        XCTAssertEqual(save.config["direction"], .string("to_zh"))
        XCTAssertEqual(save.config["language"], .string("zh_CN"))
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(attachment.cleanupCalls, 0)
        try f.finishSave(client)
        XCTAssertEqual(client.imageRequests.last?.payload["app_language"], .string("zh_CN"))
        XCTAssertEqual(client.imageRequests.last?.payload["image_path"], .string(attachment.imagePath))
        XCTAssertEqual(f.model.modelProfile, "fixture/é")
    }

    @MainActor
    func testConfigOnlyUpgradeRetainsAttachmentUntilNativeTargetTerminal() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.cliAvailable = false
        let old = try f.ready()
        XCTAssertFalse(f.model.nativeTranslation)
        f.cliAvailable = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { old.base.stopCount == 1 }
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        XCTAssertTrue(old.imageRequests.isEmpty)
        XCTAssertEqual(attachment.cleanupCalls, 0)
        old.base.stopped()
        XCTAssertEqual(attachment.cleanupCalls, 0)
        let current = try f.ready()
        XCTAssertFalse(current === old)
        let request = try XCTUnwrap(current.imageRequests.last)
        XCTAssertEqual(request.payload["image_path"], .string(attachment.imagePath))
        XCTAssertTrue(current.base.configurationSaves.isEmpty)
        XCTAssertTrue(f.model.nativeTranslation)
        f.complete(current, request: request)
        try await CaptureProductFixture.waitFor { attachment.removed }
    }

    @MainActor
    func testMissingCLIShowsSetupAndReleasesImageWithoutTextFallback() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.cliAvailable = false
        let client = try f.ready()
        var setupRequests = 0
        f.model.onConfigurationRequired = { setupRequests += 1 }
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.model.productPhase == .failed && !f.resources.working }
        XCTAssertTrue(f.model.needsCLI)
        XCTAssertEqual(setupRequests, 1)
        XCTAssertTrue(f.model.settingsReady)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertTrue(f.factory.attachments.allSatisfy(\.removed))
    }

    @MainActor
    func testUnavailableCapabilityDoesNotSubstituteTextOrProbeAccounts() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready(supported: false)
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.model.productPhase == .failed && !f.resources.working }
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertTrue(client.base.translations.isEmpty)
        XCTAssertTrue(client.base.configurationSaves.isEmpty)
        XCTAssertTrue(f.model.productMessage.contains("Translate text"))
        XCTAssertEqual(f.model.modelProfile, "auto-fast")
        XCTAssertFalse(f.model.cliBusy)
    }

    @MainActor
    func testSettingsFailureReleasesUnsubmittedImageWithoutReplay() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.direction = "to_en"
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !client.base.configurationSaves.isEmpty }
        client.base.event("failed", id: try XCTUnwrap(client.base.configurationSaves.last?.id),
                          payload: ["code": .string("config_save_failed")])
        try await CaptureProductFixture.waitFor { !f.resources.working }
        XCTAssertEqual(f.model.productPhase, .failed)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
        XCTAssertEqual(client.base.configurationSaves.count, 1)
    }

    @MainActor
    func testCancelBeforeEncoderStartsDoesNoImageOrHelperIO() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.model.translateImage(try CaptureProductFixture.image())
        f.model.cancel()
        try await CaptureProductFixture.waitFor { !f.resources.working }
        XCTAssertTrue(f.factory.images.isEmpty)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertEqual(f.runtimeCalls, 0)
        XCTAssertEqual(f.model.productPhase, .cancelled)
    }

    @MainActor
    func testLateEncodingAfterCancelCleansWithoutOverwritingNewTranslation() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.factory.hold = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        f.model.cancel()
        f.model.input = "New text request"
        f.model.translate(useCache: false)
        let textID = try XCTUnwrap(client.base.translations.last?.id)
        let attachment = f.factory.finish()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(f.model.resultInput, "New text request")
        XCTAssertTrue(f.model.resultHasOriginalInput)
        XCTAssertTrue(f.model.active)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(client.base.translations.last?.id, textID)
    }

    @MainActor
    func testCancelDuringSettingsReadbackCannotResubmitImage() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.modelProfile = "fixture/custom-model"
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !client.base.configurationSaves.isEmpty }
        f.model.cancel()
        try f.finishSave(client)
        try await CaptureProductFixture.waitFor { !f.resources.working }
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
        XCTAssertEqual(f.model.productPhase, .cancelled)
    }

    @MainActor
    func testCancelAckIsNotDrainAndOnlyTargetTerminalRemovesImage() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let request = try await f.send(client)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        f.model.cancel()
        let cancel = try XCTUnwrap(client.base.messages.last)
        XCTAssertEqual(cancel.type, "cancel")
        XCTAssertEqual(cancel.payload["request_id"], .string(request.id))
        client.base.event("cancel_requested", id: cancel.id, payload: ["request_id": .string(request.id)])
        XCTAssertTrue(f.model.active)
        XCTAssertTrue(f.model.productMessage.contains("Cancelling"))
        XCTAssertEqual(attachment.cleanupCalls, 0)
        client.base.event("cancelled", id: request.id, payload: ["submitted": .bool(true)])
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(attachment.cleanupCalls, 1)
        XCTAssertEqual(f.model.productPhase, .cancelled)
    }

    @MainActor
    func testUnknownOutcomeKeepsAttachmentUntilConnectionStoppedWithoutReplay() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        _ = try await f.send(client)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        client.base.failure(.translationOutcomeUnknown)
        XCTAssertEqual(attachment.cleanupCalls, 0)
        XCTAssertEqual(f.model.productPhase, .failed)
        client.base.stopped()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertEqual(f.clients.count, 1)
    }

    @MainActor
    func testTerminalWhileStoppingCleansButLateOutputStaysHidden() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let request = try await f.send(client)
        f.model.closePanel()
        f.complete(client, request: request)
        try await CaptureProductFixture.waitFor { !f.resources.working }
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
        XCTAssertEqual(f.model.output, "")
        client.base.stopped()
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
    }

    @MainActor
    func testReselectCancelsOnlyCaptureOwnedImageIntent() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture()
        defer { capture.cancel() }
        capture.translateImage(using: f.model)
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        let request = try XCTUnwrap(client.imageRequests.last)
        capture.reselect()
        XCTAssertEqual(client.base.messages.last?.payload["request_id"], .string(request.id))
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 0)
        client.base.event("cancelled", id: request.id, payload: ["submitted": .bool(false)])
        f.model.input = "Unrelated translation"
        f.model.translate(useCache: false)
        let cancellations = client.base.messages.filter { $0.type == "cancel" }.count
        capture.cancel()
        XCTAssertEqual(client.base.messages.filter { $0.type == "cancel" }.count, cancellations)
        XCTAssertTrue(f.model.active)
    }

    @MainActor
    func testBusyImageClickDoesNotEncodeOrCancelActiveTextTranslation() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let (capture, _, _) = try await f.capture()
        defer { capture.cancel() }
        f.model.input = "Active ordinary request"
        f.model.translate(useCache: false)
        let message = f.model.productMessage
        capture.translateImage(using: f.model)
        XCTAssertTrue(f.factory.images.isEmpty)
        XCTAssertTrue(client.imageRequests.isEmpty)
        XCTAssertFalse(client.base.messages.contains { $0.type == "cancel" })
        XCTAssertEqual(f.model.productMessage, message)
    }

    @MainActor
    func testLiveCommittedHistoryOptOutAppliesBeforeImageSubmission() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.factory.hold = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        f.model.saveSettings(history: false)
        try f.finishSave(client)
        f.factory.finish()
        try await CaptureProductFixture.waitFor { !client.imageRequests.isEmpty }
        XCTAssertEqual(client.imageRequests.last?.payload["record_history"], .bool(false))
        XCTAssertFalse(f.model.historyEnabled)
    }

    @MainActor
    func testImageResultsBlockOriginalActionsButPermitOutputActionsAndCopy() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let request = try await f.send(client)
        f.complete(client, request: request)
        for action in [ResultAction.explainCode, .asText, .retranslate] {
            f.model.performResultAction(action, targetLanguage: action == .retranslate ? "zh" : nil)
        }
        f.model.copyBilingual()
        XCTAssertTrue(client.base.resultActions.isEmpty)
        XCTAssertTrue(f.copied.isEmpty)
        f.model.copyResult()
        XCTAssertEqual(f.copied, ["Translated image text"])
        f.model.performResultAction(.concise)
        XCTAssertEqual(client.base.resultActions.last?.text, "Translated image text")
        XCTAssertEqual(f.model.resultInput, "")
        XCTAssertFalse(f.model.resultHasOriginalInput)
    }

    @MainActor
    func testOutputOnlyHistoryDecodesAndOpensWithoutFabricatedOriginal() throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let entry: JSONValue = .object([
            "input": .null, "output": .string("Saved image translation"), "kind": .string("ocr"),
            "ts": .string("2026-01-01T12:00:00Z")
        ])
        let row = try ProbeModel.HistoryRow.decode(entry, id: "image-history")
        XCTAssertFalse(row.hasOriginalInput)
        XCTAssertEqual(row.input, "")
        f.model.input = "Keep my draft"
        f.model.reuseHistory(row)
        XCTAssertEqual(f.model.input, "Keep my draft")
        XCTAssertEqual(f.model.output, "Saved image translation")
        XCTAssertEqual(f.model.resultInput, "")
        XCTAssertFalse(f.model.resultHasOriginalInput)
        XCTAssertTrue(f.clients.isEmpty)
    }

    @MainActor
    func testCatalogCompletionCannotChangeCurrentImageOrSavedSelection() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.refreshModels()
        let catalog = try XCTUnwrap(client.base.messages.last)
        XCTAssertEqual(catalog.payload["operation"], .string("model_catalog"))
        let request = try await f.send(client)
        client.base.event("completed", id: catalog.id, payload: CatalogAppFixture.payload())
        XCTAssertEqual(client.imageRequests.last?.id, request.id)
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertTrue(f.model.active)
        XCTAssertEqual(f.model.modelProfile, "auto-fast")
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 0)
        XCTAssertFalse(client.base.messages.contains { $0.type == "cancel" })
    }

    @MainActor
    func testOldConnectionDrainCannotDeleteNewPendingImage() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready()
        let oldRequest = try await f.send(old)
        let oldAttachment = try XCTUnwrap(f.factory.attachments.last)
        old.base.failure(.translationOutcomeUnknown)
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.factory.attachments.count == 2 }
        let currentAttachment = try XCTUnwrap(f.factory.attachments.last)
        try await CaptureProductFixture.waitFor { old.base.stopCount > 0 }
        old.base.stopped()
        let current = try f.ready()
        let currentRequest = try XCTUnwrap(current.imageRequests.last)
        XCTAssertEqual(currentRequest.payload["image_path"], .string(currentAttachment.imagePath))
        try await CaptureProductFixture.waitFor { oldAttachment.removed }
        XCTAssertEqual(currentAttachment.cleanupCalls, 0)
        old.base.event("completed", id: oldRequest.id, payload: ImageAppFixture.completion)
        XCTAssertEqual(f.model.output, "")
        XCTAssertTrue(f.model.active)
        XCTAssertEqual(currentAttachment.cleanupCalls, 0)
    }

    @MainActor
    func testChangingCLIStopsImageAndLateTerminalCannotOverwriteNewScope() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let old = try f.ready()
        let request = try await f.send(old)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        f.model.selectedCLI = f.base.alternateExecutable.path
        XCTAssertEqual(old.base.stopCount, 1)
        XCTAssertEqual(attachment.cleanupCalls, 0)
        old.base.stopped()
        let current = try f.ready()
        old.base.event("completed", id: request.id, payload: ImageAppFixture.completion)
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(f.model.output, "")
        XCTAssertTrue(current.imageRequests.isEmpty)
    }

    @MainActor
    func testReentrantStartCallbackCancelsExactlyOneAlreadyRegisteredImageRequest() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        f.model.onTranslationStarted = { [weak f] in f?.model.cancel() }
        let request = try await f.send(client)
        XCTAssertEqual(client.imageRequests.count, 1)
        let cancels = client.base.messages.filter { $0.type == "cancel" }
        XCTAssertEqual(cancels.count, 1)
        XCTAssertEqual(cancels.first?.payload["request_id"], .string(request.id))
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 0)
    }

    @MainActor
    func testReentrantImageCompletionCannotLoseAttachmentOrDuplicateSend() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        client.onImage = { [weak client] request in
            client?.base.event("completed", id: request.id, payload: ImageAppFixture.completion)
        }
        _ = try await f.send(client)
        try await CaptureProductFixture.waitFor { !f.resources.working }
        XCTAssertEqual(f.model.output, "Translated image text")
        XCTAssertEqual(f.model.productPhase, .completed)
        XCTAssertEqual(client.imageRequests.count, 1)
        XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
    }

    @MainActor
    func testRuntimeAndClientFactoryCancellationCannotStartImageHelper() async throws {
        for duringRuntime in [true, false] {
            let f = try ImageAppFixture()
            defer { f.cleanUp() }
            if duringRuntime { f.onRuntime = { [weak f] in f?.model.cancel() } }
            else { f.onMake = { [weak f] in f?.model.cancel() } }
            f.model.translateImage(try CaptureProductFixture.image())
            try await CaptureProductFixture.waitFor { f.model.productPhase == .cancelled && !f.resources.working }
            XCTAssertTrue(f.clients.allSatisfy { $0.base.operations.isEmpty })
            XCTAssertFalse(f.model.connected)
            XCTAssertEqual(f.factory.attachments.last?.cleanupCalls, 1)
        }
    }

    @MainActor
    func testCleanupFailureIsRetainedVisibleAndOnlyExplicitlyRetried() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let request = try await f.send(client)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        attachment.failsCleanup = true
        f.complete(client, request: request)
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        XCTAssertFalse(f.resources.working)
        XCTAssertFalse(attachment.removed)
        client.base.stopped()
        XCTAssertEqual(attachment.cleanupCalls, 1)
        XCTAssertEqual(f.model.output, "Translated image text")
        attachment.failsCleanup = false
        f.resources.retryCleanup()
        try await CaptureProductFixture.waitFor { attachment.removed }
        XCTAssertEqual(f.resources.cleanupFailureCount, 0)
        XCTAssertEqual(attachment.cleanupCalls, 2)
        XCTAssertEqual(client.imageRequests.count, 1)
    }

    @MainActor
    func testEncodingFailureDoesNotPrepareHelperAndReportsFailure() async throws {
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.factory.fails = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { f.model.productPhase == .failed }
        XCTAssertFalse(f.resources.working)
        XCTAssertTrue(f.clients.isEmpty)
        XCTAssertTrue(f.factory.attachments.isEmpty)
        XCTAssertTrue(f.model.productMessage.contains("Nothing was sent"))
    }

    @MainActor
    func testShutdownWaitsForLateCreationAndRejectsFurtherImageRequests() async throws {
        _ = NSApplication.shared
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        f.factory.hold = true
        f.model.translateImage(try CaptureProductFixture.image())
        try await CaptureProductFixture.waitFor { !f.factory.continuations.isEmpty }
        let app = AppDelegate(model: f.model, capture: CaptureModel(),
                              diagnostics: ProbeModel(persistsPreferences: false))
        var replies: [Bool] = []
        app.terminationReply = { replies.append($0) }
        XCTAssertEqual(app.applicationShouldTerminate(NSApp), .terminateLater)
        XCTAssertTrue(f.model.hasProcesses)
        let attachment = f.factory.finish()
        try await CaptureProductFixture.waitFor { !replies.isEmpty }
        XCTAssertTrue(attachment.removed)
        XCTAssertEqual(replies, [true])
        XCTAssertNil(f.model.translateImage(try CaptureProductFixture.image()))
        XCTAssertTrue(f.clients.isEmpty)
    }

    @MainActor
    func testQuitOffersExplicitRetryForImageCleanupFailureWithoutModelReplay() async throws {
        _ = NSApplication.shared
        let f = try ImageAppFixture()
        defer { f.cleanUp() }
        let client = try f.ready()
        let request = try await f.send(client)
        let attachment = try XCTUnwrap(f.factory.attachments.last)
        attachment.failsCleanup = true
        f.complete(client, request: request)
        try await CaptureProductFixture.waitFor { f.resources.cleanupFailureCount == 1 }
        let app = AppDelegate(model: f.model, capture: CaptureModel(),
                              diagnostics: ProbeModel(persistsPreferences: false))
        var choices = 0
        var replies: [Bool] = []
        app.imageCleanupQuitChoice = {
            choices += 1
            attachment.failsCleanup = false
            return false
        }
        app.terminationReply = { replies.append($0) }
        XCTAssertEqual(app.applicationShouldTerminate(NSApp), .terminateLater)
        client.base.stopped()
        try await CaptureProductFixture.waitFor { !replies.isEmpty }
        XCTAssertEqual(choices, 1)
        XCTAssertEqual(replies, [true])
        XCTAssertTrue(attachment.removed)
        XCTAssertEqual(attachment.cleanupCalls, 2)
        XCTAssertEqual(client.imageRequests.count, 1)
    }

    @MainActor
    func testNativeAdapterWritesOnlySyntheticPNGAndExplicitCleanupIsIdempotent() async throws {
        let image = try CaptureProductFixture.image()
        let attachment = try await NativeImageAttachment.make(image)
        let url = URL(fileURLWithPath: attachment.imagePath)
        let data = try Data(contentsOf: url)
        let expected = try ImageTranslationPNG.encode(image)
        XCTAssertEqual(data, expected)
        XCTAssertEqual(data.count, attachment.imageBytes)
        XCTAssertEqual(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(), attachment.imageSHA256)
        try await attachment.cleanup()
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        try await attachment.cleanup()
    }
}
