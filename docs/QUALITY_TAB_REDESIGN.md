# Quality tab redesign — implementation brief

> Paste this whole file into your AI IDE as the task. It assumes Tkinter/ttk (the screenshot looks like Tk on Linux). If the app uses another toolkit (PySide/PyQt, wx), keep the structure and swap the widget names — the mapping table at the end covers that.

## Goal

Replace the current Quality tab, which shows all 11 filter sections at once in a two-column grid, with a **signal chain + detail** layout:

- a compact **Analysis** strip at the top,
- a **chain list** on the left (every stage, one row each, in processing order),
- a **detail panel** on the right showing only the selected stage's controls,
- a fixed **Preview / Output** bar at the bottom.

Do not change any DSP, preset storage or processing code. This is a view-layer refactor. Every existing parameter must remain reachable.

## Problems being fixed (for context)

1. All 11 sections are visible at once, so the tab needs a long scroll and the two columns leave large empty gaps (EQ has 13 rows, High-pass has 1).
2. Every parameter row has its own **Reset** button — ~40 identical buttons.
3. Each section has **Bypass** + **Show** checkboxes. "Bypass ☐" is a double negative, and "Show" is unclear.
4. Combobox values are echoed in a label next to them ("spectral ▾ spectral", "peak ▾ peak").
5. The processing order isn't visible.
6. The analysis legend text overlaps the waveform, and the waveform only covers ~27 % of the width.
7. The warning "Noise floor shifts by **223 dB**" is almost certainly a calculation bug (see Bugs).
8. True peak +2.1 dBTP (clipping) is shown as plain text with no emphasis.

## Layout

```
┌ Quality ─────────────────────────────────────────────────────────────────────┐
│ Analysis                                                     ┌──────┬──────┐ │
│ [ waveform canvas, full width, 84 px tall                 ]  │ Int. │ TP ⚠ │ │
│ ■ Level ■ Clipping ■ Noisier   (legend BELOW the canvas)     │ NF   │ DR   │ │
│ ⚠ Background noise changes across this file…   [Turn it on]  └──────┴──────┘ │
├──────────────────────────┬───────────────────────────────────────────────────┤
│ Preset [Noisy room ▾][Save as…][⋯] │ EQ                     ☑ Enabled [Reset to preset] │
│ Modified — Save as… to keep       │ Four-band tone shaping.                         │
│ Signal chain                  on  │ ───────────────────────────────────────────────│
│ 1 High-pass   90 Hz           ☑   │  (stage-specific controls, see below)          │
│ 2 De-click    thr 0.30·sens…  ☑   │                                                 │
│ 3 Denoise     spectral · 0.80 ☑   │                                                 │
│ 4 Neural enh. not installed   ☐   │                                                 │
│ 5 Noise gate  −42 dB · 200 ms ☑   │                                                 │
│ 6 EQ          flat          ● ☑   │  ← selected row highlighted                     │
│ 7 De-esser    off             ☐   │                                                 │
│ 8 Compressor  −22 dB · 1.5:1  ☑   │                                                 │
│ 9 Loudness    −19.0 LUFS      ☑   │                                                 │
│10 Limiter     −1.0 dB         ☑   │                                                 │
├──────────────────────────┴───────────────────────────────────────────────────┤
│ PREVIEW ▶ ■ from [0:00:00] for 30 s [Hear original] │ OUTPUT [MP3▾][48 kbps▾] ▬▬▬ status [Replace library file] [Process full file] │
└──────────────────────────────────────────────────────────────────────────────┘
```

Sizes (at 100 % scaling): chain column fixed ~320 px; detail panel takes the rest; analysis strip ~150–190 px; footer ~60 px. Only the detail panel scrolls (vertically), if needed. The whole tab should fit in ~1280×860 with no outer scrollbar.

### Tk widget tree

