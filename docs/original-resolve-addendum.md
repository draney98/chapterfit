# Addendum: fix the Resolve panel and the header

The overall layout is now right; the chapter table and tabs are good. This addendum covers the header, the stage bar logic, and the Resolve stage panel, which still looks disorganized and doesn't guide the user. Everything in the original prompt still applies.

## Problems in the current build

- The header repeats the stage states as a text line that truncates ("ID3 t… Measure: done · Identify: attention …"). The stage bar directly below already shows this.
- The stage bar shows Finalize as done while Emit is pending. That state is impossible.
- The left column of the Resolve panel is a vertical stack of buttons with different widths, indented for no reason. The "or pin by ear:" label points at nothing. The "sweep range S-E" field is two rows below the sweep buttons it belongs to. Cost notes are crammed into button labels.
- The playback row still has two gaps. The play/pause button that belongs between −5s and +5s isn't shown, and Pause sits at the far right. "Set from playback" and "keep playing" are on a misaligned third row.
- "Use placed time" is still present, although selecting a table row was supposed to replace it.
- Selecting a row doesn't fill the chapter and time fields. In the current state Chapter Four is selected and both fields are empty.
- The transport buttons are disabled with no explanation.
- Something is clipped at the bottom-right edge of the window, below the legend.
- The panel doesn't say which chapters need work, which option to try next, or what has already been tried.

## Header

One line: title — author · duration · ASIN · file name. Elide the file path in the middle and show the full path in a tooltip. Remove the text list of stage states.

## Stage bar logic

A stage can't be done while an earlier stage it depends on is pending. Finalize requires Emit to be done; if Emit is pending, Finalize is pending too, whatever the folder contents say. Apply the same rule to every stage.

## Resolve panel layout

Fixed height of no more than 200px, in three parts:

```
┌─ 6 chapters unresolved:  5  6  7  10  12  14 ─────────────── next: Sweep probe ─┐
│ AUTOMATIC                                     │ BY EAR — Chapter Four            │
│ Position hint [3=0:34:34   ] [Apply hint]  free       │ placed 0:49:54.600  conf 0.01 │
│ Listen                       [Listen    ]  ~4 min      heard 3  │ [−10][−5][▶ Play][+5][+10][■ Stop] │
│ Sweep range [0:44:54]–[0:54:54] [Sweep range]  ~10 min │ position 0:49:51.2         │
│ Sweep probe                  [Probe     ]  10 min     not run  │ time [0:49:54.600] [Set from playback] │
│ Full sweep                   [Full sweep]  ~31 min    not run  │ [Pin] [Remove pin]  ☐ keep playing │
└──────────────────────────────────────────────┴───────────────────────────────────┘
```

**Summary line (top).** "N chapters unresolved:" followed by the chapter numbers, each clickable to select that row in the table. At the right, "next:" names the recommended action. When nothing is unresolved, the line says so and the panel shows only the by-ear controls for spot checks.

**Automatic (left).** A grid with four columns: label, input where one applies, button, cost, status. All buttons in this column share one width; use `grid(..., uniform="auto")` on the button column, or give every button the same `width=`. Labels are right-aligned in their own column. Rows:

- **Position hint** — a `CH=TIME` field and Apply hint. Cost "free". Status shows the hint in effect, if any.
- **Listen** — cost "~4 min of audio". Status shows whether it has run and how many announcements it heard.
- **Sweep range** — start and end fields inline on the same row, prefilled with the selected chapter's placed time minus and plus 5 minutes. Cost computed from the range. This is the targeted option for one stubborn chapter.
- **Sweep probe** — cost "10 min". Status shows whether a probe has run.
- **Full sweep** — cost computed from the book's duration divided by ten, shown in minutes, not as "book/10". Status shows minutes already swept, since cached ranges cost nothing to redo.

Derive statuses from the analysis artifact: Listen's findings are `heard.positions` entries without `source: "sweep"`; swept minutes are the sum of lengths in `heard.clips` keys that contain `sweep` (keys are `start|length|model#format`); pins are `pins`.

**By ear (right).** Titled with the selected chapter's name. Shows its placed time and confidence. Transport controls in one evenly spaced row: −10s, −5s, a single Play/Pause toggle, +5s, +10s, Stop. The current playback position appears beneath them while playing. Then the time field, Set from playback, Pin, Remove pin, and the keep-playing checkbox. Remove "Use placed time". When no row is selected, disable these controls and show "Select a chapter in the table" in place of the chapter title.

## The recommended action

Highlight one button as the primary action, using an accent style, according to what's already known:

1. Listen hasn't run → Listen.
2. Listen ran and heard nothing → Sweep probe. The breaks have no usable silence.
3. Listen heard announcements but chapters remain unresolved → Sweep range, prefilled around the selected unresolved chapter.
4. A sweep has already covered the unresolved chapters' ranges → By ear.

Name the recommendation in the summary line's "next:" label as well.

## Selection wiring

Selecting a table row fills the by-ear chapter and time fields and the sweep range fields. Selecting an unresolved chapter from the summary line does the same and scrolls the table to it. After Pin, Listen, or Sweep, re-run Align, refresh the table and summary line, and keep the selection if the chapter still exists.

## Fixes

- Find what occupies the gaps in the playback row. It is probably a play button that was never gridded or was gridded into the wrong column; show it as the Play/Pause toggle in place of the separate Pause button.
- Find what's clipped at the bottom-right edge and make it fit inside the window.
- Don't mix `pack` and `grid` within one container. Use `sticky="ew"` so fields and buttons line up.

## Acceptance checks

- The Resolve panel is no taller than 200px, and the table still shows at least 25 rows at 1920×1080.
- All Automatic buttons are the same width and line up in one column.
- The playback row has no gaps and a single Play/Pause control.
- Selecting any row fills the chapter, time and sweep range fields.
- The summary line lists the unresolved chapters and names the next action, and exactly one button is highlighted as primary.
- The stage bar never shows a stage as done while one it depends on is pending.
- Nothing is clipped at the window edges.
