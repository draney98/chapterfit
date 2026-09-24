#!/usr/bin/env python3
"""find_asin.py - compatibility shim: find the Audible edition (ASIN) of a local file.

The implementation is chaptertools.sources.find_asin; `python3 find_asin.py ...` runs it and
`import find_asin` returns that module itself (so patches and globals are shared).
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if __name__ == "__main__":
    import runpy
    runpy.run_module("chaptertools.sources.find_asin", run_name="__main__", alter_sys=True)
else:
    import chaptertools.sources.find_asin as _impl  # noqa: E402
    sys.modules[__name__] = _impl
