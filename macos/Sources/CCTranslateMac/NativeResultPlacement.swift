import AppKit
import SwiftUI

enum NativeResultPlacement: String, CaseIterable {
    case remembered, center, pointer

    static let preferenceKey = "nativeResultPlacement"
    static let frameKey = "nativeResultFrame"

    static func restoredFrame(_ value: String?) -> NSRect? {
        guard let value else { return nil }
        let frame = NSRectFromString(value)
        return usable(frame) ? frame : nil
    }

    private static func usable(_ frame: NSRect) -> Bool {
        [frame.minX, frame.minY, frame.width, frame.height, frame.maxX, frame.maxY].allSatisfy(\.isFinite) &&
            frame.width > 0 && frame.height > 0
    }

    func frame(current: NSRect, remembered: NSRect?, pointer: NSPoint, visibleScreens: [NSRect]) -> NSRect? {
        let screens = visibleScreens.filter(Self.usable)
        guard Self.usable(current), pointer.x.isFinite, pointer.y.isFinite,
              let pointerScreen = screens.min(by: {
                  Self.distanceSquared(pointer, to: $0) < Self.distanceSquared(pointer, to: $1)
              }) else { return nil }
        let previous = remembered.flatMap { Self.usable($0) ? $0 : nil }
        let screen: NSRect
        if self == .remembered, let previous,
           let overlapping = screens.max(by: { Self.overlap(previous, $0) < Self.overlap(previous, $1) }),
           Self.overlap(previous, overlapping) > 0 {
            screen = overlapping
        } else {
            screen = pointerScreen
        }
        let size = NSSize(width: min(current.width, screen.width), height: min(current.height, screen.height))
        let origin: NSPoint
        switch self {
        case .remembered:
            origin = previous?.origin ?? NSPoint(x: screen.midX - size.width / 2, y: screen.midY - size.height / 2)
        case .center:
            origin = NSPoint(x: screen.midX - size.width / 2, y: screen.midY - size.height / 2)
        case .pointer:
            let x = pointer.x + 16 + size.width <= screen.maxX ? pointer.x + 16 : pointer.x - size.width - 16
            let y = pointer.y - size.height - 16 >= screen.minY ? pointer.y - size.height - 16 : pointer.y + 16
            origin = NSPoint(x: x, y: y)
        }
        return NSRect(x: min(max(origin.x, screen.minX), screen.maxX - size.width),
                      y: min(max(origin.y, screen.minY), screen.maxY - size.height),
                      width: size.width, height: size.height)
    }

    private static func overlap(_ first: NSRect, _ second: NSRect) -> CGFloat {
        let intersection = first.intersection(second)
        return intersection.isEmpty ? 0 : intersection.width * intersection.height
    }

    private static func distanceSquared(_ point: NSPoint, to frame: NSRect) -> CGFloat {
        let dx = max(frame.minX - point.x, 0, point.x - frame.maxX)
        let dy = max(frame.minY - point.y, 0, point.y - frame.maxY)
        return dx * dx + dy * dy
    }
}

@MainActor
struct NativeResultPlacementPicker: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        Picker(model.text("Result window position", "结果窗口位置"), selection: $model.resultPlacement) {
            Text(model.text("Remember last position (Default)", "记住上次位置（默认）"))
                .tag(NativeResultPlacement.remembered)
            Text(model.text("Center of screen", "屏幕中央")).tag(NativeResultPlacement.center)
            Text(model.text("Near pointer", "鼠标附近")).tag(NativeResultPlacement.pointer)
        }
        .help(model.text("Applies when a result opens or is recalled. Streaming text does not move the window.",
                         "打开或召回结果时生效，流式输出不会移动窗口。"))
        .onChange(of: model.resultPlacement) { _, _ in model.persistPresentation() }
    }
}
