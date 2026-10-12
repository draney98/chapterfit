"""LibriVox catalog search and Archive.org section fetch.

Imported only by the ``chapterfit librivox`` subcommand. The GUI process
never imports this module; it drives jobs via ``--json`` lines.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date

LV_API = "https://librivox.org/api/feed/audiobooks"
IA_META = "https://archive.org/metadata/"
USER_AGENT = "chapterfit/1.0"
DEFAULT_SETS = ("128Kbps MP3", "VBR MP3", "64Kbps MP3")
_FILENAME_NUM = re.compile(r"(?:^|[_\-\s])(\d{1,3})(?:[_\-\s.]|$)")

Emit = Callable[[dict], None]


def _http_json(url: str, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 ** attempt)
    raise last  # type: ignore[misc]


def _http_download(url: str, dest: str, *, resume: bool = True,
                   on_progress: Callable[[int, int, int], None] | None = None,
                   emit: Emit | None = None) -> dict:
    """Download to dest via .partial with optional HTTP Range resume."""
    partial = dest + ".partial"
    existing = os.path.getsize(partial) if resume and os.path.isfile(partial) else 0
    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    last: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                total_hdr = resp.headers.get("Content-Length")
                # Content-Range: bytes start-end/total
                cr = resp.headers.get("Content-Range") or ""
                total = existing
                if cr and "/" in cr:
                    try:
                        total = int(cr.rsplit("/", 1)[1])
                    except ValueError:
                        total = existing + int(total_hdr or 0)
                elif total_hdr:
                    total = existing + int(total_hdr)
                mode = "ab" if existing and resp.status == 206 else "wb"
                if mode == "wb":
                    existing = 0
                written = existing
                with open(partial, mode) as handle:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(written, total, existing)
            os.replace(partial, dest)
            return {"bytes": written, "total": total or written,
                    "resumed_from": existing}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError) as exc:
            last = exc
            if emit:
                emit({"event": "retry", "attempt": attempt + 1,
                      "error": str(exc)})
            time.sleep(1.5 ** attempt)
    raise last  # type: ignore[misc]


def _parse_playtime(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        total = 0.0
        for part in parts:
            total = total * 60 + part
        return total
    try:
        return float(text)
    except ValueError:
        return 0.0


def _authors(book: dict) -> str:
    authors = book.get("authors") or []
    names = []
    for author in authors:
        if isinstance(author, dict):
            last = (author.get("last_name") or "").strip()
            first = (author.get("first_name") or "").strip()
            names.append(f"{first} {last}".strip() or last or first)
        else:
            names.append(str(author))
    return ", ".join(n for n in names if n)


def _readers(book: dict) -> list[str]:
    seen: list[str] = []
    for section in book.get("sections") or []:
        for reader in section.get("readers") or []:
            name = (reader.get("display_name") or "").strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def _safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", text).strip() or "untitled"
    return text[:180]


def _archive_id(url: str) -> str:
    # https://archive.org/details/pride_and_prejudice_librivox
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else ""


def _filename_number(name: str) -> int | None:
    base = os.path.basename(name)
    # Prefer the first number (section index), not bitrate suffixes.
    matches = _FILENAME_NUM.findall(base)
    if not matches:
        return None
    return int(matches[0])


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_error(message: str) -> dict:
    return {"event": "error", "error": message, "hits": [], "unfinished": []}


def _author_matches(book: dict, needle: str) -> bool:
    needle = needle.casefold()
    blob = _authors(book).casefold()
    if needle in blob:
        return True
    for author in book.get("authors") or []:
        if not isinstance(author, dict):
            continue
        last = (author.get("last_name") or "").casefold()
        if needle == last or last.startswith(needle):
            return True
    return False


def search_catalog(title: str, author: str | None = None) -> dict:
    """Return ``{"hits": [...], "unfinished": [...]}`` from LibriVox.

    Title and author must not go in the same catalog request: that
    combination 500s on librivox.org. Author is applied here after the
    title (or author-only) fetch.
    """
    title = (title or "").strip()
    author = (author or "").strip() or None
    params = {"format": "json", "extended": "1"}
    if title:
        params["title"] = f"^{title}"
    elif author:
        params["author"] = author
    else:
        return {"hits": [], "unfinished": []}
    url = f"{LV_API}/?" + urllib.parse.urlencode(params)
    try:
        payload = _http_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"hits": [], "unfinished": []}
        return _search_error(f"LibriVox HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return _search_error(f"could not reach LibriVox: {exc}")
    books = payload.get("books") or []
    if isinstance(books, dict):
        books = [books]
    if title and author:
        books = [b for b in books if _author_matches(b, author)]
    hits: list[dict] = []
    unfinished: list[dict] = []
    for book in books:
        row = {
            "id": int(book.get("id") or 0),
            "title": (book.get("title") or "").strip(),
            "author": _authors(book),
            "language": (book.get("language") or "").strip(),
        }
        sections = book.get("sections") or []
        archive = (book.get("url_iarchive") or "").strip()
        if not archive or not sections:
            unfinished.append(row)
            continue
        readers = _readers(book)
        duration = sum(_parse_playtime(s.get("playtime")) for s in sections)
        hits.append({
            **row,
            "readers": readers,
            "duration": duration,
            "sections": len(sections),
        })
    return {"hits": hits, "unfinished": unfinished}


def print_search(result: dict, *, as_json: bool) -> int:
    failed = result.get("event") == "error"
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return 1 if failed else 0
    if failed:
        print(f"LibriVox search failed: {result.get('error')}")
        return 1
    hits = result["hits"]
    unfinished = result["unfinished"]
    if hits:
        print("Downloadable:")
        for hit in hits:
            readers = hit.get("readers") or []
            if len(readers) > 2:
                who = f"{len(readers)} readers"
            elif readers:
                who = ", ".join(readers)
            else:
                who = "—"
            mins = int(round((hit.get("duration") or 0) / 60))
            print(f"  {hit['id']:>6}  {hit['title']} — {hit['author']}  "
                  f"[{who}]  {mins} min  {hit.get('sections', 0)} sections  "
                  f"{hit.get('language') or ''}")
    if unfinished:
        print("Unfinished (not downloadable yet):")
        for row in unfinished:
            print(f"  {row['id']:>6}  {row['title']} — {row['author']}  "
                  f"{row.get('language') or ''}")
    if not hits and not unfinished:
        print("No matches.")
    return 0


# ---------------------------------------------------------------------------
# Archive metadata / plan
# ---------------------------------------------------------------------------

def _lv_book(lv_id: int | str) -> dict:
    url = f"{LV_API}/?" + urllib.parse.urlencode({
        "format": "json", "extended": "1", "id": str(lv_id)})
    payload = _http_json(url)
    books = payload.get("books") or []
    if isinstance(books, dict):
        books = [books]
    if not books:
        raise ValueError(f"LibriVox id {lv_id} not found")
    return books[0]


def _ia_metadata(identifier: str) -> dict:
    return _http_json(IA_META + urllib.parse.quote(identifier))


def _set_name_from_m3u_format(fmt: str) -> str:
    set_name = fmt.replace("M3U", "MP3").replace("m3u", "MP3")
    if "VBR" in fmt.upper() and "MP3" not in set_name.upper():
        set_name = "VBR MP3"
    return set_name


def _parse_m3u_names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Absolute Archive URLs or bare filenames
        names.append(os.path.basename(urllib.parse.urlparse(line).path or line))
    return names


def _fetch_playlist_names(identifier: str, playlist_name: str) -> list[str]:
    url = (f"https://archive.org/download/{identifier}/"
           f"{urllib.parse.quote(playlist_name)}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return _parse_m3u_names(resp.read().decode("utf-8", errors="replace"))


def _set_formats(files: list[dict], *, identifier: str | None = None,
                 playlist_bodies: dict[str, list[str]] | None = None
                 ) -> dict[str, list[str]]:
    """Map audio set name -> list of source filenames in playlist order.

    ``playlist_bodies`` maps set name -> ordered filenames (for tests).
    When omitted and ``identifier`` is set, each M3U is fetched once.
    """
    playlists: dict[str, str] = {}
    audio_by_format: dict[str, list[str]] = {}
    for entry in files:
        fmt = (entry.get("format") or "").strip()
        name = entry.get("name") or ""
        if not name:
            continue
        lower = fmt.lower()
        if "m3u" in lower:
            playlists[_set_name_from_m3u_format(fmt)] = name
        elif "mp3" in lower and "zip" not in lower:
            audio_by_format.setdefault(fmt, []).append(name)
    ordered: dict[str, list[str]] = {}
    for set_name, playlist_name in playlists.items():
        names: list[str] = []
        if playlist_bodies and set_name in playlist_bodies:
            names = list(playlist_bodies[set_name])
        elif identifier:
            try:
                names = _fetch_playlist_names(identifier, playlist_name)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    OSError):
                names = []
        if not names:
            candidates = audio_by_format.get(set_name) or []
            if not candidates:
                key = set_name.lower().replace(" ", "")
                for fmt, cand in audio_by_format.items():
                    if fmt.lower().replace(" ", "") == key:
                        candidates = cand
                        break
            names = sorted(candidates)
        ordered[set_name] = names
    for fmt, names in audio_by_format.items():
        if fmt not in ordered and names:
            ordered[fmt] = sorted(names)
    return ordered


def _pick_set(available: dict[str, list[str]], wanted: str | None) -> str:
    if wanted:
        if wanted in available:
            return wanted
        # case-insensitive
        for name in available:
            if name.lower() == wanted.lower():
                return name
        raise ValueError(
            f"set {wanted!r} not available; have: {', '.join(available)}")
    for name in DEFAULT_SETS:
        if name in available:
            return name
        for have in available:
            if have.lower() == name.lower():
                return have
    if not available:
        raise ValueError("no MP3 sets on this Archive item")
    return next(iter(available))


def _sections_sorted(book: dict) -> list[dict]:
    sections = list(book.get("sections") or [])
    sections.sort(key=lambda s: int(s.get("section_number") or 0))
    return sections


def build_plan(lv_id: int | str, library: str, set_name: str | None = None,
               *, book: dict | None = None, ia: dict | None = None) -> dict:
    """Build a fetch plan or a refused event. Never downloads.

    When ``book`` and ``ia`` are both supplied (fixture / offline), M3U
    bodies are not fetched; file order falls back to sorted names unless
    ``ia["_playlists"]`` provides them.
    """
    injected = book is not None and ia is not None
    book = book or _lv_book(lv_id)
    archive_url = (book.get("url_iarchive") or "").strip()
    sections = _sections_sorted(book)
    if not archive_url or not sections:
        return {
            "event": "refused",
            "reason": "unfinished",
            "detail": "no Archive URL or no sections",
        }
    identifier = _archive_id(archive_url)
    ia = ia or _ia_metadata(identifier)
    files = ia.get("files") or []
    playlist_bodies = ia.get("_playlists") if isinstance(ia, dict) else None
    available = _set_formats(
        files,
        identifier=None if injected else identifier,
        playlist_bodies=playlist_bodies)
    try:
        chosen = _pick_set(available, set_name)
    except ValueError as exc:
        return {"event": "refused", "reason": "set", "detail": str(exc)}
    playlist = available[chosen]
    section_count = len(sections)
    playlist_count = len(playlist)
    if playlist_count != section_count:
        return {
            "event": "refused",
            "reason": "count_mismatch",
            "playlist_count": playlist_count,
            "section_count": section_count,
            "set": chosen,
            "sets_available": sorted(available),
            "detail": (f"playlist has {playlist_count} files, "
                       f"catalog has {section_count} sections"),
        }
    # Pair playlist order with catalog section_number order; refuse on
    # disagreeing filename numbers.
    catalog_nums = [int(s.get("section_number") or 0) for s in sections]
    file_nums = [_filename_number(name) for name in playlist]
    disagree = []
    for cat, fname, src in zip(catalog_nums, file_nums, playlist, strict=True):
        if fname is not None and fname != cat:
            disagree.append({"catalog": cat, "filename": fname, "source": src})
    if disagree:
        return {
            "event": "refused",
            "reason": "number_mismatch",
            "catalog_numbers": catalog_nums,
            "filename_numbers": file_nums,
            "disagreements": disagree,
            "detail": "filename section numbers disagree with the catalog",
        }

    author = _authors(book) or "Unknown"
    title = (book.get("title") or f"LibriVox {lv_id}").strip()
    dest = os.path.join(os.path.abspath(library), _safe_name(author),
                        _safe_name(title))
    planned_files = []
    for section, src in zip(sections, playlist, strict=True):
        num = int(section.get("section_number") or 0)
        sec_title = (section.get("title") or f"Section {num}").strip()
        nn = f"{num:02d}"
        out_name = f"{nn} - {_safe_name(sec_title)}.mp3"
        readers = [
            (r.get("display_name") or "").strip()
            for r in (section.get("readers") or [])
            if (r.get("display_name") or "").strip()
        ]
        planned_files.append({
            "section_number": num,
            "title": sec_title,
            "reader": readers[0] if len(readers) == 1 else (
                f"{len(readers)} readers" if readers else ""),
            "readers": readers,
            "duration": _parse_playtime(section.get("playtime")),
            "source": src,
            "dest": os.path.join(dest, out_name),
            "url": f"https://archive.org/download/{identifier}/{urllib.parse.quote(src)}",
        })
    return {
        "event": "plan",
        "id": int(book.get("id") or lv_id),
        "title": title,
        "author": author,
        "language": (book.get("language") or "").strip(),
        "set": chosen,
        "sets_available": sorted(available),
        "playlist_count": playlist_count,
        "section_count": section_count,
        "dest": dest,
        "url_librivox": book.get("url_librivox") or "",
        "url_iarchive": archive_url,
        "files": planned_files,
    }


# ---------------------------------------------------------------------------
# Tag + sidecars
# ---------------------------------------------------------------------------

def _tag_file(path: str, *, title: str, album: str, artist: str,
              track: int, track_total: int) -> None:
    """ffmpeg stream-copy tags; no re-encode."""
    import subprocess
    tmp = path + ".tagged.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", path, "-codec", "copy",
        "-metadata", f"title={title}",
        "-metadata", f"album={album}",
        "-metadata", f"artist={artist}",
        "-metadata", f"album_artist={artist}",
        "-metadata", f"track={track}/{track_total}",
        tmp,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.replace(tmp, path)


def _write_sidecars(plan: dict) -> None:
    from chaptertools.nfo import render_nfo

    dest = plan["dest"]
    os.makedirs(dest, exist_ok=True)
    chapters = []
    t = 0.0
    for item in plan["files"]:
        dur = float(item.get("duration") or 0)
        chapters.append({
            "title": item["title"],
            "start": t,
            "end": t + dur,
        })
        t += dur
    nfo_path = os.path.join(dest, "album.nfo")
    existing = None
    if os.path.isfile(nfo_path):
        with open(nfo_path) as handle:
            existing = handle.read()
    text = render_nfo(existing, chapters, plan["title"], plan["author"])
    with open(nfo_path, "w") as handle:
        handle.write(text)

    sections_meta = []
    for item in plan["files"]:
        sections_meta.append({
            "section_number": item["section_number"],
            "title": item["title"],
            "reader": item.get("reader") or "",
            "readers": item.get("readers") or [],
            "duration": item.get("duration") or 0,
            "source": item["source"],
            "file": os.path.basename(item["dest"]),
        })
    meta = {
        "id": plan["id"],
        "title": plan["title"],
        "author": plan["author"],
        "language": plan.get("language") or "",
        "set": plan["set"],
        "fetched": date.today().isoformat(),
        "url_librivox": plan.get("url_librivox") or "",
        "url_iarchive": plan.get("url_iarchive") or "",
        "sections": sections_meta,
    }
    with open(os.path.join(dest, "librivox.json"), "w") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_book(lv_id: int | str, library: str, *, set_name: str | None = None,
               plan_only: bool = False, emit: Emit | None = None,
               book: dict | None = None, ia: dict | None = None,
               download_fn=None) -> int:
    """Fetch one bitrate set. Returns process exit code."""
    def out(obj: dict):
        if emit:
            emit(obj)

    plan = build_plan(lv_id, library, set_name, book=book, ia=ia)
    out(plan)
    if plan.get("event") == "refused":
        return 2
    if plan_only:
        return 0

    download = download_fn or _http_download
    os.makedirs(plan["dest"], exist_ok=True)
    total_files = len(plan["files"])
    for index, item in enumerate(plan["files"], start=1):
        dest = item["dest"]
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            out({"event": "file", "state": "done", "index": index,
                 "total": total_files, "path": dest, "bytes": os.path.getsize(dest),
                 "resumed_from": 0, "skipped": True})
            continue
        out({"event": "file", "state": "downloading", "index": index,
             "total": total_files, "path": dest, "url": item["url"]})

        def progress(written, total, resumed_from, idx=index, path=dest):
            out({"event": "file", "state": "downloading", "index": idx,
                 "total": total_files, "path": path, "bytes": written,
                 "total_bytes": total, "resumed_from": resumed_from})

        try:
            info = download(item["url"], dest, on_progress=progress, emit=out)
        except Exception as exc:  # noqa: BLE001 - surface as failed event
            out({"event": "failed", "index": index, "total": total_files,
                 "path": dest, "error": str(exc)})
            return 1
        out({"event": "file", "state": "done", "index": index,
             "total": total_files, "path": dest, **info})
        try:
            _tag_file(
                dest,
                title=item["title"],
                album=plan["title"],
                artist=plan["author"],
                track=item["section_number"],
                track_total=max(s["section_number"] for s in plan["files"]),
            )
            out({"event": "file", "state": "tagged", "index": index,
                 "total": total_files, "path": dest,
                 "track": item["section_number"]})
        except Exception as exc:  # noqa: BLE001
            out({"event": "failed", "index": index, "path": dest,
                 "error": f"tag failed: {exc}"})
            return 1

    _write_sidecars(plan)
    out({"event": "done", "dest": plan["dest"], "files": total_files,
         "id": plan["id"]})
    return 0


def cmd_search(args) -> int:
    result = search_catalog(args.title, args.author)
    return print_search(result, as_json=bool(args.json))


def cmd_fetch(args) -> int:
    lines: list[dict] = []

    def emit(obj: dict):
        if args.json:
            print(json.dumps(obj, ensure_ascii=False), flush=True)
        else:
            lines.append(obj)
            _print_human(obj)

    return fetch_book(
        args.id, args.library, set_name=args.set,
        plan_only=bool(args.plan_only), emit=emit)


def _print_human(obj: dict) -> None:
    event = obj.get("event")
    if event == "plan":
        print(f"Plan: {obj['title']} — {obj['author']}")
        print(f"  set {obj['set']}  ({obj['playlist_count']} files, "
              f"{obj['section_count']} sections)")
        print(f"  -> {obj['dest']}")
        for item in obj["files"]:
            print(f"  {item['section_number']:02d}  {item['title']}  "
                  f"({item['source']})")
    elif event == "refused":
        print(f"Refused: {obj.get('detail') or obj.get('reason')}")
        if obj.get("reason") == "count_mismatch":
            print(f"  playlist={obj.get('playlist_count')}  "
                  f"catalog={obj.get('section_count')}")
        if obj.get("reason") == "number_mismatch":
            print(f"  catalog: {obj.get('catalog_numbers')}")
            print(f"  files:   {obj.get('filename_numbers')}")
    elif event == "file":
        state = obj.get("state")
        if state == "downloading" and obj.get("bytes") is not None:
            return
        print(f"  [{obj.get('index')}/{obj.get('total')}] {state}  "
              f"{os.path.basename(obj.get('path') or '')}")
    elif event == "retry":
        print(f"  retry {obj.get('attempt')}: {obj.get('error')}")
    elif event == "failed":
        print(f"Failed: {obj.get('error')}")
    elif event == "done":
        print(f"Done: {obj.get('files')} files in {obj.get('dest')}")
