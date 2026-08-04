"""cc_app_history — the translation-history window for CC Translate.

HistoryMixin holds the seven methods behind the tray's "History" entry: the
window opener (open_history/_open_history) and the builders/wirers it composes
(_build_history_titlebar, _build_history_views, _populate_history_list,
_render_history_detail, _wire_history_interactions).

Extracted verbatim from ``TranslatorApp`` in translator.pyw (bodies unchanged).
Like the other cc_app_* mixins this imports only leaf modules (tkinter, i18n,
cc_rich) and the shared foundation (cc_core) at import time, so there is no
import cycle: translator.pyw imports this module, never the other way round at
module load.

The history *data layer* (load_history/add_history/clear_history, HISTORY_PATH)
and the shared config-label helpers (get_history_filter_labels and the
history_entry_* / filter_history_entries helpers) deliberately stay in
translator.pyw — the unit tests mutate ``tr.HISTORY_PATH`` and patch ``tr.os`` /
``tr.log_error`` against those exact names, and the label helpers belong to a
shared family also used by Settings. The four window methods that need them do a
*lazy* ``import translator as _tr`` at call time (long after both modules have
finished loading, so it is a cheap sys.modules lookup with no cycle) and call
``_tr.load_history()`` etc. This keeps the data/label seam — and every test that
depends on it — exactly where it was.
"""

import tkinter as tk
from tkinter import ttk

import i18n

from cc_rich import iter_rich_segments
from cc_core import CFG, POPUP_CORNER_RADIUS, V2_CORNER_RADIUS

# The v2 skin (cc_ui_v2) is optional (needs Pillow). Import it guarded so a
# missing renderer never breaks the history window; every v2 path also gates on
# self._v2_popup_on(), which requires BOTH the UI_V2 flag AND a working
# renderer. Flag off -> the legacy window is built byte-for-byte.
try:
    import cc_ui_v2 as ccv2
except Exception:
    ccv2 = None


# Vivid, brand-harmonious accent per history entry kind — the coloured tag chip
# that anchors each card row in the v2 list (mirrors the POC's 译/词/码/图 chips).
_TAG_COLORS = {
    "text": (91, 124, 250),    # translation — blue
    "dict": (139, 108, 246),   # dictionary  — violet
    "code": (168, 92, 230),    # code        — purple
    "ocr": (99, 132, 241),     # screenshot  — indigo-blue
}


