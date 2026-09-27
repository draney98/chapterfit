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

from chaptertools.util import clock

from . import fonts
from .library import book_marks, scan_library
from .library import library_root as resolve_library_root
from .log_drawer import LogDrawer
from .mpv import Player
from .player_bar import PlayerBar
from .runner import Runner
from .session import BookSession
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
        ttk.Label(searchbar, text="search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_search())
        search_entry = ttk.Entry(searchbar, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
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
        self.lib_tree = ttk.Treeview(tree_box, show="tree")
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_box, orient="vertical",
                           command=self.lib_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(tree_box, orient="horizontal",
                            command=self.lib_tree.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self.lib_tree.configure(yscrollcommand=sb.set,
                                xscrollcommand=xsb.set)
        self.lib_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._pane = pane

        # ---- player bar (the one transport; packed before the pane) ------
        self.busy_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.player_bar = PlayerBar(self, self)
        self.player_bar.pack(side="bottom", fill="x", before=pane)
        self.log_drawer = LogDrawer(self)
        self.log_text = self.log_drawer.text
        self.bind_all("<Control-Shift-L>", lambda e: self.toggle_log())
        self.bind_all("<Control-l>", self.toggle_library)
        self.bind_all("<Button-1>", self._log_button_click, add="+")

        # ---- main area ----------------------------------------------------
        main = ttk.Frame(pane)
        pane.add(main, weight=1)
        self.workflow = WorkflowPanel(main, app=self)
        self.workflow.pack(fill="both", expand=True)

    def toggle_log(self):
        self.log_drawer.toggle()

    def _log_key(self, event):
        return self.log_drawer._key(event)

    # ------------------------------------------------------------ log pane --
    def _copy_selection(self):
        self.log_drawer._copy_selection()

    def clear_log(self):
        self.log_drawer.clear()

    def _log_button_click(self, event):
        w = event.widget
        # A combobox dropdown is a Tcl widget with no Python wrapper, so
        # Tkinter leaves event.widget as a path string.
        if not isinstance(w, tk.Misc):
            return
        if w.winfo_class() not in ("TButton", "Button"):
            return
        try:
            if "disabled" in str(w.cget("state")):
                return
            label = str(w.cget("text") or "").strip()
        except tk.TclError:
            return
        if label:
            self.log(f"clicked {label}")

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
        query = (query or "").strip().lower()
        active = len(query) >= 3
        self.lib_tree.delete(*self.lib_tree.get_children())
        selected = self.lib_tree.selection()

        font = tkfont.nametofont("TkDefaultFont", root=self)
        widest = 0
        for author in sorted(self._library):
            author_hit = active and query in author.lower()
            paths = [p for p in self._library[author]
                     if (not active or author_hit or query in _title_of(p))
                     and self._chip_allows(p)]
            if not paths:
                continue
            author_id = self.lib_tree.insert("", "end",
                                             text=author, open=active)
            widest = max(widest, font.measure(author))
            for path in paths:
                label = _book_label(path, self._library[author])
                marks = book_marks(path)
                if marks:
                    label = f"{marks} {label}"
                self.lib_tree.insert(author_id, "end", iid=str(path),
                                     text=label)
                widest = max(widest, font.measure(label) + 20)
        # wider than the pane -> the horizontal scrollbar reaches the rest
        self.lib_tree.column("#0", minwidth=widest + 40, stretch=True)

        if selected and self.lib_tree.exists(selected[0]):
            self.lib_tree.selection_set(selected[0])

    def _apply_search(self):
        self._render_library(self.search_var.get())

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
            if path:
                self.workflow.set_book(path)

    def _path_for(self, iid) -> str | None:
        """Leaf ids are the absolute path of the audio file."""
        return iid if os.path.isfile(iid) else None

    # ------------------------------------------------------------ plumbing --
    def _pump(self):
        """Drain runner events and poll the player on the Tk thread."""
        while True:
            try:
                kind, job, payload = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
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
        t = self.player.poll()
        self._playback_time = t
        if t is not None:
            self.session.playhead = t
        self.player_bar.update_time(t)
        self.workflow.update_playback(t)
        quality_busy = getattr(getattr(self.workflow, "quality", None),
                               "busy", False)
        busy = self.runner.busy or quality_busy
        self.workflow.set_busy(busy)
        self.busy_var.set("busy ◌" if busy else "")
        if self.session.job.running != busy:
            self.session.set_job(busy, "busy" if busy else "")
        self.after(100, self._pump)

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


def main(argv: list[str] | None = None):
    if argv is None:
        argv = sys.argv[1:]
    library_root = next((a for a in argv if not a.startswith("-")), None)
    app = App(library_root=library_root)
    app.mainloop()


if __name__ == "__main__":
    main()