```
QualityTab(ttk.Frame)                         grid rows: 0 analysis, 1 body (weight=1), 2 footer
├─ AnalysisFrame(ttk.Frame)                   row 0, sticky="ew"
│  ├─ waveform: tk.Canvas(height=84)          col 0, weight=1
│  ├─ legend: ttk.Frame (3 swatches + labels) col 0, under canvas — never drawn on the canvas
│  ├─ metrics: ttk.Frame, 2×2 grid of MetricTile  col 1, fixed width
│  └─ advice: ttk.Frame (icon, message, button)   spans both cols, only gridded when there is advice
├─ body: ttk.PanedWindow(orient=HORIZONTAL)   row 1, sticky="nsew"
│  ├─ ChainPanel(ttk.Frame, width=320)
│  │  ├─ preset row: ttk.Combobox(state="readonly"), ttk.Button "Save as…", ttk.Menubutton "⋯"
│  │  │     (⋯ menu: Apply, Restore defaults, Rename, Delete)
│  │  ├─ preset_status: ttk.Label (e.g. "Modified — Save as… to keep these settings")
│  │  └─ ChainRow × N  (one per stage, see below)
│  └─ DetailPanel(ttk.Frame)
│     ├─ header: title Label, description Label, ttk.Checkbutton "Enabled", ttk.Button "Reset to preset"
│     └─ body: ScrollableFrame holding the current stage's editor (swap on selection)
└─ FooterBar(ttk.Frame)                       row 2, sticky="ew"
   ├─ "PREVIEW" label, Play, Stop, "from" Entry, "for 30 s" label, ttk.Checkbutton(style="Toolbutton") "Hear original"
   ├─ ttk.Separator(orient=VERTICAL)
   └─ "OUTPUT" label, format Combobox, bitrate Combobox, ttk.Progressbar + status Label,
      "Replace library file" Button, "Process full file" Button (style="Accent.TButton")
```

## Components

### ChainRow

One row per stage, fixed height ~40 px:

| Part | Detail |
|---|---|
| Index | 1…N, small, muted |
| Name | stage name; muted if disabled; greyed if not installed |
| Summary | one-line live summary of key values (table below), monospace, ellipsised |
| Modified dot | small accent dot when any value differs from the loaded preset |
| Enabled checkbox | right-aligned; toggles the stage without selecting it |

Clicking anywhere except the checkbox selects the row (highlight background) and loads that stage into the DetailPanel. Up/Down arrow keys move the selection; Space toggles the enabled checkbox of the selected row.

Summaries:

| Stage | Summary when enabled |
|---|---|
| High-pass | `90 Hz` |
| De-click | `thr 0.30 · sens 0.50` |
| Denoise | `spectral · 0.80` (+ ` · per seg` when re-estimate is on) |
| Neural enhance | `not installed` when extra is missing |
| Noise gate | `−42 dB · 200 ms` |
| EQ | `flat` if all gains are 0, else `N bands active` |
| De-esser | `6500 Hz · −18.0 dB` |
| Compressor | `−22.0 dB · 1.5:1` |
| Loudness | `−19.0 LUFS` |
| Limiter | `−1.0 dB` |

Disabled stages show `off`.

### Stage order

Show stages in the order the processing code actually applies them. The mockup assumes:
High-pass → De-click → Denoise → Neural enhance → Noise gate → EQ → De-esser → Compressor → Loudness → Limiter.
**Read the filter chain builder in the code and use its real order.** If the order is user-configurable, add drag-to-reorder later — out of scope for now.

### ParamRow (generic slider)

Used by every stage except EQ's band grid.

```
[Label 150px] [ttk.Scale ───────────o──── stretch] [ttk.Spinbox 96px, right-aligned, with unit] [↺ 32px]
```

- Scale and Spinbox share one `tk.DoubleVar`; typing a value in the Spinbox is the precise way to set it.
- Spinbox shows the unit via a format (`"90 Hz"`, `"−18.0 dB"`, `"1.5:1"`); parse by stripping the unit.
- The **↺ reset** icon button only appears when the value differs from the loaded preset's value (keep the column reserved so nothing shifts). Resetting returns to the **preset** value, not the factory default.
- Also: double-click the Scale → reset; mouse wheel over Scale → step.
- Changed values tint the Spinbox background lightly.

Drive this from a declarative spec so all stages use the same code:

```python
@dataclass
class ParamSpec:
    key: str
    label: str
    min: float
    max: float
    step: float
    unit: str = ""
    decimals: int = 0
    kind: Literal["slider", "choice", "bool"] = "slider"
    choices: list[str] | None = None

@dataclass
class StageSpec:
    id: str
    name: str
    description: str
    params: list[ParamSpec]
    editor: Literal["generic", "eq", "denoise"] = "generic"
    requires_extra: str | None = None   # e.g. "neural"
```

