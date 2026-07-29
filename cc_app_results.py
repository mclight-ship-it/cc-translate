"""cc_app_results — post-result action methods for TranslatorApp.

Mixed into TranslatorApp as ResultActionsMixin. Pure mechanical extraction from
translator.pyw of the 16 methods that operate on an already-shown translation
popup: the explain-code / result-actions buttons and menu, retranslate-to-language,
tone/length transforms (concise / formal / summary), copy (plain + bilingual),
and in-place code explanation.

Imports only leaf modules (cc_core / i18n / pyperclip / stdlib), never translator,
so there is no import cycle. History writes go through ``self._add_history`` (a thin
translator-core wrapper) because the history-IO family stays in translator.pyw.
"""

import threading
import tkinter as tk

import pyperclip
import i18n
from cc_core import (
    CFG, DIRECTION_MODES, LANGUAGES,
    SYSTEM_SUFFIX, CODE_EXPLAIN_APPEND_PROMPT, RESULT_ACTION_PROMPTS,
    is_single_word, log_error,
)


class ResultActionsMixin:
    def _maybe_add_explain_button(self, win):
        """For a mixed prose+code selection, add a one-shot '解释代码' button to
        the result popup's title bar. Clicking it explains the code portion in
        Chinese and appends that below the existing translation (which is left
        untouched)."""
        if self._last_class != "mixed":
            return
        if not win or getattr(win, "_has_explain_btn", False):
            return
        bar = getattr(win, "_btn_bar", None)
        mk = getattr(win, "_mk_bar_btn", None)
        if bar is None or mk is None:
            return
        try:
            btn = mk(i18n.get("result.explain"), self._explain_code_in_result)
            # Sit to the left of 复制 / ✕ (packed right-to-left).
            btn.pack(side="right", padx=(0, 4))
            win._explain_btn = btn
            win._has_explain_btn = True
        except Exception:
            pass

    def _maybe_add_as_text_button(self, win):
        """For a selection detected as code, add a '文字翻译' escape-hatch button
        to the code-explanation popup. Clicking it re-runs the original input as
        a plain-text translation, overriding the code heuristic — the one-click
        fix for a sentence that was wrongly explained as code."""
        if self._last_class != "code":
            return
        if not win or getattr(win, "_has_as_text_btn", False):
            return
        bar = getattr(win, "_btn_bar", None)
        mk = getattr(win, "_mk_bar_btn", None)
        if bar is None or mk is None:
            return
        try:
            btn = mk(i18n.get("result.as_text"), self._translate_as_text)
            # Sit to the left of 复制 / ✕ (packed right-to-left).
            btn.pack(side="right", padx=(0, 4))
            win._as_text_btn = btn
            win._has_as_text_btn = True
        except Exception:
            pass

    def _translate_as_text(self):
        """Re-translate the current input as plain text, overriding the code
        classification. The escape hatch behind the code-explain popup's
        '文字翻译' button; never on the hot path, so it adds no latency."""
        src = self._last_input
        if not src:
            return
        self._show_loading(src, origin=self._last_origin, force_class="text")

    def _maybe_add_result_actions_button(self, win):
        """Add a compact post-result actions menu for successful translations.

        The menu groups together alternate target-language retranslation,
        bilingual copy, and one-click rewrites (more concise / more formal /
        key-point summary) so the title bar stays compact even as we add more
        useful follow-up actions."""
        if not win or getattr(win, "_has_actions_btn", False):
            return
        if self._last_class == "code":
            return
        if self._last_input and is_single_word(self._last_input):
            return
        bar = getattr(win, "_btn_bar", None)
        mk = getattr(win, "_mk_bar_btn", None)
        if bar is None or mk is None:
            return
        try:
            t = self.theme
            menu = tk.Menu(
                win, tearoff=0,
                bg=t.get("popup_bg", t["bg"]), fg=t["fg"],
                activebackground=t["accent"], activeforeground="#ffffff",
                bd=0, relief="flat",
                font=("Microsoft YaHei UI", 9))
            if self._last_input:
                for code, (zh_name, en_name) in LANGUAGES.items():
                    if i18n.get_language() == "en_US":
                        lang_name = (i18n.get("result.language_chinese")
                                     if code == "zh" else en_name)
                    else:
                        lang_name = zh_name
                    menu.add_command(
                        label=i18n.get("result.retranslate_to").format(language=lang_name),
                        command=lambda c=code: self._retranslate_to(c))
                menu.add_separator()
                menu.add_command(label=i18n.get("result.copy_bilingual"), command=self._copy_bilingual_result)
                menu.add_separator()
            for mode in ("concise", "formal", "summary"):
                label_key = RESULT_ACTION_PROMPTS[mode][0]
                menu.add_command(
                    label=i18n.get(label_key),
                    command=lambda m=mode: self._transform_result(m))
            btn = mk(i18n.get("result.actions"), lambda: self._show_result_actions_menu(win))
            btn.pack(side="right", padx=(0, 4))
            win._actions_btn = btn
            win._actions_menu = menu
            win._has_actions_btn = True
        except Exception:
            pass

    def _show_result_actions_menu(self, win):
        menu = getattr(win, "_actions_menu", None)
        btn = getattr(win, "_actions_btn", None)
        if menu is None or btn is None:
            return
        try:
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _retranslate_to(self, code):
        src = self._last_input
        prompt = DIRECTION_MODES.get(f"to_{code}")
        if not src or not prompt:
            return
        win = self.popup
        btn = getattr(win, "_actions_btn", None) if win else None
        if btn is not None:
            try:
                btn.config(
                    text=i18n.get("result.processing"), state="disabled",
                    cursor="watch")
            except Exception:
                pass
        threading.Thread(
            target=self._do_retranslate,
            args=(src, prompt + SYSTEM_SUFFIX, code), daemon=True).start()

    def _do_retranslate(self, src, prompt, code):
        try:
            ok, result = self._call_claude(src, prompt)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(0, lambda: self._apply_retranslation(ok, result, code))

    def _apply_retranslation(self, ok, result, code):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            if getattr(win._text, "_rich", False):
                win._text._rich_highlight = True
            self._set_popup_text(result, resize=True)
            self._remember_result(True, self._result_title(True), result)
            if self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                    self._last_input or self._last_origin == "ocr"):
                self._add_history(self._last_input or "", result, False,
                            self.cfg.get(CFG.HISTORY_LIMIT, 100),
                            is_code=False, kind=self._history_kind())
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.actions"), state="normal",
                           cursor="hand2")
            except Exception:
                pass

    def _current_popup_text(self):
        if self.popup and getattr(self.popup, "_text", None):
            return self.popup._text.get("1.0", "end-1c")
        return ""

    def _copy_text_content(self, content):
        try:
            pyperclip.copy(content)
            return True
        except Exception as e:
            log_error("copy_text", e)
            return False

    def _flash_popup_button(self, attr, busy_text, reset_text, delay=1200):
        win = self.popup
        btn = getattr(win, attr, None) if win else None
        if btn is None:
            return
        try:
            btn.config(text=busy_text)
            win.after(delay, lambda: (
                self.popup and getattr(self.popup, attr, None)
                and getattr(self.popup, attr).config(text=reset_text)))
        except Exception:
            pass

    def _copy_bilingual_result(self):
        result = self._current_popup_text()
        if not result:
            return
        if self._last_input:
            content = (
                f"{i18n.get('result.source_label')}:\n{self._last_input}\n\n"
                f"{i18n.get('result.output_label')}:\n{result}"
            )
        else:
            content = result
        if self._copy_text_content(content):
            self._flash_popup_button("_actions_btn", i18n.get("result.copied"), i18n.get("result.actions"))

    def _transform_result(self, mode):
        item = RESULT_ACTION_PROMPTS.get(mode)
        current = self._current_popup_text()
        if not item or not current:
            return
        win = self.popup
        btn = getattr(win, "_actions_btn", None) if win else None
        if btn is not None:
            try:
                btn.config(text=i18n.get(item[0]) + "…", state="disabled",
                           cursor="watch")
            except Exception:
                pass
        threading.Thread(target=self._do_transform_result,
                         args=(mode, current), daemon=True).start()

    def _do_transform_result(self, mode, current):
        prompt = RESULT_ACTION_PROMPTS.get(mode, ("", ""))[1]
        try:
            ok, result = self._call_claude(current, prompt)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(0, lambda: self._apply_result_transform(ok, result))

    def _apply_result_transform(self, ok, result):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            if getattr(win._text, "_rich", False):
                win._text._rich_highlight = True
            self._set_popup_text(result, resize=True)
            self._remember_result(True, self._result_title(True), result)
            if self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                    self._last_input or self._last_origin == "ocr"):
                self._add_history(self._last_input or "", result,
                            is_single_word(self._last_input),
                            self.cfg.get(CFG.HISTORY_LIMIT, 100),
                            is_code=(self._last_class == "code"),
                            kind=self._history_kind())
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.actions"), state="normal", cursor="hand2")
            except Exception:
                pass

    def _explain_code_in_result(self):
        """Button handler: explain the code in the current result. Runs the
        model off the main thread so the UI stays responsive; this is a
        user-initiated action, not on the translation hot path, so it never
        affects translation speed."""
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_explain_btn", None)
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.explaining"), state="disabled", cursor="watch")
            except Exception:
                pass
        base = win._text.get("1.0", "end-1c")
        src = self._last_input or base
        threading.Thread(target=self._do_explain_code, args=(src, base),
                         daemon=True).start()

    def _do_explain_code(self, src, base):
        try:
            ok, explanation = self._call_claude(src, CODE_EXPLAIN_APPEND_PROMPT)
        except Exception as e:
            ok, explanation = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(
            0, lambda: self._append_code_explanation(ok, base, explanation))

    def _append_code_explanation(self, ok, base, explanation):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_explain_btn", None)
        if not ok:
            if btn is not None:
                try:
                    btn.config(text=i18n.get("result.explain"), state="normal",
                               cursor="hand2")
                except Exception:
                    pass
            explanation = explanation or i18n.get("result.explain_failed")
            return
        divider = i18n.get("result.explain_divider")
        combined = base + divider + explanation
        # Final frame: highlight code blocks in the combined result.
        if getattr(win._text, "_rich", False):
            win._text._rich_highlight = True
        # _set_popup_text branches on layout: centred refits, dynamic resizes.
        self._set_popup_text(combined, resize=True)
        self._remember_result(True, self._result_title(True), combined)
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.explained"), state="disabled",
                           cursor="arrow")
            except Exception:
                pass

    # ---------- Popup ----------
