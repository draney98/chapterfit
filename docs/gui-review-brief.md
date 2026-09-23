# GUI review brief — transition document for a reviewing AI

You are taking over a review of the Tkinter GUI in this repository. This
document gives you the context, the original intention, my own assessment
of the weakest parts, and three specific review asks. Be rigorous and
skeptical: I wrote this code, and I am asking you to find what I got
wrong, not to confirm what I did.

## 1. What this project is

`chaptertools` is a local audiobook chapter-alignment toolkit (stdlib +
ffmpeg only). Two pipelines exist and must not be merged:

- **Silence-snap**: `chapterize.py` + siblings (`abchapters.py`,
  `consensus_chapters.py`, `librivox_chapters.py`, `chapterdebug.py`,
  `find_asin.py`) — published timelines snapped onto detected silences.
- **Envelope**: `chapterfit.py` (CLI) orchestrated by `chapterbook.sh` —
  measure → identify → align → pin → emit → finalize, with optional
  faster-whisper listen/sweep in a separate venv
  (`~/.venvs/whisper/bin/python`).

The **GUI under review** wraps the envelope pipeline. It lives in
`src/chaptertools/gui/` and shells out to `chapterfit.py` as a
subprocess; the artifact JSON
(`<work-dir>/<audio>.analysis.json`) is the single source of truth for
GUI state. It never parses CLI output for state.

Hard constraints (do not propose violating these):

- stdlib + ffmpeg/ffprobe + optional faster-whisper. **No new
  dependencies.**
- The alignment math in `src/chaptertools/envelope/` and
  `src/chaptertools/silence/` is production-tested and considered
  sacred: behavior-preserving refactors only.
- `chapterfit.py`'s CLI behavior is stable; the GUI must not require CLI
  changes to function (small additive flags were already added: emit's
  `--title/--author/...`, and `alignment["conflicts"]` is stored by
  `stage_align`).
- The media tree stays data-only; secrets live in
  `~/.config/chapterfit/.env`.
- The GUI must run headless-constructible for tests (display-dependent
  tests skip, everything else must not).

## 2. Original intention (fit-to-intent sources)

The GUI was built against two authored design documents, now copied
into the repo:

- `docs/original-layout-prompt.md` — the full layout redesign spec
  (workflow-oriented stage bar, panels, tabs, acceptance checks).
- `docs/original-resolve-panel-prompt.md` — the Resolve panel addendum
  (summary line, escalation statuses, recommendation ladder, by-ear
  redesign).

Plus in-session owner directives that **override or refine** those
documents, in chronological order:

1. If the owner supplies an ASIN, Identify is **done** — no
   second-guessing. The edition-fit warning belongs to **Align**
   (attention `!` with the weak-chapter count and fit score). Align is
   never blocked by a score.
2. Stage dependency rule: no stage is done while a stage it depends on
   is pending (`align←identify`, `resolve←align`, `emit←align`,
   `finalize←emit`).
3. Playback never self-stops: previews are uncapped (`length=0`); Stop
   is the only ender. The old "keep playing" checkbox was removed
   because it became the default.
4. No empty time cells anywhere: `0:00:00.000` placeholders.
5. The orange attention banner shows only for the **selected** stage —
   never other stages' problems on a tab that can't fix them.
6. Gap graph: **pins as bars** (purple, height = the silence the pin
   sits on, min height off-gap) + the **25 most similar gaps** (blue),
   with a radio switching similarity mode: **chapter cadence**
   (predicted positions of unpinned chapters from pin anchors;
   default) vs **silence length** (duration-matched look-alikes). The
   top-100-longest series was **removed** (owner chose replace).
7. Identify panel shows the source actually used (prefill from
   `identification.label`, e.g. `Audnexus B0041M5MRE`).
8. Unplaced-chapter grey is light (`#b0b0b0`); chapter-table font +2 pt
   over the UI base.
9. "Play from time" must work from a typed time with **no row selection
   required**; Enter in the time field plays from it.
10. Tooltips on every button; buttons must never truncate or clip; the
    panel host sizes to content.
