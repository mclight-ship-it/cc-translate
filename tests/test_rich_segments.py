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
