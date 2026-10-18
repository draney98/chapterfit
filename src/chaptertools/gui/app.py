"""Main window: library pane, workflow main area, tabs, status bar."""
from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from chaptertools import file_log
from chaptertools.util import clock

from . import fonts
from .busy import JobWindow, progress_sentence
from .library import book_marks, scan_library
from .library import library_root as resolve_library_root
from .log_drawer import LogDrawer
from .mpv import Player
from .player_bar import PlayerBar
from .runner import Runner
from .session import BookSession
from .step_rail import STEP_LABELS
from .table import SHORTCUTS
from .theme import (
    APPEARANCE_MODES,
    apply_theme,
    ctk_control,
    ctk_frame,
    ctk_kind,
    bind_menu_before,
    install_click_focus,
    install_menu_trace,
    is_disabled,
    load_gui_prefs,
    require_customtkinter,
    dispatch_theme,
    follow_theme,
    restyle_menus,
    restyle_ttk,
    save_gui_prefs,
    widget_text,
)
from .ui_kit import PAD, C, SMALL, font, ground, secondary, small, style_treeview
from .workflow import WorkflowPanel

LIBRARY_WIDTH = 300

ctk = require_customtkinter()


class App(ctk.CTk):
    def __init__(self, library_root: str | None = None, *,
                 modal_busy: bool = True):
        prefs = load_gui_prefs()
        mode = prefs.get("appearance", "Auto")
        if mode == "System":
            mode = "Auto"
        if mode not in APPEARANCE_MODES:
            mode = "Auto"
        apply_theme(mode=mode)
        super().__init__()
        apply_theme(self, mode=mode)
        install_click_focus(self)
        self.title("chaptertools")
        self.geometry("1600x960")
        self.appearance_mode = mode
        self.library_root = resolve_library_root(library_root)
        self.runner = Runner()
        self.modal_busy = modal_busy
        file_log.add_pause_hook(self.runner.pause_for_log)
        self._log_prompt_open = False
        self.player = Player()
        self.session = BookSession()
        self._playback_time: float | None = None
        self._library: dict[str, list[str]] = {}
        self.log_errors = 0

        self._setup_fonts()
        restyle_ttk(self)
        style_treeview(self)
        self.configure(fg_color=C["bg"])
        self._build_menu()
        self._build_ui()
        follow_theme(self, lambda: restyle_menus(self))
        self._fill_library()
        self.fit_minsize()
        self.after_idle(self._place_library_sash)
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def minsize(self, width=None, height=None):
        """Query form (no args) still works; CustomTkinter only sets size."""
        if width is None and height is None:
            return tk.Tk.minsize(self)
        return super().minsize(width, height)

    def fit_minsize(self):
        """Smallest window in which no panel clips: the widest panel's
        need (Resolve counted stacked, as it stacks when narrow) plus
        the library pane."""
        self.update_idletasks()
        need_w = self.workflow.min_width() + LIBRARY_WIDTH + 8
        need_h = self.workflow.min_height() + 60  # menu + status bar
        self.minsize(max(need_w, 1024), max(need_h, 600))

    def geometry(self, geometry_string=None):
        if geometry_string is not None:
            self._geometry_pending = geometry_string
        return super().geometry(geometry_string)

    def update(self):
        """Re-apply a geometry the first map snapped to the minimum size.

        CustomTkinter ignores ``geometry`` until the window has been
        mapped, then opens at ``minsize`` and forgets the request.
        """
        super().update()
        self.realize_geometry()

    def realize_geometry(self) -> None:
        """Apply a geometry requested before the window was mapped."""
        pending = getattr(self, "_geometry_pending", None)
        if not pending or self.winfo_width() <= 1:
            return
        self._geometry_pending = None
        super().geometry(pending)
        super().update_idletasks()

    def is_idle(self) -> bool:
        """No job of any kind is running and the status bar says Idle."""
        if self.runner.busy or self.session.job.running:
            return False
        quality = getattr(getattr(self, "workflow", None), "quality", None)
        if getattr(quality, "busy", False):
            return False
        harvest = getattr(getattr(self, "workflow", None), "harvest", None) or {}
        if harvest.get("loading"):
            return False
        bar = getattr(self, "player_bar", None)
        if bar is None:
            return False
        return bar.status_var.get() == "Idle"

    def destroy(self):
        hook = getattr(getattr(self, "runner", None), "pause_for_log", None)
        if hook is not None:
            with contextlib.suppress(ValueError):
                file_log._hooks.remove(hook)
        super().destroy()

    def _setup_fonts(self, base: int | None = None):
        if base is None:
            base = int(os.environ.get("CHAPTERGUI_FONT", "14"))
        from .ui_kit import apply_named_fonts
        self.font_base = apply_named_fonts(self, base)
        restyle_ttk(self)

    def set_font_size(self, base: int):
        """View menu: every font, column width and minimum size follows."""
        self._setup_fonts(base)
        fonts.refresh(self)
        self.workflow.size_table()
        self._render_library(self.search_var.get())
        self.fit_minsize()
        self.log(f"text size {self.font_base}")

    def set_appearance(self, mode: str):
        """View menu: Auto / Light / Dark."""
        if mode not in APPEARANCE_MODES:
            return
        self.appearance_mode = mode
        apply_theme(self, mode=mode)
        restyle_ttk(self)
        restyle_menus(self)
        dispatch_theme(self)
        workflow = getattr(self, "workflow", None)
        if workflow is not None and hasattr(workflow, "restore_step_grid"):
            workflow.restore_step_grid()
        save_gui_prefs(appearance=mode, library_open=self._library_open)
        self.log(f"appearance {mode.lower()}")

    def _build_menu(self):
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open library…", command=self._open_library)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._quit)
        menu.add_cascade(label="File", menu=file_menu)

        view = tk.Menu(menu, tearoff=0)
        view.add_command(label="Larger text", accelerator="Ctrl++",
                         command=lambda: self.set_font_size(self.font_base + 1))
        view.add_command(label="Smaller text", accelerator="Ctrl+-",
                         command=lambda: self.set_font_size(self.font_base - 1))
        view.add_command(label="Default text size", accelerator="Ctrl+0",
                         command=lambda: self.set_font_size(14))
        view.add_separator()
        appearance = tk.Menu(view, tearoff=0)
        self._appearance_var = tk.StringVar(value=self.appearance_mode)
        for mode in APPEARANCE_MODES:
            appearance.add_radiobutton(
                label=mode, variable=self._appearance_var, value=mode,
                command=lambda m=mode: self.set_appearance(m))
        view.add_cascade(label="Appearance", menu=appearance)
        menu.add_cascade(label="View", menu=view)
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base + 1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base - 1))
        self.bind_all("<Control-0>", lambda e: self.set_font_size(14))

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts",
                              command=self._show_shortcuts)
        help_menu.add_command(label="About chaptertools",
                              command=self._show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        # A native menubar sits on the window edge and clips "File".
        # These buttons post the same menus, inset with the rest of the shell.
        from .ui_kit import SMALL, font, text_button
        bar = ctk_frame(self, fg_color="transparent")
        bar.pack(side="top", fill="x", padx=14, pady=(4, 0))
        self._menu_bar = bar

        def post(submenu, widget):
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height()
            try:
                submenu.tk_popup(x, y)
            finally:
                submenu.grab_release()

        for label, submenu in (
                ("File", file_menu), ("View", view), ("Help", help_menu)):
            button = text_button(
                bar, label, width=font(SMALL).measure(label) + 20)
            button.configure(
                command=lambda w=button, m=submenu: post(m, w))
            button.pack(side="left", padx=(0, 8))

    def _show_shortcuts(self):
        messagebox.showinfo(
            "Keyboard shortcuts",
            "In the chapter table:\n\n" + SHORTCUTS.replace(" · ", "\n")
            + "\n\nAnywhere:\n\nCtrl++ / Ctrl+- larger / smaller text\n"
              "Ctrl+0 default text size\n"
              "Ctrl+L library · Ctrl+Shift+L log\n"
              "Space play or pause · ←/→ ±5 s · Shift+←/→ ±10 s",
            parent=self)

    def _show_about(self):
        from chaptertools import __version__
        messagebox.showinfo(
            "About chaptertools",
            f"chaptertools {__version__}\n\nChapter placement for "
            "audiobooks: Measure, Identify, Align, Resolve, Emit, "
            "Finalize.", parent=self)

    def _build_ui(self):
        pane = ttk.Panedwindow(self, orient="horizontal",
                               style="Library.TPanedwindow")
        pane.pack(fill="both", expand=True)

        # ---- library pane -------------------------------------------------
        lib = ground(pane)
        pane.add(lib, weight=0)
        self._lib = lib
        self._library_open = bool(load_gui_prefs().get("library_open"))
        self._library_collapsed = False
        self._lib_expand = secondary(
            lib, "›", command=self.toggle_library, width=28)
        from . import tips
        tips.tooltip(self._lib_expand, "Show library")
        self._lib_body = ctk_frame(lib, corner_radius=0, fg_color=C["bg"])
        self._lib_body.pack(side="top", fill="both", expand=True,
                            padx=14, pady=PAD // 2)
        self._lib_body.columnconfigure(0, weight=1)
        self._lib_body.rowconfigure(2, weight=1)

        searchbar = ctk_frame(self._lib_body, fg_color="transparent")
        searchbar.grid(row=0, column=0, sticky="ew", pady=(0, PAD // 2))
        searchbar.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        self._search_has_placeholder = False
        search_entry = ctk.CTkEntry(
            searchbar, textvariable=self.search_var,
            placeholder_text="Search books",
            fg_color=C["panel"], border_color=C["border"],
            text_color=C["text"])
        search_entry.grid(row=0, column=0, sticky="ew")
        self._search_entry = search_entry

        def _on_search_write(*_a):
            self._apply_search()

        self.search_var.trace_add("write", _on_search_write)
        search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
        collapse = secondary(searchbar, "‹", command=self.toggle_library, width=36)
        collapse.grid(row=0, column=1, padx=(PAD // 2, 0))
        from . import tips
        tips.tooltip(collapse, "Hide the library")

        chips = ctk_frame(self._lib_body, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="ew", pady=(0, PAD // 2))
        chips.columnconfigure(0, weight=1)
        self.library_filter = tk.StringVar(value="all")
        self.library_filter.trace_add("write", lambda *_: self._apply_search())
        self._lib_filter_labels = {
            "All": "all", "Needs work": "work", "Done": "done"}
        self._lib_filter_values = {v: k for k, v in self._lib_filter_labels.items()}
        self._lib_seg = ctk.CTkSegmentedButton(
            chips, values=list(self._lib_filter_labels),
            command=self._on_library_filter, border_width=None,
            dynamic_resizing=True, height=28,
            font=font(SMALL),
            fg_color=C["sunken"], selected_color=C["accent_soft"],
            selected_hover_color=C["accent_soft"],
            unselected_color=C["sunken"],
            unselected_hover_color=C["border"],
            text_color=C["text"])
        self._lib_seg.set("All")
        self._lib_seg.grid(row=0, column=0, sticky="ew")
        from .theme import paint_segment_text
        paint_segment_text(self._lib_seg)
        self._lib_count_var = tk.StringVar(value="")
        self._lib_count = small(chips, text="")
        self._lib_count.configure(textvariable=self._lib_count_var, anchor="w")
        self._lib_count.grid(row=1, column=0, sticky="w", pady=(4, 0))

        tree_box = ctk_frame(self._lib_body, fg_color="transparent")
        tree_box.grid(row=2, column=0, sticky="nsew")
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.lib_tree = ttk.Treeview(
            tree_box, show="tree", columns=("status",))
        self.lib_tree.heading("status", text="")
        self.lib_tree.column("#0", stretch=True, minwidth=40)
        self.lib_tree.column("status", width=56, stretch=False, anchor="e")
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        from .ui_kit import vscroll
        vscroll(tree_box, self.lib_tree, row=0, column=1, sticky="ns")
        self._status_tips: dict[str, str] = {}
        self.lib_tree.bind("<Motion>", self._library_status_tip)
        self.lib_tree.bind("<Leave>", self._hide_status_tip)
        self._status_tip = None
        self.lib_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._librivox_btn = secondary(
            self._lib_body, "+ Get a book from LibriVox…",
            command=self._open_librivox)
        self._librivox_btn.configure(state="disabled")
        self._librivox_btn.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        self._pane = pane

        # ---- player bar (the one transport; packed before the pane) ------
        self.busy_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.player_bar = PlayerBar(self, self)
        self.player_bar.pack(side="bottom", fill="x", padx=14, before=pane)
        self.job_window = JobWindow(self)
        self.session.subscribe("job", self.job_window.sync)
        self.log_drawer = LogDrawer(self)
        self.log_text = self.log_drawer.text
        self.log(f"log file {file_log.open_session()}")
        install_menu_trace(self)
        self._logged_job = (False, "", "")
        self.session.subscribe("job", self._log_job_edge)
        self.bind_all("<Control-Shift-L>", lambda e: self.toggle_log())
        self.bind_all("<Control-l>", self.toggle_library)
        self.bind_all("<Button-1>", self._log_button_click, add="+")
        self.bind_all("<ButtonRelease-1>", self._log_value_release, add="+")
        self.bind_class("TCombobox", "<<ComboboxSelected>>", self._log_combobox, add="+")
        bind_menu_before(self, "<ButtonRelease>", self._log_menu_click)

        # ---- main area ----------------------------------------------------
        main = ground(pane)
        pane.add(main, weight=1)
        self._book_host = main
        self.workflow = WorkflowPanel(main, app=self)
        self.workflow.pack(fill="both", expand=True)
        from .librivox_view import LibrivoxView
        self.librivox_view = LibrivoxView(main, app=self)
        # Packed only while Get-from-LibriVox is open.
        self._librivox_btn.configure(state="normal")

    def _on_library_filter(self, label: str) -> None:
        value = self._lib_filter_labels.get(label, "all")
        if self.library_filter.get() != value:
            self.library_filter.set(value)
        seg_label = self._lib_filter_values.get(value, "All")
        if self._lib_seg.get() != seg_label:
            self._lib_seg.set(seg_label)

    def toggle_log(self):
        self.log_drawer.toggle()

    def _log_key(self, event):
        return self.log_drawer._key(event)

    # ------------------------------------------------------------ log pane --
    def _copy_selection(self):
        self.log_drawer._copy_selection()

    def clear_log(self):
        self.log_drawer.clear()

    def _log_job_edge(self) -> None:
        """Log busy when a job starts or ends. Progress lines stay on the hover."""
        job = self.session.job
        running, name, detail = self._logged_job
        if job.running and running and job.name == name:
            return
        if job.running:
            if running:
                self.log(f"idle: {detail}")
            self.log(f"busy: {job.detail or 'Working'}")
            self._logged_job = (True, job.name, job.detail or "Working")
            return
        if running:
            self.log(f"idle: {detail}")
        self._logged_job = (False, "", "")

    def _target(self, event):
        """The control under the pointer.

        A click inside a canvas window can arrive as the canvas. The chain
        enable boxes and the detail scroller are drawn that way.
        """
        widget = event.widget
        if not isinstance(widget, tk.Misc):
            return widget
        if widget.winfo_class() != "Canvas":
            return widget
        try:
            found = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            return widget
        if isinstance(found, tk.Misc) and found is not widget:
            return found
        return widget

    def _log_button_click(self, event):
        widget = self._target(event)
        # A combobox dropdown is a Tcl widget with no Python wrapper, so
        # Tkinter leaves event.widget as a path string.
        if not isinstance(widget, tk.Misc):
            return
        control = ctk_control(widget)
        if control is not None:
            widget = control
            kind = ctk_kind(widget)
        else:
            kind = widget.winfo_class()
        if kind in ("TScale", "Scale", "TSpinbox", "Spinbox", "CTkSlider"):
            return
        if kind not in (
            "TButton", "Button", "TCheckbutton", "Checkbutton",
            "TRadiobutton", "Radiobutton", "TMenubutton", "Menubutton",
            "TLabel", "Label",
            "CTkButton", "CTkCheckBox", "CTkSwitch", "CTkRadioButton",
            "CTkSegmentedButton", "CTkLabel",
        ):
            return
        if self._control_disabled(widget):
            return
        label = widget_text(widget)
        if kind == "CTkSegmentedButton":
            with contextlib.suppress(tk.TclError, AttributeError):
                label = str(widget.get() or "").strip()
        sentence = self._click_sentence(widget, label)
        if sentence:
            self.log(self._with_context(sentence))

    def _log_value_release(self, event):
        widget = self._target(event)
        if not isinstance(widget, tk.Misc):
            return
        control = ctk_control(widget)
        if control is not None and ctk_kind(control) == "CTkSlider":
            widget = control
            kind = "CTkSlider"
        else:
            kind = widget.winfo_class()
        if kind not in ("TScale", "Scale", "TSpinbox", "Spinbox", "CTkSlider"):
            return
        if self._control_disabled(widget):
            return
        shown = ""
        try:
            shown = str(widget.get()).strip()
        except (tk.TclError, ValueError, AttributeError):
            shown = ""
        name = self._field_label(widget)
        sentence = f"{name}: {shown}" if shown else name
        self.log(self._with_context(sentence))

    def _log_combobox(self, event):
        widget = event.widget
        if not isinstance(widget, tk.Misc):
            return
        if widget.winfo_class() not in ("TCombobox", "Combobox"):
            return
        if self._control_disabled(widget):
            return
        try:
            value = str(widget.get()).strip()
        except tk.TclError:
            return
        name = self._field_label(widget)
        sentence = f"{name}: {value}" if value else name
        self.log(self._with_context(sentence))

    def _log_menu_click(self, event):
        from .theme import trace_menu_event
        trace_menu_event(self, "menu-click-enter", event)
        menu = event.widget
        if not isinstance(menu, tk.Menu):
            return
        try:
            index = menu.index("active")
        except tk.TclError:
            return
        if index is None:
            return
        try:
            if menu.type(index) not in ("command", "checkbutton", "radiobutton"):
                return
            if "disabled" in str(menu.entrycget(index, "state")):
                return
            label = str(menu.entrycget(index, "label") or "").strip()
        except tk.TclError:
            return
        if label:
            trace_menu_event(self, "menu-click-before-log", event)
            self.log(self._with_context(label))
            trace_menu_event(self, "menu-click-after-log", event)

    def _control_disabled(self, widget) -> bool:
        return is_disabled(widget)

    def _with_context(self, action: str) -> str:
        context = self._click_context()
        if context:
            return f"{action} — {context}"
        return action

    def _click_context(self) -> str:
        parts = []
        title = self.session.book_info.title.strip()
        if not title and self.session.book:
            title = os.path.splitext(os.path.basename(self.session.book))[0]
        if title:
            parts.append(title)
        step = ""
        try:
            step = self.workflow._step
        except AttributeError:
            step = ""
        label = STEP_LABELS.get(step, "")
        if label:
            parts.append(label)
        return " — ".join(parts)

    def _click_sentence(self, widget, label: str) -> str:
        if widget is self.player_bar.play_btn:
            return self._play_action()
        if widget is self.player_bar.log_btn:
            if self.log_drawer.visible:
                return "Close the log"
            return "Open the log"
        seek = {
            "−10": "Seek back 10 s",
            "−5": "Seek back 5 s",
            "+5": "Seek forward 5 s",
            "+10": "Seek forward 10 s",
        }.get(label)
        if seek:
            return seek
        if label in {"Cancel job", "Cancel"}:
            return "Cancel the running job"
        for name in STEP_LABELS.values():
            if label == name or label.endswith("  " + name):
                return f"Show {name}"
        kind = ctk_kind(widget) or widget.winfo_class()
        if kind in ("TMenubutton", "Menubutton"):
            return f"Open {label}" if label else "Open the menu"
        if kind in ("TCheckbutton", "Checkbutton", "CTkCheckBox", "CTkSwitch"):
            taking = "on"
            try:
                if kind.startswith("CTk"):
                    taking = "off" if widget.get() else "on"
                else:
                    taking = "off" if widget.instate(["selected"]) else "on"
            except (tk.TclError, AttributeError):
                pass
            name = label or self._nearby_label(widget) or "Checkbox"
            return f"{name}: {taking}"
        if kind in ("TRadiobutton", "Radiobutton", "CTkRadioButton",
                    "CTkSegmentedButton"):
            if label in {"Original", "original"}:
                return "Hear original"
            if label in {"Edited", "edited"}:
                return "Hear edited"
            if widget is getattr(self.player_bar, "original_btn", None):
                return "Hear original"
            if widget is getattr(self.player_bar, "edited_btn", None):
                return "Hear edited"
            return f"{label or 'Choice'}: on"
        if kind in ("TLabel", "Label", "CTkLabel"):
            return self._label_sentence(widget, label)
        if not label or label == "\u21ba":
            return self._reset_sentence(widget)
        extra = self._action_detail(label)
        if extra:
            return f"{label}: {extra}"
        return label

    def _label_sentence(self, widget, label: str) -> str:
        if not widget.bind("<Button-1>"):
            return ""
        text = label.strip()
        nearby = self._nearby_label(widget)
        if text.isdigit():
            if nearby:
                return f"Select {nearby}"
            return f"Select chapter {text}"
        if nearby and nearby != text:
            return f"Select {nearby}"
        if text:
            return f"Select {text}"
        return ""

    def _reset_sentence(self, widget) -> str:
        name = self._nearby_label(widget) or "this setting"
        baseline = self._owning_baseline(widget)
        if baseline is None or baseline == "":
            return f"Reset {name}"
        return f"Reset {name} to {baseline}"

    def _owning_baseline(self, widget):
        current = widget
        for _ in range(6):
            parent = current.winfo_parent()
            if not parent:
                return None
            try:
                current = current.nametowidget(parent)
            except tk.TclError:
                return None
            baseline = getattr(current, "baseline", None)
            if baseline is not None and not isinstance(baseline, dict):
                return baseline
        return None

    def _field_label(self, widget) -> str:
        workflow = self._workflow()
        quality = getattr(workflow, "quality", None)
        panel = getattr(quality, "chain_panel", None)
        if panel is not None and widget is getattr(panel, "preset_box", None):
            return "Preset"
        if workflow is not None and widget is getattr(workflow, "filter_box", None):
            return "Chapter filter"
        return self._nearby_label(widget) or "Choice"

    def _workflow(self):
        try:
            return self.workflow
        except AttributeError:
            return None

    def _action_detail(self, label: str) -> str:
        workflow = self._workflow()
        if workflow is None:
            return ""
        if label == "Search":
            return self._search_detail(workflow)
        if label == "Identify":
            mode = workflow.identify_mode.get().strip()
            value = workflow.identify_value.get().strip()
            return f"{mode} {value}".strip()
        if label == "Use this edition":
            return self._edition_detail(workflow)
        if label in {"Dry run", "Apply to library"}:
            embed = "embed chapters" if workflow.embed_var.get() else "chapters file only"
            clean = "clean audio" if workflow.include_cleanup.get() else "leave audio"
            return f"{embed}, {clean}"
        if label == "Learn from selection":
            span = self.session.selection
            if not span:
                return ""
            return f"{clock(span[0])}–{clock(span[1])}"
        if label == "Align with hint":
            return workflow.hint_var.get().strip()
        return ""

    def _search_detail(self, workflow) -> str:
        parts = []
        title = workflow.asin_title.get().strip()
        author = workflow.asin_author.get().strip()
        keywords = workflow.asin_keywords.get().strip()
        region = workflow.asin_region.get().strip()
        if title:
            parts.append(f"title {title}")
        if author:
            parts.append(f"author {author}")
        if keywords:
            parts.append(f"keywords {keywords}")
        if region:
            parts.append(f"region {region}")
        return ", ".join(parts)

    def _edition_detail(self, workflow) -> str:
        try:
            selected = workflow.asin_tree.selection()
        except tk.TclError:
            return ""
        if not selected:
            return ""
        values = workflow.asin_tree.item(selected[0], "values")
        if not values:
            return ""
        return str(values[0])

    def _play_action(self) -> str:
        starting = str(self.player_bar.play_btn.cget("text")) != "❚❚"
        cleanup = False
        quality = None
        try:
            cleanup = self.workflow._step == "cleanup"
            quality = self.workflow.quality
        except AttributeError:
            cleanup = False
        if not starting:
            return "Stop the preview" if cleanup else "Pause playback"
        span = self.session.selection
        start = span[0] if span else (self.session.playhead or 0.0)
        when = clock(start)
        if not cleanup:
            return f"Play from {when}"
        name = (getattr(quality, "_preset_name", "") or "unsaved")
        hear = getattr(quality, "hear_var", None)
        source = "original" if hear is not None and hear.get() else "edited"
        sentence = f"Play a 30 s preview from {when}, preset {name}, {source}"
        chain = getattr(quality, "chain", None)
        if chain is not None and quality._is_modified():
            changed = [
                stage.name for stage in chain.stages
                if quality._stage_modified(stage.name)
            ]
            if changed:
                sentence += ", modified: " + ", ".join(changed)
        return sentence

    def _nearby_label(self, widget) -> str:
        try:
            parent = widget.nametowidget(widget.winfo_parent())
        except (KeyError, tk.TclError):
            return ""
        for child in parent.winfo_children():
            if child is widget:
                continue
            try:
                text = str(child.cget("text") or "").strip()
            except (tk.TclError, ValueError):
                continue
            kind = ctk_kind(child) or child.winfo_class()
            if text and not text.isdigit() and kind in (
                    "TLabel", "Label", "CTkLabel"):
                return text
        return ""

    def copy_log(self):
        self.log_drawer.copy_all()

    def save_log(self):
        self.log_drawer.save()

    def log(self, line: str):
        self.log_drawer.append(line)
        # Status bar is Idle / job detail only — not every log line.
        self.status_var.set(line)
        # Deliberately do not push arbitrary log lines into the player bar.

    # ------------------------------------------------------------ library --
    def _open_library(self):
        chosen = filedialog.askdirectory(parent=self,
                                         initialdir=self.library_root)
        if chosen:
            self.library_root = chosen
            self._fill_library()

    def _fill_library(self):
        self._library = scan_library(self.library_root)
        self._render_library(self.search_var.get())

    def _render_library(self, query: str | None):
        """(Re)draw the tree; a query of 3+ chars filters by title/author."""
        query = (query or "").strip().lower()
        active = len(query) >= 3
        self.lib_tree.delete(*self.lib_tree.get_children())
        self._status_tips = {}
        selected = self.lib_tree.selection()
        shown_books = 0
        total_books = 0

        for author in sorted(self._library):
            all_paths = list(self._library[author])
            # Count total books (grouped) for the chip/total label.
            seen_total: set[str] = set()
            for path in all_paths:
                folder = os.path.dirname(path)
                if folder in seen_total:
                    continue
                siblings = [p for p in all_paths if os.path.dirname(p) == folder]
                meta = _folder_librivox(folder)
                if meta or len(siblings) > 1:
                    seen_total.add(folder)
                else:
                    seen_total.add(path)
            total_books += len(seen_total)

            author_hit = active and query in author.lower()
            paths = [p for p in all_paths
                     if (not active or author_hit or query in _title_of(p))
                     and self._chip_allows(p)]
            if not paths:
                continue
            author_id = self.lib_tree.insert("", "end",
                                             text=author, open=active,
                                             values=(""))
            shown: set[str] = set()
            for path in paths:
                folder = os.path.dirname(path)
                if folder in shown:
                    continue
                siblings = [p for p in self._library[author]
                            if os.path.dirname(p) == folder]
                meta = _folder_librivox(folder)
                if meta or len(siblings) > 1:
                    shown.add(folder)
                    label = os.path.basename(folder)
                    status = _book_status(folder, siblings, meta)
                    iid = f"book:{folder}"
                    self.lib_tree.insert(
                        author_id, "end", iid=iid, text=label,
                        values=(status,))
                    tip = _status_tip(status)
                    if tip:
                        self._status_tips[iid] = tip
                    shown_books += 1
                else:
                    label = _book_label(path, self._library[author])
                    status = _book_status(folder, [path], None)
                    self.lib_tree.insert(
                        author_id, "end", iid=str(path), text=label,
                        values=(status,))
                    tip = _status_tip(status)
                    if tip:
                        self._status_tips[str(path)] = tip
                    shown_books += 1

        if selected and self.lib_tree.exists(selected[0]):
            self.lib_tree.selection_set(selected[0])
        if hasattr(self, "_lib_count_var"):
            self._lib_count_var.set(
                f"{shown_books} books" if total_books else "")

    def _library_status_tip(self, event):
        row = self.lib_tree.identify_row(event.y)
        col = self.lib_tree.identify_column(event.x)
        text = self._status_tips.get(row, "") if col == "#1" else ""
        current = getattr(self, "_status_tip_text", "")
        if text == current:
            return
        self._hide_status_tip()
        self._status_tip_text = text
        if not text:
            return
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 16}")
        from . import palette
        tip.configure(background=palette.current("BORDER"))
        ttk.Label(tip, text=text, style="Tip.TLabel", takefocus=0).pack(
            padx=1, pady=1)
        self._status_tip = tip

    def _hide_status_tip(self, _event=None):
        tip = getattr(self, "_status_tip", None)
        if tip is not None:
            tip.destroy()
            self._status_tip = None
        self._status_tip_text = ""

    def _apply_search(self):
        self._render_library(self.search_var.get())

    def _open_librivox(self):
        """Open the Get-from-LibriVox view (Part 2)."""
        view = getattr(self, "librivox_view", None)
        if view is not None:
            view.open_search()

    def _show_librivox(self, show: bool) -> None:
        """Swap the book pane for the LibriVox search/fetch view."""
        if show:
            self.workflow.pack_forget()
            self.librivox_view.pack(fill="both", expand=True)
        else:
            self.librivox_view.pack_forget()
            self.workflow.pack(fill="both", expand=True)

    def _chip_allows(self, path: str) -> bool:
        choice = self.library_filter.get()
        if choice == "all":
            return True
        done = "✓" in book_marks(path)
        if choice == "done":
            return done
        return not done

    def toggle_library(self, event=None):
        """Expand or collapse the list. The choice is remembered.

        Shift is left for the log shortcut.
        """
        if event is not None and int(getattr(event, "state", 0)) & 0x0001:
            return
        self._library_open = self._library_collapsed
        _save_library_open(self._library_open)
        self._apply_library_collapse(not self._library_open)
        return "break"

    def sync_library_strip(self):
        """Collapse while a book is open, unless the list is pinned open."""
        collapse = bool(self.workflow.audio) and not self._library_open
        self._apply_library_collapse(collapse)

    def _apply_library_collapse(self, collapsed: bool):
        self._library_collapsed = collapsed
        if collapsed:
            self._lib_body.pack_forget()
            self._lib_expand.pack(side="top", anchor="n")
        else:
            self._lib_expand.pack_forget()
            self._lib_body.pack(side="top", fill="both", expand=True)
        self._place_library_sash()

    def _place_library_sash(self):
        width = LIBRARY_WIDTH
        style = ttk.Style(self)
        if self._library_collapsed:
            width = 28
            # No grip dots on the collapsed strip. Other panes keep a sash.
            style.configure("Library.TPanedwindow", sashthickness=0)
            style.configure("Sash", gripcount=0)
        else:
            style.configure("Library.TPanedwindow", sashthickness=6)
            style.configure("Sash", gripcount=10, sashthickness=6)
        with contextlib.suppress(tk.TclError):
            self._pane.sashpos(0, width)

    def _on_select(self, _event=None):
        sel = self.lib_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if self.lib_tree.parent(iid):
            path = self._path_for(iid)
            if not path:
                return
            # In-progress LibriVox fetch: reopen the fetch view
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            meta = _folder_librivox(folder)
            fetch = (meta or {}).get("fetch") or {}
            if fetch and int(fetch.get("done") or 0) < int(fetch.get("total") or 0):
                self._open_librivox()
                return
            if getattr(self, "librivox_view", None) is not None:
                self._show_librivox(False)
            self.workflow.set_book(path)

    def _path_for(self, iid) -> str | None:
        """Leaf ids are an audio path, or book:<folder> for multi-file books."""
        if isinstance(iid, str) and iid.startswith("book:"):
            folder = iid[5:]
            meta = _folder_librivox(folder)
            if meta:
                return folder
            for _author, paths in self._library.items():
                for path in paths:
                    if os.path.dirname(path) == folder:
                        return path
            return None
        return iid if os.path.isfile(iid) else None

    # ------------------------------------------------------------ plumbing --
    def _pump(self, again: bool = True):
        """Drain runner events and poll the player on the Tk thread."""
        self._check_log_size()
        while True:
            try:
                kind, job, payload = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                sentence = progress_sentence(payload)
                if sentence:
                    self.session.set_job_detail(sentence)
                if getattr(job, "on_line", None):
                    job.on_line(payload)
                else:
                    self.log(payload)
            elif kind == "done":
                if payload != 0 and not job.cancelled:
                    self.log_errors += 1
                    self.player_bar.set_log_errors(self.log_errors)
                if job.on_done:
                    job.on_done(job, payload)
            elif kind == "refused":
                self.log(f"! {job.name} not started: {payload}")
        self.workflow.poll_facts()
        if getattr(self.workflow, "quality", None) is not None:
            self.workflow.quality.poll()
        was_alive = getattr(self, "_player_was_alive", False)
        t = self.player.poll()
        alive = self.player.alive
        self._player_was_alive = alive
        # LibriVox sections: mpv's clock is file-local; show book time.
        book_t = self.workflow.book_time_from_file(t) if hasattr(
            self.workflow, "book_time_from_file") else t
        self._playback_time = book_t
        if book_t is not None:
            self.session.playhead = book_t
        self.player_bar.update_time(book_t)
        self.workflow.update_playback(book_t)
        # Multi-file: continue into the next chapter when one file ends
        if was_alive and not alive and getattr(
                self.workflow, "section_files", None):
            self.workflow.play_next_section()
        elif was_alive and not alive and getattr(
                self.workflow, "_multifile_mode", False):
            mf = getattr(self.workflow, "multifile", None)
            if (mf is not None and self.session.chapter_index is not None
                    and self.session.chapter_index + 1 < len(mf.files)):
                mf.play_next()
        quality_busy = getattr(getattr(self.workflow, "quality", None),
                               "busy", False)
        busy = self.runner.busy or quality_busy
        self.workflow.set_busy(busy)
        # Progress lives on the player bar while a job runs; no idle glyph.
        self.busy_var.set("")
        if not busy and self.session.job.running:
            self.session.set_job(False)
        elif busy and not self.session.job.running:
            self.session.set_job(True, "busy", "Working")
        if again:
            self.after(100, self._pump)

    def _check_log_size(self) -> None:
        """A new 50 MB step stops the job and asks before anything else runs."""
        if self._log_prompt_open:
            return
        file_log.consider()
        if not file_log.prompt_pending():
            return
        self._log_prompt_open = True
        try:
            from chaptertools.audio_quality import neural
            size_mb = file_log.size() / (1024 * 1024)
            yes = messagebox.askyesno(
                "Log file",
                f"{file_log.LOG_PATH} is {size_mb:.0f} MB. Keep going?",
                parent=self)
            if not yes:
                neural.abort_active()
            file_log.resolve(bool(yes))
            self.runner.release_log_hold(bool(yes))
            if not yes:
                self._cancel()
        finally:
            self._log_prompt_open = False

    def _cancel(self):
        self.runner.cancel()
        self.player.stop()
        if getattr(self.workflow, "quality", None) is not None:
            self.workflow.quality.cancel()

    def current_playback(self) -> float | None:
        return self._playback_time

    def play(self, path: str, start: float | None = None,
             length: int | None = None):
        self.stop_audio()
        try:
            self.player.play(path,
                             start=clock(start) if start is not None else None,
                             length=length)
        except OSError as e:
            messagebox.showerror("mpv", f"could not start mpv: {e}", parent=self)

    def stop_audio(self):
        self.player.stop()
        quality = getattr(self.workflow, "quality", None)
        player = getattr(quality, "_player", None)
        if player is not None:
            player.stop()
        self._playback_time = None
        self.workflow.update_playback(None)
        self.player_bar.update_time(None)

    def _quit(self):
        self.runner.cancel()
        self.player.stop()
        if getattr(self.workflow, "quality", None) is not None:
            self.workflow.quality.cancel()
        self.destroy()


def _save_library_open(open_: bool) -> None:
    prefs = load_gui_prefs()
    prefs["library_open"] = open_
    save_gui_prefs(**prefs)


def _book_label(path: str, siblings: list[str]) -> str:
    """The book folder's name; the file name too when a folder holds
    several audio files."""
    folder = os.path.dirname(path)
    book = os.path.basename(folder)
    shared = sum(1 for p in siblings if os.path.dirname(p) == folder)
    if shared > 1 or not book:
        name = os.path.splitext(os.path.basename(path))[0]
        return f"{book} — {name}" if book else name
    return book


def _title_of(path: str) -> str:
    """Searchable title text: filename without extension + book folder."""
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(os.path.dirname(path))
    return f"{folder} {base}".lower()


def _folder_librivox(folder: str) -> dict | None:
    path = os.path.join(folder, "librivox.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _status_tip(status: str) -> str:
    if status == "✓":
        return "4 of 4 steps done"
    if status == "•":
        return "Needs work"
    return ""


def _book_status(folder: str, files: list[str], meta: dict | None) -> str:
    """Right-aligned library status: 4/4, 1/4, 27 files, ↓ 9/27."""
    if meta and meta.get("fetch"):
        done = int(meta["fetch"].get("done") or 0)
        total = int(meta["fetch"].get("total") or 0)
        if total and done < total:
            return f"↓ {done}/{total}"
    if meta or len(files) > 1:
        return f"{len(files)} files"
    audio = files[0] if files else ""
    marks = book_marks(audio) if audio else ""
    if "✓" in marks:
        return "✓"
    if marks:
        return "•"
    return ""


def main(argv: list[str] | None = None):
    if argv is None:
        argv = sys.argv[1:]
    require_customtkinter()
    if any(a == "--screenshot" or a.startswith("--screenshot=") for a in argv):
        from .screenshot import run_screenshot
        raise SystemExit(run_screenshot(argv))
    library_root = next((a for a in argv if not a.startswith("-")), None)
    app = App(library_root=library_root)
    app.mainloop()


if __name__ == "__main__":
    main()
