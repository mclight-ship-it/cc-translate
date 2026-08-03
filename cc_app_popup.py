"""cc_app_popup — popup / result-window rendering for TranslatorApp.

Mixed into TranslatorApp as PopupMixin. Pure mechanical extraction from
translator.pyw of the 43 popup methods (loading card, result popup build/size,
rounded borderless windows, drag/resize, streaming text fill, rich-tag styling,
copy, dismiss/destroy). Method bodies are unchanged.

Also hosts the module-level rounded-corner plumbing (win32 window-proc
subclassing + the Canvas rounded-rectangle drawer) that only the popup code
uses. Imports only leaf modules (cc_core / win32util / cc_rich / i18n / stdlib),
never translator, so there is no import cycle.
"""

import os
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import i18n
import win32util
from win32util import get_monitor_rect
from cc_rich import iter_rich_segments
from cc_core import (
    APP_NAME, CFG, ICON_PATH,
    POPUP_CORNER_RADIUS, ROUND_KEY_COLOR,
    LOADING_SPINNER, LOADING_CORNER_RADIUS,
    MIN_POPUP_HEIGHT_COMPACT,
    STREAM_OPEN_MIN_LINES,
    MIN_RESIZE_WIDTH,
    MIN_RESIZE_HEIGHT, RESIZE_HIT,
    POPUP_BAR_PAD_X, POPUP_BAR_PAD_TOP, POPUP_BAR_PAD_BOTTOM,
    POPUP_BODY_PAD_X, POPUP_BODY_PAD_BOTTOM, POPUP_TEXT_PAD_X, POPUP_TEXT_PAD_Y,
    CENTERED_POPUP_W, CENTERED_POPUP_H, HISTORY_WINDOW_W, HISTORY_WINDOW_H,
    resolve_theme_name, ui_v2_enabled,
    log_error, log_perf,
)

# The v2 skin (cc_ui_v2) is optional: it needs Pillow, which the app treats as
# an optional dependency. Import it guarded so a missing Pillow can never break
# popup rendering; every v2 code path additionally checks _v2_popup_on(), which
# requires BOTH the UI_V2 flag AND a working renderer before doing anything.
try:
    import cc_ui_v2 as ccv2
except Exception:
    ccv2 = None

# Extra inset (design points) the v2 shell reserves between the rounded window
# edge and the content card, on top of the corner radius. Set to 0: the v2
# "frame" is now a thin brand-gradient hairline baked at the perimeter (see
# cc_ui_v2.bake_border_ring), NOT a wide glow margin — an opaque tk window can't
# render a real translucent glow outside itself, so a wide margin only ever read
# as a lighter "solid border". Kept as a named knob in case a small inset is
# ever wanted again. DPI-scaled at use.
V2_HALO_PTS = 0


# ---------------------------------------------------------------------------
# Reliable rounded corners for borderless (overrideredirect) windows.
#
# Previous approach applied a rounded region from Tk's <Configure>/<Map>
# handlers using winfo_width()/height(). During a live drag-resize those
# values lag the requested geometry, so a stale (too-large) region could get
# cached — its rounded corners then fall *outside* the shrunk window and it
# renders square. The fix: subclass the window procedure and re-apply the
# region on WM_WINDOWPOSCHANGED / WM_SIZE, which Windows sends *after* the
# window has actually been resized. GetWindowRect then reports the true final
# size every time, regardless of who triggered the resize (Tk geometry, a
# drag, or a DPI change). This removes all Tk timing/caching races.
# ---------------------------------------------------------------------------
_ROUND_GWLP_WNDPROC = -4
_ROUND_WM_SIZE = 0x0005
_ROUND_WM_WINDOWPOSCHANGED = 0x0047
_ROUND_WM_DPICHANGED = 0x02E0
_ROUND_LRESULT = ctypes.c_ssize_t
_ROUND_WNDPROC = ctypes.WINFUNCTYPE(
    _ROUND_LRESULT, wintypes.HWND, ctypes.c_uint,
    ctypes.c_size_t, ctypes.c_ssize_t)

# hwnd -> {"cb": <WNDPROC>, "old": <old proc ptr>, "radius": int}
# Keeps the ctypes callback alive for the window's whole lifetime (GC of the
# callback while Windows still holds the pointer would crash).
_ROUND_REGISTRY = {}


def _round_apply_region(hwnd, radius):
    """Backwards-compatible shim → win32util.round_apply_region()."""
    win32util.round_apply_region(hwnd, radius)


def _round_prefer_dwm(hwnd):
    """Backwards-compatible shim → win32util.prefer_dwm_rounded()."""
    win32util.prefer_dwm_rounded(hwnd)


def attach_rounded_corners(win, radius):
    """Subclass a Tk Toplevel's window proc so its rounded region is refreshed
    on every real resize. Returns nothing; safe to call once per window."""
    try:
        hwnd = int(win.winfo_id())
    except Exception:
        return
    if hwnd in _ROUND_REGISTRY:
        _ROUND_REGISTRY[hwnd]["radius"] = int(radius)
        _round_apply_region(hwnd, radius)
        return

    user32 = ctypes.windll.user32
    set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    call_proc = user32.CallWindowProcW
    set_ptr.restype = ctypes.c_void_p
    set_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    call_proc.restype = _ROUND_LRESULT
    call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                          ctypes.c_size_t, ctypes.c_ssize_t]

    entry = {"cb": None, "old": None, "radius": int(radius)}

    def _wndproc(h, msg, wparam, lparam):
        old = entry["old"]
        res = call_proc(old, h, msg, wparam, lparam) if old else 0
        if msg in (_ROUND_WM_WINDOWPOSCHANGED, _ROUND_WM_SIZE,
                   _ROUND_WM_DPICHANGED):
            _round_apply_region(h, entry["radius"])
        return res

    cb = _ROUND_WNDPROC(_wndproc)
    entry["cb"] = cb
    old_proc = set_ptr(hwnd, _ROUND_GWLP_WNDPROC,
                       ctypes.cast(cb, ctypes.c_void_p))
    entry["old"] = old_proc
    _ROUND_REGISTRY[hwnd] = entry

    _round_prefer_dwm(hwnd)
    _round_apply_region(hwnd, radius)

    def _cleanup(event=None):
        # Only react to the Toplevel's own destruction, not child widgets.
        if event is not None and event.widget is not win:
            return
        # The HWND is gone after destroy; just drop our references so the
        # ctypes callback can be collected. Defer so any in-flight messages
        # during teardown still have a live callback.
        def _drop():
            _ROUND_REGISTRY.pop(hwnd, None)
        try:
            win.after(0, _drop)
        except Exception:
            _ROUND_REGISTRY.pop(hwnd, None)

    win.bind("<Destroy>", _cleanup, add="+")


# ---------------------------------------------------------------------------
# Rounded borderless windows via a transparent colour key.
#
# SetWindowRgn (above) clips the window to a rounded shape, but on some
# compositors (notably remote-desktop / VM sessions) the clipped-out corners
# render as opaque black instead of compositing through to the desktop. For
# the larger chrome windows (settings, history) we instead paint a rounded
# card on a Canvas and set a transparent colour key, so the corner pixels are
# genuinely see-through. The key is a near-black sentinel so that even if a
# session somehow ignored colour-key transparency, the corners would look the
# same as the old behaviour (no regression).
# ---------------------------------------------------------------------------


def _draw_round_rect(cv, x1, y1, x2, y2, r, **kwargs):
    """Draw a filled rounded rectangle on a Canvas as two rectangles plus four
    corner pie-slices. This gives a crisp, exact-radius arc (a smooth-spline
    polygon collapses the radius and bulges the straight edges). All pieces
    share the caller's ``tags`` so they can be cleared/lowered as one."""
    r = max(0, min(int(r), (x2 - x1) // 2, (y2 - y1) // 2))
    fill = kwargs.get("fill", "")
    tags = kwargs.get("tags")
    base = {"fill": fill, "outline": fill, "width": 0}
    if tags:
        base["tags"] = tags
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, **base)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, **base)
    d = 2 * r
    arc = {"fill": fill, "outline": fill, "style": "pieslice"}
    if tags:
        arc["tags"] = tags
    cv.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, **arc)
    cv.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, **arc)
    cv.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, **arc)
    cv.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, **arc)


