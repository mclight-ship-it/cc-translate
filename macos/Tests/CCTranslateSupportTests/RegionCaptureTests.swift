import XCTest
import AppKit
import CoreText
@testable import CCTranslateSupport

private enum CaptureFixture {
    static let red: [UInt8] = [255, 0, 0, 255]
    static let green: [UInt8] = [0, 255, 0, 255]
    static let blue: [UInt8] = [0, 0, 255, 255]
    static let yellow: [UInt8] = [255, 255, 0, 255]
    static let white: [UInt8] = [255, 255, 255, 255]

    // Raw rows deliberately use the image's top-left origin, independent of CGContext drawing transforms.
    static func image(width: Int = 8, height: Int = 8, color: [UInt8]? = nil,
                      rowBytes: Int? = nil) throws -> CGImage {
        let stride = rowBytes ?? width * 4
        var bytes = [UInt8](repeating: 0, count: stride * height)
        for y in 0..<height {
            for x in 0..<width {
                let pixel = color ?? (y < height / 2 ? (x < width / 2 ? red : green)
                                       : (x < width / 2 ? blue : yellow))
                for channel in 0..<4 { bytes[y * stride + x * 4 + channel] = pixel[channel] }
            }
        }
        let provider = try XCTUnwrap(CGDataProvider(data: Data(bytes) as CFData))
        return try XCTUnwrap(CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: stride,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue |
                                    CGBitmapInfo.byteOrder32Big.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent
        ))
    }

    static func pixel(_ image: CGImage, x: Int, y: Int) throws -> [UInt8] {
        let provider = try XCTUnwrap(image.dataProvider)
        let data = try XCTUnwrap(provider.data)
        let bytes = try XCTUnwrap(CFDataGetBytePtr(data))
        return (0..<4).map { bytes[y * image.bytesPerRow + x * 4 + $0] }
    }

    static func display(_ id: UInt32 = 1, _ frame: CGRect = CGRect(x: 0, y: 0, width: 4, height: 4),
                        pixels: Int = 8) -> CaptureDisplay {
        CaptureDisplay(id: id, frame: frame, pixelWidth: pixels, pixelHeight: pixels)
    }

    static func retained(_ display: CaptureDisplay, _ image: CGImage) -> CapturedDisplayFrame {
        CapturedDisplayFrame(display: display, image: image, capturedAt: 123)
    }
}

final class RegionCaptureGeometryTests: XCTestCase {
    func testReverseDragsNormalizeAllDirectionsIncludingNegativeOrigins() throws {
        for (start, end) in [
            (CGPoint(x: -8, y: -5), CGPoint(x: 3, y: 7)),
            (CGPoint(x: 3, y: 7), CGPoint(x: -8, y: -5)),
            (CGPoint(x: -8, y: 7), CGPoint(x: 3, y: -5)),
            (CGPoint(x: 3, y: -5), CGPoint(x: -8, y: 7))
        ] {
            XCTAssertEqual(try RegionCaptureGeometry.selection(from: start, to: end),
                           CGRect(x: -8, y: -5, width: 11, height: 12))
        }
    }

