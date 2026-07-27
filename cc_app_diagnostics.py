"""cc_app_diagnostics — the Diagnostics window for CC Translate.

DiagnosticsMixin holds the ten methods behind the tray's "Diagnostics" entry:
collecting the environment/backend/login snapshot, formatting it into a summary
line and a full copyable report, building the themed window, refreshing it on a
worker thread, copying the report, and retrying the last translation from the
window.

The pure, GUI-free detection logic (backend inference, endpoint probing, action
suggestions, log tailing, JSON loading, value redaction) lives in
``diagnostics.py`` and is unit-tested there; this module is only the UI/glue
layer that presents it.

Extracted verbatim from ``TranslatorApp`` in translator.pyw (bodies unchanged).
Like the other cc_app_* mixins this imports only leaf modules (tkinter, i18n,
diagnostics, cc_warm, cc_update) and the shared foundation (cc_core); it never
imports translator.pyw, so there is no import cycle. ``self`` resolves at runtime
against the assembled ``TranslatorApp`` instance, so calls into other mixins and
shared window-building helpers (``self._rounded_shell``,
``self._reveal_rounded_window``, ``self._pill_button`` …) keep working.
"""

import os
import re
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

import i18n

from diagnostics import (
    load_json_object as _load_json_object,
    redact_diag_value as _redact_diag_value,
    infer_claude_backend,
    describe_model_routing,
    build_diagnostics_actions,
    probe_base_url,
    tail_text_file,
)
from cc_warm import CLAUDE_CMD
from cc_update import is_git_deploy, version_string
from cc_core import (
    APP_DIR, DATA_DIR, CFG, POPUP_CORNER_RADIUS, _user_data_path,
)

# Paths reported in the diagnostics snapshot. Derived from the same
# cc_core._user_data_path used by translator.pyw so the values are identical to
# tr.CONFIG_PATH / tr.HISTORY_PATH without importing translator.pyw (which would
# create a cycle).
CONFIG_PATH = _user_data_path("config.json")
HISTORY_PATH = _user_data_path("history.json")


