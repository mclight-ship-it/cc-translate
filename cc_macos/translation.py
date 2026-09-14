"""Explicit native translation service; no account, CLI or user-home discovery."""

import json
import sys

from cc_classify import classify_selection, is_single_word
from cc_config import CFG
from cc_direction import DIRECTION_MODES, direction_prompt, resolve_target_lang
from cc_prompts import CODE_EXPLAIN_PROMPT, DICTIONARY_PROMPT, PROVIDER_PROMPT_REVISIONS, SYSTEM_SUFFIX
from cc_providers.base import CODEX_PROVIDER, ProviderRequest, ProviderSelection
from cc_providers.codex_darwin import DarwinCodexProvider
from cc_providers.darwin_process import ProcessError
from cc_request import RequestSnapshot
from cc_result_rules import history_kind, provider_cache_signature
from cc_storage import macos_user_paths
from cc_summary import SUMMARY_MIN_CHARS, codex_summary_instruction, is_summarizable_prose
from .configuration import ConfigurationError, ConfigurationSession
from .history import MAX_HISTORY_ENTRIES
from .protocol import MAX_TEXT_BYTES, MAX_STREAM_BYTES, ProtocolError, decode_json_document


CLI_ENVIRONMENT_KEY = "CC_TRANSLATE_CODEX_ENV"
MAX_CLI_ENVIRONMENT_BYTES = 32_768
MAX_OUTPUT_BYTES = 24_000
MAX_DELTA_BYTES = 4_096
TRANSLATION_FAILURE_CODES = {
    "invalid_translation", "translation_unavailable", "unsupported_provider",
    "invalid_translation_settings", "translation_timeout", "translation_output_limit",
    "provider_version_unsupported", "provider_cleanup_failed", "provider_protocol_error",
    "provider_failed",
}


class TranslationError(ConfigurationError):
    def __init__(self, code, submitted=False):
        super().__init__(code)
        self.code, self.submitted = code, submitted


def parse_cli_environment(environment, home):
    raw = None if environment is None else environment.get(CLI_ENVIRONMENT_KEY)
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CLI_ENVIRONMENT_BYTES:
            raise ProtocolError("invalid_startup")
        value = decode_json_document(raw.encode("utf-8"), max_depth=2, object_required=True)
        if (any(not key or "=" in key or "\0" in key or type(child) is not str or "\0" in child
                for key, child in value.items())
                or value.get("HOME") != home or "PATH" not in value):
            raise ProtocolError("invalid_startup")
        return value
    except (UnicodeError, ProtocolError):
        raise ProtocolError("invalid_startup") from None


def validate_translation_request(payload):
    if set(payload) != {"operation", "text", "app_language", "origin", "use_cache", "record_history"}:
        raise ProtocolError("invalid_translation")
    if (payload["operation"] != "translate" or type(payload["text"]) is not str
            or not payload["text"].strip()
            or payload["app_language"] not in ("zh_CN", "en_US")
            or payload["origin"] not in ("text", "selection")
            or type(payload["use_cache"]) is not bool or type(payload["record_history"]) is not bool):
        raise ProtocolError("invalid_translation")
    try:
        if len(payload["text"].encode("utf-8")) > MAX_TEXT_BYTES:
            raise ProtocolError("invalid_translation")
    except UnicodeError:
        raise ProtocolError("invalid_translation") from None


