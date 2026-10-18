"""Dev-only window capture for GUI review screenshots."""
from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

STEP_ALIASES = {
    "scan": "scan",
    "match": "match",
    "place": "place",
    "finish": "finish",
    "audio": "cleanup",
    "cleanup": "cleanup",
    "librivox": "librivox",
}


def parse_screenshot_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chaptertools.gui",
        description="chaptertools GUI (dev: --screenshot for review PNGs)")
    parser.add_argument(
        "--screenshot", required=True, metavar="PATH",
        help="write a PNG of the window and exit")
    parser.add_argument("--book", default=None, help="audio file or book folder")
    parser.add_argument(
        "--step", default="scan",
        choices=sorted(STEP_ALIASES),
        help="step to show (audio = cleanup)")
    parser.add_argument(
        "--size", default="1600x1030",
        help="window size WxH (default 1600x1030)")
    parser.add_argument(
        "--library", default=None,
        help="library root (default CHAPTERFIT_LIBRARY or cwd)")
    parser.add_argument(
        "--settle-ms", type=int, default=800,
        help="ms to wait after layout before capture")
    return parser.parse_args(argv)


def _parse_size(text: str) -> tuple[int, int]:
    try:
        width_s, height_s = text.lower().split("x", 1)
        width, height = int(width_s), int(height_s)
    except ValueError as exc:
        raise SystemExit(f"bad --size {text!r}; want WxH") from exc
    if width < 320 or height < 240:
        raise SystemExit(f"bad --size {text!r}; too small")
    return width, height


def grab_window(app, path: str) -> None:
    """Capture the app window to PNG (PIL, else ImageMagick import)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    app.update_idletasks()
    app.update()
    x = int(app.winfo_rootx())
    y = int(app.winfo_rooty())
    width = int(app.winfo_width())
    height = int(app.winfo_height())
    if width < 2 or height < 2:
        raise RuntimeError("window has no size yet")
    bbox = (x, y, x + width, y + height)
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab(bbox=bbox)
        image.save(out)
        return
    except Exception as pillow_err:
        wid = hex(int(app.winfo_id()))
        try:
            subprocess.run(
                ["import", "-window", wid, str(out)],
                check=True, capture_output=True, text=True)
            return
        except (OSError, subprocess.CalledProcessError) as magick_err:
            raise SystemExit(
                f"screenshot failed (PIL: {pillow_err}; "
                f"ImageMagick: {magick_err})"
            ) from magick_err


def run_screenshot(argv: list[str]) -> int:
    """Open the GUI, load book/step, capture, exit."""
    args = parse_screenshot_args(argv)
    width, height = _parse_size(args.size)
    step = STEP_ALIASES[args.step]

    from chaptertools.gui.app import App

    library = args.library or os.environ.get("CHAPTERFIT_LIBRARY")
    app = App(library_root=library)
    app.geometry(f"{width}x{height}")
    app.update_idletasks()

    if args.book:
        book = os.path.abspath(args.book)
        if not os.path.exists(book):
            app.destroy()
            raise SystemExit(f"book not found: {book}")
        app.workflow.set_book(book)
        app.update_idletasks()

    if step == "librivox":
        app._open_librivox()
    else:
        if getattr(app, "librivox_view", None) is not None:
            app._show_librivox(False)
        app.workflow.show_step(step)
    app.update_idletasks()

    result: dict[str, BaseException | None] = {"err": None}

    def _capture() -> None:
        try:
            # Wait out set_book autoscan and the async harvest (NFO rows).
            import time
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                app.update()
                harvest = getattr(app.workflow, "harvest", None) or {}
                ready = (not app.runner.busy
                         and not harvest.get("loading", False))
                if args.book and not harvest:
                    ready = False
                if ready:
                    break
                time.sleep(0.05)
            app.workflow._refresh()
            app.update_idletasks()
            from chaptertools.gui.debug_layout import check
            target = getattr(getattr(app, "workflow", None), "workspace", None)
            problems = check(target or app)
            for problem in problems:
                print(problem, file=sys.stderr)
            if problems:
                raise SystemExit(
                    f"debug_layout: {len(problems)} problem(s) "
                    f"before screenshot")
            grab_window(app, args.screenshot)
        except BaseException as exc:  # noqa: BLE001 — surface to caller
            result["err"] = exc
        finally:
            app.quit()

    app.after(max(0, args.settle_ms), _capture)
    app.mainloop()
    with contextlib.suppress(Exception):
        app.destroy()
    if result["err"] is not None:
        raise SystemExit(str(result["err"]))
    print(args.screenshot, file=sys.stderr)
    return 0
