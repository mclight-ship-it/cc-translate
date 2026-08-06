"""cc_app_quickinput — the quick-input translation window for CC Translate.

QuickInputMixin holds the three methods behind the tray's "Quick input" entry:
open_quick_input (marshal to the Tk thread), _open_quick_input (build the
rounded editor window), and _apply_ime_composition_font (push a matching LOGFONT
into the Windows IME so the in-progress pinyin pre-edit text matches the editor
font).

Extracted verbatim from ``TranslatorApp`` in translator.pyw (bodies unchanged).
Like the other cc_app_* mixins this imports only leaf modules (tkinter, ctypes,
i18n) and the shared foundation (cc_core); it never imports translator.pyw, so
there is no import cycle. ``self`` resolves at runtime against the assembled
``TranslatorApp`` instance, so calls into shared window/pipeline helpers
(``self._rounded_shell``, ``self._show_loading`` …) keep working.
"""

import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import i18n
from win32util import get_monitor_rect

from cc_core import (
    CFG, log_error, POPUP_CORNER_RADIUS, V2_CORNER_RADIUS,
    QUICK_INPUT_WINDOW_W, QUICK_INPUT_WINDOW_H,
)

# The v2 skin (cc_ui_v2) is optional (needs Pillow). Import it guarded so a
# missing Pillow never breaks the quick-input window; every v2 path also gates
# on self._v2_popup_on(), which requires BOTH the UI_V2 flag AND a working
# renderer. Flag off -> the legacy window below is built byte-for-byte.
try:
    import cc_ui_v2 as ccv2
except Exception:
    ccv2 = None


