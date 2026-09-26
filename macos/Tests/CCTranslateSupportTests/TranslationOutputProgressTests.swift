import XCTest
@testable import CCTranslateSupport

final class TranslationOutputProgressTests: XCTestCase {
    func testContractHeadingsInEveryTargetLanguageAndSplitDeltas() {
        for (summary, translation) in [
            ("摘要", "译文"), ("Summary", "Translation"), ("要約", "翻訳"), ("요약", "번역"),
            ("Résumé", "Traduction"), ("Zusammenfassung", "Übersetzung"), ("Resumen", "Traducción")
        ] {
            let title = "## \(summary)"
            for end in title.indices {
                XCTAssertFalse(TranslationOutputProgress.inspect(String(title[...end])).hasMeaningfulContent)
            }
            XCTAssertFalse(TranslationOutputProgress.inspect(title).hasSummary)
            XCTAssertTrue(TranslationOutputProgress.inspect(title + "\n").hasSummary)
            XCTAssertTrue(TranslationOutputProgress.inspect(title, isFinal: true).hasSummary)
            XCTAssertFalse(TranslationOutputProgress.inspect(title).summaryComplete)
            let prefix = title + "\r\nA useful point.\r\n"
            XCTAssertTrue(TranslationOutputProgress.inspect(prefix).hasMeaningfulContent)
            let boundary = "## \(translation)"
            for end in boundary.indices {
                XCTAssertFalse(TranslationOutputProgress.inspect(prefix + String(boundary[...end])).summaryComplete)
            }
            XCTAssertTrue(TranslationOutputProgress.inspect(prefix + boundary + "\r\n").summaryComplete)
            XCTAssertTrue(TranslationOutputProgress.inspect(prefix + boundary, isFinal: true).summaryComplete)
        }
    }

    func testSummaryRequiresFirstNonblankDelimitedHeadingWithoutForeignPrefix() {
        for text in ["## Summary", "## Summary suffix\n", "Foreign prefix\n## Summary\n",
                     "Translation body\n\n## Summary\n", "---\n## Summary\n",
                     "### Action title\n## Summary\n", "prefix ## Summary\n",
                     "    ## Summary\n", "\t## Summary\n",
                     "```text\nexample\n```\n## Summary\n",
                     "~~~\n## Summary\n~~~\n## Summary\n"] {
            XCTAssertFalse(TranslationOutputProgress.inspect(text).hasSummary, text)
        }
        for text in ["## Summary\n", "\r\n  \r\n## 摘要\r\n", "   ## Summary ##\n",
                     "## Résumé\nContenu.\n## Traduction\n"] {
            XCTAssertTrue(TranslationOutputProgress.inspect(text).hasSummary, text)
        }
    }

    func testBodyUnknownHeadingOrFencePrefixNeverClaimsSummarySections() {
        for prefix in ["Ordinary body.\n", "## Translation\nOrdinary body.\n", "## Other\n",
                       "# Document\n", "### Nested title\n", "```\nexample\n```\n"] {
            let progress = TranslationOutputProgress.inspect(
                prefix + "## Summary\nA point.\n## Translation\nA translation.", isFinal: true)
            XCTAssertFalse(progress.hasSummary, prefix)
            XCTAssertFalse(progress.summaryComplete, prefix)
            XCTAssertTrue(progress.hasMeaningfulContent, prefix)
        }
    }

    func testRepeatedAndNestedTitlesDoNotResetOrPrematurelyFinishAnchoredSummary() {
        for body in ["A point.\n## Summary\n", "A point.\n### Summary\n",
                     "### Detail\nA point.\n", "A point.\n### Translation\n"] {
            let text = "## Summary\n" + body
            XCTAssertTrue(TranslationOutputProgress.inspect(text).hasSummary)
            XCTAssertFalse(TranslationOutputProgress.inspect(text).summaryComplete)
            XCTAssertTrue(TranslationOutputProgress.inspect(text + "## Translation\n").summaryComplete)
        }
        XCTAssertFalse(TranslationOutputProgress.inspect(
            "## Summary\n## Summary\n## Translation\n").summaryComplete)
        XCTAssertFalse(TranslationOutputProgress.inspect(
            "## Summary\nA point.\n# Other document\n## Translation\n").summaryComplete)
    }

    func testCompleteDeltaAndFinalHeadingKeepDelimiterEvidenceExplicit() {
        let text = "## Summary\nA point.\n## Translation"
        XCTAssertTrue(TranslationOutputProgress.inspect(text).hasSummary)
        XCTAssertFalse(TranslationOutputProgress.inspect(text).summaryComplete)
        XCTAssertTrue(TranslationOutputProgress.inspect(text + "\n").summaryComplete)
        XCTAssertTrue(TranslationOutputProgress.inspect(text, isFinal: true).summaryComplete)
    }

    func testEmptyMismatchedUnknownAndIncompleteSectionsDoNotClaimCompletion() {
        for text in [
            "## Summary\n## Translation\n",
            "## Summary\n---\n**\n## Translation\n",
            "## Summary\nbody\n## Translat",
            "## Summary\nbody\n## Translation extra\n",
            "## Summary\nbody\n## 译文\n",
            "## Summary\nbody\n## Other\n## Translation\n",
            "## Translation\nbody\n## Summary\n",
            "# Summary\nbody\n## Translation\n"
        ] {
            XCTAssertFalse(TranslationOutputProgress.inspect(text).summaryComplete, text)
        }
    }

    func testHeadingsAndMarkdownMarkersAreNotFirstBodyContent() {
        for text in ["", " ", "#", "## ", "## Summary", "### Action title\n", "## Unknown\n",
                     "**", "_", "---", ">", "- ", "* ", "1. ", "1) ", "![]()", "``", "```swift\n", "~~~python\n"] {
            XCTAssertFalse(TranslationOutputProgress.inspect(text).hasMeaningfulContent, text)
        }
        for text in ["hello", "摘要内容", "- point", "1. point", "**body", "42", "✅", "```swift\nlet x = 1"] {
            XCTAssertTrue(TranslationOutputProgress.inspect(text).hasMeaningfulContent, text)
        }
    }

    func testCodeFenceHeadingsAreNeverSections() {
        for marker in ["```", "~~~", "````"] {
            let fenced = marker + "text\n## Summary\nbody\n## Translation\n" + marker
            XCTAssertFalse(TranslationOutputProgress.inspect(fenced, isFinal: true).hasSummary)
            XCTAssertFalse(TranslationOutputProgress.inspect(fenced, isFinal: true).summaryComplete)
            XCTAssertTrue(TranslationOutputProgress.inspect(fenced).hasMeaningfulContent)
            let text = "## Summary\nbody\n" + marker + "\n## Translation\n" + marker + "\n"
            XCTAssertFalse(TranslationOutputProgress.inspect(text).summaryComplete)
            XCTAssertTrue(TranslationOutputProgress.inspect(text + "## Translation\n").summaryComplete)
        }
    }

    func testOneShotAndCachedFinalUseSameBoundaryEvidenceNotEndOfText() {
        XCTAssertTrue(TranslationOutputProgress.inspect(
            "## Summary ##\nA point.\n## Translation ###\n").summaryComplete)
        XCTAssertTrue(TranslationOutputProgress.inspect(
            "## Summary\nA point.\n## Translation\nTranslated body.", isFinal: true).summaryComplete)
        XCTAssertFalse(TranslationOutputProgress.inspect("## Summary\nA point.", isFinal: true).summaryComplete)
        XCTAssertFalse(TranslationOutputProgress.inspect("Plain translation.", isFinal: true).hasSummary)
    }
}
