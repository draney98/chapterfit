# Prompt for the implementing AI — legibility rules (+ the Match screen)

> Paste everything below the line. Part 1 is a set of rules that apply to every screen. Part 2 is a checker that catches breaks automatically. Part 3 applies the rules to the Match screen in the latest screenshot.

---

The Match screen is hard to read. These rules apply to **every** screen from now on. Follow them literally. When a rule and the current code disagree, the rule wins.

## Part 1 — Rules

### A. Text size and fonts

1. **Sizes are real pixels: title 16, body 14, secondary 12.** Tk sizes are always passed negative, so Tk draws pixels and not points. Nothing sits outside 12–16 at the default base. The View menu scales the base from 12 to 18; secondary is base−2 and the title is base+2.
2. **Tables use the body size.** The editions table, the chapter table, and the inspector use the same 14 px. Treeview `rowheight` is 26, from `ui_kit`. The chapter table does not get a separate larger font.
3. **One family** (`ui_kit.FONT`) for all text, including section headers. "Book Info (written at Finish)" is in a different, rounded font today. **Mono only** for times, durations, ASINs and numeric values in tables — and with normal letter spacing (the "6 : 34 : 33" in the status bar is spaced out).
4. **Bold is for one thing per block:** a title, a verdict or a selected row. Not for labels.

### Surface and contrast

23. **One ground.** The window, timeline, step rail, and form hosts use `ui_kit.C["bg"]`. White (`C["panel"]`) is only for cards: the editions table, the verdict card, Book info, and text fields. A white rectangle on a gray rectangle with no card border is a bug.
24. **White text only on accent.** Body and muted text are dark on `bg` or `panel` (WCAG AA, 4.5:1). White is allowed on `C["accent"]` only: the primary button and the selected segment. Unselected segments use `C["text"]` on `C["sunken"]`. White on gray or white on sunken is not allowed.
25. **A label's drawn text fits its widget** (rules 5–8). Segmented controls are included. `debug_layout` must report a segment whose label is wider than its button. Fonts are real pixels (`ui_kit.font` / `ui_kit.tk_font`); a theme-default font must still draw at 12 px or larger and must not clip.
26. **A painted strip is a card.** The title, step rail, timeline, and workspace share one 14 px left and right inset. The timeline canvas draws marks, labels, levels, and the playhead another 14 px inside its own border, and stops 14 px before the opposite border. Time maps onto that inner width, and clicks use the same inset, so the first and last moment are not drawn on the border.

### B. Nothing overlaps, nothing is cut off

5. **Every pane has 16 px inner padding.** Today the book pane's content starts *left* of the window edge: "TIMELINE", the ASIN column and "ejected 0.9" are cut off at x = 0. The layout checker reports clipped text, including segmented-button labels (rule 25).
6. **Text never draws over another widget.** "rejected" in the Fit column runs into the inspector's "chooses and verifies…" line. That is the pane's own content overflowing; fix it with column widths (rule 8), not by moving the other widget.
7. **Truncation policy** — each kind of value has exactly one treatment:

   | Kind of value | Treatment |
   |---|---|
   | IDs (ASIN), times, numbers | Never truncated. The column is wide enough. |
   | Titles, names | End in "…" and show the full text in a tooltip. Never cut mid-glyph. |
   | Sentences | Wrap (`wraplength` = the container width minus padding). Never a single line that runs off. |

8. **Table columns have a minimum width equal to the header text or the widest value, whichever is larger.** Numeric columns are right-aligned. Exactly **one** column stretches (usually Title). "Runtir" and "Chapt" headers and "02V1OHIV" ASINs are not acceptable.
9. **Don't show a column that's empty for every row,** and don't show one whose value is the same in every row (Author, Source).

### C. Layout and alignment

10. **Forms:** labels in one left-aligned column of fixed width (90 px), no trailing colons, fields stretching to the right edge minus 16 px. One form style app-wide — today Book info uses right-aligned labels with colons while the search form above it uses left labels without.
11. **Fields fit their container.** No entry extends past its pane ("Anthony Burgess, Walter" is cut off). In a 400 px inspector, a form is **one column**, not two.
12. **Vertical rhythm:** 8 px between related rows, 16 px between sections, and a section label (12 px, muted, uppercase) at the top of each section. No floating unlabeled blocks of text.
13. **Big empty areas are a bug.** The editions table is ~70 % empty white while the inspector is crammed. Give space to what has content: a short table gets a short frame, and the verdict card goes directly under it.