class QuickInputMixin:
    """Quick-input translation window (mixed into TranslatorApp)."""

    # ---------- Quick input window ----------
    def open_quick_input(self):
        self.root.after(0, self._open_quick_input)

    def _v2_field_photo(self, w, h, radius, pal, scale, focused):
        """Bake the glowing input field at (w, h) as a PhotoImage, returning
        ``(photo, inset)`` where ``inset`` is the transparent glow margin the
        Text should sit inside. Cached per (w, h, focused) via _v2_photo. The
        inset is generous so the violet bloom fully fades to transparent inside
        the image instead of being clipped at the edge (which showed a hard
        line)."""
        inset = ccv2.scaled(20, scale)
        photo = self._v2_photo(
            ("qi_field", int(w), int(h), round(scale, 2), bool(focused)),
            lambda: ccv2.bake_input_field(w, h, radius, pal, scale, focused,
                                          inset=inset)[0])
        return photo, inset

    def _apply_ime_composition_font(self, widget, family, point_size):
        """Best-effort override for Windows IME pre-edit font on this widget.

        The in-progress pinyin string is drawn by the IME composition layer,
        not by Tk's normal text rendering, so it may ignore the Text widget
        font unless we push a matching LOGFONT into the current IME context.

        We compute lfHeight from the widget's actual DPI (GetDpiForWindow) and
        the font's point size so the pre-edit glyph matches the committed text
        regardless of display scaling.
        """
        hwnd = None
        himc = None
        try:
            class LOGFONTW(ctypes.Structure):
                _fields_ = [
                    ("lfHeight", wintypes.LONG),
                    ("lfWidth", wintypes.LONG),
                    ("lfEscapement", wintypes.LONG),
                    ("lfOrientation", wintypes.LONG),
                    ("lfWeight", wintypes.LONG),
                    ("lfItalic", ctypes.c_ubyte),
                    ("lfUnderline", ctypes.c_ubyte),
                    ("lfStrikeOut", ctypes.c_ubyte),
                    ("lfCharSet", ctypes.c_ubyte),
                    ("lfOutPrecision", ctypes.c_ubyte),
                    ("lfClipPrecision", ctypes.c_ubyte),
                    ("lfQuality", ctypes.c_ubyte),
                    ("lfPitchAndFamily", ctypes.c_ubyte),
                    ("lfFaceName", ctypes.c_wchar * 32),
                ]

            hwnd = int(widget.winfo_id())
            himc = ctypes.windll.imm32.ImmGetContext(hwnd)
            if not himc:
                return False

            # Resolve font family and point size from the widget's actual font.
            face = str(family)
            pts = float(point_size)
            try:
                f = tkfont.Font(font=widget.cget("font"))
                actual = f.actual()
                face = str(actual.get("family") or face)
                sz = actual.get("size", 0)
                if sz and sz > 0:
                    pts = float(sz)          # positive = points in Tk
                elif sz and sz < 0:
                    # Tk negative size = pixels; back-convert via 96 dpi base
                    pts = abs(sz) * 72.0 / 96.0
            except Exception:
                pass

            # Get the window's actual DPI so we convert points → pixels
            # correctly under Per-Monitor V2 DPI awareness.
            dpi = 96
            try:
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                if dpi <= 0:
                    dpi = 96
            except Exception:
                pass

            # lfHeight < 0 means "character height" (ascent+descent) in pixels.
            px = int(round(pts * dpi / 72.0))

            lf = LOGFONTW()
            lf.lfHeight = -px
            lf.lfWeight = 400
            lf.lfCharSet = 1  # DEFAULT_CHARSET
            lf.lfQuality = 5  # CLEARTYPE_QUALITY
            lf.lfFaceName = face[:31]

            ok = ctypes.windll.imm32.ImmSetCompositionFontW(
                himc, ctypes.byref(lf))
            return bool(ok)
        except Exception as e:
            log_error("ime_comp_font", e)
            return False
        finally:
            if hwnd and himc:
                try:
                    ctypes.windll.imm32.ImmReleaseContext(hwnd, himc)
                except Exception:
                    pass

    def _open_quick_input(self):
        if self.quick_input_win and tk.Toplevel.winfo_exists(self.quick_input_win):
            self._bring_to_front(self.quick_input_win)
            text_widget = getattr(self.quick_input_win, "_quick_input_text", None)
            if text_widget and text_widget.winfo_exists():
                text_widget.focus_set()
            return

        t = self.theme
        bg = t["settings_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        FONT = "Microsoft YaHei UI"

        # v2 dark-launch skin: when the UI_V2 flag is on AND the renderer is
        # available, reskin to the deep-navy palette (gradient title + gradient
        # Translate pill + violet input field). Legacy is untouched otherwise.
        v2on = self._v2_popup_on()
        scale = self._ui_scale() if v2on else 1.0
        if v2on:
            pal = self._v2_palette()
            v2c = self._v2_tk_colors()
            bg = v2c["panel"]
            border = v2c["border"]
            hint = v2c["hint"]
            accent = v2c["accent"]
            fg = ccv2.rgb_to_hex(pal["fg"])
            field_bg = ccv2.rgb_to_hex(pal["field"])
            field_frame_bg = ccv2.rgb_to_hex(pal["field_brd"])
        else:
            field_bg = t["list_bg"]
            field_frame_bg = border

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.quick_input_win = win

        def _on_destroy(_e=None):
            if self.quick_input_win is win:
                self.quick_input_win = None
        win.bind("<Destroy>", _on_destroy, add="+")

        win._v2 = v2on
        # The quick-input window is a fixed-size card, so its whole ring drags
        # (never resizes); _resize_hit reads this flag.
        win._v2_resizable = False
        _radius = V2_CORNER_RADIUS if v2on else POPUP_CORNER_RADIUS
        card = self._rounded_shell(win, _radius, bg, border)

        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=12, pady=(12, 6))
        # v2: align the logo's left edge with the input field's rounded-left
        # (the field is inset by ~this margin inside its canvas), so the icon,
        # the field box and the footer text all share one left column.
        field_inset = ccv2.scaled(15, scale) if (v2on and ccv2 is not None) else 0
        if v2on and ccv2 is not None:
            logo_img = self._v2_logo_image(22) or self._v2_badge_image(24)
        else:
            logo_img = self._logo_image(18)
        drag_targets = [bar]
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(field_inset, 8), anchor="center")
            drag_targets.append(logo_lbl)
        title_text = i18n.get("quick_input.title")
        title_img = None
        if v2on and ccv2 is not None:
            # v2: the tri-colour brand gradient title (an image, not a glyph).
            title_img = self._v2_photo(
                ("qi_title", title_text, round(scale, 2)),
                lambda: ccv2.gradient_text(
                    title_text, ccv2.load_font("bold", 13, scale)))
        if title_img is not None:
            title_lbl = tk.Label(bar, image=title_img, bg=bg, bd=0,
                                 highlightthickness=0)
            title_lbl.image = title_img
        else:
            title_lbl = tk.Label(bar, text=title_text, bg=bg,
                                 fg=accent, font=(FONT, 11, "bold"))
        # Header is now just logo + title (the usage hint moved to the footer,
        # left of the Translate button, so it's less prominent); centre-anchor
        # both so the icon and title share one vertical centre.
        title_lbl.pack(side="left", anchor="center")
        drag_targets.append(title_lbl)
        if v2on and ccv2 is not None:
            close_btn = self._v2_ghost_button(
                bar, lambda: win.destroy(), icon="close", danger=True)
            close_btn.pack(side="right")
        else:
            close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                                 font=(FONT, 11), cursor="hand2", padx=6)
            close_btn.pack(side="right")
            close_btn.bind("<Button-1>", lambda e: win.destroy())
            close_btn.bind("<Enter>",
                           lambda e: close_btn.config(fg=t["status_err"]))
            close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
        self._make_draggable(tuple(drag_targets), win)
        # Legacy shows a hairline under the header; v2 drops it (the concept has
        # no divider between the title row and the input field).
        if not v2on:
            tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        body = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=12, pady=(2, 2))

        # Keep quick-input font aligned with app font settings to avoid an
        # oversized editor, while keeping a readable lower bound.
        editor_font_size = max(10, int(self.cfg[CFG.FONT_SIZE]))

        if v2on and ccv2 is not None:
            # v2: a single-line glowing input field. The violet glow + rounded
            # dark fill + hairline are baked onto a Canvas (brighter on focus);
            # the Text sits inside via create_window so no ugly scrollbar shows.
            panel_rgb = ccv2.hex_to_rgb(bg)
            editor_bg = ccv2.rgb_to_hex(ccv2.over(pal["field"], panel_rgb))
            # Field canvas is taller than the visible box so the violet bloom
            # (baked with a transparent inset) fades fully inside the image — no
            # hard clip line at the canvas top/bottom. The visible box is a
            # rounded RECTANGLE (small radius vs height), not a stadium pill, to
            # match the concept.
            field_h = ccv2.scaled(88, scale)
            radius = ccv2.scaled(9, scale)
            fcanvas = tk.Canvas(body, bg=bg, bd=0, highlightthickness=0,
                                height=field_h)
            # No expand: the window is sized to content (below), so the field
            # sits at its natural height with no vertical slack to centre into.
            fcanvas.pack(fill="x")
            editor = tk.Text(
                fcanvas, bg=editor_bg, fg=fg, wrap="none", relief="flat", bd=0,
                width=1, height=1, padx=0, pady=0,
                font=(FONT, editor_font_size), highlightthickness=0,
                insertbackground=fg, selectbackground=t["sel_bg"])
            win._quick_input_text = editor
            state = {"w": 0, "focused": False, "win": None}

            def _paint_field(_e=None):
                w = fcanvas.winfo_width()
                if w <= 1:
                    return
                if w == state["w"] and _e is not None:
                    return
                state["w"] = w
                # Bake fresh for this width/focus (cache keyed on both).
                photo, inset = self._v2_field_photo(
                    w, field_h, radius, pal, scale, state["focused"])
                fcanvas._field_photo = photo
                fcanvas.delete("field")
                if photo is not None:
                    fcanvas.create_image(0, 0, image=photo, anchor="nw",
                                         tags="field")
                pad_x = ccv2.scaled(12, scale)
                tx = inset + pad_x
                tw = max(1, w - 2 * tx)
                if state["win"] is None:
                    state["win"] = fcanvas.create_window(
                        tx, field_h // 2, window=editor, anchor="w", width=tw)
                else:
                    fcanvas.coords(state["win"], tx, field_h // 2)
                    fcanvas.itemconfig(state["win"], width=tw)
                fcanvas.tag_lower("field")

            def _focus_in(_e=None):
                state["focused"] = True
                state["w"] = 0
                _paint_field()

            def _focus_out(_e=None):
                state["focused"] = False
                state["w"] = 0
                _paint_field()

            fcanvas.bind("<Configure>", _paint_field, add="+")
            editor.bind("<FocusIn>", _focus_in, add="+")
            editor.bind("<FocusOut>", _focus_out, add="+")
        else:
            editor_shell = tk.Frame(body, bg=field_frame_bg, bd=0,
                                    highlightthickness=0)
            editor_shell.pack(fill="both", expand=True)
            editor_bg = field_bg
            editor = tk.Text(
                editor_shell, bg=editor_bg, fg=fg, wrap="word", relief="flat",
                bd=0, width=1, height=1,
                padx=10, pady=8, font=(FONT, editor_font_size),
                highlightthickness=0,
                insertbackground=fg, selectbackground=t["sel_bg"])
            scroll = ttk.Scrollbar(
                editor_shell, orient="vertical", style="CC.Vertical.TScrollbar",
                command=editor.yview)
            editor.config(yscrollcommand=scroll.set)
            editor.pack(side="left", fill="both", expand=True, padx=(1, 0),
                        pady=1)
            scroll.pack(side="right", fill="y", padx=(0, 1), pady=1)
            win._quick_input_text = editor

        def _sync_ime_font(_e=None):
            self._apply_ime_composition_font(editor, FONT, editor_font_size)

        # Re-apply when focus changes: some IMEs reset composition style when
        # context hops between widgets/windows.
        editor.bind("<FocusIn>", _sync_ime_font, add="+")
        editor.bind("<Button-1>", _sync_ime_font, add="+")

        bottom = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        # Align the footer with the field's glowing rim: pad it by the same inset
        # as the field, so the usage hint (left) starts at the field's left edge
        # and 翻译's right edge lines up with the field's right edge.
        _foot_pad = (16 + field_inset) if (v2on and ccv2 is not None) else 16
        bottom.pack(side="bottom", fill="x", padx=_foot_pad,
                    pady=(6, 12) if v2on else (6, 14))

        # One label serves double duty: the subtle usage hint by default, and the
        # red "please enter text" error on an empty submit (reverting as soon as
        # the user types). Kept small + dim so it never competes with the field.
        hint_text = i18n.get("quick_input.hint")
        info_lbl = tk.Label(
            bottom, text=hint_text if v2on else "", bg=bg,
            fg=hint if v2on else t["status_err"], anchor="w",
            font=(FONT, 8) if v2on else (FONT, 9))
        info_lbl.pack(side="left", fill="x", expand=True, padx=(0, 10))
        status = info_lbl  # legacy code paths below reference `status`
        win._quick_input_hint = info_lbl

        def submit(_e=None):
            text = editor.get("1.0", "end-1c").strip()
            if not text:
                status.config(text=i18n.get("quick_input.empty"),
                              fg=t["status_err"])
                editor.focus_set()
                return "break"
            status.config(text=hint_text if v2on else "",
                          fg=hint if v2on else t["status_err"])
            text = text[: self.cfg[CFG.MAX_CHARS]]
            win.destroy()
            self._show_loading(text)
            return "break"

        if v2on:
            # Revert the error back to the neutral hint as soon as the user types.
            def _reset_info(_e=None):
                if status.cget("text") != hint_text:
                    status.config(text=hint_text, fg=hint)
            editor.bind("<Key>", _reset_info, add="+")

        translate_text = i18n.get("quick_input.translate")
        pill_img = None
        if v2on and ccv2 is not None:
            # v2: a brand-gradient filled pill (image), matching the result
            # popup's accent action. Transparent rounded corners blend onto the
            # navy footer.
            pill_img = self._v2_photo(
                ("qi_translate", translate_text, round(scale, 2)),
                lambda: ccv2.gradient_pill(
                    translate_text, ccv2.load_font("bold", 10, scale), pal,
                    grad=True, px=20, py=7, scale=scale))
        if pill_img is not None:
            submit_btn = tk.Button(
                bottom, image=pill_img, command=submit, bg=bg,
                activebackground=bg, relief="flat", bd=0, highlightthickness=0,
                cursor="hand2", takefocus=0)
            submit_btn.image = pill_img
        else:
            submit_btn = self._pill_button(
                bottom, translate_text, submit,
                bg=accent, fg="#ffffff",
                hover_bg=accent, hover_fg="#ffffff",
                active_bg=accent, active_fg="#ffffff",
                font=(FONT, 10), padx=20, pady=6)
        submit_btn.pack(side="right")
        win._quick_input_submit_btn = submit_btn

        editor.bind("<Control-Return>", submit)
        if v2on:
            # Single-line field: Enter submits (return "break" so no newline is
            # inserted). Keep Ctrl+Enter as a compatible alternate shortcut.
            editor.bind("<Return>", submit)
        editor.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        if v2on:
            # Size the window to the card's own requested height (plus the two
            # card insets) instead of a fixed guess, so nothing is clipped (the
            # Translate pill was being squeezed) and there is no top/bottom
            # slack band. Width stays a comfortable fixed value; the field is
            # fill="x".
            win.update_idletasks()
            card.update_idletasks()
            ci = int(getattr(win, "_card_inset", _radius))
            w, _h_guess, x, _y_guess = self._scaled_centered_box(
                QUICK_INPUT_WINDOW_W, 150, min_w=440, min_h=120)
            content_h = card.winfo_reqheight() + 2 * ci
            rect = get_monitor_rect()
            if rect:
                mon_top, mon_h = rect[1], rect[3] - rect[1]
            else:
                mon_top, mon_h = 0, self.root.winfo_screenheight()
            h = min(content_h, mon_h - 40)
            y = mon_top + (mon_h - h) // 2
        else:
            w, h, x, y = self._scaled_centered_box(
                QUICK_INPUT_WINDOW_W, QUICK_INPUT_WINDOW_H, min_w=420,
                min_h=260)
        self._reveal_rounded_window(win, w, h, x, y)
        editor.focus_set()
        self.root.after(0, _sync_ime_font)
