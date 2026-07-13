"""Markdown-lite rich-text rendering for CC Translate result popups.

Translation output is plain text, but the model (in dictionary / code modes)
can emit light markdown, and even plain translations often carry `inline code`
or URLs. We parse a small, safe subset into (text, tag) segments that a
tk.Text renders with per-tag colour/font. The parser is deliberately
stream-safe: an unclosed marker is left as literal text.

Public API used by translator.pyw:
  iter_rich_segments(message, highlight=False) -> list[(str, str|None)]
  highlight_code(code, lang=None)              -> list[(str, str)] | None
  _PYGMENTS_OK                                 bool: True when Pygments is present
"""

import re

# Pygments is optional — provides syntax-highlighting inside fenced code blocks.
# When absent the renderer falls back to single-colour code style with no crash.
try:
    from pygments import lex as _pyg_lex
    from pygments.lexers import get_lexer_by_name as _pyg_get_lexer, guess_lexer as _pyg_guess
    from pygments.token import Token as _PygToken
    from pygments.util import ClassNotFound as _PygClassNotFound
    _PYGMENTS_OK = True
except Exception:
    _PYGMENTS_OK = False
    _PygToken = None

# ---------------------------------------------------------------------------
# Inline span parser
# ---------------------------------------------------------------------------
_INLINE_RE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<bold>\*\*[^\n]+?\*\*)"
    r"|(?P<italic>(?<![\w*])\*[^*\n]+?\*(?![\w*])"
    r"|(?<![\w_])_[^_\n]+?_(?![\w_]))"
    r"|(?P<url>https?://[^\s)\]}>]+)"
)


def _iter_inline_segments(text):
    """Yield (text, tag) tuples for one line's inline markdown-lite spans."""
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            yield (text[pos:m.start()], None)
        kind = m.lastgroup
        s = m.group()
        if kind == "code":
            yield (s[1:-1], "rich_code")
        elif kind == "bold":
            yield (s[2:-2], "rich_bold")
        elif kind == "italic":
            yield (s[1:-1], "rich_italic")
        elif kind == "url":
            yield (s, "rich_url")
        pos = m.end()
    if pos < len(text):
        yield (text[pos:], None)


# ---------------------------------------------------------------------------
# Pygments syntax highlighting
# ---------------------------------------------------------------------------

def _pyg_token_tag(ttype):
    """Map a Pygments token type to one of our tk.Text tag names."""
    if ttype in _PygToken.Comment:
        return "rich_tok_comment"
    if ttype in _PygToken.Keyword:
        return "rich_tok_keyword"
    if ttype in _PygToken.Name.Function or ttype in _PygToken.Name.Class:
        return "rich_tok_func"
    if ttype in _PygToken.String:
        return "rich_tok_string"
    if ttype in _PygToken.Number:
        return "rich_tok_number"
    if ttype in _PygToken.Operator:
        return "rich_tok_operator"
    if ttype in _PygToken.Name:
        return "rich_tok_ident"
    return "rich_codeblock"


def highlight_code(code, lang=None):
    """Return [(text, tag)] segments for a code block using Pygments, or None if
    Pygments is unavailable / can't lex it (caller then falls back to a single
    colour). Called only on the final frame, never on the streaming hot path."""
    if not _PYGMENTS_OK or not code:
        return None
    try:
        lexer = None
        if lang:
            try:
                lexer = _pyg_get_lexer(lang)
            except _PygClassNotFound:
                lexer = None
        if lexer is None:
            try:
                lexer = _pyg_guess(code)
            except Exception:
                lexer = None
        if lexer is None:
            return None
        out = []
        for ttype, val in _pyg_lex(code, lexer):
            if val:
                out.append((val, _pyg_token_tag(ttype)))
        return out or None
    except Exception:
        return None


def _flush_highlighted_fence(segs, fence_lines, lang):
    """Append a finished fenced code block to segs, syntax-highlighted when
    possible, otherwise as literal single-colour code lines."""
    code = "\n".join(fence_lines)
    toks = highlight_code(code, lang)
    if toks:
        segs.extend(toks)
        if not code.endswith("\n"):
            segs.append(("\n", None))
    else:
        for ln in fence_lines:
            segs.append((ln, "rich_codeblock"))
            segs.append(("\n", None))


# ---------------------------------------------------------------------------
# Block-level + inline combined parser
# ---------------------------------------------------------------------------

def iter_rich_segments(message, highlight=False):
    """Parse markdown-lite text into a flat list of (text, tag) segments,
    including the newlines between lines. tag is a tk.Text tag name or None
    (plain). Handles fenced code blocks, ATX headings, bullet/numbered lists,
    and the inline spans from _iter_inline_segments.

    When highlight=True (final frames only), closed fenced code blocks are
    syntax-highlighted with Pygments. When highlight=False (streaming), code is
    rendered line-by-line in a single colour so partial blocks appear instantly
    and no lexer runs on the hot path."""
    segs = []
    lines = message.split("\n")
    in_fence = False
    fence_lang = None
    fence_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if highlight:
                if not in_fence:
                    in_fence = True
                    fence_lang = stripped[3:].strip() or None
                    fence_lines = []
                else:
                    _flush_highlighted_fence(segs, fence_lines, fence_lang)
                    in_fence = False
                    fence_lang = None
                    fence_lines = []
            else:
                # Toggle a fenced code block; the fence line isn't rendered.
                in_fence = not in_fence
            continue
        if in_fence:
            if highlight:
                fence_lines.append(line)
            else:
                segs.append((line, "rich_codeblock"))
                segs.append(("\n", None))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = min(len(m.group(1)), 3)
            segs.append((m.group(2), f"rich_h{level}"))
            segs.append(("\n", None))
            continue
        m = re.match(r"^(\s*)(?:[-*+]|\d+\.)\s+(.*)$", line)
        if m:
            segs.append((m.group(1) + "•  ", "rich_bullet"))
            segs.extend(_iter_inline_segments(m.group(2)))
            segs.append(("\n", None))
            continue
        segs.extend(_iter_inline_segments(line))
        segs.append(("\n", None))
    # An unterminated fence (still streaming, or malformed) renders literally.
    if highlight and in_fence and fence_lines:
        for ln in fence_lines:
            segs.append((ln, "rich_codeblock"))
            segs.append(("\n", None))
    # Drop the trailing newline we always append after the last line.
    if segs and segs[-1] == ("\n", None):
        segs.pop()
    return segs
