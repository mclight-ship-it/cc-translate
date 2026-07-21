"""Diagnostics helpers for CC Translate — pure, GUI-free logic.

These functions back the diagnostics window (backend detection, model-routing
explanation, suggested actions, endpoint reachability, config/log reading).
They are deliberately free of any Tk dependency so they can be unit-tested in
isolation; the window itself (which arranges them on screen) stays in
translator.pyw.

Public API used by translator.pyw:
    load_json_object(path)                          -> dict | None
    redact_diag_value(name, value)                  -> str
    infer_claude_backend(env)                       -> dict
    describe_model_routing(app_model, mode, model)  -> str
    build_diagnostics_actions(snapshot)             -> list[str]
    probe_base_url(base_url, timeout=1.5)           -> dict | None
    tail_text_file(path, max_lines=8)               -> str
"""

import json
import socket
from urllib.parse import urlsplit

import i18n


def load_json_object(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {"__error__": i18n.get("diagnostics.json_root_not_object")}
    except FileNotFoundError:
        return None
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def redact_diag_value(name, value):
    if value in (None, ""):
        return ""
    text = str(value)
    low = (name or "").lower()
    if any(k in low for k in ("api_key", "token", "auth")):
        if text.strip() == "Powered by Agent Maestro":
            return text
        return i18n.get("diagnostics.value_set")
    return text


def infer_claude_backend(env):
    env = dict(env or {})
    base_url = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    api_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    auth_token = (env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    model = (env.get("ANTHROPIC_MODEL") or "").strip()
    parsed = urlsplit(base_url) if base_url else None
    host = (parsed.hostname or "").lower() if parsed else ""
    port = parsed.port if parsed else None
    if parsed and port is None:
        port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    if base_url:
        if host in ("127.0.0.1", "localhost") and (
                port == 23333 or "agent maestro" in auth_token.lower()):
            label = i18n.get("diagnostics.backend.agent_maestro")
            mode = "agent_maestro"
        elif host.endswith("anthropic.com"):
            label = i18n.get("diagnostics.backend.anthropic_api")
            mode = "anthropic_api"
        else:
            label = i18n.get("diagnostics.backend.custom_endpoint")
            mode = "custom_endpoint"
    elif api_key or auth_token:
        label = i18n.get("diagnostics.backend.api_token")
        mode = "api_token"
    else:
        label = i18n.get("diagnostics.backend.subscription")
        mode = "subscription"
    return {
        "mode": mode,
        "label": label,
        "base_url": base_url,
        "host": host,
        "port": port,
        "model": model,
        "has_api_key": bool(api_key),
        "has_auth_token": bool(auth_token),
    }


def describe_model_routing(app_model, backend_mode, backend_model):
    app_model = (app_model or "").strip() or i18n.get("diagnostics.model_not_set")
    backend_model = (backend_model or "").strip()
    if backend_model and backend_mode != "subscription":
        if backend_model == app_model:
            return i18n.get("diagnostics.routing.same_model")
        return i18n.get("diagnostics.routing.proxy_override").format(
            backend_model=backend_model)
    return i18n.get("diagnostics.routing.no_proxy")


def build_diagnostics_actions(snapshot):
    snapshot = dict(snapshot or {})
    backend = dict(snapshot.get("backend") or {})
    login = dict(snapshot.get("login") or {})
    cli = dict(snapshot.get("claude_cli") or {})
    endpoint_probe = snapshot.get("endpoint_probe")
    ps_policy = dict(snapshot.get("powershell_policy") or {})
    last_result = dict(snapshot.get("last_result") or {})
    detail = ((last_result.get("detail") or "") + "\n" +
              (last_result.get("preview") or "")).casefold()

    actions = []
    if not cli.get("ok"):
        actions.append(i18n.get("diagnostics.action.fix_cli"))
    if backend.get("mode") == "subscription" and not login.get("ok"):
        actions.append(i18n.get("diagnostics.action.login_subscription"))
    if backend.get("mode") == "agent_maestro":
        if endpoint_probe and not endpoint_probe.get("ok"):
            actions.append(i18n.get("diagnostics.action.start_agent_maestro"))
        else:
            actions.append(i18n.get("diagnostics.action.keep_agent_maestro_running"))
    elif (backend.get("mode") in ("custom_endpoint", "api_token", "anthropic_api")
          and endpoint_probe and not endpoint_probe.get("ok")):
        actions.append(i18n.get("diagnostics.action.check_endpoint"))
    if ps_policy.get("value") in ("Restricted", "AllSigned"):
        actions.append(i18n.get("diagnostics.action.use_claude_cmd"))

    if last_result.get("ok") is False:
        if any(k in detail for k in ("timeout", "超时")):
            actions.append(i18n.get("diagnostics.action.retry_after_timeout"))
        elif any(k in detail for k in ("rate limit", "429", "限流", "频率")):
            actions.append(i18n.get("diagnostics.action.retry_after_rate_limit"))
        elif any(k in detail for k in ("not logged in", "authentication",
                                       "unauthorized", "login", "未登录", "登录")):
            actions.append(i18n.get("diagnostics.action.retry_after_login"))
        else:
            actions.append(i18n.get("diagnostics.action.retry_generic"))

    if not actions:
        actions.append(i18n.get("diagnostics.action.no_action_needed"))

    deduped = []
    for item in actions:
        if item not in deduped:
            deduped.append(item)
    return deduped


def probe_base_url(base_url, timeout=1.5):
    if not base_url:
        return None
    try:
        parsed = urlsplit(base_url)
    except Exception as e:
        return {"ok": False, "summary": i18n.get("diagnostics.endpoint.parse_failed").format(error=e)}
    host = parsed.hostname
    if not host:
        return {"ok": False, "summary": i18n.get("diagnostics.endpoint.missing_host")}
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    if port is None:
        return {"ok": False, "summary": i18n.get("diagnostics.endpoint.missing_port")}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "summary": i18n.get("diagnostics.endpoint.reachable").format(host=host, port=port)}
    except ConnectionRefusedError:
        return {"ok": False, "summary": i18n.get("diagnostics.endpoint.refused").format(host=host, port=port)}
    except OSError as e:
        return {"ok": False, "summary": i18n.get("diagnostics.endpoint.unreachable").format(host=host, port=port, error=e)}


def tail_text_file(path, max_lines=8):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:]).strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        return i18n.get("diagnostics.read_failed").format(
            error_type=type(e).__name__, error=e)
