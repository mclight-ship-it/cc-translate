"""The shared classifier must also work in an isolated, GUI-free interpreter."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cc_classify


class TestClassificationIsolation(unittest.TestCase):
    def test_import_and_classification_have_no_platform_or_io_side_effects(self):
        source = Path(cc_classify.__file__).resolve().parent
        script = r"""
import builtins
import os
import sys

sys.path.insert(0, sys.argv[1])
for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
    os.environ[key] = sys.argv[2]

blocked = {
    "cc_core", "translator", "i18n", "cc_providers", "cc_warm",
    "tkinter", "_tkinter", "win32util", "winreg", "ctypes", "pynput",
    "socket", "subprocess",
}
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.split(".")[0] in blocked:
        raise AssertionError("classifier imported platform/provider dependency")
    return original_import(name, *args, **kwargs)

def audit(event, args):
    if event == "import" and args[0].split(".")[0] in blocked:
        raise AssertionError("classifier imported platform/provider dependency")
    if event == "open":
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("classifier attempted to write a file")
    if event in {"os.mkdir", "os.rename", "os.remove", "os.rmdir",
                 "os.system", "subprocess.Popen"} or event.startswith("socket."):
        raise AssertionError("classifier performed external IO")

before = set(sys.modules)
builtins.__import__ = guarded_import
sys.addaudithook(audit)
import cc_classify
assert cc_classify.classify_selection("def foo():\n    pass") == "code"
assert cc_classify.classify_selection("ordinary prose") == "text"
assert cc_classify.classify_selection("This is prose\ncode();\nmore prose") == "mixed"
assert not blocked.intersection(set(sys.modules) - before)
print("isolated classifier passed")
"""
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "absent-user-data"
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(source), str(user_data)],
                cwd=directory, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "isolated classifier passed")
            self.assertEqual(result.stderr, "")
            self.assertFalse(user_data.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])
