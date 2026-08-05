"""cc_app_results — post-result action methods for TranslatorApp.

Mixed into TranslatorApp as ResultActionsMixin. Pure mechanical extraction from
translator.pyw of the methods that operate on an already-shown translation
popup: the explain-code / result-actions buttons and menu, retranslate-to-language,
tone/length transforms (concise / formal / summary), copy (plain + bilingual),
and in-place code explanation.

Follow-up actions (retranslation and rewrites) append their output below the
existing translation with a labelled divider rather than replacing it, always
transforming the primary result snapshot so chained actions never compound.
These appended sections are annotations, not new translations, so they are not
written to history.

Imports only leaf modules (cc_core / i18n / pyperclip / stdlib), never translator,
so there is no import cycle.
"""

import threading
import tkinter as tk

import pyperclip
import i18n
from cc_core import (
    DIRECTION_MODES, LANGUAGES,
    SYSTEM_SUFFIX, CODE_EXPLAIN_APPEND_PROMPT, RESULT_ACTION_PROMPTS,
    is_single_word, log_error,
)

# Optional v2 renderer (needs Pillow). Guarded so a missing Pillow never breaks
# the result actions; every v2 path also gates on getattr(win, "_v2", False).
try:
    import cc_ui_v2 as ccv2
except Exception:
    ccv2 = None


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
        """For a selection detected as code, add a '作为文字翻译' escape-hatch
        button to the code-explanation popup. Clicking it re-runs the original
        input as a plain-text translation, overriding the code heuristic — the
        one-click fix for a sentence that was wrongly explained as code."""
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
        '作为文字翻译' button; never on the hot path, so it adds no latency."""
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
            items = self._build_result_action_items()
            win._actions_items = items
            win_v2 = bool(getattr(win, "_v2", False)) and ccv2 is not None
            if not win_v2:
                t = self.theme
                menu = tk.Menu(
                    win, tearoff=0,
                    bg=t.get("popup_bg", t["bg"]), fg=t["fg"],
                    activebackground=t["accent"], activeforeground="#ffffff",
                    bd=0, relief="flat",
                    font=("Microsoft YaHei UI", 9))
                for kind, label, cmd in items:
                    if kind == "sep":
                        menu.add_separator()
                    else:
                        menu.add_command(label=label, command=cmd)
                win._actions_menu = menu
            btn = mk(i18n.get("result.actions"), lambda: self._show_result_actions_menu(win))
            btn.pack(side="right", padx=(0, 4))
            win._actions_btn = btn
            win._has_actions_btn = True
        except Exception:
            pass

    def _build_result_action_items(self):
        """The post-result action menu contents as a list of
        ``(kind, label, command)`` tuples (``kind`` is 'cmd' or 'sep'). Shared by
        the legacy native tk.Menu and the v2 custom dropdown so both stay in
        sync: alternate-language retranslation, bilingual copy, and one-click
        rewrites (concise / formal / summary)."""
        items = []
        if self._last_input:
            for code, (zh_name, en_name) in LANGUAGES.items():
                lang_name = self._language_display_name(code, zh_name, en_name)
                items.append((
                    "cmd",
                    i18n.get("result.retranslate_to").format(language=lang_name),
                    (lambda c=code: self._retranslate_to(c))))
            items.append(("sep", None, None))
            items.append((
                "cmd", i18n.get("result.copy_bilingual"),
                self._copy_bilingual_result))
            items.append(("sep", None, None))
        for mode in ("concise", "formal", "summary"):
            label_key = RESULT_ACTION_PROMPTS[mode][0]
            items.append((
                "cmd", i18n.get(label_key),
                (lambda m=mode: self._transform_result(m))))
        return items

    def _show_result_actions_menu(self, win):
        # v2 gets a modern rounded dropdown; legacy keeps the native tk.Menu.
        if bool(getattr(win, "_v2", False)) and ccv2 is not None:
            self._show_v2_actions_menu(win)
            return
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

    def _show_v2_actions_menu(self, win):
        """A modern rounded dropdown for the v2 result popup: a deep-navy card
        with a thin brand hairline, soft item hover, and drawn separators — the
        native tk.Menu looked dated (system-grey, hard border) against the v2
        skin. Toggles closed if already open; dismisses on Escape / focus-out /
        item click."""
        btn = getattr(win, "_actions_btn", None)
        if btn is None:
            return
        existing = getattr(self, "_v2_actions_menu_win", None)
        if existing is not None:
            try:
                if tk.Toplevel.winfo_exists(existing):
                    existing.destroy()
            except Exception:
                pass
            self._v2_actions_menu_win = None
            return
        items = getattr(win, "_actions_items", None) or \
            self._build_result_action_items()
        if not items:
            return

        pal = self._v2_palette()
        scale = self._ui_scale()
        colors = self._v2_tk_colors()
        panel = colors["panel"]
        sep_col = colors["border"]
        fg = ccv2.rgb_to_hex(pal["fg"])
        sub = colors["hint"]
        hover_bg = (ccv2.rgb_to_hex((46, 51, 96)) if pal["is_dark"]
                    else ccv2.rgb_to_hex((228, 232, 246)))

        menu = tk.Toplevel(win)
        menu.withdraw()
        menu.overrideredirect(True)
        menu._v2 = True
        menu._v2_resizable = False
        self._v2_actions_menu_win = menu

        def _close():
            try:
                menu.destroy()
            finally:
                if getattr(self, "_v2_actions_menu_win", None) is menu:
                    self._v2_actions_menu_win = None

        def _on_destroy(_e=None):
            if getattr(self, "_v2_actions_menu_win", None) is menu:
                self._v2_actions_menu_win = None
        menu.bind("<Destroy>", _on_destroy, add="+")

        radius = ccv2.scaled(14, scale)
        card = self._rounded_shell(menu, radius, panel, sep_col)
        inner = tk.Frame(card, bg=panel, bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True,
                   padx=ccv2.scaled(6, scale), pady=ccv2.scaled(6, scale))
        FONT = ("Microsoft YaHei UI", 10)

        ipadx = ccv2.scaled(12, scale)
        ipady = ccv2.scaled(6, scale)
        for kind, label, cmd in items:
            if kind == "sep":
                tk.Frame(inner, bg=sep_col, height=1, bd=0,
                         highlightthickness=0).pack(
                             fill="x", padx=ccv2.scaled(10, scale),
                             pady=ccv2.scaled(4, scale))
                continue
            it = tk.Label(inner, text=label, bg=panel, fg=fg, anchor="w",
                          font=FONT, padx=ipadx, pady=ipady, cursor="hand2",
                          bd=0, highlightthickness=0)
            it.pack(fill="x")
            it.bind("<Enter>", lambda e, w=it: w.config(bg=hover_bg))
            it.bind("<Leave>", lambda e, w=it: w.config(bg=panel))
            it.bind("<Button-1>",
                    lambda e, c=cmd: (_close(), c() if c else None))

        menu.update_idletasks()
        card.update_idletasks()
        ci = int(getattr(menu, "_card_inset", radius))
        w = card.winfo_reqwidth() + 2 * ci
        h = card.winfo_reqheight() + 2 * ci

        gap = ccv2.scaled(6, scale)
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + gap
        x, y = self._clamp_to_monitor(x, y, w, h, ref=(x, y))

        self._record_v2_size(menu, w, h)
        menu.geometry(f"{w}x{h}+{x}+{y}")
        menu.deiconify()
        menu.update_idletasks()
        try:
            menu._round_redraw()
        except Exception:
            pass
        try:
            menu.attributes("-topmost", True)
        except Exception:
            pass
        menu.lift()
        menu.focus_force()

        # Arm outside-dismiss only after construction so the initial focus
        # settling doesn't immediately close the menu.
        menu._armed = False

        def _arm():
            menu._armed = True
        menu.after(180, _arm)
        menu.bind("<Escape>", lambda e: _close())
        menu.bind(
            "<FocusOut>",
            lambda e: _close() if getattr(menu, "_armed", False) else None)

    def _retranslate_to(self, code):
        src = self._last_input
        prompt = DIRECTION_MODES.get(f"to_{code}")
        if not src or not prompt:
            return
        names = LANGUAGES.get(code, (code, code))
        label = i18n.get("result.retranslate_to").format(
            language=self._language_display_name(code, names[0], names[1]))
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
            args=(src, prompt + SYSTEM_SUFFIX, label,
                  self._provider_selection(), win), daemon=True).start()

    def _do_retranslate(self, src, prompt, label, selection=None,
                        expected_win=None):
        try:
            ok, result = self._call_model(src, prompt, selection)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(
            0, lambda: self._apply_retranslation(
                ok, result, label, expected_win))

    def _apply_retranslation(self, ok, result, label, expected_win=None):
        if expected_win is not None and self.popup is not expected_win:
            return
        win = expected_win or self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            # Append below the existing translation with a labelled divider,
            # preserving the original result (like the code-explanation flow).
            if expected_win is None:
                self._append_result_section(label, result)
            else:
                self._append_result_section(label, result, win)
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

    def _result_primary_text(self, win):
        """The main translation, snapshotted before any follow-up section is
        appended. Rewrites always transform this original result rather than the
        growing popup text, so chaining actions never compounds earlier output.
        Captured lazily on first use."""
        val = getattr(win, "_primary_result", None)
        if val is None:
            val = self._current_popup_text()
            win._primary_result = val
        return val

    def _language_display_name(self, code, zh_name, en_name):
        """The menu-facing name for a target language, honouring the app UI
        language (Chinese shown as 中文/Chinese, others by their own name)."""
        if i18n.get_language() == "en_US":
            return i18n.get("result.language_chinese") if code == "zh" else en_name
        return zh_name

    def _append_result_section(self, label, addition, expected_win=None):
        """Append a follow-up section (rewrite / retranslation) below the current
        result with a labelled divider, mirroring the code-explanation flow: the
        existing translation is preserved and the new output is added beneath it.
        Follow-up sections are annotations, so they are not written to history."""
        if expected_win is not None and self.popup is not expected_win:
            return
        win = expected_win or self.popup
        if not win or not getattr(win, "_text", None):
            return
        # Snapshot the primary result before mutating the visible text so later
        # rewrites still transform the original translation.
        self._result_primary_text(win)
        base = self._current_popup_text()
        divider = i18n.get("result.section_divider").format(label=label)
        combined = base + divider + (addition or "")
        if getattr(win._text, "_rich", False):
            win._text._rich_highlight = True
        self._set_popup_text(combined, resize=True, append=True)
        self._remember_result(True, self._result_title(True), combined)

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
        win = self.popup
        if not item or not win or not getattr(win, "_text", None):
            return
        # Rewrites transform the original translation (primary snapshot), not the
        # growing popup text, so chaining actions never compounds earlier output.
        primary = self._result_primary_text(win)
        if not primary:
            return
        label = i18n.get(item[0])
        btn = getattr(win, "_actions_btn", None)
        if btn is not None:
            try:
                btn.config(text=label + "…", state="disabled",
                           cursor="watch")
            except Exception:
                pass
        threading.Thread(target=self._do_transform_result,
                         args=(mode, primary, label, self._provider_selection(),
                               win),
                         daemon=True).start()

    def _do_transform_result(self, mode, current, label, selection=None,
                             expected_win=None):
        prompt = RESULT_ACTION_PROMPTS.get(mode, ("", ""))[1]
        try:
            ok, result = self._call_model(current, prompt, selection)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(
            0, lambda: self._apply_result_transform(
                ok, result, label, expected_win))

    def _apply_result_transform(self, ok, result, label, expected_win=None):
        if expected_win is not None and self.popup is not expected_win:
            return
        win = expected_win or self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            # Append below the existing translation with a labelled divider,
            # preserving the original result (like the code-explanation flow).
            if expected_win is None:
                self._append_result_section(label, result)
            else:
                self._append_result_section(label, result, win)
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
        self._result_primary_text(win)
        base = win._text.get("1.0", "end-1c")
        src = self._last_input or base
        threading.Thread(
            target=self._do_explain_code,
            args=(src, base, self._provider_selection(), win),
            daemon=True).start()

    def _do_explain_code(self, src, base, selection=None,
                         expected_win=None):
        try:
            ok, explanation = self._call_model(
                src, CODE_EXPLAIN_APPEND_PROMPT, selection)
        except Exception as e:
            ok, explanation = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(
            0, lambda: self._append_code_explanation(
                ok, base, explanation, expected_win))

    def _append_code_explanation(self, ok, base, explanation,
                                 expected_win=None):
        if expected_win is not None and self.popup is not expected_win:
            return
        win = expected_win or self.popup
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
        self._result_primary_text(win)
        divider = i18n.get("result.explain_divider")
        combined = base + divider + explanation
        # Final frame: highlight code blocks in the combined result.
        if getattr(win._text, "_rich", False):
            win._text._rich_highlight = True
        # _set_popup_text branches on layout: centred refits, dynamic resizes.
        # append=True keeps the reader's scroll position (grow downward, no jump).
        self._set_popup_text(combined, resize=True, append=True)
        self._remember_result(True, self._result_title(True), combined)
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.explained"), state="disabled",
                           cursor="arrow")
            except Exception:
                pass

    # ---------- Popup ----------
