"""Shared model-free classification, with no UI, platform or data-path imports."""

import re


_CODE_KEYWORD_RE = re.compile(
    r"\b(?:def|class|function|const|let|var|import|from|export|return|"
    r"public|private|protected|static|void|int|float|double|bool|boolean|"
    r"string|struct|enum|interface|namespace|package|func|fn|impl|trait|"
    r"async|await|yield|lambda|require|include|typedef|template|typename|"
    r"if|elif|else|for|while|switch|case|foreach|try|catch|except|finally|"
    r"throw|throws|new|delete|null|nil|None|True|False|true|false|"
    r"println|printf|console\.log|System\.out)\b")
_CODE_CALL_RE = re.compile(r"[A-Za-z_]\w*\(")             # foo(  bar(
_CODE_OPERATOR_RE = re.compile(r"(?:=>|->|::|\+\+|--|==|!=|<=|>=|&&|\|\||"
                               r"\+=|-=|\*=|/=|:=)")
_CODE_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z]\w*\b")          # getUserById
_CODE_SNAKE_RE = re.compile(r"\b[a-z]+_[a-z]\w*\b")         # user_name
_CODE_SYMBOLS = set("{}[]();<>=+-*/%&|^~")
_CODE_NON_PAREN_SYMBOLS = _CODE_SYMBOLS - set("()")
_PY_DECL_RE = re.compile(
    r"^(?:async\s+)?(?:def|class)\s+[A-Za-z_]\w*"
    r"(?:\s*\([^)]*\))?\s*(?:->\s*[^:]+)?\s*:")
_PY_CONTROL_RE = re.compile(
    r"^(?:(?:async\s+)?(?:with|for)|if|elif|else|while|try|except|finally|"
    r"match|case)\b.*:\s*(?:#.*)?$")
_PY_IMPORT_RE = re.compile(
    r"^(?:from\s+[\w.]+\s+import\s+.+|import\s+[\w.]+(?:\s+as\s+\w+)?"
    r"(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)$")
_PY_DECORATOR_RE = re.compile(
    r"^@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(.*\))?$")
_PY_STATEMENT_RE = re.compile(
    r"^(?:return|yield|raise|assert|pass|break|continue|del|global|nonlocal)\b")
_PY_ANNOTATION_RE = re.compile(
    r"^[A-Za-z_]\w*\s*:\s*[\w.\[\], |]+(?:\s*=\s*.+)?$")
_ASSIGNMENT_RE = re.compile(
    r"^(?P<lhs>(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\]]+\])?"
    r"(?:\s*:\s*[\w.\[\], |]+)?|"
    r"(?:[A-Za-z_]\w*\s*,\s*)+[A-Za-z_]\w*))"
    r"\s*(?:(?<![<>=!])=(?!=|>)|:=|\+=|-=|\*=|/=|//=|%=|\|=|&=)"
    r"\s*(?P<rhs>\S.*)$")
_BARE_CALL_RE = re.compile(
    r"^(?:await\s+)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
    r"\s*\(.*\)\s*;?$")
_JSON_MEMBER_RE = re.compile(r'^"[^"]+"\s*:\s*.+,?$')
_INLINE_JSON_RE = re.compile(r"^[\[{]\s*(?:\"[^\"]+\"\s*:|[\"'\d\-])")
_YAML_MEMBER_RE = re.compile(
    r"^(?P<indent>\s*)(?P<list>-\s+)?"
    r"(?P<key>[A-Za-z_][\w.-]*)\s*:\s*(?P<value>.*)$")
_CONFIG_SECTION_RE = re.compile(r"^\[[A-Za-z_][\w .-]*\]$")
_CONFIG_ASSIGN_RE = re.compile(r"^[A-Za-z_][\w.-]*\s*=\s*\S.*$")
_STANDALONE_DELIMITER_RE = re.compile(r"^[\[\]{}()]$")
_REGEX_PREFIX_RE = re.compile(r"^(?:regex|regexp|pattern)\s*:\s*\S", re.I)


