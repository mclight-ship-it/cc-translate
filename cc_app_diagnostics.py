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
    APP_DIR, DATA_DIR, CFG, DEFAULT_CONFIG, POPUP_CORNER_RADIUS,
    V2_CORNER_RADIUS,
    CODEX_STREAM_MIN_CHARS,
    _user_data_path,
    get_provider_labels, summarize_provider_dogfood, evaluate_codex_rollout,
)
from cc_providers.codex_appserver import appserver_version_supported

# Paths reported in the diagnostics snapshot. Derived from the same
# cc_core._user_data_path used by translator.pyw so the values are identical to
# tr.CONFIG_PATH / tr.HISTORY_PATH without importing translator.pyw (which would
# create a cycle).
CONFIG_PATH = _user_data_path("config.json")
HISTORY_PATH = _user_data_path("history.json")


def _paint_round_rect(canvas, x1, y1, x2, y2, radius, *, fill, tag):
    radius = max(0, min(int(radius), int((x2 - x1) / 2),
                        int((y2 - y1) / 2)))
    shared = {"fill": fill, "outline": fill, "width": 0, "tags": tag}
    canvas.create_rectangle(
        x1 + radius, y1, x2 - radius, y2, **shared)
    canvas.create_rectangle(
        x1, y1 + radius, x2, y2 - radius, **shared)
    diameter = 2 * radius
    arc = {"fill": fill, "outline": fill, "style": "pieslice",
           "tags": tag}
    canvas.create_arc(
        x1, y1, x1 + diameter, y1 + diameter,
        start=90, extent=90, **arc)
    canvas.create_arc(
        x2 - diameter, y1, x2, y1 + diameter,
        start=0, extent=90, **arc)
    canvas.create_arc(
        x1, y2 - diameter, x1 + diameter, y2,
        start=180, extent=90, **arc)
    canvas.create_arc(
        x2 - diameter, y2 - diameter, x2, y2,
        start=270, extent=90, **arc)


def _codex_streaming_status_text(streaming):
    if not streaming.get("enabled"):
        return i18n.get("diagnostics.codex_stream.off")
    if not streaming.get("version_supported"):
        return i18n.get("diagnostics.codex_stream.version_unsupported")
    return i18n.get("diagnostics.codex_stream.ready")


