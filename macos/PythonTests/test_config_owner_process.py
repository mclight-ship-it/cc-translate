"""Mandatory configuration service and real owner lifecycle in the unchanged app."""

import errno
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not substitute a host run.")

import cc_config
import cc_config_store
import cc_storage
import cc_macos
from cc_macos import config_owner, config_store_fixture, file_owner, history_owner
from cc_macos.config_owner import ConfigInUseError, MacConfigOwner
from owner_process_support import OwnerProcessCase
from test_history_owner_process import SIBLING_SCRIPT


OWNER_SCRIPT = r"""
import os
import signal
import sys
sys.path.insert(0, sys.argv[1])
from cc_macos.config_owner import ConfigForkError, MacConfigOwner
owner = MacConfigOwner(sys.argv[2], sys.argv[3])
writes = 0
print("acquired", flush=True)
for command in sys.stdin:
    command = command.strip()
    if command == "write":
        writes += 1
        owner.save({"font_size": "16", "future": [writes]})
        print("written", flush=True)
    elif command == "load":
        owner.load()
        print("loaded", flush=True)
    elif command == "close":
        owner.close()
        print("closed", flush=True)
    elif command == "fork":
        child = os.fork()
        if child == 0:
            signal.alarm(5)
            try:
                for operation in (owner.load, lambda: owner.save({"forbidden": True}), owner.__enter__):
                    try:
                        operation()
                    except ConfigForkError:
                        pass
                    else:
                        os._exit(71)
                try:
                    owner.close()
                except ConfigForkError:
                    pass
                else:
                    os._exit(72)
                if owner._fd is not None:
                    os._exit(73)
            except BaseException:
                os._exit(74)
            os._exit(0)
        waited, status = os.waitpid(child, 0)
        if waited != child or os.waitstatus_to_exitcode(status) != 0:
            raise RuntimeError("synthetic config fork child failed or was not reaped")
        print("fork-reaped", flush=True)
    elif command == "crash":
        os._exit(23)
    elif command == "exit":
        owner.close()
        print("exiting", flush=True)
        break
    else:
        raise RuntimeError("unexpected synthetic command")
else:
    raise RuntimeError("unexpected synthetic stdin EOF")
"""