def _history_relative_time(ts):
    """A friendly relative timestamp ("刚刚" / "3 分钟前" / "2 小时前" / "昨天")
    from the stored ``"%Y-%m-%d %H:%M"`` string, falling back to a compact date
    for anything older than a day, or the raw string if it can't be parsed."""
    import time as _time
    from datetime import datetime
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except Exception:
        return ts
    now = datetime.now()
    delta = (now - dt).total_seconds()
    if delta < 0:
        delta = 0
    if delta < 60:
        return i18n.get("history.time_now")
    if delta < 3600:
        return i18n.get("history.time_min").format(n=int(delta // 60))
    if delta < 86400 and now.date() == dt.date():
        return i18n.get("history.time_hour").format(n=int(delta // 3600))
    if (now.date() - dt.date()).days == 1:
        return i18n.get("history.time_yesterday")
    # Older: compact date. Localised order is not critical for a list subtitle.
    return dt.strftime("%m-%d")


def _bake_round_panel(w, h, r, fill_rgb, border_rgb=None, stroke=1):
    """A rounded-rectangle RGBA image (anti-aliased corners) for a filled panel
    with an optional hairline border. These sit on the OPAQUE content card, so
    the anti-aliased corners blend against a known colour — no colour-key grit —
    giving the search box / filter pill / detail panel real rounded corners
    instead of the ugly square tk frames."""
    from PIL import Image, ImageDraw
    w = max(1, int(w))
    h = max(1, int(h))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    solid = Image.new("RGBA", (w, h), tuple(fill_rgb) + (255,))
    solid.putalpha(ccv2.rounded_mask(w, h, r))
    img.alpha_composite(solid)
    if border_rgb is not None:
        ring = Image.new("L", (w, h), 0)
        ImageDraw.Draw(ring).rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=r, outline=255, width=max(1, int(stroke)))
        st = Image.new("RGBA", (w, h), tuple(border_rgb) + (255,))
        st.putalpha(ring)
        img.alpha_composite(st)
    return img


class _ListboxHistoryList:
    """Adapter that lets the shared history wiring drive the legacy ``Listbox``
    through the same tiny interface the v2 card list exposes."""

    def __init__(self, app, listbox):
        self.app = app
        self.lb = listbox

    def widget(self):
        return self.lb

    def render(self, entries):
        self.lb.delete(0, "end")
        self.app._populate_history_list(self.lb, entries)

    def select(self, i):
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(i)
        self.lb.activate(i)
        self.lb.see(i)

    def selected_index(self):
        sel = self.lb.curselection()
        return sel[0] if sel else None

    def bind_select(self, cb):
        self.lb.bind("<<ListboxSelect>>", lambda _e: cb())


class _CardHistoryList:
    """The v2 history list: a scrollable column of comfortable two-line cards
    (coloured kind chip + title + relative time), mirroring the roomy POC. Drawn
    on a single Canvas — cards and chips are cached baked images (they sit on the
    opaque panel, so their anti-aliased corners blend cleanly, no colour-key
    grit), while the title/time are live Canvas text (crisp CJK, no per-row
    baking). Exposes render/select/selected_index/bind_select so the shared
    wiring treats it exactly like the legacy Listbox."""

    def __init__(self, app, parent, *, theme, font, scale):
        self.app = app
        self.theme = theme
        self.font = font
        self.scale = scale
        self.entries = []
        self.sel = None
        self._cb = None
        self._rows = []           # per-row canvas item ids
        self._laid_w = 0

        import tkinter.font as tkfont
        S = lambda v: ccv2.scaled(v, scale)
        self.S = S
        self.card_h = S(54)
        self.gap = S(9)
        self.pad = S(2)
        # NEGATIVE tk font sizes are DEVICE PIXELS, so the title/time render at a
        # size we control exactly — the old positive point sizes got scaled AGAIN
        # by the 1.5x display DPI, which is why the title ballooned. Title now
        # reads a touch larger than the time, both comfortably inside a 54pt card.
        self._title_font = tkfont.Font(family=font, size=-S(13))
        self._time_font = tkfont.Font(family=font, size=-S(11))

        panel = theme["bg"]
        wrap = tk.Frame(parent, bg=panel)
        # No scrollbar — the list scrolls by wheel only (the ttk bar read as an
        # ugly seam between the columns). The selected row is always scrolled
        # into view programmatically.
        cv = tk.Canvas(wrap, bg=panel, highlightthickness=0, bd=0, takefocus=0)
        cv.pack(side="left", fill="both", expand=True)
        self.wrap, self.cv = wrap, cv
        cv.bind("<Configure>", self._on_configure)
        cv.bind("<Button-1>", self._on_click)
        cv.bind("<MouseWheel>", self._on_wheel)
        self._card_normal = None
        self._card_sel = None
        self._chips = {}

    # -- public adapter interface ------------------------------------------
    def pack(self, **kw):
        self.wrap.pack(**kw)

    def render(self, entries):
        self.entries = list(entries)
        if self.sel is not None and self.sel >= len(self.entries):
            self.sel = None
        self._relayout(force=True)

    def select(self, i):
        if not self.entries:
            self.sel = None
            return
        i = max(0, min(int(i), len(self.entries) - 1))
        self.sel = i
        self._paint_selection()
        self._scroll_to(i)

    def selected_index(self):
        return self.sel

    def bind_select(self, cb):
        self._cb = cb

    # -- internals ---------------------------------------------------------
    def _on_configure(self, _e=None):
        self._relayout()

    def _on_wheel(self, e):
        self.cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _on_click(self, e):
        y = self.cv.canvasy(e.y)
        step = self.card_h + self.gap
        idx = int((y - self.gap) // step)
        if 0 <= idx < len(self.entries):
            self.select(idx)
            if self._cb:
                self._cb()

    def _card_images(self, w):
        """(normal, selected) baked rounded-card PhotoImages of width ``w``."""
        from PIL import Image, ImageDraw
        S = self.S
        h = self.card_h
        r = S(12)

        def bake(fill_hex, border_hex=None):
            fill = ccv2.hex_to_rgb(fill_hex)
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            solid = Image.new("RGBA", (w, h), tuple(fill) + (255,))
            solid.putalpha(ccv2.rounded_mask(w, h, r))
            img.alpha_composite(solid)
            if border_hex:
                ring = Image.new("L", (w, h), 0)
                ImageDraw.Draw(ring).rounded_rectangle(
                    (0, 0, w - 1, h - 1), radius=r, outline=255,
                    width=max(1, S(1.4)))
                stroke = Image.new("RGBA", (w, h),
                                   tuple(ccv2.hex_to_rgb(border_hex)) + (255,))
                stroke.putalpha(ring)
                img.alpha_composite(stroke)
            return ccv2.to_photo(img, master=self.app.root)

        normal = bake(self.theme["list_bg"])
        sel = bake(self.theme["list_sel"], self.theme["accent"])
        return normal, sel

    def _chip_image(self, kind):
        if kind in self._chips:
            return self._chips[kind]
        import translator as _tr
        from PIL import Image, ImageDraw
        S = self.S
        label = {
            "text": i18n.get("history.tag.text"),
            "dict": i18n.get("history.tag.dict"),
            "code": i18n.get("history.tag.code"),
            "ocr": i18n.get("history.tag.ocr"),
        }.get(kind, i18n.get("history.tag.text"))
        color = _TAG_COLORS.get(kind, ccv2.hex_to_rgb(self.theme["accent"]))
        f = ccv2.load_font("bold", 11, self.scale)
        probe = ImageDraw.Draw(Image.new("L", (4, 4)))
        b = probe.textbbox((0, 0), label, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        pad_x = S(11)
        w = tw + pad_x * 2
        h = S(26)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        solid = Image.new("RGBA", (w, h), tuple(color) + (255,))
        solid.putalpha(ccv2.rounded_mask(w, h, S(8)))
        img.alpha_composite(solid)
        d = ImageDraw.Draw(img)
        d.text(((w - tw) / 2 - b[0], (h - th) / 2 - b[1]), label, font=f,
               fill=(255, 255, 255, 255))
        photo = ccv2.to_photo(img, master=self.app.root)
        self._chips[kind] = photo
        return photo

    def _ellipsize(self, text, tkf, max_w):
        text = " ".join((text or "").split())
        if not text or tkf.measure(text) <= max_w:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if tkf.measure(text[:mid] + ell) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell

    def _relayout(self, force=False):
        import translator as _tr
        cv = self.cv
        w = cv.winfo_width()
        if w <= 1:
            return
        if not force and w == self._laid_w:
            return
        self._laid_w = w
        cv.delete("all")
        self._rows = []
        card_w = w - self.pad * 2
        self._card_normal, self._card_sel = self._card_images(card_w)
        S = self.S
        text_x = self.pad + S(15)  # chip left inside card
        # First card hugs y=0 so its top edge lines up with the detail panel's
        # top edge across the gutter (the cards' inter-row breathing room is the
        # gap ADDED AFTER each row, not before the first). Starting at self.gap
        # used to drop the whole list a row lower than the detail card.
        y = 0
        for idx, e in enumerate(self.entries):
            kind = _tr.history_entry_kind(e)
            chip = self._chip_image(kind)
            img = self._card_sel if idx == self.sel else self._card_normal
            bg_id = cv.create_image(self.pad, y, anchor="nw", image=img)
            chip_w = chip.width()
            cv.create_image(text_x, y + self.card_h // 2, anchor="w", image=chip)
            tx = text_x + chip_w + S(12)
            avail = card_w - (tx - self.pad) - S(15)
            title = _tr.history_entry_preview(e, limit=200)
            title = self._ellipsize(title, self._title_font, avail)
            when = _history_relative_time(e.get("ts", ""))
            cv.create_text(tx, y + S(18), anchor="w", text=title,
                           font=self._title_font, fill=self.theme["settings_fg"])
            cv.create_text(tx, y + S(37), anchor="w", text=when,
                           font=self._time_font, fill=self.theme["popup_hint"])
            self._rows.append({"bg": bg_id, "y": y})
            y += self.card_h + self.gap
        cv.configure(scrollregion=(0, 0, w, max(y, cv.winfo_height())))

    def _paint_selection(self):
        for idx, row in enumerate(self._rows):
            img = self._card_sel if idx == self.sel else self._card_normal
            self.cv.itemconfigure(row["bg"], image=img)

    def _scroll_to(self, i):
        if i >= len(self._rows):
            return
        cv = self.cv
        top = self._rows[i]["y"]
        bottom = top + self.card_h
        vh = cv.winfo_height()
        y0 = cv.canvasy(0)
        total = float(cv.cget("scrollregion").split()[-1] or 1)
        if top < y0:
            cv.yview_moveto(max(0, top - self.gap) / total)
        elif bottom > y0 + vh:
            cv.yview_moveto(max(0, bottom - vh + self.gap) / total)


class HistoryMixin:
    """Translation-history window (mixed into TranslatorApp)."""

    def _v2_history_theme(self):
        """A copy of ``self.theme`` with the specific colour keys the history
        builders read overridden with values derived from the cc_ui_v2 palette,
        so the (unchanged) builders render the v2 skin just by being handed a
        different colour map. Structural v2 differences (brand badge, gradient
        title, ghost close, soft-pill actions) are branched separately.

        Elevation is theme-aware: on the deep-navy dark card inset surfaces go a
        touch LIGHTER; on the near-white light card they go a touch DARKER, so
        the list column always reads as a distinct navigation pane."""
        pal = self._v2_palette()
        v2c = self._v2_tk_colors()
        is_dark = pal["is_dark"]
        panel = ccv2.hex_to_rgb(v2c["panel"])
        accent = ccv2.hex_to_rgb(v2c["accent"])
        ink = (255, 255, 255) if is_dark else (20, 30, 70)

        def mix(a, b, f):
            return tuple(int(round(a[i] * (1 - f) + b[i] * f)) for i in range(3))

        elev = mix(panel, ink, 0.09 if is_dark else 0.07)
        row_sel = mix(panel, accent, 0.30 if is_dark else 0.16)
        txt_sel = mix(panel, accent, 0.38 if is_dark else 0.20)
        btn_hov = mix(panel, ink, 0.12 if is_dark else 0.08)
        thumb = mix(panel, accent, 0.35 if is_dark else 0.28)
        thumb_hi = mix(panel, accent, 0.55 if is_dark else 0.45)
        H = ccv2.rgb_to_hex

        t = dict(self.theme)
        t.update({
            "settings_bg": v2c["panel"],
            "bg": v2c["panel"],
            "popup_border": v2c["border"],
            "accent": v2c["accent"],
            "popup_hint": v2c["hint"],
            "fg": H(pal["fg"]),
            "settings_fg": H(pal["fg"]),
            "list_bg": H(elev),
            "list_sel": H(row_sel),
            "sel_bg": H(txt_sel),
            "rich_heading_fg": v2c["accent"],
            "status_ok": H(pal["ok"]),
            "status_err": H(pal["err"]),
            "btn_active": H(btn_hov),
            "btn_close_active": H(pal["err"]),
            "scroll_thumb": H(thumb),
            "scroll_thumb_active": H(thumb_hi),
            "trough": v2c["panel"],
        })
        return t

    def _build_history_titlebar(self, card, win, *, bg, border, accent, hint,
                                font, v2=False, scale=1.0):
        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        if v2 and ccv2 is not None:
            bar.pack(fill="x", padx=24, pady=(20, 14))
        else:
            bar.pack(fill="x", padx=16, pady=(12, 8))
        title_text = i18n.get("history.title")
        drag_targets = [bar]

        if v2 and ccv2 is not None:
            # v2: a roomy brand header mirroring the POC — a larger app-mark tile,
            # the tri-colour gradient title with a calm subtitle stacked beneath
            # it, and a ghost close button. No hairline divider (the concept has
            # none); the generous padding does the separating.
            logo_img = self._v2_logo_image(30) or self._v2_badge_image(32)
            if logo_img:
                logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                    highlightthickness=0)
                logo_lbl.image = logo_img
                logo_lbl.pack(side="left", padx=(0, 12), anchor="center")
                drag_targets.append(logo_lbl)
            heading = tk.Frame(bar, bg=bg, bd=0, highlightthickness=0)
            heading.pack(side="left", anchor="center")
            drag_targets.append(heading)
            title_img = self._v2_photo(
                ("hist_title", title_text, round(scale, 2)),
                lambda: ccv2.gradient_text(
                    title_text, ccv2.load_font("bold", 15, scale)))
            if title_img is not None:
                title_lbl = tk.Label(heading, image=title_img, bg=bg, bd=0,
                                     highlightthickness=0)
                title_lbl.image = title_img
            else:
                title_lbl = tk.Label(heading, text=title_text, bg=bg, fg=accent,
                                     font=(font, 13, "bold"))
            title_lbl.pack(side="top", anchor="w")
            drag_targets.append(title_lbl)
            subtitle = tk.Label(heading, text=i18n.get("history.subtitle"),
                                bg=bg, fg=hint, font=(font, 9))
            subtitle.pack(side="top", anchor="w", pady=(2, 0))
            drag_targets.append(subtitle)
            close_btn = self._v2_ghost_button(
                bar, lambda: win.destroy(), icon="close", danger=True)
            close_btn.pack(side="right", anchor="n")
            self._make_draggable(tuple(drag_targets), win)
            return

        logo_img = self._logo_image(18)
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(0, 8))
            drag_targets.append(logo_lbl)
        title_lbl = tk.Label(bar, text=title_text, bg=bg,
                             fg=accent, font=(font, 11, "bold"))
        title_lbl.pack(side="left")
        drag_targets.append(title_lbl)
        close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                             font=(font, 11), cursor="hand2", padx=6)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: win.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(
            fg=self.theme["status_err"]))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
        self._make_draggable(tuple(drag_targets), win)
        tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

    def _build_history_views(self, card, *, width, bg, border, theme, font,
                             v2=False, scale=1.0):
        if v2 and ccv2 is not None:
            return self._build_history_views_v2(
                card, width=width, border=border, theme=theme, font=font,
                scale=scale)
        import translator as _tr
        # Bottom action bar — packed first so it always stays visible, with
        # themed flat buttons matching the rest of the app.
        tk.Frame(card, bg=border, height=1).pack(side="bottom", fill="x")
        bottom = tk.Frame(card, bg=bg)
        bottom.pack(side="bottom", fill="x")

        # Panes container fills everything between the title bar and buttons.
        panes = tk.Frame(card, bg=bg)
        panes.pack(side="top", fill="both", expand=True)

        # Left: entry list (~40% of the window). Right: detail fills the rest.
        list_w = max(150, int(width * 0.30))
        left = tk.Frame(panes, bg=bg, width=list_w)
        left.pack(side="left", fill="y", expand=False)
        left.pack_propagate(False)
        controls = tk.Frame(left, bg=bg)
        controls.pack(fill="x", padx=(12, 0), pady=(8, 4))
        history_filter_labels = _tr.get_history_filter_labels()
        filter_var = tk.StringVar(value=history_filter_labels["all"])
        filt = ttk.Combobox(
            controls, textvariable=filter_var, state="readonly", width=6,
            style="CC.TCombobox", font=(font, 9),
            values=list(history_filter_labels.values()))
        filt.pack(side="right")
        search_wrap = tk.Frame(
            controls, bg=theme["bg"], bd=0, highlightthickness=1,
            highlightbackground=border)
        search_wrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
        search_var = tk.StringVar()
        search = tk.Entry(
            search_wrap, textvariable=search_var,
            bg=theme["bg"], fg=theme["fg"], relief="flat", bd=0,
            insertbackground=theme["fg"], highlightthickness=0,
            font=(font, 9), width=12)
        search.pack(side="left", fill="x", expand=True, ipady=5, padx=(8, 0))
        search_icon = tk.Label(
            search_wrap, text="⌕", bg=theme["bg"], fg=theme["popup_hint"],
            font=("Segoe UI Symbol", 10), padx=8)
        search_icon.pack(side="right")
        listbox = tk.Listbox(
            left, bg=theme["list_bg"], fg=theme["settings_fg"],
            selectbackground=theme["list_sel"],
            selectforeground=theme["settings_fg"],
            relief="flat", highlightthickness=0, activestyle="none",
            font=(font, 10))
        listbox.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 8))
        lb_scroll = ttk.Scrollbar(
            left, orient="vertical", style="CC.Vertical.TScrollbar",
            command=listbox.yview)
        listbox.config(yscrollcommand=lb_scroll.set)
        lb_scroll.pack(side="left", fill="y", pady=(0, 8))

        right = tk.Frame(panes, bg=bg)
        right.pack(side="left", fill="both", expand=True)
        detail = tk.Text(
            right, bg=theme["bg"], fg=theme["fg"], wrap="word", relief="flat",
            padx=12, pady=10, font=(font, self.cfg[CFG.FONT_SIZE]),
            selectbackground=theme["sel_bg"], highlightthickness=0)
        detail.pack(fill="both", expand=True, padx=(8, 12), pady=8)
        # Reuse the main popup's markdown-lite renderer so history detail looks
        # consistent with the live result window.
        self._configure_rich_tags(detail)
        detail.tag_configure(
            "detail_head",
            font=("Microsoft YaHei UI", int(self.cfg[CFG.FONT_SIZE]), "bold"),
            foreground=theme["rich_heading_fg"], spacing1=2, spacing3=4)
        return (bottom, _ListboxHistoryList(self, listbox), detail, search_wrap,
                search, search_icon, search_var, filter_var)

    def _build_history_views_v2(self, card, *, width, border, theme, font,
                                scale):
        """The roomy POC-style history body: a rounded search field over a
        scrollbar-free card list on the left, an equal-height rounded filter pill
        over a rounded detail panel on the right, tops aligned across both
        columns. Returns the same tuple shape as the legacy builder, with a
        :class:`_CardHistoryList` adapter standing in for the Listbox."""
        import translator as _tr
        S = lambda v: ccv2.scaled(v, scale)
        panel = theme["bg"]

        # Action bar (bottom) — no hairline divider; the padding separates it.
        # Weighted DOWNWARD: more room above (so it's not crammed against the
        # history list) and less below (so it's not marooned far from the window
        # floor).
        bottom = tk.Frame(card, bg=panel)
        bottom.pack(side="bottom", fill="x", padx=S(20), pady=(S(18), S(10)))

        body = tk.Frame(card, bg=panel)
        body.pack(side="top", fill="both", expand=True, padx=S(20), pady=(0, S(4)))

        list_w = max(S(210), int(width * 0.40))
        left = tk.Frame(body, bg=panel, width=list_w)
        left.pack(side="left", fill="y", expand=False)
        left.pack_propagate(False)
        right = tk.Frame(body, bg=panel)
        right.pack(side="left", fill="both", expand=True, padx=(S(16), 0))

        # One shared row height for the search field and the filter pill, so the
        # card list (left) and the detail panel (right) start on the SAME line.
        row_h = S(38)
        gap_below = S(12)

        # -- left column: rounded search over the scrollbar-free card list --
        search_wrap, search, search_var, search_icon = self._v2_hist_search(
            left, theme=theme, scale=scale, font=font, height=row_h,
            pad_below=gap_below)
        hlist = _CardHistoryList(self, left, theme=theme, font=font, scale=scale)
        hlist.pack(side="top", fill="both", expand=True)

        # -- right column: rounded filter pill over the rounded detail panel --
        labels = _tr.get_history_filter_labels()
        filter_var, _filt = self._v2_hist_filter(
            right, theme=theme, scale=scale, font=font, height=row_h,
            labels=labels, pad_below=gap_below)
        detail = self._v2_hist_detail(right, theme=theme, scale=scale, font=font)
        return (bottom, hlist, detail, search_wrap, search, search_icon,
                search_var, filter_var)

    def _v2_hist_search(self, parent, *, theme, scale, font, height, pad_below):
        """A rounded search field: a baked rounded panel on a Canvas with a flat
        Entry and a search glyph placed inside. Returns
        ``(canvas, entry, var, icon)`` — the canvas doubles as ``search_wrap`` so
        the shared wiring's click-to-focus binding keeps working."""
        S = lambda v: ccv2.scaled(v, scale)
        panel = theme["bg"]
        field = theme["list_bg"]
        fill = ccv2.hex_to_rgb(field)
        brd = ccv2.hex_to_rgb(border) if (border := theme.get("popup_border")) \
            else ccv2.hex_to_rgb(theme["list_sel"])
        cv = tk.Canvas(parent, bg=panel, height=height, highlightthickness=0,
                       bd=0, takefocus=0)
        cv.pack(side="top", fill="x", pady=(0, pad_below))
        var = tk.StringVar()
        entry = tk.Entry(cv, textvariable=var, bg=field, fg=theme["fg"],
                         relief="flat", bd=0, insertbackground=theme["fg"],
                         highlightthickness=0, font=(font, 10))
        icon = tk.Label(cv, text="⌕", bg=field, fg=theme["popup_hint"],
                        font=("Segoe UI Symbol", 11))
        state = {"w": 0, "win": None, "iwin": None}

        def paint(_e=None):
            w = cv.winfo_width()
            if w <= 1 or (w == state["w"] and _e is not None):
                return
            state["w"] = w
            img = _bake_round_panel(w, height, S(11), fill, brd, max(1, S(1)))
            photo = ccv2.to_photo(img, master=cv)
            cv._bg = photo
            cv.delete("bg")
            cv.create_image(0, 0, anchor="nw", image=photo, tags="bg")
            cv.tag_lower("bg")
            if state["iwin"] is None:
                state["iwin"] = cv.create_window(
                    w - S(10), height // 2, anchor="e", window=icon)
            else:
                cv.coords(state["iwin"], w - S(10), height // 2)
            iw = icon.winfo_reqwidth() + S(10)
            tw = max(1, w - S(15) - iw)
            if state["win"] is None:
                state["win"] = cv.create_window(
                    S(15), height // 2, anchor="w", window=entry, width=tw)
            else:
                cv.coords(state["win"], S(15), height // 2)
                cv.itemconfig(state["win"], width=tw)

        cv.bind("<Configure>", paint, add="+")
        return cv, entry, var, icon

    def _v2_hist_filter(self, parent, *, theme, scale, font, height, labels,
                        pad_below):
        """A rounded filter pill: a baked rounded panel with the current label +
        a drawn caret; clicking posts a native menu of the filter options.
        Returns ``(var, canvas)``."""
        S = lambda v: ccv2.scaled(v, scale)
        panel = theme["bg"]
        field = theme["list_bg"]
        fill = ccv2.hex_to_rgb(field)
        brd = ccv2.hex_to_rgb(border) if (border := theme.get("popup_border")) \
            else ccv2.hex_to_rgb(theme["list_sel"])
        width = S(116)
        var = tk.StringVar(value=labels["all"])
        cv = tk.Canvas(parent, bg=panel, height=height, width=width,
                       highlightthickness=0, bd=0, takefocus=0)
        cv.pack(side="top", anchor="w", pady=(0, pad_below))
        img = _bake_round_panel(width, height, S(11), fill, brd, max(1, S(1)))
        photo = ccv2.to_photo(img, master=cv)
        cv._bg = photo
        cv.create_image(0, 0, anchor="nw", image=photo)
        lbl = tk.Label(cv, textvariable=var, bg=field, fg=theme["fg"],
                       font=(font, 10))
        cv.create_window(S(14), height // 2, anchor="w", window=lbl)
        # Draw the caret as a real filled triangle sized to MATCH the result
        # window's 操作 ▾ caret (soft_pill: cw=S(8) wide, ch=S(3) half-tall), so
        # the two dropdown affordances read as one design language — the old
        # Segoe "▾" glyph was a tiny, mismatched tofu-prone character.
        cw = S(8)
        ch = S(3)
        cx = width - S(13) - cw
        cy = height // 2
        cv.create_polygon(
            cx, cy - ch, cx + cw, cy - ch, cx + cw / 2, cy + ch,
            fill=theme["fg"], outline="")
        # A themed dropdown: tinted to the card (not the stark system-white menu),
        # with an accent-wash active row, so it belongs to the v2 skin.
        menu = tk.Menu(
            cv, tearoff=0, bg=field, fg=theme["fg"],
            activebackground=theme["list_sel"], activeforeground=theme["fg"],
            relief="flat", bd=0, activeborderwidth=0, font=(font, 10))
        for val in labels.values():
            menu.add_command(label=val, command=lambda v=val: var.set(v))

        def popup(_e=None):
            try:
                menu.tk_popup(cv.winfo_rootx(), cv.winfo_rooty() + height + S(4))
            finally:
                menu.grab_release()

        for wdg in (cv, lbl):
            wdg.bind("<Button-1>", popup)
        return var, cv

    def _v2_hist_detail(self, parent, *, theme, scale, font):
        """The floating rounded detail panel: a baked rounded card on a Canvas
        with the markdown-lite Text inset. Wheel-scrolls (no scrollbar). Returns
        the Text widget."""
        S = lambda v: ccv2.scaled(v, scale)
        panel = theme["bg"]
        field = theme["list_bg"]
        fill = ccv2.hex_to_rgb(field)
        brd = ccv2.hex_to_rgb(border) if (border := theme.get("popup_border")) \
            else ccv2.hex_to_rgb(theme["list_sel"])
        cv = tk.Canvas(parent, bg=panel, highlightthickness=0, bd=0, takefocus=0)
        cv.pack(side="top", fill="both", expand=True)
        detail = tk.Text(
            cv, bg=field, fg=theme["fg"], wrap="word", relief="flat", bd=0,
            padx=0, pady=0, font=(font, self.cfg[CFG.FONT_SIZE]),
            selectbackground=theme["sel_bg"], highlightthickness=0,
            cursor="arrow")
        self._configure_rich_tags(detail)
        detail.tag_configure(
            "detail_head",
            font=("Microsoft YaHei UI", int(self.cfg[CFG.FONT_SIZE]), "bold"),
            foreground=theme["rich_heading_fg"], spacing1=2, spacing3=6)
        state = {"w": 0, "h": 0, "win": None}

        def paint(_e=None):
            w = cv.winfo_width()
            h = cv.winfo_height()
            if w <= 1 or h <= 1 or (
                    w == state["w"] and h == state["h"] and _e is not None):
                return
            state["w"], state["h"] = w, h
            img = _bake_round_panel(w, h, S(14), fill, brd, max(1, S(1)))
            photo = ccv2.to_photo(img, master=cv)
            cv._bg = photo
            cv.delete("bg")
            cv.create_image(0, 0, anchor="nw", image=photo, tags="bg")
            cv.tag_lower("bg")
            pad = S(18)
            tw = max(1, w - 2 * pad)
            th = max(1, h - 2 * pad)
            if state["win"] is None:
                state["win"] = cv.create_window(
                    pad, pad, anchor="nw", window=detail, width=tw, height=th)
            else:
                cv.coords(state["win"], pad, pad)
                cv.itemconfig(state["win"], width=tw, height=th)

        cv.bind("<Configure>", paint, add="+")
        detail.bind(
            "<MouseWheel>",
            lambda e: (detail.yview_scroll(int(-1 * (e.delta / 120)), "units"),
                       "break")[1])
        return detail

    def _populate_history_list(self, listbox, entries):
        import translator as _tr
        for e in entries:
            listbox.insert(
                "end", f"[{_tr.history_entry_tag(e)}] {_tr.history_entry_preview(e)}")

    def _render_history_detail(self, detail, entry):
        detail.config(state="normal")
        detail.delete("1.0", "end")
        # Source stays literal (it may be code the user selected); the result is
        # rendered with the rich markdown-lite tags.
        detail.insert("end", f"{i18n.get('result.source_label')}\n", "detail_head")
        detail.insert("end", (entry.get("input", "") or "") + "\n\n")
        detail.insert("end", f"{i18n.get('result.output_label')}\n", "detail_head")
        for chunk, tag in iter_rich_segments(entry.get("output", "") or "",
                                             highlight=True):
            if tag:
                detail.insert("end", chunk, tag)
            else:
                detail.insert("end", chunk)
        detail.config(state="disabled")

    def _wire_history_interactions(self, win, hlist, detail, entries,
                                   bottom, theme, font, search_wrap, search,
                                   search_icon, search_var, filter_var,
                                   v2=False, scale=1.0):
        import translator as _tr
        state = {"all": list(entries), "shown": []}
        kind_by_label = {v: k for k, v in _tr.get_history_filter_labels().items()}
        icon_visible = {"on": True}
        default_border = theme["popup_border"]
        status = tk.Label(bottom, text="", bg=theme["settings_bg"],
                          fg=theme["popup_hint"], font=(font, 9))
        status.pack(side="left", padx=(0, 8), pady=(4, 12))

        def set_status(text_, colour=None):
            status.config(text=text_, fg=colour or theme["popup_hint"])

        def focus_search(_evt=None):
            try:
                search.focus_force()
            except Exception:
                try:
                    search.focus_set()
                except Exception:
                    pass

        def show_search_icon():
            if v2 or icon_visible["on"]:
                return
            icon_visible["on"] = True
            search_icon.pack(side="right")

        def hide_search_icon():
            if v2 or not icon_visible["on"]:
                return
            icon_visible["on"] = False
            search_icon.pack_forget()

        def is_search_widget(widget):
            while widget is not None:
                if widget in (search_wrap, search, search_icon):
                    return True
                widget = getattr(widget, "master", None)
            return False

        def sync_search_adornment():
            # v2's search field is a baked rounded canvas: the glyph is fixed in
            # place and the focus ring is part of the bake, so there's nothing to
            # toggle — the legacy pack/highlight dance would only fight the canvas.
            if v2:
                return
            try:
                focused = (win.focus_get() == search)
            except Exception:
                focused = False
            has_text = bool(search_var.get().strip())
            search_wrap.config(
                highlightbackground=theme["accent"] if focused else default_border)
            if focused or has_text:
                hide_search_icon()
            else:
                show_search_icon()

        def sync_search_ime():
            self._apply_ime_composition_font(search, font, 9)

        def on_search_focus_in(_evt=None):
            sync_search_adornment()
            win.after_idle(sync_search_ime)

        def on_search_focus_out(_evt=None):
            win.after_idle(sync_search_adornment)

        def on_window_click(evt=None):
            if evt is not None and is_search_widget(evt.widget):
                return
            if not search_var.get().strip():
                try:
                    evt.widget.focus_set()
                except Exception:
                    try:
                        win.focus_set()
                    except Exception:
                        pass
            win.after_idle(sync_search_adornment)

        def selected_entry():
            idx = hlist.selected_index()
            if idx is None:
                return None
            if idx >= len(state["shown"]):
                return None
            return state["shown"][idx]

        def show_detail(_evt=None):
            entry = selected_entry()
            if entry is None:
                detail.config(state="normal")
                detail.delete("1.0", "end")
                detail.insert("1.0", i18n.get("history.no_match"))
                detail.config(state="disabled")
                return
            self._render_history_detail(detail, entry)

        def refresh_list(*_args):
            current = selected_entry()
            shown = _tr.filter_history_entries(
                state["all"], search_var.get(),
                kind_by_label.get(filter_var.get(), "all"))
            state["shown"] = shown
            hlist.render(shown)
            if not shown:
                set_status(i18n.get("history.matches_zero"))
                show_detail()
                return
            idx = shown.index(current) if current in shown else 0
            hlist.select(idx)
            set_status(i18n.get("history.matches_count").format(
                shown=len(shown), total=len(state["all"])))
            show_detail()

        def do_clear():
            _tr.clear_history()
            state["all"].clear()
            refresh_list()
            set_status(i18n.get("history.cleared"), theme["status_ok"])

        def copy_output():
            entry = selected_entry()
            if entry is None:
                return
            if self._copy_text_content(entry.get("output", "")):
                set_status(i18n.get("history.copied_result"), theme["status_ok"])
            else:
                set_status(i18n.get("history.copy_failed"), theme["status_err"])

        def copy_bilingual():
            entry = selected_entry()
            if entry is None:
                return
            source = (entry.get("input") or "").strip()
            output = (entry.get("output") or "").strip()
            payload = output if not source else (
                f"{i18n.get('result.source_label')}:\n{source}\n\n"
                f"{i18n.get('result.output_label')}:\n{output}"
            )
            if self._copy_text_content(payload):
                set_status(i18n.get("history.copied_bilingual"), theme["status_ok"])
            else:
                set_status(i18n.get("history.copy_failed"), theme["status_err"])

        def rerun_entry():
            entry = selected_entry()
            if entry is None:
                return
            src = (entry.get("input") or "").strip()
            if not src:
                set_status(i18n.get("history.no_source"), theme["status_err"])
                return
            origin = "ocr" if _tr.history_entry_kind(entry) == "ocr" else "text"
            win.destroy()
            self._show_loading(src, origin=origin, use_cache=False)

        min_w = ccv2.scaled(58, scale) if (v2 and ccv2 is not None) else 0

        def hist_btn(text_, cmd, danger=False):
            if v2 and ccv2 is not None:
                # v2: soft translucent pills matching the result window's
                # 复制 / 操作 buttons, sized to a shared min width so the row
                # stays tidy. Destructive intent (清空) reads from its label +
                # the red status feedback, so all four share one calm style.
                return self._v2_soft_button(bottom, text_, cmd, min_w=min_w)
            hover = theme["btn_close_active"] if danger else theme["btn_active"]
            hover_fg = "#ffffff" if danger else theme["settings_fg"]
            return self._pill_button(
                bottom, text_, cmd,
                bg=theme["list_bg"], fg=theme["settings_fg"],
                hover_bg=hover, hover_fg=hover_fg,
                active_bg=hover, active_fg=hover_fg,
                font=(font, 9), padx=14, pady=6)

        hist_btn(i18n.get("history.clear"), do_clear, danger=True).pack(
            side="right", padx=(0, 16), pady=(4, 12))
        hist_btn(i18n.get("history.rerun"), rerun_entry).pack(
            side="right", padx=(0, 8), pady=(4, 12))
        hist_btn(i18n.get("history.copy_bilingual"), copy_bilingual).pack(
            side="right", padx=(0, 8), pady=(4, 12))
        hist_btn(i18n.get("history.copy_result"), copy_output).pack(
            side="right", padx=(0, 8), pady=(4, 12))

        hlist.bind_select(show_detail)
        win.bind("<ButtonPress-1>", on_window_click, add="+")
        search_wrap.bind("<Button-1>", focus_search, add="+")
        search_icon.bind("<Button-1>", focus_search, add="+")
        search.bind("<FocusIn>", on_search_focus_in)
        search.bind("<FocusOut>", on_search_focus_out, add="+")
        search_var.trace_add("write",
                             lambda *_args: (refresh_list(), sync_search_adornment()))
        filter_var.trace_add("write", refresh_list)
        sync_search_adornment()
        refresh_list()
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-f>", lambda e: (focus_search(), "break"))

    # ---------- History window ----------
    def open_history(self):
        self.root.after(0, self._open_history)

    def _open_history(self):
        import translator as _tr
        if self.history_win and tk.Toplevel.winfo_exists(self.history_win):
            self._bring_to_front(self.history_win)
            return

        FONT = "Microsoft YaHei UI"

        # v2 skin: when the UI_V2 flag is on AND the renderer is available, hand
        # the builders a palette-derived colour map (self._v2_history_theme) and
        # branch the structural bits (brand badge, gradient title, ghost close,
        # soft-pill actions). Legacy is byte-for-byte untouched otherwise.
        v2on = self._v2_popup_on()
        scale = self._ui_scale() if v2on else 1.0
        if v2on:
            t = self._v2_history_theme()
        else:
            t = self.theme
        bg = t["settings_bg"]
        border = t["popup_border"]
        accent = t["accent"]
        hint = t["popup_hint"]
        self._setup_form_style(theme=t)
        if v2on:
            # Re-tint the shared capsule scrollbar for the navy/light v2 card
            # (base setup used the legacy theme colours).
            style = ttk.Style(self.root)
            style.configure(
                "CC.Vertical.TScrollbar",
                background=t["scroll_thumb"], troughcolor=t["trough"],
                bordercolor=t["trough"])
            style.map(
                "CC.Vertical.TScrollbar",
                background=[("active", t["scroll_thumb_active"]),
                           ("pressed", t["scroll_thumb_active"])])

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.history_win = win

        win._v2 = v2on
        # Fixed-size card: its whole ring drags (never resizes).
        win._v2_resizable = False

        # A larger centred card than the result/settings popups, so the richer
        # history tools (search, filters, copy/rerun actions) still have room.
        w, h, x, y = self._history_box()
        _radius = V2_CORNER_RADIUS if v2on else POPUP_CORNER_RADIUS
        card = self._rounded_shell(win, _radius, bg, border)
        self._build_history_titlebar(
            card, win, bg=bg, border=border, accent=accent, hint=hint,
            font=FONT, v2=v2on, scale=scale)

        entries = _tr.load_history()
        (bottom, hlist, detail, search_wrap, search, search_icon,
         search_var, filter_var) = self._build_history_views(
            card, width=w, bg=bg, border=border, theme=t, font=FONT,
            v2=v2on, scale=scale)
        self._wire_history_interactions(
            win, hlist, detail, entries, bottom, t, FONT, search_wrap, search,
            search_icon, search_var, filter_var, v2=v2on, scale=scale)

        # ---- Reveal centred, staying above the (topmost) settings window ----
        self._reveal_rounded_window(win, w, h, x, y)
