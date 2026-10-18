"""Layout checker for screenshots and legibility tests."""
from __future__ import annotations

import tkinter.font as tkfont
from tkinter import ttk

MIN_FONT = 13


def check(root) -> list[str]:
    """Return layout problems (empty list = clean)."""
    problems: list[str] = []

    def walk(w):
        try:
            if not w.winfo_ismapped():
                return
            try:
                if not w.winfo_viewable():
                    return
            except Exception:
                pass
            w.update_idletasks()
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ww, wh = w.winfo_width(), w.winfo_height()
            if ww < 2 or wh < 2:
                # Unlaid-out or scrollable canvas placeholders.
                for c in w.winfo_children():
                    walk(c)
                return
            p = w.master
            if p is not None:
                px, py = p.winfo_rootx(), p.winfo_rooty()
                pw, ph = p.winfo_width(), p.winfo_height()
                if pw >= 2 and ph >= 2:
                    # Scrollable canvases clip children intentionally.
                    scrollable = False
                    ancestor = p
                    while ancestor is not None:
                        name = type(ancestor).__name__.lower()
                        if "scrollable" in name or ancestor.winfo_class() == "Canvas":
                            scrollable = True
                            break
                        ancestor = getattr(ancestor, "master", None)
                    if (not scrollable
                            and (x < px - 1 or y < py - 1
                                 or x + ww > px + pw + 2
                                 or y + wh > py + ph + 2)):
                        problems.append(
                            f"OUTSIDE PARENT  {w!s}  ({x},{y},{ww}x{wh}) "
                            f"parent ({px},{py},{pw}x{ph})")
                cls = w.winfo_class()
                if (cls in ("Label", "TLabel", "Button", "TButton", "Entry")
                        and w.winfo_reqwidth() > ww + 2):
                    problems.append(
                        f"CLIPPED  {w!s}  wants {w.winfo_reqwidth()} has {ww}")
            try:
                size = None
                master = getattr(w, "master", None)
                if master is not None and type(master).__module__.startswith(
                        "customtkinter"):
                    try:
                        cf = master.cget("font")
                        if hasattr(cf, "cget"):
                            size = abs(int(cf.cget("size")))
                        elif isinstance(cf, (tuple, list)) and len(cf) >= 2:
                            size = abs(int(cf[1]))
                    except Exception:
                        size = None
                if size is None:
                    raw = w.cget("font")
                    f = (tkfont.nametofont(raw) if isinstance(raw, str)
                         else tkfont.Font(font=raw))
                    size = abs(f.actual("size"))
                if size < MIN_FONT:
                    problems.append(f"FONT TOO SMALL  {w!s}  size={size}")
            except Exception:
                pass
            if isinstance(w, ttk.Treeview) and ww >= 2:
                f = tkfont.nametofont("TkDefaultFont")
                try:
                    f = tkfont.Font(font=ttk.Style().lookup("Treeview", "font"))
                except Exception:
                    pass
                for col in w["columns"]:
                    width = int(w.column(col, "width"))
                    head = w.heading(col, "text")
                    values = [f.measure(str(head)) + 16]
                    for i in w.get_children():
                        values.append(f.measure(str(w.set(i, col))) + 16)
                    widest = max(values) if values else 0
                    stretch = w.column(col, "stretch")
                    if widest > width and not stretch:
                        problems.append(
                            f"COLUMN TOO NARROW  {w!s}.{col}  "
                            f"needs {widest} has {width}")
        except Exception:
            pass
        for c in w.winfo_children():
            walk(c)

    walk(root)
    return problems
