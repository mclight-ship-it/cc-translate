"""cc_app_about — About / Support-author / Uninstall windows for CC Translate.

AboutMixin holds the seven methods behind the tray's "About" and "Support
author" entries plus the themed uninstall flow: open_about/_open_about,
_confirm_and_uninstall/_perform_uninstall, open_support_author/
_open_support_author, and the _open_url helper.

Extracted verbatim from ``TranslatorApp`` in translator.pyw (bodies unchanged).
Like the other cc_app_* mixins this imports only leaf modules (tkinter, i18n,
win32util, cc_update) and the shared foundation (cc_core); it never imports
translator.pyw, so there is no import cycle. ``self`` resolves at runtime against
the assembled ``TranslatorApp`` instance, so calls into other mixins and shared
window-building helpers (``self._rounded_shell``, ``self._reveal_rounded_window``
…) keep working.
"""

import os
import threading
import tkinter as tk

import i18n

from win32util import get_monitor_rect
from cc_update import version_string, remove_shortcuts, spawn_uninstaller
from cc_core import (APP_DIR, DATA_DIR, log_error, POPUP_CORNER_RADIUS,
                     V2_CORNER_RADIUS)


class AboutMixin:
    """About / Support-author / Uninstall dialogs (mixed into TranslatorApp)."""

    # ---------- About window ----------
    def open_about(self):
        self.root.after(0, self._open_about)

    def _open_about(self):
        if self.about_win and tk.Toplevel.winfo_exists(self.about_win):
            self._bring_to_front(self.about_win)
            return

        v2on = self._v2_popup_on()
        scale = self._ui_scale() if v2on else 1.0
        t = self._v2_window_theme() if v2on else self.theme
        bg = t["settings_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        FONT = "Microsoft YaHei UI"

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.about_win = win
        # v2 skin: frosted rounded shell + shared brand header. Fixed-size card,
        # so its whole ring drags (never resizes).
        win._v2 = v2on
        win._v2_resizable = False

        radius = V2_CORNER_RADIUS if v2on else POPUP_CORNER_RADIUS
        card = self._rounded_shell(win, radius, bg, border)

        # ---- Title bar ----
        if v2on:
            # Shared v2 chrome: brand tile + gradient title + ghost close. No
            # subtitle (the hero logo/name/description below carry the identity)
            # and no hairline divider — the roomy padding does the separating.
            bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
            bar.pack(fill="x", padx=24, pady=(20, 14))
            self._v2_brand_header(
                bar, win, title=i18n.get("about.title"),
                subtitle=None,
                bg=bg, hint=hint, accent=accent, font=FONT, scale=scale,
                cache_tag="about_title")
        else:
            bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
            bar.pack(fill="x", padx=16, pady=(12, 8))
            logo_img = self._logo_image(18)
            drag_targets = [bar]
            if logo_img:
                logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                                    highlightthickness=0)
                logo_lbl.image = logo_img
                logo_lbl.pack(side="left", padx=(0, 8))
                drag_targets.append(logo_lbl)
            title_lbl = tk.Label(bar, text=i18n.get("about.title"), bg=bg,
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
            self._make_draggable(tuple(drag_targets), win)

            tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        # ---- Content (vertically centered) ----
        # v2 gets roomy side margins (matching the settings window) so the card
        # doesn't feel cramped; legacy keeps its tighter fixed-box padding.
        body = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=(44 if v2on else 20),
                  pady=24)

        # Wrapper frame for content to center vertically in body
        content_frame = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        content_frame.pack(fill="none", expand=True, anchor="center")

        # Top spacer
        tk.Frame(content_frame, bg=bg, height=8).pack()

        # App logo/icon (larger: 48px)
        logo_img_large = self._logo_image(48)
        if logo_img_large:
            logo_large_lbl = tk.Label(content_frame, image=logo_img_large, bg=bg, bd=0,
                                      highlightthickness=0)
            logo_large_lbl.image = logo_img_large
            logo_large_lbl.pack(pady=(0, 16))

        # App name
        name_lbl = tk.Label(content_frame, text=i18n.get("about.name"), bg=bg, fg=accent,
                            font=(FONT, 14, "bold"))
        name_lbl.pack(pady=(0, 8))

        # Description
        desc_lbl = tk.Label(content_frame, text=i18n.get("about.description"), bg=bg, fg=hint,
                            font=(FONT, 10))
        desc_lbl.pack(pady=(0, 20))

        # Middle spacer
        tk.Frame(content_frame, bg=bg, height=8).pack()

        info_group = tk.Frame(content_frame, bg=bg, bd=0, highlightthickness=0)
        info_group.pack(pady=(4, 0))

        # Version + GitHub
        version_str = version_string()
        version_frame = tk.Frame(info_group, bg=bg, bd=0, highlightthickness=0)
        version_frame.pack(pady=5)
        version_lbl = tk.Label(
            version_frame, text=f"{i18n.get('about.version')}: {version_str}", bg=bg, fg=fg,
            font=(FONT, 10))
        version_lbl.pack(side="left")

        # "Check for updates" lives here (moved off the tray menu to keep it
        # short). It reuses the same in-Settings flow as before, so the update
        # experience — status line + explicit "更新并重启" button — is unchanged.
        # Styled like the "support author" action (accent colour, no underline,
        # no hover recolour): it's a button, not a URL, and the accent colour
        # already signals it's clickable.
        update_lbl = tk.Label(
            version_frame, text=i18n.get("about.check_update"), bg=bg,
            fg=accent, font=(FONT, 10), cursor="hand2")
        update_lbl.pack(side="left", padx=(20, 0))
        update_lbl.bind("<Button-1>", lambda e: self._about_check_update())

        github_url = "https://github.com/mclight-ship-it/cc-translate"
        github_frame = tk.Frame(info_group, bg=bg, bd=0, highlightthickness=0)
        github_frame.pack(pady=5)
        github_label = tk.Label(github_frame, text="GitHub: ",
                               bg=bg, fg=fg, font=(FONT, 10))
        github_label.pack(side="left")
        github_lbl = tk.Label(github_frame, text=github_url, bg=bg, fg=accent,
                             font=(FONT, 10, "underline"), cursor="hand2")
        github_lbl.pack(side="left")
        github_lbl.bind("<Button-1>", lambda e: self._open_url(github_url))

        # Contact author + coffee link
        contact_group = tk.Frame(content_frame, bg=bg, bd=0, highlightthickness=0)
        contact_group.pack(pady=(30, 0))

        contact_frame = tk.Frame(contact_group, bg=bg, bd=0, highlightthickness=0)
        contact_frame.pack(pady=5)
        contact_label = tk.Label(contact_frame, text=i18n.get('about.contact_author') + ": ",
                                bg=bg, fg=fg, font=(FONT, 10))
        contact_label.pack(side="left")
        email_addr = i18n.get('about.author_email')
        email_lbl = tk.Label(contact_frame, text=email_addr, bg=bg, fg=accent,
                            font=(FONT, 10, "underline"), cursor="hand2")
        email_lbl.pack(side="left")
        email_lbl.bind("<Button-1>", lambda e: self._open_url(f"mailto:{email_addr}"))

        support_row = tk.Frame(contact_group, bg=bg, bd=0, highlightthickness=0)
        support_row.pack(pady=(14, 0))
        coffee_photo = self._emoji_image("\u2615", px=13, bg_hex=bg)
        if coffee_photo:
            coffee_lbl = tk.Label(
                support_row, image=coffee_photo, bg=bg, cursor="hand2",
                bd=0, highlightthickness=0)
            coffee_lbl.image = coffee_photo
        else:
            coffee_lbl = tk.Label(
                support_row, text="\u2615", bg=bg, fg=accent, cursor="hand2",
                font=("Segoe UI Emoji", 11))
        coffee_lbl.pack(side="left", padx=(0, 6))
        coffee_lbl.bind("<Button-1>", lambda e: self.open_support_author())
        support_lbl = tk.Label(
            support_row, text=i18n.get("about.support_author"), bg=bg,
            fg=accent, font=(FONT, 10), cursor="hand2")
        support_lbl.pack(side="left")
        support_lbl.bind("<Button-1>", lambda e: self.open_support_author())

        # Bottom spacer
        tk.Frame(content_frame, bg=bg, height=8).pack()

        win.bind("<Escape>", lambda e: win.destroy())

        # v2 hugs its measured content (like settings); legacy uses the fixed
        # centred box and lets the content centre inside it.
        if v2on:
            win.update_idletasks()
            ci = int(getattr(win, "_card_inset", radius))
            w = card.winfo_reqwidth() + 2 * ci
            h = card.winfo_reqheight() + 2 * ci
            rect = get_monitor_rect()
            if rect:
                left, top, right, bottom = rect
                x = left + (right - left - w) // 2
                y = top + (bottom - top - h) // 2
            else:
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                x, y = (sw - w) // 2, (sh - h) // 2
        else:
            w, h, x, y = self._centered_box()
        self._reveal_rounded_window(win, w, h, x, y)

    def _confirm_and_uninstall(self):
        """Show a themed confirm dialog with a checked-by-default "keep my data"
        toggle, then run the uninstaller if the user confirms."""
        t = self.theme
        bg = t["settings_bg"]
        fg = t["settings_fg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        FONT = "Microsoft YaHei UI"

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        card = self._rounded_shell(win, POPUP_CORNER_RADIUS, bg, border)

        # ---- Title bar ----
        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=16, pady=(12, 8))
        title_lbl = tk.Label(bar, text=i18n.get("uninstall.title"), bg=bg,
                             fg=t["status_err"], font=(FONT, 11, "bold"))
        title_lbl.pack(side="left")
        close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                             font=(FONT, 11), cursor="hand2", padx=6)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: win.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=t["status_err"]))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
        self._make_draggable((bar, title_lbl), win)

        tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        body = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=20, pady=(16, 16))

        msg = tk.Label(body, text=i18n.get("uninstall.body"), bg=bg, fg=fg,
                       font=(FONT, 10), justify="left", wraplength=360)
        msg.pack(anchor="w", pady=(0, 14))

        keep_row = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        keep_row.pack(fill="x", pady=(0, 4))
        keep_lbl = tk.Label(keep_row, text=i18n.get("uninstall.keep_data"),
                            bg=bg, fg=fg, font=(FONT, 10))
        keep_lbl.pack(side="left")
        keep_sw = self._make_toggle(keep_row, True, bg)
        keep_sw.pack(side="right")

        status = tk.Label(body, text="", bg=bg, fg=t["status_err"],
                          font=(FONT, 9))
        status.pack(anchor="w", pady=(10, 0))

        footer = tk.Frame(body, bg=bg, bd=0, highlightthickness=0)
        footer.pack(fill="x", pady=(14, 0))

        def on_confirm():
            status.config(text=i18n.get("uninstall.working"), fg=hint)
            win.update_idletasks()
            keep = keep_sw.get()
            ok = self._perform_uninstall(remove_data=not keep)
            if not ok:
                status.config(text=i18n.get("uninstall.failed"),
                              fg=t["status_err"])

        confirm_btn = self._pill_button(
            footer, i18n.get("uninstall.confirm"), on_confirm,
            bg=t["status_err"], fg="#ffffff",
            hover_bg=t["status_err"], hover_fg="#ffffff",
            active_bg=t["status_err"], active_fg="#ffffff",
            font=(FONT, 10), padx=18, pady=6)
        confirm_btn.pack(side="right")
        cancel_btn = self._pill_button(
            footer, i18n.get("uninstall.cancel"), win.destroy,
            bg=t["list_bg"], fg=fg,
            hover_bg=t["btn_active"], hover_fg=fg,
            active_bg=t["list_sel"], active_fg=fg,
            font=(FONT, 10), padx=18, pady=6)
        cancel_btn.pack(side="right", padx=(0, 8))

        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        w = max(card.winfo_reqwidth() + 2 * POPUP_CORNER_RADIUS, 420)
        h = card.winfo_reqheight() + 2 * POPUP_CORNER_RADIUS
        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
            x = left + (right - left - w) // 2
            y = top + (bottom - top - h) // 2
        else:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x, y = (sw - w) // 2, (sh - h) // 2
        self._reveal_rounded_window(win, w, h, x, y)

    def _perform_uninstall(self, remove_data=False):
        """Remove shortcuts, spawn the detached cleanup helper, then tear the
        app down (modeled on _relaunch). Returns False if the helper couldn't
        be spawned (in which case the app stays running)."""
        try:
            remove_shortcuts()
        except Exception as e:
            log_error("uninstall_shortcuts", e)
        ok = False
        try:
            ok = spawn_uninstaller(app_dir=APP_DIR, data_dir=DATA_DIR,
                                   remove_data=remove_data)
        except Exception as e:
            log_error("uninstall_spawn", e)
            ok = False
        if not ok:
            return False
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        self.close_warm_pool()
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass
        # Force a prompt exit so the interpreter releases its file locks and the
        # cleanup helper can delete the program folder.
        threading.Timer(1.2, lambda: os._exit(0)).start()
        return True

    def open_support_author(self):
        self.root.after(0, self._open_support_author)

    def _open_support_author(self):
        if self.support_win and tk.Toplevel.winfo_exists(self.support_win):
            self._bring_to_front(self.support_win)
            return

        t = self.theme
        bg = t["settings_bg"]
        border = t["popup_border"]
        hint = t["popup_hint"]
        accent = t["accent"]
        FONT = "Microsoft YaHei UI"

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.support_win = win

        card = self._rounded_shell(win, POPUP_CORNER_RADIUS, bg, border)

        bar = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=16, pady=(12, 8))
        logo_img = self._logo_image(18)
        drag_targets = [bar]
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=bg, bd=0,
                               highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(0, 8))
            drag_targets.append(logo_lbl)
        title_lbl = tk.Label(bar, text=i18n.get("support.title"), bg=bg,
                             fg=accent, font=(FONT, 11, "bold"))
        title_lbl.pack(side="left")
        drag_targets.append(title_lbl)
        close_btn = tk.Label(bar, text="✕", bg=bg, fg=hint,
                             font=(FONT, 11), cursor="hand2", padx=6)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: win.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=t["status_err"]))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=hint))
        self._make_draggable(tuple(drag_targets), win)
        tk.Frame(card, bg=border, height=1).pack(fill="x", padx=16)

        body = tk.Frame(card, bg=bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True, padx=18, pady=18)

        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        mon_w = right - left
        mon_h = bottom - top
        max_image_w = max(240, mon_w - 64)
        max_image_h = max(180, mon_h - 120)
        support_img, img_w, img_h = self._load_support_image(max_image_w, max_image_h)
        if support_img:
            image_frame = tk.Frame(
                body, bg=bg, bd=0, highlightthickness=0,
                width=img_w, height=img_h)
            image_frame.pack_propagate(False)
            image_frame.pack(expand=True)
            img_lbl = tk.Label(image_frame, image=support_img, bg=bg, bd=0,
                               highlightthickness=0)
            img_lbl.pack(fill="both", expand=True)
            win._support_image = support_img
        else:
            fallback_lbl = tk.Label(
                body, text=i18n.get("support.image_missing"), bg=bg, fg=hint,
                font=(FONT, 10))
            fallback_lbl.pack(expand=True, padx=12, pady=24)

        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        w = card.winfo_reqwidth() + 2 * POPUP_CORNER_RADIUS
        h = card.winfo_reqheight() + 2 * POPUP_CORNER_RADIUS
        w = min(w, max(320, mon_w - 20))
        h = min(h, max(240, mon_h - 20))
        x = left + (mon_w - w) // 2
        y = top + (mon_h - h) // 2
        self._reveal_rounded_window(win, w, h, x, y)

    def _about_check_update(self):
        """"Check for updates" from the About window: close About, then run the
        same in-Settings check the tray used to trigger, so both paths share one
        experience (status line + explicit "更新并重启" button)."""
        try:
            if self.about_win and tk.Toplevel.winfo_exists(self.about_win):
                self.about_win.destroy()
        except Exception:
            pass
        self.check_update_via_settings()

    def _open_url(self, url):
        """Open a URL in the default browser."""
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
