#!/usr/bin/env python3
"""
chaptergui.py - Tkinter front end for chapterfit.

Browse the media library, then drive the chapterfit stages
(measure / identify / align / pin / emit / finalize) for a book,
previewing chapter boundaries with mpv.

    chaptergui.py /srv/media/books   # name the library root explicitly
    chaptergui.py                    # else $CHAPTERFIT_LIBRARY, else cwd

Implementation lives in src/chaptertools/gui/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Editable / src layout support when not installed.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from chaptertools.gui.app import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