Pull min/max/step from wherever the current sliders define them; don't invent new ranges.

### Stage editors

- **Generic** — header + a list of ParamRows. Used by High-pass, De-click, Noise gate, De-esser, Compressor, Loudness, Limiter, Neural enhance.
- **Denoise** — rows in this order:
  - Mode: Combobox (`Spectral`, `Neural` — Neural disabled with "(install extra)" when the extra is missing). **No echo label.**
  - Noise estimate: Checkbutton "Re-estimate per segment" + a small "recommended" badge when the analysis flagged a varying noise floor.
  - Noise profile: "Learn from selection" button, disabled until a range is selected on the waveform, with helper text "Select a quiet stretch in the waveform first".
  - Strength ParamRow.
- **EQ** — a 4-column grid, one bordered card per band (Low, Low-mid, Presence, High). Each card: band name + type hint; Frequency, Gain, Q as compact stacked rows (label + value on one line, Scale full width below, ↺ when changed). The High card adds a Shape combobox (Peak/Shelf, no echo label). Below the grid: "All gains at 0 dB — the EQ is currently flat and has no effect." when that's true.
- **Not installed** (Neural enhance, when the extra is missing): a dashed info box — "The neural extra isn't installed. Once it is, this stage and the neural Denoise mode become available." — and an "Install neural extra…" button that runs/shows the existing install path. Params below it are disabled. The chain row's checkbox is disabled.

A disabled stage's editor stays editable but is visually dimmed (use a "Dim" ttk style on labels).

### Enabled vs Bypass vs Show

- Replace each section's **Bypass** checkbox with **Enabled** (inverted meaning; wire to the same model field: `enabled = not bypass`). It appears both in the chain row and the detail header; both bind to the same `BooleanVar`.
- **Show**: its purpose is unclear from the UI. Check the code:
  - If it controlled section visibility → drop it; the chain list replaces it.
  - If it controls whether the stage is drawn/overlaid on the waveform, or included in preview → keep it as a checkbox in the detail header with a clear label ("Overlay on waveform" / "Include in preview").

### Presets

- Combobox selection **applies immediately** (remove the separate Apply button). If there are unsaved changes, confirm first.
- "Save as…" prompts for a name.
- "⋯" menu: Restore defaults, Rename, Delete.
- Status line under the combobox: `Modified — Save as… to keep these settings` whenever any stage differs from the loaded preset.

### Analysis strip

- Waveform canvas spans full width of its column and shows the **whole file**. Draw level bars; shade "noisier than the rest" segments as a background band; mark clipping in amber.
- The legend goes in a separate row **under** the canvas.
- Support click-drag selection on the canvas (used by Denoise "Learn from selection" and could set the preview start time).
- Metrics as 4 tiles in a 2×2 grid: Integrated LUFS (sub-label "target −19.0" from the Loudness stage), True peak, Noise floor, Dynamic range. **True peak > 0 dBTP** → warning style (amber border/background) with sub-label "over 0 — clips".
- Advice banner (only when relevant): message + action button. For the noise-floor case: "The background noise changes across this file. Re-estimating the noise per segment in Denoise will handle it better." Button "Turn it on" → sets re-estimate on and selects the Denoise stage. Hide the banner once applied.

### Footer