### D. Words

14. **Never show internal identifiers or raw diagnostics.** No `find-asin`, no `ALL`, no `rejected 0.9`, no "ID3 tags: album, artist, date, encoder, title · no embedded chapters · album.nfo (46 - …)". Translate each one into a plain phrase (Part 3 lists the replacements), or move it to the Log.
15. **One word, one meaning.** Today "Source" names both the segmented control (ASIN/JSON/…) **and** a combobox (ALL), and the table has a "Source" column too. Rename so that each label is used once on a screen.
16. **Lead with the verdict, then the reason, then the numbers.** Don't run all three into one paragraph.
    - Bad: `Audnexus B00TXM4WT6 · 49 chapters · fit score 0.9 · rate spread 5.0% · runtime ratio 1.120 · rejected: most likely…`
    - Good: headline `Doesn't fit this audio`, then the sentence `The file is 12 % longer than this edition — usually a different recording.`, then the numbers on their own muted line.
17. **Helper text appears only where it helps,** in 12 px muted, under the thing it explains. Delete taglines like "chooses and verifies the chapter list against the audio".

### E. State and actions

18. **Show what's selected and what's in use.** The table needs a visible selection, and the edition currently in use (the ASIN in the header) is marked `● in use`.
19. **Buttons:**
    - one primary per screen, and **only when its action makes sense**
    - "Use this edition" can't be primary (or even enabled) while the selected edition is rejected; there it becomes a secondary "Use anyway…" with a confirmation
    - every other button is secondary (outlined)
    - today Search, Browse, Re-read and Try another edition are all identical filled blue
20. **Disable or hide controls that don't apply to the current mode:**
    - Browse… only exists in JSON mode
    - the search form only exists in Search mode
    - an ASIN field only exists in ASIN mode
21. **Data conflicts are flagged, not silent.** The header says "A Clockwork Orange" but the Book info title says "Clockwork Orange". Show a small inline note under the field — `Edition says "A Clockwork Orange" · Use` — rather than leaving it for the user to spot.
22. **The status bar reads `Idle`** or the running job. "Copy all — A Clockwork Orange — Match" is not a status.

## Part 2 — Add a layout checker and run it before every screenshot

Add `chaptertools/gui/debug_layout.py` and run it from the `--screenshot` command. **It must print zero problems before you show me a screenshot.**

```python
import tkinter.font as tkfont
from tkinter import ttk

MIN_FONT = 13

def check(root):
    problems = []

    def walk(w):
        try:
            w.update_idletasks()
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ww, wh = w.winfo_width(), w.winfo_height()
            p = w.master
            if p is not None and w.winfo_ismapped():
                px, py = p.winfo_rootx(), p.winfo_rooty()
                pw, ph = p.winfo_width(), p.winfo_height()
                if x < px or y < py or x + ww > px + pw + 1 or y + wh > py + ph + 1:
                    problems.append(f"OUTSIDE PARENT  {w!s}  ({x},{y},{ww}x{wh}) parent ({px},{py},{pw}x{ph})")
                if w.winfo_reqwidth() > ww + 2 and w.winfo_class() in ("Label", "TLabel", "Button", "TButton", "Entry"):
                    problems.append(f"CLIPPED  {w!s}  wants {w.winfo_reqwidth()} has {ww}")
            try:
                f = tkfont.nametofont(w.cget("font")) if isinstance(w.cget("font"), str) else tkfont.Font(font=w.cget("font"))
                if abs(f.actual("size")) < MIN_FONT:
                    problems.append(f"FONT TOO SMALL  {w!s}  {f.actual()}")
            except Exception:
                pass
            if isinstance(w, ttk.Treeview):
                f = tkfont.nametofont("TkDefaultFont")
                try:
                    f = tkfont.Font(font=ttk.Style().lookup("Treeview", "font"))
                except Exception:
                    pass
                for col in w["columns"]:
                    width = w.column(col, "width")
                    head = w.heading(col, "text")
                    widest = max([f.measure(str(head)) + 16] +
                                 [f.measure(str(w.set(i, col))) + 16 for i in w.get_children()])
                    stretch = w.column(col, "stretch")
                    if widest > width and not stretch:
                        problems.append(f"COLUMN TOO NARROW  {w!s}.{col}  needs {widest} has {width}")
        except Exception:
            pass
        for c in w.winfo_children():
            walk(c)

    walk(root)
    for p in problems:
        print(p)
    return problems
```

