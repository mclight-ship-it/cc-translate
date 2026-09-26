import AppKit
import CoreVideo
import ScreenCaptureKit

@MainActor
protocol RegionCaptureSource: AnyObject {
    func requestPermission() -> Bool
    func currentLayout() throws -> [CaptureDisplay]
    func capture(_ request: DisplayCaptureRequest) async throws -> CGImage
}

@MainActor
final class ScreenCaptureKitSource: RegionCaptureSource {
    func requestPermission() -> Bool { Permissions.requestScreenCapture() }

    func currentLayout() throws -> [CaptureDisplay] {
        try NSScreen.screens.map { screen in
            guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
                throw RegionCaptureError.invalidLayout
            }
            let size = screen.convertRectToBacking(CGRect(origin: .zero, size: screen.frame.size)).size
            guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0,
                  size.width < CGFloat(Int.max), size.height < CGFloat(Int.max) else {
                throw RegionCaptureError.invalidLayout
            }
            return CaptureDisplay(id: number.uint32Value, frame: screen.frame,
                                  pixelWidth: Int(size.width.rounded()), pixelHeight: Int(size.height.rounded()),
                                  rotation: CGDisplayRotation(number.uint32Value))
        }
    }

    func capture(_ request: DisplayCaptureRequest) async throws -> CGImage {
        try Task.checkCancellation()
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        try Task.checkCancellation()
        guard let display = content.displays.first(where: { $0.displayID == request.display.id }) else {
            throw RegionCaptureError.layoutChanged
        }
        let pid = ProcessInfo.processInfo.processIdentifier
        let ownApplications = content.applications.filter { $0.processID == pid }
        let filter: SCContentFilter
        if ownApplications.isEmpty {
            filter = SCContentFilter(display: display, excludingWindows: content.windows.filter {
                $0.owningApplication?.processID == pid
            })
        } else {
            filter = SCContentFilter(display: display, excludingApplications: ownApplications, exceptingWindows: [])
        }
        let configuration = SCStreamConfiguration()
        configuration.width = request.width
        configuration.height = request.height
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        configuration.showsCursor = false
        configuration.capturesAudio = false
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
        try Task.checkCancellation()
        return image
    }
}