- Preview: Play, Stop, start time Entry, "for 30 s", and an **"Hear original"** toggle (replaces the preview "Bypass" checkbox, which is ambiguous next to all the per-stage Bypass boxes). While it's pressed, give it the warning style so it's obvious you're hearing the unprocessed audio.
- Output: format, bitrate, progress bar + status text (e.g. "Processing… 42 %", "Processed 12:04 ago · matches current settings", "Settings changed since last process"), "Replace library file" (enabled only when there's a processed file that matches current settings), and **"Process full file"** as the primary (accent) button.
- Cancel appears in place of "Process full file" only while processing.

## Bugs to fix along the way

1. **"Noise floor shifts by 223 dB"** — impossible for a file with a −51.5 dB floor. Likely a sign error, a max-min over values including `-inf`/silence, or dB of a ratio computed twice. Find where the message is built, fix it, and treat digital-silence segments as excluded. Phrase the advice without the number unless it's reliable.
2. **Waveform only fills ~27 % of the width** — likely a scale based on analysed-so-far length or the wrong canvas width (read `winfo_width()` after `<Configure>`, not at construction).
3. **Legend text drawn over the waveform** — move to its own row.
4. **Echo labels** next to comboboxes ("spectral", "peak") — remove.

## Styling (ttk)

Use the `clam` theme as the base and define a few styles; don't hard-code colors per widget.

| Token | Hex | Use |
|---|---|---|
| ground | `#F3F2EF` | tab background |
| panel | `#FFFFFF` | chain + detail panels |
| border | `#D9D6D0` | panel borders |
| text | `#1F1E1C` | body text |
| muted | `#5E5B55` | secondary labels |
| accent | `#2B5FA8` | selection, primary button, modified dot |
| selection bg | `#EEF3FB` | selected chain row, changed Spinbox |
| warn text / bg / border | `#7A4A00` / `#FFF4E0` / `#E8B77A` | clipping tile, advice, "Hear original" pressed |
| clip mark | `#D97706` | waveform clipping |
| noisy band | `#EADFC8` | waveform background shading |

Styles to add: `Chain.TFrame`, `ChainSelected.TFrame`, `Muted.TLabel`, `Dim.TLabel`, `Mono.TLabel`, `Warn.TFrame`/`Warn.TLabel`, `Accent.TButton`, `Card.TFrame`. Use `TkFixedFont` (or the app's mono font) for values and summaries.

## Suggested file structure

```
quality_tab/
  __init__.py
  specs.py          # StageSpec / ParamSpec definitions, in processing order
  model.py          # QualitySettings: values, enabled flags, preset baseline, modified checks; emits change callbacks
  analysis_view.py  # waveform canvas, legend, metric tiles, advice banner
  chain_view.py     # preset row + ChainRow list
  detail_view.py    # DetailPanel + editors: GenericEditor, DenoiseEditor, EqEditor, NotInstalledEditor
  widgets.py        # ParamRow, MetricTile, ScrollableFrame
  footer_view.py    # preview + output bar
  tab.py            # QualityTab: wires it together
```

Keep the model separate from widgets: widgets bind to model vars; the model knows the preset baseline, so "modified", reset and the preset status line all come from one place.

## Acceptance criteria

- [ ] At 1280×860 the tab has no outer scrollbar; only the detail panel scrolls, and only if needed.
- [ ] Every parameter from the old tab is reachable and writes to the same setting.
- [ ] Chain list order matches the real processing order.
- [ ] Toggling Enabled in the chain row and in the detail header stay in sync.
- [ ] Changing a value shows ↺ on that row, a dot on the chain row, and the preset "Modified" line; ↺ restores the preset value.
- [ ] "Reset to preset" resets only the selected stage.
- [ ] Neural enhance shows "not installed" and its checkbox is disabled when the extra is missing.
- [ ] No per-row Reset buttons, no Bypass/Show pairs, no echo labels.
- [ ] Waveform spans the full width for the whole file; legend doesn't overlap it.
- [ ] True peak > 0 dBTP is visibly flagged.
- [ ] Advice banner's "Turn it on" enables per-segment re-estimation and jumps to Denoise.
- [ ] Noise-floor-shift figure is plausible (or omitted).
- [ ] Keyboard: Tab reaches every control; Up/Down moves in the chain; Space toggles.

## Toolkit mapping (if not Tk)

| Tk / ttk | PySide6 / PyQt |
|---|---|
| ttk.PanedWindow | QSplitter |
| ChainRow list | QListView + custom delegate, or QListWidget with item widgets |
| DetailPanel swap | QStackedWidget (one page per stage) |
| ttk.Scale + Spinbox | QSlider + QDoubleSpinBox (with suffix) |
| tk.Canvas waveform | custom QWidget with paintEvent |
| ttk.Checkbutton Toolbutton | QToolButton(checkable=True) |
| ttk styles | QSS stylesheet |

## Reference

Interactive mockup: the "Quality Tab Redesign" Design canvas (click stages in the chain, move sliders, toggle stages, try "Turn it on" and "Hear original").
