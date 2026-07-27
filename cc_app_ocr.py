"""cc_app_ocr — OCR / screenshot-translation methods for TranslatorApp.

Mixed into TranslatorApp as OcrMixin. Pure mechanical extraction from
translator.pyw: the 13 OCR methods (region selector overlay, screen capture,
local Windows-OCR path, and the Claude Vision one-shot path). Method bodies are
unchanged.

Imports only leaf modules (cc_core / cc_warm / cc_ocr / i18n / stdlib), never
translator, so there is no import cycle and no double module load.
"""

import os
import json
import time
import uuid
import queue
import threading
import subprocess
import ctypes
import tkinter as tk
from typing import Tuple

import i18n
import cc_ocr
from cc_warm import CLAUDE_CMD
from cc_core import (
    CFG, DATA_DIR, TRIGGER_POLL_MS, StreamSession,
    OCR_VISION_PROMPT, vision_image_mention,
    log_error, log_perf,
)


class OcrMixin:
    def _virtual_screen_rect(self):
        """(x, y, w, h) of the whole virtual desktop in Windows virtual-screen
        coordinates (origin can be negative on multi-monitor setups)."""
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            x = gsm(76)   # SM_XVIRTUALSCREEN
            y = gsm(77)   # SM_YVIRTUALSCREEN
            w = gsm(78)   # SM_CXVIRTUALSCREEN
            h = gsm(79)   # SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception as e:
            log_error("virtual_screen_rect", e)
        # Fallback: primary screen only.
        return (0, 0, self.root.winfo_screenwidth(),
                self.root.winfo_screenheight())

    def _pump_ocr(self):
        """Main-thread drain of Win+Shift+C requests queued by the listener."""
        fired = False
        try:
            while True:
                self._ocr_queue.get_nowait()
                fired = True
        except queue.Empty:
            pass
        if fired and not self.paused and not self._ocr_selecting:
            self._open_region_selector()
        self.root.after(TRIGGER_POLL_MS, self._pump_ocr)

    def _ocr_from_menu(self):
        """Tray 'screenshot translate' entry — start region selection now
        (ignores pause, since it's an explicit user action)."""
        if not self._ocr_selecting:
            self._open_region_selector()

    def _open_region_selector(self):
        """Full-screen overlay for click-drag region selection. Outside the
        selection is dimmed; the selected area stays at normal brightness so the
        user can verify exactly what will be captured. ESC or a right-click
        cancels; a drag smaller than 10x10 px cancels silently."""
        if self._ocr_selecting:
            return
        vx, vy, vw, vh = self._virtual_screen_rect()

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        dim_bg = "#101216"
        key_bg = "#00ff00"
        transparent_hole = False
        try:
            # Preferred path on Windows: make the canvas key color transparent,
            # then draw dim masks around the drag box so the selected area is
            # truly undimmed.
            overlay.configure(bg=key_bg, cursor="crosshair")
            overlay.attributes("-transparentcolor", key_bg)
            transparent_hole = True
        except Exception:
            # Fallback for environments that do not support transparentcolor.
            try:
                overlay.attributes("-alpha", 0.28)
            except Exception:
                pass
            overlay.configure(bg=dim_bg, cursor="crosshair")
        overlay.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self._ocr_selecting = True
        self._ocr_overlay = overlay

        canvas_bg = key_bg if transparent_hole else dim_bg
        canvas = tk.Canvas(overlay, bg=canvas_bg, highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)

        shade_kwargs = {"fill": dim_bg, "outline": ""}
        if transparent_hole:
            shade_kwargs["stipple"] = "gray50"
        shades = [
            canvas.create_rectangle(0, 0, vw, vh, **shade_kwargs),  # top/full
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # left
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # right
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # bottom
        ]

        hint = canvas.create_text(
            vw // 2, 30, fill="#e6e9f0",
            font=("Microsoft YaHei UI", 13),
            text=i18n.get("ocr.drag_select_hint"))

        state = {"sx": 0, "sy": 0, "rect": None}

        def set_dim_hole(x0, y0, x1, y1):
            x0 = max(0, min(vw, x0))
            x1 = max(0, min(vw, x1))
            y0 = max(0, min(vh, y0))
            y1 = max(0, min(vh, y1))
            canvas.coords(shades[0], 0, 0, vw, y0)      # top
            canvas.coords(shades[1], 0, y0, x0, y1)     # left
            canvas.coords(shades[2], x1, y0, vw, y1)    # right
            canvas.coords(shades[3], 0, y1, vw, vh)     # bottom

        def on_down(e):
            state["sx"], state["sy"] = e.x, e.y
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#7aa2f7", width=2)
            set_dim_hole(e.x, e.y, e.x, e.y)
            canvas.delete(hint)

        def on_drag(e):
            if state["rect"]:
                x0, x1 = sorted((state["sx"], e.x))
                y0, y1 = sorted((state["sy"], e.y))
                canvas.coords(state["rect"], x0, y0, x1, y1)
                set_dim_hole(x0, y0, x1, y1)

        def on_up(e):
            x0, y0 = min(state["sx"], e.x), min(state["sy"], e.y)
            x1, y1 = max(state["sx"], e.x), max(state["sy"], e.y)
            w, h = x1 - x0, y1 - y0
            self._close_region_selector()
            if w < 10 or h < 10:
                return   # accidental click / tiny drag → cancel silently
            # Translate canvas (overlay-local) coords back to virtual-screen
            # coords for the grab. Delay it a beat so the dimming overlay is
            # fully repainted away before we capture the underlying pixels.
            gx, gy = vx + x0, vy + y0
            self.root.after(
                120, lambda: self._capture_and_translate(gx, gy, w, h))

        def cancel(_e=None):
            self._close_region_selector()

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        canvas.bind("<Button-3>", cancel)
        overlay.bind("<Escape>", cancel)
        try:
            overlay.grab_set()
        except Exception:
            pass
        overlay.focus_force()

    def _close_region_selector(self):
        self._ocr_selecting = False
        ov = getattr(self, "_ocr_overlay", None)
        self._ocr_overlay = None
        if ov:
            try:
                ov.grab_release()
            except Exception:
                pass
            try:
                ov.destroy()
            except Exception:
                pass

    def _capture_and_translate(self, x, y, w, h):
        """Grab the chosen region, then translate it via the configured OCR
        engine (Claude Vision by default, or offline Windows OCR)."""
        # Unique temp file per capture so overlapping OCR requests can't clobber
        # each other's screenshot (each path is cleaned up by its own worker).
        img_path = os.path.join(DATA_DIR, "tmp_ocr_%s.png" % uuid.uuid4().hex)
        # The overlay is already destroyed; give the compositor one frame to
        # repaint the uncovered screen before we grab it.
        self.root.update_idletasks()
        if not cc_ocr.save_region(x, y, w, h, img_path):
            self._last_input = None
            self._last_origin = "ocr"
            self._last_class = "ocr"
            self._destroy_popup()
            self.popup = self._make_popup(
                i18n.get("error.screenshot_failed"), is_error=True, title=i18n.get("error.title"),
                highlight=False)
            return

        engine = self.cfg.get(CFG.OCR_ENGINE, "claude")
        if engine == "local":
            self._ocr_translate_local(img_path)
        else:
            self._ocr_translate_vision(img_path)

    def _ocr_translate_local(self, img_path):
        """Offline path: recognise text locally, then run it through the normal
        translation pipeline (which reuses dictionary/sentence/code handling).

        Recognition runs on a worker thread (cc_ocr.ocr_local drives a
        synchronous asyncio.run) so a slow or wedged Windows OCR call can never
        freeze the tray app / hotkeys."""
        self._destroy_popup()
        self._last_input = None
        self._last_origin = "ocr"
        self._last_class = "ocr"
        self._cancel_stream_flush()
        self._ss = StreamSession()
        job_id = self._begin_job()
        self.popup = self._make_loading_popup()
        self._animate_loading(0)
        threading.Thread(target=self._do_ocr_local,
                         args=(img_path, job_id), daemon=True).start()

    def _do_ocr_local(self, img_path, job_id):
        """Worker thread: run local OCR, then hand the result back to the UI."""
        text = ""
        try:
            text = cc_ocr.ocr_local(img_path)
        except Exception as e:
            log_error("ocr_local_call", e)
        finally:
            self._cleanup_ocr_temp(img_path)
        text = (text or "").strip()
        self.root.after(0, lambda: self._finish_ocr_local(text, job_id))

    def _finish_ocr_local(self, text, job_id):
        """UI thread: show the recognised text or an error, guarded by job id so
        a superseded OCR request can't overwrite a newer popup."""
        if not self._job_is_current(job_id):
            return
        self._stop_animation()
        if not text:
            self._last_input = None
            self._last_origin = "ocr"
            self._last_class = "ocr"
            self._destroy_popup()
            self.popup = self._make_popup(
                i18n.get("error.no_text_detected"), is_error=True,
                title=i18n.get("tray.screenshot"), highlight=False)
            return
        text = text[: self.cfg[CFG.MAX_CHARS]]
        self._show_loading(text, origin="ocr")

    def _ocr_translate_vision(self, img_path):
        """Default path: send the screenshot to Claude, which reads and
        translates it in one multimodal call. Only the translation is shown."""
        self._destroy_popup()
        self._last_input = None
        self._last_origin = "ocr"
        self._last_class = "ocr"
        self._cancel_stream_flush()
        self._ss = StreamSession()
        job_id = self._begin_job()
        self.popup = self._make_loading_popup()
        self._animate_loading(0)
        threading.Thread(
            target=self._do_translate_vision, args=(img_path, job_id),
            daemon=True).start()

    def _do_translate_vision(self, img_path, job_id):
        ok, result = self._call_claude_vision(img_path)
        self._cleanup_ocr_temp(img_path)
        self.root.after(0, lambda: self._show_result(ok, result, job_id))

    def _cleanup_ocr_temp(self, img_path):
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception as e:
            log_error("ocr_temp_cleanup", e)

    def _call_claude_vision(self, img_path: str) -> Tuple[bool, str]:
        """One-shot Claude call that reads the image via the CLI's `@path`
        reference and returns only the translation. Mirrors _call_claude's
        subprocess/JSON handling.

        Two details are essential for the image to actually be read:
          * The `@path` mention is quoted — DATA_DIR contains a space
            ("CC Translate"), and an unquoted mention would break at the space,
            so Claude never sees the file and replies "please share the image".
          * `--tools ""` disables tools, so the CLI attaches the image as a
            multimodal content block instead of routing it through the Read
            tool (which, in safe-mode headless runs, asks for permission and
            returns a "I need permission to read the file" message)."""
        payload = vision_image_mention(img_path)
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model",
                 self.cfg[CFG.MODEL],
                 "--system-prompt", OCR_VISION_PROMPT,
                 "--output-format", "json",
                 "--tools", "",
                 "--no-session-persistence"],
                input=payload,
                capture_output=True, text=True, encoding="utf-8",
                timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if proc.stdout:
                out = proc.stdout.strip()
                try:
                    result = json.loads(out).get("result", "").strip()
                    if result:
                        log_perf("ocr_vision_done", {
                            "wall_ms": int((time.perf_counter() - t0) * 1000),
                        })
                        return True, result
                except json.JSONDecodeError:
                    if out:
                        return True, out
            return False, self._humanize_error(proc.stderr or "")
        except subprocess.TimeoutExpired:
            return False, i18n.get("error.ocr_timeout")
        except Exception as e:
            log_error("call_claude_vision", e)
            return False, i18n.get("error.unexpected").format(error=e)
