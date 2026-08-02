"""Tests for the markdown-lite parser iter_rich_segments and its helper
_iter_inline_segments.

Key guarantees under test:
- inline spans (code / bold / italic / url) parse to the right tags
- block elements (headings, bullets) parse to the right tags
- markers are stripped, so the reconstructed text is clean (copy/history)
- an unclosed marker is left as literal text (stream-safe, no crash)
- highlight=True syntax-highlights closed fenced blocks; highlight=False keeps
  code single-colour line-by-line (the streaming hot path)
"""
import unittest

from tests._tr import tr


def tags(segs):
    return [t for _, t in segs if t]


def reconstruct(segs):
    return "".join(c for c, _ in segs)


class TestInlineSpans(unittest.TestCase):
    def test_bold_code_italic(self):
        segs = tr.iter_rich_segments("普通 **加粗** 和 `code` 还有 *斜体*")
        self.assertIn("rich_bold", tags(segs))
        self.assertIn("rich_code", tags(segs))
        self.assertIn("rich_italic", tags(segs))

    def test_url(self):
        segs = tr.iter_rich_segments("看 https://example.com 这个")
        self.assertIn("rich_url", tags(segs))
        # The URL text itself is preserved verbatim.
        self.assertTrue(any(c == "https://example.com" for c, t in segs
                            if t == "rich_url"))

    def test_markers_stripped_for_clean_copy(self):
        # What Text.get() would yield (copy/history) must have no markup left.
        segs = tr.iter_rich_segments("普通 **加粗** 和 `code`")
        self.assertEqual(reconstruct(segs), "普通 加粗 和 code")


class TestBlocks(unittest.TestCase):
    def test_heading(self):
        segs = tr.iter_rich_segments("# 标题行")
        self.assertEqual(segs[0], ("标题行", "rich_h1"))

    def test_bullet(self):
        segs = tr.iter_rich_segments("- 列表项一")
        self.assertIn("rich_bullet", tags(segs))
        self.assertIn("列表项一", reconstruct(segs))

    def test_empty_marker_absorbs_orphaned_text_line(self):
        # Regression: the model sometimes emits a bare marker ("- ") and puts
        # the item's text on the FOLLOWING line with no marker. That used to
        # render a lone "•" with its text orphaned on a plain, un-indented line
        # below it. The empty marker must absorb the continuation so it reads as
        # one normal bullet.
        md = "- 第一项\n- \n通过添加辅助函数删除了平行逻辑\n- 第三项"
        segs = tr.iter_rich_segments(md)
        # Exactly three bullets, none of them empty.
        bullets = [c for c, t in segs if t == "rich_bullet"]
        self.assertEqual(len(bullets), 3)
        # The orphaned text is now rendered as the second bullet's content,
        # immediately after a bullet marker (not as a stray plain line).
        idx = next(i for i, (c, _) in enumerate(segs)
                   if c == "通过添加辅助函数删除了平行逻辑")
        self.assertEqual(segs[idx - 1][1], "rich_bullet",
                         "orphaned line should follow a bullet marker")

    def test_genuinely_empty_marker_is_dropped(self):
        # A bare marker with nothing following (e.g. a mid-stream frame that has
        # the "- " but not yet its text) must NOT render a lone bullet dot.
        segs = tr.iter_rich_segments("- 第一项\n- ")
        bullets = [c for c, t in segs if t == "rich_bullet"]
        self.assertEqual(len(bullets), 1, "trailing empty marker must be dropped")

    def test_normal_bullets_unchanged(self):
        # The absorption must not touch well-formed bullets.
        md = "- 一\n- 二\n- 三"
        segs = tr.iter_rich_segments(md)
        self.assertEqual([c for c, t in segs if t == "rich_bullet"],
                         ["•  ", "•  ", "•  "])
        self.assertIn("一", reconstruct(segs))
        self.assertIn("二", reconstruct(segs))
        self.assertIn("三", reconstruct(segs))


class TestStreamSafety(unittest.TestCase):
    def test_unclosed_bold_is_literal(self):
        # A half-streamed "**" must render literally, not crash or eat text.
        segs = tr.iter_rich_segments("这是 **未闭合")
        self.assertNotIn("rich_bold", tags(segs))
        self.assertEqual(reconstruct(segs), "这是 **未闭合")

    def test_plain_text_roundtrips(self):
        text = "just plain text, nothing special"
        self.assertEqual(reconstruct(tr.iter_rich_segments(text)), text)


class TestFencedCodeBlocks(unittest.TestCase):
    CODE_MD = "前言\n```python\ndef f():\n    return 1\n```\n后语"

    def test_streaming_path_no_token_tags(self):
        # highlight=False (streaming): code stays single-colour, no lexer runs.
        segs = tr.iter_rich_segments(self.CODE_MD, highlight=False)
        tok = [t for t in tags(segs) if t.startswith("rich_tok_")]
        self.assertEqual(tok, [])
        self.assertIn("rich_codeblock", tags(segs))

    def test_final_path_highlights_when_available(self):
        # highlight=True: closed fence gets Pygments token tags (if installed).
        segs = tr.iter_rich_segments(self.CODE_MD, highlight=True)
        tok = [t for t in tags(segs) if t.startswith("rich_tok_")]
        if tr._PYGMENTS_OK:
            self.assertTrue(tok, "expected token tags when Pygments is present")
        else:
            # Graceful degradation: single-colour code block, no crash.
            self.assertIn("rich_codeblock", tags(segs))

    def test_unterminated_fence_does_not_highlight(self):
        # A still-open fence must render literally, never lex a partial block.
        segs = tr.iter_rich_segments("开始\n```python\ndef g():", highlight=True)
        tok = [t for t in tags(segs) if t.startswith("rich_tok_")]
        self.assertEqual(tok, [])


if __name__ == "__main__":
    unittest.main()
