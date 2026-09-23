"""Process-lifetime derived fonts, per Tk interpreter.

tkinter Font objects delete their underlying Tk font when garbage
collected, so a font created as a local and handed to a style/widget
goes dangling once the local dies. Derived fonts therefore live in a
cache keyed by the interpreter's root widget (tests create and destroy
Tk instances; a destroyed root's fonts drop out automatically).
"""
from __future__ import annotations

import tkinter.font as tkfont
import weakref

_by_root: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def derived(widget, base: str = "TkDefaultFont", **kw) -> tkfont.Font:
    """A copy of `base` with `kw` applied, alive as long as the root."""
    root = widget.winfo_toplevel()
    per = _by_root.setdefault(root, {})
    key = (base, tuple(sorted(kw.items())))
    f = per.get(key)
    if f is None:
        f = tkfont.nametofont(base, root=root).copy()
        f.configure(**kw)
        per[key] = f
    return f