class TestConfigOwnerProcess(OwnerProcessCase):
    bundle_modules = (cc_config, cc_config_store, cc_storage, cc_macos, config_owner,
                      config_store_fixture, file_owner, history_owner)
    owner_script = OWNER_SCRIPT

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with (cls.contents / "Info.plist").open("rb") as stream:
            cls.application_id = plistlib.load(stream)["CFBundleIdentifier"]
        if not isinstance(cls.application_id, str) or not cls.application_id:
            raise RuntimeError("The config fixture requires the actual bundle identity.")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".cc-config-process-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "synthetic home \u4e2d # %"
        self.directory = cc_storage.macos_user_paths(self.home, self.application_id).application_support
        self.directory.mkdir(parents=True)
        self.path = self.directory / "config.json"
        self.lock_path = self.directory / "config.json.lock"

    def process_arguments(self):
        return (self.core, self.home, self.application_id)

    def create(self):
        return MacConfigOwner(self.home, self.application_id)

    def test_real_bundle_identity_config_service_fixture(self):
        home = self.root / "fixture home \u4e2d # %"
        home.mkdir()
        report = config_store_fixture.probe_config_store(home, self.application_id)
        self.assertEqual(report, {
            "status": "passed", "fixture": True, "config_verified": True,
            "migration_verified": True, "snapshot_verified": True, "reopen_verified": True})
        with self.assertRaises(FileExistsError):
            config_store_fixture.probe_config_store(home, self.application_id)
        print("PASS: bundled config owner fixture", flush=True)

    def test_missing_read_only_creates_owner_side_file_not_configuration(self):
        with self.create() as owner:
            for _ in range(2):
                self.assertEqual(owner.load(), cc_config.Config())
                self.assertFalse(self.path.exists())
            self.assertEqual(set(self.directory.iterdir()), {self.lock_path})
            self.assertFalse(os.get_inheritable(owner._fd))
            self.assertEqual(stat.S_IMODE(self.lock_path.stat().st_mode), 0o600)
        with self.create() as owner:
            self.assertEqual(owner.load(), cc_config.Config())
            self.assertFalse(self.path.exists())

    def test_raw_migration_runs_once_and_reopen_preserves_unknown_values(self):
        raw = {"future": {"values": ["synthetic \u4e2d # %"]}, "font_size": "16",
               "codex_model": "gpt-5.4-mini", "codex_streaming_experimental": False}
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        changed, expected = cc_config.plan_config_migration(raw, cc_config.Config(raw))
        self.assertTrue(changed)
        with self.create() as owner:
            loaded = owner.load()
            self.assertEqual(loaded.font_size, 16)
            self.assertEqual(loaded.codex_model, "auto-fast")
            self.assertEqual(self.path.read_bytes(), json.dumps(expected, ensure_ascii=False, indent=2).encode("utf-8"))
            inode = self.path.stat().st_ino
            loaded["future"]["values"].append("detached")
            self.assertEqual(owner.load()["future"], raw["future"])
            self.assertEqual(self.path.stat().st_ino, inode)
        with self.create() as owner:
            self.assertEqual(owner.load()["future"], raw["future"])
            self.assertEqual(self.path.stat().st_ino, inode)

    def test_save_and_returned_views_are_detached_and_opt_out_is_preserved(self):
        raw = {"ui_v2_default_migrated": True, "ui_v2": False, "labs_defaults_migrated": True,
               "codex_model_default_migrated": True,
               "summary_enabled": False, "future": {"values": ["synthetic"]}}
        with self.create() as owner:
            owner.save(raw)
            saved = self.path.read_bytes()
            raw["future"]["values"].append("caller change")
            first = owner.load()
            first["future"]["values"].append("returned view change")
            second = owner.load()
            self.assertIsNot(first, second)
            self.assertEqual(second["future"]["values"], ["synthetic"])
            self.assertIs(second["ui_v2"], False)
            self.assertIs(second.summary_enabled, False)
            self.assertEqual(self.path.read_bytes(), saved)
        with self.create() as owner:
            self.assertEqual(owner.load()["future"]["values"], ["synthetic"])

    def test_second_process_owner_rejected_after_save_load_replace_and_close(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        lock_inode = self.lock_path.stat().st_ino
        with self.assertRaises(ConfigInUseError):
            self.create()
        self.send(process, "write", "written")
        first_inode = self.path.stat().st_ino
        with self.assertRaises(ConfigInUseError):
            self.create()
        self.send(process, "load", "loaded")
        self.assertNotEqual(self.path.stat().st_ino, first_inode)
        self.assertEqual(self.lock_path.stat().st_ino, lock_inode)
        self.send(process, "write", "written")
        with self.assertRaises(ConfigInUseError):
            self.create()
        with self.assertRaises(history_owner.HistoryInUseError):
            history_owner.MacHistoryOwner(self.path)
        self.send(process, "close", "closed")
        with self.create() as successor:
            self.assertEqual(successor.load()["future"], [2])
            self.assertEqual(successor.lock_path.stat().st_ino, lock_inode)
        self.send(process, "exit", "exiting")
        self.finish(process)

    def test_normal_exit_and_crash_release_lock_after_reap_without_harming_sibling(self):
        sibling = self.spawn(SIBLING_SCRIPT)
        self.assertEqual(self.line(sibling), "sibling-ready")
        for command, code in (("exit", 0), ("crash", 23)):
            with self.subTest(command=command):
                process = self.spawn()
                self.assertEqual(self.line(process), "acquired")
                self.send(process, "write", "written")
                self.send(process, command, "exiting" if command == "exit" else None)
                self.finish(process, code)
                with self.create() as successor:
                    self.assertEqual(successor.load().font_size, 16)
                self.assertIsNone(sibling.poll())
        self.send(sibling, "exit", "sibling-exiting")
        self.finish(sibling)

    def test_fork_rejects_child_operations_and_child_close_does_not_unlock_parent(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        self.send(process, "fork", "fork-reaped")
        self.assertFalse(self.path.exists())
        with self.assertRaises(ConfigInUseError):
            self.create()
        self.send(process, "write", "written")
        self.send(process, "exit", "exiting")
        self.finish(process)
        with self.create() as successor:
            self.assertEqual(successor.load()["future"], [1])

    def test_directory_alias_cannot_create_a_second_owner(self):
        alias = self.root / "home alias"
        alias.symlink_to(self.home, target_is_directory=True)
        with self.create():
            with self.assertRaises(ConfigInUseError):
                MacConfigOwner(alias, self.application_id)
        with MacConfigOwner(alias, self.application_id) as successor:
            self.assertEqual(successor.path, self.path)
            self.assertEqual(successor.lock_path, self.lock_path)

    def test_missing_application_directory_and_app_paths_do_not_fallback(self):
        bundle = self.root / "Synthetic.APP"
        inside = bundle / "Library" / "Application Support" / self.application_id
        inside.mkdir(parents=True)
        alias = self.root / "bundle alias"
        alias.symlink_to(bundle, target_is_directory=True)
        with patch.object(config_owner.os, "open") as opened:
            for home in (Path("relative"), self.home / "..", bundle, alias):
                with self.subTest(home=home), self.assertRaises(ValueError):
                    MacConfigOwner(home, self.application_id)
            with self.assertRaises(FileNotFoundError):
                MacConfigOwner(self.home, "synthetic.absent")
            opened.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(list(inside.iterdir()), [])

    def test_json_symlinks_are_rejected_before_acquisition_and_on_later_operations(self):
        target = self.root / "synthetic target"
        target.write_bytes(b"{}")
        for destination in (target, self.root / "missing target"):
            with self.subTest(destination=destination):
                self.path.symlink_to(destination)
                try:
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        self.create()
                    self.assertFalse(self.lock_path.exists())
                finally:
                    self.path.unlink()
        with self.create() as owner:
            self.path.symlink_to(target)
            for operation in (owner.load, lambda: owner.save({})):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    operation()
        self.assertEqual(target.read_bytes(), b"{}")

    def test_side_file_symlink_is_never_followed_removed_or_replaced(self):
        target = self.root / "synthetic lock target"
        target.write_bytes(b"sentinel")
        self.lock_path.symlink_to(target)
        with self.assertRaises(OSError):
            self.create()
        self.assertTrue(self.lock_path.is_symlink())
        self.assertEqual(target.read_bytes(), b"sentinel")

    def test_non_regular_side_file_closes_real_descriptor(self):
        os.mkfifo(self.lock_path, 0o600)
        actual_open, opened = os.open, []
        def record(*args):
            descriptor = actual_open(*args)
            opened.append(descriptor)
            return descriptor
        with patch.object(config_owner.os, "open", side_effect=record):
            with self.assertRaisesRegex(ValueError, "regular_file"):
                self.create()
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        self.assertTrue(stat.S_ISFIFO(self.lock_path.stat().st_mode))

    def test_fstat_and_flock_failures_release_real_fd_and_allow_takeover(self):
        import fcntl
        actual_open = os.open
        for target, attribute in ((config_owner.os, "fstat"), (fcntl, "flock")):
            with self.subTest(attribute=attribute):
                opened = []
                def record(*args):
                    descriptor = actual_open(*args)
                    opened.append(descriptor)
                    return descriptor
                with patch.object(config_owner.os, "open", side_effect=record), \
                        patch.object(target, attribute, side_effect=OSError(errno.EIO, "synthetic failure")):
                    with self.assertRaises(OSError) as raised:
                        self.create()
                self.assertEqual(raised.exception.errno, errno.EIO)
                self.assertEqual(len(opened), 1)
                self.assert_fd_closed(opened[0])
                with self.create():
                    pass

    def test_rejected_competitor_closes_only_its_own_descriptor(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        actual_open, opened = os.open, []
        def record(*args):
            descriptor = actual_open(*args)
            opened.append(descriptor)
            return descriptor
        with patch.object(config_owner.os, "open", side_effect=record):
            with self.assertRaises(ConfigInUseError):
                self.create()
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        with self.assertRaises(ConfigInUseError):
            self.create()
        self.send(process, "exit", "exiting")
        self.finish(process)

    def test_bad_json_encoding_root_and_conversion_preserve_original_bytes(self):
        cases = ((b"{", ValueError), (b"\xff", UnicodeError), (b"[]", ValueError),
                 (b"null", ValueError), (b"false", ValueError),
                 (b'{"font_size":"invalid"}', ValueError),
                 (b'{"history_limit":null}', TypeError),
                 (b'{"double_press_window":"invalid"}', ValueError),
                 (b'{"history_enabled":[]}', TypeError),
                 (b'{"font_size":Infinity}', OverflowError))
        for data, error in cases:
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.create() as owner, patch.object(cc_config_store, "atomic_write_json") as writer:
                    with self.assertRaises(error):
                        owner.load()
                    writer.assert_not_called()
                self.assertEqual(self.path.read_bytes(), data)
                self.assertEqual(set(self.directory.iterdir()), {self.path, self.lock_path})

    def test_real_read_permission_failure_does_not_migrate_or_overwrite(self):
        self.path.write_bytes(b"{}")
        self.path.chmod(0)
        try:
            with self.create() as owner, patch.object(cc_config_store, "atomic_write_json") as writer:
                with self.assertRaises(PermissionError):
                    owner.load()
                writer.assert_not_called()
        finally:
            self.path.chmod(0o600)
        self.assertEqual(self.path.read_bytes(), b"{}")
        self.assertEqual(set(self.directory.iterdir()), {self.path, self.lock_path})

    def test_migration_and_explicit_save_replace_failure_preserve_old_file_and_temps(self):
        self.path.write_bytes(b"{}")
        unrelated = self.directory / ".tmp_unrelated.json"
        unrelated.write_bytes(b"keep")
        with self.create() as owner:
            inode = self.lock_path.stat().st_ino
            for operation in (owner.load, lambda: owner.save({"future": ["new"]})):
                with patch("cc_storage.os.replace", side_effect=OSError("synthetic replace failure")):
                    with self.assertRaisesRegex(OSError, "replace failure"):
                        operation()
                self.assertEqual(self.path.read_bytes(), b"{}")
                self.assertEqual(self.lock_path.stat().st_ino, inode)
                self.assertEqual(set(self.directory.iterdir()), {self.path, self.lock_path, unrelated})
                with self.assertRaises(ConfigInUseError):
                    self.create()
        self.assertEqual(unrelated.read_bytes(), b"keep")
        with self.create() as owner:
            self.assertEqual(owner.load().font_size, cc_config.DEFAULT_CONFIG["font_size"])

    def test_close_waits_for_entire_load_migration_and_then_rejects_operations(self):
        self.path.write_bytes(b"{}")
        entered, release, waiting, closed = (threading.Event() for _ in range(4))
        failures = []
        actual_write = cc_config_store.atomic_write_json
        owner = self.create()
        self.addCleanup(owner.close)
        fd = owner._fd
        class ObservedLock:
            def __init__(self, inner):
                self.inner = inner
            def __enter__(self):
                if threading.current_thread().name == "synthetic-config-close":
                    waiting.set()
                self.inner.acquire()
                return self
            def __exit__(self, *args):
                self.inner.release()
        def write(path, payload):
            entered.set()
            if not release.wait(8):
                raise AssertionError("synthetic config write was not released")
            actual_write(path, payload)
        def run(operation):
            try:
                operation()
            except BaseException as error:
                failures.append(error)
        def close():
            owner.close()
            closed.set()
        loader = threading.Thread(target=run, args=(owner.load,))
        closer = threading.Thread(name="synthetic-config-close", target=run, args=(close,))
        owner._lock = ObservedLock(owner._lock)
        with patch.object(cc_config_store, "atomic_write_json", side_effect=write):
            try:
                loader.start()
                self.assertTrue(entered.wait(5))
                closer.start()
                self.assertTrue(waiting.wait(5))
                self.assertFalse(closed.is_set())
                self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
                with self.assertRaises(ConfigInUseError):
                    self.create()
            finally:
                release.set()
                loader.join(8)
                if closer.ident is not None:
                    closer.join(8)
        self.assertFalse(loader.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(closed.is_set())
        self.assert_fd_closed(fd)
        for operation in (owner.load, lambda: owner.save({}), owner.__enter__):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation()
        with self.create() as successor:
            self.assertTrue(successor.load()["ui_v2_default_migrated"])

    def test_close_failure_is_visible_and_consumed_fd_is_never_retried(self):
        owner = self.create()
        fd, actual_close = owner._fd, os.close
        def close_then_fail(descriptor):
            actual_close(descriptor)
            raise OSError("synthetic ambiguous close")
        with patch.object(config_owner.os, "close", side_effect=close_then_fail) as close:
            with self.assertRaisesRegex(OSError, "ambiguous close"):
                owner.close()
            owner.close()
            close.assert_called_once_with(fd)
        self.assert_fd_closed(fd)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            owner.load()
        with self.create():
            pass


if __name__ == "__main__":
    unittest.main()
