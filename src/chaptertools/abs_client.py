"""Audiobookshelf client and finalize helpers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from chaptertools.envelope.artifact import Artifact, __version__
from chaptertools.nfo import write_album_nfo
from chaptertools.util import die, hms, need
from chaptertools.util import q as _q

ENV_KEYS = ("ABS_URL", "ABS_TOKEN", "ABS_LIBRARY_ID", "ABS_LIBRARY_NAME")


def load_env(explicit: str | None, work_dir: str | None, audio: str) -> dict:
    """Read ABS settings from the first .env found, nearest first."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if work_dir:
        candidates.append(os.path.join(work_dir, ".env"))
    candidates += [
        os.path.join(os.path.dirname(os.path.abspath(audio)), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.config/chapterfit/.env"),
    ]
    env = {}
    used = None
    for c in candidates:
        if not c or not os.path.isfile(c):
            continue
        try:
            mode = os.stat(c).st_mode
            if mode & 0o044:
                print(f"  ! {c} is readable by other users; it holds an API "
                      f"token.\n    chmod 600 it.")
            with open(c) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            used = c
            break
        except OSError:
            continue
    # environment variables win over the file
    for k in ENV_KEYS:
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["_source"] = used or "(environment only)"
    return env


def mask(tok: str | None) -> str:
    if not tok:
        return "(none)"
    return tok[:6] + "..." + tok[-4:] if len(tok) > 14 else "***"


class ABS:
    """Minimal Audiobookshelf client. Read-mostly; the one destructive call
    is delete_item, which removes the database row and leaves files alone."""

    def __init__(self, url: str, token: str, verbose=False, timeout=30):
        self.url = url.rstrip("/")
        self.token = token
        self.verbose = verbose
        self.timeout = timeout

    def _req(self, method: str, path: str, body=None):
        url = f"{self.url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        })
        if self.verbose:
            print(f"    {method} {path}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                try:
                    return json.loads(raw) if raw.strip() else {}
                except ValueError:
                    # some ABS endpoints reply 2xx with a non-JSON body
                    # (e.g. plain-text scan confirmations)
                    if self.verbose:
                        print(f"    (non-JSON response: {raw[:120]!r})")
                    return {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"cannot reach {self.url}: {e.reason}")

    def ping(self):
        return self._req("GET", "/ping")

    def libraries(self):
        r = self._req("GET", "/api/libraries")
        return r.get("libraries", r if isinstance(r, list) else [])

    def library(self, lid):
        r = self._req("GET", f"/api/libraries/{lid}")
        return r.get("library", r)

    def library_items(self, lid, limit=500):
        out, page = [], 0
        while page < 40:
            r = self._req("GET",
                          f"/api/libraries/{lid}/items?limit={limit}&page={page}")
            results = r.get("results", [])
            out += results
            if len(results) < limit:
                break
            page += 1
        return out

    def item(self, iid, expanded=True):
        q = "?expanded=1" if expanded else ""
        return self._req("GET", f"/api/items/{iid}{q}")

    def scan_item(self, iid):
        return self._req("POST", f"/api/items/{iid}/scan", body={})

    def scan_library(self, lid, force=False):
        q = "?force=1" if force else ""
        return self._req("POST", f"/api/libraries/{lid}/scan{q}", body={})

    def delete_item(self, iid):
        # Documented as: deletes the item from the database, no files deleted.
        return self._req("DELETE", f"/api/items/{iid}")

    def progress(self, iid):
        try:
            return self._req("GET", f"/api/me/progress/{iid}")
        except RuntimeError:
            return None

    def set_progress(self, iid, payload):
        return self._req("PATCH", f"/api/me/progress/{iid}", body=payload)

    def set_chapters(self, iid, chapters):
        return self._req("PATCH", f"/api/items/{iid}/media",
                         body={"chapters": chapters})


def item_audio_path(it) -> str | None:
    media = it.get("media") or {}
    files = media.get("audioFiles") or []
    if files:
        meta = files[0].get("metadata") or {}
        return meta.get("path") or files[0].get("path")
    return None


def item_duration(it) -> float:
    media = it.get("media") or {}
    if media.get("duration"):
        return float(media["duration"])
    total = 0.0
    for f in media.get("audioFiles") or []:
        total += float(f.get("duration") or 0)
    return total


def item_chapter_count(it) -> int:
    return len(((it.get("media") or {}).get("chapters")) or [])


def find_item(abs_api: ABS, lid: str, audio: str, title_hint: str | None):
    """Locate the library item by path first, then by title. Never guess."""
    target = os.path.abspath(audio)
    folder = os.path.dirname(target)
    items = abs_api.library_items(lid)
    by_path, by_folder, by_title = [], [], []
    for it in items:
        p = item_audio_path(it) or ""
        ipath = it.get("path") or ""
        if p and os.path.abspath(p) == target:
            by_path.append(it)
        elif ipath and os.path.abspath(ipath) == folder:
            by_folder.append(it)
        else:
            t = ((it.get("media") or {}).get("metadata") or {}).get("title") or ""
            if title_hint and t and title_hint.lower() in t.lower():
                by_title.append(it)
    for label, group in (("path", by_path), ("folder", by_folder),
                         ("title", by_title)):
        if len(group) == 1:
            return group[0], label
        if len(group) > 1:
            print(f"  {len(group)} items matched by {label}:")
            for it in group:
                t = ((it.get("media") or {}).get("metadata") or {}).get("title")
                print(f"    {it.get('id')}  {t}  {it.get('path')}")
            die("ambiguous match - pass --item-id to choose")
    return None, None


def verify_embedded(path: str, expect_dur: float | None):
    """The embedded copy must be sound before the original is displaced."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters",
                        "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode:
        die(f"ffprobe cannot read {path}")
    j = json.loads(r.stdout)
    chapters = j.get("chapters") or []
    dur = float((j.get("format") or {}).get("duration") or 0)
    if len(chapters) < 2:
        die(f"{os.path.basename(path)} has {len(chapters)} chapter(s) - "
            f"run 'emit --embed' first")
    if expect_dur and abs(dur - expect_dur) > 2.0:
        die(f"{os.path.basename(path)} is {hms(dur)} but the analysis measured "
            f"{hms(expect_dur)} - these are not the same audio")
    return len(chapters), dur


AUDIO_EXT = (".mp3", ".m4a", ".m4b", ".opus", ".ogg", ".flac", ".aac", ".wav",
             ".wma", ".mp4")


def classify_folder(folder: str, keep: str):
    """What in this folder should move, and what should stay."""
    move_audio, move_side, stay = [], [], []
    keep_abs = os.path.abspath(keep)
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if os.path.abspath(full) == keep_abs:
            continue
        low = name.lower()
        if low.endswith(AUDIO_EXT):
            move_audio.append(full)
        elif (low.endswith(".orig") or low.endswith(".analysis.json")
              or low.endswith(".envelope.i8") or low.endswith(".silences.json")
              or low.endswith(".ffmeta.tmp")):
            move_side.append(full)
        else:
            stay.append(full)
    return move_audio, move_side, stay


def wait_for_scan(abs_api: ABS, lid: str, baseline, timeout=900, poll=5):
    """A library scan is asynchronous; the server stamps lastScan when done."""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(poll)
        try:
            lib = abs_api.library(lid)
        except RuntimeError:
            continue
        cur = lib.get("lastScan")
        if cur and cur != baseline:
            return True
        sys.stdout.write(".")
        sys.stdout.flush()
    return False


def item_is_correct(it, expect_dur, want_chapters=True):
    """Does ABS now agree with the file on disk?"""
    files = ((it.get("media") or {}).get("audioFiles")) or []
    dur = item_duration(it)
    ch = item_chapter_count(it)
    problems = []
    if len(files) != 1:
        problems.append(f"{len(files)} audio files (expected 1)")
    if expect_dur and abs(dur - expect_dur) > 5.0:
        problems.append(f"duration {hms(dur)} != {hms(expect_dur)}")
    if want_chapters and ch < 2:
        problems.append(f"{ch} chapters")
    return (not problems), {"files": len(files), "duration": dur,
                            "chapters": ch, "problems": problems}


def cmd_finalize(args):
    need("ffprobe")
    print(f"\n=== chapterfit {__version__} - finalize ===")
    apply = args.apply
    if not apply:
        print("  DRY RUN - nothing will be changed. Add --apply to act.\n")

    # ---- 1. work out which file is the source and which name it takes ---
    given = os.path.abspath(args.audio)
    folder = os.path.dirname(given)
    root, ext = os.path.splitext(given)
    if root.endswith(".chapters"):
        final = root[:-len(".chapters")] + ext
        default_src = given
    else:
        final = given
        default_src = f"{root}.chapters{ext}"
    src = os.path.abspath(args.chaptered) if args.chaptered else None
    if not src:
        for cand in (default_src, given):
            if os.path.isfile(cand):
                src = cand
                break
    if not src or not os.path.isfile(src):
        die(f"no chaptered file found (looked for "
            f"{os.path.basename(default_src)}). Run 'emit --embed' first, "
            f"or pass --chaptered.")

    # The analysis may have been run against either name - the original the
    # user works with, or the embedded copy. Prefer whichever actually has a
    # completed alignment rather than assuming.
    art = None
    for cand in (final, src, given):
        if not os.path.isfile(cand):
            continue
        a = Artifact(cand, args.work_dir)
        if a.data.get("alignment"):
            art = a
            break
    if art is None:
        art = Artifact(src if os.path.isfile(src) else given, args.work_dir)
    al = art.require("alignment", "chapterfit.py align FILE")
    measured = art.data["envelope"]["duration"]
    audio = final
    nch, dur = verify_embedded(src, measured)
    print(f"  chaptered file : {os.path.basename(src)}")
    print(f"                   {nch} chapters, {hms(dur)} - verified")

    # ---- 2. what moves ---------------------------------------------------
    move_audio, move_side, stay = classify_folder(folder, src)
    stage = args.stage or os.path.join(
        "/tmp/chapterfit",
        os.path.basename(os.path.dirname(folder)) or "unknown",
        os.path.basename(folder))
    print(f"\n  staging dir    : {stage}")
    if move_audio:
        print("  extra audio (this is what makes ABS merge books):")
        for f in move_audio:
            print(f"      move  {os.path.basename(f)}")
    if move_side:
        print("  analysis sidecars:")
        for f in move_side:
            print(f"      move  {os.path.basename(f)}")
    for f in stay:
        print(f"      keep  {os.path.basename(f)}")

    if os.path.abspath(src) != os.path.abspath(final):
        print(f"      rename {os.path.basename(src)} -> "
              f"{os.path.basename(final)}")

    if apply:
        os.makedirs(stage, exist_ok=True)
        for f in move_audio + move_side:
            dest = os.path.join(stage, os.path.basename(f))
            shutil.move(f, dest)
        if os.path.abspath(src) != os.path.abspath(final):
            if os.path.exists(final):
                shutil.move(final, os.path.join(stage,
                                                os.path.basename(final)))
            shutil.move(src, final)
        # the service user has to be able to read it
        if args.group:
            try:
                shutil.chown(final, group=args.group)
                os.chmod(final, os.stat(final).st_mode | 0o040)
                print(f"  set group {args.group} and group-read on "
                      f"{os.path.basename(final)}")
            except (OSError, LookupError, PermissionError) as e:
                print(f"  ! could not set group {args.group}: {e}")
        left = [f for f in os.listdir(folder)
                if f.lower().endswith(AUDIO_EXT)]
        if len(left) != 1:
            die(f"after moving, {len(left)} audio files remain in the folder: "
                f"{left}. Fix this before continuing or ABS will merge them.")
        print(f"\n  folder now holds exactly one audio file: {left[0]}")
        print(f"  reclaim with:  mv {_q(stage)}/* {_q(folder)}/")

    # ---- 2b. Kodi-style album.nfo with one track per chapter -------------
    emitted_tags = (art.data.get("emitted") or {}).get("tags") or {}
    write_album_nfo(folder, final if apply else src, apply=apply,
                    title=emitted_tags.get("title"),
                    artist=emitted_tags.get("artist"))

    # ---- 3. talk to Audiobookshelf --------------------------------------
    env = load_env(args.env, args.work_dir, audio)
    url, token = env.get("ABS_URL"), env.get("ABS_TOKEN")
    if not url or not token:
        print(f"\n  no ABS_URL/ABS_TOKEN (looked in {env['_source']}).")
        print("  Files are in place; trigger the scan yourself in the UI.")
        return
    print(f"\n  server   : {url}   token {mask(token)}   "
          f"from {env['_source']}")
    api = ABS(url, token, args.verbose)
    try:
        api.ping()
    except RuntimeError as e:
        die(str(e))

    lid = args.library_id or env.get("ABS_LIBRARY_ID")
    if not lid:
        name = args.library_name or env.get("ABS_LIBRARY_NAME")
        libs = api.libraries()
        if name:
            hit = [l for l in libs if (l.get("name") or "").lower() == name.lower()]
            if len(hit) != 1:
                die(f"{len(hit)} libraries named {name!r}; set ABS_LIBRARY_ID")
            lid = hit[0]["id"]
        elif len(libs) == 1:
            lid = libs[0]["id"]
        else:
            print("  libraries:")
            for l in libs:
                print(f"    {l.get('id')}  {l.get('name')}")
            die("set ABS_LIBRARY_ID or ABS_LIBRARY_NAME")
    print(f"  library  : {lid}")

    title_hint = ((art.data.get("identification") or {})
                  .get("meta") or {}).get("title") \
        or os.path.basename(os.path.dirname(audio))
    if args.item_id:
        it, how = api.item(args.item_id), "id"
    else:
        it, how = find_item(api, lid, final, title_hint)
    if not it:
        print("  item not found in the library - it may be new. "
              "Running a library scan.")
        if apply:
            base = api.library(lid).get("lastScan")
            api.scan_library(lid)
            wait_for_scan(api, lid, base, args.timeout)
            it, how = find_item(api, lid, final, title_hint)
        if not it:
            print("  still not found; check the library folder covers this path.")
            return
    iid = it.get("id")
    print(f"  item     : {iid} (matched by {how})")

    ok, st = item_is_correct(it, measured, not args.no_chapters)
    print(f"  state    : {st['files']} file(s), {hms(st['duration'])}, "
          f"{st['chapters']} chapters"
          + ("  OK" if ok else "  -> " + "; ".join(st["problems"])))
    if ok:
        print("\n  Already correct. Nothing to do.")
        return
    if not apply:
        print("\n  DRY RUN: would now escalate until ABS agrees -\n"
              "    1. item scan   2. library scan   3. forced library scan"
              + ("   4. delete and re-add" if args.allow_delete
                 else "   (delete not permitted; add --allow-delete)"))
        return

    # ---- 4. escalate, cheapest first ------------------------------------
    def recheck():
        time.sleep(2)
        cur = api.item(iid)
        good, s = item_is_correct(cur, measured, not args.no_chapters)
        print(f"    -> {s['files']} file(s), {hms(s['duration'])}, "
              f"{s['chapters']} chapters")
        return good, cur

    print("\n  [1] item scan")
    try:
        r = api.scan_item(iid)
        print(f"    result {r.get('result')}")
    except RuntimeError as e:
        print(f"    failed: {e}")
    good, it = recheck()
    if good:
        return finalize_done(api, iid, art, args)

    for label, force in (("[2] library scan", False),
                         ("[3] forced library scan", True)):
        print(f"\n  {label}")
        base = api.library(lid).get("lastScan")
        api.scan_library(lid, force=force)
        if not wait_for_scan(api, lid, base, args.timeout):
            print("    scan did not report completion within the timeout")
        good, it = recheck()
        if good:
            return finalize_done(api, iid, art, args)

    # ---- 5. last resort --------------------------------------------------
    if not args.allow_delete:
        print("""
  ABS still disagrees with the file on disk. The remaining option is to
  delete the library item and let it be re-added - the "Plex dance". It
  clears listening progress for the item, so it is opt-in:

      ... finalize ... --apply --allow-delete""")
        return

    prog = api.progress(iid)
    if prog and prog.get("progress"):
        print(f"\n  ! this item has listening progress "
              f"({float(prog.get('progress', 0))*100:.1f}%). Deleting clears it.")
        saved = os.path.join(args.work_dir or folder, f"progress.{iid}.json")
        try:
            with open(saved, "w") as f:
                json.dump(prog, f, indent=1)
            print(f"    saved to {saved}; will restore after re-add")
        except OSError:
            saved = None
        if not args.yes and sys.stdin.isatty():
            if input("    proceed with delete? (y/n) ").strip().lower() != "y":
                print("    stopped.")
                return
    else:
        saved = None

    print("\n  [4] delete and re-add")
    api.delete_item(iid)
    print("    deleted from the database (no files touched)")
    base = api.library(lid).get("lastScan")
    api.scan_library(lid, force=True)
    wait_for_scan(api, lid, base, args.timeout)
    new, how = find_item(api, lid, final, title_hint)
    if not new:
        die("the item did not come back - run a library scan in the UI")
    iid = new["id"]
    print(f"    re-added as {iid}")
    good, s = item_is_correct(new, measured, not args.no_chapters)
    print(f"    -> {s['files']} file(s), {hms(s['duration'])}, "
          f"{s['chapters']} chapters")

    if saved and os.path.exists(saved):
        try:
            with open(saved) as f:
                prev = json.load(f)
            api.set_progress(iid, {
                "currentTime": prev.get("currentTime", 0),
                "progress": prev.get("progress", 0),
                "isFinished": prev.get("isFinished", False),
            })
            print("    restored listening progress")
        except (OSError, ValueError, RuntimeError) as e:
            print(f"    ! could not restore progress: {e}")
    finalize_done(api, iid, art, args)


def finalize_done(api, iid, art, args):
    print("\n  Audiobookshelf agrees with the file on disk.")
    it = api.item(iid)
    if item_chapter_count(it) < 2 and not args.no_chapters:
        al = art.data.get("alignment") or {}
        chapters = [
            {"id": i, "start": r["time"], "end": None, "title": r["title"]}
            for i, r in enumerate(r2 for r2 in al.get("results", [])
                                  if r2.get("time") is not None)]
        print(f"  Chapters are still empty. {len(chapters)} are available "
              f"from the analysis;\n  push them with --push-chapters, or use "
              f"the chapter editor's bulk import.")
        if args.push_chapters and chapters:
            dur = art.data["envelope"]["duration"]
            for i, c in enumerate(chapters):
                c["end"] = (chapters[i + 1]["start"]
                            if i + 1 < len(chapters) else dur)
            try:
                api.set_chapters(iid, chapters)
                print(f"  pushed {len(chapters)} chapters over the API")
            except RuntimeError as e:
                print(f"  ! chapter push failed: {e}")
    return


# ==========================================================================
# listen - find chapter announcements by transcribing only what matters
# ==========================================================================
#
