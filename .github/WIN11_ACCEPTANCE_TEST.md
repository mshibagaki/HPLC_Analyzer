# Windows 11 Acceptance Test

This is the procedure for the five release gates that a Windows 11 machine can
close on its own. It is not replaced by unit tests or offscreen captures.
`VALIDATION_BLOCKERS.md` states this explicitly: offscreen tests and captures do
not replace this evidence.

Record results in `.github/WIN11_ACCEPTANCE_EVIDENCE.md`, one filled copy per
candidate commit, and link it from `VALIDATION_BLOCKERS.md`.

## Gates this procedure closes

| Gate in `VALIDATION_BLOCKERS.md` | Session below |
|---|---|
| Windows 11 x64 installer | 0 |
| Windows 11 PyQtGraph default desktop view | 1 |
| 3D chromatogram view and export | 2 |
| Automatic peak detection on laboratory chromatograms | 3 |
| Physical print path | 4 |

Two further gates need this machine but not only this machine, so they are out of
scope here: **installer upgrade / data preservation** has its own procedure in
`.github/INSTALLER_UPGRADE_TEST.md`, and **Win11 ↔ Win7 project portability**
needs the Windows 7 build as well.

## Before you start

Prepare these once. Sessions 1-4 all depend on them.

- A Windows 11 x64 machine at its **native display scaling**. Do not test at 100%
  if the machine normally runs at 125% or 150% — the readability checks are about
  the real setting.
- A dedicated Windows test account, and **backed-up, non-production data**.
- The candidate commit's installer, built by `build_windows11.bat`.
- **At least 10 laboratory chromatograms** for session 2, and a set covering
  **quiet, noisy and crowded** traces for session 3. Real measurement data, not
  `sample_data/`.
- For session 2, the figure the Google Colab notebook produced for the same data,
  to compare against.
- A **physical printer** for session 4.
- Japanese UI selected for at least one pass. Several checks are about Japanese
  text fitting in its control.

## How to record a result

For each check write **OK**, **NG**, or **N/A**, and for anything that is not OK
write what you saw. A screenshot helps for layout and readability items.

Do not mark a check OK because a test passes or because it looked right in a
screenshot taken elsewhere. **These gates exist because the offscreen result and
the real machine have disagreed before.** Pan release was declared fixed on
passing tests in Issue #176 and was still broken on the machine; Issue #201 had to
redo it.

If a check fails, record it, finish the rest of the session, then open an Issue
with what you saw. Do not stop the whole session on one failure.

---

## Session 0 — Installer

Roughly 30 minutes. Do this first: everything else runs on what it installs.

1. Build the installer from the candidate commit with `build_windows11.bat`.
   Record the resulting filename and its SHA-256.
2. Clean install on the test account.
3. Launch. Record the version shown in the title and in ヘルプ → このソフトについて.
4. Import a `.gcd` file and a `.txt` file.
5. Save a project, close the application, reopen the project.
6. Export one figure (PNG) and one A4 analysis report (PDF).
7. Uninstall, then reinstall, then launch again.

Protected-data byte comparison and the upgrade path are **not** part of this
session. They are covered by `.github/INSTALLER_UPGRADE_TEST.md`, which needs the
previous Stable installer as well.

---

## Session 1 — Screen acceptance (PyQtGraph as the default)

The largest session; budget 2-3 hours. Work through the groups in order — a
failure in A makes the rest meaningless.

### A. Startup and renderer

- [ ] The application starts with **PyQtGraph** without being asked
- [ ] Switching the renderer persists across a restart
- [ ] When PyQtGraph cannot be used, it falls back to Matplotlib **and shows why**

### B. Fonts, DPI and devices

- [ ] Japanese fonts render correctly in the plot, axes, legend and dialogs
- [ ] Nothing is clipped or overlapping at the machine's **native** DPI
- [ ] Mouse, and a trackpad or pen if the machine has one, both work
- [ ] Both Y axes, the overview panel and the split panels all display correctly
- [ ] Copy to clipboard produces a usable image

### C. Readability at native DPI

- [ ] The compact legend / axis button row is readable
- [ ] The trace-position reset controls are readable
- [ ] The reorganized home table is readable and usable
- [ ] The long bilingual 解析 buttons fit their controls
- [ ] Selected rows still show their trace color (not inverted away)
- [ ] The source-path conditions column is usable
- [ ] The サブフォルダを含む wording reads clearly

### D. What the view-reset controls mean

- [ ] **Y軸全体** scales using only the currently visible X interval, not the whole trace
- [ ] **Home** gives the same result as the full-view action

### E. Flicker and zoom retention

Toggle each of these repeatedly and watch the plot.

