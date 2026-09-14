"""Portable summary contracts and unchanged Windows entry-point seams."""

import ast
import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from cc_direction import LANGUAGES
import cc_summary as rules


ROOT = Path(__file__).resolve().parents[1]

# UTF-8 source fingerprints captured before extraction, including docstrings.
LEGACY_SOURCE_SHA256 = {
    "STREAM_MIN_CHARS": "d9f6a1d6dd14958e629c740de098938af08e39ad9955688ae7b34049837ecc86",
    "SUMMARY_MIN_CHARS": "9fae1e14c4a30b822b135e4b5cb98198d1e6833ee2db1c28555f9e60b925a95b",
    "_LIST_MARKER_RE": "3edc7c648e6f8fb6872d839d390e894976d8d6573aca44a1b1725205881aca1d",
    "_CONFIG_KV_LINE_RE": "494b47ca011c076a7cbb5cacc20394a0cb395f7e7625b50cc5238e028d323a5a",
    "_CONFIG_ASSIGN_LINE_RE": "e30d5f34ebb880d47c1af998ebbee2f1e80e3f34b58fc7e585fb6c1d29d6b4c1",
    "is_summarizable_prose": "384227e025e22c544d651566108321e8435e0f82d5f92c9af6dca585307d9567",
    "SUMMARY_HEADINGS": "3d24ef7a5eb815a0f4a4d5001e2c0f9403154058fcd2be2f972adeaf98b30023",
    "summary_headings": "2b182a43167e178b08bf62ca48c901ca2c22dc46033c530a51e800ee04a013c9",
    "summary_instruction": "eabd02d93e9c239c126db13c48d9df8a280e3dc5c227ad2f262da152c6ebb9a0",
    "codex_summary_instruction": "105d68af952fcdc06d8855bfc5a52ec197f22715fce8b60d5d1c90b0838172d5",
}
LEGACY_WINDOWS_SHA256 = {
    "_should_summarize": "0778fdac3645f45716c37c5589903a3eb8b4227bdf0b11e6816fab6e3f258362",
    "_system_prompt_for": "ab3505b7cb4155c324872beabf9778ca71139a66fc24f0ea871a41af88584f8b",
}
LEGACY_PROMPT_SHA256 = {
    "zh": ("cd156f8b54dc7071d2fc2b59d6af746fcf5584a8d4e851a73e123a6f84442aa0",
           "e8072739006b01bfc97a64b4861b83e22893b664d7f75ef77ad5944862b64dfd"),
    "en": ("e486c6659f8d1ef135a7ec07fd19112ccc460b2b5bade46d698391e20cefbc9c",
           "932b4c4af979addeeebebb9cb79f2756af9082a9be6cf1acf7813e8dec776462"),
    "ja": ("91e2c78006228ce268af50eca58648d75e6595b0b8a3de4195ccf6d58dce894e",
           "86a6a04217a61d79a82427c31c3537ffdc3e134e48694483be08861a478543f3"),
    "ko": ("b99fc889b847a2c7f7d6346ab54dae0802fe47d578ba9f7fad5c96307d24958a",
           "5a084251b685d0f33e5a464b989936629bc83ae0faf41c8eabb7c08a31d83032"),
    "fr": ("a5850a8652def5f8b621dd9d9480d9923781fe5827d36948094fce2d18122e5c",
           "3f8fe263339d44274f1881af027cc2c696fef7bf4de3b772366397cc0023195f"),
    "de": ("0bff6bfd9f666e59ce8fb1e0a3540dd31f0de1635dbe7382c4094d53aeece315",
           "7aa2ac27e5afdf03e559576aab5fc58d064cb275067417c03cf13a20693a65ab"),
    "es": ("b7acf45d9652baccbe4e5e33616834bcca32fce83688c1ccc339fe9146b6adc8",
           "b3c4117a44b4f0f20fcd89a3a1150c931181d380fbc9bfd236c546399127e077"),
    "xx": ("75f57e3befb38cb5aaba563f878b3e7f1686aacf8223bd75f0b5f2322fb1d9b2",
           "d0ba991fa80cb8b8d65e0afa42e9985d1484758c0450fe768d277303f65716a4"),
}


