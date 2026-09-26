import XCTest
import AppKit
@testable import CCTranslateMac

final class RegionSelectionTests: XCTestCase {
    func testReverseDragUsesGlobalCoordinatesIncludingNegativeOrigins() {
        for sendsIntermediateEvent in [false, true] {
            var state = RegionSelectionState()
            state.mouseDown(at: CGPoint(x: 200, y: 90))
            if sendsIntermediateEvent { state.mouseDragged(to: CGPoint(x: -450, y: -210)) }
            XCTAssertEqual(state.mouseUp(at: CGPoint(x: -450, y: -210)),
                           .selected(CGRect(x: -450, y: -210, width: 650, height: 300)))
        }
    }

    func testTwoClicksSelectWithoutDraggingAcrossDisplays() {
        var state = RegionSelectionState()
        state.mouseDown(at: CGPoint(x: -800, y: 100))
        XCTAssertEqual(state.mouseUp(at: CGPoint(x: -800, y: 100)), .pending)
        state.mouseMoved(to: CGPoint(x: 1000, y: 800))
        XCTAssertEqual(state.rectangle, CGRect(x: -800, y: 100, width: 1800, height: 700))
        state.mouseDown(at: CGPoint(x: 1000, y: 800))
        XCTAssertEqual(state.mouseUp(at: CGPoint(x: 1000, y: 800)),
                       .selected(CGRect(x: -800, y: 100, width: 1800, height: 700)))
    }

    func testSmallDragNeverSubmitsAndCanBeReplaced() {
        var state = RegionSelectionState()
        state.mouseDown(at: .zero)
        state.mouseDragged(to: CGPoint(x: 9, y: 60))
        XCTAssertEqual(state.mouseUp(at: CGPoint(x: 9, y: 60)), .tooSmall)
        state.mouseDown(at: CGPoint(x: 20, y: 20))
        state.mouseDragged(to: CGPoint(x: 30, y: 30))
        XCTAssertEqual(state.mouseUp(at: CGPoint(x: 30, y: 30)),
                       .selected(CGRect(x: 20, y: 20, width: 10, height: 10)))
    }

    func testKeyboardSelectionSupportsMovementResizeAndFullScreenWithoutPointer() {
        var state = RegionSelectionState()
        let left = CGRect(x: -1440, y: -300, width: 1440, height: 900)
        let desktop = CGRect(x: -1440, y: -300, width: 3360, height: 1380)
        state.keyboardRectangle(in: left)
        let initial = state.rectangle
        state.adjust(dx: 20, dy: -10, resize: false, bounds: desktop)
        XCTAssertEqual(state.rectangle?.origin, initial?.offsetBy(dx: 20, dy: -10).origin)
        state.adjust(dx: 10, dy: 20, resize: true, bounds: desktop)
        XCTAssertEqual(state.rectangle?.size, CGSize(width: 330, height: 200))
        XCTAssertEqual(state.confirm(), state.rectangle.map(RegionSelectionState.Outcome.selected))
        XCTAssertEqual(state.chooseScreen(left), .selected(left))
    }

    func testKeyboardMovementAndResizeStayWithinDesktopAndMinimum() {
        var state = RegionSelectionState()
        let bounds = CGRect(x: -200, y: -100, width: 400, height: 300)
        state.keyboardRectangle(in: bounds)
        state.adjust(dx: -10000, dy: 10000, resize: false, bounds: bounds)
        XCTAssertEqual(state.rectangle?.minX, -200)
        XCTAssertEqual(state.rectangle?.maxY, 200)
        state.adjust(dx: -10000, dy: -10000, resize: true, bounds: bounds)
        XCTAssertEqual(state.rectangle?.size, CGSize(width: 10, height: 10))
    }

    func testCancelClearsAnchorAndSelectionForNextAttempt() {
        var state = RegionSelectionState()
        state.mouseDown(at: CGPoint(x: -20, y: -30))
        _ = state.mouseUp(at: CGPoint(x: -20, y: -30))
        state.mouseMoved(to: CGPoint(x: 50, y: 50))
        XCTAssertEqual(state.cancel(), .cancelled)
        XCTAssertNil(state.rectangle)
        XCTAssertEqual(state.mouseUp(at: CGPoint(x: 50, y: 50)), .pending)
        state.mouseDown(at: .zero)
        XCTAssertEqual(state.mouseUp(at: .zero), .pending)
    }
}