- [ ] 積分範囲・ベースライン toggle: no flicker, zoom unchanged
- [ ] 保持時間ラベル toggle: no flicker, zoom unchanged
- [ ] グラジエント (%B) toggle: no flicker, zoom unchanged
- [ ] グリッド toggle: no flicker, zoom unchanged
- [ ] 凡例 toggle: no flicker, zoom unchanged
- [ ] Selecting peaks repeatedly in the main peak table: no flicker
- [ ] Selecting peaks repeatedly in the detached integration list: no flicker

### F. Mouse modes

- [ ] Every mouse mode works with the major grid **on**
- [ ] Every mouse mode works with the major grid **off**
- [ ] Choosing 通常 (pan/zoom) lets you drag the plot **immediately**, with no extra toolbar click
- [ ] Returning to 通常 does **not** silently replace an already-active zoom with pan
- [ ] With the major grid on, panning stays **two-directional** in single view
- [ ] With the major grid on, panning stays **two-directional** in split view
- [ ] The eight mouse-mode toolbar icons are discoverable next to the pointer icon
- [ ] Their Japanese tooltips are legible
- [ ] A **right-button drag** in 通常 mode reaches the rectangle zoom

### G. Selection and editing

- [ ] The unified selection mode selects integrations **and** vertical markers
- [ ] Delete removes everything selected as **one** action (one Undo step)
- [ ] Vertical-marker retention labels stay readable **while dragging**
- [ ] The integration-range dialog enters mouse selection with **no extra mode change**

### H. Split panel

- [ ] The divider between the upper and lower panels is discoverable
- [ ] It can be dragged
- [ ] The ratio it is set to is retained during the view

### I. Fraction ranges

- [ ] Numeric-only operation is practical with a **one-second** interval
- [ ] The divider-count cap prevents excessive rendering — try to exceed it deliberately

### J. Measurement / sample conditions dialog

- [ ] The two-row controls make element-selective copy/paste discoverable
- [ ] Run grouping is discoverable from the dialog
- [ ] The grouping confirmation names **every source Run and the target Run**
- [ ] The timestamp is **visibly unchanged** after grouping

### K. Line styles and colors

- [ ] All four trace line styles match between the two screen renderers
- [ ] They match in PNG, SVG and PDF output
- [ ] They match in the analysis report
- [ ] They remain distinct from the integration overlays
- [ ] The ordered gradient-color workflow is practical for many selected traces
- [ ] Its colors are visually consistent with the 3D color families

---

## Session 2 — 3D chromatogram view and export

Roughly 1 hour. Needs the 10+ real chromatograms and the earlier notebook figure.

- [ ] Select at least 10 laboratory chromatograms and produce the 3D figure
- [ ] Compare it against the figure the Colab notebook produced for the same data.
      Record any difference you would not accept in a publication
- [ ] The dialog is usable in Japanese at native DPI
- [ ] Preview speed is practical at that number of traces — record roughly how long
- [ ] The requested Z limits are applied
- [ ] The requested colors are applied
- [ ] The requested viewpoint is applied, and the view reset returns to the intended
      orientation
- [ ] PNG output is publication quality
- [ ] SVG output is publication quality
- [ ] PDF output is publication quality

The Windows 7 half of this gate — Matplotlib 3.7.5 / PySide2 preview and export
without changing the fixed dependency set — is not part of this procedure.

---

## Session 3 — Automatic peak detection on laboratory data

Roughly 1 hour. Needs quiet, noisy and crowded chromatograms.

For **each** of the three kinds of trace:

- [ ] 低 / 中 / 高 sensitivity produce **meaningfully different** candidate counts.
      Record the three counts
- [ ] Mouse-selected detection stays inside the intended displayed range
- [ ] It still does so **after a time shift** has been applied
- [ ] Integrations that already existed outside the selected range are **intact**

Then once:

- [ ] The three Preferences tabs are usable at native DPI
- [ ] Each reset action restores that level's defaults
- [ ] Reset does not apply until OK is pressed

The Windows 7 half of this gate is not part of this procedure.

---

## Session 4 — Physical print

Roughly 30 minutes. Needs a real printer.

- [ ] Print the current view through a real Windows printer driver
- [ ] Print the A4 analysis report through a real Windows printer driver
- [ ] On the printed report, the chromatogram X-axis label is **separated from the
      peak table** — this was Issue #182 and #252, confirm it survived on paper
- [ ] Record the page layout result
- [ ] Record any clipping

Keep the printed sheets, or scan them, as the evidence.

---

## Finishing

1. Fill `.github/WIN11_ACCEPTANCE_EVIDENCE.md` completely. A blank field is not a
   pass.
2. For each gate, decide **passed** or **still blocked**, and say why.
3. Update the matching rows in `VALIDATION_BLOCKERS.md`, changing only the rows
   this procedure covers, and link the evidence.
4. Open an Issue for each failure, with what you saw and on which check.

**A gate is passed only when every check in its session is OK or explicitly N/A
with a reason.** Partial completion leaves the gate blocked.
