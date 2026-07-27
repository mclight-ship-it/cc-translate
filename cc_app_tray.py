"""cc_app_tray — system-tray behaviour for CC Translate.

TrayMixin holds the five methods that build and maintain the pystray tray icon:
loading/refreshing the theme-matched icon, drawing a fallback glyph, dispatching
the configurable left-click action, and building the right-click menu.

These methods were extracted verbatim from ``TranslatorApp`` in translator.pyw;
the bodies are unchanged. Like the other cc_app_* mixins this module imports only
leaf modules (i18n, threading) and the shared foundation (cc_core) — it never
imports translator.pyw, so there is no import cycle. ``self`` still resolves at
runtime against the single assembled ``TranslatorApp`` instance, so calls into
other mixins (``self.open_settings``, ``self.open_history`` …) and shared state
(``self.cfg``, ``self.root``, ``self.tray`` …) keep working.
"""

import threading

import i18n

from cc_core import (
    APP_NAME, CFG, log_error,
    detect_taskbar_theme, tray_icon_path,
)


class TrayMixin:
    """System-tray icon lifecycle and menu (mixed into TranslatorApp)."""

    def _load_tray_image(self, taskbar_theme=None):
        """Load the tray icon that matches the current taskbar theme."""
        from PIL import Image
        theme = taskbar_theme or detect_taskbar_theme()
        path = tray_icon_path(theme)
        if path:
            try:
                return Image.open(path)
            except Exception as e:
                # Fall back to the drawn glyph below, but record why the shipped
                # icon didn't load (rare — only on theme change, not a hot loop).
                log_error("load_tray_image", e)
        return self._make_cc_image(theme)

    def _run_tray_click_action(self):
        """Run the action the user chose for a single left-click on the tray
        icon. Reads config live so a change in Settings takes effect without
        rebuilding the tray menu. Unknown/legacy values fall back to Settings."""
        action = self.cfg.get(CFG.TRAY_CLICK_ACTION, "settings")
        if action == "history":
            self.open_history()
        elif action == "screenshot":
            self.root.after(0, self._ocr_from_menu)
        elif action == "quick_input":
            self.open_quick_input()
        else:
            self.open_settings()

    def _start_tray(self):
        import pystray

        self._tray_theme = detect_taskbar_theme()
        image = self._load_tray_image(self._tray_theme)

        def on_settings(icon, item):
            self.open_settings()

        def on_history(icon, item):
            self.open_history()

        def on_quick_input(icon, item):
            self.open_quick_input()

        def on_ocr(icon, item):
            self.root.after(0, self._ocr_from_menu)

        def on_toggle_pause(icon, item):
            self.paused = not self.paused
            icon.update_menu()

        def on_check_update(icon, item):
            self.check_update_via_settings()

        def on_diagnostics(icon, item):
            self.open_diagnostics()

        def on_about(icon, item):
            self.open_about()

        def on_default_click(icon, item):
            # Left-clicking the tray icon runs the user-chosen action. Read the
            # config at click-time (not menu-build time) so changing the setting
            # takes effect without rebuilding the tray menu.
            self._run_tray_click_action()

        def on_quit(icon, item):
            icon.stop()
            self.close_warm_pool()
            self.root.after(0, self.root.destroy)

        menu = pystray.Menu(
            # Invisible default item: this is what a left-click activates. It
            # dispatches to the user-configured action instead of being wired to
            # a single fixed entry, while the visible items below stay complete
            # so every feature remains reachable from the right-click menu.
            pystray.MenuItem(
                "default", on_default_click, default=True, visible=False),
            pystray.MenuItem(i18n.get("tray.history"), on_history),
            pystray.MenuItem(i18n.get("tray.quick_input"), on_quick_input),
            pystray.MenuItem(i18n.get("tray.screenshot_menu"), on_ocr),
            pystray.MenuItem(
                lambda item: i18n.get("tray.resume") if self.paused else i18n.get("tray.pause"),
                on_toggle_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18n.get("tray.settings"), on_settings),
            pystray.MenuItem(i18n.get("tray.diagnostics"), on_diagnostics),
            pystray.MenuItem(i18n.get("about.title"), on_about),
            pystray.MenuItem(i18n.get("tray.check_update"), on_check_update),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18n.get("tray.exit"), on_quit),
        )
        self.tray = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()
        # Keep the tray glyph contrasting when the user flips the Windows
        # taskbar between light and dark at runtime.
        self.root.after(3000, self._watch_taskbar_theme)

    def _watch_taskbar_theme(self):
        """Swap the tray icon if the taskbar theme changed (polled)."""
        try:
            theme = detect_taskbar_theme()
            if theme != getattr(self, "_tray_theme", None) and self.tray:
                self._tray_theme = theme
                self.tray.icon = self._load_tray_image(theme)
        except Exception:
            pass
        self.root.after(3000, self._watch_taskbar_theme)

    def _make_cc_image(self, taskbar_theme=None):
        """Fallback glyph drawn in code when the .ico files are unavailable.

        Mirrors the shipped icons: a transparent 'CC' tinted light for a dark
        taskbar and brand-blue for a light one, so it stays visible either way.
        """
        from PIL import Image, ImageDraw, ImageFont
        theme = taskbar_theme or detect_taskbar_theme()
        colour = (37, 99, 235, 255) if theme == "light" else (245, 246, 248, 255)
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("seguibl.ttf", 40)
        except Exception:
            try:
                font = ImageFont.truetype("arialbd.ttf", 40)
            except Exception:
                font = ImageFont.load_default()
        draw.text((32, 32), "CC", font=font, fill=colour, anchor="mm")
        return img