    func testActualImageCornerCropTableAcrossNegativeAndVerticalLayouts() throws {
        let image = try CaptureFixture.image()
        let corners: [(CGFloat, CGFloat, [UInt8])] = [
            (0, 2, CaptureFixture.red), (2, 2, CaptureFixture.green),
            (0, 0, CaptureFixture.blue), (2, 0, CaptureFixture.yellow)
        ]
        for origin in [CGPoint.zero, CGPoint(x: -6, y: 8), CGPoint(x: 3, y: -9)] {
            let display = CaptureFixture.display(1, CGRect(origin: origin, size: CGSize(width: 4, height: 4)))
            for (x, y, color) in corners {
                let selection = try RegionCaptureGeometry.compose(
                    CGRect(x: origin.x + x, y: origin.y + y, width: 2, height: 2),
                    from: [CaptureFixture.retained(display, image)]
                )
                XCTAssertEqual(selection.image.width, 4)
                XCTAssertEqual(selection.image.height, 4)
                for row in 0..<4 {
                    for column in 0..<4 {
                        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: column, y: row), color)
                    }
                }
            }
        }
    }

    func testFullImageCompositionDoesNotInvertOrReflectPixels() throws {
        let display = CaptureFixture.display()
        let image = try CaptureFixture.image()
        let selection = try RegionCaptureGeometry.compose(display.frame, from: [CaptureFixture.retained(display, image)])
        for y in 0..<8 {
            for x in 0..<8 {
                XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: x, y: y),
                               try CaptureFixture.pixel(image, x: x, y: y))
            }
        }
    }

    func testFractionalCropRoundsSourceOutwardsAndUsesActualRetainedScale() throws {
        let display = CaptureFixture.display(pixels: 80)
        let selection = try RegionCaptureGeometry.compose(
            CGRect(x: 0.25, y: 1.25, width: 1.5, height: 1.5),
            from: [CaptureFixture.retained(display, try CaptureFixture.image())]
        )
        XCTAssertEqual(selection.fragments.first?.sourcePixels, CGRect(x: 0, y: 2, width: 4, height: 4))
        XCTAssertEqual(selection.image.width, 3)
        XCTAssertEqual(selection.image.height, 3)
        XCTAssertEqual(selection.pixelsPerPoint, CGSize(width: 2, height: 2))
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 1, y: 0), CaptureFixture.red)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 1, y: 2), CaptureFixture.blue)
    }

    func testSelectionEndingAtExactDisplayBoundaryDoesNotIncludeNextDisplay() throws {
        let left = CaptureFixture.display()
        let right = CaptureFixture.display(2, CGRect(x: 4, y: 0, width: 4, height: 4))
        let selection = try RegionCaptureGeometry.compose(left.frame, from: [
            CaptureFixture.retained(left, try CaptureFixture.image()),
            CaptureFixture.retained(right, try CaptureFixture.image(color: CaptureFixture.blue))
        ])
        XCTAssertEqual(selection.fragments.map(\.displayID), [1])
        XCTAssertEqual(selection.fragments.first?.sourcePixels, CGRect(x: 0, y: 0, width: 8, height: 8))
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 7, y: 0), CaptureFixture.green)
    }

    func testNonUniformActualPixelAxesAreMappedWithoutAssumingRetina() throws {
        let display = CaptureFixture.display()
        let image = try CaptureFixture.image(width: 12, height: 8)
        let selection = try RegionCaptureGeometry.compose(
            CGRect(x: 2, y: 2, width: 2, height: 2), from: [CaptureFixture.retained(display, image)]
        )
        XCTAssertEqual(selection.fragments.first?.sourcePixels, CGRect(x: 6, y: 0, width: 6, height: 4))
        XCTAssertEqual(selection.pixelsPerPoint, CGSize(width: 3, height: 3))
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 3, y: 3), CaptureFixture.green)
    }

    func testMixedScaleHorizontalStitchUsesCommonScaleWithNoSeam() throws {
        let left = CaptureFixture.display(1, CGRect(x: -4, y: 0, width: 4, height: 4), pixels: 4)
        let right = CaptureFixture.display(2)
        let selection = try RegionCaptureGeometry.compose(
            CGRect(x: -4, y: 0, width: 8, height: 4),
            from: [CaptureFixture.retained(right, try CaptureFixture.image()),
                   CaptureFixture.retained(left, try CaptureFixture.image(width: 4, height: 4))]
        )
        XCTAssertEqual(selection.image.width, 16)
        XCTAssertEqual(selection.image.height, 8)
        XCTAssertEqual(selection.pixelsPerPoint, CGSize(width: 2, height: 2))
        XCTAssertEqual(selection.fragments.map(\.destinationPixels),
                       [CGRect(x: 0, y: 0, width: 8, height: 8), CGRect(x: 8, y: 0, width: 8, height: 8)])
        for x in [1, 9] {
            XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: x, y: 1), CaptureFixture.red)
            XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: x, y: 6), CaptureFixture.blue)
        }
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 7, y: 0), CaptureFixture.green)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 8, y: 0), CaptureFixture.red)
    }

    func testAboveAndBelowMonitorsComposeInAppKitGlobalOrientation() throws {
        let frames = try [
            (-4, CaptureFixture.blue), (0, CaptureFixture.red), (4, CaptureFixture.green)
        ].enumerated().map { index, item in
            CaptureFixture.retained(
                CaptureFixture.display(UInt32(index + 1), CGRect(x: -2, y: CGFloat(item.0), width: 4, height: 4)),
                try CaptureFixture.image(color: item.1)
            )
        }
        let selection = try RegionCaptureGeometry.compose(CGRect(x: -2, y: -4, width: 4, height: 12), from: frames)
        XCTAssertEqual(selection.image.height, 24)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 4, y: 1), CaptureFixture.green)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 4, y: 12), CaptureFixture.red)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 4, y: 22), CaptureFixture.blue)
    }

    func testCrossScreenCompositionCropsEachIntersectionBeforeStitching() throws {
        let frames = try [0, 4].enumerated().map { index, x in
            CaptureFixture.retained(
                CaptureFixture.display(UInt32(index + 1), CGRect(x: CGFloat(x), y: 0, width: 4, height: 4)),
                try CaptureFixture.image()
            )
        }
        let selection = try RegionCaptureGeometry.compose(CGRect(x: 3, y: 2, width: 2, height: 2), from: frames)
        XCTAssertEqual(selection.fragments.map(\.sourcePixels),
                       [CGRect(x: 6, y: 0, width: 2, height: 4), CGRect(x: 0, y: 0, width: 2, height: 4)])
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 0, y: 0), CaptureFixture.green)
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 3, y: 0), CaptureFixture.red)
    }

    func testMonitorGapIsWhiteAndOffscreenMarginIsClipped() throws {
        let frames = try [0, 8].enumerated().map { index, x in
            CaptureFixture.retained(
                CaptureFixture.display(UInt32(index + 1), CGRect(x: CGFloat(x), y: 0, width: 4, height: 4)),
                try CaptureFixture.image(color: CaptureFixture.blue)
            )
        }
        let selection = try RegionCaptureGeometry.compose(CGRect(x: -5, y: -2, width: 22, height: 8), from: frames)
        XCTAssertEqual(selection.rect, CGRect(x: 0, y: 0, width: 12, height: 4))
        XCTAssertEqual(try CaptureFixture.pixel(selection.image, x: 12, y: 4), CaptureFixture.white)
        XCTAssertThrowsError(try RegionCaptureGeometry.compose(CGRect(x: 5, y: 0, width: 2, height: 2), from: frames)) {
            XCTAssertEqual($0 as? RegionCaptureError, .noSelection)
        }
    }

    func testNonFiniteZeroAndOffscreenSelectionsAreExplicitErrors() throws {
        let frames = [CaptureFixture.retained(CaptureFixture.display(), try CaptureFixture.image())]
        for rect in [CGRect.zero, CGRect(x: CGFloat.nan, y: 0, width: 1, height: 1),
                     CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 1)] {
            XCTAssertThrowsError(try RegionCaptureGeometry.compose(rect, from: frames)) {
                XCTAssertEqual($0 as? RegionCaptureError, .invalidSelection)
            }
        }
        XCTAssertThrowsError(try RegionCaptureGeometry.compose(CGRect(x: 10, y: 10, width: 2, height: 2), from: frames)) {
            XCTAssertEqual($0 as? RegionCaptureError, .noSelection)
        }
    }

    func testCommonDual5KCaptureFitsWithoutDownscaling() throws {
        let displays = (0..<2).map {
            CaptureDisplay(id: UInt32($0 + 1), frame: CGRect(x: CGFloat($0 * 2560), y: 0, width: 2560, height: 1440),
                           pixelWidth: 5120, pixelHeight: 2880)
        }
        let plan = try RegionCaptureGeometry.capturePlan(for: displays)
        XCTAssertEqual(plan.map(\.width), [5120, 5120])
        XCTAssertEqual(plan.map(\.height), [2880, 2880])
    }

    func testCaptureBudgetDownscalesAllDisplaysUniformlyBeforeAllocation() throws {
        let displays = [
            CaptureDisplay(id: 1, frame: CGRect(x: 0, y: 0, width: 3000, height: 2000), pixelWidth: 6000, pixelHeight: 4000),
            CaptureDisplay(id: 2, frame: CGRect(x: 3000, y: 0, width: 2000, height: 1000), pixelWidth: 4000, pixelHeight: 2000)
        ]
        let budget = RegionCaptureBudget(retainedPixels: 8_000_000, retainedBytes: 33_000_000)
        let plan = try RegionCaptureGeometry.capturePlan(for: displays, budget: budget)
        XCTAssertEqual(plan.map(\.width), [3000, 2000])
        XCTAssertEqual(plan.map(\.height), [2000, 1000])
        XCTAssertEqual(plan.reduce(0) { $0 + $1.width * $1.height }, 8_000_000)
    }

    func testPanoramicCaptureAndCompositeRespectDimensionAndPixelBudgets() throws {
        let display = CaptureDisplay(id: 1, frame: CGRect(x: -1000, y: 0, width: 20000, height: 100),
                                     pixelWidth: 40000, pixelHeight: 200)
        let plan = try RegionCaptureGeometry.capturePlan(for: [display])
        XCTAssertEqual(plan.first?.width, RegionCaptureBudget.standard.maximumDimension)
        let frames = [CaptureFixture.retained(CaptureFixture.display(), try CaptureFixture.image())]
        let selection = try RegionCaptureGeometry.compose(
            frames[0].display.frame, from: frames,
            budget: RegionCaptureBudget(compositePixels: 16, maximumDimension: 8)
        )
        XCTAssertEqual(selection.image.width, 4)
        XCTAssertEqual(selection.image.height, 4)
    }

    func testActualRetainedStrideCountsTowardByteBudget() throws {
        let image = try CaptureFixture.image(width: 1, height: 2, rowBytes: 64)
        XCTAssertThrowsError(try RegionCaptureGeometry.validateFrames(
            [CaptureFixture.retained(CaptureFixture.display(), image)],
            budget: RegionCaptureBudget(retainedPixels: 4, retainedBytes: 16)
        )) { XCTAssertEqual($0 as? RegionCaptureError, .budgetExceeded) }
    }

    func testPreCaptureByteBudgetReservesAlignedRowsForMultipleHighResolutionDisplays() throws {
        let displays = (0..<3).map {
            CaptureDisplay(id: UInt32($0 + 1), frame: CGRect(x: CGFloat($0 * 3000), y: 0, width: 3000, height: 2000),
                           pixelWidth: 6000, pixelHeight: 4000)
        }
        let budget = RegionCaptureBudget.standard
        let plan = try RegionCaptureGeometry.capturePlan(for: displays, budget: budget)
        let bytes = plan.reduce(0) { $0 + (($1.width * 4 + 255) / 256) * 256 * $1.height }
        XCTAssertLessThanOrEqual(bytes, budget.retainedBytes)
        XCTAssertLessThanOrEqual(plan.reduce(0) { $0 + $1.width * $1.height }, budget.retainedPixels)
        XCTAssertEqual(Set(plan.map(\.width)).count, 1)
        XCTAssertLessThan(try XCTUnwrap(plan.first).width, 6000)
    }

    func testInvalidBudgetOrDisplayLayoutIsRejectedBeforeCapture() {
        for budget in [RegionCaptureBudget(retainedPixels: 0), RegionCaptureBudget(retainedBytes: 3),
                       RegionCaptureBudget(compositePixels: 0), RegionCaptureBudget(maximumDimension: Int.max)] {
            XCTAssertThrowsError(try RegionCaptureGeometry.capturePlan(for: [CaptureFixture.display()], budget: budget))
        }
        for displays in [[], [CaptureFixture.display(), CaptureFixture.display()],
                         [CaptureFixture.display(1, CGRect(x: 0, y: 0, width: -4, height: 4))],
                         [CaptureFixture.display(1, CGRect(x: 0, y: 0, width: 4, height: -4))],
                         [CaptureFixture.display(pixels: 0)]] {
            XCTAssertThrowsError(try RegionCaptureGeometry.capturePlan(for: displays))
        }
    }

    func testLayoutIdentityIncludesUnplugPositionScaleAndRotationButNotOrder() {
        let first = CaptureFixture.display()
        let second = CaptureFixture.display(2, CGRect(x: -4, y: 0, width: 4, height: 4))
        XCTAssertTrue(RegionCaptureGeometry.layoutMatches([first, second], [second, first]))
        for layout in [
            [first],
            [first, CaptureFixture.display(2, CGRect(x: 0, y: 4, width: 4, height: 4))],
            [first, CaptureFixture.display(2, second.frame, pixels: 4)],
            [first, CaptureDisplay(id: 2, frame: second.frame, pixelWidth: 8, pixelHeight: 8, rotation: 90)]
        ] {
            XCTAssertFalse(RegionCaptureGeometry.layoutMatches([first, second], layout))
        }
    }

    func testVisionRecognizesCompositedRetainedImageWithExistingLanguagePolicy() throws {
        let context = try XCTUnwrap(CGContext(
            data: nil, width: 1000, height: 180, bitsPerComponent: 8, bytesPerRow: 4000,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 1000, height: 180))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 60, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)
        ]
        context.textPosition = CGPoint(x: 24, y: 65)
        CTLineDraw(CTLineCreateWithAttributedString(
            NSAttributedString(string: "RETAINED FRAME 12345", attributes: attributes) as CFAttributedString
        ), context)
        let display = CaptureDisplay(id: 1, frame: CGRect(x: -500, y: -90, width: 500, height: 90),
                                     pixelWidth: 1000, pixelHeight: 180)
        let retained = CaptureFixture.retained(display, try XCTUnwrap(context.makeImage()))
        context.setFillColor(CGColor(gray: 0, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 1000, height: 180))
        let selection = try RegionCaptureGeometry.compose(display.frame, from: [retained])
        let result = try LocalOCR.recognize(selection.image)
        XCTAssertTrue(result.text.uppercased().contains("RETAINED"), result.text)
        XCTAssertTrue(result.text.contains("12345"), result.text)
        XCTAssertEqual(result.selectedLanguages,
                       ["en-US", "zh-Hans", "zh-Hant"].filter { result.supportedLanguages.contains($0) })
    }

    func testVisionBlankImageReturnsEmptyTextWithoutFailure() throws {
        let result = try LocalOCR.recognize(CaptureFixture.image(width: 320, height: 120, color: CaptureFixture.white))
        XCTAssertEqual(result.text, "")
        XCTAssertFalse(result.supportedLanguages.isEmpty)
    }

    func testExistingOCRJobCancellationBeforeRecognitionIsRespected() throws {
        let job = OCRJob()
        job.cancel()
        XCTAssertThrowsError(try job.recognize(CaptureFixture.image())) { XCTAssertTrue($0 is CancellationError) }
    }
}

