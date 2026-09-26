"""Host-testable Darwin config dispatch and single-owner cleanup contracts."""

import errno
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, CodexConfigError, read_native_config
from cc_providers import codex_config_darwin as native
from cc_providers import darwin_process as owned
from cc_providers.darwin_process import ProcessError


class TestDarwinConfigContract(unittest.TestCase):
    def setUp(self):
        signals = patch.object(owned, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
        signals.start()
        self.addCleanup(signals.stop)

    def test_dispatch_preserves_native_environment_arguments_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"CODEX_HOME": directory, "SYNTHETIC_NATIVE_SETTING": "opaque"}
            result = {"config": {"model_provider": "synthetic"}, "layers": ["synthetic"]}
            with patch("cc_providers.codex_config.sys.platform", "darwin"), \
                    patch.object(native, "read_config", return_value=result) as read, \
                    patch("cc_providers.codex_config.subprocess.Popen") as popen:
                self.assertIs(read_native_config("synthetic-codex", environment, directory), result)
            args, passed_env, cwd = read.call_args.args
            self.assertEqual(args[:3], ["synthetic-codex", "app-server", "--strict-config"])
            self.assertEqual(args[3:], [part for item in CODEX_CONFIG_OVERRIDES for part in ("-c", item)])
            self.assertIs(passed_env, environment)
            self.assertEqual(cwd, directory)
            self.assertEqual(read.call_args.kwargs, {"cancel_event": None})
            popen.assert_not_called()

    def test_no_host_library_fallback(self):
        with patch("ctypes.CDLL") as load:
            with self.assertRaisesRegex(ProcessError, "runtime_unavailable"):
                owned.load_supervision()
            load.assert_not_called()

    def test_setup_os_error_has_only_a_fixed_diagnostic(self):
        with patch.object(native, "_ConfigSession", side_effect=OSError("SYNTHETIC_PRIVATE_SETUP")):
            with self.assertRaises(CodexConfigError) as error:
                native.read_config(["synthetic"], {}, "unused")
        self.assertEqual(str(error.exception), "config_probe_unavailable")

    def test_wrong_bridge_abi_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Synthetic.app/Contents"
            core = contents / "Resources/Core/cc_providers"
            core.mkdir(parents=True)
            library = contents / "Resources/python/lib/libCCProcessSupport.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"synthetic-not-a-library")
            bridge = Mock()
            bridge.cc_process_support_abi.return_value = 2
            with patch.object(owned, "__file__", str(core / "darwin_process.py")), \
                    patch("ctypes.CDLL", return_value=bridge):
                with self.assertRaisesRegex(ProcessError, "runtime_unavailable"):
                    owned.load_supervision()

    def session(self, signal_error=0, wait_error=None):
        session = owned.OwnedProcess.__new__(owned.OwnedProcess)
        session.closed = False
        session.finished = False
        session.owned = True
        session.bridge = Mock()
        session.process = Mock(pid=12345)
        session.process.stderr = None
        calls = []
        session.bridge.cc_cli_signal_group.side_effect = (
            lambda pid, sig: (calls.append((pid, sig)), signal_error)[1])
        def wait(timeout):
            calls.append("wait")
            if wait_error is not None:
                raise wait_error
            return -9
        session.process.wait.side_effect = wait
        return session, calls

    def test_cleanup_signals_before_reap_and_is_idempotent(self):
        session, calls = self.session()
        with patch.object(native.time, "sleep"):
            session.close()
            session.close()
        self.assertEqual(calls, [(12345, 15), (12345, 9), "wait"])
        session.process.poll.assert_not_called()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()

    def test_permission_failure_is_not_suppressed(self):
        session, _ = self.session(signal_error=errno.EPERM)
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
                session.close()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()

    def test_wait_failure_still_closes_both_pipes(self):
        session, _ = self.session(wait_error=subprocess.TimeoutExpired("synthetic", 2))
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
                session.close()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()

    def test_probe_selector_cleanup_failure_still_closes_process_owner(self):
        owner, selector = Mock(), Mock()
        owner.finished = False
        owner.has_exited.return_value = False
        selector.get_map.return_value = {"synthetic": object()}
        selector.select.side_effect = OSError("SYNTHETIC_READ_FAILURE")
        selector.close.side_effect = OSError("SYNTHETIC_CLOSE_FAILURE")
        with patch.object(owned, "OwnedProcess", return_value=owner), \
                patch.object(owned.selectors, "DefaultSelector", return_value=selector), \
                patch.object(owned.os, "set_blocking"):
            try:
                with self.assertRaisesRegex(ProcessError, "^probe_cleanup_failed$"):
                    owned.capture_output(["synthetic"], {}, "unused", cancel_event=None,
                                         timeout=1, max_bytes=100)
            finally:
                owner.close.assert_called_once()

    def test_config_selector_cleanup_failure_still_closes_process_owner(self):
        for error, expected in ((OSError("SYNTHETIC_CLOSE"), ProcessError),
                                (RuntimeError("synthetic programming error"), RuntimeError)):
            with self.subTest(error=type(error).__name__):
                session = native._ConfigSession.__new__(native._ConfigSession)
                session.closed = False
                session.selector = Mock()
                session.selector.close.side_effect = error
                session.owner = Mock()
                try:
                    with self.assertRaises(expected) as caught:
                        session.close()
                finally:
                    session.owner.close.assert_called_once()
                if isinstance(error, OSError):
                    self.assertEqual(str(caught.exception), "probe_cleanup_failed")

    def test_lost_child_ownership_never_signals_or_reaps_again(self):
        session, calls = self.session(signal_error=errno.ECHILD)
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
                session.close()
        self.assertEqual(calls, [(12345, 15)])
        session.process.wait.assert_not_called()
        self.assertEqual(session.process.returncode, -1)

    def test_non_reaping_observation_lost_ownership_blocks_all_future_signals(self):
        session, calls = self.session()
        session.bridge.cc_cli_has_exited.return_value = errno.ECHILD
        with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
            session.has_exited()
        with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
            session.close()
        session.close()
        self.assertEqual(calls, [])
        session.process.poll.assert_not_called()
        session.process.wait.assert_not_called()
        self.assertEqual(session.process.returncode, -1)
        session.process.stdout.close.assert_called_once()

    def test_config_translates_shared_errors_and_closes_its_selector(self):
        for code in ("runtime_unavailable", "probe_unavailable", "probe_cleanup_failed"):
            with patch.object(native, "_ConfigSession", side_effect=ProcessError(code)):
                with self.assertRaisesRegex(CodexConfigError, "^config_" + code + "$"):
                    native.read_config(["synthetic"], {}, "unused")
        session = native._ConfigSession.__new__(native._ConfigSession)
        session.closed = False
        session.selector = Mock()
        session.owner = Mock()
        session.close()
        session.close()
        session.selector.close.assert_called_once()
        session.owner.close.assert_called_once()

    def test_ownership_lost_at_final_signal_never_waits(self):
        session, _ = self.session()
        session.bridge.cc_cli_signal_group.side_effect = [0, errno.ECHILD]
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
                session.close()
        session.close()
        self.assertEqual(session.bridge.cc_cli_signal_group.call_count, 2)
        session.process.wait.assert_not_called()
        self.assertEqual(session.process.returncode, -1)

    def test_pre_cancelled_darwin_config_does_not_launch(self):
        cancel = threading.Event()
        cancel.set()
        with patch("cc_providers.codex_config.sys.platform", "darwin"), \
                patch.object(native, "read_config") as read, \
                patch("cc_providers.codex_config.os.makedirs") as create:
            with self.assertRaisesRegex(CodexConfigError, "config_probe_cancelled"):
                read_native_config("synthetic", {}, "unused", cancel_event=cancel)
            read.assert_not_called()
            create.assert_not_called()

    def test_legacy_platform_does_not_silently_ignore_new_cancel_option(self):
        with patch("cc_providers.codex_config.sys.platform", "win32"):
            with self.assertRaisesRegex(CodexConfigError, "config_cancel_unsupported"):
                read_native_config("synthetic", {}, "unused", cancel_event=threading.Event())