class DiagnosticsMixin:
    """Diagnostics window lifecycle and report building (mixed into TranslatorApp)."""

    def open_diagnostics(self):
        self.root.after(0, self._open_diagnostics)

    def _diagnostics_settings_paths(self):
        home = os.path.expanduser("~")
        return [
            (i18n.get("diagnostics.settings.user_json"),
             os.path.join(home, ".claude", "settings.json")),
            (i18n.get("diagnostics.settings.user_local_json"),
             os.path.join(home, ".claude", "settings.local.json")),
            (i18n.get("diagnostics.settings.app_json"),
             os.path.join(APP_DIR, ".claude", "settings.json")),
            (i18n.get("diagnostics.settings.app_local_json"),
             os.path.join(APP_DIR, ".claude", "settings.local.json")),
        ]

    def _collect_diagnostics_snapshot(self):
        runtime_env = {
            k: v for k, v in os.environ.items()
            if re.search(r"(^ANTHROPIC_|^CLAUDE_|PROXY)", k)
        }
        settings_sources = []
        merged_settings_env = {}
        for label, path in self._diagnostics_settings_paths():
            data = _load_json_object(path)
            env_block = {}
            if isinstance(data, dict) and "__error__" not in data:
                raw_env = data.get("env")
                if isinstance(raw_env, dict):
                    env_block = {
                        str(k): str(v)
                        for k, v in raw_env.items()
                        if isinstance(v, (str, int, float, bool))
                    }
            if env_block:
                merged_settings_env.update(env_block)
            settings_sources.append({
                "label": label,
                "path": path,
                "exists": os.path.exists(path),
                "data": data,
                "env": env_block,
            })

        effective_env = dict(merged_settings_env)
        effective_env.update(runtime_env)
        backend = infer_claude_backend(effective_env)
        endpoint_probe = probe_base_url(backend["base_url"])

        claude_meta_path = os.path.join(os.path.expanduser("~"), ".claude.json")
        claude_meta = _load_json_object(claude_meta_path)
        login = {
            "path": claude_meta_path,
            "exists": os.path.exists(claude_meta_path),
            "summary": i18n.get("diagnostics.login.not_detected"),
            "ok": False,
            "error": "",
        }
        if isinstance(claude_meta, dict):
            if "__error__" in claude_meta:
                login["summary"] = i18n.get("diagnostics.login.meta_read_failed")
                login["error"] = claude_meta["__error__"]
            elif claude_meta.get("userID") and claude_meta.get("hasCompletedOnboarding"):
                login["summary"] = i18n.get("diagnostics.login.complete")
                login["ok"] = True
            elif claude_meta.get("userID"):
                login["summary"] = i18n.get("diagnostics.login.account_incomplete")
        elif claude_meta is None:
            login["summary"] = i18n.get("diagnostics.login.meta_missing")

        resolved_cmd = CLAUDE_CMD if os.path.isabs(CLAUDE_CMD) else (
            shutil.which(CLAUDE_CMD) or CLAUDE_CMD)
        claude_cli = {
            "configured": CLAUDE_CMD,
            "resolved": resolved_cmd,
            "version": "",
            "ok": False,
        }
        try:
            proc = subprocess.run(
                [CLAUDE_CMD, "--version"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            claude_cli["version"] = out or i18n.get("diagnostics.exit_code").format(
                code=proc.returncode)
            claude_cli["ok"] = (proc.returncode == 0)
        except Exception as e:
            claude_cli["version"] = f"{type(e).__name__}: {e}"

        ps_policy = {"value": "", "ok": False}
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ps_policy["value"] = (proc.stdout or proc.stderr or "").strip()
            ps_policy["ok"] = (proc.returncode == 0 and bool(ps_policy["value"]))
        except Exception as e:
            ps_policy["value"] = f"{type(e).__name__}: {e}"

        advice = []
        app_model = self.cfg.get(CFG.MODEL, "")
        model_route_note = describe_model_routing(
            app_model, backend["mode"], backend.get("model"))
        if backend["mode"] == "agent_maestro":
            if endpoint_probe and not endpoint_probe["ok"]:
                advice.append(i18n.get("diagnostics.advice.agent_unreachable"))
            else:
                advice.append(i18n.get("diagnostics.advice.agent_maybe_down"))
        elif backend["mode"] in ("custom_endpoint", "api_token", "anthropic_api"):
            advice.append(i18n.get("diagnostics.advice.api_mode"))
        if backend["mode"] != "subscription" and login["ok"]:
            advice.append(i18n.get("diagnostics.advice.login_overridden"))
        if backend["mode"] == "subscription" and not login["ok"]:
            advice.append(i18n.get("diagnostics.advice.login_missing"))
        if backend.get("model") and backend["mode"] != "subscription":
            advice.append(model_route_note)
        if ps_policy["value"] in ("Restricted", "AllSigned"):
            advice.append(i18n.get("diagnostics.advice.ps_policy"))
        if not claude_cli["ok"]:
            advice.append(i18n.get("diagnostics.advice.cli_failed"))
        if not advice:
            advice.append(i18n.get("diagnostics.advice.no_obvious_issue"))

        snapshot = {
            "app": {
                "version": version_string(),
                "git_deploy": is_git_deploy(),
                "app_dir": APP_DIR,
                "data_dir": DATA_DIR,
                "config_path": CONFIG_PATH,
                "history_path": HISTORY_PATH,
                "cwd": os.getcwd(),
            },
            "backend": backend,
            "runtime_env": runtime_env,
            "settings_sources": settings_sources,
            "login": login,
            "claude_cli": claude_cli,
            "powershell_policy": ps_policy,
            "endpoint_probe": endpoint_probe,
            "app_model": app_model,
            "model_route_note": model_route_note,
            "last_result": {
                "ok": self._last_result_ok,
                "title": self._last_result_title,
                "preview": (self._last_result_text[:180] + "…")
                if len(self._last_result_text) > 180 else self._last_result_text,
                "detail": self._last_result_text,
                "origin": self._last_origin,
            },
            "recent_errors": tail_text_file(os.path.join(DATA_DIR, "error.log"), 8),
            "advice": advice,
        }
        snapshot["actions"] = build_diagnostics_actions(snapshot)
        return snapshot

    def _diagnostics_summary_text(self, snapshot):
        backend = snapshot["backend"]["label"]
        cli = (i18n.get("diagnostics.summary.cli_ok")
               if snapshot["claude_cli"]["ok"]
               else i18n.get("diagnostics.summary.cli_bad"))
        if snapshot["endpoint_probe"] is not None:
            conn = snapshot["endpoint_probe"]["summary"]
        elif snapshot["login"]["ok"] or snapshot["backend"]["mode"] != "subscription":
            conn = i18n.get("diagnostics.summary.link_ready")
        else:
            conn = i18n.get("diagnostics.summary.pending_login")
        return f"{backend} · {cli} · {conn}"

    def _format_diagnostics_report(self, snapshot):
        backend = snapshot["backend"]
        login = snapshot["login"]
        app = snapshot["app"]
        last_result = snapshot["last_result"]
        lines = [
            i18n.get("diagnostics.overview"),
            f"- {i18n.get('diagnostics.version')}: {app['version']}",
            f"- {i18n.get('diagnostics.git_deployed')}: "
            f"{i18n.get('diagnostics.yes') if app['git_deploy'] else i18n.get('diagnostics.no')}",
            f"- {i18n.get('diagnostics.backend')}: {backend['label']}",
            f"- {i18n.get('diagnostics.cli_version')}: "
            f"{snapshot['claude_cli']['version'] or i18n.get('diagnostics.unknown')}",
            f"- {i18n.get('diagnostics.login_status')}: {login['summary']}",
            f"- {i18n.get('diagnostics.powershell_policy')}: "
            f"{snapshot['powershell_policy']['value'] or i18n.get('diagnostics.unknown')}",
            f"- {i18n.get('diagnostics.custom_model')}: "
            f"{snapshot['app_model'] or i18n.get('diagnostics.model_not_set')}",
        ]
        if backend.get("model"):
            lines.append(f"- {i18n.get('model.routing_proxy')}: {backend['model']}")
        if snapshot["endpoint_probe"] is not None:
            lines.append(
                f"- {i18n.get('diagnostics.endpoint_connectivity')}: "
                f"{snapshot['endpoint_probe']['summary']}"
            )
        else:
            lines.append(
                f"- {i18n.get('diagnostics.endpoint_connectivity')}: "
                f"{i18n.get('diagnostics.endpoint.not_configured')}"
            )
        lines.append(f"- {i18n.get('model.routing_note')}: {snapshot['model_route_note']}")
        if last_result["ok"] is None:
            lines.append(
                f"- {i18n.get('diagnostics.last_result')}: {i18n.get('diagnostics.last.none')}"
            )
        else:
            state = (i18n.get("diagnostics.last.success")
                     if last_result["ok"]
                     else i18n.get("diagnostics.last.failed"))
            preview = last_result["preview"] or i18n.get("diagnostics.last.no_preview")
            lines.append(
                f"- {i18n.get('diagnostics.last_result')}: {state} · "
                f"{last_result['title'] or i18n.get('diagnostics.last.unknown_type')} · {preview}")

        lines.extend(["", i18n.get("diagnostics.section.advice")])
        for item in snapshot["advice"]:
            lines.append(f"- {item}")

        lines.extend(["", i18n.get("diagnostics.section.next_steps")])
        for idx, item in enumerate(snapshot.get("actions", []), start=1):
            lines.append(f"{idx}. {item}")

        lines.extend([
            "", i18n.get("diagnostics.section.paths"),
            f"- APP_DIR = {app['app_dir']}",
            f"- DATA_DIR = {app['data_dir']}",
            f"- CONFIG_PATH = {app['config_path']}",
            f"- HISTORY_PATH = {app['history_path']}",
            f"- {i18n.get('diagnostics.path.work_dir')} = {app['cwd']}",
            f"- CLAUDE_CMD = {snapshot['claude_cli']['resolved']}",
            f"- {i18n.get('diagnostics.path.login_meta')} = {login['path']}",
        ])

        lines.extend(["", i18n.get("diagnostics.section.env")])
        if snapshot["runtime_env"]:
            for key in sorted(snapshot["runtime_env"]):
                val = _redact_diag_value(key, snapshot["runtime_env"][key])
                lines.append(f"- {key} = {val}")
        else:
            lines.append(f"- {i18n.get('diagnostics.env.none')}")

        lines.extend(["", i18n.get("diagnostics.section.configs")])
        for src in snapshot["settings_sources"]:
            if not src["exists"]:
                lines.append(
                    f"- {src['label']}: {i18n.get('diagnostics.config.missing')}")
                continue
            data = src["data"]
            if isinstance(data, dict) and "__error__" in data:
                lines.append(
                    f"- {src['label']}: "
                    f"{i18n.get('diagnostics.config.read_failed').format(error=data['__error__'])}")
                continue
            lines.append(f"- {src['label']}: {src['path']}")
            if src["env"]:
                for key in sorted(src["env"]):
                    lines.append(f"    - {key} = {_redact_diag_value(key, src['env'][key])}")
            else:
                lines.append(f"    - {i18n.get('diagnostics.config.no_env_override')}")

        lines.extend(["", i18n.get("diagnostics.section.recent_errors")])
        lines.append(snapshot["recent_errors"] or i18n.get("diagnostics.error_log.empty"))
        return "\n".join(lines)

    def _can_retry_last_translation(self):
        return bool(self._last_input or self._last_origin == "ocr")

    def _retry_from_diagnostics(self, win):
        retry_btn = getattr(win, "_diag_retry_btn", None)
        if not self._can_retry_last_translation():
            if retry_btn is not None:
                retry_btn.config(
                    state="disabled", cursor="arrow",
                    text=i18n.get("diagnostics.retry_unavailable"))
            return
        if retry_btn is not None:
            retry_btn.config(
                state="disabled", cursor="watch",
                text=i18n.get("diagnostics.retrying"))
        if self._last_input:
            win.destroy()
            self._retry()
            return
        win.destroy()
        self._ocr_from_menu()

    def _apply_diagnostics_report(self, win, summary_text, report):
        try:
            if not tk.Toplevel.winfo_exists(win):
                return
        except Exception:
            return
        summary = getattr(win, "_diag_summary", None)
        text = getattr(win, "_diag_text", None)
        refresh_btn = getattr(win, "_diag_refresh_btn", None)
        copy_btn = getattr(win, "_diag_copy_btn", None)
        retry_btn = getattr(win, "_diag_retry_btn", None)
        if summary is not None:
            summary.config(text=summary_text, fg=self.theme["accent"])
        if text is not None:
            text.config(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", report)
            text.config(state="disabled")
        win._diag_report = report
        if refresh_btn is not None:
            refresh_btn.config(
                state="normal", cursor="hand2",
                text=i18n.get("diagnostics.redetect"))
        if copy_btn is not None:
            copy_btn.config(state="normal", cursor="hand2")
        if retry_btn is not None:
            can_retry = self._can_retry_last_translation()
            retry_btn.config(
                state="normal" if can_retry else "disabled",
                cursor="hand2" if can_retry else "arrow",
                text=(i18n.get("diagnostics.retry_translate")
                      if can_retry else i18n.get("diagnostics.retry_unavailable")))

    def _refresh_diagnostics_window(self, win=None):
        win = win or self.diagnostics_win
        if not win:
            return
        try:
            if not tk.Toplevel.winfo_exists(win):
                return
        except Exception:
            return
        summary = getattr(win, "_diag_summary", None)
        text = getattr(win, "_diag_text", None)
        refresh_btn = getattr(win, "_diag_refresh_btn", None)
        copy_btn = getattr(win, "_diag_copy_btn", None)
        retry_btn = getattr(win, "_diag_retry_btn", None)
        if summary is not None:
            summary.config(text=i18n.get("diagnostics.refreshing"), fg=self.theme["popup_hint"])
        if text is not None:
            text.config(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", i18n.get("diagnostics.refreshing"))
            text.config(state="disabled")
        if refresh_btn is not None:
            refresh_btn.config(state="disabled", cursor="watch", text=i18n.get("diagnostics.refreshing"))
        if copy_btn is not None:
            copy_btn.config(state="disabled", cursor="arrow")
        if retry_btn is not None:
            retry_btn.config(
                state="disabled", cursor="watch",
                text=i18n.get("diagnostics.refreshing"))

        result_q = queue.Queue()
        win._diag_queue = result_q

        def work():
            try:
                snapshot = self._collect_diagnostics_snapshot()
                result_q.put((
                    self._diagnostics_summary_text(snapshot),
                    self._format_diagnostics_report(snapshot),
                ))
            except Exception as e:
                result_q.put((
                    i18n.get("diagnostics.redetect_failed_title"),
                    i18n.get("diagnostics.redetect_failed_detail").format(
                        error_type=type(e).__name__, error=e),
                ))

        def poll():
            try:
                summary_text, report = result_q.get_nowait()
            except queue.Empty:
                try:
                    if tk.Toplevel.winfo_exists(win):
                        win.after(80, poll)
                except Exception:
                    pass
                return
            self._apply_diagnostics_report(win, summary_text, report)

        threading.Thread(target=work, daemon=True).start()
        win.after(80, poll)

    def _copy_diagnostics_report(self, win):
        report = getattr(win, "_diag_report", "") or ""
        if not report:
            return
        btn = getattr(win, "_diag_copy_btn", None)
        if self._copy_text_content(report) and btn is not None:
            try:
                btn.config(text=i18n.get("diagnostics.copied"))
                win.after(1200, lambda: (
                    tk.Toplevel.winfo_exists(win)
                    and getattr(win, "_diag_copy_btn", None)
                    and win._diag_copy_btn.config(text=i18n.get("diagnostics.copy"))))
            except Exception:
                pass

    def _open_diagnostics(self):
        if self.diagnostics_win and tk.Toplevel.winfo_exists(self.diagnostics_win):
            self._bring_to_front(self.diagnostics_win)
            self._refresh_diagnostics_window(self.diagnostics_win)
            return

        t = self.theme
        bg = t["settings_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        FONT = "Microsoft YaHei UI"

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.diagnostics_win = win

        card = self._rounded_shell(win, POPUP_CORNER_RADIUS, bg, border)

        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=16, pady=(12, 8))
        logo_img = self._logo_image(18)
        drag_targets = [bar]
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(0, 8))
            drag_targets.append(logo_lbl)
        title_lbl = tk.Label(bar, text=i18n.get("diagnostics.title"), bg=bg,
                             fg=accent, font=(FONT, 11, "bold"))
        title_lbl.pack(side="left")
        drag_targets.append(title_lbl)
        close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                             font=(FONT, 11), cursor="hand2", padx=6)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: win.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=t["status_err"]))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
        self._make_draggable(tuple(drag_targets), win)
        tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        summary = tk.Label(card, text=i18n.get("diagnostics.refreshing"), bg=bg, fg=hint,
                           anchor="w", justify="left", font=(FONT, 9, "bold"))
        summary.pack(fill="x", padx=16, pady=(10, 4))

        body = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        text = tk.Text(
            body, bg=t["bg"], fg=fg, wrap="word", relief="flat", bd=0,
            padx=12, pady=10, font=(FONT, 10), highlightthickness=0,
            insertwidth=0, selectbackground=t["sel_bg"])
        scroll = ttk.Scrollbar(
            body, orient="vertical", style="CC.Vertical.TScrollbar",
            command=text.yview)
        text.config(yscrollcommand=scroll.set, state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)
        bottom = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bottom.pack(fill="x", padx=16, pady=(10, 14))
        refresh_btn = self._pill_button(
            bottom, i18n.get("diagnostics.redetect"), lambda: self._refresh_diagnostics_window(win),
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=18, pady=6)
        refresh_btn.pack(side="right")
        close2 = self._pill_button(
            bottom, i18n.get("settings.label.close"), win.destroy,
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=18, pady=6)
        close2.pack(side="right", padx=(0, 8))
        copy_btn = self._pill_button(
            bottom, i18n.get("diagnostics.copy"), lambda: self._copy_diagnostics_report(win),
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=18, pady=6)
        copy_btn.pack(side="right", padx=(0, 8))
        retry_btn = self._pill_button(
            bottom, i18n.get("diagnostics.retry_translate"),
            lambda: self._retry_from_diagnostics(win),
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=18, pady=6)
        retry_btn.pack(side="right", padx=(0, 8))

        win._diag_summary = summary
        win._diag_text = text
        win._diag_refresh_btn = refresh_btn
        win._diag_copy_btn = copy_btn
        win._diag_retry_btn = retry_btn
        win._diag_report = ""
        win.bind("<Escape>", lambda e: win.destroy())

        w, h, x, y = self._centered_box()
        self._reveal_rounded_window(win, w, h, x, y)
        self._refresh_diagnostics_window(win)
