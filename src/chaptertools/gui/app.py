"""Main window: library pane, workflow main area, tabs, status bar."""
from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
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
from .workflow import WorkflowPanel

LIBRARY_WIDTH = 300


class App(tk.Tk):
    def __init__(self, library_root: str | None = None):
        super().__init__()
        self.title("chaptertools")
        self.geometry("1600x960")
        self.library_root = resolve_library_root(library_root)
        self.runner = Runner()
        file_log.add_pause_hook(self.runner.pause_for_log)
        self._log_prompt_open = False
        self.player = Player()
        self.session = BookSession()
        self._playback_time: float | None = None
        self._library: dict[str, list[str]] = {}
        self.log_errors = 0

        self._setup_fonts()
        self._build_menu()
        self._build_ui()
        self._fill_library()
        self.fit_minsize()
        self.after_idle(self._place_library_sash)
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def fit_minsize(self):
        """Smallest window in which no panel clips: the widest panel's
        need (Resolve counted stacked, as it stacks when narrow) plus
        the library pane."""
        self.update_idletasks()
        need_w = self.workflow.min_width() + LIBRARY_WIDTH + 8
        need_h = self.workflow.min_height() + 60  # menu + status bar
        self.minsize(max(need_w, 1024), max(need_h, 600))

    def _setup_fonts(self, base: int | None = None):
        if base is None:
            base = int(os.environ.get("CHAPTERGUI_FONT", "11"))
        self.font_base = base
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkFixedFont"):
            try:
                f = tkfont.nametofont(name, root=self)
            except tk.TclError:
                continue
            f.configure(size=base + (2 if name == "TkFixedFont" else 0))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=tkfont.nametofont(
            "TkDefaultFont", root=self).metrics("linespace") + 6)
        style.configure("TButton", padding=(8, 1))

    def set_font_size(self, base: int):
        """View menu: every font, column width and minimum size follows."""
        base = max(8, min(base, 24))
        self._setup_fonts(base)
        fonts.refresh(self)
        self.workflow.size_table()
        self._render_library(self.search_var.get())
        self.fit_minsize()
        self.log(f"text size {base} pt")

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
                         command=lambda: self.set_font_size(11))
        menu.add_cascade(label="View", menu=view)
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base + 1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base - 1))
        self.bind_all("<Control-0>", lambda e: self.set_font_size(11))

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts",
                              command=self._show_shortcuts)
        help_menu.add_command(label="About chaptertools",
                              command=self._show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

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
        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # ---- library pane -------------------------------------------------
        lib = ttk.Frame(pane, padding=4)
        pane.add(lib, weight=0)
        self._lib = lib
        self._library_open = _load_library_open()
        self._library_collapsed = False
        ttk.Style(self).configure("Library.TButton", padding=(2, 2))
        self._lib_expand = ttk.Button(lib, text="›", width=2,
                                      style="Library.TButton",
                                      command=self.toggle_library)
        self._lib_body = ttk.Frame(lib)
        self._lib_body.pack(side="top", fill="both", expand=True)
        searchbar = ttk.Frame(self._lib_body)
        searchbar.pack(side="top", fill="x", pady=(0, 4))
        self.search_var = tk.StringVar()
        self._search_placeholder = "Search title or author"
        self._search_has_placeholder = True
        self.search_var.set(self._search_placeholder)
        search_entry = ttk.Entry(searchbar, textvariable=self.search_var,
                                 foreground="#888888")
        search_entry.pack(side="left", fill="x", expand=True)
        self._search_entry = search_entry

        def _search_focus_in(_e=None):
            if self._search_has_placeholder:
                self._search_has_placeholder = False
                self.search_var.set("")
                search_entry.configure(foreground="")

        def _search_focus_out(_e=None):
            if not self.search_var.get().strip():
                self._search_has_placeholder = True
                self.search_var.set(self._search_placeholder)
                search_entry.configure(foreground="#888888")

        def _on_search_write(*_a):
            value = self.search_var.get()
            if self._search_has_placeholder:
                if value == self._search_placeholder:
                    return
                # Programmatic set (tests / clear) replaces the placeholder
                self._search_has_placeholder = False
                search_entry.configure(foreground="")
            self._apply_search()

        self.search_var.trace_add("write", _on_search_write)
        search_entry.bind("<FocusIn>", _search_focus_in)
        search_entry.bind("<FocusOut>", _search_focus_out)
        search_entry.bind("<Escape>", lambda e: (
            self.search_var.set(""), search_entry.configure(foreground="")))
        ttk.Button(searchbar, text="‹", width=3,
                   command=self.toggle_library).pack(side="right", padx=(4, 0))
        chips = ttk.Frame(self._lib_body)
        chips.pack(side="top", fill="x", pady=(0, 4))
        self.library_filter = tk.StringVar(value="all")
        self.library_filter.trace_add("write", lambda *_: self._apply_search())
        for value, label in (("all", "All"), ("work", "Needs work"),
                             ("done", "Done")):
            ttk.Radiobutton(chips, text=label, value=value,
                            variable=self.library_filter).pack(side="left")
        tree_box = ttk.Frame(self._lib_body)
        tree_box.pack(side="top", fill="both", expand=True)
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.lib_tree = ttk.Treeview(
            tree_box, show="tree", columns=("status",))
        self.lib_tree.heading("status", text="")
        self.lib_tree.column("#0", stretch=True, minwidth=40)
        self.lib_tree.column("status", width=56, stretch=False, anchor="e")
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_box, orient="vertical",
                           command=self.lib_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lib_tree.configure(yscrollcommand=sb.set)
        self.lib_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._librivox_btn = ttk.Button(
            self._lib_body, text="+ Get a book from LibriVox…",
            command=self._open_librivox, state="disabled")
        self._librivox_btn.pack(side="bottom", fill="x", pady=(4, 0))
        self._pane = pane

        # ---- player bar (the one transport; packed before the pane) ------
        self.busy_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.player_bar = PlayerBar(self, self)
        self.player_bar.pack(side="bottom", fill="x", before=pane)
        self.job_window = JobWindow(self)
        self.session.subscribe("job", self.job_window.sync)
        self.log_drawer = LogDrawer(self)
        self.log_text = self.log_drawer.text
        self.log(f"log file {file_log.open_session()}")
        self._logged_job = (False, "", "")
        self.session.subscribe("job", self._log_job_edge)
        self.bind_all("<Control-Shift-L>", lambda e: self.toggle_log())
        self.bind_all("<Control-l>", self.toggle_library)
        self.bind_all("<Button-1>", self._log_button_click, add="+")
        self.bind_all("<ButtonRelease-1>", self._log_value_release, add="+")
        self.bind_class("TCombobox", "<<ComboboxSelected>>", self._log_combobox, add="+")
        self.bind_class("Menu", "<ButtonRelease-1>", self._log_menu_click, add="+")

        # ---- main area ----------------------------------------------------
        main = ttk.Frame(pane)
        pane.add(main, weight=1)
        self._book_host = main
        self.workflow = WorkflowPanel(main, app=self)
        self.workflow.pack(fill="both", expand=True)
        from .librivox_view import LibrivoxView
        self.librivox_view = LibrivoxView(main, app=self)
        # Packed only while Get-from-LibriVox is open.
        self._librivox_btn.configure(state="normal")

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
        kind = widget.winfo_class()
        if kind in ("TScale", "Scale", "TSpinbox", "Spinbox"):
            return
        if kind not in (
            "TButton", "Button", "TCheckbutton", "Checkbutton",
            "TRadiobutton", "Radiobutton", "TMenubutton", "Menubutton",
            "TLabel", "Label",
        ):
            return
        if self._control_disabled(widget):
            return
        try:
            label = str(widget.cget("text") or "").strip()
        except tk.TclError:
            return
        sentence = self._click_sentence(widget, label)
        if sentence:
            self.log(self._with_context(sentence))

    def _log_value_release(self, event):
        widget = self._target(event)
        if not isinstance(widget, tk.Misc):
            return
        if widget.winfo_class() not in ("TScale", "Scale", "TSpinbox", "Spinbox"):
            return
        if self._control_disabled(widget):
            return
        shown = ""
        try:
            shown = str(widget.get()).strip()
        except (tk.TclError, ValueError):
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
            self.log(self._with_context(label))

    def _control_disabled(self, widget) -> bool:
        try:
            return bool(widget.instate(["disabled"]))
        except (tk.TclError, AttributeError):
            pass
        try:
            return "disabled" in str(widget.cget("state"))
        except tk.TclError:
            return False

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
        kind = widget.winfo_class()
        if kind in ("TMenubutton", "Menubutton"):
            return f"Open {label}" if label else "Open the menu"
        if kind in ("TCheckbutton", "Checkbutton"):
            taking = "off" if widget.instate(["selected"]) else "on"
            name = label or self._nearby_label(widget) or "Checkbox"
            return f"{name}: {taking}"
        if kind in ("TRadiobutton", "Radiobutton"):
            if widget is self.player_bar.original_btn:
                return "Hear original"
            if widget is self.player_bar.edited_btn:
                return "Hear edited"
            return f"{label or 'Choice'}: on"
        if kind in ("TLabel", "Label"):
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
            except tk.TclError:
                continue
            if text and not text.isdigit() and child.winfo_class() in ("TLabel", "Label"):
                return text
        return ""

    def copy_log(self):
        self.log_drawer.copy_all()

    def save_log(self):
        self.log_drawer.save()

    def log(self, line: str):
        self.log_drawer.append(line)
        self.status_var.set(line)
        if hasattr(self, "player_bar"):
            self.player_bar.show_status(line)

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
        if getattr(self, "_search_has_placeholder", False):
            query = ""
        query = (query or "").strip().lower()
        active = len(query) >= 3
        self.lib_tree.delete(*self.lib_tree.get_children())
        selected = self.lib_tree.selection()

        for author in sorted(self._library):
            author_hit = active and query in author.lower()
            paths = [p for p in self._library[author]
                     if (not active or author_hit or query in _title_of(p))
                     and self._chip_allows(p)]
            if not paths:
                continue
            author_id = self.lib_tree.insert("", "end",
                                             text=author, open=active,
                                             values=(""))
            # Group multi-file folders into one row when librivox.json exists
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
                else:
                    label = _book_label(path, self._library[author])
                    status = _book_status(folder, [path], None)
                    self.lib_tree.insert(
                        author_id, "end", iid=str(path), text=label,
                        values=(status,))

        if selected and self.lib_tree.exists(selected[0]):
            self.lib_tree.selection_set(selected[0])

    def _apply_search(self):
        q = "" if self._search_has_placeholder else self.search_var.get()
        self._render_library(q)

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
        if self._library_collapsed:
            width = max(44, self._lib_expand.winfo_reqwidth() + 8)
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
    def _pump(self):
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
        self._playback_time = t
        if t is not None:
            self.session.playhead = t
        self.player_bar.update_time(t)
        self.workflow.update_playback(t)
        # Multi-file: continue into the next chapter when one file ends
        if was_alive and not alive and getattr(
                self.workflow, "_multifile_mode", False):
            mf = getattr(self.workflow, "multifile", None)
            if mf is not None and self.session.chapter_index is not None:
                if self.session.chapter_index + 1 < len(mf.files):
                    mf.play_next()
        quality_busy = getattr(getattr(self.workflow, "quality", None),
                               "busy", False)
        busy = self.runner.busy or quality_busy
        self.workflow.set_busy(busy)
        self.busy_var.set("busy \u25cc" if busy else "")
        if not busy and self.session.job.running:
            self.session.set_job(False)
        elif busy and not self.session.job.running:
            self.session.set_job(True, "busy", "Working")
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


def _library_pref_path():
    from chaptertools.audio_quality.presets import config_dir
    return config_dir() / "gui.json"


def _load_library_open() -> bool:
    """False collapses the list once a book is open."""
    try:
        with open(_library_pref_path()) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return bool(data.get("library_open"))


def _save_library_open(open_: bool) -> None:
    path = _library_pref_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"library_open": open_}, handle)
    except OSError:
        pass


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
        return "4/4"
    if marks:
        # crude progress from glyphs; detailed stage count is in the rail
        return "1/4" if "●" in marks else "0/4"
    return "0/4"


def main(argv: list[str] | None = None):
    if argv is None:
        argv = sys.argv[1:]
    library_root = next((a for a in argv if not a.startswith("-")), None)
    app = App(library_root=library_root)
    app.mainloop()


if __name__ == "__main__":
    main()
