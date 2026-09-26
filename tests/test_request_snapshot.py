"""Portable request snapshots; no backend, user configuration, or GUI access."""

from collections import UserDict
from dataclasses import MISSING, FrozenInstanceError, fields, replace
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest

import cc_request
from cc_providers.base import ProviderRequest, ProviderSelection
from cc_request import RequestSnapshot


def snapshot(**overrides):
    values = dict(
        request=ProviderRequest("translate", None, "fixed prompt", "  中🙂\n"),
        selection=ProviderSelection("claude_cli", None),
        config={},
        input="  中🙂\n",
        origin="text",
        content_class="text",
        kind="text",
        sig="claude_cli|auto|auto|sum0|zh",
        direction="auto",
        app_language="en_US",
        target_lang="zh",
        summarize=False,
        dictionary=False,
        stream_enabled=True,
    )
    values.update(overrides)
    return RequestSnapshot(**values)


class MutableString(str):
    pass


class MutableInt(int):
    pass


class MutableFloat(float):
    pass


class MutableCustom:
    def __init__(self):
        self.values = []

    def __copy__(self):
        raise AssertionError("unsupported values must not be copied")

    def __deepcopy__(self, memo):
        raise AssertionError("unsupported values must not be deep-copied")


class TestRequestSnapshot(unittest.TestCase):
    def test_fixed_fields_and_only_action_has_a_default(self):
        contract = fields(RequestSnapshot)
        self.assertEqual(tuple(field.name for field in contract), (
            "request", "selection", "config", "input", "origin", "content_class",
            "kind", "sig", "direction", "app_language", "target_lang",
            "summarize", "dictionary", "stream_enabled", "action",
        ))
        for field in contract[:-1]:
            with self.subTest(field=field.name):
                self.assertIs(field.default, MISSING)
                self.assertIs(field.default_factory, MISSING)
        self.assertEqual(contract[-1].default, "translation")
        self.assertIs(contract[-1].default_factory, MISSING)

    def test_fields_cannot_be_assigned_or_deleted(self):
        captured = snapshot()
        for field in fields(captured):
            with self.subTest(field=field.name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(captured, field.name, None)
                with self.assertRaises(FrozenInstanceError):
                    delattr(captured, field.name)

    def test_nested_caller_mutations_are_isolated(self):
        inner = {"text": "first", "values": [1, {"nested": ["中"]}]}
        original = {"future": [inner], "tuple": ({"list": [False]},)}
        captured = snapshot(config=original)
        inner["text"] = "changed"
        inner["values"][1]["nested"].append("new")
        inner["values"].clear()
        original["future"].append("extra")
        original["tuple"][0]["list"][0] = True
        original["new"] = "not captured"
        self.assertEqual(captured.config, {
            "future": ({"text": "first", "values": (1, {"nested": ("中",)})},),
            "tuple": ({"list": (False,)},),
        })
        self.assertIsNot(captured.config, original)
        self.assertIsNot(captured.config["future"][0], inner)

    def test_mappings_and_sequences_are_deeply_read_only(self):
        captured = snapshot(config={"outer": [{"inner": [0, {"last": []}]}]})
        outer = captured.config["outer"]
        inner = outer[0]["inner"]
        for mapping in (captured.config, outer[0], inner[1]):
            with self.subTest(mapping=mapping):
                self.assertIsInstance(mapping, MappingProxyType)
                with self.assertRaises(TypeError):
                    mapping["new"] = 1
                with self.assertRaises(TypeError):
                    del mapping[next(iter(mapping))]
        for sequence in (outer, inner, inner[1]["last"]):
            with self.subTest(sequence=sequence):
                self.assertIs(type(sequence), tuple)
                with self.assertRaises(TypeError):
                    sequence[0] = "changed"
                with self.assertRaises(AttributeError):
                    sequence.append("changed")

    def test_read_only_and_general_mapping_inputs_are_copied(self):
        raw = {"nested": UserDict({"items": ["original"]})}
        captured = snapshot(config=MappingProxyType(raw))
        raw["nested"]["items"].append("changed")
        raw["new"] = 1
        self.assertEqual(captured.config, {"nested": {"items": ("original",)}})
        self.assertIsInstance(captured.config["nested"], MappingProxyType)

    def test_dict_subclass_is_copied_without_retaining_attributes(self):
        class RawConfig(dict):
            pass

        raw = RawConfig(unknown=["first"])
        raw.custom = []
        captured = snapshot(config=raw)
        raw["unknown"].append("second")
        raw.custom.append("mutable")
        self.assertEqual(captured.config, {"unknown": ("first",)})
        self.assertFalse(hasattr(captured.config, "custom"))

    def test_unknown_keys_order_falsey_and_unicode_are_preserved(self):
        raw = {
            "future-中🙂": {"second": " e\u0301\r\n ", "first": "中文"},
            "font_size": "16",
            "none": None,
            "false": False,
            "zero": 0,
            "float_zero": 0.0,
            "empty": "",
            "empty_list": [],
            "empty_dict": {},
        }
        captured = snapshot(config=raw)
        self.assertEqual(tuple(captured.config), tuple(raw))
        self.assertEqual(tuple(captured.config["future-中🙂"]), ("second", "first"))
        self.assertEqual(captured.config["future-中🙂"]["second"], " e\u0301\r\n ")
        self.assertEqual(captured.config["future-中🙂"]["first"], "中文")
        self.assertEqual(captured.config["font_size"], "16")
        for key in ("none", "false", "zero", "float_zero", "empty"):
            with self.subTest(key=key):
                self.assertIs(captured.config[key], raw[key])
        self.assertEqual(captured.config["empty_list"], ())
        self.assertEqual(captured.config["empty_dict"], {})
        self.assertEqual(set(captured.config), set(raw))

    def test_numbers_are_not_normalized_or_rejected(self):
        raw = {
            "large": 10 ** 100,
            "negative_zero": -0.0,
            "nan": float("nan"),
            "infinity": float("inf"),
            "negative_infinity": float("-inf"),
        }
        captured = snapshot(config=raw)
        for key, value in raw.items():
            with self.subTest(key=key):
                self.assertIs(captured.config[key], value)
        self.assertTrue(math.isnan(captured.config["nan"]))
        self.assertEqual(math.copysign(1, captured.config["negative_zero"]), -1)

    def test_shared_children_are_not_mistaken_for_cycles(self):
        child = {"values": ["first"]}
        captured = snapshot(config={"first": child, "second": [child]})
        child["values"].append("later")
        self.assertEqual(captured.config["first"], {"values": ("first",)})
        self.assertEqual(captured.config["second"], ({"values": ("first",)},))

    def test_provider_contract_objects_and_image_list_are_copied(self):
        paths = ["合成一.png", "合成二.png"]
        request = ProviderRequest("translate", "model", "prompt", "text", paths, 13)
        selection = ProviderSelection("claude_cli", "model")
        captured = snapshot(request=request, selection=selection)
        self.assertIs(type(captured.request), ProviderRequest)
        self.assertIs(type(captured.selection), ProviderSelection)
        self.assertIsNot(captured.request, request)
        self.assertIsNot(captured.selection, selection)
        self.assertIs(request.image_paths, paths)
        self.assertIs(type(captured.request.image_paths), tuple)
        paths[0] = "changed"
        paths.append("third")
        object.__setattr__(request, "model", "changed")
        object.__setattr__(selection, "model", "changed")
        self.assertEqual(captured.request, ProviderRequest(
            "translate", "model", "prompt", "text", ("合成一.png", "合成二.png"), 13,
        ))
        self.assertEqual(captured.selection, ProviderSelection("claude_cli", "model"))

    def test_provider_copy_remains_frozen(self):
        captured = snapshot(request=ProviderRequest(
            "translate", None, "", "", ("one.png",),
        ))
        with self.assertRaises(FrozenInstanceError):
            captured.request.user_text = "changed"
        with self.assertRaises(FrozenInstanceError):
            captured.selection.model = "changed"
        with self.assertRaises(TypeError):
            captured.request.image_paths[0] = "changed"

    def test_provider_request_original_api_is_unchanged(self):
        paths = ["first"]
        original = ProviderRequest("translate", None, "prompt", "text", paths)
        self.assertEqual(original.timeout_seconds, 60.0)
        self.assertIs(original.image_paths, paths)
        snapshot(request=original)
        self.assertIs(original.image_paths, paths)
        self.assertIs(cc_request.ProviderRequest, ProviderRequest)
        with self.assertRaises(FrozenInstanceError):
            original.task = "changed"
        changed = replace(original, user_text="different", timeout_seconds=90)
        self.assertIs(changed.image_paths, paths)
        self.assertEqual(original.user_text, "text")
        self.assertEqual(changed.user_text, "different")

    def test_timeout_copies_for_one_shot_and_streaming(self):
        captured = snapshot(request=ProviderRequest(
            "translate", "model", "prompt", "text", ["one.png"], 17.5,
        ))
        for seconds in (60, 90.0):
            with self.subTest(seconds=seconds):
                request = captured.with_timeout(seconds)
                self.assertIs(type(request), ProviderRequest)
                self.assertIsNot(request, captured.request)
                self.assertEqual(request, replace(captured.request, timeout_seconds=seconds))
                self.assertIs(type(request.timeout_seconds), type(seconds))
                self.assertEqual(captured.request.timeout_seconds, 17.5)
                with self.assertRaises(FrozenInstanceError):
                    request.timeout_seconds = 1

    def test_timeout_does_not_introduce_numeric_normalization(self):
        captured = snapshot()
        for seconds in (0, -2, -0.0, float("nan"), float("inf")):
            with self.subTest(seconds=seconds):
                self.assertIs(captured.with_timeout(seconds).timeout_seconds, seconds)
        self.assertEqual(captured.request.timeout_seconds, 60.0)

    def test_history_metadata_has_exact_keys_order_and_captured_values(self):
        for input_text in (None, "", "  中🙂\n"):
            with self.subTest(input=input_text):
                captured = snapshot(input=input_text, origin="ocr", kind="", sig=" 中 ")
                metadata = captured.history_metadata
                self.assertIsInstance(metadata, MappingProxyType)
                self.assertEqual(tuple(metadata), ("input", "origin", "is_code", "kind", "sig"))
                self.assertEqual(metadata, {
                    "input": input_text, "origin": "ocr", "is_code": False,
                    "kind": "", "sig": " 中 ",
                })

    def test_history_metadata_code_flag_depends_only_on_content_class(self):
        for content_class in ("code", "text", "mixed", "Code", "", "future"):
            with self.subTest(content_class=content_class):
                captured = snapshot(
                    content_class=content_class, origin="code", kind="code",
                    config={"is_code": content_class != "code"},
                )
                self.assertIs(captured.history_metadata["is_code"], content_class == "code")

    def test_history_metadata_is_read_only_and_copies_are_independent(self):
        captured = snapshot()
        metadata = captured.history_metadata
        for key in metadata:
            with self.subTest(key=key):
                with self.assertRaises(TypeError):
                    metadata[key] = "changed"
                with self.assertRaises(TypeError):
                    del metadata[key]
        with self.assertRaises(FrozenInstanceError):
            captured.history_metadata = {}
        copied = metadata.copy()
        copied["input"] = "changed"
        copied["history_enabled"] = True
        fresh = captured.history_metadata
        self.assertIsNot(fresh, metadata)
        self.assertEqual(fresh, metadata)
        self.assertEqual(fresh["input"], captured.input)
        self.assertNotIn("history_enabled", fresh)

    def test_history_metadata_never_exposes_history_or_cancellation_policy(self):
        captured = snapshot(config={
            "history_enabled": True, "history_limit": 10, "limit": 20,
            "cancel_event": False, "cancelled": True, "ui_session": "raw",
            "job_id": 12, "history_write_allowed": True,
        })
        self.assertTrue(set(captured.config).isdisjoint(captured.history_metadata))
        self.assertEqual(tuple(captured.history_metadata), (
            "input", "origin", "is_code", "kind", "sig",
        ))
        disabled = replace(captured, config={"history_enabled": False, "history_limit": 0})
        self.assertEqual(disabled.history_metadata, captured.history_metadata)

    def test_metadata_is_captured_without_policy(self):
        captured = snapshot(
            input=None, origin="", content_class="", kind="", sig="  中🙂  ",
            direction="future-direction", app_language="", target_lang=None,
            summarize=True, dictionary=True, stream_enabled=False, action="future-action",
        )
        self.assertIsNone(captured.input)
        self.assertEqual(captured.origin, "")
        self.assertEqual(captured.content_class, "")
        self.assertEqual(captured.kind, "")
        self.assertEqual(captured.sig, "  中🙂  ")
        self.assertEqual(captured.direction, "future-direction")
        self.assertEqual(captured.app_language, "")
        self.assertIsNone(captured.target_lang)
        self.assertTrue(captured.summarize)
        self.assertTrue(captured.dictionary)
        self.assertFalse(captured.stream_enabled)
        self.assertEqual(captured.action, "future-action")
        self.assertEqual(snapshot(input="", target_lang="").input, "")
        self.assertEqual(snapshot(input="", target_lang="").target_lang, "")

    def test_operational_names_remain_raw_config_not_snapshot_permissions(self):
        raw = {
            "cancel_event": False, "ui_session": {"unknown": []},
            "job_id": 12, "history_write_allowed": True,
        }
        captured = snapshot(config=raw)
        self.assertEqual(captured.config, {
            "cancel_event": False, "ui_session": {"unknown": ()},
            "job_id": 12, "history_write_allowed": True,
        })
        for name in raw:
            with self.subTest(name=name):
                self.assertFalse(hasattr(captured, name))
        with self.assertRaises(TypeError):
            snapshot(job_id=12)

    def test_invalid_config_root_is_rejected(self):
        for value in (None, [], (), "{}", 0, SimpleNamespace(), MutableCustom()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "config must be a mapping"):
                    snapshot(config=value)

    def test_unsupported_nested_values_are_rejected_not_copied(self):
        for value in (
            set(), frozenset(), bytearray(b"mutable"), b"bytes",
            MutableCustom(), SimpleNamespace(values=[]), object(),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, r"config\.value\[0\].*unsupported type"):
                    snapshot(config={"nested": [value]})

    def test_configuration_errors_do_not_include_caller_keys_or_values(self):
        key = "SYNTHETIC_PRIVATE_KEY"
        cycle = {}
        cycle[key] = cycle
        for raw, error in (({key: [bytearray(b"SYNTHETIC_PRIVATE_VALUE")]}, TypeError),
                           (cycle, ValueError)):
            with self.subTest(error=error.__name__):
                with self.assertRaises(error) as raised:
                    snapshot(config=raw)
                self.assertNotIn(key, str(raised.exception))
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", str(raised.exception))

    def test_invalid_mapping_keys_are_rejected_at_every_depth(self):
        key = MutableString("custom-key")
        key.mutable = []
        for value in (None, 1, False, ("tuple",), key):
            for nested in (False, True):
                with self.subTest(key=value, nested=nested):
                    raw = {value: "value"}
                    if nested:
                        raw = {"nested": [raw]}
                    with self.assertRaisesRegex(TypeError, "key must be str"):
                        snapshot(config=raw)

    def test_config_scalar_subclasses_cannot_hide_mutable_references(self):
        for value in (MutableString("text"), MutableInt(7), MutableFloat(1.5)):
            value.mutable = []
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "unsupported type"):
                    snapshot(config={"nested": {"value": value}})

    def test_cycles_raise_explicit_value_errors(self):
        mapping = {}
        mapping["self"] = mapping
        sequence = []
        sequence.append(sequence)
        mixed = []
        mixed_tuple = ({"loop": mixed},)
        mixed.append(mixed_tuple)
        proxy_backing = {}
        proxy = MappingProxyType(proxy_backing)
        proxy_backing["loop"] = proxy
        for value in (mapping, sequence, mixed_tuple, proxy):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(ValueError, "cycle detected at config"):
                    snapshot(config={"value": value})

    def test_wrong_provider_contract_types_are_rejected(self):
        for name in ("request", "selection"):
            for value in (None, {}, [], SimpleNamespace(), MutableCustom()):
                with self.subTest(name=name, value=type(value).__name__):
                    with self.assertRaisesRegex(TypeError, f"{name} must be Provider"):
                        snapshot(**{name: value})

    def test_request_text_fields_reject_mutable_and_wrong_types(self):
        original = ProviderRequest("translate", None, "prompt", "text")
        for name in ("task", "model", "system_prompt", "user_text"):
            for value in ([], {}, MutableCustom(), MutableString("hidden"), 0, False):
                with self.subTest(name=name, value=type(value).__name__):
                    with self.assertRaisesRegex(TypeError, rf"request\.{name} must be"):
                        snapshot(request=replace(original, **{name: value}))
        for name in ("task", "system_prompt", "user_text"):
            with self.subTest(name=name):
                with self.assertRaises(TypeError):
                    snapshot(request=replace(original, **{name: None}))

    def test_selection_fields_reject_mutable_and_wrong_types(self):
        original = ProviderSelection("claude_cli", None)
        for name in ("provider_id", "model"):
            for value in ([], {}, MutableCustom(), MutableString("hidden"), 0, False):
                with self.subTest(name=name, value=type(value).__name__):
                    with self.assertRaisesRegex(TypeError, rf"selection\.{name} must be"):
                        snapshot(selection=replace(original, **{name: value}))
        with self.assertRaises(TypeError):
            snapshot(selection=ProviderSelection(None, None))

    def test_image_paths_require_a_sequence_of_plain_strings(self):
        original = ProviderRequest("translate", None, "prompt", "text")
        for value in ("path.png", b"path.png", {"path.png"}, {"path": 1}, None):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, r"request\.image_paths must be"):
                    snapshot(request=replace(original, image_paths=value))
        for value in ([], {}, None, 1, MutableString("path.png"), MutableCustom()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, r"request\.image_paths\[0\] must be str"):
                    snapshot(request=replace(original, image_paths=[value]))

    def test_timeouts_reject_mutable_values_and_scalar_subclasses(self):
        original = ProviderRequest("translate", None, "prompt", "text")
        captured = snapshot()
        for value in ([], {}, None, "60", True, MutableInt(60), MutableFloat(90)):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, r"request\.timeout_seconds must be"):
                    snapshot(request=replace(original, timeout_seconds=value))
                with self.assertRaisesRegex(TypeError, "seconds must be"):
                    captured.with_timeout(value)

    def test_snapshot_text_fields_reject_hidden_mutable_values(self):
        for name in (
            "input", "origin", "content_class", "kind", "sig", "direction",
            "app_language", "target_lang", "action",
        ):
            for value in ([], {}, MutableString("hidden"), MutableCustom(), False, 0):
                with self.subTest(name=name, value=type(value).__name__):
                    with self.assertRaisesRegex(TypeError, f"{name} must be"):
                        snapshot(**{name: value})
            if name not in ("input", "target_lang"):
                with self.subTest(name=name, value=None):
                    with self.assertRaises(TypeError):
                        snapshot(**{name: None})

    def test_boolean_fields_are_not_coerced(self):
        for name in ("summarize", "dictionary", "stream_enabled"):
            for value in (None, 0, 1, "", "false", [], {}, MutableCustom()):
                with self.subTest(name=name, value=type(value).__name__):
                    with self.assertRaisesRegex(TypeError, f"{name} must be bool"):
                        snapshot(**{name: value})

    def test_replacing_snapshot_refreezes_config_without_changing_original(self):
        captured = snapshot(config={"nested": ["first"]})
        updated = replace(captured, stream_enabled=False)
        self.assertIsNot(updated.config, captured.config)
        self.assertIsNot(updated.request, captured.request)
        self.assertIsNot(updated.selection, captured.selection)
        self.assertEqual(updated.config, captured.config)
        self.assertFalse(updated.stream_enabled)
        self.assertTrue(captured.stream_enabled)


class TestRequestSnapshotImport(unittest.TestCase):
    def test_isolated_import_and_capture_have_no_user_or_platform_side_effects(self):
        source = Path(cc_request.__file__).resolve().parent
        script = r"""
import builtins
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
home = os.path.abspath(sys.argv[2])
for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
    os.environ[key] = home

blocked = {
    "cc_core", "cc_app", "translator", "i18n", "cc_warm", "cc_config",
    "cc_providers.claude_cli", "cc_providers.codex_cli",
    "cc_providers.codex_appserver", "cc_providers.codex_config",
    "cc_providers.codex_catalog", "cc_providers.darwin_process",
    "tkinter", "_tkinter", "win32util", "winreg", "ctypes", "pynput",
    "socket", "subprocess", "fcntl",
}
original_import = builtins.__import__

def forbidden_import(name):
    return name in blocked or name.split(".")[0] in blocked or name.startswith("win32")

def guarded_import(name, *args, **kwargs):
    if forbidden_import(name):
        raise AssertionError("request snapshot imported forbidden dependency: " + name)
    return original_import(name, *args, **kwargs)

def audit(event, args):
    if event == "import" and forbidden_import(args[0]):
        raise AssertionError("request snapshot imported forbidden dependency: " + args[0])
    if event == "open":
        if isinstance(args[0], (str, bytes)):
            path = os.path.abspath(os.fsdecode(args[0]))
            if path == home or path.startswith(home + os.sep):
                raise AssertionError("request snapshot accessed user data")
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("request snapshot wrote a file")
    # Import machinery lists source/library directories; only user scans are forbidden.
    if event in {"os.listdir", "os.scandir"} and isinstance(args[0], (str, bytes)):
        path = os.path.abspath(os.fsdecode(args[0]))
        if path == home or path.startswith(home + os.sep):
            raise AssertionError("request snapshot scanned user data")
    if event in {
        "os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.system",
        "subprocess.Popen",
    } or event.startswith(("socket.", "sqlite3.connect")):
        raise AssertionError("request snapshot performed external IO: " + event)

class NoEnvironment:
    def __getattribute__(self, name):
        raise AssertionError("request snapshot read environment")

def no_getenv(*args, **kwargs):
    raise AssertionError("request snapshot read environment")

before = set(sys.modules)
builtins.__import__ = guarded_import
sys.addaudithook(audit)
original_environment, original_getenv = os.environ, os.getenv
os.environ, os.getenv = NoEnvironment(), no_getenv
try:
    from cc_request import RequestSnapshot
    from cc_providers.base import ProviderRequest, ProviderSelection
    raw = {"future": [{"text": "\u4e2d", "flag": False}], "font_size": "16"}
    request = ProviderRequest("translate", None, "prompt", "synthetic", ["one.png"])
    selection = ProviderSelection("claude_cli", None)
    captured = RequestSnapshot(
        request, selection, raw, "synthetic", "text", "text", "text", "sig",
        "auto", "en_US", "zh", False, False, True,
    )
    raw["future"][0]["text"] = "changed"
    request.image_paths.append("changed")
    assert captured.config["future"][0]["text"] == "\u4e2d"
    assert captured.config["future"][0]["flag"] is False
    assert captured.config["font_size"] == "16"
    assert captured.request.image_paths == ("one.png",)
    assert captured.request is not request and captured.selection is not selection
    assert captured.with_timeout(90).timeout_seconds == 90
    assert captured.request.timeout_seconds == 60
    assert tuple(captured.history_metadata) == ("input", "origin", "is_code", "kind", "sig")
    assert captured.history_metadata["is_code"] is False
    assert not blocked.intersection(set(sys.modules) - before)
finally:
    os.environ, os.getenv = original_environment, original_getenv
print("isolated request snapshot passed")
"""
        with tempfile.TemporaryDirectory(prefix=".request-snapshot-test-", dir=source) as directory:
            user_data = Path(directory) / "absent-user-data"
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(source), str(user_data)],
                cwd=directory, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "isolated request snapshot passed")
            self.assertEqual(result.stderr, "")
            self.assertFalse(user_data.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])