def _codex_route_text(route):
    if not route:
        return i18n.get("diagnostics.codex_stream.route.none")
    mode = route.get("mode") or "none"
    label = i18n.get(
        f"diagnostics.codex_stream.route.{mode}")
    reason = route.get("error_code") or route.get("reason")
    return f"{label} ({reason})" if reason else label


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

        selection = self._provider_selection()
        provider_labels = get_provider_labels()
        selected_provider = {
            "id": selection.provider_id,
            "label": provider_labels.get(
                selection.provider_id, selection.provider_id),
            "model": selection.model or "auto",
            "status": None,
        }
        if selection.provider_id == "codex_cli":
            status = self._provider_status(selection.provider_id)
            selected_provider["status"] = {
                "installed": status.installed,
                "authenticated": status.authenticated,
                "command": status.command or "",
                "version": status.version,
                "auth_method": status.auth_method,
                "error_code": status.error_code,
                "error_detail": status.error_detail,
            }
            selected_provider["streaming"] = {
                "enabled": bool(self.cfg.get(
                    CFG.CODEX_STREAMING_EXPERIMENTAL,
                    DEFAULT_CONFIG[CFG.CODEX_STREAMING_EXPERIMENTAL])),
                "version_supported": appserver_version_supported(
                    status.version),
                "min_chars": CODEX_STREAM_MIN_CHARS,
                "fast_profile": selection.model == "auto-fast",
                "last_route": dict(getattr(
                    self, "_last_provider_route", {}) or {}),
            }
            dogfood = summarize_provider_dogfood()
            dogfood["rollout"] = evaluate_codex_rollout(dogfood)
            selected_provider["dogfood"] = dogfood

        advice = []
        app_model = selection.model or ""
        model_route_note = describe_model_routing(
            app_model, backend["mode"], backend.get("model"))
        if selection.provider_id == "codex_cli":
            provider_status = selected_provider["status"]
            if not provider_status["installed"]:
                advice.append(i18n.get(
                    "diagnostics.action.install_codex"))
            elif not provider_status["authenticated"]:
                advice.append(i18n.get(
                    "diagnostics.action.login_codex"))
        else:
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
            "selected_provider": selected_provider,
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
        if selection.provider_id == "codex_cli":
            snapshot["actions"] = list(advice)
        else:
            snapshot["actions"] = build_diagnostics_actions(snapshot)
        return snapshot

    def _diagnostics_summary_text(self, snapshot):
        provider = snapshot.get("selected_provider", {})
        if provider.get("id") == "codex_cli":
            status = provider.get("status") or {}
            cli = (i18n.get("diagnostics.summary.cli_ok")
                   if status.get("installed")
                   else i18n.get("diagnostics.summary.cli_bad"))
            conn = (i18n.get("diagnostics.summary.link_ready")
                    if status.get("authenticated")
                    else i18n.get("diagnostics.summary.pending_login"))
            stream = _codex_streaming_status_text(
                provider.get("streaming") or {})
            return (
                f"{provider.get('label', 'Codex')} · {cli} · {conn} · {stream}")
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
        provider = snapshot.get("selected_provider", {})
        is_codex = provider.get("id") == "codex_cli"
        lines = [
            i18n.get("diagnostics.overview"),
            f"- {i18n.get('diagnostics.version')}: {app['version']}",
            f"- {i18n.get('diagnostics.git_deployed')}: "
            f"{i18n.get('diagnostics.yes') if app['git_deploy'] else i18n.get('diagnostics.no')}",
            f"- {i18n.get('diagnostics.model_provider')}: "
            f"{provider.get('label', 'Claude')}",
            f"- {i18n.get('diagnostics.custom_model')}: "
            f"{provider.get('model') or snapshot['app_model'] or i18n.get('diagnostics.model_not_set')}",
        ]
        provider_status = provider.get("status")
        if is_codex and provider_status is not None:
            login_text = (
                i18n.get("diagnostics.codex_login.ready").format(
                    method=provider_status.get("auth_method") or
                    i18n.get("diagnostics.unknown"))
                if provider_status.get("authenticated")
                else i18n.get("diagnostics.codex_login.required"))
            lines.extend([
                f"- {i18n.get('diagnostics.codex_cli_version')}: "
                f"{provider_status.get('version') or i18n.get('diagnostics.unknown')}",
                f"- {i18n.get('diagnostics.login_status')}: {login_text}",
            ])
            streaming = provider["streaming"]
            trigger_key = (
                "diagnostics.codex_stream_trigger_fast_value"
                if streaming.get("fast_profile")
                else "diagnostics.codex_stream_trigger_value")
            lines.extend([
                f"- {i18n.get('diagnostics.codex_streaming')}: "
                f"{_codex_streaming_status_text(streaming)}",
                f"- {i18n.get('diagnostics.codex_stream_trigger')}: "
                f"{i18n.get(trigger_key).format(chars=streaming.get('min_chars', CODEX_STREAM_MIN_CHARS))}",
                f"- {i18n.get('diagnostics.codex_stream_last_route')}: "
                f"{_codex_route_text(streaming.get('last_route'))}",
            ])
            dogfood = provider.get("dogfood") or {}
            samples = dogfood.get("sample_count", 0)
            if samples:
                lines.append(
                    f"- {i18n.get('diagnostics.codex_dogfood')}: "
                    f"{i18n.get('diagnostics.codex_dogfood.summary').format(days=dogfood.get('days', 7), samples=samples, success=dogfood.get('success_count', 0), cancelled=dogfood.get('cancelled_count', 0), failed=dogfood.get('failed_count', 0), p50=dogfood.get('p50_ms'), p95=dogfood.get('p95_ms'))}")
                routes = dogfood.get("route_counts") or {}
                lines.append(
                    f"- {i18n.get('diagnostics.codex_dogfood.routes')}: "
                    f"{i18n.get('diagnostics.codex_dogfood.routes_value').format(streamed=routes.get('streamed', 0), stable=routes.get('stable_exec', 0), fallback=routes.get('stable_fallback', 0), cancelled=routes.get('stream_cancelled', 0), failed=routes.get('stream_failed', 0))}")
                models = dogfood.get("model_counts") or {}
                lines.append(
                    f"- {i18n.get('diagnostics.codex_dogfood.models')}: "
                    f"{', '.join(f'{model}: {count}' for model, count in sorted(models.items()))}")
            else:
                lines.append(
                    f"- {i18n.get('diagnostics.codex_dogfood')}: "
                    f"{i18n.get('diagnostics.codex_dogfood.empty')}")
            rollout = dogfood.get("rollout") or evaluate_codex_rollout(dogfood)
            rollout_status = rollout.get("status", "collecting")
            rollout_reason = rollout.get("reason", "request_volume")
            rollout_text = i18n.get(
                f"diagnostics.codex_rollout_gate.{rollout_status}").format(
                    samples=samples,
                    requests=rollout.get("required_requests", 200),
                    observed=dogfood.get("observation_days", 0),
                    days=rollout.get("required_days", 7),
                    reason=i18n.get(
                        f"diagnostics.codex_rollout_reason.{rollout_reason}"),
                )
            lines.append(
                f"- {i18n.get('diagnostics.codex_rollout_gate')}: "
                f"{rollout_text}")
            pending = i18n.get("diagnostics.pending")
            stream_p95 = dogfood.get("stream_first_result_p95_ms")
            stable_p95 = dogfood.get("stable_long_text_p95_ms")
            performance_text = i18n.get(
                "diagnostics.codex_rollout_performance_value").format(
                    stream_samples=dogfood.get(
                        "stream_first_result_count", 0),
                    stream_p95=(
                        stream_p95 if stream_p95 is not None else pending),
                    stable_samples=dogfood.get("stable_long_text_count", 0),
                    stable_p95=(
                        stable_p95 if stable_p95 is not None else pending),
                )
            lines.append(
                f"- {i18n.get('diagnostics.codex_rollout_performance')}: "
                f"{performance_text}")
            lines.append(
                f"- {i18n.get('diagnostics.codex_rollout_manual')}: "
                f"{i18n.get('diagnostics.codex_rollout_manual_value')}")
        else:
            lines.extend([
                f"- {i18n.get('diagnostics.backend')}: {backend['label']}",
                f"- {i18n.get('diagnostics.cli_version')}: "
                f"{snapshot['claude_cli']['version'] or i18n.get('diagnostics.unknown')}",
                f"- {i18n.get('diagnostics.login_status')}: {login['summary']}",
                f"- {i18n.get('diagnostics.powershell_policy')}: "
                f"{snapshot['powershell_policy']['value'] or i18n.get('diagnostics.unknown')}",
            ])
            if backend.get("model"):
                lines.append(
                    f"- {i18n.get('model.routing_proxy')}: {backend['model']}")
            endpoint = (
                snapshot["endpoint_probe"]["summary"]
                if snapshot["endpoint_probe"] is not None
                else i18n.get("diagnostics.endpoint.not_configured"))
            lines.extend([
                f"- {i18n.get('diagnostics.endpoint_connectivity')}: {endpoint}",
                f"- {i18n.get('model.routing_note')}: "
                f"{snapshot['model_route_note']}",
            ])
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
        ])
        if is_codex and provider_status is not None:
            lines.append(
                f"- CODEX_CMD = {provider_status.get('command') or i18n.get('diagnostics.unknown')}")
        else:
            lines.extend([
                f"- CLAUDE_CMD = {snapshot['claude_cli']['resolved']}",
                f"- {i18n.get('diagnostics.path.login_meta')} = {login['path']}",
                "", i18n.get("diagnostics.section.env"),
            ])
            if snapshot["runtime_env"]:
                for key in sorted(snapshot["runtime_env"]):
                    val = _redact_diag_value(
                        key, snapshot["runtime_env"][key])
                    lines.append(f"- {key} = {val}")
            else:
                lines.append(f"- {i18n.get('diagnostics.env.none')}")

            lines.extend(["", i18n.get("diagnostics.section.configs")])
            for src in snapshot["settings_sources"]:
                if not src["exists"]:
                    lines.append(
                        f"- {src['label']}: "
                        f"{i18n.get('diagnostics.config.missing')}")
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
                        lines.append(
                            f"    - {key} = "
                            f"{_redact_diag_value(key, src['env'][key])}")
                else:
                    lines.append(
                        f"    - "
                        f"{i18n.get('diagnostics.config.no_env_override')}")

        lines.extend(["", i18n.get("diagnostics.section.recent_errors")])
        lines.append(snapshot["recent_errors"] or i18n.get("diagnostics.error_log.empty"))
        return "\n".join(lines)

    def _can_retry_last_translation(self):
        return bool(self._last_input or self._last_origin == "ocr")

    @staticmethod
    def _set_diagnostics_button(btn, *, text=None, enabled=None, cursor=None):
        if btn is None:
            return
        if text is not None:
            setter = getattr(btn, "_chip_set", None)
            if setter is not None:
                setter(text)
            else:
                btn.config(text=text)
        if enabled is not None:
            setter = getattr(btn, "_chip_set_enabled", None)
            if setter is not None:
                setter(enabled)
                btn.config(takefocus=bool(enabled))
            else:
                btn.config(state="normal" if enabled else "disabled")
        if cursor is not None:
            btn.config(cursor=cursor)

    def _retry_from_diagnostics(self, win):
        retry_btn = getattr(win, "_diag_retry_btn", None)
        if not self._can_retry_last_translation():
            self._set_diagnostics_button(
                retry_btn, enabled=False, cursor="arrow",
                text=i18n.get("diagnostics.retry_unavailable"))
            return
        self._set_diagnostics_button(
            retry_btn, enabled=False, cursor="watch",
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
        theme = getattr(win, "_diag_theme", self.theme)
        if summary is not None:
            summary.config(text=summary_text, fg=theme["accent"])
        if text is not None:
            text.config(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", report)
            text.config(state="disabled")
        win._diag_report = report
        self._set_diagnostics_button(
            refresh_btn, enabled=True, cursor="hand2",
            text=i18n.get("diagnostics.redetect"))
        self._set_diagnostics_button(
            copy_btn, enabled=True, cursor="hand2")
        can_retry = self._can_retry_last_translation()
        self._set_diagnostics_button(
            retry_btn, enabled=can_retry,
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
        theme = getattr(win, "_diag_theme", self.theme)
        if summary is not None:
            summary.config(
                text=i18n.get("diagnostics.refreshing"),
                fg=theme["popup_hint"])
        if text is not None:
            text.config(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", i18n.get("diagnostics.refreshing"))
            text.config(state="disabled")
        self._set_diagnostics_button(
            refresh_btn, enabled=False, cursor="watch",
            text=i18n.get("diagnostics.refreshing"))
        self._set_diagnostics_button(
            copy_btn, enabled=False, cursor="arrow")
        self._set_diagnostics_button(
            retry_btn, enabled=False, cursor="watch",
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
            self._set_diagnostics_button(
                btn, text=i18n.get("diagnostics.copied"))

            def restore_label():
                try:
                    if not tk.Toplevel.winfo_exists(win):
                        return
                except tk.TclError:
                    return
                self._set_diagnostics_button(
                    getattr(win, "_diag_copy_btn", None),
                    text=i18n.get("diagnostics.copy"))

            win.after(1200, restore_label)

    def _open_diagnostics(self):
        if self.diagnostics_win and tk.Toplevel.winfo_exists(self.diagnostics_win):
            self._bring_to_front(self.diagnostics_win)
            self._refresh_diagnostics_window(self.diagnostics_win)
            return

        v2on = self._v2_popup_on()
        scale = self._ui_scale() if v2on else 1.0
        t = self._v2_window_theme() if v2on else self.theme
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
        win._v2 = v2on
        win._v2_resizable = False
        win._diag_theme = t
        self._apply_taskbar_identity(win, i18n.get("diagnostics.title"))

        radius = V2_CORNER_RADIUS if v2on else POPUP_CORNER_RADIUS
        card = self._rounded_shell(win, radius, bg, border)

        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        if v2on:
            bar.pack(fill="x", padx=40, pady=(20, 14))
            self._v2_brand_header(
                bar, win, title=i18n.get("diagnostics.title"),
                subtitle=None, bg=bg, hint=hint, accent=accent, font=FONT,
                scale=scale, cache_tag="diagnostics_title")
        else:
            bar.pack(fill="x", padx=16, pady=(12, 8))
            logo_img = self._logo_image(18)
            drag_targets = [bar]
            if logo_img:
                logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                    highlightthickness=0)
                logo_lbl.image = logo_img
                logo_lbl.pack(side="left", padx=(0, 8))
                drag_targets.append(logo_lbl)
            title_lbl = tk.Label(
                bar, text=i18n.get("diagnostics.title"), bg=bg,
                fg=accent, font=(FONT, 11, "bold"))
            title_lbl.pack(side="left")
            drag_targets.append(title_lbl)
            close_btn = tk.Label(
                bar, text="✕", bg=bg, fg=hint,
                font=(FONT, 11), cursor="hand2", padx=6)
            close_btn.pack(side="right")
            close_btn.bind("<Button-1>", lambda e: win.destroy())
            close_btn.bind(
                "<Enter>", lambda e: close_btn.config(fg=t["status_err"]))
            close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
            self._make_draggable(tuple(drag_targets), win)
            tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        summary_wrap = tk.Frame(
            card, bg=bg, bd=0, highlightthickness=0)
        summary_wrap.pack(
            fill="x", padx=40 if v2on else 16,
            pady=(2, 16) if v2on else (10, 4))
        summary = tk.Label(
            summary_wrap, text=i18n.get("diagnostics.refreshing"),
            bg=bg, fg=hint, anchor="w", justify="left",
            font=(FONT, 9, "bold"))
        summary.pack(fill="x")

        body_bg = t["list_bg"] if v2on else bg
        if v2on:
            body = tk.Canvas(
                card, bg=bg, bd=0, highlightthickness=0)
            body_inner = tk.Frame(
                body, bg=body_bg, bd=0, highlightthickness=0)
            inset = max(3, int(round(3 * scale)))
            radius = max(10, int(round(10 * scale)))
            body_window = body.create_window(
                inset, inset, anchor="nw", window=body_inner)

            def layout_rounded_body(event):
                width = max(1, event.width)
                height = max(1, event.height)
                body.delete("diag_round_body")
                _paint_round_rect(
                    body, 0, 0, width - 1, height - 1, radius,
                    fill=border, tag="diag_round_body")
                _paint_round_rect(
                    body, 1, 1, width - 2, height - 2, radius - 1,
                    fill=body_bg, tag="diag_round_body")
                body.tag_lower("diag_round_body")
                body.coords(body_window, inset, inset)
                body.itemconfigure(
                    body_window,
                    width=max(1, width - 2 * inset),
                    height=max(1, height - 2 * inset))

            body.bind("<Configure>", layout_rounded_body)
            body._diag_rounded = True
        else:
            body = tk.Frame(
                card, bg=body_bg, bd=0, highlightthickness=0)
            body_inner = body
        text = tk.Text(
            body_inner, bg=body_bg if v2on else t["bg"], fg=fg,
            wrap="word", relief="flat", bd=0,
            padx=16 if v2on else 12, pady=14 if v2on else 10,
            font=(FONT, 10), highlightthickness=0,
            insertwidth=0, selectbackground=t["sel_bg"])
        scroll = ttk.Scrollbar(
            body_inner, orient="vertical", style="CC.Vertical.TScrollbar",
            command=text.yview)
        text.config(yscrollcommand=scroll.set, state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        separator = None
        if not v2on:
            separator = tk.Frame(card, bg=border, height=1)
        bottom = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bottom.pack(
            side="bottom", fill="x", padx=40 if v2on else 16,
            pady=(0, 20) if v2on else (10, 14))
        actions = tk.Frame(bottom, bg=bg, bd=0, highlightthickness=0)
        actions.pack(side="right")
        if separator is not None:
            separator.pack(side="bottom", fill="x", padx=16)
        body.pack(
            fill="both", expand=True, padx=40 if v2on else 12,
            pady=(0, 14) if v2on else (0, 6))

        def make_button(text_, command, *, primary=False):
            if v2on:
                button = self._v2_soft_button(
                    actions, text_, command, grad=primary)
                button.config(takefocus=True)
                button.bind(
                    "<FocusIn>",
                    lambda _event, b=button: b.config(image=b._chip_hover)
                    if getattr(b, "_chip_enabled", True) else None)
                button.bind(
                    "<FocusOut>",
                    lambda _event, b=button: b.config(image=b._chip_normal))
                return button
            return self._pill_button(
                actions, text_, command,
                bg=t["list_bg"], fg=fg,
                hover_bg=t["btn_active"], hover_fg=fg,
                active_bg=t["list_sel"], active_fg=fg,
                font=(FONT, 10), padx=18, pady=6)

        retry_btn = make_button(
            i18n.get("diagnostics.retry_translate"),
            lambda: self._retry_from_diagnostics(win))
        retry_btn.pack(side="left")
        copy_btn = make_button(
            i18n.get("diagnostics.copy"),
            lambda: self._copy_diagnostics_report(win))
        copy_btn.pack(side="left", padx=(10 if v2on else 8, 0))
        close2 = make_button(
            i18n.get("settings.label.close"), win.destroy)
        close2.pack(side="left", padx=(10 if v2on else 8, 0))
        refresh_btn = make_button(
            i18n.get("diagnostics.redetect"),
            lambda: self._refresh_diagnostics_window(win), primary=v2on)
        refresh_btn.pack(side="left", padx=(10 if v2on else 8, 0))

        win._diag_summary = summary
        win._diag_summary_wrap = summary_wrap
        win._diag_body = body
        win._diag_text = text
        win._diag_refresh_btn = refresh_btn
        win._diag_close_btn = close2
        win._diag_copy_btn = copy_btn
        win._diag_retry_btn = retry_btn
        win._diag_report = ""
        win.bind("<Escape>", lambda e: win.destroy())

        if v2on:
            w, h, x, y = self._scaled_centered_box(680, 520)
        else:
            w, h, x, y = self._centered_box()
        self._reveal_rounded_window(win, w, h, x, y)
        self._refresh_diagnostics_window(win)
