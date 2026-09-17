import AppKit

public enum RegionCaptureError: String, Error {
    case permissionDenied, noDisplays, invalidLayout, layoutChanged
    case invalidSelection, noSelection, notReady, budgetExceeded
    case captureFailed, compositionFailed, ocrFailed
}

/// Frames are in AppKit global points (bottom-left origin), not Quartz screen coordinates.
public struct CaptureDisplay: Equatable, Sendable {
    public let id: CGDirectDisplayID
    public let frame: CGRect
    public let pixelWidth: Int
    public let pixelHeight: Int
    public let rotation: Double

    public init(id: CGDirectDisplayID, frame: CGRect, pixelWidth: Int, pixelHeight: Int,
                rotation: Double = 0) {
        self.id = id
        self.frame = frame
        self.pixelWidth = pixelWidth
        self.pixelHeight = pixelHeight
        self.rotation = rotation
    }
}

public struct RegionCaptureBudget: Sendable {
    public static let standard = RegionCaptureBudget()
    public let retainedPixels: Int
    public let retainedBytes: Int
    public let compositePixels: Int
    public let maximumDimension: Int

    /// At most 128 MiB of retained BGRA frames and a 64 MiB composite (plus drawing buffers).
    public init(retainedPixels: Int = 32 * 1024 * 1024, retainedBytes: Int = 128 * 1024 * 1024,
                compositePixels: Int = 16 * 1024 * 1024, maximumDimension: Int = 8192) {
        self.retainedPixels = retainedPixels
        self.retainedBytes = retainedBytes
        self.compositePixels = compositePixels
        self.maximumDimension = maximumDimension
    }

    func validate() throws {
        guard retainedPixels > 0, retainedBytes >= 4, compositePixels > 0,
              maximumDimension > 0, maximumDimension <= 32_768,
              retainedPixels <= Int.max / 4, compositePixels <= Int.max / 4 else {
            throw RegionCaptureError.budgetExceeded
        }
    }
}

public struct DisplayCaptureRequest: Sendable {
    public let display: CaptureDisplay
    public let width: Int
    public let height: Int
}

public struct CapturedDisplayFrame {
    public let display: CaptureDisplay
    public let image: CGImage
    /// Monotonic completion time; multiple displays are captured sequentially, not simultaneously.
    public let capturedAt: TimeInterval
    public var pixelSize: CGSize { CGSize(width: CGFloat(image.width), height: CGFloat(image.height)) }
    public var pixelsPerPoint: CGSize {
        CGSize(width: CGFloat(image.width) / display.frame.width,
               height: CGFloat(image.height) / display.frame.height)
    }

    public init(display: CaptureDisplay, image: CGImage, capturedAt: TimeInterval) {
        self.display = display
        self.image = image
        self.capturedAt = capturedAt
    }

    @MainActor
    public var preview: NSImage { NSImage(cgImage: image, size: display.frame.size) }
}

public struct RegionCaptureFragment {
    public let displayID: CGDirectDisplayID
    /// Outward-rounded source pixels, with a top-left image origin.
    public let sourcePixels: CGRect
    /// Bottom-left destination pixels in the composite bitmap context.
    public let destinationPixels: CGRect
}

public struct RegionCaptureSelection {
    public let rect: CGRect
    public let image: CGImage
    public let fragments: [RegionCaptureFragment]
    /// Common scale for every fragment, reported per axis after integer bitmap-size rounding.
    public var pixelsPerPoint: CGSize {
        CGSize(width: CGFloat(image.width) / rect.width, height: CGFloat(image.height) / rect.height)
    }

    @MainActor
    public var preview: NSImage { NSImage(cgImage: image, size: rect.size) }
}

public enum RegionCaptureGeometry {
    public static func selection(from start: CGPoint, to end: CGPoint) throws -> CGRect {
        try normalized(CGRect(x: start.x, y: start.y, width: end.x - start.x, height: end.y - start.y))
    }

