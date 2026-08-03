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
import time
import unittest

import tkinter as tk

from tests._tr import tr
import tests.test_full as tf  # reuse its shared-root + headless-app helpers

try:
    import cc_ui_v2 as ccv2
    _V2_OK = ccv2.ui_v2_available()
except Exception:
    ccv2 = None
    _V2_OK = False


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
        # metrics, but nothing like the old ~2x margin inflation.
        self.assertLessEqual(
            v2_w, legacy_w + 12,
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
        before = str(win._copy_btn.cget("image"))
        app._copy_result()
        after = str(win._copy_btn.cget("image"))
        self.assertNotEqual(before, after,
                            "copy feedback should swap the chip image")

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


if __name__ == "__main__":
    unittest.main()