@MainActor
private final class FixtureCaptureSource: RegionCaptureSource {
    var layout = [CaptureFixture.display()]
    var permissionGranted = true
    var permissionCalls = 0
    var onPermission: (() -> Void)?
    var layoutCalls = 0
    var requests: [DisplayCaptureRequest] = []
    var automatic = true
    var images: [UInt32: CGImage] = [:]
    var onCapture: (() -> Void)?
    private var pending: [CheckedContinuation<CGImage, Error>] = []

    func requestPermission() -> Bool {
        permissionCalls += 1
        let granted = permissionGranted
        onPermission?()
        return granted
    }

    func currentLayout() throws -> [CaptureDisplay] {
        layoutCalls += 1
        return layout
    }

    func capture(_ request: DisplayCaptureRequest) async throws -> CGImage {
        requests.append(request)
        if automatic {
            onCapture?()
            guard let image = images[request.display.id] else { throw RegionCaptureError.captureFailed }
            return image
        }
        return try await withCheckedThrowingContinuation { continuation in
            pending.append(continuation)
            onCapture?()
        }
    }

    func finish(_ result: Result<CGImage, Error>, index: Int = 0) {
        pending.remove(at: index).resume(with: result)
    }
}

private final class GateOCRJob: ScreenOCRRecognizing, @unchecked Sendable {
    private let condition = NSCondition()
    private var released = false
    private var cancellations = 0
    private var image: CGImage?
    let started: XCTestExpectation
    let outcome: Result<OCRResult, Error>