def source_definitions(source):
    result = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            result[node.name] = node
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            result[node.targets[0].id] = node
    return result


class SummaryRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(rules.__file__).read_text(encoding="utf-8")
        cls.windows_source = (ROOT / "translator.pyw").read_text(encoding="utf-8")
        cls.windows_nodes = source_definitions(cls.windows_source)

    def windows_gate(self, **overrides):
        from cc_classify import is_single_word

        namespace = {
            "CFG": SimpleNamespace(SUMMARY_ENABLED="summary_enabled"),
            "DEFAULT_CONFIG": {"summary_enabled": True},
            "SUMMARY_MIN_CHARS": rules.SUMMARY_MIN_CHARS,
            "is_single_word": is_single_word,
            "is_summarizable_prose": rules.is_summarizable_prose,
            **overrides,
        }
        tree = ast.Module(body=[self.windows_nodes["_should_summarize"]], type_ignores=[])
        exec(compile(tree, "<windows-summary-gate>", "exec"), namespace)
        return namespace["_should_summarize"]

    def test_extracted_definitions_preserve_original_source_bytes(self):
        nodes = source_definitions(self.source)
        for name, expected in LEGACY_SOURCE_SHA256.items():
            with self.subTest(name=name):
                source = ast.get_source_segment(self.source, nodes[name])
                self.assertEqual(hashlib.sha256(source.encode("utf-8")).hexdigest(), expected)

    def test_windows_gate_and_prompt_preserve_original_source_bytes(self):
        for name, expected in LEGACY_WINDOWS_SHA256.items():
            with self.subTest(name=name):
                source = ast.get_source_segment(self.windows_source, self.windows_nodes[name])
                self.assertEqual(hashlib.sha256(source.encode("utf-8")).hexdigest(), expected)

    def test_windows_reexports_same_objects_without_duplicate_thresholds(self):
        for filename, names in (
                ("translator.pyw", set(LEGACY_SOURCE_SHA256)),
                ("cc_core.py", {"STREAM_MIN_CHARS"})):
            source = (ROOT / filename).read_text(encoding="utf-8")
            imports = [node for node in ast.parse(source).body
                       if isinstance(node, ast.ImportFrom) and node.module == "cc_summary"]
            namespace = {}
            exec(compile(ast.Module(body=imports, type_ignores=[]), filename, "exec"), namespace)
            for name in names:
                with self.subTest(filename=filename, name=name):
                    self.assertIs(namespace[name], getattr(rules, name))
                    self.assertNotIn(name, source_definitions(source))
        self.assertIs(rules.STREAM_MIN_CHARS, rules.SUMMARY_MIN_CHARS)
        self.assertIs(type(rules.STREAM_MIN_CHARS), int)
        self.assertEqual(rules.STREAM_MIN_CHARS, 400)
        self.assertIs(rules.LANGUAGES, LANGUAGES)

    def test_import_is_platform_neutral_without_application_io(self):
        imports = [node for node in ast.parse(self.source).body
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(
            {node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
             for node in imports}, {"re", "cc_direction"})
        script = r'''
import os
import sys
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event == "open":
        path, mode, flags = args
        if not isinstance(path, str) or not path.endswith((".py", ".pyc")):
            raise AssertionError(("non-import file read", path))
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError(("file write", path))
    if event.startswith(("socket.", "subprocess.", "os.mkdir", "os.remove",
                         "os.rename", "os.rmdir", "os.system")):
        raise AssertionError(("application I/O", event))
    if event == "import" and args[0].split(".")[0] in {
            "cc_core", "translator", "i18n", "tkinter", "win32util", "ctypes",
            "cc_warm", "cc_update", "cc_macos", "cc_providers"}:
        raise AssertionError(("platform dependency", args[0]))
sys.addaudithook(audit)
import cc_summary
assert cc_summary.SUMMARY_MIN_CHARS == cc_summary.STREAM_MIN_CHARS
assert cc_summary.is_summarizable_prose("A natural language sentence.")
assert "## Summary" in cc_summary.summary_instruction("en")
assert "## 翻訳" in cc_summary.codex_summary_instruction("ja")
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(Path(rules.__file__).resolve().parent)],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_and_invalid_input_contract_is_unchanged(self):
        for text in (None, "", "   \n  ", False, 0):
            with self.subTest(text=text):
                self.assertIs(rules.is_summarizable_prose(text), False)
        with self.assertRaises(AttributeError):
            rules.is_summarizable_prose(1)
        with self.assertRaises(TypeError):
            rules.is_summarizable_prose(b"Prose.")

    def test_prose_requires_sentences_or_multiple_lines_not_length(self):
        for text, expected in (
                ("A short sentence.", True), ("A sentence without punctuation", False),
                ("A first paragraph\nA second paragraph", True),
                ("   A single line with blank lines   \n\n", False),
                ("这是自然语言。", True), ("終わり！", True), ("계속…", True)):
            with self.subTest(text=text):
                self.assertIs(rules.is_summarizable_prose(text), expected)

    def test_link_and_path_token_threshold_is_inclusive(self):
        for link in ("https://example.com/long/path", "http://example.com",
                     "www.example.com", "long/path/value", r"C:\long\path"):
            with self.subTest(link=link):
                self.assertFalse(rules.is_summarizable_prose(link + " prose."))
                self.assertTrue(rules.is_summarizable_prose(link + " more prose."))
        self.assertTrue(rules.is_summarizable_prose("a/b short."))

    def test_list_line_count_and_ratio_boundaries(self):
        for marker in ("-", "*", "+", "•", "1.", "2)"):
            bullet = marker + " A sufficiently descriptive list entry."
            with self.subTest(marker=marker):
                self.assertTrue(rules.is_summarizable_prose("\n".join([bullet] * 2)))
                self.assertFalse(rules.is_summarizable_prose("\n".join([bullet] * 3)))
                self.assertFalse(rules.is_summarizable_prose(
                    "\n".join([bullet] * 4 + ["A natural language explanation."])))
                self.assertTrue(rules.is_summarizable_prose(
                    "\n".join([bullet] * 3 + ["A natural language explanation."] * 2)))

    def test_config_line_count_and_ratio_boundaries(self):
        for config in ("name: a sufficiently descriptive configuration value",
                       "NAME=a_sufficiently_descriptive_configuration_value"):
            prose = "This is an ordinary prose sentence with context."
            with self.subTest(config=config):
                self.assertTrue(rules.is_summarizable_prose("\n".join([config] * 3)))
                self.assertFalse(rules.is_summarizable_prose("\n".join([config] * 4)))
                self.assertFalse(rules.is_summarizable_prose(
                    "\n".join([config] * 2 + [prose] * 2)))
                self.assertTrue(rules.is_summarizable_prose(
                    "\n".join([config] + [prose] * 3)))

    def test_structural_punctuation_threshold_is_inclusive(self):
        self.assertTrue(rules.is_summarizable_prose("x" * 92 + "." + "{" * 7))
        self.assertFalse(rules.is_summarizable_prose("x" * 91 + "." + "{" * 8))

    def test_heading_types_targets_and_fallback_are_unchanged(self):
        expected = {
            "zh": ("摘要", "译文"), "en": ("Summary", "Translation"),
            "ja": ("要約", "翻訳"), "ko": ("요약", "번역"),
            "fr": ("Résumé", "Traduction"), "de": ("Zusammenfassung", "Übersetzung"),
            "es": ("Resumen", "Traducción"),
        }
        self.assertIs(type(rules.SUMMARY_HEADINGS), dict)
        self.assertEqual(rules.SUMMARY_HEADINGS, expected)
        self.assertEqual(set(rules.SUMMARY_HEADINGS), set(LANGUAGES))
        for target in (*LANGUAGES, "xx", None, "", 7):
            with self.subTest(target=target):
                self.assertIs(rules.summary_headings(target),
                              rules.SUMMARY_HEADINGS.get(target, rules.SUMMARY_HEADINGS["en"]))
                self.assertIs(type(rules.summary_headings(target)), tuple)
        with self.assertRaises(TypeError):
            rules.summary_headings([])

    def test_original_prompt_bytes_for_every_language_and_unknown_target(self):
        for target, expected in LEGACY_PROMPT_SHA256.items():
            for function, digest in zip(
                    (rules.summary_instruction, rules.codex_summary_instruction), expected):
                with self.subTest(target=target, function=function.__name__):
                    result = function(target)
                    self.assertIs(type(result), str)
                    self.assertEqual(hashlib.sha256(result.encode("utf-8")).hexdigest(), digest)
        self.assertEqual(rules.summary_instruction(None), rules.summary_instruction("xx"))
        self.assertEqual(rules.codex_summary_instruction(None), rules.codex_summary_instruction("xx"))

    def test_windows_gate_keeps_lazy_short_circuit_order(self):
        word_test = mock.Mock(side_effect=AssertionError("unexpected word test"))
        prose_test = mock.Mock(side_effect=AssertionError("unexpected prose test"))
        gate = self.windows_gate(is_single_word=word_test, is_summarizable_prose=prose_test)
        for app in (
                SimpleNamespace(cfg={"summary_enabled": False}),
                SimpleNamespace(cfg={}, _last_origin="ocr"),
                SimpleNamespace(cfg={}, _last_origin="text", _last_class="code")):
            with self.subTest(app=app):
                self.assertFalse(gate(app, object()))
        word_test.assert_not_called()
        prose_test.assert_not_called()
        app = SimpleNamespace(cfg={}, _last_origin="text", _last_class="text")
        word_test.side_effect = None
        word_test.return_value = True
        self.assertFalse(gate(app, object()))
        prose_test.assert_not_called()
        word_test.return_value = False
        self.assertFalse(gate(app, "Short sentence."))
        prose_test.assert_not_called()

    def test_windows_gate_uses_shared_threshold_and_keeps_word_patch_seam(self):
        for content_class in ("text", "mixed"):
            app = SimpleNamespace(cfg={}, _last_origin="text", _last_class=content_class)
            for length in (rules.SUMMARY_MIN_CHARS - 1, rules.SUMMARY_MIN_CHARS,
                           rules.SUMMARY_MIN_CHARS + 1):
                text = "An ordinary natural language sentence. " + "a" * (length - 39)
                self.assertEqual(len(text), length)
                with self.subTest(content_class=content_class, length=length):
                    word_test = mock.Mock(return_value=False)
                    gate = self.windows_gate(is_single_word=word_test)
                    self.assertIs(gate(app, text), length >= rules.SUMMARY_MIN_CHARS)
                    word_test.assert_called_once_with(text)
                    word_test.return_value = True
                    self.assertFalse(gate(app, text))

    def test_windows_gate_keeps_prose_and_threshold_patch_seams(self):
        app = SimpleNamespace(cfg={}, _last_origin="text", _last_class="text")
        text = "An ordinary natural language sentence. " * 20
        result = object()
        prose_test = mock.Mock(return_value=result)
        gate = self.windows_gate(is_summarizable_prose=prose_test)
        self.assertIs(gate(app, text), result)
        prose_test.assert_called_once_with(text)
        gate = self.windows_gate(SUMMARY_MIN_CHARS=len(text) + 1,
                                 is_summarizable_prose=prose_test)
        self.assertFalse(gate(app, text))
        prose_test.assert_called_once()


if __name__ == "__main__":
    unittest.main()