(CTk widgets wrap inner Tk widgets, so the checker sees the inner Label/Entry. That's fine — those are what get clipped.)

## Part 3 — Match screen, applied

```
book pane (16 px padding all round)
row 0  step rail
row 1  timeline (as before)
row 2  ┌ col 0, weight 1 ─────────────────────────────────┐ ┌ col 1, 400 px, scrolls ─────────────┐
       │ section("Editions")                    small(n)  │ │ section("Find the edition")         │
       │ TABLE (height = rows, max 10 rows, then scroll)   │ │ [Search|ASIN|JSON|Embedded|NFO]     │
       │ VERDICT CARD for the selected row (see below)     │ │ fields for that mode only           │
       │ (spare height stays below, empty and calm)        │ │ section("Book info")                │
       └───────────────────────────────────────────────────┘ │ one-column form + conflict notes    │
                                                              └─────────────────────────────────────┘
```

**Editions table columns:**

| Column | Width | Notes |
|---|---|---|
| (marker) | 24 | `●` = in use |
| ASIN | 130 | mono, never truncated |
| Title | stretch | |
| Narrator | 170 | "…" + tooltip for multiple narrators |
| Length | 80 | right-aligned, `h:mm` |
| Ch | 48 | right-aligned |
| Fit | 130 | `✓ fits` green · `✕ doesn't fit` red · `not checked` muted |

- **Remove** Author (same in every row) and Source (`find-asin` is internal). Group by how a row was found only if there's more than one way: a muted divider row `From ASIN lookup` / `From search`.
- **Every row gets a Fit value.** Run the fit check for each result in the background and fill it in as it completes; show `checking…` meanwhile. Empty Fit and Ch cells today make the table look broken.
- Sort: fits first, then by Fit score.

**Verdict card** (a `panel` directly under the table, full width of col 0):

```
Doesn't fit this audio                                   ← 16 bold, red (green "Fits this audio" when it fits)
The file is 12 % longer than this edition (6:34 vs 5:52). That usually means a different recording.
49 chapters · fit 0.9 · rate spread 5.0 % · runtime ratio 1.12          ← 12 muted, mono numbers
[Use anyway…]                        or, when it fits:   [Use this edition]  ← primary
```

- Write the reason sentence from the existing rejection logic. Each rejection reason maps to one plain sentence; keep the mapping in one dict.
- Delete the orphan `rejected 0.9` label at the bottom left.

**Inspector:**

- Segmented control: **Search** · ASIN · JSON · Embedded · NFO. Add Search; it's the mode the title/author fields belong to.
- **Search mode:** Title, Author, Keywords, **Catalog** (renamed from the second "Source"; options `All catalogs`, `Audible`, … — never show `ALL`), Region → `secondary("Search")`.
- **ASIN mode:** ASIN entry + `secondary("Check")`. No Browse.
- **JSON mode:** path entry + `secondary("Browse…")` + `secondary("Check")`.
- **Embedded / NFO modes:** one muted line saying what was found (`49 chapters in the file's tags`) + `secondary("Check")`.
- **Delete "Try another edition".** Switching mode or picking another row is how you try another edition.

**Book info** — one column, labels left at 90 px:

- Fields: Title, Author, Narrator, Album, Year, Genre, Comment. The Author field must show "Anthony Burgess, Walter …" in full on hover.
- **Conflict note** under any field that differs from the selected edition: `Edition says "A Clockwork Orange"` + `text_button("Use")`.
- Footer, 13 muted: `Written as tags at Finish · Found in file: title, artist, album, date · no chapters` + `text_button("Re-read from file")`. That replaces the raw ID3/nfo line.

**Status bar:** `Idle` · time `6:34:33` in normal mono · `Log (1)`.

## Done means

- [ ] `debug_layout.check()` prints nothing for Match at 1600×1030 and 1366×768.
- [ ] No text under 12 px or over 16 px at the default base; table text and inspector text are visibly the same size.
- [ ] Nothing starts left of the pane padding; nothing crosses into a neighbouring pane.
- [ ] Every edition row has Fit and Ch filled in (or `checking…`).
- [ ] The verdict reads as a headline + one sentence; there are no raw IDs or internal words on screen.
- [ ] Exactly one primary button, and it's disabled or absent when the selected edition doesn't fit.
