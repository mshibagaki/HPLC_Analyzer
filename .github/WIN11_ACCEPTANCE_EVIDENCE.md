# Windows 11 Acceptance Evidence

One filled copy per candidate commit. Follow `.github/WIN11_ACCEPTANCE_TEST.md`.
A blank field is not a pass.

## Provenance of this record

**Sessions 0-4 were run on the Windows 11 machine by the repository owner, who
reported on 2026-09-08 that all five gates pass.** This file records that report.

It is not a transcription of a filled worksheet, so the machine-specific and
measured fields below are marked `要記入` rather than guessed. **Those fields must
be completed by the verifier before this record can support a Stable release**,
because `.github/RELEASE_PROCESS.md` requires the verifier, hardware, commit SHA,
artifact hash and result for each gate.

The gate rows in `VALIDATION_BLOCKERS.md` are marked passed on the strength of the
owner's report. The blank fields here are what still separates that from a
complete release record.

- Tester and date/time: repository owner, 2026-09-08 (exact time 要記入)
- Source commit (40-character SHA): 要記入 — the build actually exercised. Current
  `main` was `f3f3725` on 2026-09-08. **If the sessions were run before #267 and
  #268 merged, sessions 1 and 2 need a re-check on the newer build**, because
  those two changed overview navigation and zoom behaviour that session 1 covers.
- Installer filename / SHA-256: 要記入
- Application version shown in the title bar: 1.3.0-dev.1
- Windows 11 edition and build: 要記入
- Display scaling actually in use (%): 要記入
- Display resolution: 要記入
- Pointer devices tested: 要記入
- UI language(s) tested: 要記入
- Printer make/model (session 4): 要記入
- Data used (session 2, at least 10 traces): 要記入
- Data used (session 3, quiet / noisy / crowded): 要記入

## Gate results

| Gate | Result | Notes |
|---|---|---|
| Windows 11 x64 installer | passed | Owner-reported, 2026-09-08. Session 0 covers the clean-install path only. The upgrade path and the protected-data byte comparison are `.github/INSTALLER_UPGRADE_TEST.md`, which needs the previous Stable installer and is still outstanding |
| Windows 11 PyQtGraph default desktop view | passed | Owner-reported, 2026-09-08 |
| 3D chromatogram view and export | passed | Owner-reported. Two follow-up requests came out of this session and are tracked separately: axis-label/number visibility and screen-versus-export text layout |
| Automatic peak detection on laboratory chromatograms | passed | Owner-reported, 2026-09-08 |
| Physical print path | passed | Owner-reported, 2026-09-08 |

Result is `passed` or `still blocked`. Anything else leaves the gate blocked.

## Session 0 — Installer

| Step | Result | Notes |
|---|---|---|
| Build from candidate commit | passed | |
| Clean install | passed | |
| Launch, version confirmed | passed | |
| Import `.gcd` | passed | |
| Import `.txt` | passed | |
| Save, close, reopen project | passed | |
| Export figure (PNG) | passed | |
| Export A4 report (PDF) | passed | |
| Uninstall | passed | |
| Reinstall and launch | passed | |

## Session 1 — Screen acceptance

| Group | Result | Failing checks and what was seen |
|---|---|---|
| A. Startup and renderer | passed | |
| B. Fonts, DPI and devices | passed | |
| C. Readability at native DPI | passed | |
| D. View-reset meaning | passed | |
| E. Flicker and zoom retention | passed | |
| F. Mouse modes | passed | |
| G. Selection and editing | passed | |
| H. Split panel | passed | |
| I. Fraction ranges | passed | |
| J. Conditions dialog | passed | |
| K. Line styles and colors | passed | |

Screenshots taken (filenames or links): 要記入

The overview panel's own interaction model was raised as a separate request in the
same session and is tracked as its own batch, not as a session 1 failure. The
group above covers the divider, the rectangles and the axis spacing, all of which
the owner reported as acceptable.

## Session 2 — 3D view and export

| Check | Result | Notes |
|---|---|---|
| 10+ traces rendered | passed | Number used: 要記入 |
| Compared against the notebook figure | passed | Differences: 要記入 |
| Japanese dialog usable at native DPI | passed | |
| Preview speed practical | passed | Approx. seconds: 要記入 |
| Z limits applied | passed | |
| Colors applied | passed | |
| Viewpoint applied and reset correct | passed | |
| PNG publication quality | passed | Text spacing differs from the preview; tracked separately |
| SVG publication quality | passed | Same note as PNG |
| PDF publication quality | passed | Same note as PNG |

## Session 3 — Automatic peak detection

Candidate counts per sensitivity:

| Trace kind | 低 | 中 | 高 | Meaningfully different? |
|---|---|---|---|---|
| Quiet | 要記入 | 要記入 | 要記入 | yes (owner-reported) |
| Noisy | 要記入 | 要記入 | 要記入 | yes (owner-reported) |
| Crowded | 要記入 | 要記入 | 要記入 | yes (owner-reported) |

| Check | Result | Notes |
|---|---|---|
| Mouse-selected range respected | passed | |
| Still respected after a time shift | passed | |
| Outside integrations intact | passed | |
| Three Preferences tabs usable | passed | |
| Reset restores that level's defaults | passed | |
| Reset applies only on OK | passed | |

## Session 4 — Physical print

| Check | Result | Notes |
|---|---|---|
| Current view printed | passed | |
| A4 analysis report printed | passed | |
| X-axis label separated from the peak table on paper | passed | |
| Page layout | passed | The peak table stretches to fill the page height when few peaks are integrated; raised as a separate request, not a print failure |
| Clipping | passed | |

Printed sheets kept or scanned (location): 要記入

## Failures raised

None. No session produced a failing check.

Three improvement requests came out of these sessions and are tracked as their own
batch rather than as gate failures: the overview panel's interaction model, the
fitted-peak area baseline and the report table's vertical stretch, and the 3D axis
label controls and screen-versus-export consistency.

## Sign-off

- [x] Every check is OK, or N/A with a reason recorded
- [x] `VALIDATION_BLOCKERS.md` rows updated for the gates this run closed
- [x] An Issue exists for every failure — none were raised
- [ ] The `要記入` fields above are completed by the verifier

Verifier signature / date: 要記入
