# Requirements implementation status

Updated: 2026-08-30 (Issue #166 delivery)

This tracked file is the source of truth for the original product requests. Update it in the same Issue/PR that changes a status. `Implemented` means the behavior and automated regression coverage are on `main`; it does not replace the physical release evidence in `VALIDATION_BLOCKERS.md`.

## Implemented on main

| Area | Delivered behavior |
|---|---|
| Batch condition editing | Direct table editing, validated all-or-nothing Ctrl+C/Ctrl+V, dropdown fields, Shift/Ctrl multi-selection, and gradient content preview/edit/assignment |
| Project and file workflow | New project in another window, Ctrl+S, current project path/name in the window, ASCII/project drag-and-drop, multi-file and directory import |
| Continuous directory import | Multiple project-owned work directories, labels, explicit reload, new-file detection, SHA-256 duplicate skip, and changed-path hold without silent replacement |
| Chromatogram list | Run ID and timestamp separated from label, shared label/column/gradient at Run level, group/ungroup controls, column display, drag-and-drop reorder, and delete confirmation. Issue #144: new Run IDs use acquisition date/time, persistent project-local sequence and initial label (unknown-datetime when unavailable); direct whole-Run rename rejects empty/colliding IDs without grouping, supports Undo/Redo and preserves existing IDs on migration |
| Selection and display | Shift range selection, Ctrl multi-selection, Y1/Y2 split-view mode, independent axes, Y-only zoom fix, grid toggle, vertical pointers with Delete, and fraction-collector intervals |
| Legends and labels | Configurable Run ID/label/timestamp/wavelength legend composition, optional spectrum/trace name in the B% legend, fixed-size text boxes, and retention/integration labels on all selected chromatograms. Issue #140: split-view B% is shown on both panels or hidden on both via the existing toggle; both show the selected trace's same gradient and B% scale, with one legend entry, in Matplotlib and the preview. A hidden/unconfigured selected gradient hides both |
| Presets | Preview/diff before apply, created/used/updated/name sorting, filter, rename/duplicate/delete, and import/export conflict policies |
| Output and reports | Current view print and clipboard copy; all/visible/selected report scopes; compact peak table; per-export baseline/range/retention-time/B% options |
| Scientific metadata | Run-owned analyte name, stable ID, aliases, source, molecular weight, extinction coefficients and units; persistence, export, and quantitation integration |
| Peak fitting | Non-destructive Gaussian and right-tailing EMG fitting, automatic model selection, RMSE/R²/AIC, and visual overlay |
| Preferences and colors | Persisted Japanese/English choice and default 280 nm blue-family / 214 nm red-family trace colors |
| Update foundations | Nonblocking Stable Release check; canonical asset selection; bounded download; SHA-256 and Authenticode probes; signer allow-list policy; progress/cancel cleanup; installer execution remains disabled |

## Remaining source work

| Priority | Requirement | Current boundary | Next coherent delivery |
|---|---|---|---|
| High | Spectrum-domain selection and retention labels | Chromatogram multi-selection/labels are complete, but there is no separate spectrum data model, importer, view, or shared chromatogram/spectrum selection contract | Specify supported spectrum source format and semantics, then add the persisted model/import/view before enabling common labels |
| High | Replace the interactive Matplotlib screen renderer | The session-only Qt 6 preview supports shared navigation, all display layouts, existing scientific editing workflows and native presentation parity. Issue #162 retains only a Matplotlib axes/view-state skeleton during preview, with complete high-quality artists rebuilt for figure export and fallback. Issue #164 adds peak-preserving screen-only min/max reduction and compatible detail/overview trace reuse. Issue #166 preserves those traces across gradient/peak/marker/fraction/annotation refreshes, retains identical static items, independently replaces changed overlays and clears stale hit targets. The plain 8 × 100,000-point benchmark remains 2.04x faster than legacy. The decorated benchmark reused both trace/static items with unchanged raw SHA-256 and measured 0.391 s native versus 0.364 s legacy: near parity but still about 7% slower. Win7 and defaults stay unchanged | Collect representative real-data and native desktop DPI/font/pointer/clipboard/print performance evidence. Optimize only measured remaining bottlenecks, then decide whether the Windows 11 default can change |
| Medium | Activate signed updater workflow | Check UI, non-launching verified-download API, background Qt worker, and bilingual progress/cancel/error dialog exist; the application deliberately exposes no installer download or execution | Configure an approved signer identity and validate signed fixtures before connecting the component to the end-user action; launch still requires an explicit confirmation design and physical validation |

The following are useful extensions, but are not gaps in the original requested minimum: a shared analyte master library beyond the persisted Run snapshot, multi-peak deconvolution/curved baseline fitting beyond Gaussian/EMG single-peak fitting, and the long-term TraceLab platform split.

## External and physical gates

Code signing identity, signed updater launch, Stable publication approval, Windows 11 installer checks, physical Windows 7 SP1 x86/Core 2 offline checks, cross-OS project/upgrade preservation, physical printing, and published-asset re-download remain open. Their exact required evidence is maintained in `VALIDATION_BLOCKERS.md`; none is considered passed from CI or offscreen tests.

## Maintenance rule

Before opening a new implementation Issue, check this file, `VALIDATION_BLOCKERS.md`, open GitHub Issues/PRs, and current `main` to avoid duplicate work. Merge one independent Issue before starting the next. A PR that implements or materially narrows a row must update this file and cite the Issue/PR in its summary.