11. Listen/Sweep/Pin/Remove-pin all chain into an automatic re-Align
    (mirroring `chapterbook.sh`'s `align_now`), because pins drop the
    alignment stage and the table must not appear to "lose" pins.
12. Pre-Align table state: rows come from the identification list,
    **honoring pins** (pin time shown, `pin` marker, not greyed), since
    a sweep/listen pin leaves the artifact without alignment.

Read those two documents first; judge fit against them plus this list.

## 3. Code map

```
src/chaptertools/gui/
  app.py        291 l.  main window: menu, H-PanedWindow (library|main),
                       status bar, Log tab, log/error counting, pump loop
  workflow.py  1629 l.  THE MAIN MASS: stage panels, chapter table, all
                       command plumbing (one method per chapterfit
                       subcommand), pin-by-ear, gap graph, harvest
                       integration, banner, selection wiring
  stages.py     196 l.  pure stage-state machine + StageBar widget
  harvest.py    105 l.  reads ID3 tags / embedded chapters / album.nfo
  mpv.py        160 l.  mpv playback + JSON-IPC polling (Player)
  runner.py      72 l.  serial subprocess queue; Job(name, argv, on_done,
                       on_line); events drained by the Tk pump
  library.py     67 l.  media scan (hides *.chapters.*), work-dir rules
  fonts.py       27 l.  per-root derived-font cache (see weakness #4)
  tips.py        45 l.  delayed tooltips
tests/                 test_gui.py (27), test_stages.py (7),
                       test_suggest.py (4), test_nfo.py (7),
                       test_abs_client.py (4) + pre-existing suite
chaptergui.py          root launcher (sys.path shim, like chapterize.py)
docs/                  the two intent documents + this brief
```

Run/verify:

```bash
pytest -q                          # all tests; integration ones run by default
ruff check src/chaptertools/gui tests/test_gui.py   # clean scope
python3 chaptergui.py /srv/media/books              # launch (needs a display; or set CHAPTERFIT_LIBRARY)
```

Note: `ruff check src/` as a whole reports ~40 pre-existing findings in
files this work never touched (envelope/, asr.py, silence/, legacy
tests) — out of scope for this review.

## 4. My own assessment — the weakest parts

Review these hard. Each is something I knowingly shipped or came to
suspect.

1. **`workflow.py` is a 1,629-line god object.** WorkflowPanel is
   controller, view builder, state integrator, disk prober, canvas
   renderer, and tooltip owner in one class. The original design
   intended separate `panels.py`/`stages.py` modules; panels never got
   extracted. Consequences: private-method reach-in tests, high-risk
   edits, and state scattered across ~20 instance attributes
   (`_emit_stale`, `_finalize_dry_ok`, `_pin_autofilled`,
   `_identify_autofilled`, `_row_data`, `_disk`, `_stage`).

2. **`_refresh()` does everything, too often.** One call reloads the
   artifact, rebuilds the table, recomputes stage states, rebuilds the
   summary line, updates every panel result, updates the header/banner,
   and redraws the gap graph — and runs `_disk_facts()`, which spawns
   **two ffprobe subprocesses synchronously on the Tk thread**. The
   min-conf spinner traces into `_refresh()` on every keystroke, so
   tweaking the spinner can fork ffprobe repeatedly and jank the UI.
   Book selection also ffprobes twice more via `harvest_book`.
   The sync-subprocess-on-UI-thread pattern is my most regretted
   architectural choice.

3. **The GUI re-derives pipeline semantics instead of consuming them.**
   Drift risk, each a place where the GUI can silently lie:
   - `_withheld()` mirrors `cmd_emit`'s keep/dedup rule by hand.
   - `listening_value()` in `_update_resolve` re-mirrors
     `suggest_measurements` — which I *extracted into the package
     precisely so the GUI could call it, and then didn't*.
   - `similar_gaps()` cadence prediction reimplements the local-rate
     model that `posteriors`/`solve` use.
   - Stage thresholds (score < 5, weak counts) live in `stages.py`
     while chapterbook.sh has its own gates.

4. **Derived fonts needed a cache to survive.** `Font.copy()` objects
   created as locals get gc'd, which deletes the underlying Tk font and
   leaves styles pointing at dead names — the +2 table font silently
   reverted until `fonts.py` fixed it with a per-root WeakKeyDictionary.
   Check for remaining `.copy()` usages that bypass the cache.

5. **Enter-key overload in the pin-by-ear region.** Enter on the
   chapter table pins the selected chapter; Enter in the time field
   plays from the typed time. Focus-dependent divergence of the same
   key, in adjacent widgets. I think it's defensible; I am not
   confident a new user agrees. Also three playback starters exist
   (row double-click, ▶ Play, ▶ Play from time) plus Space-when-idle.

6. **`_near_spec()` published-time ambiguity.** A bare `2:40` parses as
   m:ss (160 s) but users sometimes mean h:mm (I watched the owner type
   `2:40` meaning 2:40:05). It auto-maps to the nearest chapter by
   placed time and shows the resolution in the hint status, but the
   wrong-chapter mapping is silent if nobody reads the status.

7. **The conflict-pin fallback is representation archaeology.** For
   artifacts predating `alignment["conflicts"]`, a pinned row with
   `spread is None` is tagged as a conflict — an accident of how
   `stage_align` saves conflicting pins. The primary path is the stored
   key; the fallback is unprincipled.

8. **Session-only gating state.** `_emit_stale` and `_finalize_dry_ok`
   are instance flags, not persisted; re-selecting the same book resets
   `_finalize_dry_ok` even if the artifact is unchanged. Stale-gate and
   lost-gate behaviors are both reachable.

9. **Test quality.** Tk tests reach into privates (`_refresh`,
   `_fill_from_selection`, `_row_data`, tree tag internals); several
   hardcode `/srv/media/books/chapterfit.py` and
   `/home/oem/.venvs/whisper/bin/python` by absolute path (breaks on
   any other machine); the dialog-capture hack patches
   `wf.messagebox`. Pure-logic coverage (`stages.py`,
   `similar_gaps`, `_withheld`, `suggest_measurements`,
   `harvest`, nfo, abs-client) is the good part.

10. **Dead code and leftovers.** `_is_withheld()` (workflow.py:423) is
    defined but never called since the gap-graph rewrite.
    `PREVIEW_SECONDS` was removed but audit for other orphans.

11. **`Player` IPC polling is bounded but not free.** `_read_reply`
    holds the Tk thread up to 0.5 s if mpv is late answering; the poll
    runs ~10 Hz from the pump. Fine in practice, ugly in principle.

12. **The gap strip lives in the Measure panel but consumes
    Identify/Align/Resolve-stage data (pins, identification,
    candidate gaps).** A Resolve aid rendered in Measure's house — I
    did it because Measure owns the "gaps" concept, but I doubt it.

## 5. Review asks

### A. Rigorous review of the weak parts above

For each item in section 4: is my diagnosis right, is the severity
right, and what is the *cheapest* fix that respects the hard
constraints? Propose concrete refactors (e.g., how to split
WorkflowPanel without breaking the method-level test seams, how to
move `_disk_facts`/harvest off the UI thread with only stdlib).

### B. Overall improvement assessment

Beyond my list: architecture, state management, error handling
(`_on_done`'s generic warning vs rich log context), accessibility of
the flow for a first-time user, and anything in the intent documents
(section 2) that the implementation betrays. Rank findings by
severity × effort.

### C. Button-by-button audit — overlap, conflict, and sense

Every control, its panel, its action, and what to verify. **Flag any
button whose action overlaps another, contradicts the workflow order,
or will surprise a user.** Specific tensions I already see (verify and
find more):

| Control | Expected action | Audit notes |
|---|---|---|
| search entry (library) | 3+ chars filters by author/title; Esc clears | |
| Library tree rows | selects book → harvest + stage states | |
| Run measure (Measure) | `measure` subprocess | |
| similar-by radios (Measure) | redraw gap strip: cadence / duration | Home-panel question (weakness 12) |
| gap bars (Measure) | hover: pin/chapter/silence detail | No click action by owner decision |
| Identify radios + entry + Browse | three-way source; prefill from used label | |
| Identify button | `identify` per mode | Always-done semantics (directive 1) |
| min conf spinbox (Align) | threshold for tints, markers, weak list, emit, gap strip | **One control, five consumers** — is that legible? |
| near CH=TIME (Align) | canonicalized via `_near_spec`; bare time auto-maps | Ambiguity weakness 6 |
| Align button | `align` | |
| Apply hint (Resolve) | canonicalize → set near → **full Align runs** | Surprising? Button says "hint", does Align |
| Listen / Sweep range / Probe / Full sweep | whisper runs, `--pin -y`, then **auto-Align** | Six paths into Align total (incl. Pin/Remove pin) — coherent? |
| sweep range fields | prefilled ±5 min around selected chapter | **Clobbers manual edits — no provenance guard, unlike pin fields. I believe this is a real bug.** |
| unresolved chapter numbers (Resolve) | click → select + scroll + fill | |
| −10/−5/+5/+10 (transport) | seek while playing | |
| ▶ Play / ⏸ Pause | idle → preview selected row; playing → pause/resume | Overlaps double-click and Play-from-time |
| ■ Stop | ends playback | vs status-bar **Cancel** (kills job) — similar words, different scopes |
| time field + Enter | plays from typed time | Enter-on-tree pins; Enter-here plays (weakness 5) |
| Set from playback | frozen clock → time field | Third writer of the time field |
| ▶ Play from time | playback at field time, uncapped | No row selection needed (directive 9) |
| Pin | `pin --at`, then auto-Align | |
| Remove pin | `pin --remove` (row fallback), then auto-Align | |
| keep playing checkbox | *(removed — uncapped is default)* | Confirm no remnants |
| re-read (Emit) | re-harvest tags/chapters/nfo | |
| embed checkbox + Emit | `emit --embed` + metadata flags | Emit threshold = spinner (see above) |
| Dry run (Finalize) | `finalize` (no apply) | Enables Apply for this session |
| Apply (Finalize) | `finalize --apply [-y] [--allow-delete]` | Gating resets on re-select (weakness 8) |
| allow delete checkbox | last-resort flag | |
| Table row select | fills chapter/time/sweep; by-ear title | |
| Table double-click | preview from that chapter | |
| Table keys | Space play/pause, ←/→ ±5 s, Shift ±10 s, Enter pin, Delete unpin | |
| Transcripts grep + button | `transcripts --grep` → tab text | |
| Copy all / Save log… (Log) | clipboard / file | Overlaps right-click Copy — harmless? |
| Cancel (status bar) | terminate running job + stop player | vs ■ Stop (see above) |
| Stage bar stages | click → switch panel; auto-advance on success | Does auto-advance yank the panel out from under the user? |

Also audit these flows end-to-end: the pin-by-ear loop
(type→Enter→seek→pause→Set→Pin), the escalation ladder
(Listen→probe→range→by ear) including that "next:" and the accent
button agree with the artifact state, and Measure→Identify→Align→
Resolve→Emit→Finalize stage transitions against the state machine in
`stages.py`.

## 6. Known-suspect list (verify, don't rediscover)

- `_prefill_sweep` clobbers manual sweep-range edits (no provenance
  guard) — I believe this is a shipped bug.
- `_is_withheld` dead code (workflow.py:423).
- Hardcoded absolute paths in tests (test_gui.py:417, 609, 617, 700).
- ffprobe on the UI thread (weaknesses 2).
- `_finalize_dry_ok` reset on same-book re-select.
- gap strip does not redraw when pins change unless a job completed
  (redraws ride `_refresh`, which jobs trigger — direct pin edits in
  the artifact by other tools won't refresh until the next event).

## 7. What not to propose

- Merging the two pipelines.
- New dependencies (incl. requests/numpy — deliberately excluded).
- Behavioral changes to the alignment/solve/posteriors math.
- Restructuring the media library layout or moving console scripts out
  of the tree.

## 8. Deliverable

A findings report: (1) verdict on each weakness in section 4, (2) the
button audit as an annotated table with conflicts called out, (3) a
ranked improvement list (severity × effort), (4) an overall
fit-to-intention judgment against section 2's two documents and twelve
directives. Cite file:line for every claim. Run the test suite and the
GUI before asserting behavior; do not trust this document over the
code — where they disagree, the code is what shipped, and I want to
know about the disagreement.
