import XCTest
import CoreGraphics
import CoreText
import CryptoKit
import ImageIO
@testable import CCTranslateSupport

final class LocalOCRTests: XCTestCase {
    func testOriginalSmallAboutNavigationRecognizesChineseAndEnglish() throws {
        let image = try originalAboutFixture()
        // The failing crop is the original top 120 points at 1x, not a resized or redrawn substitute.
        let navigation = try XCTUnwrap(image.cropping(to: CGRect(x: 0, y: 0, width: 660, height: 120)))
        XCTAssertEqual(navigation.width, 660)
        XCTAssertEqual(navigation.height, 120)

        let result = try LocalOCR.recognize(navigation)
        assertRecognized(result, contains: [
            "CC Translate", "macOS", "\u{5173}\u{4E8E}", "\u{7B2C}\u{4E09}\u{65B9}\u{8BB8}\u{53EF}"
        ])
        assertPreferredLanguagesRemainSupported(result)
    }

    func testSmallPureEnglishRemainsReadableWithoutLanguageCorrection() throws {
        let image = try textImage(
            ["Local screenshot translation 12345", "Settings and history remain local"],
            fontName: "Helvetica", fontSize: 13
        )
        let result = try LocalOCR.recognize(image)
        assertRecognized(result, contains: ["Local screenshot translation", "12345", "Settings and history"])
        assertPreferredLanguagesRemainSupported(result)
    }

    func testOrdinaryMixedSimplifiedChineseAndEnglishInEitherLineOrder() throws {
        let chinese = "\u{672C}\u{5730}\u{622A}\u{56FE}\u{7FFB}\u{8BD1}"
        let english = "Local screenshot translation 12345"
        for lines in [[english, chinese], [chinese, english]] {
            let image = try textImage(lines, fontName: "PingFangSC-Regular", fontSize: 24)
            let result = try LocalOCR.recognize(image)
            assertRecognized(result, contains: [chinese, english])
        }
    }

    func testOrdinaryMixedTraditionalChineseAndEnglishRemainRecognizable() throws {
        let chinese = "\u{672C}\u{6A5F}\u{622A}\u{5716}\u{7FFB}\u{8B6F}"
        let english = "Local screenshot translation 12345"
        let image = try textImage([chinese, english], fontName: "PingFangTC-Regular", fontSize: 24)
        let result = try LocalOCR.recognize(image)
        assertRecognized(result, contains: [chinese, english])
    }

    private func originalAboutFixture() throws -> CGImage {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Fixtures", isDirectory: true)
            .appendingPathComponent("about-metadata-zh-narrow.png")
        let data = try Data(contentsOf: url)
        // Exact synthetic product pixels retained from d699185 (also byte-identical to the 83405c7 failure).
        XCTAssertEqual(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(),
                       "bdd8714fc87a7d5fe5812bd7211706bb0343afa4a377d72ab4e17b3bf45da86a",
                       "Keep the original regression image; do not regenerate or resize it to satisfy OCR.")
        let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
        XCTAssertEqual(CGImageSourceGetCount(source), 1)
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        XCTAssertEqual(image.width, 660)
        XCTAssertEqual(image.height, 520)
        return image
    }

    private func textImage(_ lines: [String], fontName: String, fontSize: CGFloat) throws -> CGImage {
        let context = try XCTUnwrap(CGContext(
            data: nil, width: 660, height: 120, bitsPerComponent: 8, bytesPerRow: 660 * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 660, height: 120))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String):
                CTFontCreateWithName(fontName as CFString, fontSize, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)
        ]
        for (index, text) in lines.enumerated() {
            context.textPosition = CGPoint(x: 16, y: 80 - CGFloat(index) * 40)
            let line = CTLineCreateWithAttributedString(NSAttributedString(
                string: text, attributes: attributes
            ) as CFAttributedString)
            CTLineDraw(line, context)
        }
        return try XCTUnwrap(context.makeImage())
    }

    private func assertRecognized(_ result: OCRResult, contains expected: [String],
                                  file: StaticString = #filePath, line: UInt = #line) {
        let text = normalized(result.text)
        for token in expected {
            XCTAssertTrue(text.contains(normalized(token)), "Missing \(token) in production OCR: \(result.text)",
                          file: file, line: line)
        }
    }

    private func assertPreferredLanguagesRemainSupported(_ result: OCRResult,
                                                        file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(result.supportedLanguages.isEmpty, file: file, line: line)
        XCTAssertEqual(result.selectedLanguages,
                       ["en-US", "zh-Hans", "zh-Hant"].filter { result.supportedLanguages.contains($0) },
                       file: file, line: line)
    }

    private func normalized(_ text: String) -> String {
        text.lowercased().filter { !$0.isWhitespace }
    }
}
