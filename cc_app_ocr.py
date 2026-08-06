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
import win32util
from cc_warm import CLAUDE_CMD
from cc_core import (
    CFG, DATA_DIR, TRIGGER_POLL_MS, StreamSession,
    OCR_VISION_PROMPT, vision_image_mention,
    log_error, log_perf,
)

try:
    from PIL import Image, ImageEnhance, ImageTk
except Exception:
    Image = ImageEnhance = ImageTk = None


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

    def _open_region_selector(self, screen_image=None):
        """Full-screen overlay for click-drag region selection. Outside the
        selection is dimmed; the selected area stays at normal brightness so the
        user can verify exactly what will be captured. ESC or a right-click
        cancels; a drag smaller than 10x10 px cancels silently."""
        if self._ocr_selecting:
            return
        vx, vy, vw, vh = self._virtual_screen_rect()
        v2on = self._v2_popup_on()
        supplied_image = screen_image is not None
        source_image = None
        if v2on and Image is not None and ImageTk is not None:
            source_image = screen_image
            if source_image is None:
                source_image = cc_ocr.grab_region(vx, vy, vw, vh)
            if source_image is not None:
                source_image = source_image.convert("RGB")
                if source_image.size != (vw, vh):
                    source_image = source_image.resize(
                        (vw, vh), Image.Resampling.LANCZOS)

        overlay = tk.Toplevel(self.root)
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.title(i18n.get("tray.screenshot"))
        overlay.geometry(f"{vw}x{vh}+{vx}+{vy}")
        if source_image is not None and not supplied_image:
            overlay.update_idletasks()
            if not win32util.exclude_window_from_capture(overlay.winfo_id()):
                source_image = None
        overlay._v2 = bool(v2on and source_image is not None)
        dim_bg = "#101216"
        key_bg = "#00ff00"
        transparent_hole = False
        if source_image is not None:
            overlay.configure(bg=dim_bg, cursor="crosshair")
        else:
            try:
                # Legacy/fallback path: make the canvas key colour transparent,
                # then place stippled masks around the drag box.
                overlay.configure(bg=key_bg, cursor="crosshair")
                overlay.attributes("-transparentcolor", key_bg)
                transparent_hole = True
            except Exception:
                try:
                    overlay.attributes("-alpha", 0.28)
                except Exception:
                    pass
                overlay.configure(bg=dim_bg, cursor="crosshair")
        self._ocr_selecting = True
        self._ocr_overlay = overlay

        canvas_bg = dim_bg if source_image is not None else (
            key_bg if transparent_hole else dim_bg)
        canvas = tk.Canvas(overlay, bg=canvas_bg, highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        overlay._ocr_canvas = canvas
        overlay._ocr_capture_mode = (
            "image" if source_image is not None else
            "transparent" if transparent_hole else "alpha")

        shades = []
        if source_image is not None:
            dimmed = ImageEnhance.Brightness(source_image).enhance(0.58)
            cool_tint = Image.new("RGB", source_image.size, (8, 12, 34))
            dimmed = Image.blend(dimmed, cool_tint, 0.16)
            dim_photo = ImageTk.PhotoImage(dimmed, master=overlay)
            canvas.create_image(0, 0, anchor="nw", image=dim_photo)
            overlay._ocr_dim_photo = dim_photo
            overlay._ocr_source_image = source_image
        else:
            shade_kwargs = {"fill": dim_bg, "outline": ""}
            if transparent_hole:
                shade_kwargs["stipple"] = "gray50"
            shades = [
                canvas.create_rectangle(
                    0, 0, vw, vh, **shade_kwargs),  # top/full
                canvas.create_rectangle(
                    0, 0, 0, 0, **shade_kwargs),    # left
                canvas.create_rectangle(
                    0, 0, 0, 0, **shade_kwargs),    # right
                canvas.create_rectangle(
                    0, 0, 0, 0, **shade_kwargs),    # bottom
            ]

        hint_text = i18n.get("ocr.drag_select_hint")
        hint = canvas.create_text(
            vw // 2, 30, fill="#e6e9f0",
            font=("Microsoft YaHei UI", 13),
            text=hint_text)
        accessible_hint = tk.Label(
            overlay, text=hint_text, takefocus=0)
        accessible_hint.place(x=-10000, y=-10000)
        overlay._ocr_accessible_hint = accessible_hint

        scale = max(1.0, min(2.5, self._ui_scale()))
        state = {
            "sx": 0, "sy": 0, "rect": None,
            "selection_photo": None, "selection_image": None,
            "click_anchor": None, "dragged": False,
        }
        overlay._ocr_selection_state = state
        selection_items = []
        handles = []
        anchor_marker = []
        if source_image is not None:
            state["selection_image"] = canvas.create_image(
                0, 0, anchor="nw", state="hidden")
            selection_items = [
                canvas.create_rectangle(
                    0, 0, 0, 0, outline="#5546d9",
                    width=max(6, int(round(6 * scale))), state="hidden"),
                canvas.create_rectangle(
                    0, 0, 0, 0, outline="#9878ff",
                    width=max(4, int(round(4 * scale))), state="hidden"),
                canvas.create_rectangle(
                    0, 0, 0, 0, outline="#d8e9ff",
                    width=max(2, int(round(1.5 * scale))), state="hidden"),
            ]
            for _ in range(4):
                handles.append((
                    canvas.create_oval(
                        0, 0, 0, 0, fill="#6652e8", outline="",
                        state="hidden"),
                    canvas.create_oval(
                        0, 0, 0, 0, fill="#eef6ff", outline="",
                        state="hidden"),
                ))
            anchor_marker = [
                canvas.create_oval(
                    0, 0, 0, 0, fill="#6652e8", outline="", state="hidden"),
                canvas.create_oval(
                    0, 0, 0, 0, fill="#eef6ff", outline="", state="hidden"),
            ]
        overlay._ocr_selection_items = selection_items
        overlay._ocr_handles = handles
        overlay._ocr_anchor_marker = anchor_marker

        def set_dim_hole(x0, y0, x1, y1):
            if not shades:
                return
            x0 = max(0, min(vw, x0))
            x1 = max(0, min(vw, x1))
            y0 = max(0, min(vh, y0))
            y1 = max(0, min(vh, y1))
            canvas.coords(shades[0], 0, 0, vw, y0)      # top
            canvas.coords(shades[1], 0, y0, x0, y1)     # left
            canvas.coords(shades[2], x1, y0, vw, y1)    # right
            canvas.coords(shades[3], 0, y1, vw, vh)     # bottom

        def normalized_box(x0, y0, x1, y1):
            x0, x1 = sorted((
                max(0, min(vw, int(x0))),
                max(0, min(vw, int(x1)))))
            y0, y1 = sorted((
                max(0, min(vh, int(y0))),
                max(0, min(vh, int(y1)))))
            return x0, y0, x1, y1

        def set_selection(x0, y0, x1, y1):
            x0, y0, x1, y1 = normalized_box(x0, y0, x1, y1)
            set_dim_hole(x0, y0, x1, y1)
            if source_image is None:
                if state["rect"] is not None:
                    canvas.coords(state["rect"], x0, y0, x1, y1)
                return x0, y0, x1, y1
            if x1 <= x0 or y1 <= y0:
                canvas.itemconfigure(state["selection_image"], state="hidden")
                for item in selection_items:
                    canvas.itemconfigure(item, state="hidden")
                for outer, inner in handles:
                    canvas.itemconfigure(outer, state="hidden")
                    canvas.itemconfigure(inner, state="hidden")
                return x0, y0, x1, y1

            crop = source_image.crop((x0, y0, x1, y1))
            photo = ImageTk.PhotoImage(crop, master=overlay)
            state["selection_photo"] = photo
            canvas.coords(state["selection_image"], x0, y0)
            canvas.itemconfigure(
                state["selection_image"], image=photo, state="normal")
            for item in selection_items:
                canvas.coords(item, x0, y0, x1, y1)
                canvas.itemconfigure(item, state="normal")
                canvas.tag_raise(item)

            outer_r = max(5, int(round(5 * scale)))
            inner_r = max(2, int(round(2 * scale)))
            for (cx, cy), (outer, inner) in zip(
                    ((x0, y0), (x1, y0), (x0, y1), (x1, y1)), handles):
                canvas.coords(
                    outer, cx - outer_r, cy - outer_r,
                    cx + outer_r, cy + outer_r)
                canvas.coords(
                    inner, cx - inner_r, cy - inner_r,
                    cx + inner_r, cy + inner_r)
                canvas.itemconfigure(outer, state="normal")
                canvas.itemconfigure(inner, state="normal")
                canvas.tag_raise(outer)
                canvas.tag_raise(inner)
            return x0, y0, x1, y1

        def accept_selection(x0, y0, x1, y1):
            x0, y0, x1, y1 = normalized_box(x0, y0, x1, y1)
            width, height = x1 - x0, y1 - y0
            selected_image = (
                source_image.crop((x0, y0, x1, y1))
                if source_image is not None and width >= 10 and height >= 10
                else None)
            self._close_region_selector()
            if width < 10 or height < 10:
                return
            gx, gy = vx + x0, vy + y0
            self.root.after(
                0 if selected_image is not None else 120,
                lambda: self._capture_and_translate(
                    gx, gy, width, height, image=selected_image))

        def set_anchor_marker(point):
            if not anchor_marker:
                return
            if point is None:
                for item in anchor_marker:
                    canvas.itemconfigure(item, state="hidden")
                return
            cx, cy = point
            outer_r = max(5, int(round(5 * scale)))
            inner_r = max(2, int(round(2 * scale)))
            canvas.coords(
                anchor_marker[0], cx - outer_r, cy - outer_r,
                cx + outer_r, cy + outer_r)
            canvas.coords(
                anchor_marker[1], cx - inner_r, cy - inner_r,
                cx + inner_r, cy + inner_r)
            for item in anchor_marker:
                canvas.itemconfigure(item, state="normal")
                canvas.tag_raise(item)

        def click_point(x, y):
            x, y, _, _ = normalized_box(x, y, x, y)
            if state["click_anchor"] is None:
                state["click_anchor"] = (x, y)
                set_anchor_marker(state["click_anchor"])
                return False
            x0, y0 = state["click_anchor"]
            state["click_anchor"] = None
            set_anchor_marker(None)
            accept_selection(x0, y0, x, y)
            return True

        overlay._ocr_set_selection = set_selection
        overlay._ocr_accept_selection = accept_selection
        overlay._ocr_click_point = click_point

        def on_down(e):
            if source_image is not None and state["click_anchor"] is not None:
                state["sx"], state["sy"] = state["click_anchor"]
            else:
                state["sx"], state["sy"] = e.x, e.y
            state["dragged"] = False
            if state["rect"] is not None:
                canvas.delete(state["rect"])
            if source_image is None:
                state["rect"] = canvas.create_rectangle(
                    e.x, e.y, e.x, e.y, outline="#7aa2f7", width=2)
                set_selection(e.x, e.y, e.x, e.y)
            elif state["click_anchor"] is None:
                set_selection(e.x, e.y, e.x, e.y)
            canvas.delete(hint)

        def on_drag(e):
            if state["rect"] is not None or source_image is not None:
                if (abs(e.x - state["sx"]) >= 3
                        or abs(e.y - state["sy"]) >= 3):
                    state["dragged"] = True
                    state["click_anchor"] = None
                    set_anchor_marker(None)
                set_selection(state["sx"], state["sy"], e.x, e.y)

        def on_up(e):
            if source_image is not None and not state["dragged"]:
                click_point(e.x, e.y)
                return
            accept_selection(state["sx"], state["sy"], e.x, e.y)

        def on_move(e):
            if source_image is not None and state["click_anchor"] is not None:
                x0, y0 = state["click_anchor"]
                set_selection(x0, y0, e.x, e.y)
                set_anchor_marker(state["click_anchor"])

        def cancel(_e=None):
            self._close_region_selector()

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        canvas.bind("<Motion>", on_move)
        canvas.bind("<Button-3>", cancel)
        overlay.bind("<Escape>", cancel)
        overlay.deiconify()
        overlay.lift()
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

    def _capture_and_translate(self, x, y, w, h, image=None):
        """Persist the approved region, then translate it via the configured OCR
        engine (vision by default, or offline Windows OCR)."""
        # Unique temp file per capture so overlapping OCR requests can't clobber
        # each other's screenshot (each path is cleaned up by its own worker).
        img_path = os.path.join(DATA_DIR, "tmp_ocr_%s.png" % uuid.uuid4().hex)
        self.root.update_idletasks()
        saved = False
        if image is not None:
            try:
                image.save(img_path, "PNG")
                saved = True
            except Exception as e:
                log_error("ocr_save_preview_region", e)
        else:
            # Legacy/fallback selectors have no preview frame to preserve, so
            # recapture after the overlay has repainted away.
            saved = cc_ocr.save_region(x, y, w, h, img_path)
        if not saved:
            self._cleanup_ocr_temp(img_path)
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
        selection = self._provider_selection()
        cancel_event = getattr(self, "_provider_cancel_event", None)
        self.popup = self._make_loading_popup()
        self._animate_loading(0)
        threading.Thread(
            target=self._do_translate_vision,
            args=(img_path, job_id, selection, cancel_event),
            daemon=True).start()

    def _do_translate_vision(self, img_path, job_id, selection=None,
                             cancel_event=None):
        try:
            ok, result = self._call_model_image(
                img_path, selection, cancel_event)
        except Exception as exc:
            log_error("provider_vision", exc)
            ok = False
            result = i18n.get("error.unexpected").format(error=exc)
        finally:
            self._cleanup_ocr_temp(img_path)
        self.root.after(0, lambda: self._show_result(ok, result, job_id))

    def _cleanup_ocr_temp(self, img_path):
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception as e:
            log_error("ocr_temp_cleanup", e)

    def _call_claude_vision(
            self, img_path: str, *, model=None) -> Tuple[bool, str]:
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
        model = model or self.cfg[CFG.MODEL]
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model",
                 model,
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