    init(started: XCTestExpectation, outcome: Result<OCRResult, Error> = .success(
        OCRResult(text: "RETAINED", supportedLanguages: ["en-US"], selectedLanguages: ["en-US"])
    )) {
        self.started = started
        self.outcome = outcome
    }

    func recognize(_ image: CGImage) throws -> OCRResult {
        condition.lock()
        self.image = image
        started.fulfill()
        while !released { condition.wait() }
        condition.unlock()
        // Deliberately completes even after cancellation, to exercise the caller's stale-result gate.
        return try outcome.get()
    }

    func cancel() {
        condition.lock()
        cancellations += 1
        condition.unlock()
    }

    func release() {
        condition.lock()
        released = true
        condition.broadcast()
        condition.unlock()
    }

    var cancellationCount: Int {
        condition.lock()
        defer { condition.unlock() }
        return cancellations
    }

    var recognizedImage: CGImage? {
        condition.lock()
        defer { condition.unlock() }
        return image
    }
}

final class RegionCaptureLifecycleTests: XCTestCase {
    @MainActor
    private func fixture(budget: RegionCaptureBudget = .standard,
                         job: GateOCRJob? = nil) throws -> (ScreenProbe, FixtureCaptureSource) {
        let source = FixtureCaptureSource()
        source.images[1] = try CaptureFixture.image()
        let probe = ScreenProbe(source: source, budget: budget, makeOCRJob: {
            if let job { return job }
            return OCRJob()
        }, notificationCenter: NotificationCenter())
        return (probe, source)
    }

