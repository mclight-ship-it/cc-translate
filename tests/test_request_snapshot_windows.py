"""Real Windows dispatch/execute consumers of the shared immutable request."""

import ast
import hashlib
from types import SimpleNamespace
import unittest
from unittest import mock

import cc_app_results
import cc_prompts
from cc_providers.base import ProviderRequest, ProviderResult, ProviderSelection
from tests._tr import tr
from tests import test_full as fixtures


# Frozen ae04dc194982f51898974148f4a1502e4d3e0b3c:translator.pyw body.
LEGACY_META = '''
def _history_meta(self):
    selection = self._provider_selection()
    text = self._last_input or ""
    summarize = self._should_summarize(text)
    system_prompt = self._system_prompt_for(text)
    task = "translation_summary" if summarize else "text"
    if selection.provider_id == CODEX_PROVIDER and summarize:
        mode = self.cfg.get(CFG.DIRECTION, "auto")
        app_language = self.cfg.get(CFG.LANGUAGE) or i18n.get_language()
        target_lang = resolve_target_lang(mode, app_language, text)
        system_prompt = codex_summary_instruction(target_lang)
    return {
        "input": self._last_input,
        "origin": self._last_origin,
        "is_code": self._last_class == "code",
        "kind": self._history_kind(),
        "sig": self._cache_signature(),
        "provider": selection.provider_id,
        "model": selection.model,
        "direction": self.cfg.get(CFG.DIRECTION, "auto"),
        "summarize": summarize,
        "task": task,
        "system_prompt": system_prompt,
        "cancel_event": getattr(self, "_provider_cancel_event", None),
    }
'''
_namespace = dict(tr.__dict__)
exec(compile(ast.parse(LEGACY_META), "<frozen-request-metadata>", "exec"), _namespace)
legacy_meta = _namespace["_history_meta"]


def app_for(text="A short source sentence.", provider="codex_cli", **config):
    app = object.__new__(tr.TranslatorApp)
    app.cfg = tr.Config({
        tr.CFG.MODEL_PROVIDER: provider,
        tr.CFG.CLAUDE_MODEL: "haiku",
        tr.CFG.CODEX_MODEL: "gpt-5.4",
        tr.CFG.LANGUAGE: "zh",
        tr.CFG.LABS_DEFAULTS_MIGRATED: True,
        tr.CFG.SUMMARY_ENABLED: False,
        tr.CFG.CODEX_STREAMING_EXPERIMENTAL: False,
        **config,
    })
    app._last_input, app._last_origin = text, "text"
    app._last_class = tr.classify_selection(text)
    app._last_dictionary_local = False
    app._job_id = 7
    app._ss = tr.StreamSession()
    app._provider_cancel_event = tr.threading.Event()
    app.root = mock.Mock()
    app.popup = None
    app._show_result = mock.Mock()
    app._provider_registry = mock.Mock()
    app._provider_registry.get.return_value.complete.return_value = ProviderResult(
        True, "SYNTHETIC RESULT")
    app._destroy_popup = mock.Mock()
    app._is_centered_layout = mock.Mock(return_value=True)
    app._warm_translate = mock.Mock(return_value=False)
    app._make_loading_popup = mock.Mock()
    app._animate_loading = mock.Mock()
    return app


def stub_rendering(app):
    app._show_result = tr.TranslatorApp._show_result.__get__(app)
    for name in (
            "_stop_animation", "_result_title", "_cycle_popup_anchor",
            "_destroy_popup", "_remember_result", "_make_popup",
            "_maybe_add_explain_button", "_maybe_add_as_text_button",
            "_maybe_add_ai_dictionary_button", "_maybe_add_result_actions_button"):
        setattr(app, name, mock.Mock())


