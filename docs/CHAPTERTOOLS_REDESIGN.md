# chaptertools — workflow redesign brief

> For an AI IDE. Implement **one phase at a time**, run the app, and commit before moving on. Every phase leaves the app working. Mockups: the "chaptertools Redesign" Design canvas (artboards 0–4).
>
> Scope: view layer and wiring only. Do not change the audio analysis, alignment, sweep/transcription, tagging or Audiobookshelf code. It's fine to change *when* those are called (for example, running Scan automatically).

## Why

The window is one book, cleaned up from start to finish. Today it takes two selectors that don't know about each other: the **stage strip** picks the controls and the **notebook tabs** pick the evidence. So the user keeps jumping between them (for example, Resolve → Gaps tab → Transcripts tab → Chapters tab). Several things also exist twice:

| Duplicate | Where |
|---|---|
| Two timelines | Gaps tab; Quality waveform |
| Two decodes | Measure; Quality "Scan file" |
| Two players | Resolve pin-by-ear transport; Quality Play/Stop |
| Two position-hint fields | Align "Near"; Resolve "Position hint" |
| Two title/author forms | Identify search; Emit book info |
| Two ways to replace the library file | Emit → Finalize; Quality Process → Replace |

## Target model

```
Steps:   1 Scan  ›  2 Match  ›  3 Place  │  Audio cleanup (optional side lane)  ›  4 Finish
```

| New | Replaces | Notes |
|---|---|---|
| **Scan** | Measure + Quality "Scan file" | Runs automatically when a book opens and there's no valid cached result. One decode feeds the silences, the header check **and** the loudness/noise analysis. Shows "Copy fix command" only if the header timing is wrong. |
| **Match** | Identify + Emit's book-info form | Search results are the main view. Book info (title, author, narrator, album, year, genre, comment) is edited here. "Use this edition" fills the chapter list and book info, then runs Place automatically. |
| **Place** | Align + Resolve (+ pin by ear, Transcripts tab) | Auto-align, then fix. Problem rows expand inline with their evidence and pin actions. One Hints box (`CH=TIME` per line) serves both Align's Near and Resolve's Position hint. |
| **Audio cleanup** | Quality tab | Side lane: it never blocks the steps and the steps never block it. Analysis comes from Scan. No Process/Replace buttons here — it has an "Include at Finish" checkbox instead. |
| **Finish** | Emit + Finalize + Quality Process/Replace | One summary (Chapters, Tags, Audio), one dry run, one Apply. Apply is off until a dry run matches the current settings. The first original is always kept. |

Global, always visible (step-independent): **Timeline**, **Player bar**, **job status + Cancel**, **Log** (drawer).

## Window layout (1920×1080 target; must still work at 1366×768)

```
┌ menu ────────────────────────────────────────────────────────────────────────────────────────┐
│Lib│ Title — Author · duration · ASIN · file                                                   │
│rary│ [✓ Scan] › [✓ Match] › [● Place  23 placed · 1 conflict] │ [≋ Audio cleanup] › [4 Finish] │
│   ├───────────────────────────────────────────────────────────────────────────────────────────┤
│260│ TIMELINE: level bars · silence ticks · chapter marks · noisy bands · clipping · playhead · selection │
│px │                                                                                           │
│or ├──────────────────────────────────────────────────────────────┬────────────────────────────┤
│44 │ STEP MAIN VIEW                                               │ INSPECTOR (420 px)         │
│px │  Match: editions table + fit card                            │  step controls             │
│   │  Place: chapter table with inline evidence                   │                            │
│   │  Audio: chain list + stage detail                            │                            │
│   │  Finish: 3 summary cards + dry-run output                    │                            │
├───┴──────────────────────────────────────────────────────────────┴────────────────────────────┤
│ PLAYER  −10 −5 ▶ +5 +10  0:45:31.960 / 17:27:14.950 · ch 3   ☐ Hear original │ status · [Log] [Cancel job] │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Library: full width (260 px) on the book picker; collapses to a 44 px strip while you work on a book (button to expand, and `Ctrl+L`). Remember the state.
- The timeline is hidden on Finish (nothing to look at there) and visible on the other steps.
- Only the main view and the inspector scroll. No outer scrollbar.
- At < 1500 px width, the inspector becomes a bottom sheet under the main view, or collapses to a toggle.

### Tk widget tree

```
App(tk.Tk)
├─ menubar
└─ root: ttk.Frame (grid: row 0 body weight=1, row 1 PlayerBar)
   ├─ body: ttk.PanedWindow(HORIZONTAL)
   │  ├─ LibraryPane          (collapsible; tree + search + filter chips All / Needs work / Done)
   │  └─ BookPane: ttk.Frame (grid rows: header, timeline, workspace weight=1)
   │     ├─ BookHeader        (title line + StepRail)
   │     ├─ Timeline          (tk.Canvas, ~100 px)
   │     └─ workspace: ttk.PanedWindow(HORIZONTAL)
   │        ├─ MainStack      (one frame per step; tkraise() to switch)
   │        └─ InspectorStack (one frame per step; tkraise() to switch)
   ├─ PlayerBar
   └─ LogDrawer: tk.Toplevel or a bottom pane toggled by the Log button