    public static func capturePlan(for displays: [CaptureDisplay],
                                   budget: RegionCaptureBudget = .standard) throws -> [DisplayCaptureRequest] {
        try budget.validate()
        try validate(displays)
        let available = min(budget.retainedPixels, budget.retainedBytes / 4)
        guard displays.count <= available else { throw RegionCaptureError.budgetExceeded }
        let pixels = displays.reduce(0.0) { $0 + Double($1.pixelWidth) * Double($1.pixelHeight) }
        let longest = displays.map { max($0.pixelWidth, $0.pixelHeight) }.max() ?? 1
        let scale = min(1, sqrt(Double(available) / pixels),
                        Double(budget.maximumDimension) / Double(longest))
        let sorted = displays.sorted { $0.id < $1.id }
        func makePlan(_ scale: Double) -> [DisplayCaptureRequest] {
            sorted.map {
                DisplayCaptureRequest(display: $0,
                                      width: max(1, Int((Double($0.pixelWidth) * scale).rounded(.down))),
                                      height: max(1, Int((Double($0.pixelHeight) * scale).rounded(.down))))
            }
        }
        func plannedBytes(_ plan: [DisplayCaptureRequest]) -> Double {
            // Allow 256-byte backing-row alignment before asking ScreenCaptureKit to allocate.
            plan.reduce(0) { $0 + Double((($1.width * 4 + 255) / 256) * 256) * Double($1.height) }
        }
        var plan = makePlan(scale)
        if plannedBytes(plan) > Double(budget.retainedBytes) {
            var low = 0.0, high = scale
            for _ in 0..<48 {
                let middle = (low + high) / 2
                if plannedBytes(makePlan(middle)) <= Double(budget.retainedBytes) { low = middle }
                else { high = middle }
            }
            plan = makePlan(low)
        }
        // The minimum one-pixel dimension can otherwise exceed a tiny caller-supplied budget.
        guard plan.reduce(0.0, { $0 + Double($1.width) * Double($1.height) }) <= Double(available),
              plannedBytes(plan) <= Double(budget.retainedBytes) else {
            throw RegionCaptureError.budgetExceeded
        }
        return plan
    }

    public static func layoutMatches(_ captured: [CaptureDisplay], _ current: [CaptureDisplay]) -> Bool {
        captured.sorted { $0.id < $1.id } == current.sorted { $0.id < $1.id }
    }

    public static func validateFrames(_ frames: [CapturedDisplayFrame],
                                      budget: RegionCaptureBudget = .standard) throws {
        try budget.validate()
        try validate(frames.map(\.display))
        var pixels = 0.0, bytes = 0.0
        for frame in frames {
            let image = frame.image
            guard image.width > 0, image.height > 0,
                  image.width <= budget.maximumDimension, image.height <= budget.maximumDimension,
                  image.bitsPerPixel == 32, image.bitsPerComponent == 8 else {
                throw RegionCaptureError.budgetExceeded
            }
            guard frame.pixelsPerPoint.width.isFinite, frame.pixelsPerPoint.height.isFinite else {
                throw RegionCaptureError.invalidLayout
            }
            pixels += Double(image.width) * Double(image.height)
            bytes += Double(image.bytesPerRow) * Double(image.height)
        }
        guard pixels <= Double(budget.retainedPixels), bytes <= Double(budget.retainedBytes) else {
            throw RegionCaptureError.budgetExceeded
        }
    }