class RequestSnapshotWindowsTests(unittest.TestCase):
    def test_ocr_layout_prompt_is_shared_with_unchanged_windows_bytes_and_no_image_task(self):
        self.assertIs(tr.OCR_STRUCTURE_HINT, cc_prompts.OCR_STRUCTURE_HINT)
        self.assertIs(tr.with_ocr_structure_hint, cc_prompts.with_ocr_structure_hint)
        text = "  Heading \u4e2d\U0001f642\r\n\r\n1. First item\r\n2. Second item\r\n"
        for direction in ("auto", "to_ja"):
            for origin in ("text", "selection", "ocr"):
                app = app_for(text, direction=direction, language="en_US", summary_enabled=True)
                app._last_origin = origin
                meta = app._history_meta()
                snapshot = meta["snapshot"]
                expected = (tr.direction_prompt(direction, "en_US")
                            + (cc_prompts.OCR_STRUCTURE_HINT if origin == "ocr" else "") + tr.SYSTEM_SUFFIX)
                self.assertEqual(snapshot.request.system_prompt.encode("utf-8"), expected.encode("utf-8"))
                self.assertEqual((snapshot.request.user_text, snapshot.request.image_paths, snapshot.request.task),
                                 (text, (), "text"))
                self.assertEqual(snapshot.kind, "ocr" if origin == "ocr" else "text")

    def test_local_ocr_dispatch_skips_dictionary_and_cache_but_keeps_word_code_and_summary_rules(self):
        for text, prompt in (
                ("hello", tr.DICTIONARY_PROMPT),
                ("def greeting():\n    return 42", tr.CODE_EXPLAIN_PROMPT),
                ("A natural language source sentence with context. " * 30,
                 tr.direction_prompt("auto", "en_US") + cc_prompts.OCR_STRUCTURE_HINT + tr.SYSTEM_SUFFIX)):
            app = app_for(text, language="en_US", summary_enabled=True,
                          history_enabled=True, local_dictionary_enabled=True)
            app._local_dictionary = mock.Mock()
            app._local_dictionary.lookup.side_effect = AssertionError("OCR local lookup")
            with mock.patch.object(tr, "find_cached_translation", side_effect=AssertionError("OCR cache")), \
                    mock.patch.object(tr.threading, "Thread") as worker:
                app._show_loading(text, origin="ocr")
            snapshot = worker.call_args.kwargs["args"][2]["snapshot"]
            self.assertEqual((snapshot.origin, snapshot.kind, snapshot.summarize), ("ocr", "ocr", False))
            self.assertEqual(snapshot.request.system_prompt, prompt)
            self.assertEqual(snapshot.request.task, "text")
            self.assertEqual(snapshot.request.image_paths, ())
            self.assertEqual(snapshot.content_class, tr.classify_selection(text))
            self.assertEqual(snapshot.dictionary, tr.is_single_word(text))
            app._provider_registry.get.assert_not_called()

    def test_local_ocr_worker_reuses_captured_text_request_and_records_ocr_metadata(self):
        for text in ("hello", "def greeting():\n    return 42", "  OCR line one.\r\nOCR line two. \n"):
            app = app_for(text, language="en_US", history_enabled=True)
            with mock.patch.object(tr.threading, "Thread") as worker:
                app._show_loading(text, origin="ocr", use_cache=False)
            target, args = worker.call_args.kwargs["target"], worker.call_args.kwargs["args"]
            snapshot = args[2]["snapshot"]
            app._last_input, app._last_origin = "later request", "text"
            app.cfg.update(codex_model="later model", direction="to_ja")
            with mock.patch.object(tr, "log_perf"), mock.patch.object(tr, "add_history") as write:
                target(*args)
            captured = app._provider_registry.get.return_value.complete.call_args.args[0]
            self.assertIs(captured, snapshot.request)
            self.assertEqual(captured.user_text, text)
            self.assertEqual(captured.image_paths, ())
            write.assert_called_once_with(text, "SYNTHETIC RESULT", tr.is_single_word(text),
                                          app.cfg[tr.CFG.HISTORY_LIMIT], is_code=snapshot.content_class == "code",
                                          kind="ocr", sig=snapshot.sig)

    def test_oracle_matches_fixed_source_ast_fingerprint(self):
        body = ast.Module(body=ast.parse(LEGACY_META).body[0].body, type_ignores=[])
        self.assertEqual(
            hashlib.sha256(ast.dump(body, include_attributes=False).encode()).hexdigest(),
            "c9b7bd27edf737eb5895f5a78cd46535460101dee8297487d40897f4a3b5d3e8")

    def test_metadata_prompt_signature_and_task_match_frozen_oracle(self):
        texts = (
            "word", "A short sentence.", "\u4e2d\u6587 with code",
            "def f(x):\n    return x + 1",
            "A natural language source sentence with context. " * 30,
        )
        for provider in ("claude_cli", "codex_cli"):
            for language in ("zh", "en_US"):
                for direction in ("auto", "to_ja"):
                    for text in texts:
                        with self.subTest(provider=provider, language=language,
                                          direction=direction, text_length=len(text)):
                            app = app_for(text, provider, language=language,
                                          direction=direction, summary_enabled=True)
                            before, after = legacy_meta(app), app._history_meta()
                            self.assertEqual(
                                {key: after[key] for key in before}, before)
                            self.assertEqual(
                                after["snapshot"].request.system_prompt.encode(),
                                before["system_prompt"].encode())
                            self.assertEqual(after["snapshot"].sig.encode(),
                                             before["sig"].encode())

    def test_read_only_metadata_and_execution_config_are_isolated(self):
        app = app_for(future={"names": ["original"]})
        meta = app._history_meta()
        snapshot = meta["snapshot"]
        event, session = app._provider_cancel_event, app._ss
        app.cfg["future"]["names"][0] = "changed"
        app.cfg[tr.CFG.MODEL_PROVIDER] = "claude_cli"
        app._last_input = "NEW INPUT"
        app._ss = tr.StreamSession()
        app._provider_cancel_event = tr.threading.Event()
        event.set()
        self.assertEqual(snapshot.config["future"]["names"], ("original",))
        self.assertEqual(snapshot.selection.provider_id, "codex_cli")
        self.assertEqual(snapshot.input, "A short source sentence.")
        self.assertIs(meta["stream_session"], session)
        self.assertIs(meta["cancel_event"], event)
        self.assertTrue(meta["cancel_event"].is_set())
        self.assertFalse(hasattr(snapshot, "cancel_event"))
        with self.assertRaises(TypeError):
            meta["model"] = "new"

    def test_actual_loading_dispatch_captures_before_worker_start(self):
        app = app_for(history_enabled=False)
        with mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("Original source sentence.", use_cache=False)
        target = thread.call_args.kwargs["target"]
        args = thread.call_args.kwargs["args"]
        snapshot = args[2]["snapshot"]
        app.cfg.update(model_provider="claude_cli", codex_model="changed",
                       direction="to_ja", codex_streaming_experimental=True)
        app._last_input = "REPLACED"
        app._system_prompt_for = mock.Mock(side_effect=AssertionError("live prompt"))
        with mock.patch.object(tr, "log_perf"):
            target(*args)
        request, event = app._provider_registry.get.return_value.complete.call_args.args
        self.assertIs(request, snapshot.request)
        self.assertEqual(request.user_text, "Original source sentence.")
        self.assertIs(event, args[2]["cancel_event"])

    def test_stream_eligibility_and_session_are_not_read_from_live_ui(self):
        app = app_for("Long source sentence. " * 30,
                      codex_streaming_experimental=True)
        meta = app._history_meta()
        original_session = app._ss
        app._ss = tr.StreamSession()
        app.cfg[tr.CFG.CODEX_STREAMING_EXPERIMENTAL] = False
        app._stream_codex = mock.Mock(return_value=True)
        app._call_model = mock.Mock()
        with mock.patch.object(tr, "log_perf"):
            app._do_provider_translate("wrong input", 7, meta)
        args = app._stream_codex.call_args.args
        self.assertEqual(args[0], meta["snapshot"].request.user_text)
        self.assertIs(args[2], original_session)
        app._call_model.assert_not_called()

    def test_codex_stream_passes_frozen_provider_request_with_original_timeout(self):
        app = app_for("A long English sentence. " * 30,
                      codex_streaming_experimental=True)
        app._stream_finalize = mock.Mock()
        meta = app._history_meta()
        provider = app._provider_registry.get.return_value
        provider.stream.return_value = ProviderResult(True, "DONE")
        app._system_prompt_for = mock.Mock(side_effect=AssertionError("live prompt"))
        with mock.patch.object(tr, "log_perf"), mock.patch.object(tr, "add_history"):
            self.assertTrue(app._stream_codex(
                meta["input"], 7, meta["stream_session"], meta,
                meta["snapshot"].selection))
        request = provider.stream.call_args.args[0]
        self.assertEqual(request, meta["snapshot"].with_timeout(90.0))
        self.assertEqual(meta["snapshot"].request.timeout_seconds, 60.0)

    def test_claude_oneshot_uses_snapshot_not_mutated_model_or_prompt(self):
        app = app_for(provider="claude_cli")
        meta = app._history_meta()
        app.cfg[tr.CFG.MODEL] = "changed"
        app._last_class = "code"
        app._system_prompt_for = mock.Mock(side_effect=AssertionError("live prompt"))
        app._should_summarize = mock.Mock(side_effect=AssertionError("live summary"))
        with mock.patch.object(tr.subprocess, "run",
                               return_value=SimpleNamespace(stdout='{"result":"OK"}', stderr="")) as run, \
                mock.patch.object(tr, "add_history"), mock.patch.object(tr, "log_perf"):
            app._do_translate("wrong input", 7, meta)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--system-prompt") + 1],
                         meta["snapshot"].request.system_prompt)
        self.assertEqual(run.call_args.kwargs["input"],
                         "<text>\nA short source sentence.\n</text>")

    def test_claude_stream_uses_frozen_inputs_and_owned_session(self):
        app = app_for("A source sentence. " * 40, provider="claude_cli")
        meta = app._history_meta()
        app.cfg[tr.CFG.MODEL] = "changed"
        app._system_prompt_for = mock.Mock(side_effect=AssertionError("live prompt"))
        proc = fixtures._FakeProc([fixtures._result_event("FINAL")])
        with mock.patch.object(tr.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(tr, "add_history"), mock.patch.object(tr, "log_perf"):
            self.assertTrue(app._stream_claude(
                "wrong input", 7, meta["stream_session"], meta))
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--system-prompt") + 1],
                         meta["snapshot"].request.system_prompt)
        self.assertEqual(proc.stdin.data, "<text>\n" + meta["input"] + "\n</text>")

    def warm_app(self):
        app = app_for(provider="claude_cli")
        app._warm_translate = tr.TranslatorApp._warm_translate.__get__(app)
        app._warm_lock = tr.threading.Lock()
        app._warm_enabled = True
        app._spawn_warm_async = mock.Mock()
        app._record_history = mock.Mock()
        app._stream_finalize = mock.Mock()
        meta = app._history_meta()
        key = ("translate", "haiku", "auto")

        def candidate(prompt):
            warm = tr.WarmClaude("haiku", prompt, key)
            warm.ready = True
            warm.proc = mock.Mock()
            warm.proc.poll.return_value = None
            warm.send_and_stream = mock.Mock(return_value="SYNTHETIC WARM")
            warm.close = mock.Mock()
            return warm

        wrong = candidate(tr.direction_prompt("auto", "en_US") + tr.SYSTEM_SUFFIX)
        right = candidate(meta["snapshot"].request.system_prompt)
        app._warm_pool = {"translate": [wrong, right]}
        return app, meta, wrong, right

    def test_warm_requires_captured_prompt_not_just_model_direction_key(self):
        app, meta, wrong, right = self.warm_app()
        with mock.patch.object(tr, "log_perf"):
            self.assertTrue(app._warm_translate(
                "wrong input", 7, meta["stream_session"], meta))
        wrong.send_and_stream.assert_not_called()
        right.send_and_stream.assert_called_once()
        self.assertEqual(right.send_and_stream.call_args.args[0], meta["input"])

    def test_warm_snapshot_does_not_resolve_later_live_configuration(self):
        app, meta, wrong, right = self.warm_app()
        app._warm_profile_spec = mock.Mock(side_effect=AssertionError("live config"))
        with mock.patch.object(tr, "log_perf"):
            self.assertTrue(app._warm_translate(
                meta["input"], 7, meta["stream_session"], meta))
        app._warm_profile_spec.assert_not_called()
        wrong.send_and_stream.assert_not_called()
        right.send_and_stream.assert_called_once()

    def test_history_still_obeys_current_switch_current_limit_and_job(self):
        app = app_for(history_enabled=True, history_limit=99)
        meta = app._history_meta()
        with mock.patch.object(tr, "add_history") as write:
            app.cfg[tr.CFG.HISTORY_ENABLED] = False
            app._record_history(7, meta, "DONE", False)
            write.assert_not_called()
            app.cfg[tr.CFG.HISTORY_ENABLED] = True
            app.cfg[tr.CFG.HISTORY_LIMIT] = 3
            app._last_input = "NEW UI INPUT"
            app._record_history(7, meta, "DONE", False)
            write.assert_called_once_with(
                meta["input"], "DONE", False, 3, is_code=meta["is_code"],
                kind=meta["kind"], sig=meta["sig"])
            app._job_id = 8
            app._record_history(7, meta, "STALE", False)
            self.assertEqual(write.call_count, 1)

    def test_vision_dispatch_snapshot_and_captured_history_survive_ui_changes(self):
        app = app_for(provider="codex_cli")
        with mock.patch.object(tr.threading, "Thread") as thread:
            app._ocr_translate_vision("synthetic-owned-image.png")
        args = thread.call_args.kwargs["args"]
        snapshot = args[-1]
        self.assertEqual(snapshot.request.image_paths, ("synthetic-owned-image.png",))
        self.assertEqual(snapshot.request.task, "image")
        self.assertIsNone(snapshot.input)
        self.assertIsNone(snapshot.target_lang)
        self.assertEqual(snapshot.kind, "ocr")
        app.cfg[tr.CFG.MODEL_PROVIDER] = "claude_cli"
        app._last_input, app._last_class, app._last_origin = "NEW", "code", "text"
        stub_rendering(app)
        with mock.patch.object(app, "_cleanup_ocr_temp") as cleanup, \
                mock.patch.object(tr, "add_history") as write, mock.patch.object(tr, "log_perf"):
            thread.call_args.kwargs["target"](*args)
            write.assert_not_called()
            app.root.after.call_args.args[1]()
        request = app._provider_registry.get.return_value.complete.call_args.args[0]
        self.assertIs(request, snapshot.request)
        cleanup.assert_called_once_with("synthetic-owned-image.png")
        write.assert_called_once_with(
            "", "SYNTHETIC RESULT", False, app.cfg[tr.CFG.HISTORY_LIMIT],
            is_code=False, kind="ocr", sig=snapshot.sig)

    def test_vision_current_history_optout_is_not_frozen(self):
        app = app_for()
        with mock.patch.object(tr.threading, "Thread") as thread:
            app._ocr_translate_vision("synthetic.png")
        stub_rendering(app)
        with mock.patch.object(app, "_cleanup_ocr_temp"), \
                mock.patch.object(tr, "add_history") as write, mock.patch.object(tr, "log_perf"):
            thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
            app.cfg[tr.CFG.HISTORY_ENABLED] = False
            app.root.after.call_args.args[1]()
        write.assert_not_called()

    def test_claude_vision_uses_captured_provider_model_prompt_path_and_timeout(self):
        app = app_for(provider="claude_cli")
        path = r"synthetic image\owned.png"
        with mock.patch.object(tr.threading, "Thread") as thread:
            app._ocr_translate_vision(path)
        snapshot = thread.call_args.kwargs["args"][-1]
        app.cfg.update(model_provider="codex_cli", model="changed")
        with mock.patch.object(app, "_cleanup_ocr_temp") as cleanup, \
                mock.patch.object(tr.subprocess, "run",
                                  return_value=SimpleNamespace(stdout='{"result":"OK"}', stderr="")) as run, \
                mock.patch.object(tr, "log_perf"):
            thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], snapshot.request.model)
        self.assertEqual(argv[argv.index("--system-prompt") + 1],
                         snapshot.request.system_prompt)
        self.assertEqual(run.call_args.kwargs["timeout"], 90.0)
        self.assertEqual(run.call_args.kwargs["input"], tr.vision_image_mention(path))
        cleanup.assert_called_once_with(path)
        app._provider_registry.get.assert_not_called()

    def test_followup_actions_capture_before_thread_and_do_not_record_history(self):
        for action, argument in (("_retranslate_to", "ja"),
                                 ("_transform_result", "concise"),
                                 ("_explain_code_in_result", None)):
            with self.subTest(action=action):
                app = app_for()
                app.popup = SimpleNamespace(_text=fixtures._FakeText("PRIMARY RESULT"))
                app._set_result_actions_busy = mock.Mock()
                with mock.patch.object(tr.threading, "Thread") as thread:
                    method = getattr(app, action)
                    method() if argument is None else method(argument)
                args = thread.call_args.kwargs["args"]
                snapshot = args[-1]
                self.assertIsInstance(snapshot.request, ProviderRequest)
                app.cfg.update(model_provider="claude_cli", codex_model="changed")
                app._last_input = "NEW INPUT"
                with mock.patch.object(tr, "add_history") as write, \
                        mock.patch.object(tr, "log_perf"), \
                        mock.patch.dict(cc_app_results.RESULT_ACTION_PROMPTS,
                                        {"concise": ("different label", "MUTATED PROMPT")}):
                    thread.call_args.kwargs["target"](*args)
                self.assertIs(
                    app._provider_registry.get.return_value.complete.call_args.args[0],
                    snapshot.request)
                write.assert_not_called()

    def test_dictionary_supplement_captures_escaped_payload_and_cache_signature(self):
        app = app_for("word")
        app.popup = SimpleNamespace(_text=fixtures._FakeText("LOCAL <meaning>"))
        app._set_popup_text = mock.Mock()
        app._store_ai_dictionary_supplement = mock.Mock()
        with mock.patch.object(tr.threading, "Thread") as thread:
            app._start_ai_dictionary_supplement(
                "word", 7, base_result="LOCAL <meaning>")
        args = thread.call_args.kwargs["args"]
        snapshot = args[-1]
        self.assertEqual(snapshot.request.user_text,
                         "<query>word</query>\n<local_result>LOCAL &lt;meaning&gt;</local_result>")
        app.cfg.update(model_provider="claude_cli", codex_model="changed")
        with mock.patch.object(tr, "log_perf"):
            thread.call_args.kwargs["target"](*args)
        self.assertIs(
            app._provider_registry.get.return_value.complete.call_args.args[0],
            snapshot.request)
        app._store_ai_dictionary_supplement.assert_called_once_with(
            "word", snapshot.sig, "SYNTHETIC RESULT")


if __name__ == "__main__":
    unittest.main()
