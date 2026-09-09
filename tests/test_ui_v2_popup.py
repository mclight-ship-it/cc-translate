"""Integration tests for the Phase 1 v2 result-popup skin (cc_app_popup + cc_ui_v2).

These build the REAL result popup on the hidden test desktop (see tests/_headless
via test_full's shared root) with the UI_V2 flag ON, and assert:
  * the flag gates correctly (OFF -> byte-for-byte legacy, no v2 attributes),
  * the v2 shell wires up the GradientBackground and paints a face photo,
  * the header shows the gradient title + app-mark tile images,
  * the shared geometry/streaming engine still runs (popup grows downward as it
  streams), and v2 no longer inflates the window with a wide glow margin
  (the frame is now a thin baked brand hairline).

The class is skipped when Pillow (the v2 renderer) or Tk is unavailable, since
the v2 skin degrades to legacy in exactly that case.
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from tests._tr import tr
import tests.test_full as tf  # reuse its shared-root + headless-app helpers
import cc_app_ocr
import cc_app_popup
import cc_app_settings
from cc_rich import encode_more_senses, encode_source_details
from cc_rich import encode_pronunciation

try:
    import cc_ui_v2 as ccv2
    _V2_OK = ccv2.ui_v2_available()
except Exception:
    ccv2 = None
    _V2_OK = False


class TestSourceCardPosition(unittest.TestCase):
    def test_card_stays_on_negative_coordinate_secondary_monitor(self):
        position = cc_app_popup._source_card_position(
            (-1500, 400, 120, 20),
            (320, 240),
            (-1920, 0, 0, 1040),
        )
        self.assertEqual(position, (-1500, 426))

    def test_card_flips_above_anchor_near_monitor_bottom(self):
        position = cc_app_popup._source_card_position(
            (2100, 980, 120, 24),
            (320, 240),
            (1920, 0, 3840, 1040),
        )
        self.assertEqual(position, (2100, 734))


@unittest.skipUnless(_V2_OK, "cc_ui_v2 renderer (Pillow) unavailable")
class TestV2ResultPopup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tf._get_shared_root()
        except Exception as e:
            raise unittest.SkipTest(f"Tk not available: {e}")

    def _app(self, v2=True, layout="dynamic"):
        app = tf._make_headless_app()
        app.cfg[tr.CFG.UI_V2] = bool(v2)
        app.cfg[tr.CFG.POPUP_LAYOUT] = layout
        self.addCleanup(lambda: self._destroy(app))
        return app

    def _destroy(self, app):
        win = getattr(app, "popup", None) or getattr(self, "_win", None)
        for w in (win,):
            try:
                if w is not None and tr.tk.Toplevel.winfo_exists(w):
                    w.destroy()
            except Exception:
                pass

    def _kill_later(self, win):
        self._win = win
        self.addCleanup(lambda: self._safe(win))

    @staticmethod
    def _safe(win):
        try:
            if tr.tk.Toplevel.winfo_exists(win):
                win.destroy()
        except Exception:
            pass

    # -- gating -------------------------------------------------------------
    def test_flag_off_is_legacy(self):
        app = self._app(v2=False)
        self.assertFalse(app._v2_popup_on())
        self.assertEqual(app._v2_margin(), 0)
        win = app._make_popup("hello world")
        app.popup = win
        self._kill_later(win)
        self.assertFalse(getattr(win, "_v2", False),
                         "flag off must build the legacy popup")
        self.assertFalse(hasattr(win, "_v2_gb"),
                         "legacy popup must not create a GradientBackground")

    def test_flag_on_activates_v2(self):
        app = self._app(v2=True)
        self.assertTrue(app._v2_popup_on())
        # v2 no longer reserves a wide glow-halo margin — the frame is a thin
        # brand hairline baked at the perimeter, so no extra window inset.
        self.assertEqual(app._v2_margin(), 0)

    # -- shell --------------------------------------------------------------
    def test_v2_shell_paints_gradient_face(self):
        app = self._app(v2=True)
        win = app._make_popup("你好，世界")
        app.popup = win
        self._kill_later(win)
        self.assertTrue(getattr(win, "_v2", False))
        self.assertIsInstance(getattr(win, "_v2_gb", None),
                              ccv2.GradientBackground)
        # Rounded colour-key card is still used (the "black corners" guard).
        self.assertTrue(hasattr(win, "_round_redraw"))
        self.assertEqual(
            str(win.wm_attributes("-transparentcolor")).lower(),
            tr.ROUND_KEY_COLOR.lower())
        # Force a redraw at the real size and confirm a face photo was baked.
        win.update_idletasks()
        win._round_redraw()
        face = getattr(win, "_v2_face", None)
        self.assertIsNotNone(face, "v2 shell should paint a gradient face image")
        self.assertGreater(face.width(), 0)
        self.assertGreater(face.height(), 0)

    def test_v2_header_has_gradient_title_and_logo(self):
        app = self._app(v2=True)
        win = app._make_popup("译文")
        app.popup = win
        self._kill_later(win)
        # The header packs the real app-logo image label then a gradient title
        # image label (v2 uses the brand logo, never a drawn "CC" placeholder).
        bar = getattr(win, "_btn_bar", None)
        self.assertIsNotNone(bar)
        image_labels = [c for c in bar.winfo_children()
                        if isinstance(c, tk.Label) and c.cget("image")]
        self.assertGreaterEqual(
            len(image_labels), 2,
            "v2 header should show the app logo + gradient title as images")

    def test_local_dictionary_word_has_inline_lightning_badge(self):
        app = self._app(v2=True)
        win = app._make_popup("## 本地释义 [[cc-instant]]")
        app.popup = win
        self._kill_later(win)
        holders = getattr(win._text, "_rich_badges", ())
        self.assertEqual(len(holders), 1)
        badge = holders[0]._badge_icon
        self.assertTrue(badge.cget("image"))
        self.assertEqual(badge.winfo_reqwidth(), badge.winfo_reqheight())
        self.assertGreater(holders[0].winfo_reqwidth(), badge.winfo_reqwidth())
        self.assertEqual(win._text.get("1.0", "end-1c"), "本地释义")

    def test_regular_result_has_no_inline_lightning_badge(self):
        app = self._app(v2=True)
        win = app._make_popup("AI 结果")
        app.popup = win
        self._kill_later(win)
        self.assertEqual(getattr(win._text, "_rich_badges", ()), [])

    def test_dictionary_pronunciation_is_colored_upright_text(self):
        app = self._app(v2=True)
        win = app._make_popup(encode_pronunciation("Zhōng guó"))
        app.popup = win
        self._kill_later(win)
        font = tkfont.Font(
            root=win,
            font=win._text.tag_cget("rich_pronunciation", "font"))
        self.assertEqual(font.actual("slant"), "roman")
        self.assertEqual(
            win._text.tag_cget("rich_pronunciation", "foreground"),
            app.theme["popup_hint"])

    def test_dictionary_bullets_are_neutral_normal_weight(self):
        app = self._app(v2=True)
        win = app._make_popup("- definition")
        app.popup = win
        self._kill_later(win)
        font = tkfont.Font(
            root=win, font=win._text.tag_cget("rich_bullet", "font"))
        self.assertEqual(font.actual("weight"), "normal")
        self.assertEqual(
            win._text.tag_cget("rich_bullet", "foreground"),
            app.theme["fg"])

    def test_dictionary_sources_are_collapsed_but_preserved_for_copy(self):
        app = self._app(v2=True)
        details = [{
            "id": "fixture", "label": "Fixture source", "version": "1",
            "license": "Test license",
        }]
        win = app._make_popup(encode_source_details(details))
        app.popup = win
        self._kill_later(win)
        button = win._text._rich_badges[0]
        self.assertIn("Fixture source 1", win._text._source_copy_text)
        app._copy_text_content = mock.Mock(return_value=True)
        app._copy_result()
        copied = app._copy_text_content.call_args.args[0]
        self.assertIn("Sources / 来源: Fixture source 1", copied)
        app._show_dictionary_sources_menu(button, details)
        card = button._sources_card
        labels = [label.cget("text") for label in card._source_labels]
        self.assertIn(tr.i18n.get("result.sources_title"), labels)
        self.assertNotIn("result.sources_title", labels)
        self.assertIn("Fixture source 1", labels)
        self.assertIn("Test license", labels)
        self.assertEqual(
            card._notices_button.cget("text"),
            tr.i18n.get("about.third_party_notices"))
        self.assertGreaterEqual(card.winfo_width(), 440)
        self.assertTrue(all(
            int(label.cget("wraplength")) == card._source_content_width
            for label in card._source_labels))
        card.destroy()

    def test_dictionary_source_names_and_licenses_wrap_inside_card(self):
        app = self._app(v2=True)
        owner = tk.Toplevel(app.root)
        button = tk.Button(owner, text="Sources")
        button.pack()
        owner.update_idletasks()
        self._kill_later(owner)
        details = [{
            "id": "fixture",
            "label": (
                "Chinese Open Wordnet / Open Multilingual Wordnet "
                "alignment supplement"),
            "version": "2.0",
            "license": (
                "A deliberately long license description that must wrap "
                "inside the source card instead of being clipped."),
        }]

        app._show_dictionary_sources_menu(button, details)
        card = button._sources_card
        card.update_idletasks()
        content_width = card._source_content_width
        detail_labels = [
            label for label in card._source_labels
            if label.cget("text") != tr.i18n.get("result.sources_title")
        ]
        self.assertTrue(detail_labels)
        self.assertTrue(all(
            int(label.cget("wraplength")) == content_width
            for label in detail_labels))
        self.assertTrue(all(
            label.winfo_reqwidth() <= content_width
            for label in detail_labels))
        card.destroy()

    def test_more_senses_is_an_embedded_control(self):
        app = self._app(v2=True)
        app._expand_local_dictionary_senses = mock.Mock()
        win = app._make_popup(encode_more_senses(4, "expanded result"))
        app.popup = win
        self._kill_later(win)
        button = win._text._rich_badges[0]
        self.assertIn("4", button.cget("text"))
        button.invoke()
        app._expand_local_dictionary_senses.assert_called_once_with(
            "expanded result")

    def test_copy_includes_senses_hidden_by_collapsed_view(self):
        app = self._app(v2=True)
        expanded = "## word [[cc-instant]]\n- visible\n- hidden"
        win = app._make_popup(
            "## word [[cc-instant]]\n- visible\n"
            + encode_more_senses(1, expanded))
        app.popup = win
        self._kill_later(win)
        app._copy_text_content = mock.Mock(return_value=True)

        app._copy_result()

        copied = app._copy_text_content.call_args.args[0]
        self.assertIn("visible", copied)
        self.assertIn("hidden", copied)
        self.assertNotIn("[[cc-", copied)

    def test_error_popup_keeps_plain_title(self):
        # Error popups must stay plain text (never gradient) so a raw error
        # string is legible and never mis-rendered.
        app = self._app(v2=True)
        win = app._make_popup("boom", is_error=True, title="Error")
        app.popup = win
        self._kill_later(win)
        bar = getattr(win, "_btn_bar", None)
        text_titles = [c for c in bar.winfo_children()
                       if isinstance(c, tk.Label) and c.cget("text")]
        self.assertTrue(any("Error" in c.cget("text") for c in text_titles),
                        "error popup title should remain plain text")

    # -- geometry / streaming ----------------------------------------------
    def test_v2_does_not_inflate_window_vs_legacy(self):
        # Same message: the v2 popup must NOT be substantially wider than legacy
        # any more — the old wide glow margin (~2x the halo inset) is gone, the
        # frame is now a thin baked hairline that adds no window width.
        msg = "measure me"
        legacy = self._app(v2=False)
        lw = legacy._make_popup(msg)
        legacy.popup = lw
        self.addCleanup(lambda: self._safe(lw))
        lw.update_idletasks(); lw.update()
        legacy_w = lw.winfo_width()

        v2app = self._app(v2=True)
        vw = v2app._make_popup(msg)
        v2app.popup = vw
        self._kill_later(vw)
        vw.update_idletasks(); vw.update()
        v2_w = vw.winfo_width()

        # Allow a small delta for the gradient-title image vs plain-text title
        # metrics, plus the intentional bigger v2 corner radius (the content card
        # is inset by the radius on each side, so a larger radius legitimately
        # widens the window a little) — but nothing like the old ~2x halo margin.
        radius_delta = 2 * (tr.V2_CORNER_RADIUS - tr.POPUP_CORNER_RADIUS)
        self.assertLessEqual(
            v2_w, legacy_w + 12 + radius_delta,
            "v2 popup should no longer be widened by a glow-halo margin")

    def test_v2_streaming_grows_downward(self):
        app = self._app(v2=True)
        app._ss = tr.StreamSession()
        win = app._make_popup("摘要", anchor=(300, 300), reveal=False)
        app.popup = win
        self._kill_later(win)

        app._set_popup_text("## 摘要\n第一段内容", stream_grow=True)
        win.update_idletasks(); win.update()
        self.assertTrue(app._ss.placed)
        h0 = win.winfo_height()

        big = "## 摘要\n" + "\n".join("这是第%d行流式内容" % i
                                     for i in range(1, 30))
        app._set_popup_text(big, stream_grow=True)
        win.update_idletasks(); win.update()
        h1 = win.winfo_height()

        self.assertGreater(h1, h0,
                           "v2 streaming popup should grow downward with content")
        # The v2 shell repainted at the grown size without error.
        self.assertIsNotNone(getattr(win, "_v2_face", None))

    def test_v2_redraw_uses_target_size_when_canvas_is_stale(self):
        # Regression: after a programmatic geometry() grow, Tk leaves
        # canvas.winfo_width/height reporting the OLD size until a full
        # update() runs. A manual redraw at that moment must paint the
        # gradient face at the INTENDED size (win._v2_size), otherwise the
        # grown edge shows a transparent colour-key strip ("black halo") and
        # the content card is too short (last streamed line clipped).
        app = self._app(v2=True)
        app._ss = tr.StreamSession()
        win = app._make_popup("摘要", anchor=(300, 300), reveal=False)
        app.popup = win
        self._kill_later(win)

        big = "## 摘要\n" + "\n".join("这是第%d行流式内容" % i
                                     for i in range(1, 40))
        # stream_grow deliberately avoids a settling update() per frame, so the
        # canvas size is stale right after this call -- exactly the bad frame.
        app._set_popup_text(big, stream_grow=True)

        target = getattr(win, "_v2_size", None)
        self.assertIsNotNone(target, "resize sites must record win._v2_size")
        cv = win._round_canvas
        # Prove the canvas really is stale (smaller than target) at this point.
        self.assertLess(cv.winfo_height(), target[1],
                        "precondition: canvas height should lag the grow")
        # Manual redraw (event=None path) -- must consume _v2_size, not winfo.
        win._round_redraw()
        face = getattr(win, "_v2_face", None)
        self.assertIsNotNone(face)
        self.assertEqual((face.width(), face.height()), tuple(target),
                         "v2 face must be painted at the target size, not the "
                         "stale canvas size")

    # -- quick-input (Phase 2) ---------------------------------------------
    def test_v2_quick_input_uses_v2_skin(self):
        app = self._app(v2=True)
        app._open_quick_input()
        win = app.quick_input_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertTrue(getattr(win, "_v2", False),
                        "quick-input should build the v2 skin when the flag is on")
        # The Translate action is a brand-gradient pill IMAGE (not a text pill).
        btn = getattr(win, "_quick_input_submit_btn", None)
        self.assertIsNotNone(btn)
        self.assertTrue(str(btn.cget("image")),
                        "v2 quick-input Translate button should be a gradient "
                        "pill image")
        self.assertFalse(str(btn.cget("text")),
                         "v2 gradient pill button carries no text glyph")
        self.assertEqual(
            win._quick_input_hint.cget("text"),
            tr.i18n.get("quick_input.hint"))
        self.assertIn("Enter", win._quick_input_hint.cget("text"))

    def test_v2_quick_input_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._open_quick_input()
        win = app.quick_input_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertFalse(getattr(win, "_v2", False),
                         "flag off must build the legacy quick-input window")
        # Legacy Translate button is a plain text pill (no image).
        btn = getattr(win, "_quick_input_submit_btn", None)
        self.assertIsNotNone(btn)
        self.assertTrue(str(btn.cget("text")),
                        "legacy quick-input Translate button is a text pill")

    # -- concept polish (glow logo / chip buttons / no divider / 1-line) ----
    def test_v2_header_buttons_are_image_chips(self):
        # The result popup's top-right Copy/pin/close become modern baked chip
        # buttons (image-based), not plain text pills.
        app = self._app(v2=True)
        win = app._make_popup("译文")
        app.popup = win
        self._kill_later(win)
        bar = getattr(win, "_btn_bar", None)
        chips = [c for c in bar.winfo_children()
                 if isinstance(c, tk.Button) and str(c.cget("image"))]
        self.assertGreaterEqual(
            len(chips), 3, "Copy/pin/close should be image chip buttons")

    def test_v2_header_has_no_divider(self):
        # The concept has no hairline between the title row and the body: the
        # header frame should hold only the bar (no extra 1px separator frame).
        app = self._app(v2=True)
        win = app._make_popup("译文")
        app.popup = win
        self._kill_later(win)
        header = getattr(win, "_bar", None)
        frames = [c for c in header.winfo_children()
                  if isinstance(c, tk.Frame)]
        self.assertEqual(len(frames), 1,
                         "v2 header must not add a divider frame")

    def test_v2_copy_feedback_swaps_chip(self):
        # Copy feedback must still work with the image chip (re-bakes the label
        # into a new chip image instead of setting button text).
        app = self._app(v2=True)
        win = app._make_popup("hello")
        app.popup = win
        self._kill_later(win)
        self.assertTrue(hasattr(win, "_copy_set"))
        self.assertEqual(
            win._copy_btn.cget("text"), tr.i18n.get("result.copy"))
        before = str(win._copy_btn.cget("image"))
        with unittest.mock.patch.object(
                app, "_copy_text_content", return_value=True) as copy_text:
            app._copy_result()
        self.assertEqual(
            win._copy_btn.cget("text"), tr.i18n.get("result.copied"))
        copy_text.assert_called_once_with("hello")
        after = str(win._copy_btn.cget("image"))
        self.assertNotEqual(before, after,
                            "copy feedback should swap the chip image")

    def test_v2_follow_up_shows_processing_in_action_chip(self):
        app = self._app(v2=True)
        app._last_input = "a complete source sentence"
        app._last_class = "text"
        win = app._make_popup("translated sentence")
        app.popup = win
        self._kill_later(win)
        app._maybe_add_result_actions_button(win)
        btn = win._actions_btn
        before = str(btn.cget("image"))
        original_command = str(btn.cget("command"))

        app._set_result_actions_busy(win, True)

        self.assertEqual(
            btn.cget("text"), tr.i18n.get("result.processing"))
        self.assertEqual(str(btn.cget("state")), "normal")
        self.assertFalse(btn._chip_enabled)
        self.assertEqual(str(btn.cget("command")), original_command)
        self.assertNotEqual(
            before, str(btn.cget("image")),
            "processing feedback should be baked into a new action-chip image")

        app._set_result_actions_busy(win, False)
        self.assertEqual(btn.cget("text"), tr.i18n.get("result.actions"))
        self.assertEqual(str(btn.cget("state")), "normal")
        self.assertTrue(btn._chip_enabled)

    def test_v2_quick_input_is_single_line_no_scrollbar(self):
        app = self._app(v2=True)
        app._open_quick_input()
        win = app.quick_input_win
        self._kill_later(win)
        ed = getattr(win, "_quick_input_text", None)
        self.assertIsNotNone(ed)
        self.assertEqual(str(ed.cget("wrap")), "none")
        self.assertEqual(int(ed.cget("height")), 1)
        self.assertTrue(ed.bind("<Return>"),
                        "single-line field should submit on Enter")

        def _walk(w):
            for c in w.winfo_children():
                yield c
                yield from _walk(c)
        scrolls = [c for c in _walk(win)
                   if isinstance(c, tr.ttk.Scrollbar)]
        self.assertEqual(len(scrolls), 0,
                         "v2 single-line quick-input must have no scrollbar")

    def test_v2_quick_input_enter_submits(self):
        app = self._app(v2=True)
        app._show_loading = unittest.mock.Mock()
        app._open_quick_input()
        win = app.quick_input_win
        self._kill_later(win)
        editor = win._quick_input_text
        editor.insert("1.0", "translate this")
        win.update()
        editor.focus_force()

        editor.event_generate("<KeyPress-Return>")
        win.update()

        app._show_loading.assert_called_once_with("translate this")


    # -- history window v2 skin --------------------------------------------
    def test_v2_history_uses_v2_skin(self):
        app = self._app(v2=True)
        app._open_history()
        win = app.history_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertTrue(getattr(win, "_v2", False),
                        "history window should build the v2 skin when the flag "
                        "is on")
        # Fixed-size card: its whole ring drags rather than resizing.
        self.assertFalse(getattr(win, "_v2_resizable", True),
                         "v2 history card is a fixed-size (ring-drag) window")

        def _walk(w):
            for c in w.winfo_children():
                yield c
                yield from _walk(c)
        image_btns = [c for c in _walk(win)
                      if isinstance(c, tk.Button) and str(c.cget("image"))]
        self.assertGreaterEqual(
            len(image_btns), 1,
            "v2 history chrome uses baked image buttons (soft pills / ghost "
            "close)")

    def test_v2_history_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._open_history()
        win = app.history_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertFalse(getattr(win, "_v2", False),
                         "flag off must build the legacy history window")

    def test_v2_history_uses_card_list_not_listbox(self):
        # The roomy POC redesign draws entries as canvas cards, so the v2 body
        # must NOT contain a tk.Listbox — while the legacy body still does.
        def _walk(w):
            for c in w.winfo_children():
                yield c
                yield from _walk(c)

        app = self._app(v2=True)
        app._open_history()
        win = app.history_win
        self._kill_later(win)
        listboxes = [c for c in _walk(win) if isinstance(c, tk.Listbox)]
        self.assertEqual(
            listboxes, [],
            "v2 history should use the canvas card list, not a Listbox")

        app2 = self._app(v2=False)
        app2._open_history()
        win2 = app2.history_win
        self._kill_later(win2)
        legacy_listboxes = [c for c in _walk(win2) if isinstance(c, tk.Listbox)]
        self.assertGreaterEqual(
            len(legacy_listboxes), 1,
            "legacy history should still use a Listbox")

    def test_v2_history_collapses_secondary_actions(self):
        app = self._app(v2=True)
        app._open_history()
        win = app.history_win
        self._kill_later(win)

        def _walk(w):
            for child in w.winfo_children():
                yield child
                yield from _walk(child)

        button_texts = {
            child.cget("text")
            for child in _walk(win)
            if isinstance(child, tk.Button)
        }
        self.assertIn(tr.i18n.get("history.copy_result"), button_texts)
        self.assertIn(tr.i18n.get("history.more"), button_texts)
        self.assertNotIn(tr.i18n.get("history.copy_bilingual"), button_texts)
        self.assertNotIn(tr.i18n.get("history.rerun"), button_texts)
        self.assertNotIn(tr.i18n.get("history.clear"), button_texts)

    def test_v2_settings_uses_v2_skin(self):
        app = self._app(v2=True)
        app._open_settings()
        win = app.settings_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertTrue(getattr(win, "_v2", False),
                        "settings window should build the v2 skin when the flag "
                        "is on")
        # Fixed-size card: its whole ring drags rather than resizing.
        self.assertFalse(getattr(win, "_v2_resizable", True),
                         "v2 settings card is a fixed-size (ring-drag) window")

        def _walk(w):
            for c in w.winfo_children():
                yield c
                yield from _walk(c)
        image_btns = [c for c in _walk(win)
                      if isinstance(c, tk.Button) and str(c.cget("image"))]
        self.assertGreaterEqual(
            len(image_btns), 1,
            "v2 settings chrome uses a baked image button (ghost close)")
        close_btn = next(
            c for c in image_btns
            if c.cget("text") == tr.i18n.get("settings.label.close"))
        self.assertNotEqual(close_btn.cget("text"), "result.close")

    def test_v2_settings_only_title_header_is_draggable(self):
        app = self._app(v2=True)
        app._open_settings()
        win = app.settings_win
        self._kill_later(win)

        def _walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from _walk(child)

        win.update()
        canvas = win._round_canvas
        start = (win.winfo_x(), win.winfo_y())
        canvas.event_generate("<Button-1>", x=3, y=win.winfo_height() // 2)
        canvas.event_generate(
            "<B1-Motion>", x=23, y=win.winfo_height() // 2 + 12)
        win.update_idletasks()
        self.assertEqual((win.winfo_x(), win.winfo_y()), start)

        title = next(
            child for child in _walk(win)
            if isinstance(child, tk.Label)
            and child.cget("text") == tr.i18n.get("settings.title"))
        title.event_generate("<Button-1>", x=3, y=3)
        title.event_generate("<B1-Motion>", x=23, y=15)
        win.update_idletasks()
        self.assertEqual(
            (win.winfo_x(), win.winfo_y()),
            (start[0] + 20, start[1] + 12))

    def test_v2_settings_form_fields_use_rounded_chrome(self):
        app = self._app(v2=True)
        app._open_settings()
        win = app.settings_win
        self._kill_later(win)
        style = ttk.Style(app.root)
        self.assertNotIn(
            "CC.RoundedField", str(style.layout("CC.TCombobox")))
        self.assertNotIn(
            "CC.RoundedField", str(style.layout("CC.TSpinbox")))
        field_bg = app._settings_form_theme["list_bg"]
        for style_name in ("CC.TCombobox", "CC.TSpinbox"):
            self.assertEqual(
                style.lookup(style_name, "lightcolor", ("focus",)),
                field_bg)
            self.assertEqual(
                style.lookup(style_name, "bordercolor", ("focus",)),
                field_bg)
        def _walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from _walk(child)

        combos = [child for child in _walk(win)
                  if isinstance(child, ttk.Combobox)]
        self.assertTrue(combos)
        self.assertTrue(all(
            isinstance(combo._settings_host, tk.Canvas) for combo in combos))
        self.assertTrue(all(
            hasattr(combo._settings_host, "_field_photo") for combo in combos))
        self.assertTrue(all(
            hasattr(combo, "_settings_dropdown_toggle") for combo in combos))
        self.assertLessEqual(
            max(combo._settings_host.winfo_reqheight() for combo in combos), 50)
        combo = combos[0]
        combo._settings_dropdown_toggle()
        self.assertIsNotNone(combo._settings_dropdown_state["win"])
        combo._settings_dropdown_toggle()
        self.assertIsNone(combo._settings_dropdown_state["win"])

    def test_v2_settings_groups_labs_and_version_actions(self):
        app = self._app(v2=True)
        app.cfg[tr.CFG.PLAIN_TEXT_PASTE_ENABLED] = True
        app._plain_paste_hotkey_available = False
        app._open_settings()
        win = app.settings_win
        self._kill_later(win)

        def _walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from _walk(child)

        widgets = list(_walk(win))
        texts = {
            str(widget.cget("text"))
            for widget in widgets
            if "text" in widget.keys()
        }
        self.assertIn(tr.i18n.get("settings.label.labs_section"), texts)
        self.assertIn(tr.i18n.get("settings.label.summary_enabled"), texts)
        self.assertIn(
            tr.i18n.get("settings.label.clipboard_protection"), texts)
        self.assertIn(
            tr.i18n.get("settings.label.plain_text_paste"), texts)
        self.assertNotIn("Ctrl+Shift+K", texts)
        self.assertIn(
            tr.i18n.get("settings.label.plain_text_paste_unavailable"),
            texts)
        plain_paste_toggle = next(
            widget for widget in widgets
            if isinstance(widget, tk.Checkbutton)
            and widget.cget("text")
            == tr.i18n.get("settings.label.plain_text_paste"))
        self.assertTrue(plain_paste_toggle.get())
        self.assertIn(tr.i18n.get("settings.label.language_field"), texts)
        self.assertNotIn("Codex 流式输出 (Beta)", texts)
        self.assertNotIn("Codex streaming output (Beta)", texts)

        labs_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.labs_section"))
        update_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.update_section"))
        system_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.system_section"))
        max_chars_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.max_chars"))
        screenshot_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.screenshot_section"))
        double_press_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.double_press_window"))
        win.update_idletasks()
        self.assertGreater(
            labs_label.winfo_rootx(), win.winfo_rootx() + win.winfo_width() // 2,
            "Labs should be in the right Settings column")
        self.assertLess(
            labs_label.winfo_rooty(), update_label.winfo_rooty(),
            "Update should be the last section in the right Settings column")
        self.assertLess(
            max_chars_label.winfo_rootx(),
            win.winfo_rootx() + win.winfo_width() // 2,
            "Maximum characters should be in the left Translation section")
        self.assertLess(
            max_chars_label.winfo_rooty(), screenshot_label.winfo_rooty(),
            "Maximum characters should remain inside the Translation section")
        self.assertGreater(
            system_label.winfo_rootx(),
            win.winfo_rootx() + win.winfo_width() // 2,
            "System should be in the right Settings column")
        self.assertGreater(
            double_press_label.winfo_rootx(),
            win.winfo_rootx() + win.winfo_width() // 2,
            "Double-click interval should be grouped under System")
        self.assertNotIn(
            tr.i18n.get("settings.label.behavior_section"), texts)

        check_button = next(
            widget for widget in widgets
            if isinstance(widget, tk.Button)
            and widget.cget("text")
            == tr.i18n.get("settings.label.check_update_action"))
        auto_update_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text") == tr.i18n.get("settings.label.auto_update"))
        current_version_label = next(
            widget for widget in widgets
            if isinstance(widget, tk.Label)
            and widget.cget("text")
            == tr.i18n.get("settings.label.current_version"))
        version_labels = [
            child for child in current_version_label.master.winfo_children()
            if isinstance(child, tk.Label)
            and child.cget("text") == tr.version_string()
        ]
        self.assertEqual(len(version_labels), 1)
        self.assertLess(
            auto_update_label.grid_info()["row"],
            current_version_label.master.grid_info()["row"])
        self.assertEqual(
            check_button.grid_info()["row"],
            current_version_label.master.grid_info()["row"])
        app._begin_update = lambda *, check_only, on_status: on_status(
            tr.i18n.get("update.no_update"), "ok")
        app._settings_check()
        win.update_idletasks()
        self.assertEqual(
            check_button.cget("text"), tr.i18n.get("update.no_update"))
        self.assertEqual(check_button.cget("state"), "disabled")

        combo_widths = {
            int(widget.cget("width"))
            for widget in widgets
            if isinstance(widget, ttk.Combobox)
        }
        self.assertEqual(
            len(combo_widths), 1,
            "All Settings dropdowns should match the Model dropdown width")
        self.assertEqual(
            cc_app_settings._expanded_settings_combo_width(20), 22)

    def test_tooltip_is_singleton_and_closes_with_owner(self):
        app = self._app(v2=True)
        owner = tk.Toplevel(app.root)
        button = tk.Button(owner, text="Close")
        button.pack()
        owner.deiconify()
        owner.update()
        self._kill_later(owner)
        before = set(app.root.winfo_children())

        app._make_tooltip(button, "Close", delay_ms=0)
        button.event_generate("<Enter>")
        app.root.update()
        tooltips = set(app.root.winfo_children()) - before
        self.assertEqual(len(tooltips), 1)

        button.event_generate("<FocusIn>")
        button.event_generate("<Enter>")
        app.root.update()
        self.assertEqual(set(app.root.winfo_children()) - before, tooltips)

        button.event_generate("<ButtonPress-1>")
        app.root.update()
        self.assertEqual(set(app.root.winfo_children()) - before, set())

        button.event_generate("<Enter>")
        app.root.update()
        self.assertEqual(len(set(app.root.winfo_children()) - before), 1)
        owner.destroy()
        app.root.update()
        self.assertEqual(set(app.root.winfo_children()) - before, set())

    def test_tooltip_anchors_below_icon_and_clamps_to_its_monitor(self):
        app = self._app(v2=True)
        owner = tk.Toplevel(app.root)
        owner.geometry("200x120+220+160")
        button = tk.Button(owner, text="?")
        button.place(x=80, y=30, width=30, height=30)
        owner.deiconify()
        owner.update()
        self._kill_later(owner)
        before = set(app.root.winfo_children())

        app._make_tooltip(button, "Helpful text", delay_ms=0)
        with unittest.mock.patch(
                "cc_app_popup.get_monitor_rect",
                return_value=(100, 50, 700, 500)):
            button.event_generate("<Enter>")
            app.root.update()

        tooltip = (set(app.root.winfo_children()) - before).pop()
        self.assertGreaterEqual(
            tooltip.winfo_y(),
            button.winfo_rooty() + button.winfo_height())
        self.assertGreaterEqual(tooltip.winfo_x(), 104)
        self.assertLessEqual(
            tooltip.winfo_x() + tooltip.winfo_width(), 696)

    def test_tooltip_flips_above_near_monitor_bottom(self):
        app = self._app(v2=True)
        owner = tk.Toplevel(app.root)
        owner.geometry("200x120+220+350")
        button = tk.Button(owner, text="?")
        button.place(x=80, y=80, width=30, height=30)
        owner.deiconify()
        owner.update()
        self._kill_later(owner)
        before = set(app.root.winfo_children())

        app._make_tooltip(button, "Helpful text", delay_ms=0)
        with unittest.mock.patch(
                "cc_app_popup.get_monitor_rect",
                return_value=(100, 50, 700, 500)):
            button.event_generate("<Enter>")
            app.root.update()

        tooltip = (set(app.root.winfo_children()) - before).pop()
        self.assertLessEqual(
            tooltip.winfo_y() + tooltip.winfo_height(),
            button.winfo_rooty())
        self.assertGreaterEqual(tooltip.winfo_y(), 54)

    def test_v2_settings_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._open_settings()
        win = app.settings_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertFalse(getattr(win, "_v2", False),
                         "flag off must build the legacy settings window")

    def test_v2_diagnostics_uses_v2_skin(self):
        app = self._app(v2=True)
        app._refresh_diagnostics_window = lambda _win=None: None
        app._open_diagnostics()
        win = app.diagnostics_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertTrue(getattr(win, "_v2", False))
        self.assertFalse(getattr(win, "_v2_resizable", True))
        self.assertEqual(
            win._diag_theme["settings_bg"],
            app._v2_window_theme()["settings_bg"])
        self.assertEqual(win.title(), tr.i18n.get("diagnostics.title"))
        self.assertIsInstance(win._diag_body, tk.Canvas)
        self.assertTrue(getattr(win._diag_body, "_diag_rounded", False))
        self.assertEqual(
            win._diag_summary.cget("background"),
            win._diag_theme["settings_bg"])
        self.assertEqual(
            win._diag_summary_wrap.cget("background"),
            win._diag_theme["settings_bg"])
        self.assertEqual(
            int(win._diag_summary_wrap.cget("highlightthickness")), 0)
        self.assertEqual(
            tuple(win._diag_summary_wrap.pack_info()["pady"]), (2, 16))

        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)

        self.assertTrue(any(
            isinstance(widget, tk.Label)
            and widget.cget("text") == tr.i18n.get("diagnostics.title")
            for widget in walk(win)))
        win.update_idletasks()
        for button in (
                win._diag_retry_btn, win._diag_copy_btn,
                win._diag_close_btn, win._diag_refresh_btn):
            self.assertGreater(button.winfo_height(), 1)
            self.assertLessEqual(
                button.winfo_y() + button.winfo_height(),
                button.master.winfo_height())
        self.assertEqual(
            [
                win._diag_retry_btn,
                win._diag_copy_btn,
                win._diag_close_btn,
                win._diag_refresh_btn,
            ],
            list(win._diag_retry_btn.master.winfo_children()))
        self.assertEqual(str(win._diag_copy_btn.cget("takefocus")), "1")
        self.assertEqual(str(win._diag_close_btn.cget("takefocus")), "1")
        self.assertTrue(win._diag_copy_btn.bind("<FocusIn>"))
        self.assertTrue(win._diag_copy_btn.bind("<FocusOut>"))
        app._set_diagnostics_button(win._diag_copy_btn, enabled=False)
        self.assertEqual(str(win._diag_copy_btn.cget("takefocus")), "0")
        app._set_diagnostics_button(win._diag_copy_btn, enabled=True)
        self.assertEqual(str(win._diag_copy_btn.cget("takefocus")), "1")

        callbacks = []
        copied = []
        app._copy_text_content = lambda text: copied.append(text) or True
        win.after = lambda _delay, callback: callbacks.append(callback)
        win._diag_report = "diagnostics report"
        app._copy_diagnostics_report(win)
        self.assertEqual(copied, ["diagnostics report"])
        self.assertEqual(str(win._diag_copy_btn.cget("takefocus")), "1")
        self.assertEqual(
            win._diag_copy_btn.cget("text"),
            tr.i18n.get("diagnostics.copied"))
        callbacks[0]()
        self.assertEqual(
            win._diag_copy_btn.cget("text"),
            tr.i18n.get("diagnostics.copy"))

    def test_v2_diagnostics_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._refresh_diagnostics_window = lambda _win=None: None
        app._open_diagnostics()
        win = app.diagnostics_win
        self._kill_later(win)
        self.assertIsNotNone(win)
        self.assertFalse(getattr(win, "_v2", False))

    def test_v2_support_author_uses_v2_skin(self):
        app = self._app(v2=True)
        app._open_support_author()
        win = app.support_win
        self._kill_later(win)
        self.assertTrue(getattr(win, "_v2", False))
        self.assertFalse(getattr(win, "_v2_resizable", True))
        self.assertTrue(win.bind("<Escape>"))
        self.assertIsNotNone(getattr(win, "_support_image_label", None))
        self.assertTrue(str(win._support_image_label.cget("image")))
        self.assertEqual(
            win._support_close_btn.cget("text"),
            tr.i18n.get("settings.label.close"))
        self.assertTrue(win._support_close_btn.bind("<FocusIn>"))

    def test_v2_support_author_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._open_support_author()
        win = app.support_win
        self._kill_later(win)
        self.assertFalse(getattr(win, "_v2", True))

    def test_v2_uninstall_uses_safe_default_and_preserves_behavior(self):
        app = self._app(v2=True)
        app._perform_uninstall = unittest.mock.Mock(return_value=False)
        app._confirm_and_uninstall()
        win = app._uninstall_win
        self._kill_later(win)
        self.assertTrue(getattr(win, "_v2", False))
        self.assertFalse(getattr(win, "_v2_resizable", True))
        self.assertTrue(win._uninstall_keep_toggle.get())
        self.assertIsInstance(win._uninstall_keep_toggle, tk.Checkbutton)
        self.assertEqual(
            win._uninstall_keep_toggle.cget("text"),
            tr.i18n.get("uninstall.keep_data"))
        self.assertEqual(
            str(win._uninstall_keep_toggle.cget("takefocus")), "1")
        self.assertTrue(win._uninstall_keep_toggle.bind("<space>"))
        self.assertTrue(win._uninstall_keep_toggle.bind("<Return>"))
        self.assertTrue(win._uninstall_cancel_btn.bind("<FocusIn>"))
        self.assertTrue(win.bind("<Escape>"))
        self.assertFalse(win._uninstall_status.winfo_manager())

        win._uninstall_confirm_btn.invoke()
        self.assertTrue(win._uninstall_status.winfo_manager())
        win._uninstall_keep_toggle.set(False)
        win._uninstall_confirm_btn.invoke()

        self.assertEqual(
            app._perform_uninstall.call_args_list,
            [unittest.mock.call(remove_data=False),
             unittest.mock.call(remove_data=True)])
        self.assertEqual(
            win._uninstall_status.cget("text"),
            tr.i18n.get("uninstall.failed"))

    def test_v2_uninstall_flag_off_is_legacy(self):
        app = self._app(v2=False)
        app._perform_uninstall = unittest.mock.Mock(return_value=False)
        app._confirm_and_uninstall()
        win = app._uninstall_win
        self._kill_later(win)
        self.assertFalse(getattr(win, "_v2", True))

    def test_v2_ocr_overlay_dims_screen_and_restores_selected_pixels(self):
        app = self._app(v2=True)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (0, 0, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))

        with unittest.mock.patch.object(
                cc_app_ocr.win32util, "get_monitor_rect",
                return_value=(0, 0, 640, 360)):
            app._open_region_selector(screen_image=screen)
        win = app._ocr_overlay
        self._kill_later(win)
        self.assertTrue(getattr(win, "_v2", False))
        self.assertEqual(win._ocr_capture_mode, "image")
        self.assertEqual(win.title(), tr.i18n.get("tray.screenshot"))
        self.assertEqual(
            win._ocr_accessible_hint.cget("text"),
            tr.i18n.get("ocr.drag_select_hint"))
        self.assertEqual(
            str(win._ocr_accessible_hint.cget("takefocus")), "0")
        self.assertEqual(win._ocr_hint_position, (320, 30))
        self.assertTrue(win.bind("<Escape>"))
        self.assertTrue(win._ocr_canvas.bind("<Button-3>"))
        self.assertEqual(len(win._ocr_selection_items), 3)

        win._ocr_set_selection(90, 70, 520, 260)
        win.update_idletasks()
        self.assertIsNotNone(win._ocr_selection_state["selection_photo"])
        self.assertEqual(
            win._ocr_selection_state["selection_photo"].width(), 430)
        self.assertEqual(
            win._ocr_selection_state["selection_photo"].height(), 190)
        for item in win._ocr_selection_items:
            self.assertEqual(
                win._ocr_canvas.itemcget(item, "state"), "normal")
        widths = [
            int(float(win._ocr_canvas.itemcget(item, "width")))
            for item in win._ocr_selection_items
        ]
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_ocr_hint_uses_top_center_of_cursor_monitor(self):
        virtual_rect = (-1920, -200, 5760, 1400)

        self.assertEqual(
            cc_app_ocr._region_hint_position(
                virtual_rect, (-1920, 0, 0, 1040)),
            (960, 230))
        self.assertEqual(
            cc_app_ocr._region_hint_position(
                virtual_rect, (0, 0, 1920, 1040)),
            (2880, 230))
        self.assertEqual(
            cc_app_ocr._region_hint_position(virtual_rect, None),
            (2880, 30))

    def test_v2_ocr_overlay_normalizes_virtual_screen_selection(self):
        app = self._app(v2=True)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (-200, 30, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))
        app._open_region_selector(screen_image=screen)
        win = app._ocr_overlay
        self._kill_later(win)
        captures = []
        callbacks = []
        app._capture_and_translate = (
            lambda *args, **kwargs: captures.append((args, kwargs)))

        with unittest.mock.patch.object(
                app.root, "after",
                side_effect=lambda delay, callback: callbacks.append(
                    (delay, callback))):
            win._ocr_accept_selection(520, 260, 90, 70)

        self.assertFalse(app._ocr_selecting)
        self.assertEqual(callbacks[0][0], 0)
        callbacks[0][1]()
        self.assertEqual(captures[0][0], (-110, 100, 430, 190))
        self.assertEqual(captures[0][1]["image"].size, (430, 190))

    def test_v2_ocr_overlay_supports_two_click_selection(self):
        app = self._app(v2=True)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (0, 0, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))
        app._open_region_selector(screen_image=screen)
        win = app._ocr_overlay
        self._kill_later(win)
        callbacks = []
        app._capture_and_translate = lambda *args, **kwargs: None

        self.assertFalse(win._ocr_click_point(90, 70))
        self.assertEqual(
            win._ocr_selection_state["click_anchor"], (90, 70))
        self.assertTrue(all(
            win._ocr_canvas.itemcget(item, "state") == "normal"
            for item in win._ocr_anchor_marker))
        with unittest.mock.patch.object(
                app.root, "after",
                side_effect=lambda delay, callback: callbacks.append(
                    (delay, callback))):
            self.assertTrue(win._ocr_click_point(520, 260))
        self.assertFalse(app._ocr_selecting)
        self.assertEqual(callbacks[0][0], 0)

    def test_v2_ocr_overlay_excludes_real_desktop_preview_from_capture(self):
        app = self._app(v2=True)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (0, 0, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))

        with unittest.mock.patch.object(
                cc_app_ocr.cc_ocr, "grab_region", return_value=screen), \
                unittest.mock.patch.object(
                    cc_app_ocr.win32util, "exclude_window_from_capture",
                    return_value=True) as exclude:
            app._open_region_selector()

        win = app._ocr_overlay
        self._kill_later(win)
        self.assertEqual(win._ocr_capture_mode, "image")
        exclude.assert_called_once()

    def test_v2_ocr_overlay_fails_closed_when_capture_exclusion_fails(self):
        app = self._app(v2=True)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (0, 0, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))

        with unittest.mock.patch.object(
                cc_app_ocr.cc_ocr, "grab_region", return_value=screen), \
                unittest.mock.patch.object(
                    cc_app_ocr.win32util, "exclude_window_from_capture",
                    return_value=False):
            app._open_region_selector()

        win = app._ocr_overlay
        self._kill_later(win)
        self.assertFalse(getattr(win, "_v2", False))
        self.assertNotEqual(win._ocr_capture_mode, "image")

    def test_v2_ocr_overlay_flag_off_keeps_legacy_selector(self):
        app = self._app(v2=False)
        app._ocr_selecting = False
        app._ocr_overlay = None
        app._virtual_screen_rect = lambda: (0, 0, 640, 360)
        screen = ccv2.Image.new("RGB", (640, 360), (110, 135, 170))

        app._open_region_selector(screen_image=screen)
        win = app._ocr_overlay
        self._kill_later(win)
        self.assertFalse(getattr(win, "_v2", False))
        self.assertNotEqual(win._ocr_capture_mode, "image")

    def test_ocr_partial_preview_save_is_deleted(self):
        app = self._app(v2=True)
        app._destroy_popup = lambda: None
        app._make_popup = lambda *args, **kwargs: object()

        class PartialImage:
            @staticmethod
            def save(path, _format):
                with open(path, "wb") as partial:
                    partial.write(b"partial screenshot")
                raise OSError("simulated save failure")

        with tempfile.TemporaryDirectory() as temp_dir, \
                unittest.mock.patch.object(
                    cc_app_ocr, "DATA_DIR", temp_dir), \
                unittest.mock.patch.object(cc_app_ocr, "log_error"):
            app._capture_and_translate(
                0, 0, 100, 100, image=PartialImage())
            self.assertEqual(os.listdir(temp_dir), [])


if __name__ == "__main__":
    unittest.main()
