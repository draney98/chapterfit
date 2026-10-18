"""Dev-only window capture for GUI review screenshots."""
from __future__ import annotations

import argparse
import contextlib
import json
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
        "--appearance", default=None, choices=("light", "dark", "Light", "Dark"),
        help="start in this appearance (does not change saved settings)")
    parser.add_argument(
        "--settle-ms", type=int, default=800,
        help="ms to wait after layout before capture")
    parser.add_argument(
        "--allow-busy", action="store_true",
        help="capture even when a job is still running")
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
    x = int(app.winfo_rootx())
    y = int(app.winfo_rooty())
    width = int(app.winfo_width())
    height = int(app.winfo_height())
    if width < 2 or height < 2:
        raise RuntimeError("window has no size yet")
    wid = hex(int(app.winfo_id()))
    try:
        subprocess.run(
            ["import", "-window", wid, str(out)],
            check=True, capture_output=True, text=True, timeout=15)
        return
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as magick_err:
        try:
            from PIL import ImageGrab
            image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
            image.save(out)
            return
        except Exception as pillow_err:
            raise SystemExit(
                f"screenshot failed (ImageMagick: {magick_err}; "
                f"PIL: {pillow_err})"
            ) from pillow_err


def _require_png_size(path: str, width: int, height: int) -> None:
    from PIL import Image

    with Image.open(path) as image:
        got_w, got_h = image.size
    if (got_w, got_h) != (width, height):
        raise SystemExit(f"asked {width}x{height}, got {got_w}x{got_h}")


def _wait_ready(app, width: int, height: int, allow_busy: bool) -> None:
    """Block until the window is the requested size and, unless allowed, idle.

    A timeout never captures. The status text is the reason.
    """
    import time

    deadline = time.monotonic() + 60
    geometry = f"{width}x{height}+0+0"
    while True:
        app.geometry(geometry)
        app.update_idletasks()
        app.realize_geometry()
        app._pump(again=False)
        sized = (app.winfo_width() == width and app.winfo_height() == height)
        idle = allow_busy or app.is_idle()
        if sized and idle:
            return
        if time.monotonic() >= deadline:
            if not allow_busy and not app.is_idle():
                status = app.player_bar.status_var.get()
                raise SystemExit(f"not idle after 60 s: {status}")
            raise SystemExit(
                f"asked {width}x{height}, "
                f"got {app.winfo_width()}x{app.winfo_height()}")
        time.sleep(0.05)


def run_screenshot(argv: list[str]) -> int:
    """Open the GUI, load book/step, capture, exit."""
    args = parse_screenshot_args(argv)
    width, height = _parse_size(args.size)
    step = STEP_ALIASES[args.step]

    from chaptertools.gui.app import App

    library = args.library or os.environ.get("CHAPTERFIT_LIBRARY")
    if args.appearance:
        import tempfile
        cfg = tempfile.mkdtemp(prefix="chapterfit-shot-")
        os.environ["CHAPTERFIT_CONFIG"] = cfg
        name = "Dark" if args.appearance.lower() == "dark" else "Light"
        Path(cfg, "gui.json").write_text(
            json.dumps({"appearance": name}), encoding="utf-8")
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
            _wait_ready(app, width, height, args.allow_busy)
            app.workflow._refresh()
            app.update_idletasks()
            app.realize_geometry()
            if (app.winfo_width() != width or app.winfo_height() != height):
                raise SystemExit(
                    f"asked {width}x{height}, "
                    f"got {app.winfo_width()}x{app.winfo_height()}")
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
            _require_png_size(args.screenshot, width, height)
        except BaseException as exc:  # noqa: BLE001 — surface to caller
            result["err"] = exc
            text = str(exc).strip() or exc.__class__.__name__
            print(text, file=sys.stderr)
        # quit() does not return while idle timers keep the queue busy.
        os._exit(1 if result["err"] is not None else 0)

    app.after(max(0, args.settle_ms), _capture)
    app.mainloop()
    with contextlib.suppress(Exception):
        app.destroy()
    if result["err"] is not None:
        raise SystemExit(str(result["err"]))
    print(args.screenshot, file=sys.stderr)
    return 0
