"""cc_app_settings — Settings window + its image/form helpers for CC Translate.

SettingsMixin holds the 16 methods behind the tray's "Settings" entry: the
settings dialog itself (open_settings/_open_settings), the themed form helpers
(_settings_section/_field/_toggle_row/_toggle_row_with_action, _make_toggle,
_setup_form_style, _install_combo_chevron), and the shared image builders it
relies on (_logo_image, _emoji_image, _despeckle_key_color, _load_support_image,
_make_chevron_image, _make_help_icon_image, _help_badge_diameter).

Extracted verbatim from ``TranslatorApp`` in translator.pyw (bodies unchanged).
Like the other cc_app_* mixins this imports only leaf modules (tkinter, i18n,
win32util, cc_update) and the shared foundation (cc_core); it never imports
translator.pyw, so there is no import cycle. The themes / label maps / image-fit
helper and a few UI constants it relies on were moved into cc_core so they can be
imported directly here. ``save_config`` stays in translator.pyw (it belongs to
the config-IO family — CONFIG_PATH / Config / _atomic_write_json), so the one
place that persists config calls the ``self._save_config`` instance wrapper.
``self`` resolves at runtime against the assembled ``TranslatorApp`` instance, so
calls into other mixins and shared window helpers (``self._rounded_shell``,
``self._reveal_rounded_window`` …) keep working.
"""

import os

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import i18n

from win32util import get_monitor_rect
from cc_update import version_string, is_autostart_enabled, set_autostart
from cc_core import (
    CFG, DEFAULT_CONFIG, POPUP_CORNER_RADIUS, V2_CORNER_RADIUS,
    ICON_PATH, ICON_PATH_DARK, ICON_PATH_LIGHT,
    ROUND_KEY_COLOR, SUPPORT_IMAGE_PATH, SETTINGS_MIN_W, SETTINGS_COL_MIN_W,
    fit_box_size, LANGUAGE_LABELS,
    resolve_theme_name, resolve_theme,
    get_direction_labels, get_theme_labels, get_popup_layout_labels,
    get_ocr_engine_labels, get_tray_click_action_labels, get_model_labels,
    get_provider_labels, get_provider_model_labels, provider_model,
)


_SETTINGS_COMBO_MIN_WIDTH = 19
_SETTINGS_COMBO_MAX_WIDTH = 24
_SETTINGS_COMBO_CHROME_CHARS = 3


def _settings_combo_width(font, value_groups):
    values = [
        str(value)
        for group in value_groups
        for value in group
    ]
    if not values:
        return _SETTINGS_COMBO_MIN_WIDTH
    char_width = max(1, font.measure("0"))
    text_width = max(font.measure(value) for value in values)
    text_chars = (text_width + char_width - 1) // char_width
    return min(
        _SETTINGS_COMBO_MAX_WIDTH,
        max(
            _SETTINGS_COMBO_MIN_WIDTH,
            text_chars + _SETTINGS_COMBO_CHROME_CHARS,
        ),
    )