```

**The step rail is the only selector.** Picking a step raises its MainStack and InspectorStack frames together. Delete the stage strip, the amber banner and the notebook.

## Shared state (do this first)

Create one object that all views read from and subscribe to, so that no view pulls data out of another view:

```python
class BookSession:
    book: Book | None
    scan: ScanResult | None            # silences, header check, loudness envelope, noise analysis
    edition: Edition | None            # chosen published chapter list + metadata
    book_info: BookInfo                # tags to write; filled from file → last search → folders → edition
    chapters: list[ChapterRow]         # published, placed, gap, conf, pin, heard, status
    min_confidence: float
    hints: dict[int, float]            # chapter -> seconds; from the one Hints box
    transcripts: list[TranscriptLine]
    cleanup: CleanupSettings           # preset, stages, include_in_finish
    playhead: float
    selection: tuple[float, float] | None   # timeline selection
    last_dry_run: DryRunResult | None  # with a hash of the settings it was made from
    job: JobState                      # running command, progress, cancel()

    def subscribe(self, topic: str, fn): ...
    def emit(self, topic: str): ...    # "chapters", "playhead", "selection", "job", "scan", ...
```

Step status is derived, not stored:

| Step | done | attention | blocked |
|---|---|---|---|
| Scan | scan valid for file size/mtime | header timing mismatch | — |
| Match | edition chosen and fits | chosen edition doesn't fit | no scan |
| Place | every row placed, no conflicts | any conflict / unplaced / withheld | no edition |
| Audio cleanup | — (never blocks, never "done"; shows "off" or "preset · N stages") | analysis advice pending | — |
| Finish | applied, and the output matches current settings | settings changed since apply/dry run | Place not done (unless the user only writes cleaned audio — see Open questions) |

On book open, select the first step that isn't done (as today).

## Components

### StepRail
Buttons with a status glyph (✓ done, filled number = current, ! attention, ○ not run, ✕ blocked) plus a one-line live sub-status (e.g. `23 placed · 1 conflict`). Audio cleanup has a square icon and sits after a divider, to show it's a side lane. Keys: `Ctrl+1…4` for the steps, `Ctrl+5` for Audio cleanup.

### Timeline (replaces the Gaps tab and the Quality waveform)
One `tk.Canvas`, redrawn on `<Configure>` and on session events. Layers, bottom to top:
1. noisy-segment bands (tan)
2. level bars (from the Scan envelope; clipping bars amber)
3. candidate silences (short blue ticks; the "similar by cadence / length" radio moves into a small ⋯ menu on the timeline)
4. chapter marks (purple; red = problem; dashed = preview of the selected edition in Match; dimmed in Audio cleanup), numbered
5. selection (blue translucent) and playhead (black line)

Interaction: click = seek; drag = select; wheel = zoom around the cursor; shift+wheel = pan; hover on a mark or tick = tooltip with details. The selection is shared: it's the Place "Sweep selection" range, the Audio "Learn from selection" span and the preview span.

### PlayerBar (the one player)
−10, −5, Play/Pause, +5, +10, time / total · current chapter, **Hear original** (only enabled when cleanup has any stage on; plays audio without the cleanup chain), then status text, a **Log** button (with a red count badge on errors) and **Cancel job**. Keys everywhere: Space, ←/→ ±5 s, Shift+←/→ ±10 s. Playback stops at the end of the file or on Stop.

### LogDrawer
Same content and buttons as today's Log tab (Save…, Clear, Copy all, right-click copy). Opens from the Log button or `Ctrl+Shift+L`. The last line is still shown in the PlayerBar status.

### Step: Scan
There's no main view of its own after the first run. While it's running, show a progress card in the main area. When it's done, go to Match (or the next step that isn't done). If the header is bad: an attention card with the explanation and **Copy fix command**. Keep a **Re-scan** entry in the step's ⋯ menu.

### Step: Match
- Inspector: a Source segmented control (Search / ASIN / JSON / Embedded / NFO); Search = title, author, keywords, region, **Search**. ASIN = field + Check. JSON = field + Browse… + Check. Below it, the **Book info** form (moved from Emit), noting that it's written at Finish, with a "Re-read from file" link.
- Main: an editions table (ASIN, Title, Author, Narrator, Runtime, Chapters, **Fit with this audio**). Selecting a row runs the fit check (the existing Identify verification) and draws its chapter times as dashed marks on the timeline. A fit card shows chapters, fit score, rate spread, runtime ratio and the verdict, with the primary button **Use this edition**.
- Use this edition → set the edition, fill empty book-info fields (highlight changed fields), run Align, switch to Place.

### Step: Place
- Main: the chapter table (columns #, Title, Published, Placed, Gap, Conf, **Heard**, Status) with filter chips: All / Problems / Low confidence / Unplaced. Same row tints as today (grey unplaced "?N", amber withheld, red conflict). Keep the existing key bindings.
- **Problem rows expand inline** (auto-expand the first one; Enter toggles), showing:
  - a one-sentence explanation of the problem, built from the data (e.g. for a conflicting pin: what was heard where, versus the published time; and if a neighbour's published time is within a few seconds of the heard time, say the list may be shifted by one)
  - candidate silences near the expected time: ▶ play, time, silence length, why (cadence / heard text / current pin), **Use this**
  - actions: ▶ Play from 5 s before, Pin at playhead, Remove pin, and when relevant "Shift titles down one from here…"
- Inspector:
  - summary (placed / conflicts / withheld; rate; length difference) and a **Next** suggestion (the existing "next:" logic)
  - Auto-align: Minimum confidence, **Hints** (textarea, `CH=TIME` per line), Re-align
  - Find spoken announcements: the existing ladder (Listen near gaps, Probe 10 min, Sweep selection, Full sweep) with cost and status per row. Sweep uses the timeline selection instead of two text fields.
  - Heard: a regex filter (default `chapter`) over transcript lines; double-click to seek. This replaces the Transcripts tab.

### Step: Audio cleanup
Keep the chain + detail design already built. Changes:
- Analysis tiles move to the inspector and read the Scan result (delete "Scan file").
- Advice ("Turn it on") stays in the inspector.
- Delete Play/Stop/start field (use the PlayerBar; preview = 30 s from the playhead or the selection).
- Delete Output format/bitrate, Process full file, Replace library file (moved to Finish).
- Add an **Include at Finish** checkbox next to the preset.

### Step: Finish
- Main: three cards.
  - **Chapters**: written / withheld / pins, Embed chapters checkbox (off = chapters.txt + chapters.json only), link "Edit in Place".
  - **Tags**: read-only book info, link "Edit in Match", "also written to album.nfo".
  - **Audio cleanup**: "Clean the audio in this write" checkbox (mirrors Include at Finish), preset + stage count, output format/bitrate, estimated render time, link "Edit cleanup".
  - Below them, the dry-run output (monospace, one action per line).
- Inspector: **Dry run** (secondary), **Apply to library** (primary; disabled until `last_dry_run.settings_hash == current_hash`), Options: allow delete-and-re-add (off), and a note that the first original is always kept.
- Apply = the existing Finalize path, extended so that when cleanup is included it renders the cleaned audio first, then embeds chapters and tags into that render.

## Phases

1. **Shared state + global player + log drawer.** Add `BookSession`; move playback out of Resolve and Quality into `PlayerBar`; move Log to the drawer. Old stages/tabs still work but use the session and the player.
2. **Timeline.** Build `Timeline`; put it under the header; delete the Gaps tab and the Quality waveform; wire the shared selection.
3. **Step rail + stacks.** Replace the stage strip + notebook with StepRail + MainStack/InspectorStack. At first, map the old panels in: Measure→Scan, Identify→Match, Align+Resolve side by side→Place, Emit+Finalize→Finish, Quality→Audio cleanup.
4. **Scan merge.** Auto-run on open; make Quality read the Scan analysis; delete "Scan file".
5. **Match.** Editions as the main view, per-edition fit, dashed preview on the timeline; move Book info here from Emit; "Use this edition" → auto Place.
6. **Place.** Merge Align+Resolve into the inspector; one Hints box; Heard column + Heard search (delete the Transcripts tab); inline problem rows (delete the pin-by-ear panel).
7. **Finish.** Three cards + dry run + apply; include cleaned audio; delete Emit, Finalize, Process full file and Replace library file.
8. **Library.** Collapse while working; per-book status glyphs (✓ chapters written, ● in progress, ♪ audio cleaned); filter chips.

## Acceptance criteria

- [ ] Only one selector for "where am I" (the rail); there's no notebook.
- [ ] No step needs another step's panel or a tab switch to see its evidence.
- [ ] One timeline, one player, one log, one decode per book, one Book info form, one Hints box, one place that writes to the library.
- [ ] Every feature of the old Measure/Identify/Align/Resolve/Emit/Finalize/Quality/Gaps/Transcripts/Log still exists (check against the "current layout" document).
- [ ] Audio cleanup never blocks or is blocked by the steps.
- [ ] Apply is disabled until a dry run matches the current settings; the first original is always kept.
- [ ] Works at 1920×1080 without outer scrolling and is usable at 1366×768.
- [ ] Keyboard: Ctrl+1…5 steps, Space/arrows player, Enter/Delete pins, Ctrl+L library, Ctrl+Shift+L log.

## Open questions (decide before phase 7)

1. **Cleanup-only write:** can Finish write cleaned audio when chapters aren't placed (for example, a book with no published list)? Suggested: yes — the Chapters card shows "not written" and Apply is allowed.
2. **Re-running after Apply:** applying again replaces the previous output and keeps the first original. Confirm that matches the current Finalize behaviour.
3. **Batch:** you said one book at a time, so batch runs are out of scope. The library "Needs work" filter is the only concession.