    @MainActor
    private func capture(_ probe: ScreenProbe) async throws {
        probe.beginRegionCapture()
        let task = try XCTUnwrap(probe.captureTask)
        await task.value
        XCTAssertEqual(probe.phase, .selecting)
    }

    @MainActor
    func testConstructionClearAndUnselectedOCRHaveNoCaptureOrPermissionActivity() throws {
        let (probe, source) = try fixture()
        probe.clear()
        probe.confirmOCR()
        XCTAssertThrowsError(try probe.selectRegion(CGRect(x: 0, y: 0, width: 1, height: 1)))
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(source.requests.isEmpty)
        XCTAssertNil(probe.preview)
        XCTAssertFalse(probe.busy)
    }

    @MainActor
    func testExplicitBeginRetainsEveryDisplayWithoutSelectingOrInvokingOCR() async throws {
        let (probe, source) = try fixture()
        source.layout.append(CaptureFixture.display(2, CGRect(x: -4, y: 4, width: 4, height: 4)))
        source.images[2] = try CaptureFixture.image(color: CaptureFixture.blue)
        try await capture(probe)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.map { $0.display.id }, [1, 2])
        XCTAssertEqual(probe.frames.map { $0.display.id }, [1, 2])
        XCTAssertEqual(probe.frames.map(\.pixelSize), [CGSize(width: 8, height: 8), CGSize(width: 8, height: 8)])
        XCTAssertLessThanOrEqual(probe.frames[0].capturedAt, probe.frames[1].capturedAt)
        XCTAssertNil(probe.selectedRegion)
        XCTAssertNil(probe.preview)
        XCTAssertFalse(probe.canConfirm)
        XCTAssertNil(probe.ocrTask)
    }

    @MainActor
    func testPermissionDenialDoesNotEnumerateOrCapture() throws {
        let (probe, source) = try fixture()
        source.permissionGranted = false
        probe.beginRegionCapture()
        XCTAssertEqual(probe.lastError, .permissionDenied)
        XCTAssertEqual(probe.phase, .failed)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.layoutCalls, 0)
        XCTAssertTrue(source.requests.isEmpty)
    }

    @MainActor
    func testPermissionReturnCannotRestartOrFailCancelledCapture() throws {
        for granted in [true, false] {
            for diagnostic in [true, false] {
                let (probe, source) = try fixture()
                source.permissionGranted = granted
                source.onPermission = { probe.cancel() }
                defer { source.onPermission = nil }
                if diagnostic { probe.grantAndCaptureOnce() }
                else { probe.beginRegionCapture() }
                XCTAssertEqual(probe.phase, .idle)
                XCTAssertNil(probe.lastError)
                XCTAssertNil(probe.captureTask)
                XCTAssertFalse(probe.busy)
                XCTAssertTrue(probe.frames.isEmpty)
                XCTAssertEqual(source.permissionCalls, 1)
                XCTAssertEqual(source.layoutCalls, 0)
                XCTAssertTrue(source.requests.isEmpty)
            }
        }
    }

    @MainActor
    func testStalePermissionReturnCannotReplaceNewerExplicitCapture() async throws {
        for oldGranted in [true, false] {
            let (probe, source) = try fixture()
            defer {
                source.onPermission = nil
                probe.cancel()
            }
            source.permissionGranted = oldGranted
            source.onPermission = {
                source.onPermission = nil
                source.permissionGranted = true
                probe.beginRegionCapture()
            }
            probe.beginRegionCapture()
            XCTAssertEqual(probe.phase, .capturing)
            let task = try XCTUnwrap(probe.captureTask)
            await task.value
            XCTAssertEqual(probe.phase, .selecting)
            XCTAssertNil(probe.lastError)
            XCTAssertFalse(probe.busy)
            XCTAssertEqual(probe.frames.count, 1)
            XCTAssertEqual(source.permissionCalls, 2)
            XCTAssertEqual(source.requests.count, 1)
        }
    }

    @MainActor
    func testDiagnosticCaptureKeepsSingleDisplayPreviewAndExplicitConfirmation() async throws {
        let (probe, source) = try fixture()
        source.layout.append(CaptureFixture.display(2, CGRect(x: -4, y: 0, width: 4, height: 4)))
        source.images[2] = try CaptureFixture.image()
        probe.grantAndCaptureOnce()
        let task = try XCTUnwrap(probe.captureTask)
        await task.value
        XCTAssertEqual(probe.phase, .preview)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(probe.frames.count, 1)
        XCTAssertTrue(probe.canConfirm)
        XCTAssertNotNil(probe.preview)
        XCTAssertTrue(probe.text.isEmpty)
        XCTAssertNil(probe.ocrTask)
    }

    @MainActor
    func testDiagnosticKeepsIts4096PixelLimitWithoutRestrictingProductCapture() async throws {
        let (probe, source) = try fixture()
        source.layout = [CaptureDisplay(id: 1, frame: CGRect(x: 0, y: 0, width: 3000, height: 2000),
                                       pixelWidth: 6000, pixelHeight: 4000)]
        source.images[1] = try CaptureFixture.image(width: 12, height: 8)
        probe.grantAndCaptureOnce()
        let diagnostic = try XCTUnwrap(probe.captureTask)
        await diagnostic.value
        XCTAssertEqual(probe.phase, .preview)
        XCTAssertEqual(source.requests.first?.width, 4096)
        XCTAssertEqual(source.requests.first?.height, 2730)
        try await capture(probe)
        XCTAssertEqual(source.requests.count, 2)
        XCTAssertEqual(source.requests.last?.width, 6000)
        XCTAssertEqual(source.requests.last?.height, 4000)
        XCTAssertNil(probe.ocrTask)
    }

    @MainActor
    func testOCRUsesSelectedRetainedImageAndAllowsEditingWithoutRecapture() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        let selected = try probe.selectRegion(CGRect(x: 0, y: 2, width: 2, height: 2))
        source.images[1] = try CaptureFixture.image(color: CaptureFixture.blue)
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        XCTAssertTrue(job.recognizedImage === selected.image)
        job.release()
        await task.value
        XCTAssertEqual(probe.text, "RETAINED")
        XCTAssertEqual(probe.selectedOCRLanguages, ["en-US"])
        probe.text = "user-edited local text"
        XCTAssertEqual(probe.text, "user-edited local text")
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(probe.phase, .recognized)
    }

    @MainActor
    func testClearIgnoresLateCaptureAndBoundsDrainingWorkBeforeExplicitRetry() async throws {
        let (probe, source) = try fixture()
        source.automatic = false
        let oldStarted = expectation(description: "old capture entered")
        source.onCapture = { oldStarted.fulfill() }
        probe.beginRegionCapture()
        let oldTask = try XCTUnwrap(probe.captureTask)
        await fulfillment(of: [oldStarted], timeout: 5)
        probe.cancel()
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertNil(probe.selectedRegion)
        XCTAssertEqual(probe.phase, .idle)

        probe.beginRegionCapture()
        XCTAssertEqual(probe.lastError, .notReady)
        XCTAssertEqual(source.requests.count, 1)
        source.finish(.success(try CaptureFixture.image(color: CaptureFixture.red)))
        await oldTask.value
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertEqual(probe.lastError, .notReady)
        XCTAssertEqual(source.requests.count, 1)

        let newStarted = expectation(description: "new capture entered")
        source.onCapture = { newStarted.fulfill() }
        probe.beginRegionCapture()
        let newTask = try XCTUnwrap(probe.captureTask)
        await fulfillment(of: [newStarted], timeout: 5)
        let newImage = try CaptureFixture.image(color: CaptureFixture.blue)
        source.finish(.success(newImage))
        await newTask.value
        XCTAssertTrue(probe.frames.first?.image === newImage)
        XCTAssertEqual(probe.phase, .selecting)
        XCTAssertEqual(source.requests.count, 2)
    }

    @MainActor
    func testClearCancelsVisionAndDiscardsLateCompletionAndAllOwnedFrames() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, _) = try fixture(job: job)
        try await capture(probe)
        try probe.selectRegion(CGRect(x: 0, y: 0, width: 4, height: 4))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        probe.clear()
        XCTAssertGreaterThan(job.cancellationCount, 0)
        XCTAssertNil(probe.preview)
        XCTAssertNil(probe.selectedRegion)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertFalse(probe.busy)
        job.release()
        await task.value
        XCTAssertEqual(probe.phase, .idle)
        XCTAssertEqual(probe.text, "")
        XCTAssertNil(probe.preview)
    }

    @MainActor
    func testReselectionCancelsOldOCRAndCannotPublishItsTextIntoNewPreview() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        try probe.selectRegion(CGRect(x: 0, y: 2, width: 2, height: 2))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        let newSelection = try probe.selectRegion(CGRect(x: 0, y: 0, width: 2, height: 2))
        job.release()
        await task.value
        XCTAssertTrue(probe.selectedRegion?.image === newSelection.image)
        XCTAssertEqual(probe.text, "")
        XCTAssertEqual(probe.phase, .preview)
        XCTAssertTrue(probe.canConfirm)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testPrepareSelectionCancelsOCRButKeepsFramesAndDoesNotRecognizeAgain() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        let retained = try XCTUnwrap(probe.frames.first?.image)
        try probe.selectRegion(CGRect(x: 0, y: 0, width: 4, height: 4))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        try probe.prepareRegionSelection()
        XCTAssertEqual(probe.phase, .selecting)
        XCTAssertFalse(probe.busy)
        XCTAssertFalse(probe.canConfirm)
        XCTAssertNil(probe.preview)
        XCTAssertNil(probe.selectedRegion)
        XCTAssertEqual(probe.text, "")
        XCTAssertTrue(probe.frames.first?.image === retained)
        XCTAssertGreaterThan(job.cancellationCount, 0)
        job.release()
        await task.value
        XCTAssertEqual(probe.phase, .selecting)
        XCTAssertEqual(probe.text, "")
        XCTAssertNil(probe.ocrTask)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testConfirmedReselectionWaitsForOldOCRThenRecognizesOnlyLatestImageOnce() async throws {
        for oldFails in [false, true] {
            let oldStarted = expectation(description: "old OCR entered \(oldFails)")
            let newStarted = expectation(description: "new OCR entered \(oldFails)")
            let oldJob = GateOCRJob(started: oldStarted, outcome: oldFails
                ? .failure(RegionCaptureError.ocrFailed)
                : .success(OCRResult(text: "OLD", supportedLanguages: [], selectedLanguages: [])))
            let newJob = GateOCRJob(started: newStarted, outcome: .success(
                OCRResult(text: "LATEST", supportedLanguages: ["en-US"], selectedLanguages: ["en-US"])
            ))
            defer {
                oldJob.release()
                newJob.release()
            }
            let source = FixtureCaptureSource()
            source.images[1] = try CaptureFixture.image()
            var jobCount = 0
            let probe = ScreenProbe(source: source, makeOCRJob: {
                jobCount += 1
                return jobCount == 1 ? oldJob : newJob
            }, notificationCenter: NotificationCenter())
            try await capture(probe)
            try probe.selectRegion(CGRect(x: 0, y: 2, width: 2, height: 2))
            probe.confirmOCR()
            let oldTask = try XCTUnwrap(probe.ocrTask)
            await fulfillment(of: [oldStarted], timeout: 5)

            try probe.prepareRegionSelection()
            try probe.selectRegion(CGRect(x: 2, y: 2, width: 2, height: 2))
            probe.confirmOCR()
            XCTAssertEqual(jobCount, 1)
            XCTAssertEqual(probe.phase, .recognizing)
            XCTAssertTrue(probe.busy)
            XCTAssertFalse(probe.canConfirm)
            // A newer selection replaces the single waiting intent; neither intermediate intent is replayed.
            let latest = try probe.selectRegion(CGRect(x: 0, y: 0, width: 2, height: 2))
            probe.confirmOCR()
            probe.confirmOCR()
            XCTAssertEqual(jobCount, 1)
            XCTAssertNil(newJob.recognizedImage)
            oldJob.release()
            await oldTask.value
            let newTask = try XCTUnwrap(probe.ocrTask)
            await fulfillment(of: [newStarted], timeout: 5)
            XCTAssertEqual(jobCount, 2)
            XCTAssertTrue(newJob.recognizedImage === latest.image)
            XCTAssertEqual(probe.text, "")
            newJob.release()
            await newTask.value
            XCTAssertEqual(probe.phase, .recognized)
            XCTAssertEqual(probe.text, "LATEST")
            XCTAssertEqual(jobCount, 2)
            XCTAssertEqual(source.permissionCalls, 1)
            XCTAssertEqual(source.requests.count, 1)
        }
    }

    @MainActor
    private func assertWaitingOCRDiscarded(
        phase: ScreenCapturePhase, error: RegionCaptureError? = nil,
        action: (ScreenProbe, FixtureCaptureSource) throws -> Void
    ) async throws {
        let started = expectation(description: "old OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let source = FixtureCaptureSource()
        source.images[1] = try CaptureFixture.image()
        var jobCount = 0
        let probe = ScreenProbe(source: source, makeOCRJob: {
            jobCount += 1
            return job
        }, notificationCenter: NotificationCenter())
        try await capture(probe)
        try probe.selectRegion(CGRect(x: 0, y: 2, width: 2, height: 2))
        probe.confirmOCR()
        let oldTask = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        try probe.selectRegion(CGRect(x: 0, y: 0, width: 2, height: 2))
        probe.confirmOCR()
        XCTAssertEqual(jobCount, 1)
        XCTAssertEqual(probe.phase, .recognizing)
        try action(probe, source)
        job.release()
        await oldTask.value
        XCTAssertEqual(jobCount, 1)
        XCTAssertEqual(probe.phase, phase)
        XCTAssertEqual(probe.lastError, error)
        XCTAssertEqual(probe.text, "")
        XCTAssertNil(probe.ocrTask)
        XCTAssertEqual(source.requests.count, 1)
        XCTAssertEqual(source.permissionCalls, 1)
    }

    @MainActor
    func testClearDiscardsConfirmedOCRWaitingForOldJobDrain() async throws {
        try await assertWaitingOCRDiscarded(phase: .idle) { probe, _ in
            probe.clear()
            XCTAssertTrue(probe.frames.isEmpty)
            XCTAssertNil(probe.preview)
        }
    }

    @MainActor
    func testReturningToSelectionDiscardsConfirmedWaitingOCRWithoutReleasingFrames() async throws {
        try await assertWaitingOCRDiscarded(phase: .selecting) { probe, _ in
            try probe.prepareRegionSelection()
            XCTAssertFalse(probe.frames.isEmpty)
            XCTAssertNil(probe.preview)
        }
    }

    @MainActor
    func testUnconfirmedNewSelectionDoesNotReplayPreviousWaitingOCR() async throws {
        try await assertWaitingOCRDiscarded(phase: .preview) { probe, _ in
            try probe.selectRegion(CGRect(x: 2, y: 0, width: 2, height: 2))
            XCTAssertTrue(probe.canConfirm)
        }
    }

    @MainActor
    func testWaitingOCRRevalidatesLayoutBeforeCreatingAnyNewVisionJob() async throws {
        try await assertWaitingOCRDiscarded(phase: .failed, error: .layoutChanged) { _, source in
            source.layout = []
        }
    }

    @MainActor
    func testPrepareSelectionWithoutCaptureOrAfterLayoutChangeHasNoCaptureSideEffects() async throws {
        let (probe, source) = try fixture()
        XCTAssertThrowsError(try probe.prepareRegionSelection()) {
            XCTAssertEqual($0 as? RegionCaptureError, .notReady)
        }
        XCTAssertEqual(source.permissionCalls, 0)
        XCTAssertEqual(source.layoutCalls, 0)
        try await capture(probe)
        source.layout = []
        XCTAssertThrowsError(try probe.prepareRegionSelection()) {
            XCTAssertEqual($0 as? RegionCaptureError, .layoutChanged)
        }
        XCTAssertEqual(probe.phase, .failed)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testUnplugDuringCaptureInvalidatesInsteadOfCapturingRemainingDisplays() async throws {
        let (probe, source) = try fixture()
        source.layout.append(CaptureFixture.display(2, CGRect(x: 4, y: 0, width: 4, height: 4)))
        source.automatic = false
        let started = expectation(description: "capture entered")
        source.onCapture = { started.fulfill() }
        probe.beginRegionCapture()
        let task = try XCTUnwrap(probe.captureTask)
        await fulfillment(of: [started], timeout: 5)
        source.layout.removeLast()
        source.finish(.success(try CaptureFixture.image()))
        await task.value
        XCTAssertEqual(probe.lastError, .layoutChanged)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertNil(probe.preview)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testLayoutChangeAtSelectionRejectsFramesWithoutPermissionOrRecapture() async throws {
        let (probe, source) = try fixture()
        try await capture(probe)
        source.layout = [CaptureFixture.display(1, CGRect(x: 1, y: 0, width: 4, height: 4))]
        XCTAssertThrowsError(try probe.selectRegion(CGRect(x: 1, y: 0, width: 2, height: 2))) {
            XCTAssertEqual($0 as? RegionCaptureError, .layoutChanged)
        }
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertEqual(probe.lastError, .layoutChanged)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testLayoutChangeDuringOCRRejectsEvenSuccessfulLocalText() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        try probe.selectRegion(CGRect(x: 0, y: 0, width: 4, height: 4))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        source.layout = []
        job.release()
        await task.value
        XCTAssertEqual(probe.lastError, .layoutChanged)
        XCTAssertTrue(probe.text.isEmpty)
        XCTAssertTrue(probe.frames.isEmpty)
    }

    @MainActor
    func testLayoutNotificationsInvalidateActiveTransactionAndUnregisterOnClear() async throws {
        let source = FixtureCaptureSource()
        source.images[1] = try CaptureFixture.image()
        let center = NotificationCenter()
        let probe = ScreenProbe(source: source, notificationCenter: center)
        try await capture(probe)
        source.layout = []
        center.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        XCTAssertEqual(probe.lastError, .layoutChanged)
        XCTAssertTrue(probe.frames.isEmpty)
        probe.clear()
        center.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        XCTAssertEqual(probe.phase, .idle)
        XCTAssertNil(probe.lastError)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testScreenChangeNotificationInvalidatesEvenAfterOriginalLayoutIsRestored() async throws {
        let source = FixtureCaptureSource()
        source.images[1] = try CaptureFixture.image()
        let center = NotificationCenter()
        let probe = ScreenProbe(source: source, notificationCenter: center)
        try await capture(probe)
        center.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        XCTAssertEqual(probe.lastError, .layoutChanged)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertEqual(source.requests.count, 1)
    }

    @MainActor
    func testCancelledOCRMustDrainBeforeAnotherCaptureCanAllocateFrames() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started)
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        try probe.selectRegion(CGRect(x: 0, y: 0, width: 4, height: 4))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        probe.beginRegionCapture()
        XCTAssertEqual(probe.lastError, .notReady)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertEqual(source.permissionCalls, 1)
        XCTAssertEqual(source.requests.count, 1)
        job.release()
        await task.value
        XCTAssertEqual(probe.text, "")
        XCTAssertEqual(probe.lastError, .notReady)
        try await capture(probe)
        XCTAssertEqual(source.requests.count, 2)
    }

    @MainActor
    func testOversizedActualCaptureFailsClosedEvenIfRequestedSizeWasWithinBudget() async throws {
        let (probe, source) = try fixture(budget: RegionCaptureBudget(retainedPixels: 4, retainedBytes: 1024))
        probe.beginRegionCapture()
        let task = try XCTUnwrap(probe.captureTask)
        await task.value
        XCTAssertEqual(source.requests.first?.width, 2)
        XCTAssertEqual(probe.lastError, .budgetExceeded)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertNil(probe.preview)
    }

    @MainActor
    func testPartialCaptureFailureDoesNotPublishOrRetainEarlierDisplay() async throws {
        let (probe, source) = try fixture()
        source.layout.append(CaptureFixture.display(2, CGRect(x: 4, y: 0, width: 4, height: 4)))
        probe.beginRegionCapture()
        let task = try XCTUnwrap(probe.captureTask)
        await task.value
        XCTAssertEqual(source.requests.count, 2)
        XCTAssertEqual(probe.lastError, .captureFailed)
        XCTAssertTrue(probe.frames.isEmpty)
        XCTAssertNil(probe.selectedRegion)
    }

    @MainActor
    func testVisionFailureKeepsPreviewForExplicitRetryWithoutAutomaticWork() async throws {
        let started = expectation(description: "OCR entered")
        let job = GateOCRJob(started: started, outcome: .failure(RegionCaptureError.ocrFailed))
        defer { job.release() }
        let (probe, source) = try fixture(job: job)
        try await capture(probe)
        let selection = try probe.selectRegion(CGRect(x: 0, y: 0, width: 4, height: 4))
        probe.confirmOCR()
        let task = try XCTUnwrap(probe.ocrTask)
        await fulfillment(of: [started], timeout: 5)
        job.release()
        await task.value
        XCTAssertEqual(probe.lastError, .ocrFailed)
        XCTAssertTrue(probe.canConfirm)
        XCTAssertEqual(probe.phase, .preview)
        XCTAssertTrue(probe.selectedRegion?.image === selection.image)
        XCTAssertNil(probe.ocrTask)
        XCTAssertEqual(source.requests.count, 1)
    }
}