def text_bytes(text):
    return len(json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def snapshot_for_translation(config, payload):
    validate_translation_request(payload)
    if config[CFG.MODEL_PROVIDER] != CODEX_PROVIDER:
        raise TranslationError("unsupported_provider")
    text, model, direction = payload["text"], config[CFG.CODEX_MODEL], config[CFG.DIRECTION]
    language = config.get(CFG.LANGUAGE) or payload["app_language"]
    if (type(model) is not str or not model or len(model.encode("utf-8")) > 256
            or direction not in DIRECTION_MODES or language not in ("zh_CN", "en_US")
            or config[CFG.MAX_CHARS] < 1 or len(text) > config[CFG.MAX_CHARS]
            or not 1 <= config[CFG.HISTORY_LIMIT] <= MAX_HISTORY_ENTRIES):
        raise TranslationError("invalid_translation_settings")
    content_class, dictionary = classify_selection(text), is_single_word(text)
    summarize = bool(config[CFG.SUMMARY_ENABLED] and content_class in ("text", "mixed")
                     and not dictionary and len(text) >= SUMMARY_MIN_CHARS and is_summarizable_prose(text))
    target = None if content_class == "code" or dictionary else resolve_target_lang(direction, language, text)
    if content_class == "code":
        prompt = CODE_EXPLAIN_PROMPT
    elif dictionary:
        prompt = DICTIONARY_PROMPT
    elif summarize:
        prompt = codex_summary_instruction(target)
    else:
        prompt = direction_prompt(direction, language) + SYSTEM_SUFFIX
    return RequestSnapshot(
        request=ProviderRequest("translation_summary" if summarize else "text", model, prompt, text,
                                timeout_seconds=90 if config[CFG.CODEX_STREAMING_EXPERIMENTAL] else 60),
        selection=ProviderSelection(CODEX_PROVIDER, model), config=config, input=text,
        origin=payload["origin"], content_class=content_class,
        kind=history_kind(payload["origin"], content_class, text),
        sig=provider_cache_signature(CODEX_PROVIDER, model, direction, bool(config[CFG.SUMMARY_ENABLED]),
                                     language, PROVIDER_PROMPT_REVISIONS[CODEX_PROVIDER]),
        direction=direction, app_language=language, target_lang=target, summarize=summarize,
        dictionary=dictionary, stream_enabled=bool(config[CFG.CODEX_STREAMING_EXPERIMENTAL]))


def provider_failure(code):
    if "cleanup_failed" in code:
        return "provider_cleanup_failed"
    if code == "appserver_version_unsupported":
        return "provider_version_unsupported"
    if code in ("timeout", "rpc_timeout", "probe_timeout"):
        return "translation_timeout"
    if code == "translation_output_limit":
        return code
    if code.startswith(("invalid_appserver", "unknown_appserver")) or code == "unsafe_tool_event":
        return "provider_protocol_error"
    return "provider_failed"


class TranslationSession(ConfigurationSession):
    translation_enabled = True
    validate_translation_request = staticmethod(validate_translation_request)

    def __init__(self, home, application_id, command, environment):
        super().__init__(home, application_id)
        self.command, self.environment = command, dict(environment)
        self._provider = None

    def open(self):
        opened = False
        try:
            super().open()
            paths = macos_user_paths(self.home, self.application_id)
            self._provider = DarwinCodexProvider(
                self.command, paths.application_support / "NativeWorkspace",
                environment=self.environment, catalog_cache_dir=paths.caches / "CodexModels",
                log_error=self._log_provider_failure)
            opened = True
        except (ValueError, TypeError, OSError):
            raise ConfigurationError("translation_unavailable") from None
        finally:
            if not opened:
                self.close()

    @staticmethod
    def _log_provider_failure(_where, _error):
        sys.stderr.write("cc_macos:provider_notice\n")
        sys.stderr.flush()

    def _capture(self, payload):
        with self._operations_lock:
            config = self.perform({"operation": "config_load"})["config"]
            snapshot = snapshot_for_translation(config, payload)
            cached = None
            if payload["use_cache"] and config[CFG.HISTORY_ENABLED]:
                from .history import HistoryError
                try:
                    cached = self._history.find_cached(snapshot.input, snapshot.kind, snapshot.sig)
                except HistoryError as error:
                    raise ConfigurationError(error.code) from error
            return snapshot, cached

    def _record(self, snapshot, text, requested):
        if not requested:
            return "disabled", None
        with self._operations_lock:
            try:
                current = self.perform({"operation": "config_load"})["config"]
                if not current[CFG.HISTORY_ENABLED]:
                    return "disabled", None
                self.perform_history({
                    "operation": "history_add", "input": snapshot.input, "output": text,
                    "is_dict": snapshot.dictionary, "is_code": snapshot.content_class == "code",
                    "kind": snapshot.kind, "sig": snapshot.sig, "limit": current[CFG.HISTORY_LIMIT],
                }, "history-record", 2)
            except ConfigurationError as error:
                return "failed", error.code
            return "recorded", None

    def translate(self, payload, cancel, on_delta, begin_finish):
        snapshot, cached = self._capture(payload)
        if cancel.is_set():
            return "cancelled", {"submitted": False}
        submitted, output, used_cache = False, cached, cached is not None
        if cached is None:
            output_size = 2
            def emit(text):
                try:
                    on_delta(text)
                except ProtocolError as error:
                    code = ("translation_output_limit" if error.code == "translation_output_limit"
                            else "invalid_appserver_message")
                    raise ProcessError(code) from None

            def delta(text):
                nonlocal output_size
                if type(text) is not str:
                    raise ProcessError("invalid_appserver_message")
                try:
                    output_size += text_bytes(text) - 2
                except UnicodeError:
                    raise ProcessError("invalid_appserver_message") from None
                if output_size > MAX_OUTPUT_BYTES:
                    raise ProcessError("translation_output_limit")
                piece, size = [], 2
                for character in text:
                    cost = text_bytes(character) - 2
                    if size + cost > MAX_DELTA_BYTES:
                        emit("".join(piece))
                        piece, size = [], 2
                    piece.append(character)
                    size += cost
                if piece:
                    emit("".join(piece))
            try:
                result = (self._provider.stream(snapshot.request, delta, cancel) if snapshot.stream_enabled
                          else self._provider.complete(snapshot.request, cancel))
            except ProcessError:
                raise ProtocolError("internal_error") from None
            submitted = dict(result.metrics).get("turn_submitted", False)
            if "cleanup_failed" in result.error_code:
                raise TranslationError("provider_cleanup_failed", bool(submitted))
            if cancel.is_set() or result.error_code in ("cancelled", "appserver_shutdown"):
                return "cancelled", {"submitted": bool(submitted)}
            if not result.ok:
                raise TranslationError(provider_failure(result.error_code), bool(submitted))
            output = result.text
        try:
            output_size = text_bytes(output) if type(output) is str else MAX_OUTPUT_BYTES + 1
        except UnicodeError:
            raise TranslationError("provider_protocol_error", bool(submitted)) from None
        if type(output) is not str or not output.strip() or output_size > MAX_OUTPUT_BYTES:
            raise TranslationError("translation_output_limit", bool(submitted))
        if not begin_finish():
            return "cancelled", {"submitted": bool(submitted)}
        status, error = ("unchanged", None) if used_cache else self._record(
            snapshot, output, payload["record_history"])
        return "completed", {
            "text": output, "submitted": bool(submitted), "cached": used_cache,
            "kind": snapshot.kind, "target_lang": snapshot.target_lang, "summarize": snapshot.summarize,
            "history": status, "history_error": error,
        }

    def close(self):
        try:
            if self._provider is not None:
                try:
                    self._provider.shutdown()
                except ProcessError:
                    raise ConfigurationError("provider_cleanup_failed") from None
        finally:
            super().close()
