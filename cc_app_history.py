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
from cc_core import CFG, POPUP_CORNER_RADIUS


class HistoryMixin:
    """Translation-history window (mixed into TranslatorApp)."""

    def _build_history_titlebar(self, card, win, *, bg, border, accent, hint,
                                font):
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
        title_lbl = tk.Label(bar, text=i18n.get("history.title"), bg=bg,
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

    def _build_history_views(self, card, *, width, bg, border, theme, font):
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
        return (bottom, listbox, detail, search_wrap, search, search_icon,
                search_var, filter_var)

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

    def _wire_history_interactions(self, win, listbox, detail, entries,
                                   bottom, theme, font, search_wrap, search,
                                   search_icon, search_var, filter_var):
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
            if icon_visible["on"]:
                return
            icon_visible["on"] = True
            search_icon.pack(side="right")

        def hide_search_icon():
            if not icon_visible["on"]:
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
            sel = listbox.curselection()
            if not sel:
                return None
            idx = sel[0]
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
            listbox.delete(0, "end")
            self._populate_history_list(listbox, shown)
            if not shown:
                set_status(i18n.get("history.matches_zero"))
                show_detail()
                return
            idx = shown.index(current) if current in shown else 0
            listbox.selection_set(idx)
            listbox.activate(idx)
            listbox.see(idx)
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

        def hist_btn(text_, cmd, danger=False):
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

        listbox.bind("<<ListboxSelect>>", show_detail)
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

        t = self.theme
        bg = t["settings_bg"]
        border = t["popup_border"]
        accent = t["accent"]
        hint = t["popup_hint"]
        FONT = "Microsoft YaHei UI"
        self._setup_form_style()

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.lift()
        win.focus_force()
        self.history_win = win

        # A larger centred card than the result/settings popups, so the richer
        # history tools (search, filters, copy/rerun actions) still have room.
        w, h, x, y = self._history_box()
        card = self._rounded_shell(win, POPUP_CORNER_RADIUS, bg, border)
        self._build_history_titlebar(
            card, win, bg=bg, border=border, accent=accent, hint=hint,
            font=FONT)

        entries = _tr.load_history()
        (bottom, listbox, detail, search_wrap, search, search_icon,
         search_var, filter_var) = self._build_history_views(
            card, width=w, bg=bg, border=border, theme=t, font=FONT)
        self._wire_history_interactions(
            win, listbox, detail, entries, bottom, t, FONT, search_wrap, search,
            search_icon, search_var, filter_var)

        # ---- Reveal centred, staying above the (topmost) settings window ----
        self._reveal_rounded_window(win, w, h, x, y)