def _assignment_looks_like_code(match):
    """Reject title-cased prose equations while accepting normal assignments."""
    lhs = match.group("lhs").split(":", 1)[0].strip()
    rhs = match.group("rhs").strip()
    first_name = re.match(r"[A-Za-z_]\w*", lhs)
    name = first_name.group(0) if first_name else ""
    if (name[:1].islower() or "_" in name or name.isupper()
            or "," in lhs or "." in lhs or "[" in lhs):
        return True
    return bool(
        rhs[:1] in "\"'[{("
        or re.fullmatch(r"(?:None|True|False|null|true|false|-?\d+(?:\.\d+)?)", rhs)
        or _CODE_CALL_RE.search(rhs)
        or _CODE_OPERATOR_RE.search(rhs))


def _looks_like_code_line(line):
    """Heuristic: does a single line look like source code (vs natural prose)?
    A line rich in CJK is treated as prose regardless of stray symbols."""
    s = line.strip()
    if not s:
        return None   # blank line: neutral, excluded from the ratio
    cjk = sum(1 for c in s if ord(c) > 0x2E7F)
    letters = sum(1 for c in s if c.isalpha())
    # Lines that are mostly Chinese/Japanese are prose, not code.
    if cjk and cjk >= max(2, letters * 0.5):
        return False

    if (_STANDALONE_DELIMITER_RE.fullmatch(s)
            or _PY_DECL_RE.match(s)
            or _PY_CONTROL_RE.match(s)
            or _PY_IMPORT_RE.match(s)
            or _PY_DECORATOR_RE.match(s)
            or _PY_STATEMENT_RE.match(s)
            or _BARE_CALL_RE.match(s)
            or _JSON_MEMBER_RE.match(s)
            or _INLINE_JSON_RE.match(s)
            or _CONFIG_SECTION_RE.match(s)
            or _REGEX_PREFIX_RE.match(s)):
        return True
    assignment = _ASSIGNMENT_RE.match(s)
    if assignment and _assignment_looks_like_code(assignment):
        return True

    score = 0
    if _CODE_KEYWORD_RE.search(s):
        score += 1
    if _CODE_CALL_RE.search(s):
        score += 1
    if _CODE_OPERATOR_RE.search(s):
        score += 1
    if _CODE_CAMEL_RE.search(s) or _CODE_SNAKE_RE.search(s):
        score += 1
    # Structural cues: ends with an opener/terminator, or is heavily indented.
    if s[-1] in "{};:," or s.endswith("=>"):
        score += 1
    if line[:1] in (" ", "\t") and (len(line) - len(line.lstrip())) >= 2:
        score += 1
    # Symbol density: lots of punctuation is a strong code signal.
    sym = sum(1 for c in s if c in _CODE_NON_PAREN_SYMBOLS)
    if len(s) and sym / len(s) >= 0.12:
        score += 1

    words = re.findall(r"[A-Za-z]+", s)
    if (len(words) >= 4 and not s.endswith(("{", "}", ";", ":", ","))
            and sym / len(s) < 0.12):
        return False
    structural = bool(
        _CODE_OPERATOR_RE.search(s)
        or s[-1] in "{};:,"
        or sym)
    if len(words) >= 3 and not structural:
        return False
    return score >= 2


