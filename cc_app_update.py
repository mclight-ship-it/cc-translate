"""cc_app_update — self-update UI mixin for the CC Translate app.

Nightly scheduler + user-triggered update flow (check, fast-forward merge,
compile/test verify, relaunch, and the post-restart tray notice). Extracted
verbatim from translator.pyw's TranslatorApp and mixed back in, so `self`
resolves to the assembled instance and the shared state (`_nightly_job`,
`_update_in_progress`, `tray`, `root`, `cfg`, `_settings_check`) still lives in
TranslatorApp.__init__. Imports only leaf modules (cc_core, cc_update, i18n) —
never translator — so there is no import cycle.
"""

import os
import sys
import subprocess
import threading

import i18n
from cc_core import CFG, log_error, APP_NAME, APP_DIR, DATA_DIR, UPDATE_NOTICE_PATH
from cc_update import (
    is_git_deploy, version_string, _spawn_relauncher, _git,
    GIT_REMOTE, GIT_BRANCH, SCRIPT_PATH,
)
import cc_update as _cc_update


class UpdateMixin:
    def _schedule_nightly_update(self):
        """(Re)arm a timer that fires at the configured nightly hour. Always
        reschedules itself, so toggling the setting at runtime takes effect on
        the next fire without a restart."""
        try:
            import datetime
            if self._nightly_job is not None:
                try:
                    self.root.after_cancel(self._nightly_job)
                except Exception:
                    pass
                self._nightly_job = None
            hour = int(self.cfg.get(CFG.AUTO_UPDATE_HOUR, 3))
            hour = min(23, max(0, hour))
            now = datetime.datetime.now()
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            delay_ms = int((target - now).total_seconds() * 1000)
            # Clamp so a suspended/resumed machine or clock change re-evaluates
            # at least daily and never underflows.
            delay_ms = max(60_000, min(delay_ms, 24 * 3600 * 1000))
            self._nightly_job = self.root.after(delay_ms, self._nightly_tick)
        except Exception as e:
            log_error("schedule_nightly", e)

    def _nightly_tick(self):
        """Fired at the nightly hour. Update silently when enabled and idle;
        retry shortly if the user is mid-translation, else reschedule."""
        try:
            if self.cfg.get(CFG.AUTO_UPDATE_ENABLED, True):
                if self._is_busy():
                    # Don't interrupt — try again soon, same night.
                    self._nightly_job = self.root.after(
                        10 * 60 * 1000, self._nightly_tick)
                    return
                self._begin_update(silent=True)
        except Exception as e:
            log_error("nightly_tick", e)
        self._schedule_nightly_update()

    def _begin_update(self, silent=False, on_status=None, check_only=False):
        """Kick off a check (and optional update) on a background thread (git +
        network must never run on the Tk main thread). ``on_status(msg, kind)``
        is marshalled back to the main thread; kind is
        'info' | 'ok' | 'err' | 'avail'. When ``check_only`` is True the worker
        stops after reporting availability and never modifies the checkout."""
        if self._update_in_progress:
            if on_status:
                on_status(i18n.get("update.in_progress"), "info")
            return
        self._update_in_progress = True
        threading.Thread(
            target=self._update_worker, args=(silent, on_status, check_only),
            daemon=True).start()

    def _update_worker(self, silent, on_status, check_only=False):
        def report(msg, kind="info"):
            if on_status:
                self.root.after(0, lambda: on_status(msg, kind))

        restart = False
        try:
            if not is_git_deploy():
                report(i18n.get("update.non_git"), "err")
                return
            ok, err = _cc_update.fetch_remote_branch()
            if not ok:
                log_error("update_fetch_state", RuntimeError(err or "fetch failed"))
                report(i18n.get("update.check_failed_remote"), "err")
                return
            state, local, remote = _cc_update.classify_update_state()
            if state == "unknown" or not remote:
                report(i18n.get("update.check_failed_remote"), "err")
                return
            if state != "behind":
                report(i18n.get("update.no_update"), "ok")
                return

            # There is a newer commit on the remote.
            if check_only:
                ver = _cc_update.remote_version_string() or remote[:7]
                report(i18n.get("update.found_version").format(version=ver),
                       "avail")
                return

            # The fetched remote is strictly ahead of us, so a fast-forward
            # merge should be the only change needed.
            report(i18n.get("update.downloading"), "info")
            ref = f"{GIT_REMOTE}/{GIT_BRANCH}"
            before = local
            rc, _, err = _git(["merge", "--ff-only", ref], timeout=30)
            if rc != 0:
                log_error("update_merge", RuntimeError(err or f"rc={rc}"))
                report(i18n.get("update.merge_failed"), "err")
                return

            # Safety net: the new code must at least compile (and pass tests if
            # present), else roll straight back to where we were.
            if not self._verify_update(before):
                report(i18n.get("update.rollback"), "err")
                return

            # Leave a breadcrumb so the relaunched instance can confirm success
            # with a visible tray balloon (the new process's tray icon may land
            # in Windows' overflow area, so a toast is the reliable signal).
            try:
                with open(UPDATE_NOTICE_PATH, "w", encoding="utf-8") as f:
                    f.write(version_string())
            except Exception as e:
                log_error("update_write_notice", e)

            report(i18n.get("update.done_restarting"), "ok")
            restart = True
        except Exception as e:
            log_error("update_worker", e)
            report(i18n.get("update.failed"), "err")
        finally:
            self._update_in_progress = False
            if restart:
                self.root.after(700, self._relaunch)

    def _verify_update(self, before_sha):
        """Guard against updating into a broken state. Compile-check the new
        main script and (when present) run the unit tests. On failure, hard
        reset back to ``before_sha`` and return False."""
        import py_compile
        try:
            py_compile.compile(SCRIPT_PATH, doraise=True)
        except Exception as e:
            log_error("update_verify_compile", e)
            _git(["reset", "--hard", before_sha], timeout=15)
            return False

        tests_dir = os.path.join(APP_DIR, "tests")
        if os.path.isdir(tests_dir):
            try:
                p = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover",
                     "-s", "tests"],
                    cwd=APP_DIR, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=180,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if p.returncode != 0:
                    log_error("update_verify_tests",
                              RuntimeError("unit tests failed"))
                    _git(["reset", "--hard", before_sha], timeout=15)
                    return False
            except Exception as e:
                # Couldn't run the tests (env issue) — compile already passed,
                # so don't block the update on an inability to test.
                log_error("update_verify_tests_run", e)
        return True

    def _relaunch(self):
        """Restart the app to load freshly-pulled code. Spawns the detached
        waiter first, then tears down. A hard os._exit fallback guarantees the
        process actually terminates promptly (a lingering non-daemon thread must
        not keep the old instance — and its single-instance mutex — alive, or
        the relauncher would wait and the new instance would collide)."""
        try:
            _spawn_relauncher(data_dir=DATA_DIR)
        except Exception as e:
            log_error("relaunch_spawn", e)
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        self._shutdown_model_processes()
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass
        # Force a prompt exit shortly after, whether or not the clean Tk
        # teardown fully unwinds — this releases the mutex so the relauncher's
        # wait returns and the fresh instance starts.
        threading.Timer(1.2, lambda: os._exit(0)).start()

    def check_update_via_settings(self):
        """Tray entry point for "检查更新": open Settings and trigger its check,
        so both entry points converge on the same in-window experience (status
        line + explicit "更新并重启" button) rather than updating silently."""
        def go():
            self._open_settings()
            if callable(self._settings_check):
                self.root.after(350, self._settings_check)
        self.root.after(0, go)

    def _show_update_notice_if_any(self):
        """On startup, if an update breadcrumb exists, show a tray balloon
        confirming the restart (retrying briefly until the tray is ready), then
        remove the breadcrumb so it only fires once."""
        if not os.path.exists(UPDATE_NOTICE_PATH):
            return
        try:
            with open(UPDATE_NOTICE_PATH, "r", encoding="utf-8") as f:
                ver = f.read().strip()
        except Exception:
            ver = ""
        # The tray thread may still be initialising; retry a few times.
        if self.tray is None and getattr(self, "_notice_retries", 0) < 8:
            self._notice_retries = getattr(self, "_notice_retries", 0) + 1
            self.root.after(1000, self._show_update_notice_if_any)
            return
        try:
            msg = (
                i18n.get("update.notice_with_version").format(version=ver)
                if ver else i18n.get("update.notice_no_version")
            )
            if self.tray is not None:
                self.tray.notify(msg, APP_NAME)
        except Exception as e:
            log_error("update_notice_show", e)
        try:
            os.remove(UPDATE_NOTICE_PATH)
        except Exception:
            pass
