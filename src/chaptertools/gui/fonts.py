"""Process-lifetime derived fonts, per Tk interpreter.

tkinter Font objects delete their underlying Tk font when garbage
collected, so a font created as a local and handed to a style/widget
goes dangling once the local dies. Derived fonts therefore live in a
cache keyed by the interpreter's root widget (tests create and destroy
Tk instances; a destroyed root's fonts drop out automatically).

Sizes are relative (`grow`), so `refresh()` after the base size changes
keeps every derived font in step with it.
"""
from __future__ import annotations

import tkinter.font as tkfont
import weakref

_by_root: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def derived(widget, base: str = "TkDefaultFont", grow: int = 0,
            **kw) -> tkfont.Font:
    """A copy of `base`, `grow` points larger, with `kw` applied, alive as
    long as the root."""
    root = widget.winfo_toplevel()
    per = _by_root.setdefault(root, {})
    key = (base, grow, tuple(sorted(kw.items())))
    f = per.get(key)
    if f is None:
        f = tkfont.nametofont(base, root=root).copy()
        per[key] = f
        _apply(root, f, key)
    return f


def _apply(root, f: tkfont.Font, key) -> None:
    base, grow, kw = key
    # configured options, not actual(): actual sizes are rounded from pixels
    src = tkfont.nametofont(base, root=root).configure()
    f.configure(**{**src, **dict(kw)})
    if grow:
        f.configure(size=int(src["size"]) + grow)


def refresh(widget) -> None:
    """Re-derive every cached font from its (possibly resized) base."""
    root = widget.winfo_toplevel()
    for key, f in (_by_root.get(root) or {}).items():
        _apply(root, f, key)