def _block_code_line_indexes(lines):
    """Infer code lines that only become meaningful in a surrounding block."""
    nonblank = [(index, line) for index, line in enumerate(lines)
                if line.strip()]
    if not nonblank:
        return set()

    inferred = set()
    yaml_members = []
    config_assignments = []
    python_anchor = False

    for index, line in nonblank:
        s = line.strip()
        assignment = _ASSIGNMENT_RE.match(s)
        if (_PY_DECL_RE.match(s) or _PY_CONTROL_RE.match(s)
                or _PY_IMPORT_RE.match(s) or _PY_DECORATOR_RE.match(s)
                or _PY_STATEMENT_RE.match(s)
                or (assignment and _assignment_looks_like_code(assignment))):
            python_anchor = True
            inferred.add(index)
        yaml = _YAML_MEMBER_RE.match(line)
        if yaml:
            yaml_members.append((index, yaml))
        if _CONFIG_ASSIGN_RE.match(s):
            config_assignments.append(index)
        if _CONFIG_SECTION_RE.match(s) or _JSON_MEMBER_RE.match(s):
            inferred.add(index)

    machine_yaml_members = [
        (index, match) for index, match in yaml_members
        if match.group("key")[:1].islower()
        or any(char in match.group("key") for char in "_.-")
    ]
    yaml_is_structured = (
        len(yaml_members) >= 2
        and len(machine_yaml_members) >= 2
        and (
            len(yaml_members) >= 3
            or any(match.group("indent") or match.group("list")
                   or not match.group("value").strip()
                   for _, match in yaml_members)
        )
    )
    if yaml_is_structured:
        inferred.update(index for index, _ in yaml_members)
    if len(config_assignments) >= 2:
        inferred.update(config_assignments)

    first = nonblank[0][1].strip()
    last = nonblank[-1][1].strip()
    container_block = (
        (first == "{" and last == "}")
        or (first == "[" and last == "]")
        or (first == "(" and last == ")")
    )
    if container_block:
        for index, line in nonblank:
            s = line.strip()
            if (_STANDALONE_DELIMITER_RE.fullmatch(s)
                    or _JSON_MEMBER_RE.match(s)
                    or re.match(
                        r"^(?:[\"'].*[\"']|-?\d+(?:\.\d+)?|"
                        r"true|false|null|True|False|None),?$", s)):
                inferred.add(index)

    if python_anchor:
        in_docstring = False
        container_closer = None
        for index, line in nonblank:
            s = line.strip()
            assignment = _ASSIGNMENT_RE.match(s)
            if container_closer:
                inferred.add(index)
                if s.rstrip(",") == container_closer:
                    container_closer = None
            elif assignment and assignment.group("rhs") in ("[", "{", "("):
                inferred.add(index)
                container_closer = {"[": "]", "{": "}", "(": ")"}[
                    assignment.group("rhs")]
            triple_count = s.count('"""') + s.count("'''")
            if in_docstring or triple_count:
                inferred.add(index)
            if triple_count % 2:
                in_docstring = not in_docstring
            if line[:1].isspace() and (
                    s.startswith("#")
                    or _PY_ANNOTATION_RE.match(s)
                    or _STANDALONE_DELIMITER_RE.fullmatch(s)
                    or re.match(
                        r"^(?:[\"'].*[\"']|-?\d+(?:\.\d+)?|"
                        r"True|False|None),?$", s)):
                inferred.add(index)

    return inferred


def code_ratio(text):
    """Fraction (0.0-1.0) of non-blank lines that look like source code."""
    verdicts = [_looks_like_code_line(ln) for ln in text.split("\n")]
    considered = [v for v in verdicts if v is not None]
    if not considered:
        return 0.0
    return sum(1 for v in considered if v) / len(considered)


CODE_RATIO_PURE = 0.85
CODE_RATIO_MIXED = 0.15


def classify_selection(text):
    """Return 'code', 'mixed', or 'text' from a fast local heuristic. Never
    calls the model, so it adds no latency to the translation path."""
    t = (text or "").strip()
    if not t:
        return "text"
    lines = t.split("\n")
    verdicts = [_looks_like_code_line(line) for line in lines]
    considered = [verdict for verdict in verdicts if verdict is not None]
    if not considered:
        return "text"
    code_lines = {
        index for index, verdict in enumerate(verdicts) if verdict is True}
    block_lines = _block_code_line_indexes(lines)
    effective_ratio = len(code_lines | block_lines) / len(considered)
    if effective_ratio >= CODE_RATIO_PURE:
        return "code"
    if effective_ratio >= CODE_RATIO_MIXED:
        return "mixed"
    return "text"


def is_single_word(text):
    """True if the selection is a word or short term worth a dictionary entry
    rather than a sentence translation. Allows short multi-word terms (e.g.
    "machine learning", "New York") but rejects anything that looks like a
    sentence (line breaks, trailing sentence punctuation, or too long/too many
    tokens)."""
    if not text:
        return False
    t = text.strip()
    if not t or "\n" in t:
        return False
    # A trailing sentence terminator means it's a sentence, not a lookup term.
    if t[-1] in ".!?\u2026\u3002\uff01\uff1f\uff0c,;\uff1b:\uff1a":
        return False
    has_cjk = any(ord(c) > 0x2E7F for c in t)
    if has_cjk:
        # Preserve the existing short, unspaced CJK term rule.
        return " " not in t and len(t) <= 4
    # Latin terms have at most two tokens and 30 characters, including whitespace.
    parts = t.split()
    if not (1 <= len(parts) <= 2) or len(t) > 30:
        return False
    return all(p and all(c.isalpha() or c in "-'" for c in p) for p in parts)