class SettingsMixin:
    """Settings window + its image/form helpers (mixed into TranslatorApp)."""

    # ---------- Settings window ----------
    def open_settings(self):
        self.root.after(0, self._open_settings)

    def _logo_image(self, px, theme_name=None):
        """Load the app logo as a PhotoImage sized ~px pt (DPI-scaled) for use in
        window title bars. Picks the tile that contrasts the window background
        (light theme -> blue cc-dark tile; dark theme -> white cc-light tile) so
        the badge stays crisp on either background. Cached by (theme, size);
        keeping the reference here also stops Tk from garbage-collecting it.
        Returns None if PIL/ImageTk or the icon files are unavailable."""
        if theme_name is None:
            theme_name = resolve_theme_name(self.cfg)
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            scale = 1.0
        size = max(12, round(px * scale))
        key = (theme_name, size)
        cache = getattr(self, "_logo_cache", None)
        if cache is None:
            cache = self._logo_cache = {}
        if key in cache:
            return cache[key]
        try:
            from PIL import Image, ImageTk
            path = ICON_PATH_DARK if theme_name == "light" else ICON_PATH_LIGHT
            if not os.path.exists(path):
                path = ICON_PATH
            with Image.open(path) as im:
                img = im.convert("RGBA").resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            cache[key] = photo
            return photo
        except Exception:
            return None

    def _emoji_image(self, char, px=14, bg_hex=None):
        """Render a single emoji as a FULL-COLOR PhotoImage using the system
        color emoji font (Segoe UI Emoji). Tk's own text renderer only draws
        the monochrome outline glyph of an emoji, so to get the colored ☕ we
        rasterize it with Pillow (embedded_color=True) and show it as an image.

        `px` is the target on-screen size in points (DPI-scaled here). If
        `bg_hex` is given the glyph is flattened onto that background (Tk
        labels can't show partial transparency cleanly on every theme).
        Returns None if PIL or the emoji font is unavailable (caller falls
        back to a plain text label).
        """
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            scale = 1.0
        size = max(12, round(px * scale))
        cache = getattr(self, "_emoji_cache", None)
        if cache is None:
            cache = self._emoji_cache = {}
        key = (char, size, bg_hex)
        if key in cache:
            return cache[key]
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
            font_path = os.path.join(
                os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguiemj.ttf")
            if not os.path.exists(font_path):
                return None
            # Segoe UI Emoji ships bitmap strikes rendered at 109px; request
            # that so embedded_color glyphs load, then downscale to UI size.
            font = ImageFont.truetype(font_path, 109)
            canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            try:
                draw.text((0, 0), char, font=font, embedded_color=True)
            except TypeError:
                draw.text((0, 0), char, font=font)
            bbox = canvas.getbbox()
            if bbox:
                canvas = canvas.crop(bbox)
            glyph = canvas.resize((size, size), Image.LANCZOS)
            if bg_hex:
                flat = Image.new("RGBA", glyph.size, bg_hex)
                flat.alpha_composite(glyph)
                glyph = flat.convert("RGB")
            photo = ImageTk.PhotoImage(glyph, master=self.root)
            cache[key] = photo
            return photo
        except Exception:
            return None

    @staticmethod
    def _despeckle_key_color(rgb_img, ImageChops=None):
        """Remap every pixel that exactly equals ROUND_KEY_COLOR to pure black.

        Rounded popups use ROUND_KEY_COLOR as the Win32 -transparentcolor key,
        so any pixel matching it is rendered transparent and leaks whatever is
        behind the window. Photographic / anti-aliased content (e.g. downscaled
        QR codes) can contain pixels that coincidentally hit that near-black
        key colour. Replacing them with (0,0,0) is visually indistinguishable
        but keeps them opaque. Returns the original image if the key colour is
        not near-black (nothing to do) or on any failure.
        """
        try:
            from PIL import Image, ImageChops
            key = ROUND_KEY_COLOR.lstrip("#")
            kr, kg, kb = (int(key[i:i + 2], 16) for i in (0, 2, 4))
            # Only near-black keys can collide with black QR modules; skip work
            # for distinctive keys (e.g. magenta) that never appear in content.
            if max(kr, kg, kb) > 16:
                return rgb_img
            key_img = Image.new("RGB", rgb_img.size, (kr, kg, kb))
            r, g, b = ImageChops.difference(rgb_img, key_img).split()
            # Per-channel difference summed: 0 only where all three channels
            # match the key exactly. add() saturates at 255 which is fine here.
            diff = ImageChops.add(ImageChops.add(r, g), b)
            # mask == 255 exactly where the pixel equals the key colour. Keep
            # it as an 8-bit ("L") mask and hand it straight to composite; a
            # "1"-mode conversion would dither and corrupt the exact mask.
            mask = diff.point(lambda v: 255 if v == 0 else 0)
            black = Image.new("RGB", rgb_img.size, (0, 0, 0))
            return Image.composite(black, rgb_img, mask)
        except Exception:
            return rgb_img

    def _load_support_image(self, max_w, max_h, *, bg_hex=None,
                            corner_radius=0):
        """Return (PhotoImage, width, height) for the donation QR image.

        The RGBA asset is flattened onto the window background colour so its
        transparent gutter blends into the dialog in both light and dark
        themes. It is shown at native size when it already fits
        max_w x max_h; otherwise it is downscaled ONCE with LANCZOS (a
        high-quality anti-aliasing filter) so the QR codes stay smooth and
        scannable. Any pixel that coincidentally equals the rounded-window
        transparency key (ROUND_KEY_COLOR) is remapped to pure black so it
        stays opaque instead of leaking the background. The on-disk asset is
        never modified.
        """
        if not os.path.exists(SUPPORT_IMAGE_PATH):
            return None, 0, 0
        cache = getattr(self, "_support_img_cache", None)
        if cache is None:
            cache = self._support_img_cache = {}
        # Flatten the transparent gutter onto the window's own background colour
        # so it blends in both light and dark themes (a hard-coded white fill
        # left an ugly white seam in dark mode).
        if bg_hex is None:
            try:
                bg_hex = self.theme["settings_bg"]
            except Exception:
                bg_hex = "#ffffff"
        try:
            h = bg_hex.lstrip("#")
            bg_rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            bg_rgb = (255, 255, 255)
        key = (int(max_w), int(max_h), bg_rgb, int(corner_radius))
        if key in cache:
            return cache[key]
        try:
            from PIL import Image, ImageTk
            with Image.open(SUPPORT_IMAGE_PATH) as im:
                src_w, src_h = im.size
                # The asset is RGBA with a transparent gutter between the two
                # payment panels; flatten onto the window background colour so
                # no source alpha reaches Tk and the gutter blends into the
                # dialog. The on-disk file is never touched.
                if im.mode in ("RGBA", "LA", "P"):
                    rgba = im.convert("RGBA")
                    flat = Image.new("RGBA", rgba.size, bg_rgb + (255,))
                    flat.alpha_composite(rgba)
                    base = flat.convert("RGB")
                else:
                    base = im.convert("RGB")
                fit_w, fit_h, scale = fit_box_size(src_w, src_h, max_w, max_h)
                if scale < 1.0:
                    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                    base = base.resize((fit_w, fit_h), resample)
                else:
                    fit_w, fit_h = src_w, src_h
                if corner_radius:
                    radius = max(
                        1, min(int(corner_radius), fit_w // 2, fit_h // 2))
                    mask = Image.new("L", (fit_w, fit_h), 0)
                    from PIL import ImageDraw
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, fit_w - 1, fit_h - 1),
                        radius=radius, fill=255)
                    backdrop = Image.new("RGB", (fit_w, fit_h), bg_rgb)
                    base = Image.composite(base, backdrop, mask)
                # The rounded popup uses ROUND_KEY_COLOR (#010101) as its Win32
                # transparent colour key, so ANY pixel that exactly equals that
                # colour is punched out and the desktop/background shows through.
                # LANCZOS anti-aliasing of the black QR modules produces many
                # near-black pixels, some of which land exactly on (1,1,1);
                # those turned into "speckles" that leaked the background colour
                # (white in light mode, red on a red desktop, etc.). Remap any
                # exact key-colour pixel to pure black (0,0,0) -- visually
                # identical but no longer the transparency key -- so the QR
                # stays a solid, opaque black on every background.
                base = self._despeckle_key_color(base)
                photo = ImageTk.PhotoImage(base, master=self.root)
            result = (photo, fit_w, fit_h)
            cache[key] = result
            return result
        except Exception:
            return None, 0, 0

    def _make_chevron_image(self, color_hex, scale):
        """Draw a thin, modern downward chevron as a PhotoImage for the combobox
        dropdown indicator. Supersampled then downscaled for smooth anti-aliased
        edges. Returns None if PIL/ImageTk is unavailable (caller falls back)."""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            return None
        try:
            h = color_hex.lstrip("#")
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            W = max(24, round(34 * scale))
            H = max(16, round(22 * scale))
            S = 3   # supersample factor for anti-aliasing
            img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            w = max(2, round(2.2 * scale)) * S
            cx = W * 0.42 * S              # shift left so a right margin remains
            half = round(6.2 * scale) * S
            top = H * 0.40 * S
            bot = H * 0.60 * S
            d.line([(cx - half, top), (cx, bot), (cx + half, top)],
                   fill=rgb, width=w, joint="curve")
            img = img.resize((W, H), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _make_help_icon_image(self, ring_hex, glyph_hex, bg_hex, diameter=16):
        """Draw a circular "?" help badge as a PhotoImage.

        ``diameter`` is the final on-screen pixel size. The caller sizes it from
        the adjacent label's real font metrics so the badge stays visually
        balanced with text across DPI settings. Supersampled for smooth edges
        and flattened onto bg_hex so no alpha reaches Tk. Returns None if PIL
        is unavailable (caller falls back to a text label)."""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
        except Exception:
            return None
        try:
            def _rgb(hx):
                hx = hx.lstrip("#")
                return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
            ring = _rgb(ring_hex)
            glyph = _rgb(glyph_hex)
            bg = _rgb(bg_hex)
            S = 4                      # supersample factor
            D = max(12, int(round(float(diameter))))
            size = D * S
            img = Image.new("RGB", (size, size), bg)
            d = ImageDraw.Draw(img)
            pad = max(1, round(0.06 * size))
            lw = max(2, round(0.075 * size))
            d.ellipse([pad, pad, size - pad - 1, size - pad - 1],
                      outline=ring, width=lw)
            # Center a "?" glyph. Try a truetype font for crisp shape, else use
            # the default bitmap font.
            txt = "?"
            font = None
            for name in (
                "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf",
                "segoeui.ttf", "arial.ttf", "calibri.ttf",
            ):
                try:
                    font = ImageFont.truetype(name, int(size * 0.78))
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()
            try:
                bbox = d.textbbox((0, 0), txt, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                tx = (size - tw) / 2 - bbox[0]
                ty = (size - th) / 2 - bbox[1]
            except Exception:
                tw, th = d.textsize(txt, font=font)
                tx = (size - tw) / 2
                ty = (size - th) / 2
            d.text((tx, ty), txt, fill=glyph, font=font)
            img = img.resize((D, D), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _help_badge_diameter(self, font_spec):
        """Size the help badge from the real label line-height so it tracks DPI
        but still sits a little smaller than the adjacent setting text."""
        try:
            line_px = tkfont.Font(root=self.root, font=font_spec).metrics(
                "linespace")
        except Exception:
            return 16
        return max(14, min(22, int(round(line_px * 0.78))))

    def _install_combo_chevron(self, style, hint, accent, scale):
        """Register a custom chevron image element and point the combobox layout
        at it. Elements can only be created once per name, so we cache per
        (colour, size). Returns True if the custom chevron is in use."""
        # Keep image references alive for the whole app lifetime, or Tk blanks
        # them once they're garbage collected.
        if not hasattr(self, "_chev_imgs"):
            self._chev_imgs = []
            self._chev_cache = {}
        key = (hint, accent, round(scale, 3))
        elem = self._chev_cache.get(key)
        if elem is None:
            normal = self._make_chevron_image(hint, scale)
            active = self._make_chevron_image(accent, scale)
            if normal is None or active is None:
                return False
            elem = f"CC.cbarrow{len(self._chev_cache)}"
            try:
                style.element_create(elem, "image", normal,
                                     ("active", active), ("focus", active),
                                     border=0, sticky="")
            except Exception:
                return False
            self._chev_imgs.extend([normal, active])
            self._chev_cache[key] = elem
        style.layout("CC.TCombobox", [
            ("Combobox.field", {"sticky": "nswe", "children": [
                (elem, {"side": "right", "sticky": ""}),
                ("Combobox.padding", {"sticky": "nswe", "children": [
                    ("Combobox.textarea", {"sticky": "nswe"})]})]})])
        return True

    def _setup_form_style(self, theme=None):
        """Flat, theme-aware styling for the settings comboboxes / spinboxes.
        Native ttk themes ignore colours, so we base these on 'clam' and set
        field/border colours from the active palette. The combobox uses a
        custom thin chevron indicator; the spinboxes drop their up/down arrows
        entirely (values are edited by typing).

        ``theme`` overrides the colour source (the v2 history/settings windows
        pass a palette-derived map); it defaults to the app's active theme."""
        t = theme or self.theme
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        field_bg = t["list_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        accent = t["accent"]
        hint = t["popup_hint"]
        sel = t["sel_bg"]

        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            scale = 1.0

        for name in ("CC.TCombobox", "CC.TSpinbox"):
            # clam's field border is composited from two colours: bordercolor
            # draws the outer rectangle on the top/left/bottom edges, and
            # lightcolor draws the right edge plus an inner bevel highlight on the
            # top/left. Because our chevron image element is packed against the
            # field's right edge, clam never draws bordercolor's right pixel
            # there, so bordercolor covers only three sides while lightcolor is
            # the *only* colour that reaches all four. Mapping bordercolor to the
            # accent therefore lights three sides fully but leaves the right edge
            # thin/dark, and stacking both colours makes the left render 2px while
            # the right stays 1px — the asymmetry the earlier attempts hit.
            #
            # Fix: draw the entire border with lightcolor alone (bordercolor kept
            # invisible at the field background). lightcolor renders a clean,
            # uniform 1px rectangle on all four sides, so both the resting grey
            # border and the accent hover/focus highlight wrap evenly. darkcolor
            # is unused by the field element but pinned to the background too.
            style.configure(
                name,
                fieldbackground=field_bg, background=field_bg,
                foreground=fg,
                bordercolor=field_bg, lightcolor=border, darkcolor=field_bg,
                relief="flat", borderwidth=1, padding=(12, 6, 8, 6),
            )
            style.map(
                name,
                fieldbackground=[("readonly", field_bg), ("disabled", field_bg)],
                foreground=[("disabled", hint)],
                lightcolor=[("focus", accent), ("hover", accent)],
            )

        # Modern chevron dropdown indicator (falls back to a scaled triangle if
        # PIL is unavailable, so the form still works everywhere).
        if not self._install_combo_chevron(style, hint, accent, scale):
            arrow = max(13, int(round(13 * scale)))
            style.configure("CC.TCombobox", arrowcolor=hint, arrowsize=arrow)
            style.map("CC.TCombobox", arrowcolor=[("active", accent)])

        # Strip the spinbox up/down arrows — leave a plain typeable field.
        style.layout("CC.TSpinbox", [
            ("Spinbox.field", {"sticky": "nswe", "children": [
                ("Spinbox.padding", {"sticky": "nswe", "children": [
                    ("Spinbox.textarea", {"sticky": "nswe"})]})]})])

        # Dropdown listbox colours and font (only settable via the option database).
        self.root.option_add("*TCombobox*Listbox.background", field_bg)
        self.root.option_add("*TCombobox*Listbox.foreground", fg)
        self.root.option_add("*TCombobox*Listbox.selectBackground", sel)
        self.root.option_add("*TCombobox*Listbox.selectForeground", fg)
        # A flat background-coloured border acts as uniform inner padding so the
        # item text isn't flush against the popup's left edge (a plain
        # borderWidth of 0 made the text hug the side). relief=flat keeps it an
        # invisible inset rather than a drawn border line.
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 10)
        self.root.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei UI} 10")

    def _make_toggle(self, parent, initial, bg, *, accessible_name=None,
                     enabled=True):
        """A modern pill toggle switch with .get() and .set(bool)."""
        t = self.theme
        accent = t["accent"]
        off = t["popup_border"]
        disabled_track = t["popup_hint"]
        knob = "#ffffff"
        W, H = 42, 24

        if accessible_name:
            try:
                from PIL import Image, ImageDraw, ImageTk

                def _toggle_image(on, is_enabled=True):
                    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)
                    track = (accent if on else off) if is_enabled else disabled_track
                    draw.rounded_rectangle(
                        (1, 1, W - 2, H - 2), radius=H // 2, fill=track)
                    x = W - 12 if on else 12
                    draw.ellipse(
                        (x - 8, 3, x + 8, H - 3),
                        fill=knob, outline=knob)
                    return ImageTk.PhotoImage(img, master=self.root)

                off_img = _toggle_image(False, enabled)
                on_img = _toggle_image(True, enabled)
                value = tk.BooleanVar(master=parent, value=bool(initial))
                if not enabled:
                    wrapper = tk.Frame(
                        parent, width=W, height=H, bg=bg, bd=0,
                        highlightthickness=0, takefocus=0, cursor="arrow")
                    wrapper.pack_propagate(False)
                    check = tk.Checkbutton(
                        wrapper, variable=value, text=accessible_name,
                        image=off_img, selectimage=on_img, compound="none",
                        indicatoron=False, bg=bg, activebackground=bg,
                        selectcolor=bg, relief="flat", offrelief="flat", bd=0,
                        highlightthickness=0, takefocus=0, cursor="arrow",
                        state="disabled")
                    check.place(x=0, y=0, width=W, height=H)
                    visual = tk.Label(
                        wrapper, image=on_img if value.get() else off_img,
                        bg=bg, bd=0, highlightthickness=0, takefocus=0)
                    visual.place(x=0, y=0, width=W, height=H)
                    wrapper._toggle_images = (off_img, on_img)
                    wrapper._accessible_control = check
                    wrapper.get = lambda: bool(value.get())

                    def _set_disabled(val):
                        value.set(bool(val))
                        visual.configure(image=on_img if value.get() else off_img)

                    wrapper.set = _set_disabled
                    wrapper.toggle = lambda _event=None: "break"
                    wrapper.enabled = False
                    return wrapper
                check = tk.Checkbutton(
                    parent, variable=value, text=accessible_name,
                    image=off_img, selectimage=on_img, compound="none",
                    indicatoron=False, bg=bg, activebackground=bg,
                    selectcolor=bg, relief="flat", offrelief="flat", bd=0,
                    highlightthickness=2, highlightbackground=bg,
                    highlightcolor=accent, takefocus=1 if enabled else 0,
                    cursor="hand2" if enabled else "arrow",
                    state="normal")
                check._toggle_images = (off_img, on_img)
                check.get = lambda: bool(value.get())
                check.set = lambda val: value.set(bool(val))
                check.toggle = lambda _event=None: (
                    check.invoke() if enabled else None, "break")[1]
                check.bind("<space>", check.toggle)
                check.bind("<Return>", check.toggle)
                check.enabled = bool(enabled)
                return check
            except Exception:
                pass

        c = tk.Canvas(
            parent, width=W, height=H, bg=bg, highlightthickness=0, bd=0,
            cursor="hand2" if enabled else "arrow",
            takefocus=1 if enabled else 0)
        st = {"on": bool(initial)}

        def draw():
            c.delete("all")
            track = (
                accent if st["on"] else off) if enabled else disabled_track
            # Pill = rectangle capped with two circles.
            c.create_oval(2, 2, 20, H - 2, fill=track, outline=track)
            c.create_oval(W - 20, 2, W - 2, H - 2, fill=track, outline=track)
            c.create_rectangle(11, 2, W - 11, H - 2, fill=track, outline=track)
            kx = W - 12 if st["on"] else 12
            c.create_oval(kx - 8, 3, kx + 8, H - 3, fill=knob, outline=knob)

        def toggle(_e=None):
            if enabled:
                st["on"] = not st["on"]
                draw()
            return "break"

        if enabled:
            c.bind("<Button-1>", toggle)
            c.bind("<space>", toggle)
            c.bind("<Return>", toggle)
        draw()
        c.get = lambda: st["on"]
        c.toggle = toggle
        c.enabled = bool(enabled)

        def _set(v):
            st["on"] = bool(v)
            draw()

        c.set = _set
        return c

    def _settings_section(self, body, row_state, text_, *, bg, accent, font):
        row = row_state["value"]
        lbl = tk.Label(body, text=text_, bg=bg, fg=accent,
                       font=(font, 9, "bold"))
        pady = (14, 6) if row else (0, 6)
        lbl.grid(row=row, column=0, columnspan=2, sticky="w", pady=pady)
        row_state["value"] = row + 1

    def _settings_field(self, body, row_state, text_, widget, *, bg, fg, font):
        row = row_state["value"]
        tk.Label(body, text=text_, bg=bg, fg=fg, font=(font, 10)).grid(
            row=row, column=0, sticky="w", pady=6)
        widget.grid(row=row, column=1, sticky="e", pady=6)
        row_state["value"] = row + 1

    def _settings_toggle_row(self, body, row_state, text_, initial, *,
                             bg, fg, font, help_text=None, help_ring=None,
                             help_glyph=None, enabled=True):
        row = row_state["value"]
        label_fg = fg if enabled else (help_ring or fg)
        if help_text:
            # Label + circular "?" help badge sit together in column 0 so the
            # icon follows the feature name (not the far-right switch).
            cell = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
            cell.grid(row=row, column=0, sticky="w", pady=8)
            label_font = (font, 10)
            tk.Label(cell, text=text_, bg=bg, fg=label_fg, font=label_font).pack(
                side="left")
            icon = self._make_help_icon_image(
                help_ring or fg, help_glyph or fg, bg,
                diameter=self._help_badge_diameter(label_font))
            if icon is not None:
                if not hasattr(self, "_help_icon_imgs"):
                    self._help_icon_imgs = []
                self._help_icon_imgs.append(icon)   # keep ref alive
                help_lbl = tk.Label(
                    cell, image=icon, text=help_text, compound="none",
                    bg=bg, bd=0, takefocus=1,
                    highlightthickness=2, highlightbackground=bg,
                    highlightcolor=help_ring or fg, cursor="hand2")
                help_lbl.image = icon
            else:
                help_lbl = tk.Label(
                    cell, text="(?)", bg=bg, fg=help_ring or fg,
                    font=(font, 10), cursor="hand2", takefocus=1,
                    highlightthickness=2, highlightbackground=bg,
                    highlightcolor=help_ring or fg)
            help_lbl.pack(side="left", padx=(6, 0))
            self._make_tooltip(help_lbl, help_text)
        else:
            tk.Label(body, text=text_, bg=bg, fg=label_fg, font=(font, 10)).grid(
                row=row, column=0, sticky="w", pady=8)
        sw = self._make_toggle(
            body, initial, bg, accessible_name=text_, enabled=enabled)
        sw.grid(row=row, column=1, sticky="e", pady=8)
        row_state["value"] = row + 1
        return sw

    def _settings_toggle_row_with_action(self, body, row_state, text_, initial,
                                         btn_text, btn_cmd, *, bg, fg, font,
                                         theme):
        """Like _settings_toggle_row, but with an inline action button."""
        row = row_state["value"]
        tk.Label(body, text=text_, bg=bg, fg=fg, font=(font, 10)).grid(
            row=row, column=0, sticky="w", pady=8)
        cell = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        cell.grid(row=row, column=1, sticky="e", pady=8)
        self._pill_button(
            cell, btn_text, btn_cmd,
            bg=theme["list_bg"], fg=fg,
            hover_bg=theme["btn_active"], hover_fg=fg,
            active_bg=theme["list_sel"], active_fg=fg,
            font=(font, 9), padx=14, pady=3).pack(side="left", padx=(0, 12))
        sw = self._make_toggle(cell, initial, bg)
        sw.pack(side="left")
        row_state["value"] = row + 1
        return sw

    def _open_settings(self):
        if self.settings_win and tk.Toplevel.winfo_exists(self.settings_win):
            self._bring_to_front(self.settings_win)
            return

        t = self.theme
        v2on = self._v2_popup_on()
        scale = self._ui_scale() if v2on else 1.0
        if v2on:
            t = self._v2_window_theme()
        bg = t["settings_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        self._setup_form_style(theme=t)
        direction_labels = get_direction_labels()
        theme_labels = get_theme_labels()
        layout_labels = get_popup_layout_labels()
        ocr_engine_labels = get_ocr_engine_labels()
        tray_click_labels = get_tray_click_action_labels()
        provider_labels = get_provider_labels()
        current_provider = self.cfg.get(
            CFG.MODEL_PROVIDER, DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
        model_labels = get_provider_model_labels(current_provider)

        win = tk.Toplevel(self.root)
        win.withdraw()   # reveal at final geometry (no flash/jump)
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.settings_win = win

        # v2 skin: hand the (unchanged) form builders a palette-derived colour
        # map and swap to the frosted rounded shell + brand header. The window is
        # a fixed-size card, so its whole ring drags (never resizes).
        win._v2 = v2on
        win._v2_resizable = False

        FONT = "Microsoft YaHei UI"
        combo_font = tkfont.Font(root=self.root, family=FONT, size=10)
        combo_width = _settings_combo_width(combo_font, (
            provider_labels.values(),
            get_provider_model_labels("codex_cli").values(),
            get_provider_model_labels("claude_cli").values(),
            direction_labels.values(),
            ocr_engine_labels.values(),
            theme_labels.values(),
            layout_labels.values(),
            LANGUAGE_LABELS.values(),
            tray_click_labels.values(),
        ))
        radius = V2_CORNER_RADIUS if v2on else POPUP_CORNER_RADIUS
        outer = self._rounded_shell(win, radius, bg, border)

        # ---- Title bar (draggable, with logo + close button) ----
        if v2on:
            # A roomy brand header shared with the history window: app-mark tile,
            # gradient title, ghost close. No subtitle (a settings screen needs
            # no tagline) and no hairline divider — the generous padding does the
            # separating.
            bar = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
            bar.pack(fill="x", padx=44, pady=(20, 14))
            self._v2_brand_header(
                bar, win, title=i18n.get("settings.title"),
                subtitle=None,
                bg=bg, hint=hint, accent=accent, font=FONT, scale=scale,
                cache_tag="settings_title")
        else:
            bar = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
            bar.pack(fill="x", padx=16, pady=(12, 8))
            logo_img = self._logo_image(18)
            drag_targets = [bar]
            if logo_img:
                logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                    highlightthickness=0)
                logo_lbl.image = logo_img
                logo_lbl.pack(side="left", padx=(0, 8))
                drag_targets.append(logo_lbl)
            title_lbl = tk.Label(bar, text=i18n.get("settings.title"), bg=bg,
                                 fg=accent, font=(FONT, 11, "bold"))
            title_lbl.pack(side="left")
            drag_targets.append(title_lbl)
            close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                                 font=(FONT, 11), cursor="hand2", padx=6)
            close_btn.pack(side="right")
            close_btn.bind("<Button-1>", lambda e: win.destroy())
            close_btn.bind("<Enter>",
                           lambda e: close_btn.config(fg=t["status_err"]))
            close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))

            # Drag the bar (but not the close button) to move the window.
            self._make_draggable(tuple(drag_targets), win)

            tk.Frame(outer, bg=border, height=1).pack(fill="x", padx=16)

        body = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=44, pady=(14, 6))

        # Two columns side by side so the panel stays short instead of one long
        # vertical strip. Each column is an independent label|widget grid with
        # its own row counter; sections are split to keep the columns roughly
        # the same height. The section code below is unchanged — we just alias
        # `body`/`row_state` to the active column before each group.
        left_col = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 30))
        # Weighted spacer columns on either side of the divider soak up any extra
        # window width (e.g. the room reserved for the update button) evenly, so
        # the two columns sit against the left/right edges with the divider
        # centred between them instead of leaving a dead gap on the right.
        body.grid_columnconfigure(0, minsize=SETTINGS_COL_MIN_W,
                                  uniform="settings_cols")
        body.grid_columnconfigure(1, weight=1)
        tk.Frame(body, bg=border, width=1).grid(row=0, column=2, sticky="ns")
        body.grid_columnconfigure(3, weight=1)
        body.grid_columnconfigure(4, minsize=SETTINGS_COL_MIN_W,
                                  uniform="settings_cols")
        right_col = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        right_col.grid(row=0, column=4, sticky="nsew", padx=(30, 0))
        for _col in (left_col, right_col):
            _col.grid_columnconfigure(0, weight=1)
            _col.grid_columnconfigure(1, minsize=140)
        left_state = {"value": 0}
        right_state = {"value": 0}

        # ----- Left column -----
        body = left_col
        row_state = left_state
        # ---- Section: 翻译 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.translate_section"),
            bg=bg, accent=accent, font=FONT)
        provider_var = tk.StringVar(
            value=provider_labels.get(current_provider, current_provider))
        provider_combo = ttk.Combobox(
            body, textvariable=provider_var, state="readonly",
            width=combo_width,
            style="CC.TCombobox", font=(FONT, 10),
            values=list(provider_labels.values()))
        self._settings_field(
            body, row_state, i18n.get("settings.label.model_provider"),
            provider_combo, bg=bg, fg=fg, font=FONT)

        model_var = tk.StringVar(
            value=model_labels.get(
                provider_model(self.cfg, current_provider),
                provider_model(self.cfg, current_provider)))
        model_combo = ttk.Combobox(
            body, textvariable=model_var, state="readonly",
            width=combo_width,
            style="CC.TCombobox", font=(FONT, 10),
            values=list(model_labels.values()))
        self._settings_field(
            body, row_state, i18n.get("settings.label.translate_model"),
            model_combo,
            bg=bg, fg=fg, font=FONT)

        label_to_provider = {v: k for k, v in provider_labels.items()}

        def refresh_model_choices(_event=None):
            provider_id = label_to_provider.get(
                provider_var.get(), DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
            labels = get_provider_model_labels(provider_id)
            model_combo.config(values=list(labels.values()))
            selected = provider_model(self.cfg, provider_id)
            if selected not in labels:
                selected = (DEFAULT_CONFIG[CFG.CODEX_MODEL]
                            if provider_id == "codex_cli"
                            else DEFAULT_CONFIG[CFG.CLAUDE_MODEL])
            model_var.set(labels[selected])

        def on_provider_selected(_event=None):
            refresh_model_choices()

        provider_combo.bind("<<ComboboxSelected>>", on_provider_selected)

        dir_var = tk.StringVar(
            value=direction_labels.get(self.cfg[CFG.DIRECTION],
                                       direction_labels["auto"]))
        self._settings_field(
            body, row_state, i18n.get("settings.label.translate_direction"),
            ttk.Combobox(
                body, textvariable=dir_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(direction_labels.values())),
            bg=bg, fg=fg, font=FONT)

        # ---- Section: 截图翻译 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.screenshot_section"),
            bg=bg, accent=accent, font=FONT)
        ocr_engine_var = tk.StringVar(
            value=ocr_engine_labels.get(
                self.cfg.get(CFG.OCR_ENGINE, "claude"),
                ocr_engine_labels["claude"]))
        self._settings_field(
            body, row_state, i18n.get("settings.label.ocr_engine"),
            ttk.Combobox(
                body, textvariable=ocr_engine_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(ocr_engine_labels.values())),
            bg=bg, fg=fg, font=FONT)
        ocr_hotkey_sw = self._settings_toggle_row(
            body, row_state,
            i18n.get("settings.label.ocr_hotkey"),
            self.cfg.get(CFG.OCR_HOTKEY_ENABLED, True),
            bg=bg, fg=fg, font=FONT)

        # ---- Section: 外观 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.appearance_section"),
            bg=bg, accent=accent, font=FONT)
        theme_var = tk.StringVar(
            value=theme_labels.get(self.cfg.get(CFG.THEME, "system"),
                                   theme_labels["system"]))
        self._settings_field(
            body, row_state, i18n.get("settings.label.theme_field"),
            ttk.Combobox(
                body, textvariable=theme_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(theme_labels.values())),
            bg=bg, fg=fg, font=FONT)

        layout_var = tk.StringVar(
            value=layout_labels.get(
                self.cfg.get(CFG.POPUP_LAYOUT, "dynamic"),
                layout_labels["dynamic"]))
        self._settings_field(
            body, row_state, i18n.get("settings.label.popup_layout"),
            ttk.Combobox(
                body, textvariable=layout_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(layout_labels.values())),
            bg=bg, fg=fg, font=FONT)

        font_var = tk.IntVar(value=self.cfg[CFG.FONT_SIZE])
        self._settings_field(
            body, row_state, i18n.get("settings.label.font_size"),
            ttk.Spinbox(
                body, textvariable=font_var, from_=9, to=24, increment=1,
                width=10, style="CC.TSpinbox", font=(FONT, 10)),
            bg=bg, fg=fg, font=FONT)

        lang_var = tk.StringVar(
            value=LANGUAGE_LABELS.get(self.cfg.get(CFG.LANGUAGE), "English"))
        self._settings_field(
            body, row_state, i18n.get("settings.label.language_field"),
            ttk.Combobox(
                body, textvariable=lang_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(LANGUAGE_LABELS.values())),
            bg=bg, fg=fg, font=FONT)

        # ----- Right column -----
        body = right_col
        row_state = right_state
        # ---- Section: 行为 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.behavior_section"),
            bg=bg, accent=accent, font=FONT)
        gap_var = tk.DoubleVar(value=self.cfg[CFG.DOUBLE_PRESS_WINDOW])
        self._settings_field(
            body, row_state, i18n.get("settings.label.double_press_window"),
            ttk.Spinbox(
                body, textvariable=gap_var, from_=0.2, to=1.5, increment=0.1,
                width=10, style="CC.TSpinbox", format="%.1f",
                font=(FONT, 10)),
            bg=bg, fg=fg, font=FONT)

        tray_click_var = tk.StringVar(
            value=tray_click_labels.get(
                self.cfg.get(CFG.TRAY_CLICK_ACTION, "settings"),
                tray_click_labels["settings"]))

        max_var = tk.IntVar(value=self.cfg[CFG.MAX_CHARS])
        self._settings_field(
            body, row_state, i18n.get("settings.label.max_chars"),
            ttk.Spinbox(
                body, textvariable=max_var, from_=500, to=20000, increment=500,
                width=10, style="CC.TSpinbox", font=(FONT, 10)),
            bg=bg, fg=fg, font=FONT)

        # ---- Section: 系统 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.system_section"),
            bg=bg, accent=accent, font=FONT)

        history_sw = self._settings_toggle_row_with_action(
            body, row_state,
            i18n.get("settings.label.history_enabled"),
            self.cfg.get(CFG.HISTORY_ENABLED, True),
            i18n.get("settings.label.open_history"), self._open_history,
            bg=bg, fg=fg, font=FONT, theme=t)
        # History count lives right under the "记录历史" toggle so both
        # history-related settings sit together.
        hist_limit_var = tk.IntVar(value=self.cfg.get(CFG.HISTORY_LIMIT, 100))
        self._settings_field(
            body, row_state, i18n.get("settings.label.history_limit"),
            ttk.Spinbox(
                body, textvariable=hist_limit_var, from_=20, to=500,
                increment=20, width=10, style="CC.TSpinbox",
                font=(FONT, 10)),
            bg=bg, fg=fg, font=FONT)
        autostart_sw = self._settings_toggle_row(
            body, row_state,
            i18n.get("settings.label.auto_start_boot"), is_autostart_enabled(),
            bg=bg, fg=fg, font=FONT)
        self._settings_field(
            body, row_state, i18n.get("settings.label.tray_click_action"),
            ttk.Combobox(
                body, textvariable=tray_click_var, state="readonly",
                width=combo_width,
                style="CC.TCombobox", font=(FONT, 10),
                values=list(tray_click_labels.values())),
            bg=bg, fg=fg, font=FONT)

        # ---- Section: 更新 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.update_section"),
            bg=bg, accent=accent, font=FONT)
        # Inline status line + an "更新并重启" button that only appears once a
        # newer version has been found (checking never updates on its own).
        upd_status = tk.Label(body, text="", bg=bg, fg=hint, font=(FONT, 9))
        upd_apply_btn = tk.Button(
            body, text=i18n.get("settings.update_and_restart"),
            bg=accent, fg="#ffffff",
            activebackground=accent, activeforeground="#ffffff",
            relief="flat", bd=0, highlightthickness=0,
            font=(FONT, 9), cursor="hand2", padx=14, pady=4)

        def _upd_show(msg, kind):
            colour = {"ok": t["status_ok"], "err": t["status_err"],
                      "avail": accent}.get(kind, hint)
            upd_status.config(text=msg, fg=colour)
            if kind == "avail":
                upd_apply_btn.grid()      # reveal the explicit update button
            else:
                upd_apply_btn.grid_remove()

        def on_apply_update_click():
            upd_apply_btn.grid_remove()
            upd_status.config(text=i18n.get("update.updating"), fg=hint)
            self._begin_update(check_only=False, on_status=_upd_show)

        upd_apply_btn.config(command=on_apply_update_click)

        def on_check_update_click():
            upd_apply_btn.grid_remove()
            upd_status.config(text=i18n.get("update.checking"), fg=hint)
            # Check only — if an update exists we surface a button, not an
            # automatic restart.
            self._begin_update(check_only=True, on_status=_upd_show)

        # Expose the check so the tray "检查更新" entry can route through here,
        # converging both entry points on this one UI.
        self._settings_check = on_check_update_click

        version_cell = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        tk.Label(
            version_cell, text=version_string(), bg=bg, fg=hint,
            font=(FONT, 10)).pack(side="left", padx=(0, 12))
        self._pill_button(
            version_cell, i18n.get("settings.label.check_update_action"),
            on_check_update_click,
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 9), padx=14, pady=3).pack(side="right")
        self._settings_field(
            body, row_state, i18n.get("settings.label.current_version"),
            version_cell, bg=bg, fg=fg, font=FONT)

        auto_update_sw = self._settings_toggle_row(
            body, row_state,
            i18n.get("settings.label.auto_update"),
            self.cfg.get(CFG.AUTO_UPDATE_ENABLED, True),
            bg=bg, fg=fg, font=FONT)
        upd_row = row_state["value"]
        upd_status.grid(row=upd_row, column=0, sticky="w", pady=(0, 4))
        upd_apply_btn.grid(row=upd_row, column=1, sticky="e", pady=(0, 4))
        # Permanently reserve the update row's footprint so revealing any real
        # status text or the "更新并重启" button never reflows the right column or
        # shifts the divider. Measure the widest real status we can show here,
        # with/without the button as appropriate, pin col 0's min width to that,
        # then reset to the idle (empty / hidden) look.
        status_w = 0
        status_samples = [
            (i18n.get("update.found_version").format(version="4.0.9999"), True),
            (i18n.get("update.no_update"), False),
        ]
        for sample_text, show_btn in status_samples:
            upd_status.config(text=sample_text)
            if show_btn:
                upd_apply_btn.grid()
            else:
                upd_apply_btn.grid_remove()
            right_col.update_idletasks()
            status_w = max(status_w, upd_status.winfo_reqwidth())
        right_col.grid_columnconfigure(0, minsize=status_w)
        # +4 accounts for the row's pady=(0, 4) bottom padding, which the grid
        # adds on top of the button's own height.
        right_col.grid_rowconfigure(
            upd_row, minsize=upd_apply_btn.winfo_reqheight() + 4)
        upd_status.config(text="")
        upd_apply_btn.grid_remove()       # hidden until a version is found
        row_state["value"] += 1

        # ---- Section: 实验室 ----
        self._settings_section(
            body, row_state, i18n.get("settings.label.labs_section"),
            bg=bg, accent=accent, font=FONT)
        summary_sw = self._settings_toggle_row(
            body, row_state,
            i18n.get("settings.label.summary_enabled"),
            self.cfg.get(
                CFG.SUMMARY_ENABLED, DEFAULT_CONFIG[CFG.SUMMARY_ENABLED]),
            bg=bg, fg=fg, font=FONT,
            help_text=i18n.get("settings.label.summary_help"),
            help_ring=hint, help_glyph=hint)
        clip_protect_sw = self._settings_toggle_row(
            body, row_state,
            i18n.get("settings.label.clipboard_protection"),
            self.cfg.get(
                CFG.CLIPBOARD_PROTECTION_ENABLED,
                DEFAULT_CONFIG[CFG.CLIPBOARD_PROTECTION_ENABLED]),
            bg=bg, fg=fg, font=FONT,
            help_text=i18n.get("settings.label.clipboard_protection_help"),
            help_ring=hint, help_glyph=hint)

        # ---- Footer: status + action buttons ----
        # v2 drops the hairline divider (the padding separates the row); legacy
        # keeps its thin rule above the footer.
        if not v2on:
            tk.Frame(outer, bg=border, height=1).pack(fill="x", padx=16,
                                                      pady=(4, 0))
        footer = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
        footer.pack(fill="x", padx=44, pady=(10, 14))

        # Uninstall sits far left, deliberately separated from the save/close
        # actions on the right so it can't be hit by accident.
        self._pill_button(
            footer, i18n.get("settings.label.uninstall"),
            lambda: self._confirm_and_uninstall(),
            bg=bg, fg=hint,
            hover_bg=t["list_bg"], hover_fg=t["status_err"],
            active_bg=t["list_sel"], active_fg=t["status_err"],
            font=(FONT, 9), padx=10, pady=6).pack(side="left")

        status = tk.Label(footer, text="", bg=bg, fg=t["status_ok"],
                          font=(FONT, 9))
        status.pack(side="left", padx=(12, 0))

        label_to_dir = {v: k for k, v in direction_labels.items()}
        label_to_theme = {v: k for k, v in theme_labels.items()}
        label_to_layout = {v: k for k, v in layout_labels.items()}
        label_to_ocr_engine = {v: k for k, v in ocr_engine_labels.items()}
        label_to_tray_click = {v: k for k, v in tray_click_labels.items()}
        label_to_lang = {v: k for k, v in LANGUAGE_LABELS.items()}
        restore_defaults_pending = False

        def apply_settings():
            nonlocal restore_defaults_pending
            try:
                prev_warm_key = self._warm_key()
                previous_provider = self.cfg.get(
                    CFG.MODEL_PROVIDER, DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
                new_provider = label_to_provider.get(
                    provider_var.get(), provider_var.get())
                active_model_labels = get_provider_model_labels(new_provider)
                label_to_model = {
                    value: key for key, value in active_model_labels.items()}
                new_model = label_to_model.get(
                    model_var.get(), model_var.get())
                self.cfg[CFG.MODEL_PROVIDER] = new_provider
                if new_provider == "codex_cli":
                    self.cfg[CFG.CODEX_MODEL] = new_model
                else:
                    self.cfg[CFG.CLAUDE_MODEL] = new_model
                    self.cfg[CFG.MODEL] = new_model
                self.cfg[CFG.DIRECTION] = label_to_dir[dir_var.get()]
                self.cfg[CFG.THEME] = label_to_theme[theme_var.get()]
                self.cfg[CFG.POPUP_LAYOUT] = label_to_layout[layout_var.get()]
                self.cfg[CFG.TRAY_CLICK_ACTION] = label_to_tray_click[
                    tray_click_var.get()]
                self.cfg[CFG.DOUBLE_PRESS_WINDOW] = float(gap_var.get())
                self.cfg[CFG.FONT_SIZE] = int(font_var.get())
                self.cfg[CFG.MAX_CHARS] = int(max_var.get())
                self.cfg[CFG.HISTORY_LIMIT] = int(hist_limit_var.get())
                self.cfg[CFG.HISTORY_ENABLED] = bool(history_sw.get())
                self.cfg[CFG.AUTO_UPDATE_ENABLED] = bool(auto_update_sw.get())
                self.cfg[CFG.OCR_ENGINE] = label_to_ocr_engine[
                    ocr_engine_var.get()]
                self.cfg[CFG.OCR_HOTKEY_ENABLED] = bool(ocr_hotkey_sw.get())
                self.cfg[CFG.CLIPBOARD_PROTECTION_ENABLED] = bool(clip_protect_sw.get())
                self.cfg[CFG.SUMMARY_ENABLED] = bool(summary_sw.get())
                # Streaming is now an always-on capability with safe startup
                # fallback rather than a user-facing experiment.
                self.cfg[CFG.CODEX_STREAMING_EXPERIMENTAL] = True
                if restore_defaults_pending:
                    self.cfg[CFG.UI_V2] = DEFAULT_CONFIG[CFG.UI_V2]
                    self.cfg[CFG.UI_V2_DEFAULT_MIGRATED] = DEFAULT_CONFIG[
                        CFG.UI_V2_DEFAULT_MIGRATED]

                # Handle language change
                new_lang = label_to_lang[lang_var.get()]
                old_lang = self.cfg.get(CFG.LANGUAGE)
                self.cfg[CFG.LANGUAGE] = new_lang
                
                self._save_config(self.cfg)
                if autostart_sw.get() != is_autostart_enabled():
                    set_autostart(autostart_sw.get())
                # Re-arm the nightly timer so an auto-update toggle change takes
                # effect immediately.
                self._schedule_nightly_update()
                # Re-resolve theme so new popups pick it up immediately.
                self.theme = resolve_theme(self.cfg)
                self._setup_scrollbar_style()
                # Model/direction feed the warm processes' fixed system prompt;
                # rebuild the whole pool so the next translation (and any
                # pre-warmed depth) uses the new config, not a stale prompt.
                self._set_warm_provider(new_provider)
                if (new_provider == "claude_cli"
                        and (previous_provider != new_provider
                             or self._warm_key() != prev_warm_key)):
                    self._reset_warm_pool()
                
                # If language changed, restart the app
                if new_lang != old_lang:
                    status.config(text=i18n.get("settings.label.language_changed"),
                                  fg=t["status_ok"])
                    self.root.after(600, self._relaunch)
                else:
                    status.config(text=i18n.get("settings.label.saved_notice"),
                                  fg=t["status_ok"])
            except Exception as e:
                status.config(
                    text=f"{i18n.get('settings.label.save_failed')}: {e}",
                    fg=t["status_err"])

        def restore_defaults():
            nonlocal restore_defaults_pending
            # Repopulate every form widget with its DEFAULT_CONFIG value without
            # persisting — the user still clicks Save to commit, or Close to
            # discard. Language is intentionally left untouched (it has no static
            # default and changing it forces an app relaunch).
            restore_defaults_pending = True
            provider_var.set(
                provider_labels[DEFAULT_CONFIG[CFG.MODEL_PROVIDER]])
            refresh_model_choices()
            default_model_labels = get_provider_model_labels(
                DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
            model_var.set(
                default_model_labels[
                    provider_model(DEFAULT_CONFIG)])
            dir_var.set(direction_labels[DEFAULT_CONFIG[CFG.DIRECTION]])
            theme_var.set(theme_labels[DEFAULT_CONFIG[CFG.THEME]])
            layout_var.set(layout_labels[DEFAULT_CONFIG[CFG.POPUP_LAYOUT]])
            ocr_engine_var.set(ocr_engine_labels[DEFAULT_CONFIG[CFG.OCR_ENGINE]])
            tray_click_var.set(
                tray_click_labels[DEFAULT_CONFIG[CFG.TRAY_CLICK_ACTION]])
            font_var.set(DEFAULT_CONFIG[CFG.FONT_SIZE])
            max_var.set(DEFAULT_CONFIG[CFG.MAX_CHARS])
            hist_limit_var.set(DEFAULT_CONFIG[CFG.HISTORY_LIMIT])
            gap_var.set(DEFAULT_CONFIG[CFG.DOUBLE_PRESS_WINDOW])
            summary_sw.set(DEFAULT_CONFIG[CFG.SUMMARY_ENABLED])
            ocr_hotkey_sw.set(DEFAULT_CONFIG[CFG.OCR_HOTKEY_ENABLED])
            history_sw.set(DEFAULT_CONFIG[CFG.HISTORY_ENABLED])
            clip_protect_sw.set(DEFAULT_CONFIG[CFG.CLIPBOARD_PROTECTION_ENABLED])
            auto_update_sw.set(DEFAULT_CONFIG[CFG.AUTO_UPDATE_ENABLED])
            # Autostart isn't stored in the config dict (it's OS state); a fresh
            # install enables it, so that's the default we restore to.
            autostart_sw.set(True)
            status.config(
                text=i18n.get("settings.label.defaults_restored"),
                fg=t["status_ok"])

        def mk_btn(parent, text_, cmd, primary=False):
            if primary:
                base_bg = accent
                base_fg = "#ffffff"
                hover_bg = accent
                active_bg = accent
            else:
                base_bg = t["list_bg"]
                base_fg = fg
                hover_bg = t["btn_active"]
                active_bg = t["list_sel"]
            return self._pill_button(
                parent, text_, cmd,
                bg=base_bg, fg=base_fg,
                hover_bg=hover_bg, hover_fg=base_fg,
                active_bg=active_bg, active_fg=base_fg,
                font=(FONT, 10), padx=20, pady=7,
            )

        save_btn = mk_btn(footer, i18n.get("ui.save"), apply_settings, primary=True)
        save_btn.pack(side="right")
        close2 = mk_btn(footer, i18n.get("settings.label.cancel"), win.destroy)
        close2.pack(side="right", padx=(0, 12))
        # Restore-defaults is a rare action, so it stays low-key via color only
        # (hint text on a plain background). It keeps the SAME font/padding as
        # Cancel/Save so its height matches theirs — otherwise the hover fill
        # would reveal a shorter box and jump against the neighbors.
        self._pill_button(
            footer, i18n.get("settings.label.restore_defaults"),
            restore_defaults,
            bg=bg, fg=hint,
            hover_bg=t["list_bg"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=20, pady=7).pack(side="right", padx=(0, 12))

        win.bind("<Escape>", lambda e: win.destroy())

        # ---- Size & center on the active monitor, then reveal ----
        # The content lives inside a Canvas card inset by the shell's corner
        # reveal, so measure the card and pad by that inset on every side. On the
        # v2 shell the card sits inset by win._card_inset (smaller than the full
        # radius); legacy falls back to the corner radius. The update row's
        # footprint is already reserved above (col-0 min width + row min height),
        # so the measured size stays constant whether or not an update is found.
        win.update_idletasks()
        min_w = max(380, SETTINGS_MIN_W)
        ci = int(getattr(win, "_card_inset", POPUP_CORNER_RADIUS))
        w = max(outer.winfo_reqwidth() + 2 * ci, min_w)
        h = outer.winfo_reqheight() + 2 * ci
        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
            w = min(w, max(1, right - left - 8))
            h = min(h, max(1, bottom - top - 8))
            x = left + (right - left - w) // 2
            y = top + (bottom - top - h) // 2
        else:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(w, max(1, sw - 8))
            h = min(h, max(1, sh - 8))
            x, y = (sw - w) // 2, (sh - h) // 2
        self._reveal_rounded_window(win, w, h, x, y)