class PopupMixin:
    def _make_loading_popup(self):
        """A compact, modern 'translating' card: an accent-coloured spinner
        next to a muted label. Borderless, rounded, no toolbar/scrollbar."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)

        popup_bg = self.theme.get("popup_bg", self.theme["bg"])
        popup_border = self.theme.get("popup_border", self.theme["border"])
        popup_hint = self.theme.get("popup_hint", self.theme["hint_fg"])
        accent = self.theme.get("accent", "#7aa2f7")

        # Rounded corners via a transparent colour key (genuinely transparent on
        # this environment, unlike SetWindowRgn cut-outs which render black).
        card = self._rounded_shell(win, LOADING_CORNER_RADIUS,
                                   popup_bg, popup_border)

        row = tk.Frame(card, bg=popup_bg, bd=0, highlightthickness=0)
        row.pack(padx=20, pady=14)

        spinner = tk.Label(
            row,
            text=LOADING_SPINNER[0],
            bg=popup_bg,
            fg=accent,
            font=("Segoe UI Symbol", 13),
        )
        spinner.pack(side="left", padx=(0, 9))
        win._spinner = spinner

        hint = tk.Label(
            row,
            text=(i18n.get("result.explaining") if self._last_class == "code"
                  else i18n.get("result.processing_screenshot")
                  if self._last_origin == "ocr"
                  else i18n.get("result.processing")),
            bg=popup_bg,
            fg=popup_hint,
            font=("Microsoft YaHei UI", 10),
        )
        hint.pack(side="left")
        win._hint_label = hint

        win.update_idletasks()
        radius = LOADING_CORNER_RADIUS
        w = card.winfo_reqwidth() + 2 * radius
        h = card.winfo_reqheight() + 2 * radius
        if self._is_centered_layout():
            # Centre the small hint where the fixed result card will appear, so
            # there is no positional jump when the result replaces it.
            bw, bh, bx, by = self._centered_box()
            x = bx + (bw - w) // 2
            y = by + (bh - h) // 2
        else:
            # Follow-cursor layout: use the anchor captured when this cycle was
            # triggered (in _show_loading) so the hint and the result window land
            # in the same place even if the mouse has since moved. Fall back to
            # the live cursor if, for any reason, nothing was captured.
            anchor = getattr(self, "_cycle_anchor", None)
            if anchor is not None:
                x, y = int(anchor[0]), int(anchor[1])
            else:
                x = self.root.winfo_pointerx() + 12
                y = self.root.winfo_pointery() + 18
            x, y = self._clamp_to_monitor(x, y, w, h)
        self._reveal_rounded_window(win, w, h, x, y)
        # Clicking anywhere outside dismisses only the loading hint — but arm
        # that only after a short grace period. Revealing this popup activates
        # it (see _bring_to_front); meanwhile closing the quick-input window
        # hands the OS foreground to another window asynchronously, and that
        # handoff races our activation. The stray FocusOut it delivers would
        # otherwise dismiss the "translating…" hint the instant it appears, so
        # the user never sees it. Only a genuine, later focus loss should close
        # it.
        win._dismiss_armed = False
        win.bind("<FocusOut>", lambda e: self._on_loading_focus_out(win))

        def _arm_loading_dismiss():
            try:
                if tk.Toplevel.winfo_exists(win):
                    win._dismiss_armed = True
            except Exception:
                pass

        try:
            win.after(600, _arm_loading_dismiss)
        except Exception:
            win._dismiss_armed = True
        return win

    def _on_loading_focus_out(self, win):
        """Dismiss the loading hint when it loses focus — but ignore the focus
        churn that immediately follows revealing it (see _make_loading_popup),
        which would otherwise close the hint before the user ever sees it."""
        if getattr(win, "_dismiss_armed", False):
            self._dismiss_loading_popup()

    def _build_popup_header(self, win, frame, *, title, is_error, popup_bg,
                            popup_border, hint, accent, theme):
        # Header = title bar + hairline separator, measured as one unit so the
        # geometry math (which reads win._bar height) accounts for both.
        header = tk.Frame(frame, bg=popup_bg, bd=0, highlightthickness=0)
        header.pack(fill="x")
        win._bar = header

        bar = tk.Frame(header, bg=popup_bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=POPUP_BAR_PAD_X,
                 pady=(POPUP_BAR_PAD_TOP, POPUP_BAR_PAD_BOTTOM))

        title_color = theme["status_err"] if is_error else accent
        v2on = bool(getattr(win, "_v2", False)) and ccv2 is not None
        scale = self._ui_scale()

        drag_targets = [bar]
        # v2 shows the brand badge (gradient rounded 'CC' + soft glow) — the
        # concept's designed mark. Legacy keeps the real .ico logo.
        if v2on:
            logo_img = self._v2_badge_image(28)
        else:
            logo_img = self._logo_image(15)
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=popup_bg, bd=0,
                                highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(0, 8 if v2on else 6))
            drag_targets.append(logo_lbl)

        if v2on and not is_error:
            # v2: the tri-colour brand gradient title (an image, not a glyph).
            title_img = self._v2_photo(
                ("title", title, round(scale, 2)),
                lambda: ccv2.gradient_text(
                    title, ccv2.load_font("bold", 13, scale)))
        else:
            title_img = None
        if title_img is not None:
            title_lbl = tk.Label(bar, image=title_img, bg=popup_bg, bd=0,
                                 highlightthickness=0)
            title_lbl.image = title_img
        else:
            title_lbl = tk.Label(bar, text=title if logo_img else "●  " + title,
                                 bg=popup_bg, fg=title_color,
                                 font=("Microsoft YaHei UI", 9, "bold"))
        title_lbl.pack(side="left")
        drag_targets.append(title_lbl)

        def _mk_btn(txt, cmd, danger=False):
            # v2 success popups get soft translucent pills (the concept's 操作 /
            # dynamic-action style); everything else keeps the legacy text pill.
            if v2on and not is_error and not danger:
                return self._v2_soft_button(bar, txt, cmd)
            active_bg = (theme["btn_close_active"] if danger
                         else theme["btn_active"])
            active_fg = "#ffffff" if danger else theme["fg"]
            return self._pill_button(
                bar, txt, cmd,
                bg=popup_bg, fg=hint,
                hover_bg=popup_bg, hover_fg=hint,
                active_bg=active_bg, active_fg=active_fg,
                font=("Microsoft YaHei UI", 9), padx=9, pady=1,
            )

        if v2on and not is_error:
            # v2: soft-pill primary actions + light ghost-icon window controls,
            # generously spaced — the concept's airy top-right cluster (not a row
            # of hard chips crammed together).
            close_btn = self._v2_ghost_button(
                bar, self._user_close_popup, icon="close", danger=True)
            close_btn.pack(side="right")

            pin_btn = self._v2_ghost_button(
                bar, lambda: self._toggle_popup_pin(win, pin_btn), icon="pin",
                tooltip=i18n.get("result.pin"))
            pin_btn.pack(side="right", padx=(0, 2))
            win._pin_btn = pin_btn

            copy_btn = self._v2_soft_button(
                bar, i18n.get("result.copy"), self._copy_result, icon="copy")
            copy_btn.pack(side="right", padx=(6, 8))
            win._copy_btn = copy_btn
            win._copy_set = getattr(copy_btn, "_chip_set", None)
        else:
            close_btn = _mk_btn("✕", self._user_close_popup, danger=True)
            close_btn.pack(side="right")

            # Pushpin toggle: keep this result above other windows only when the
            # user asks. Off by default (see _make_popup: win._pinned = False).
            pin_btn = tk.Button(
                bar, text="\uE718",
                command=lambda: self._toggle_popup_pin(win, pin_btn),
                bg=popup_bg, fg=hint, activebackground=popup_bg,
                activeforeground=accent, relief="flat", bd=0,
                highlightthickness=0,
                font=("Segoe MDL2 Assets", 10), cursor="hand2", padx=9, pady=1)
            pin_btn.pack(side="right", padx=(0, 4), pady=(4, 0))
            pin_btn.bind("<Enter>", lambda e: pin_btn.config(fg=accent))
            pin_btn.bind(
                "<Leave>",
                lambda e: pin_btn.config(
                    fg=(accent if getattr(win, "_pinned", False) else hint)))
            win._pin_btn = pin_btn
            self._make_tooltip(pin_btn, i18n.get("result.pin"))

            copy_btn = _mk_btn(i18n.get("result.copy"), self._copy_result)
            copy_btn.pack(side="right", padx=(0, 4))
            win._copy_btn = copy_btn
        win._btn_bar = bar
        win._mk_bar_btn = _mk_btn
        if is_error:
            retry_btn = _mk_btn(i18n.get("result.retranslate"), self._retry)
            retry_btn.pack(side="right", padx=(0, 4))

        # Legacy keeps a hairline under the header; v2 drops it (the concept has
        # no divider between the title and the translation body).
        if not v2on:
            tk.Frame(header, bg=popup_border, height=1,
                     bd=0, highlightthickness=0).pack(
                         fill="x", padx=POPUP_BAR_PAD_X)

        # Dragging the header (but not the buttons) moves the window.
        self._make_draggable(tuple(drag_targets), lambda: self.popup,
                             guard=lambda: self._resize_mode)

    def _toggle_popup_pin(self, win, pin_btn):
        """Toggle whether a result popup stays above other windows. Off by
        default; the header pushpin is the only way to turn it on. Failing to
        set the attribute must never crash the popup."""
        try:
            win._pinned = not getattr(win, "_pinned", False)
            win.attributes("-topmost", win._pinned)
        except Exception as e:
            log_error("toggle_pin", e)
            return
        t = self.theme
        accent = t.get("accent", "#7aa2f7")
        hint = t.get("popup_hint", t["hint_fg"])
        # v2 pin is an image chip: rest brighter (hover bake) while pinned.
        if getattr(pin_btn, "_chip_hover", None) is not None:
            rest = (pin_btn._chip_hover if win._pinned
                    else getattr(pin_btn, "_chip_base", pin_btn._chip_normal))
            pin_btn._chip_normal = rest
            pin_btn.config(image=rest)
        else:
            pin_btn.config(fg=(accent if win._pinned else hint))

    def _build_popup_body(self, win, frame, *, popup_bg, is_error, highlight):
        body = tk.Frame(frame, bg=popup_bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True,
                  padx=POPUP_BODY_PAD_X, pady=(0, POPUP_BODY_PAD_BOTTOM))

        scroll = ttk.Scrollbar(body, orient="vertical",
                               style="CC.Vertical.TScrollbar")
        text = tk.Text(
            body,
            bg=popup_bg,
            fg=self.theme["fg"],
            font=("Microsoft YaHei UI", self.cfg[CFG.FONT_SIZE]),
            wrap="word",
            relief="flat",
            bd=0,
            padx=POPUP_TEXT_PAD_X,
            pady=POPUP_TEXT_PAD_Y,
            insertwidth=0,
            selectbackground=self.theme["sel_bg"],
            highlightthickness=0,
            spacing1=3,
            spacing2=5,
            spacing3=3,
            width=1,
            height=1,
            yscrollcommand=scroll.set,
        )
        def _on_scrollbar(*args):
            # Dragging/clicking the scrollbar is a manual scroll too, so it must
            # also opt out of stream auto-pin-to-top.
            try:
                self._ss.user_scrolled = True
            except Exception:
                pass
            return text.yview(*args)
        scroll.config(command=_on_scrollbar)
        text.pack(side="left", fill="both", expand=True)
        win._text = text
        win._scroll = scroll
        win._scroll_body = body
        win._text_font = tkfont.Font(font=text.cget("font"))

        # Result popups render markdown-lite rich text; error popups stay plain
        # so a raw error string is never mis-parsed as markup.
        text._rich = not is_error
        if text._rich:
            self._configure_rich_tags(text)
            if highlight:
                # Final (non-streaming) frame: syntax-highlight code blocks.
                text._rich_highlight = True

    def _layout_popup_offscreen(self, win, message, anchor):
        """Measure and fill the popup while it stays parked off-screen, and
        return the final on-screen (w, h, x, y). The window is deliberately NOT
        moved on-screen here: the caller performs a single geometry move once
        the rounded card has been painted, so the reveal never flashes an
        unpainted colour-key (near-black) frame or a mid-measurement resize."""
        w, h = self._size_popup(win, message)
        if self._is_centered_layout():
            # Unified layout: the popup is sized by the SAME content-driven
            # measurement as follow-cursor mode (_size_popup); "centred" only
            # changes WHERE it lands — horizontally and vertically centred on
            # the active monitor. A final result's size is known, so it can be
            # centred exactly; streaming opens centred then grows down (see
            # _size_popup_stream_grow).
            rect = get_monitor_rect()
            if rect:
                left, top, right, bottom = rect
            else:
                left, top = 0, 0
                right = self.root.winfo_screenwidth()
                bottom = self.root.winfo_screenheight()
            x = left + ((right - left) - w) // 2
            y = top + ((bottom - top) - h) // 2
            x, y = self._clamp_to_monitor(x, y, w, h, ref=(x, y))
            return w, h, x, y
        if anchor is not None:
            x, y = int(anchor[0]), int(anchor[1])
            try:
                px = int(self.root.winfo_pointerx())
                py = int(self.root.winfo_pointery())
                if x == 0 and y == 0 and (px > 24 or py > 24):
                    x, y = px + 12, py + 18
            except Exception:
                pass
        else:
            x = self.root.winfo_pointerx() + 12
            y = self.root.winfo_pointery() + 18
        x, y = self._clamp_to_monitor(x, y, w, h, ref=anchor)
        return w, h, x, y

    def _bind_popup_window_events(self, win):
        win.bind("<Motion>", self._popup_motion)
        win.bind("<ButtonPress-1>", self._popup_press)
        win.bind("<B1-Motion>", self._popup_drag)
        win.bind("<ButtonRelease-1>", self._popup_release)
        win.bind("<Escape>", lambda e: self._user_close_popup())

    def _make_popup(self, message, anchor=None, is_error=False, title=None,
                    highlight=False, reveal=True):
        t = self.theme
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        # Result windows no longer force always-on-top. The user opts in per
        # window via the pin button in the header (see _build_popup_header);
        # default is off so results stop covering everything else.
        win._pinned = False
        # Give the result popup a real taskbar button so it can always be found
        # again when it isn't on top. Ex-style / owner changes must be applied
        # while the window is hidden, before deiconify, to take effect.
        try:
            win.update_idletasks()
            # Keep the Tk owner link for transparent rounded cards: detaching it
            # breaks Tk's reported root coordinates/focus behavior on some hosts.
            win32util.set_taskbar_presence(
                int(win.winfo_id()), True, detach_owner=False)
        except Exception as e:
            log_error("popup_taskbar", e)

        popup_bg = t.get("popup_bg", t["bg"])
        popup_border = t.get("popup_border", t["border"])
        hint = t.get("popup_hint", t["hint_fg"])
        accent = t.get("accent", "#7aa2f7")

        # v2 dark-launch skin: when the UI_V2 flag is on and the renderer is
        # available, mark the window v2 (so _rounded_shell paints the gradient
        # shell) and swap the content colours to the deep-navy palette. Legacy
        # is untouched when the flag is off — win._v2 stays False.
        win._v2 = self._v2_popup_on()
        if win._v2:
            v2c = self._v2_tk_colors()
            popup_bg = v2c["panel"]
            popup_border = v2c["border"]
            hint = v2c["hint"]
            accent = v2c["accent"]

        # Rounded corners via a transparent colour key (genuinely transparent on
        # this environment, unlike SetWindowRgn cut-outs which render black).
        card = self._rounded_shell(win, POPUP_CORNER_RADIUS,
                                   popup_bg, popup_border)
        self._build_popup_header(
            win, card, title=(title or i18n.get("result.title")),
            is_error=is_error, popup_bg=popup_bg,
            popup_border=popup_border, hint=hint, accent=accent, theme=t)
        self._build_popup_body(
            win, card, popup_bg=popup_bg, is_error=is_error,
            highlight=highlight)

        # Measure, fill and paint the popup while it is still WITHDRAWN, then
        # reveal it with exactly the same two-step sequence that
        # _reveal_rounded_window uses (the loading / history / settings windows
        # use that helper and never flash).
        #
        # The flash bug: the old code did deiconify() first, THEN measured the
        # text size.  DWM composited the window at its default/small size the
        # moment it was deiconified; moving on-screen later briefly showed that
        # stale composite → visible "small window first, then large" jump.
        #
        # Fix: keep the window WITHDRAWN for the entire measurement phase.
        # _size_popup drives the text widget through a full update() cycle,
        # which works on unmapped windows.  Once we know (w, h) we follow
        # _reveal_rounded_window exactly:
        #   1. geometry(w×h off-screen) + deiconify  → DWM first sees window
        #      at the CORRECT size (no stale small thumbnail)
        #   2. update_idletasks + _round_redraw       → paint rounded card
        #   3. geometry(w×h on-screen)                → position-only move;
        #      DWM composite is already correct, no size change on reveal
        win.geometry("+{}+{}".format(-4000, -4000))
        win.update_idletasks()   # lay out widgets while still withdrawn
        w, h, x, y = self._layout_popup_offscreen(win, message, anchor)
        self._record_v2_size(win, w, h)

        self._bind_popup_window_events(win)
        self._apply_taskbar_identity(win)
        # Cache the intended on-screen position so _window_xy can report it
        # even while the window is still parked off-screen (needed by the
        # deferred reveal path below, where the caller performs the reveal).
        self._remember_window_xy(win, x, y)

        # Step 1: deiconify at the correct final size, still off-screen.
        # DWM now has a composite at (w, h) — no default-size artefact.
        # Use win.update() (not just update_idletasks) so the canvas
        # <Configure> event fires and cv.winfo_width()/height() settle to
        # their real values before _round_redraw() tries to paint.
        # update_idletasks() only flushes idle tasks; the Configure event
        # that sizes the canvas travels through the normal event queue and
        # can be missed, leaving cv.winfo_width() == 1 so _round_redraw()
        # returns early — the window then reaches the screen as an
        # unpainted white rectangle.
        win.geometry(f"{w}x{h}+-4000+-4000")
        win.deiconify()
        win.update()
        win._round_redraw()

        if not reveal:
            # Deferred reveal: window is mapped off-screen at its correct
            # size; the caller (e.g. streaming first frame) performs the
            # sole on-screen geometry move so no intermediate size is shown.
            return win

        # Step 2: position-only move on-screen — size does not change, so
        # DWM's existing composite is the correct final frame.
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        win.update_idletasks()
        win._round_redraw()
        self._bring_to_front(win)
        return win

    def _size_popup(self, win, message):
        """Set the message and return the popup's exact (width, height) in px,
        measured from tkinter's own layout of the real text — not estimated.

        The Text width (in char columns) is the longest logical line capped at
        a max; tkinter then reports the precise pixel reqwidth/reqheight, and
        we read the true wrapped line count for the height."""
        text = win._text
        # Content sits inside the rounded colour-key card, inset by the corner
        # radius on every side, so the window must be that much larger than the
        # measured text. (Cancels with the body pad exactly as the old shell-pad
        # inset did, so effective wrapping width is unchanged.) The v2 skin adds
        # its glow-halo margin on top, so the shared math reserves room for it.
        shell_pad = POPUP_CORNER_RADIUS + self._v2_margin()

        rect = get_monitor_rect()
        mon_w = (rect[2] - rect[0]) if rect else self.root.winfo_screenwidth()
        # Column cap: long results may widen up to the centred card's width so
        # they don't wrap into a tall, narrow column; short results still hug
        # their own longest line (see `cols` below). Never wider than the
        # monitor allows.
        avg_char_px = max(win._text_font.measure("0"), 7)
        screen_cap = max(24, int((mon_w * 0.9) / avg_char_px))
        max_cols = min(self._wide_cols(win, shell_pad), screen_cap)

        # Longest logical line in display columns (CJK counts as 2).
        def line_cols(s):
            return sum(2 if ord(c) > 0x2E7F else 1 for c in s)
        longest_cols = max((line_cols(ln) for ln in message.split("\n")),
                           default=1)
        # +2 cols of slack: Text's char-based width vs real CJK glyph width is
        # inexact, and a tight fit makes a line wrap spuriously (a 1-line
        # string measured as 3), leaving the window too tall.
        cols = min(max(longest_cols + 2, 8), max_cols)

        self._fill_text(text, message)
        text.config(width=cols, height=1)
        text.update_idletasks()
        # Pre-stretch the popup to the Text's requested width BEFORE counting
        # wrapped display lines, and make sure the window is actually MAPPED
        # while we count.
        #
        # A withdrawn (unmapped) Toplevel reports a 1px-wide Text, so wrap="word"
        # folds every single character onto its own line and the display-line
        # count explodes — a 2-line translation gets measured as dozens of lines,
        # then min(.., max_lines) leaves the window hugely too tall with a big
        # empty gap below the text (and sometimes a spurious scrollbar). Fresh
        # non-streaming popups are measured while still withdrawn, which is
        # exactly when this bit. Mapping the window off-screen first makes the
        # geometry take effect so the Text has its real width and the count is
        # correct. (Streaming never hit this: it reuses an already-mapped window;
        # the append path below is likewise already on-screen — hence the guard.)
        req_w = text.winfo_reqwidth() + (shell_pad * 2)
        if win.winfo_ismapped():
            win.geometry(f"{req_w}x1000")
        else:
            win.geometry(f"{req_w}x1000+-4000+-4000")
            win.deiconify()
        text.update_idletasks()
        text.update()
        try:
            true_lines = int(text.count("1.0", "end", "displaylines")[0])
        except Exception:
            true_lines = message.count("\n") + 1
        true_lines = max(true_lines, 1)
        max_lines = 22
        display_lines = min(true_lines, max_lines)
        text.config(height=display_lines)

        # Show the scrollbar only when the content is taller than the popup.
        if true_lines > max_lines:
            win._scroll.pack(side="right", fill="y")
            win._text.bind("<MouseWheel>", self._on_mousewheel)
            win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
        else:
            win._scroll.pack_forget()
        text.update()

        w = text.winfo_reqwidth() + (shell_pad * 2)
        if true_lines > max_lines:
            w += win._scroll.winfo_reqwidth()
        bar_h = win._bar.winfo_reqheight() if getattr(win, "_bar", None) else 26
        # The body frame is packed with pady=(0, POPUP_BODY_PAD_BOTTOM); that
        # bottom gap lives inside the card but is NOT part of the Text's own
        # reqheight, so it must be added explicitly. Omitting it squeezed the
        # Text below its requested height and clipped the last line's descenders
        # (e.g. the "p" in "unmapped" showed only its top half).
        h = text.winfo_reqheight() + bar_h + (shell_pad * 2) + POPUP_BODY_PAD_BOTTOM
        # Final content already fits the text exactly; use the compact floor so a
        # short result hugs its text instead of being padded to the streaming
        # minimum.
        h = max(int(h), MIN_POPUP_HEIGHT_COMPACT)
        return int(w), int(h)

    def _size_popup_stream_grow(self, win, message):
        """Streaming: width fixed, height grows down from a fixed anchor.

        TWO heights, deliberately decoupled:

        * The OPENING height is modest — STREAM_OPEN_MIN_LINES of text (DPI-
          scaled from the live font). The window opens near its real size and
          only ever grows past it with content; it never shrinks. So a short
          streamed output (a summary compresses a long selection into a few
          lines, and streaming is gated on INPUT length, not output length)
          opens small and stays small — no "先大再小" balloon-then-collapse —
          while a long translation grows smoothly downward.
        * The RESERVED room — how far the anchor is pushed up when the cursor
          sits near the screen bottom — uses the taller centred-card height so
          a long stream has somewhere to grow into. This only shifts a short
          popup's *position* slightly higher; it never inflates its height.

        The monitor and anchor are locked on the first frame so a mouse that
        drifts mid-stream never drags the window around."""
        text = win._text
        # Match _size_popup: colour-key card inset is the corner radius (+ the
        # v2 glow-halo margin when the v2 skin is active).
        shell_pad = POPUP_CORNER_RADIUS + self._v2_margin()

        # Lock the monitor on the first frame; reuse it for the whole stream so a
        # cursor that wanders onto another display can't shift the anchor.
        if self._ss.monitor_rect is not None:
            left, top, right, bottom = self._ss.monitor_rect
        else:
            rect = get_monitor_rect()
            if rect:
                left, top, right, bottom = rect
            else:
                left, top = 0, 0
                right = self.root.winfo_screenwidth()
                bottom = self.root.winfo_screenheight()
            self._ss.monitor_rect = (left, top, right, bottom)
        mon_w = right - left
        mon_h = bottom - top

        avg_char_px = max(win._text_font.measure("0"), 7)
        screen_cap = max(24, int((mon_w * 0.9) / avg_char_px))
        # Streamed output is long by definition, so from the first frame use the
        # centred card's width (clamped to screen). This stops long translations
        # from piling up in a tall, narrow column that scrolls forever. Width is
        # locked for the whole stream via _ss.cols so it never wanders.
        preferred_cols = min(max(36, self._wide_cols(win, shell_pad)), screen_cap)
        cols = self._ss.cols or preferred_cols
        self._ss.cols = cols

        # Preserve the reader's scroll position across the delete+reinsert in
        # _fill_text. That rebuild snaps the view to the top; the forced
        # update() calls below (used to measure the line count) would then PAINT
        # the top for a frame before _set_popup_text restored the position,
        # flashing the whole popup on every ~50ms stream frame once the user had
        # scrolled ("闪来闪去"). Capturing the top line BEFORE the refill and
        # re-applying it BEFORE each measurement paint keeps scrolling during a
        # live stream flicker-free. (The streamed text is append-only, so this
        # line index stays valid after the rebuild.)
        prev_top = None
        if getattr(self._ss, "user_scrolled", False):
            try:
                prev_top = text.index("@0,0")
            except Exception:
                prev_top = None

        self._fill_text(text, message)
        if prev_top is not None:
            try:
                text.yview(prev_top)
            except Exception:
                pass
        text.config(width=cols, height=1)
        text.update_idletasks()
        text.update()
        try:
            true_lines = int(text.count("1.0", "end", "displaylines")[0])
        except Exception:
            true_lines = message.count("\n") + 1
        true_lines = max(true_lines, 1)

        bar_h = win._bar.winfo_reqheight() if getattr(win, "_bar", None) else 26

        # Fix the anchor ONCE. Reserve room below it using the TALLER centred
        # card height so a long stream has space to grow into when the cursor is
        # near the screen bottom. This is RESERVATION ONLY (positioning) — it is
        # decoupled from the modest opening height below, so a short summary that
        # never fills this room isn't inflated to it; the anchor just sits a
        # little higher on a low cursor, which is harmless.
        reserve_h = min(self._centered_height_px(), max(120, mon_h - 20))
        min_top = top + 12
        if self._ss.origin_y is None or self._ss.origin_x is None:
            if self._is_centered_layout():
                # Centred layout (方案1): OPEN centred on the monitor, then grow
                # downward — same content-driven sizing/rendering as follow-cursor
                # mode, only positioned centrally. The opening frame is vertically
                # centred so it lands exactly where the centred loading hint was
                # (no jump when the result replaces it); the fixed top then lets
                # later frames extend downward without a per-frame recentre. No
                # cursor is read, so a low mouse can't shrink it. origin_x is
                # recomputed from the locked width each frame in _set_popup_text.
                line_px_est = max(win._text_font.metrics("linespace") + 6, 14)
                open_h_est = (bar_h + (shell_pad * 2) + POPUP_BODY_PAD_BOTTOM
                              + STREAM_OPEN_MIN_LINES * line_px_est)
                max_origin_y = max(min_top, bottom - open_h_est - 8)
                self._ss.origin_y = max(
                    min_top, min(top + (mon_h - open_h_est) // 2, max_origin_y))
                self._ss.origin_x = left
            else:
                cx, cy = self._window_xy(win)
                if (cx, cy) == (0, 0):
                    cx, cy = left + 12, min_top
                # Push up so at least reserve_h fits below the anchor (or as
                # much as a short screen allows).
                max_origin_y = max(min_top, bottom - reserve_h - 8)
                self._ss.origin_y = min(max(cy, min_top), max_origin_y)
                self._ss.origin_x = cx
        # Height may only grow downward from the fixed anchor to the screen edge.
        max_popup_h = max(1, int(bottom - self._ss.origin_y - 8))

        available_text_h = max(
            24, max_popup_h - bar_h - (shell_pad * 2) - POPUP_BODY_PAD_BOTTOM)
        line_px = max(win._text_font.metrics("linespace") + 6, 14)
        max_lines_by_height = max(4, int(available_text_h / line_px))

        display_lines = min(true_lines, max_lines_by_height)
        text.config(height=display_lines)

        if true_lines > max_lines_by_height:
            win._scroll.pack(side="right", fill="y")
            win._text.bind("<MouseWheel>", self._on_mousewheel)
            win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
        else:
            win._scroll.pack_forget()
        if prev_top is not None:
            try:
                text.yview(prev_top)
            except Exception:
                pass
        text.update()

        w = text.winfo_reqwidth() + (shell_pad * 2)
        if true_lines > max_lines_by_height:
            w += win._scroll.winfo_reqwidth()
        # Include the body frame's bottom pad (pady=(0, POPUP_BODY_PAD_BOTTOM)):
        # like _size_popup, this gap sits inside the card but outside the Text's
        # reqheight, so leaving it out clipped the final streamed line.
        content_h = (text.winfo_reqheight() + bar_h + (shell_pad * 2)
                     + POPUP_BODY_PAD_BOTTOM)

        if not self._ss.fixed_w:
            self._ss.fixed_w = int(w)

        # Opening floor is a few LINES of text (DPI-scaled via the live font),
        # NOT the tall centred-card height reserved above for positioning. The
        # window opens near its real size and only grows: a short summary opens
        # small and stays small (no balloon-then-collapse), a long translation
        # grows downward into the reserved room. Growth is monotonic (max_h) so
        # it never shrinks — no "先大再小" flicker, and a mid-stream drag stays
        # put. The final frame runs the same path (grow-only lands exactly on
        # the final content height because content only ever appends).
        open_floor = (bar_h + (shell_pad * 2) + POPUP_BODY_PAD_BOTTOM
                      + STREAM_OPEN_MIN_LINES * line_px)
        h = max(content_h, min(open_floor, max_popup_h))
        if self._ss.max_h:
            h = max(h, self._ss.max_h)
        h = min(h, max_popup_h)
        self._ss.max_h = int(h)

        return int(self._ss.fixed_w), int(h)

    def _remember_window_xy(self, win, x, y):
        """Cache the last geometry position we explicitly applied to `win`.
        Needed because some Win32 style combos can make Tk briefly report (0,0)
        even though the real window is elsewhere."""
        try:
            win._screen_xy = (int(x), int(y))
        except Exception:
            pass

    def _window_xy(self, win):
        """Best-effort screen position for a Toplevel.
        Prefer Tk's x/y, but fall back to cached geometry when Tk reports stale
        origin coordinates for owner-style edge cases."""
        cached = getattr(win, "_screen_xy", None)
        try:
            x = int(win.winfo_x())
            y = int(win.winfo_y())
        except Exception:
            if isinstance(cached, tuple) and len(cached) == 2:
                return int(cached[0]), int(cached[1])
            return 0, 0
        if isinstance(cached, tuple) and len(cached) == 2:
            cx, cy = int(cached[0]), int(cached[1])
            if (x, y) == (0, 0) and (cx, cy) != (0, 0):
                return cx, cy
            # While a popup is parked far off-screen (deferred reveal), Tk
            # reports the park coordinates. Prefer the intended on-screen
            # position we cached so stream anchoring/monitor detection is right.
            if x <= -3000 and cx > -3000:
                return cx, cy
        return x, y

    def _on_mousewheel(self, event):
        if self.popup and getattr(self.popup, "_text", None):
            # A manual scroll opts the user out of stream auto-pin-to-top so a
            # later frame (or the final frame) won't yank the view back up.
            try:
                self._ss.user_scrolled = True
            except Exception:
                pass
            self.popup._text.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _bring_to_front(self, win):
        """Raise a borderless window above other apps AND make it the true
        foreground/active window, so it behaves like a normal window.

        Two failure modes this addresses:
        * A plain lift() only reorders within our own app's z-group, so a window
          sitting behind another application would appear 'stuck' and re-pressing
          the hotkey would feel like the feature failed to launch.
        * Merely *raising* a window (e.g. a topmost pulse) without *activating*
          it leaves Windows in a 'top-but-not-active' state: the window floats on
          top, yet clicking another app won't send it behind until this window is
          clicked once. That is the 'still force-topmost' feeling users hit.

        So we do a real foreground activation on the window's ACTUAL top-level
        HWND (Tk's winfo_id() is only the inner frame — activation on it no-ops).
        Only if the OS refuses activation (rare foreground-lock races) do we fall
        back to a brief, self-releasing topmost pulse purely for visibility."""
        try:
            win.deiconify()
        except Exception:
            pass
        try:
            win.lift()
        except Exception:
            pass
        activated = False
        try:
            top_hwnd = win32util.get_toplevel_hwnd(int(win.winfo_id()))
            activated = win32util.activate_foreground(top_hwnd)
        except Exception:
            activated = False
        try:
            win.focus_set()
        except Exception:
            try:
                win.focus_force()
            except Exception:
                pass
        if activated or getattr(win, "_pinned", False):
            # Real activation succeeded (window is active and can be sent behind
            # by clicking elsewhere), or the window is pinned and legitimately
            # stays topmost — either way, no fallback pulse needed.
            return
        try:
            win.attributes("-topmost", True)
        except Exception:
            return

        def _release_topmost():
            try:
                if tk.Toplevel.winfo_exists(win) and not getattr(
                        win, "_pinned", False):
                    win.attributes("-topmost", False)
            except Exception:
                pass

        try:
            win.after(90, _release_topmost)
        except Exception:
            _release_topmost()

    def _apply_taskbar_identity(self, win, title=None):
        """Give a borderless Toplevel a proper taskbar / Alt-Tab identity so its
        hover preview shows the app name and icon rather than Tk's default
        'tk' title and feather icon."""
        try:
            win.title(title or APP_NAME)
        except Exception:
            pass
        try:
            if os.path.exists(ICON_PATH):
                win.iconbitmap(ICON_PATH)
        except Exception:
            pass

    def _setup_rounded_window(self, win, radius):
        """Attach reliable rounded corners via window-proc subclassing. Windows
        re-applies the region on every real resize (WM_WINDOWPOSCHANGED), so the
        corners can no longer flicker off or get stuck square after a shrink."""
        win._corner_radius = max(0, int(radius))
        attach_rounded_corners(win, win._corner_radius)

    def _rounded_shell(self, win, radius, card_bg, border):
        """Turn a borderless Toplevel into a rounded card using a transparent
        colour key, so its corners are genuinely transparent (verified to work
        in this environment where SetWindowRgn cut-outs render opaque). Returns
        the content Frame to fill; the window reveals via deiconify (colour-key
        transparency is incompatible with -alpha, so don't mix them)."""
        if getattr(win, "_v2", False):
            return self._rounded_shell_v2(win, radius, card_bg, border)
        win.configure(bg=ROUND_KEY_COLOR)
        try:
            win.wm_attributes("-transparentcolor", ROUND_KEY_COLOR)
        except Exception:
            pass
        cv = tk.Canvas(win, bg=ROUND_KEY_COLOR, highlightthickness=0, bd=0,
                       takefocus=0)
        cv.pack(fill="both", expand=True)
        card = tk.Frame(cv, bg=card_bg, bd=0, highlightthickness=0)
        item = cv.create_window(radius, radius, anchor="nw", window=card)

        def _redraw(event=None):
            w = cv.winfo_width()
            h = cv.winfo_height()
            if w <= 2 or h <= 2:
                return
            cv.delete("cc_shell")
            _draw_round_rect(cv, 0, 0, w, h, radius,
                             fill=border, outline=border, tags="cc_shell")
            _draw_round_rect(cv, 1, 1, w - 1, h - 1, radius,
                             fill=card_bg, outline=card_bg, tags="cc_shell")
            cv.tag_lower("cc_shell")
            cv.coords(item, radius, radius)
            cv.itemconfigure(item, width=w - 2 * radius, height=h - 2 * radius)

        cv.bind("<Configure>", _redraw)
        win._round_canvas = cv
        win._round_redraw = _redraw
        return card

    def _rounded_shell_v2(self, win, radius, card_bg, border):
        """v2 skin of the rounded shell: the canvas paints a flat deep-navy plate
        with a thin brand-gradient hairline at the rounded perimeter (via
        cc_ui_v2.GradientBackground — a height-stable bake the streaming redraw
        just crops, no per-frame gradient math), and the solid content card sits
        inset by the corner radius so its square corners hide inside the rounded
        shape. The card colour equals the plate colour, so the radius-wide reveal
        around it is invisible and only the perimeter hairline shows. The
        header/body/text tree inside `card` is identical to legacy, so the whole
        geometry / streaming / scroll engine is reused unchanged."""
        pal = self._v2_palette()
        scale = self._ui_scale()
        margin = ccv2.scaled(V2_HALO_PTS, scale)
        gb = ccv2.GradientBackground(pal, scale=scale)
        win._v2_gb = gb

        win.configure(bg=ROUND_KEY_COLOR)
        try:
            win.wm_attributes("-transparentcolor", ROUND_KEY_COLOR)
        except Exception:
            pass
        cv = tk.Canvas(win, bg=ROUND_KEY_COLOR, highlightthickness=0, bd=0,
                       takefocus=0)
        cv.pack(fill="both", expand=True)
        card = tk.Frame(cv, bg=card_bg, bd=0, highlightthickness=0)
        item = cv.create_window(radius + margin, radius + margin, anchor="nw",
                                window=card)

        def _redraw(event=None):
            # A <Configure> event carries the ACTUAL new canvas size (the WM has
            # already resized it), so trust it. A manual call (event is None)
            # fires right after a programmatic win.geometry() — at which point
            # cv.winfo_width() is still the OLD size, because the WM resize is
            # asynchronous (even win.geometry()/winfo_geometry() read back the
            # old value until the event loop runs). Painting the gradient face or
            # sizing the content card at that stale size was the real cause of
            # the "black halo" on a grown edge and the clipped last streamed line
            # (issues seen mid-stream): the face was smaller than the window, so
            # the freshly grown strip showed the transparent colour key, and the
            # card was configured too short. On a manual call use the intended
            # size the resizer recorded in win._v2_size instead.
            if event is not None and int(getattr(event, "width", 0)) > 2:
                w, h = int(event.width), int(event.height)
            else:
                tgt = getattr(win, "_v2_size", None)
                if tgt is not None:
                    w, h = int(tgt[0]), int(tgt[1])
                else:
                    w, h = cv.winfo_width(), cv.winfo_height()
            if w <= 2 or h <= 2:
                return
            cv.delete("cc_shell")
            # The gradient hairline shell fills the whole rounded window: a flat
            # navy plate (identical to the content card colour) with a thin
            # brand-gradient stroke at the perimeter. The content card (tk.Frame)
            # sits inset by `radius` so its square corners hide inside the
            # rounded shape; the radius-wide reveal around it is the SAME navy, so
            # only the perimeter hairline shows.
            face = gb.rounded_face(w, h, radius)
            photo = ccv2.to_photo(face, master=cv)
            if photo is not None:
                win._v2_face = photo   # keep a ref so Tk doesn't drop the image
                cv.create_image(0, 0, anchor="nw", image=photo, tags="cc_shell")
            else:
                # Pillow unavailable: fall back to a flat rounded card with a
                # single-colour outline (no gradient), still no wide margin.
                _draw_round_rect(cv, 0, 0, w, h, radius, fill=card_bg,
                                 outline=border, tags="cc_shell")
            cv.tag_lower("cc_shell")
            cv.coords(item, radius + margin, radius + margin)
            cv.itemconfigure(item, width=w - 2 * (radius + margin),
                             height=h - 2 * (radius + margin))

        cv.bind("<Configure>", _redraw)
        win._round_canvas = cv
        win._round_redraw = _redraw
        return card

    def _ui_scale(self):
        """Physical-pixels-per-logical-point scale for the active display."""
        try:
            return self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            return 1.0

    def _record_v2_size(self, win, w, h):
        """Record the size a v2 popup is being resized to, so the shell repaint
        (_rounded_shell_v2's _redraw) can paint at the intended size on a manual
        call — the canvas's own winfo lags a programmatic win.geometry() by an
        event-loop turn, which otherwise left a transparent gap on the grown
        edge and clipped the last streamed line. No-op for legacy windows."""
        if getattr(win, "_v2", False):
            try:
                win._v2_size = (int(w), int(h))
            except Exception:
                pass

    def _v2_popup_on(self):
        """True only when the v2 result skin should render: the UI_V2 flag is on
        AND the cc_ui_v2 renderer (Pillow) is available. Every v2 branch gates on
        this, so with the flag off (default) the popup is byte-for-byte legacy."""
        if ccv2 is None:
            return False
        try:
            if not ui_v2_enabled(self.cfg):
                return False
        except Exception:
            return False
        try:
            return ccv2.ui_v2_available()
        except Exception:
            return False

    def _v2_palette(self):
        """The cc_ui_v2 palette matching the app's active theme."""
        name = "light" if resolve_theme_name(self.cfg) == "light" else "dark"
        return ccv2.get_palette(name)

    def _v2_margin(self):
        """Extra inset (physical px) the v2 shell reserves between the rounded
        window edge and the content card, on top of the corner radius. Now 0 —
        the v2 frame is a thin baked hairline, not a wide margin. Kept so the
        shared sizing math has a single hook if a small inset is ever wanted."""
        if not self._v2_popup_on():
            return 0
        return ccv2.scaled(V2_HALO_PTS, self._ui_scale())

    def _v2_tk_colors(self):
        """v2 result-popup colours as tk hex strings (card/border/hint/accent),
        derived from the cc_ui_v2 palette. Body text keeps the theme fg.

        ``panel`` is the shell's flat navy (``panel_match_color`` -> flat_base),
        so the content card is exactly the same navy as the plate around it and
        the radius-wide reveal is invisible — the only edge that shows is the
        thin brand hairline baked at the perimeter."""
        pal = self._v2_palette()
        scale = self._ui_scale()
        panel = ccv2.rgb_to_hex(ccv2.panel_match_color(pal, scale))
        sub = ccv2.rgb_to_hex(pal["sub"])
        # A subtle border a touch lighter than the panel (hairline on navy).
        if pal["is_dark"]:
            border = ccv2.rgb_to_hex((44, 48, 92))
            accent = ccv2.rgb_to_hex((161, 121, 255))   # brand violet
        else:
            border = ccv2.rgb_to_hex((214, 220, 240))
            accent = ccv2.rgb_to_hex((124, 92, 246))
        return {"panel": panel, "border": border, "hint": sub, "accent": accent}

    def _v2_photo(self, key, factory):
        """Cache-and-keep a PhotoImage for the v2 popup header (Tk drops
        unreferenced images). ``factory`` builds the PIL image on a cache miss."""
        cache = getattr(self, "_v2_photo_cache", None)
        if cache is None:
            cache = self._v2_photo_cache = {}
        photo = cache.get(key)
        if photo is None:
            try:
                photo = ccv2.to_photo(factory(), master=self.root)
            except Exception:
                photo = None
            cache[key] = photo
        return photo

    def _v2_badge_image(self, size_pt):
        """The v2 brand badge (gradient rounded 'CC' + soft glow) as a cached
        PhotoImage, replacing the flat-.ico-on-blob logo. Returns None if the
        renderer is unavailable (caller falls back to the plain logo)."""
        scale = self._ui_scale()
        return self._v2_photo(
            ("badge", size_pt, round(scale, 2)),
            lambda: ccv2.brand_badge(size_pt, self._v2_palette(), scale)[0])

    def _v2_soft_button(self, parent, text, cmd, *, icon=None, caret=False,
                        tooltip=None):
        """A soft translucent pill button (concept's 复制 / 操作 style) as a
        tk.Button whose image swaps normal<->hover. Exposes _chip_set(label) to
        re-bake the label (copy-feedback / processing text). Falls back to a
        plain text pill if the renderer can't build the image."""
        pal = self._v2_palette()
        scale = self._ui_scale()
        popup_bg = self._v2_tk_colors()["panel"]

        def _bake(label, hover):
            font = ccv2.load_font("reg", 10, scale) if label else None
            return self._v2_photo(
                ("soft", label, icon, caret, hover, round(scale, 2)),
                lambda: ccv2.soft_pill(text=label, icon=icon, font=font,
                                       palette=pal, scale=scale, hover=hover,
                                       caret=caret))

        normal = _bake(text, False)
        hover = _bake(text, True)
        if normal is None:
            return self._pill_button(parent, text or "", cmd, bg=popup_bg,
                                     fg=self._v2_tk_colors()["hint"])
        b = tk.Button(parent, image=normal, command=cmd, bg=popup_bg,
                      activebackground=popup_bg, relief="flat", bd=0,
                      highlightthickness=0, cursor="hand2")
        b.image = normal
        b._chip_normal = normal
        b._chip_hover = hover
        b.bind("<Enter>", lambda e: b.config(image=b._chip_hover))
        b.bind("<Leave>", lambda e: b.config(image=b._chip_normal))

        def _set(label):
            n = _bake(label, False)
            h = _bake(label, True)
            if n is not None:
                b._chip_normal, b._chip_hover = n, h
                b.config(image=n)
        b._chip_set = _set
        if tooltip:
            self._make_tooltip(b, tooltip)
        return b

    def _v2_ghost_button(self, parent, cmd, *, icon, danger=False,
                         tooltip=None):
        """A light-weight icon-only window control (pin / close): transparent at
        rest, soft round fill on hover (subtle red for close). Keeps the header
        airy instead of a row of hard chips. _chip_base holds the rest image so
        a toggled state (pinned) can pin the hover look as the new rest."""
        pal = self._v2_palette()
        scale = self._ui_scale()
        popup_bg = self._v2_tk_colors()["panel"]

        def _bake(hover):
            return self._v2_photo(
                ("ghost", icon, danger, hover, round(scale, 2)),
                lambda: ccv2.ghost_icon(icon, pal, scale, hover=hover,
                                        danger=danger))

        normal = _bake(False)
        hover = _bake(True)
        if normal is None:
            return self._pill_button(parent, "", cmd, bg=popup_bg,
                                     fg=self._v2_tk_colors()["hint"])
        b = tk.Button(parent, image=normal, command=cmd, bg=popup_bg,
                      activebackground=popup_bg, relief="flat", bd=0,
                      highlightthickness=0, cursor="hand2")
        b.image = normal
        b._chip_normal = normal
        b._chip_base = normal
        b._chip_hover = hover
        b.bind("<Enter>", lambda e: b.config(image=b._chip_hover))
        b.bind("<Leave>", lambda e: b.config(image=b._chip_normal))
        if tooltip:
            self._make_tooltip(b, tooltip)
        return b

    def _apply_window_rounding(self, win):
        """Force an immediate corner refresh at the window's current real size.
        Colour-key windows (the common case now) just repaint their rounded
        canvas; the few remaining region-clipped windows re-apply SetWindowRgn."""
        redraw = getattr(win, "_round_redraw", None)
        if redraw is not None:
            try:
                redraw()
            except Exception:
                pass
            return
        radius = int(getattr(win, "_corner_radius", POPUP_CORNER_RADIUS))
        try:
            _round_apply_region(int(win.winfo_id()), radius)
        except Exception:
            pass

    def _clamp_to_monitor(self, x, y, w, h, ref=None):
        """Keep a w×h window fully inside a monitor. The monitor is chosen by
        `ref` (a screen point); defaults to the current cursor position."""
        rect = get_monitor_rect(ref)
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        x = max(left + 4, min(x, right - w - 4))
        y = max(top + 4, min(y, bottom - h - 4))
        return x, y

    def _is_centered_layout(self):
        return self.cfg.get(CFG.POPUP_LAYOUT, "dynamic") == "centered"

    def _scaled_centered_box(self, logical_w, logical_h, min_w=280, min_h=150):
        """Scale a logical window size by DPI and centre it on the active monitor."""
        scale = 1.0
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            pass
        w = int(logical_w * scale)
        h = int(logical_h * scale)
        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        mon_w, mon_h = right - left, bottom - top
        w = max(min_w, min(w, mon_w - 40))
        h = max(min_h, min(h, mon_h - 40))
        x = left + (mon_w - w) // 2
        y = top + (mon_h - h) // 2
        return w, h, x, y

    def _centered_box(self):
        """Fixed popup geometry (w, h, x, y) in physical px, centred on the
        active monitor. Size is a DPI-scaled logical box (~2x the dynamic
        popup at a 4:3 ratio), clamped to fit the monitor."""
        return self._scaled_centered_box(CENTERED_POPUP_W, CENTERED_POPUP_H)

    def _centered_width_px(self):
        """Physical width of the centred result card (DPI-scaled, clamped to the
        monitor). Used as the upper width bound for wide follow-cursor popups so
        a long result reads at the same comfortable width as the fixed centred
        layout instead of wrapping into a tall, narrow column."""
        scale = 1.0
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            pass
        rect = get_monitor_rect()
        mon_w = (rect[2] - rect[0]) if rect else self.root.winfo_screenwidth()
        return max(280, min(int(CENTERED_POPUP_W * scale), mon_w - 40))

    def _centered_height_px(self):
        """Physical height of the centred result card (DPI-scaled, clamped to the
        monitor). Used as the streaming popup's opening/floor height: streaming
        only runs for long text, so the result is always tall — opening at this
        height (instead of hugging the first tiny chunk) removes the
        tiny-then-grow jitter and gives streamed output a stable reading area."""
        scale = 1.0
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            pass
        rect = get_monitor_rect()
        mon_h = (rect[3] - rect[1]) if rect else self.root.winfo_screenheight()
        return max(150, min(int(CENTERED_POPUP_H * scale), mon_h - 40))

    def _wide_cols(self, win, shell_pad):
        """Column count whose rendered width matches the centred result card, so
        a wide follow-cursor popup reads at the same comfortable width. Short
        content stays narrow because the caller still clamps to the content's
        own longest line; this only raises the ceiling."""
        avg_char_px = max(win._text_font.measure("0"), 7)
        text_px = (self._centered_width_px()
                   - 2 * shell_pad - 2 * POPUP_TEXT_PAD_X)
        return max(1, int(text_px / avg_char_px))

    def _history_box(self):
        """A roomier centred box for the feature-rich history window."""
        return self._scaled_centered_box(HISTORY_WINDOW_W, HISTORY_WINDOW_H)

    def _resize_hit(self, win, x, y):
        w, h = win.winfo_width(), win.winfo_height()
        # Overrideredirect windows can report slightly off local coordinates,
        # especially near the bottom edge; widen and normalize hit bands.
        hit = RESIZE_HIT
        edge_x = "w" if x <= hit else ("e" if x >= w - hit else "")
        edge_y = "n" if y <= hit else ("s" if y >= h - hit else "")
        if not edge_y and y >= h - (hit * 2):
            edge_y = "s"
        return edge_y + edge_x

    def _resize_cursor(self, mode):
        return {
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }.get(mode, "arrow")

    def _popup_motion(self, event):
        win = self.popup
        if not win:
            return
        if self._resize_mode:
            return
        wx, wy = self._window_xy(win)
        lx = event.x_root - wx
        ly = event.y_root - wy
        mode = self._resize_hit(win, lx, ly)
        try:
            win.configure(cursor=self._resize_cursor(mode))
        except Exception:
            pass

    def _popup_press(self, event):
        win = self.popup
        if not win:
            return
        wx, wy = self._window_xy(win)
        lx = event.x_root - wx
        ly = event.y_root - wy
        mode = self._resize_hit(win, lx, ly)
        if not mode:
            self._resize_mode = None
            self._resize_start = None
            return
        ox, oy = self._window_xy(win)
        self._resize_mode = mode
        self._resize_start = (
            event.x_root, event.y_root,
            ox, oy,
            win.winfo_width(), win.winfo_height(),
        )

    def _popup_drag(self, event):
        win = self.popup
        if not (win and self._resize_mode and self._resize_start):
            return
        sx, sy, ox, oy, ow, oh = self._resize_start
        dx, dy = event.x_root - sx, event.y_root - sy
        x, y, w, h = ox, oy, ow, oh

        mode = self._resize_mode
        if "e" in mode:
            w = ow + dx
        if "s" in mode:
            h = oh + dy
        if "w" in mode:
            x = ox + dx
            w = ow - dx
        if "n" in mode:
            y = oy + dy
            h = oh - dy

        w = max(MIN_RESIZE_WIDTH, int(w))
        h = max(MIN_RESIZE_HEIGHT, int(h))

        rect = get_monitor_rect((ox, oy))
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()

        if x < left + 4:
            if "w" in mode:
                w -= (left + 4 - x)
            x = left + 4
        if y < top + 4:
            if "n" in mode:
                h -= (top + 4 - y)
            y = top + 4

        if x + w > right - 4:
            if "e" in mode:
                w = right - 4 - x
            else:
                x = max(left + 4, right - 4 - w)
        if y + h > bottom - 4:
            if "s" in mode:
                h = bottom - 4 - y
            else:
                y = max(top + 4, bottom - 4 - h)

        w = max(MIN_RESIZE_WIDTH, int(w))
        h = max(MIN_RESIZE_HEIGHT, int(h))
        win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self._remember_window_xy(win, int(x), int(y))
        # Rounded region is refreshed automatically by the window-proc subclass
        # on WM_WINDOWPOSCHANGED, so no manual (potentially stale) call here.

    def _popup_release(self, _event):
        self._resize_mode = None
        self._resize_start = None

    def _make_draggable(self, widgets, win_getter, guard=None):
        """Bind `widgets` so dragging them moves a borderless window.

        `win_getter` is the target window or a callable returning it (deferred so
        the popup can be resolved at drag time). `guard`, if given, is a callable
        that aborts the drag while truthy (e.g. during a resize).
        """
        off = {"x": 0, "y": 0}

        def _win():
            return win_getter() if callable(win_getter) else win_getter

        def start(e):
            if guard and guard():
                return
            off["x"], off["y"] = e.x, e.y

        def move(e):
            if guard and guard():
                return
            w = _win()
            if w:
                wx, wy = self._window_xy(w)
                nx = int(wx + e.x - off["x"])
                ny = int(wy + e.y - off["y"])
                w.geometry(f"+{nx}+{ny}")
                self._remember_window_xy(w, nx, ny)

        for _w in widgets:
            _w.bind("<Button-1>", start)
            _w.bind("<B1-Motion>", move)

    def _pill_button(self, parent, text_, cmd, *, bg, fg, hover_bg=None,
                     hover_fg=None, active_bg=None, active_fg=None,
                     font=("Microsoft YaHei UI", 10), padx=18, pady=6):
        """Create a flat pill-like button with consistent hover/active behavior."""
        hb = bg if hover_bg is None else hover_bg
        hf = fg if hover_fg is None else hover_fg
        ab = hb if active_bg is None else active_bg
        af = hf if active_fg is None else active_fg
        b = tk.Button(
            parent, text=text_, command=cmd, bg=bg, fg=fg,
            activebackground=ab, activeforeground=af,
            relief="flat", bd=0, highlightthickness=0,
            font=font, cursor="hand2", padx=padx, pady=pady,
        )
        b.bind("<Enter>", lambda e: b.config(bg=hb, fg=hf))
        b.bind("<Leave>", lambda e: b.config(bg=bg, fg=fg))
        return b

    def _make_tooltip(self, widget, text, delay_ms=400):
        """Attach a simple tooltip to a widget. Shows on enter after a delay,
        hides on leave. The tooltip is topmost so it never hides behind a
        borderless -topmost dialog (e.g. the settings window)."""
        tooltip_var = {"job": None, "tooltip": None}

        def show_tooltip(e):
            def do_show():
                try:
                    tt = tk.Toplevel(self.root)
                    tt.wm_overrideredirect(True)
                    tt.attributes("-topmost", True)
                    lbl = tk.Label(tt, text=text, bg="#2b2b2b", fg="#ffffff",
                                   font=("Microsoft YaHei UI", 9),
                                   wraplength=240, justify="left",
                                   padx=10, pady=6, relief="flat", bd=0)
                    lbl.pack()
                    tt.update_idletasks()
                    # Prefer to the right of the icon; if that would run off the
                    # right screen edge, flip to the left side instead.
                    tw = tt.winfo_width()
                    th = tt.winfo_height()
                    sw = widget.winfo_screenwidth()
                    x = widget.winfo_rootx() + widget.winfo_width() + 8
                    if x + tw > sw - 8:
                        x = widget.winfo_rootx() - tw - 8
                    y = widget.winfo_rooty() + (widget.winfo_height() - th) // 2
                    if y < 8:
                        y = 8
                    tt.wm_geometry(f"+{x}+{y}")
                    tt.lift()
                    tooltip_var["tooltip"] = tt
                except Exception:
                    pass

            if tooltip_var["job"]:
                try:
                    self.root.after_cancel(tooltip_var["job"])
                except Exception:
                    pass
            tooltip_var["job"] = self.root.after(delay_ms, do_show)

        def hide_tooltip(e):
            if tooltip_var["job"]:
                try:
                    self.root.after_cancel(tooltip_var["job"])
                except Exception:
                    pass
                tooltip_var["job"] = None
            if tooltip_var["tooltip"]:
                try:
                    tooltip_var["tooltip"].destroy()
                except Exception:
                    pass
                tooltip_var["tooltip"] = None

        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)

    def _mono_family(self):
        """Resolve a monospace family once (VSCode-ish preference order)."""
        cached = getattr(self, "_mono_family_cache", None)
        if cached is not None:
            return cached
        try:
            available = set(tkfont.families(self.root))
        except Exception:
            available = set()
        fam = "Courier New"
        for cand in ("Cascadia Code", "Cascadia Mono", "Consolas",
                     "JetBrains Mono", "Courier New"):
            if cand in available:
                fam = cand
                break
        self._mono_family_cache = fam
        return fam

    def _configure_rich_tags(self, text_widget):
        """Set up the tk.Text tags used by the markdown-lite renderer, coloured
        from the active theme. Heading fonts are only mildly larger so the
        dynamic-layout height math (which reads real reqheight) stays sane."""
        t = self.theme
        base = int(self.cfg[CFG.FONT_SIZE])
        ui = "Microsoft YaHei UI"
        mono = self._mono_family()
        text_widget.tag_configure(
            "rich_code", font=(mono, base), foreground=t["rich_code_fg"],
            background=t["rich_code_bg"])
        text_widget.tag_configure(
            "rich_codeblock", font=(mono, base), foreground=t["rich_code_fg"],
            background=t["rich_code_bg"], lmargin1=10, lmargin2=10)
        text_widget.tag_configure(
            "rich_bold", font=(ui, base, "bold"), foreground=t["rich_bold_fg"])
        text_widget.tag_configure("rich_italic", font=(ui, base, "italic"))
        text_widget.tag_configure(
            "rich_url", foreground=t["rich_url_fg"], underline=True)
        text_widget.tag_configure(
            "rich_bullet", foreground=t["rich_bullet_fg"], font=(ui, base, "bold"))
        text_widget.tag_configure(
            "rich_h1", font=(ui, base + 2, "bold"),
            foreground=t["rich_heading_fg"], spacing1=4, spacing3=2)
        text_widget.tag_configure(
            "rich_h2", font=(ui, base + 1, "bold"),
            foreground=t["rich_heading_fg"], spacing1=3, spacing3=2)
        text_widget.tag_configure(
            "rich_h3", font=(ui, base, "bold"),
            foreground=t["rich_heading_fg"], spacing1=2, spacing3=1)
        # Pygments token tags: mono font on the code-block background so a
        # highlighted block keeps the same card look, just multi-coloured.
        for name in ("keyword", "string", "comment", "number",
                     "func", "operator", "ident"):
            text_widget.tag_configure(
                "rich_tok_" + name, font=(mono, base),
                foreground=t.get("rich_tok_" + name, t["rich_code_fg"]),
                background=t["rich_code_bg"], lmargin1=10, lmargin2=10)

    def _fill_text(self, text_widget, message):
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        if getattr(text_widget, "_rich", False):
            hl = getattr(text_widget, "_rich_highlight", False)
            for chunk, tag in iter_rich_segments(message, highlight=hl):
                if tag:
                    text_widget.insert("end", chunk, tag)
                else:
                    text_widget.insert("end", chunk)
        else:
            text_widget.insert("1.0", message)
        text_widget.config(state="disabled")

    def _copy_result(self):
        if self.popup and getattr(self.popup, "_text", None):
            content = self.popup._text.get("1.0", "end-1c")
            setter = getattr(self.popup, "_copy_set", None)
            if setter is None:
                setter = lambda label: self.popup._copy_btn.config(text=label)
            if self._copy_text_content(content):
                setter(i18n.get("result.copied"))
                self.popup.after(
                    1200,
                    lambda: self.popup and getattr(self.popup, "_copy_set",
                        lambda l: self.popup._copy_btn.config(text=l))(
                            i18n.get("result.copy")))
            else:
                setter(i18n.get("result.copy_failed"))
                self.popup.after(
                    1200,
                    lambda: self.popup and getattr(self.popup, "_copy_set",
                        lambda l: self.popup._copy_btn.config(text=l))(
                            i18n.get("result.copy")))

    def _set_popup_text(self, message, resize=True, stream_grow=False,
                        stream_final=False, append=False):
        win = self.popup
        if not (win and getattr(win, "_text", None)):
            return
        if stream_grow:
            # The rebuild inside _size_popup_stream_grow (delete + reinsert)
            # snaps the view to the top. Preserve the user's reading position
            # if they scrolled down to follow along.
            prev_top = None
            if getattr(self._ss, "user_scrolled", False):
                try:
                    prev_top = win._text.index("@0,0")
                except Exception:
                    prev_top = None
            w, h = self._size_popup_stream_grow(win, message)
            self._record_v2_size(win, w, h)

            # Anchor (origin_x/origin_y) and monitor were fixed inside
            # _size_popup_stream_grow on the first frame; here we only read them
            # and clamp x to the current width. No min-height math lives here.
            left, top, right, bottom = self._ss.monitor_rect
            if self._is_centered_layout():
                # Centred: horizontally centre using the locked stream width
                # so the card stays centred as it grows down (width is fixed
                # after the first frame, so this x is stable frame-to-frame).
                nx = left + ((right - left) - w) // 2
                nx = max(left + 4, min(nx, right - w - 4))
            else:
                nx = max(left + 4, min(self._ss.origin_x, right - w - 4))
            ny = self._ss.origin_y

            if not self._ss.placed:
                # First on-screen frame: the window is still parked off-screen at
                # the fitted size (_make_popup's measurement left it at
                # req_w×h_fit). Lock the stream-grow size off-screen before moving
                # on-screen so only the position changes in the final geometry
                # call — same single-transition pattern as _reveal_rounded_window.
                try:
                    if int(win.winfo_x()) <= -3000:
                        win.geometry(f"{w}x{h}+-4000+-4000")
                        win.update_idletasks()
                except Exception:
                    pass
                win.geometry(f"{w}x{h}+{nx}+{ny}")
                self._remember_window_xy(win, nx, ny)
                self._ss.placed = True
            else:
                # Every later streamed frame ONLY grows the window: change the
                # size but not the position, so the top-left corner stays fixed
                # (Tk keeps it) and the card simply extends downward. Re-applying
                # the anchor here is what used to yank a window the user had
                # dragged mid-stream back to origin every frame ("跳回去"); a
                # size-only geometry lets the user drag/reposition freely while
                # the translation is still streaming in.
                win.geometry(f"{w}x{h}")
                cx, cy = self._window_xy(win)
                self._remember_window_xy(win, cx, cy)
            self._apply_window_rounding(win)
            if prev_top is not None:
                try:
                    win._text.yview(prev_top)
                except Exception:
                    pass
            return
        if not resize:
            self._fill_text(win._text, message)
            try:
                win._text.see("end-1c")
            except Exception:
                pass
            return
        # An append (e.g. a follow-up rewrite or code explanation) grows the
        # existing result. _size_popup rebuilds the Text, which snaps the view
        # to the top; capture the reader's top line beforehand and restore it so
        # they stay where they were instead of being yanked back up. The base
        # text is a prefix of the new message, so the captured line index stays
        # valid after the rebuild.
        prev_top = None
        if append:
            try:
                prev_top = win._text.index("@0,0")
            except Exception:
                prev_top = None
        w, h = self._size_popup(win, message)
        self._record_v2_size(win, w, h)
        cx, cy = self._window_xy(win)
        x, y = self._clamp_to_monitor(cx, cy, w, h, ref=(cx, cy))
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        self._apply_window_rounding(win)
        if prev_top is not None:
            try:
                win._text.yview(prev_top)
            except Exception:
                pass

    def _dismiss_loading_popup(self):
        """Close only the temporary loading hint; keep translation pipeline alive."""
        win = self.popup
        if not (win and getattr(win, "_hint_label", None)):
            return
        self._stop_animation()
        try:
            win.destroy()
        except Exception:
            pass
        if self.popup is win:
            self.popup = None
        log_perf("loading_dismissed", {"has_stream_data": bool(self._ss.accum)})

    def _user_close_popup(self):
        """User explicitly closed the result/error window (✕ or Esc).

        Closing must also invalidate the in-flight request. Otherwise a
        translation that is still streaming keeps its worker alive, and the next
        stream frame — or _stream_finalize when the response completes —
        re-creates the very window the user just dismissed. Bumping the job id
        makes every outstanding stream callback go stale (see _job_is_current),
        so nothing repaints after the user closes the popup."""
        self._begin_job()
        self._cancel_stream_flush()
        self._destroy_popup()

    def _destroy_popup(self):
        self._stop_animation()
        self._cancel_stream_flush()
        # Cancel any loading popup that hasn't appeared yet.
        pending = getattr(self, '_pending_loading_job', None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
            self._pending_loading_job = None
        self._ss.cols = 0
        self._ss.fixed_w = 0
        self._ss.max_h = 0
        self._ss.origin_x = None
        self._ss.origin_y = None
        self._ss.placed = False
        self._ss.monitor_rect = None
        self._ss.centered_ready = False
        self._ss.rendered = ""
        self._resize_mode = None
        self._resize_start = None
        if self.popup:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None

    def _reveal_rounded_window(self, win, w, h, x, y):
        self._apply_taskbar_identity(win)
        # Map and paint the rounded card OFF-SCREEN first, then move on-screen
        # in a single painted step. Deiconifying at the final position before
        # the canvas is drawn leaks one unpainted colour-key (near-black) frame
        # — the "black flash" seen just before the loading / result card shows.
        off = -4000
        win.geometry(f"{w}x{h}+{off}+{off}")
        win.deiconify()
        win.update_idletasks()
        win._round_redraw()
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        win.update_idletasks()
        win._round_redraw()
        # Use the same activation as re-focus: a plain lift() only reorders
        # within our own app and leaves a borderless window un-activated, so it
        # visually floats on top until clicked. _bring_to_front gives it a real
        # OS activation (brief topmost pulse, then released) so clicking another
        # app correctly sends this window behind.
        self._bring_to_front(win)
