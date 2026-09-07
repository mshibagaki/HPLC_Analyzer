# Windows 11 Acceptance Evidence

One filled copy per candidate commit. Follow `.github/WIN11_ACCEPTANCE_TEST.md`.
A blank field is not a pass.

- Tester and date/time:
- Source commit (40-character SHA):
- Installer filename / SHA-256:
- Application version shown in the title bar:
- Windows 11 edition and build:
- Display scaling actually in use (%):
- Display resolution:
- Pointer devices tested:
- UI language(s) tested:
- Printer make/model (session 4):
- Data used (session 2, at least 10 traces):
- Data used (session 3, quiet / noisy / crowded):

## Gate results

| Gate | Result | Notes |
|---|---|---|
| Windows 11 x64 installer | | |
| Windows 11 PyQtGraph default desktop view | | |
| 3D chromatogram view and export | | |
| Automatic peak detection on laboratory chromatograms | | |
| Physical print path | | |

Result is `passed` or `still blocked`. Anything else leaves the gate blocked.

## Session 0 — Installer

| Step | Result | Notes |
|---|---|---|
| Build from candidate commit | | |
| Clean install | | |
| Launch, version confirmed | | |
| Import `.gcd` | | |
| Import `.txt` | | |
| Save, close, reopen project | | |
| Export figure (PNG) | | |
| Export A4 report (PDF) | | |
| Uninstall | | |
| Reinstall and launch | | |

## Session 1 — Screen acceptance

| Group | Result | Failing checks and what was seen |
|---|---|---|
| A. Startup and renderer | | |
| B. Fonts, DPI and devices | | |
| C. Readability at native DPI | | |
| D. View-reset meaning | | |
| E. Flicker and zoom retention | | |
| F. Mouse modes | | |
| G. Selection and editing | | |
| H. Split panel | | |
| I. Fraction ranges | | |
| J. Conditions dialog | | |
| K. Line styles and colors | | |

Screenshots taken (filenames or links):

## Session 2 — 3D view and export

| Check | Result | Notes |
|---|---|---|
| 10+ traces rendered | | Number used: |
| Compared against the notebook figure | | Differences: |
| Japanese dialog usable at native DPI | | |
| Preview speed practical | | Approx. seconds: |
| Z limits applied | | |
| Colors applied | | |
| Viewpoint applied and reset correct | | |
| PNG publication quality | | |
| SVG publication quality | | |
| PDF publication quality | | |

## Session 3 — Automatic peak detection

Candidate counts per sensitivity:

| Trace kind | 低 | 中 | 高 | Meaningfully different? |
|---|---|---|---|---|
| Quiet | | | | |
| Noisy | | | | |
| Crowded | | | | |

| Check | Result | Notes |
|---|---|---|
| Mouse-selected range respected | | |
| Still respected after a time shift | | |
| Outside integrations intact | | |
| Three Preferences tabs usable | | |
| Reset restores that level's defaults | | |
| Reset applies only on OK | | |

## Session 4 — Physical print

| Check | Result | Notes |
|---|---|---|
| Current view printed | | |
| A4 analysis report printed | | |
| X-axis label separated from the peak table on paper | | |
| Page layout | | |
| Clipping | | |

Printed sheets kept or scanned (location):

## Failures raised

| Session / check | Issue | Summary |
|---|---|---|

## Sign-off

- [ ] Every check is OK, or N/A with a reason recorded
- [ ] `VALIDATION_BLOCKERS.md` rows updated for the gates this run closed
- [ ] An Issue exists for every failure

Verifier signature / date:
