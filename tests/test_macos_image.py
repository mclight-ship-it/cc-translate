"""Explicit image request, bounded-copy and native service contracts using only private synthetic PNGs."""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zlib

from cc_config import CFG, Config
from cc_direction import DIRECTION_MODES
from cc_macos import image, image_fixture, translation
from cc_macos.configuration import ConfigurationError
from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, encode_frame
from cc_macos.server import Server
from cc_prompts import image_translation_prompt
from cc_providers.base import ProviderResult
from cc_providers.darwin_process import ProcessError

if __package__:
    from .test_macos_translation import _TranslationDirectory, OUTPUT, request as text_request
    from .test_macos_configuration import message
else:
    from test_macos_translation import _TranslationDirectory, OUTPUT, request as text_request
    from test_macos_configuration import message


def request(path, **changes):
    return {"operation": "translate_image", "image_path": str(path), "image_bytes": len(image_fixture.PNG_BYTES),
            "image_sha256": hashlib.sha256(image_fixture.PNG_BYTES).hexdigest(), "app_language": "en_US",
            "record_history": True} | changes


class ImageContracts(unittest.TestCase):
    def test_exact_fields_types_absolute_path_and_80mib_boundary(self):
        path = Path.cwd() / "synthetic.png"
        for size in (1, image.MAX_IMAGE_BYTES):
            image.validate_image_request(request(path, image_bytes=size))
        self.assertEqual(image.MAX_IMAGE_BYTES, 83886080)
        for changes in (
                {"operation": "translate"}, {"text": "OCR"}, {"image_paths": []}, {"use_cache": False},
                {"image_path": None}, {"image_path": []}, {"image_path": "relative.png"},
                {"image_path": str(path) + "\0"}, {"image_path": str(path) + "\ud800"},
                {"image_bytes": True}, {"image_bytes": 0}, {"image_bytes": -1}, {"image_bytes": 1.0},
                {"image_sha256": "A" * 64}, {"image_sha256": "a" * 63}, {"image_sha256": []},
                {"app_language": []}, {"app_language": "fr"}, {"record_history": 1}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ProtocolError, "^invalid_image_translation$"):
                image.validate_image_request(request(path, **changes))
        for key in request(path):
            payload = request(path)
            del payload[key]
            with self.assertRaisesRegex(ProtocolError, "^invalid_image_translation$"):
                image.validate_image_request(payload)
        with self.assertRaisesRegex(ProtocolError, "^image_too_large$"):
            image.validate_image_request(request(path, image_bytes=image.MAX_IMAGE_BYTES + 1))
        self.assertLess(len(encode_frame(message("image", "request", **request(path)))), MAX_FRAME_BYTES)

    def test_snapshot_freezes_exact_model_direction_and_has_no_original_or_ocr_text(self):
        path, owned = Path.cwd() / "source.png", str(Path.cwd() / "owned" / "region.png")
        for direction in DIRECTION_MODES:
            for language in ("en_US", "zh_CN"):
                for model in ("auto", "auto-fast", "gpt-5.4-mini", "model-\u00e9", "model-e\u0301"):
                    config = Config()
                    config.update(codex_model=model, direction=direction, language=language,
                                  summary_enabled=True, local_dictionary_enabled=True, max_chars=0)
                    snapshot = translation.snapshot_for_image(config, request(path), owned)
                    self.assertEqual((snapshot.request.task, snapshot.request.model, snapshot.request.image_paths),
                                     ("image", model, (owned,)))
                    self.assertEqual((snapshot.input, snapshot.origin, snapshot.content_class, snapshot.kind),
                                     (None, "ocr", "ocr", "ocr"))
                    self.assertFalse(snapshot.summarize)
                    self.assertFalse(snapshot.dictionary)
                    self.assertEqual(snapshot.target_lang, None if direction == "auto" else direction[3:])
                    self.assertEqual(snapshot.request.system_prompt, image_translation_prompt(direction, language))
                    self.assertIsNone(snapshot.history_metadata["input"])
                    self.assertNotIn(str(path), snapshot.request.user_text)
                    config["codex_model"] = "later"
                    self.assertEqual(snapshot.selection.model.encode(), model.encode())
                    self.assertEqual(snapshot.config[CFG.CODEX_MODEL], model)

    def test_image_operation_is_only_available_on_explicit_native_connection(self):
        for config in (None, Mock(translation_enabled=False)):
            output = io.BytesIO()
            server = Server(io.BytesIO(), output, io.StringIO(), configuration=config)
            server._handle(message("hello", "hello"))
            server._handle(message("image", "request", **request(Path.cwd() / "source.png")))
            events = [json.loads(raw) for raw in output.getvalue().splitlines()]
            self.assertNotIn("translate_image", events[0]["payload"]["capabilities"])
            self.assertEqual(events[-1]["payload"], {"code": "unsupported_operation"})


class OwnedPNGTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".owned-png-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source \u4e2d.png"
        self.source.write_bytes(image_fixture.PNG_BYTES)
        self.owner = image.OwnedPNG(self.root / "workspace")
        self.addCleanup(self.owner.close)
        self.cancel = threading.Event()

    def prepare(self, **changes):
        return self.owner.prepare(request(self.source, **changes), self.cancel)

    def test_constructor_and_precancel_do_not_create_files_or_read_source(self):
        with patch.object(os, "open", side_effect=AssertionError("unexpected open")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")):
            owner = image.OwnedPNG(self.root / "new")
            owner.close()
            self.cancel.set()
            with self.assertRaises(image.ImageCancelled):
                self.prepare()
        self.assertFalse((self.root / "workspace").exists())

    def test_copy_is_independent_byte_exact_private_and_exactly_cleaned(self):
        path = Path(self.prepare())
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), image_fixture.PNG_BYTES)
        self.assertEqual(path.name, "region.png")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.source.write_bytes(b"source replaced after capture")
        self.assertEqual(path.read_bytes(), image_fixture.PNG_BYTES)
        self.owner.close()
        self.owner.close()
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.source.read_bytes(), b"source replaced after capture")

    def test_missing_directory_special_and_symlink_sources_fail_without_opening(self):
        with patch.object(os, "open", side_effect=AssertionError("special file opened")):
            with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                self.owner.prepare(request(self.root / "missing"), self.cancel)
            with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                self.owner.prepare(request(self.root), self.cancel)
            for mode in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFLNK):
                with patch.object(os, "lstat", return_value=Mock(st_mode=mode)):
                    with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                        self.prepare()

    def test_size_hash_and_actual_oversize_fail_before_any_usable_copy(self):
        for changes in ({"image_bytes": len(image_fixture.PNG_BYTES) + 1}, {"image_sha256": "0" * 64}):
            with self.subTest(changes=changes), self.assertRaisesRegex(image.ImageError, "^image_changed$"):
                self.prepare(**changes)
            self.owner.close()
        with patch.object(image, "MAX_IMAGE_BYTES", 16):
            with self.assertRaisesRegex(image.ImageError, "^image_too_large$"):
                self.prepare(image_bytes=1)

    def test_source_change_during_bounded_copy_is_detected(self):
        actual = os.read
        changed = False
        def read(fd, count):
            nonlocal changed
            data = actual(fd, count)
            self.assertLessEqual(count, 65536)
            if not changed:
                changed = True
                with self.source.open("ab") as target:
                    target.write(b"x")
            return data
        with patch.object(os, "read", side_effect=read):
            with self.assertRaisesRegex(image.ImageError, "^image_changed$"):
                self.prepare()
        self.owner.close()
        self.assertEqual(list((self.root / "workspace").iterdir()), [])

    def test_cancel_during_copy_closes_descriptors_and_removes_only_owned_files(self):
        actual = os.read
        def read(fd, count):
            data = actual(fd, count)
            self.cancel.set()
            return data
        with patch.object(os, "read", side_effect=read):
            with self.assertRaises(image.ImageCancelled):
                self.prepare()
        self.owner.close()
        self.assertEqual(self.owner._fds, set())
        self.assertTrue(self.source.exists())
        self.assertFalse(self.owner.directory)

    def test_invalid_png_signature_crc_truncation_and_trailing_data_are_rejected(self):
        original = image_fixture.PNG_BYTES
        for data in (b"not a PNG", image.PNG_SIGNATURE, original[:-1], original + b"x",
                     original[:35] + b"bad!" + original[39:]):
            with self.subTest(data=data[:12]):
                self.source.write_bytes(data)
                with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                    self.prepare(image_bytes=len(data), image_sha256=hashlib.sha256(data).hexdigest())
                self.owner.close()

    def test_standard_png_color_depths_and_unknown_ancillary_chunks_are_not_model_restricted(self):
        for color, depth, channels in ((0, 16, 1), (2, 16, 3), (3, 1, 1), (4, 8, 2), (6, 8, 4)):
            data = (image.PNG_SIGNATURE
                    + image_fixture.png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, depth, color, 0, 0, 0))
                    + (image_fixture.png_chunk(b"PLTE", b"\0\0\0") if color == 3 else b"")
                    + image_fixture.png_chunk(b"vpAg", b"future ancillary metadata")
                    + image_fixture.png_chunk(b"IDAT", zlib.compress(b"\0" * (1 + (depth * channels + 7) // 8)))
                    + image_fixture.png_chunk(b"IEND", b""))
            self.source.write_bytes(data)
            self.assertEqual(Path(self.prepare(image_bytes=len(data), image_sha256=hashlib.sha256(data).hexdigest())).read_bytes(), data)
            self.owner.close()

    def test_read_write_and_cleanup_errors_are_sanitized_and_owned_cleanup_can_retry(self):
        with patch.object(os, "open", side_effect=PermissionError("PRIVATE SOURCE PATH")):
            with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                self.prepare()
        with patch.object(os, "write", side_effect=OSError("PRIVATE WRITE")):
            with self.assertRaisesRegex(image.ImageError, "^image_unavailable$"):
                self.prepare()
        self.owner.close()
        path = Path(self.prepare())
        with patch.object(os, "unlink", side_effect=PermissionError("PRIVATE IMAGE PATH")):
            with self.assertRaisesRegex(image.ImageError, "^image_cleanup_failed$"):
                self.owner.close()
        self.assertTrue(path.exists())
        self.owner.close()
        self.assertFalse(path.exists())

    def test_cleanup_refuses_replaced_directory_and_does_not_remove_unowned_child(self):
        path = Path(self.prepare())
        saved = path.parent.with_name("original")
        path.parent.rename(saved)
        path.parent.mkdir()
        foreign = path.parent / "region.png"
        foreign.write_bytes(b"unowned")
        with self.assertRaisesRegex(image.ImageError, "^image_cleanup_failed$"):
            self.owner.close()
        self.assertEqual(foreign.read_bytes(), b"unowned")
        foreign.unlink()
        path.parent.rmdir()
        saved.rename(path.parent)
        self.owner.close()


class ImageServiceTests(_TranslationDirectory):
    def setUp(self):
        super().setUp()
        self.source = self.home / "selected-region.png"
        self.source.write_bytes(image_fixture.PNG_BYTES)

    def send_image(self, id_="image", **changes):
        self.server._handle(message(id_, "request", **request(self.source, **changes)))

    def copies(self):
        return list((self.directory / "NativeWorkspace").glob(".cc-image-*"))

    def test_image_helper_timing_includes_owned_copy_cleanup_and_final_history(self):
        clock = [10]
        release, record, stream = self.session._release_image, self.session._record, self.provider.stream
        def model(*args):
            clock[0] += 2
            return stream(*args)
        def cleanup(*args):
            clock[0] += 4
            return release(*args)
        def history(*args):
            clock[0] += 8
            return record(*args)
        with patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.provider, "stream", side_effect=model), \
                patch.object(self.session, "_release_image", side_effect=cleanup), \
                patch.object(self.session, "_record", side_effect=history):
            event, result = self.session.translate_image(
                request(self.source), threading.Event(), lambda text: None, lambda: True)
        self.assertEqual(event, "completed")
        self.assertEqual(result["timings"], {"cache_hit": 0, "helper_elapsed_ms": 14_000})
        self.assertEqual(self.copies(), [])
        self.assertEqual(len(self.history()), 1)

    def test_loaded_nonpositive_text_limits_do_not_block_genuine_image_service_requests(self):
        for limit in (0, -7):
            with self.subTest(limit=limit):
                self.session.perform({"operation": "config_save", "config": self.config | {CFG.MAX_CHARS: limit}})
                self.assertEqual(self.session.perform({"operation": "config_load"})["config"][CFG.MAX_CHARS], limit)
                before = self.path.read_bytes()
                id_ = "image" + str(limit)
                self.send_image(id_, record_history=False)
                self.assertTrue(self.stdout.terminal(id_))
                result = self.stdout.result(id_)
                self.assertEqual(result["type"], "completed")
                self.assertEqual((result["payload"]["kind"], result["payload"]["cached"],
                                  result["payload"]["history"]), ("ocr", False, "disabled"))
                self.assertEqual(self.provider.requests[-1].task, "image")
                self.assertEqual(len(self.provider.requests[-1].image_paths), 1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.copies(), [])
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(self.history(), [])

    def test_image_is_explicit_then_private_copy_streams_and_records_only_null_input_output(self):
        self.assertEqual(self.copies(), [])
        self.assertEqual(self.provider.requests, [])
        self.assertIn("translate_image", self.stdout.events[0]["payload"]["capabilities"])
        actual_stream = self.provider.stream
        captured = []
        def stream(req, on_delta, cancel):
            captured.append(req)
            self.assertEqual(Path(req.image_paths[0]).read_bytes(), image_fixture.PNG_BYTES)
            self.assertNotEqual(req.image_paths[0], str(self.source))
            self.source.write_bytes(b"caller changed after snapshot")
            self.assertEqual(Path(req.image_paths[0]).read_bytes(), image_fixture.PNG_BYTES)
            return actual_stream(req, on_delta, cancel)
        with patch.object(self.provider, "stream", side_effect=stream), \
                patch.object(self.session._history, "find_cached", side_effect=AssertionError("image cache")):
            self.send_image()
            self.assertTrue(self.stdout.terminal("image"))
        result = self.stdout.result("image")
        self.assertEqual(result["payload"], {
            "text": OUTPUT, "submitted": True, "cached": False, "kind": "ocr",
            "target_lang": None, "summarize": False, "history": "recorded", "history_error": None,
            "timings": result["payload"]["timings"]})
        entries = self.history()
        self.assertIsNone(entries[0]["input"])
        self.assertEqual((entries[0]["kind"], entries[0]["output"]), ("ocr", OUTPUT))
        self.assertFalse(entries[0]["is_dict"])
        self.assertFalse(entries[0]["is_code"])
        self.assertEqual(self.copies(), [])
        for sensitive in (str(self.source), captured[0].image_paths[0], request(self.source)["image_sha256"]):
            self.assertNotIn(json.dumps(sensitive, ensure_ascii=False)[1:-1], json.dumps(entries, ensure_ascii=False))
            self.assertNotIn(json.dumps(sensitive, ensure_ascii=False)[1:-1], self.stdout.getvalue().decode())
            self.assertNotIn(sensitive, self.stdout.getvalue().decode())
            self.assertNotIn(sensitive, self.stderr.getvalue())

    def test_missing_changed_oversized_or_invalid_image_never_submits_or_writes_history(self):
        for index, (changes, code) in enumerate((
                ({"image_path": str(self.home / "absent.png")}, "image_unavailable"),
                ({"image_bytes": 1}, "image_changed"), ({"image_sha256": "0" * 64}, "image_changed"),
                ({"image_bytes": image.MAX_IMAGE_BYTES + 1}, "image_too_large"))):
            self.send_image(str(index), **changes)
            self.assertTrue(self.stdout.terminal(str(index)))
            result = self.stdout.result(str(index))
            self.assertEqual(result["payload"]["code"], code)
            self.assertFalse(result["payload"].get("submitted", False))
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.copies(), [])
        self.assertEqual(self.history(), [])

    def test_explicit_record_optout_skips_history_and_never_hits_cache_on_repeat(self):
        self.path.write_text(json.dumps(self.config | {CFG.LOCAL_DICTIONARY_ENABLED: True, CFG.SUMMARY_ENABLED: True}))
        with patch.object(self.session._history, "find_cached", side_effect=AssertionError("image cache")), \
                patch.object(self.session, "_record", wraps=self.session._record) as record:
            for id_ in ("first", "second"):
                self.send_image(id_, record_history=False)
                self.assertTrue(self.stdout.terminal(id_))
                self.assertEqual(self.stdout.result(id_)["payload"]["history"], "disabled")
            self.assertEqual(record.call_count, 2)
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(self.history(), [])

    def test_corrupt_history_is_never_a_cache_source_and_only_requested_recording_reports_failure(self):
        history_path = self.directory / "history.json"
        history_path.write_bytes(b"{broken")
        for enabled, record in ((True, True), (True, False), (False, True)):
            id_ = str(enabled) + str(record)
            self.session.perform({"operation": "config_save", "config": self.config | {CFG.HISTORY_ENABLED: enabled}})
            self.send_image(id_, record_history=record)
            self.assertTrue(self.stdout.terminal(id_))
            result = self.stdout.result(id_)
            self.assertEqual(result["type"], "completed")
            self.assertEqual((result["payload"]["history"], result["payload"]["history_error"]),
                             ("failed", "invalid_history") if enabled and record else ("disabled", None))
            self.assertFalse(result["payload"]["cached"])
            self.assertEqual(history_path.read_bytes(), b"{broken")
            self.assertEqual(self.copies(), [])

    def test_model_direction_are_captured_and_live_history_optout_wins_after_copy(self):
        self.provider.release.clear()
        self.send_image()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.session.perform({"operation": "config_save", "config": self.config | {
                CFG.CODEX_MODEL: "new-model", CFG.DIRECTION: "to_ja", CFG.HISTORY_ENABLED: False}})
            captured = self.provider.requests[0]
            self.assertEqual(captured.model, "synthetic")
            self.assertEqual(captured.system_prompt, image_translation_prompt("auto", "en_US"))
            self.assertTrue(Path(captured.image_paths[0]).exists())
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("image"))
        self.assertEqual(self.stdout.result("image")["payload"]["history"], "disabled")
        self.assertEqual(self.copies(), [])

    def test_started_cancel_keeps_copy_until_provider_returns_then_cleans_before_terminal(self):
        self.provider.release.clear()
        self.send_image()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("cancel", "cancel", request_id="image"))
            self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
            self.assertTrue(Path(self.provider.requests[0].image_paths[0]).exists())
            self.assertFalse(any(e["id"] == "image" and e["type"] == "cancelled" for e in self.stdout.events))
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("image"))
        self.assertEqual(self.stdout.result("image")["payload"], {"submitted": True})
        self.assertEqual(self.copies(), [])
        self.assertEqual(self.history(), [])

    def test_prestart_cancel_does_not_read_source_copy_or_submit(self):
        captured = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: captured.append((req, payload)) or True):
            self.send_image()
        self.server._handle(message("cancel", "cancel", request_id="image"))
        with patch.object(image.OwnedPNG, "prepare", side_effect=AssertionError("image read")):
            self.server._translate(*captured[0])
        self.assertEqual(self.stdout.result("image")["payload"], {})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.copies(), [])

    def test_cleanup_failure_has_correct_submission_state_and_no_history_then_close_retries(self):
        actual = image.OwnedPNG.close
        for submitted in (False, True):
            with self.subTest(submitted=submitted):
                id_ = str(submitted)
                with patch.object(self.provider, "stream", return_value=ProviderResult(
                        submitted, text=OUTPUT, error_code="" if submitted else "failed",
                        metrics=(("turn_submitted", submitted),))), \
                        patch.object(image.OwnedPNG, "close", side_effect=image.ImageError("image_cleanup_failed")):
                    self.send_image(id_)
                    self.assertTrue(self.stdout.terminal(id_))
                self.assertEqual(self.stdout.result(id_)["payload"],
                                 {"code": "image_cleanup_failed", "submitted": submitted})
                self.assertTrue(self.copies())
                for owned in tuple(self.session._images):
                    actual(owned)
                    self.session._images.remove(owned)
        self.assertEqual(self.history(), [])

    def test_unproven_provider_cleanup_retains_copy_and_requires_strict_drain_before_deletion(self):
        with patch.object(self.provider, "stream", return_value=ProviderResult(
                False, error_code="provider_cleanup_failed", metrics=(("turn_submitted", True),))):
            self.send_image()
            self.assertTrue(self.stdout.terminal("image"))
        self.assertEqual(self.stdout.result("image")["payload"], {"code": "provider_cleanup_failed", "submitted": True})
        self.assertTrue(self.copies())
        actual = self.provider.shutdown
        def shutdown(*, require_cleanup=False):
            self.assertTrue(require_cleanup)
            self.assertTrue(self.copies())
            actual(require_cleanup=require_cleanup)
        with patch.object(self.provider, "shutdown", side_effect=shutdown):
            self.session.close()
        self.assertEqual(self.copies(), [])

    def test_unexpected_provider_outcome_stays_protocol_unknown_until_shutdown_drains(self):
        with patch.object(self.provider, "stream", side_effect=ProcessError("PRIVATE_UNKNOWN")):
            self.send_image()
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "image"], ["accepted", "started"])
        self.assertTrue(self.copies())
        self.assertTrue(self.server._stopping)
        self.assertEqual(self.stdout.result("protocol")["payload"], {"code": "internal_error"})
        self.session.close()
        self.assertEqual(self.copies(), [])

    def test_unknown_provider_oserror_also_requires_strict_shutdown_before_copy_deletion(self):
        with patch.object(self.provider, "stream", side_effect=OSError("PRIVATE_UNKNOWN")):
            self.send_image()
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "image"], ["accepted", "started"])
        self.assertTrue(self.session._undrained_images)
        self.assertTrue(self.copies())
        with patch.object(self.provider, "shutdown", side_effect=ProcessError("provider_cleanup_failed")):
            with self.assertRaisesRegex(ConfigurationError, "^provider_cleanup_failed$"):
                self.session.close()
        self.assertTrue(self.copies())
        self.session.close()
        self.assertEqual(self.copies(), [])

    def test_failed_strict_shutdown_cannot_claim_image_cleanup_or_delete_undrained_copy(self):
        with patch.object(self.provider, "stream", return_value=ProviderResult(
                False, error_code="provider_cleanup_failed", metrics=(("turn_submitted", True),))):
            self.send_image()
            self.assertTrue(self.stdout.terminal("image"))
        with patch.object(self.provider, "shutdown", side_effect=ProcessError("provider_cleanup_failed")):
            with self.assertRaisesRegex(ConfigurationError, "^provider_cleanup_failed$"):
                self.session.close()
        self.assertTrue(self.copies())
        self.assertTrue(self.session._undrained_images)
        self.session.close()
        self.assertEqual(self.copies(), [])

    def test_normal_failure_then_image_repeat_and_text_translation_preserve_existing_cache(self):
        with patch.object(self.provider, "stream", return_value=ProviderResult(
                False, error_code="request_failed", metrics=(("turn_submitted", True),))):
            self.send_image("failed")
            self.assertTrue(self.stdout.terminal("failed"))
        self.assertEqual(self.stdout.result("failed")["payload"], {"code": "provider_failed", "submitted": True})
        self.assertEqual(self.copies(), [])
        self.assertEqual(self.history(), [])
        for id_ in ("image-1", "image-2"):
            self.send_image(id_)
            self.assertTrue(self.stdout.terminal(id_))
            self.assertEqual(self.stdout.result(id_)["payload"]["history"], "recorded")
        for id_, cached in (("text", False), ("cached", True)):
            self.server._handle(message(id_, "request", **text_request()))
            self.assertTrue(self.stdout.terminal(id_))
            self.assertEqual(self.stdout.result(id_)["payload"]["cached"], cached)
        self.assertEqual(len(self.provider.requests), 3)
        self.assertEqual([entry["kind"] for entry in self.history()], ["text", "ocr", "ocr"])
        self.assertIsNone(self.history()[-1]["input"])
        self.assertEqual(self.copies(), [])

    def test_snapshot_config_freezes_before_copy_and_cleanup_precedes_final_admission_and_history(self):
        actual = image.OwnedPNG.prepare
        def prepare(owner, payload, cancel):
            self.session.perform({"operation": "config_save", "config": self.config | {
                CFG.CODEX_MODEL: "changed-during-copy", CFG.DIRECTION: "to_ja"}})
            return actual(owner, payload, cancel)
        def begin_finish():
            self.assertEqual(self.copies(), [])
            self.assertEqual(self.history(), [])
            return True
        with patch.object(image.OwnedPNG, "prepare", new=prepare):
            event, result = self.session.translate_image(
                request(self.source), threading.Event(), lambda _: None, begin_finish)
        self.assertEqual((event, result["history"]), ("completed", "recorded"))
        self.assertEqual(self.provider.requests[0].model, "synthetic")
        self.assertIsNone(result["target_lang"])

    def test_synthetic_fixture_prepares_exact_image_request_without_process_or_account_io(self):
        with patch("subprocess.Popen", side_effect=AssertionError("fixture ran a process")):
            fixture = image_fixture.prepare(self.home / "image-fixture", "synthetic", direction="to_ja")
        self.assertEqual(fixture["request"]["operation"], "translate_image")
        self.assertEqual(Path(fixture["request"]["image_path"]).read_bytes(), image_fixture.PNG_BYTES)
        self.assertEqual((fixture["expected"]["kind"], fixture["expected"]["target_lang"],
                          fixture["expected"]["task"], fixture["expected"]["summarize"]),
                         ("ocr", "ja", "image", False))
        self.assertNotIn(fixture["request"]["image_path"], fixture["expected"]["prompt"])
        for name in ("calls.jsonl", "native-rpc.jsonl", "version.jsonl", "image-read.jsonl"):
            self.assertFalse((Path(fixture["root"]) / name).exists())

    def test_eof_waits_for_image_drain_then_cleans_and_releases_history_owner(self):
        self.provider.release.clear()
        self.send_image()
        self.assertTrue(self.provider.entered.wait(1))
        results = []
        with patch("cc_macos.server.PipeFrameReader") as reader:
            reader.return_value.read.return_value = None
            worker = threading.Thread(target=lambda: results.append(self.server.run()))
            worker.start()
            try:
                self.assertTrue(self.server._stop_event.wait(1))
                self.assertTrue(self.copies())
                self.assertEqual(self.provider.closed, 0)
            finally:
                self.provider.release.set()
                worker.join(4)
        self.assertEqual(results, [0])
        self.assertEqual(self.stdout.result("image")["type"], "cancelled")
        self.assertEqual(self.copies(), [])
        self.assertIsNone(self.session._history)
        self.assertIsNone(self.session._owner)
