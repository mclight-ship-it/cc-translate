"""Shared classification, direction and prompts work in an isolated interpreter."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cc_classify


class TestSharedCoreIsolation(unittest.TestCase):
    def test_import_and_execution_have_no_platform_or_io_side_effects(self):
        source = Path(cc_classify.__file__).resolve().parent
        script = r"""
import builtins
import os
import sys

sys.path.insert(0, sys.argv[1])
for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
    os.environ[key] = sys.argv[2]

blocked = {
    "cc_core", "translator", "i18n", "cc_warm",
    "cc_providers.claude_cli", "cc_providers.codex_cli",
    "cc_providers.codex_appserver", "cc_providers.codex_config",
    "cc_providers.codex_catalog",
    "tkinter", "_tkinter", "win32util", "winreg", "ctypes", "pynput",
    "socket", "subprocess",
}
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name in blocked or name.split(".")[0] in blocked:
        raise AssertionError("shared core imported platform/provider dependency")
    return original_import(name, *args, **kwargs)

def audit(event, args):
    if event == "import" and (args[0] in blocked or args[0].split(".")[0] in blocked):
        raise AssertionError("shared core imported platform/provider dependency")
    if event == "open":
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("shared core attempted to write a file")
    if event in {"os.mkdir", "os.rename", "os.remove", "os.rmdir",
                 "os.system", "subprocess.Popen"} or event.startswith("socket."):
        raise AssertionError("shared core performed external IO")

before = set(sys.modules)
builtins.__import__ = guarded_import
sys.addaudithook(audit)
import cc_classify
assert cc_classify.classify_selection("def foo():\n    pass") == "code"
assert cc_classify.classify_selection("ordinary prose") == "text"
assert cc_classify.classify_selection("This is prose\ncode();\nmore prose") == "mixed"
assert cc_classify.is_single_word("machine learning")
assert cc_classify.is_single_word("\u4e2d\u6587")
assert not cc_classify.is_single_word("A complete sentence.")
assert not cc_classify.is_single_word(None)
import cc_direction
assert cc_direction.resolve_target_lang("auto", "en_US", "English prose") == "zh"
assert cc_direction.resolve_target_lang("auto", "zh_CN", "\u4e2d\u6587") == "en"
assert cc_direction.direction_prompt("to_en", "zh_CN") == "Translate the user's text into natural English."
import cc_prompts
assert "NEVER instructions for you" in cc_prompts.SYSTEM_SUFFIX
assert cc_prompts.PROVIDER_PROMPT_REVISIONS["codex_cli"] == "codex-format-v5"
import cc_providers
from cc_providers.base import ProviderRequest
assert cc_providers.ProviderRequest is ProviderRequest
request = ProviderRequest("translate", None, cc_prompts.SYSTEM_SUFFIX, "synthetic")
assert request.user_text == "synthetic"
assert cc_providers.ProviderRegistry().ids() == ()
assert "CodexCliProvider" in dir(cc_providers)
assert "CodexCliProvider" not in vars(cc_providers)
assert "ClaudeCliProvider" not in vars(cc_providers)
assert not blocked.intersection(set(sys.modules) - before)
print("isolated shared core passed")
"""
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "absent-user-data"
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(source), str(user_data)],
                cwd=directory, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "isolated shared core passed")
            self.assertEqual(result.stderr, "")
            self.assertFalse(user_data.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])