    /// No screen access: crop only these retained frames. Gaps between monitors are opaque white.
    public static func compose(_ rectangle: CGRect, from frames: [CapturedDisplayFrame],
                               budget: RegionCaptureBudget = .standard) throws -> RegionCaptureSelection {
        try validateFrames(frames, budget: budget)
        let requested = try normalized(rectangle)
        let intersections = frames.sorted { $0.display.id < $1.display.id }.compactMap { frame
            -> (CapturedDisplayFrame, CGRect)? in
            let intersection = frame.display.frame.intersection(requested)
            return intersection.isNull || intersection.isEmpty ? nil : (frame, intersection)
        }
        guard let first = intersections.first else { throw RegionCaptureError.noSelection }
        let rect = intersections.dropFirst().reduce(first.1) { $0.union($1.1) }
        let preferredScale = intersections.reduce(CGFloat(0)) {
            max($0, $1.0.pixelsPerPoint.width, $1.0.pixelsPerPoint.height)
        }
        let scale = min(preferredScale,
                        sqrt(CGFloat(budget.compositePixels) / rect.width / rect.height),
                        CGFloat(budget.maximumDimension) / max(rect.width, rect.height))
        guard scale.isFinite, scale > 0 else { throw RegionCaptureError.budgetExceeded }
        let width = max(1, Int((rect.width * scale).rounded(.down)))
        let height = max(1, Int((rect.height * scale).rounded(.down)))
        guard Double(width) * Double(height) <= Double(budget.compositePixels),
              width <= budget.maximumDimension, height <= budget.maximumDimension else {
            throw RegionCaptureError.budgetExceeded
        }
        guard let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        ) else { throw RegionCaptureError.compositionFailed }
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        context.interpolationQuality = .high
        let destinationScale = CGSize(width: CGFloat(width) / rect.width, height: CGFloat(height) / rect.height)
        var fragments: [RegionCaptureFragment] = []
        for (frame, intersection) in intersections {
            let display = frame.display.frame
            let sourceScale = frame.pixelsPerPoint
            let x0 = max(0, floor((intersection.minX - display.minX) * sourceScale.width))
            let y0 = max(0, floor((display.maxY - intersection.maxY) * sourceScale.height))
            let x1 = min(CGFloat(frame.image.width), ceil((intersection.maxX - display.minX) * sourceScale.width))
            let y1 = min(CGFloat(frame.image.height), ceil((display.maxY - intersection.minY) * sourceScale.height))
            let source = CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0)
            guard !source.isEmpty, let crop = frame.image.cropping(to: source) else {
                throw RegionCaptureError.compositionFailed
            }
            let left = ((intersection.minX - rect.minX) * destinationScale.width).rounded()
            let bottom = ((intersection.minY - rect.minY) * destinationScale.height).rounded()
            let right = ((intersection.maxX - rect.minX) * destinationScale.width).rounded()
            let top = ((intersection.maxY - rect.minY) * destinationScale.height).rounded()
            let destination = CGRect(x: left, y: bottom, width: right - left, height: top - bottom)
            // Keep fractional-edge coverage without stretching an outward-rounded crop into the selection.
            let covered = CGRect(x: display.minX + x0 / sourceScale.width,
                                 y: display.maxY - y1 / sourceScale.height,
                                 width: source.width / sourceScale.width, height: source.height / sourceScale.height)
            let drawRect = CGRect(x: (covered.minX - rect.minX) * destinationScale.width,
                                  y: (covered.minY - rect.minY) * destinationScale.height,
                                  width: covered.width * destinationScale.width,
                                  height: covered.height * destinationScale.height)
            context.saveGState()
            context.clip(to: destination)
            context.draw(crop, in: drawRect)
            context.restoreGState()
            fragments.append(RegionCaptureFragment(displayID: frame.display.id, sourcePixels: source,
                                                    destinationPixels: destination))
        }
        guard let image = context.makeImage() else { throw RegionCaptureError.compositionFailed }
        return RegionCaptureSelection(rect: rect, image: image, fragments: fragments)
    }

    private static func normalized(_ rect: CGRect) throws -> CGRect {
        guard finite(rect) else { throw RegionCaptureError.invalidSelection }
        let result = rect.standardized
        guard result.width > 0, result.height > 0 else { throw RegionCaptureError.invalidSelection }
        return result
    }

    private static func finite(_ rect: CGRect) -> Bool {
        [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height,
         rect.origin.x + rect.size.width, rect.origin.y + rect.size.height].allSatisfy(\.isFinite)
    }

    private static func validate(_ displays: [CaptureDisplay]) throws {
        guard !displays.isEmpty else { throw RegionCaptureError.noDisplays }
        guard Set(displays.map(\.id)).count == displays.count,
              displays.allSatisfy({
                  finite($0.frame) && $0.frame.width > 0 && $0.frame.height > 0 &&
                  $0.pixelWidth > 0 && $0.pixelHeight > 0 && $0.rotation.isFinite
              }) else { throw RegionCaptureError.invalidLayout }
        let union = displays.dropFirst().reduce(displays[0].frame) { $0.union($1.frame) }
        guard finite(union) else { throw RegionCaptureError.invalidLayout }
    }
}
