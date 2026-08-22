# AGENTS.md — HPLC Analyzer

## Project purpose
HPLC Analyzer is a desktop application for importing, visualizing, analyzing, integrating, annotating, exporting, and saving chromatographic data exported as Shimadzu GCsolution/LCsolution ASCII text.

The repository currently targets two Windows environments:
- Windows 11 x64: primary/high-quality build
- Windows 7 SP1 32-bit: legacy/offline/lightweight build for old laboratory PCs

Preserve the scientific meaning of imported and saved data. Avoid silent changes that could alter numerical results, project compatibility, or chromatogram interpretation.

## Repository structure
Key files/directories include:
- `app.py` — application entry point
- `hplc_app/` — application modules
  - `analysis.py` — analysis/integration-related logic
  - `database.py` — database-related logic
  - `dialogs.py` — dialogs/UI helpers
  - `exporters.py` — export functions
  - `gui.py` — main GUI behavior
  - `i18n.py` — localization
  - `models.py` — data models
  - `naming.py` — naming helpers
  - `parser.py` — Shimadzu ASCII parsing
  - `preset_store.py` — persistent presets/settings
  - `project_io.py` — project save/load
  - `qt_compat.py` — Qt/platform compatibility
  - `rendering.py` — chromatogram rendering
  - `report.py` — reports
- `tests/` — automated tests
- `sample_data/` — sample Shimadzu ASCII data
- `requirements-win11.txt` — Windows 11 dependencies
- `requirements-win7.txt` — Windows 7 dependencies
- `requirements-win7-bootstrap.txt` — Windows 7 bootstrap dependencies
- `build_windows11.bat` — Windows 11 build
- `build_windows7.bat` / `build_windows7_offline.bat` — Windows 7 builds
- `installer/` — Inno Setup installer definitions
- `scripts/` — build verification and packaging utilities

## Critical compatibility rules

### Windows 7 legacy build
Windows 7 compatibility is a hard requirement.

Do NOT upgrade, replace, or relax Windows 7 dependency pins unless the task explicitly requests it.

Known working legacy environment:
- Windows 7 SP1 32-bit
- Python 3.8.10 x86
- NumPy 1.20.3
- Pillow 9.5.0
- PySide2 5.15.2.1
- PyInstaller 5.13.2
- Matplotlib 3.7.5
- required compatibility packages include `importlib_resources` and `zipp`

Important historical constraint:
- NumPy 1.24.4 caused startup failure with `0xc000001d` on an old Core 2 Windows 7 machine.
- NumPy 1.20.3 was confirmed to start successfully on that machine.

Therefore:
- Never modernize Win7 dependencies as incidental cleanup.
- Never assume a package that works on Windows 11 works on Windows 7 x86.
- Keep the Win7 build offline-capable.
- Prefer Win7-specific optimizations over degrading the Windows 11 experience.

### Windows 11 build
Preserve the higher-quality Windows 11 experience unless a task explicitly requests otherwise.
Do not reduce rendering quality or remove features globally merely to improve Win7 performance.

## Project-file compatibility
Project files are intended to be portable between supported builds.

When modifying `project_io.py`, models, serialization, metadata, presets, or schema-related code:
- Preserve backward compatibility with existing project files whenever practical.
- Do not silently rename/delete persisted fields.
- If a format change is unavoidable, add explicit migration/backward-loading logic.
- Test Win7-created project files on Win11 and Win11-created project files on Win7 when the change can affect serialization.

A known area requiring care is project display compatibility between Win7 and Win11.

## Chromatogram import rules
The primary input is Shimadzu GCsolution/LCsolution ASCII text data.
Typical datasets contain metadata, optional peak tables, and chromatogram sections such as `[Chromatogram (Ch1)]` with retention time and intensity values.

When modifying parsing:
- Preserve original numerical precision where possible.
- Do not assume every file has identical metadata or peak-table sections.
- Fail clearly on malformed/unsupported input instead of silently fabricating values.
- Keep sample files in `sample_data/` useful as regression fixtures.

## Scientific/numerical behavior
Changes to integration, baseline handling, peak detection, unit conversion, smoothing, interpolation, or export must be treated as scientific behavior changes.

For these changes:
- Keep raw imported values unchanged unless the user explicitly requests transformation.
- Distinguish raw data from derived/display-only data.
- Avoid hidden smoothing or interpolation.
- Preserve units and labels.
- Add/update tests whenever numerical behavior changes.

## UI behavior that should be preserved
Unless the task explicitly requests a redesign, preserve existing interaction patterns, including:
- multi-trace chromatogram display
- dual Y axes when used
- independent X/Y zoom and pan behavior
- manual integration workflow
- peak list interaction
- project save/load
- metadata display
- gradient information/presets
- image/data export

Do not remove existing keyboard/mouse functionality while implementing a new interaction.

## Presets and persistent settings
Gradient presets and detailed-condition presets are application-level settings and should persist across projects.
Avoid accidentally moving persistent global settings into individual project files unless explicitly requested.

## Build and validation expectations
Before declaring a change complete, run the most relevant checks available for the change.

At minimum, consider:
1. Python syntax/import check
2. relevant `tests/` tests
3. sample ASCII import
4. project save -> reload
5. chromatogram rendering
6. export path if modified
7. build verification scripts if dependencies/build logic changed

For Windows 7-related changes, inspect the dedicated verification scripts in `scripts/` and do not claim Win7 compatibility solely because the code works on a modern machine.

## Scope discipline
Make the smallest coherent change that satisfies the requested task.
Avoid unrelated refactors, dependency upgrades, mass formatting, or architecture rewrites unless explicitly requested.

If a requested feature requires a risky cross-cutting change, explain the affected modules and compatibility implications in the PR/task summary.

## Versioning
Use semantic versioning for releases.
Typical interpretation:
- patch: bug fixes and small compatible improvements
- minor: new backward-compatible features
- major: breaking compatibility or major redesign

Do not change the application version unless the task explicitly requests a version bump.

## Generated/binary files
Do not add normal build outputs or local environments to source control.
Common exclusions include:
- `build/`
- `dist/`
- `__pycache__/`
- virtual environments
- temporary logs/cache files

Large Win7 offline installers/wheels should normally remain outside ordinary Git history unless explicitly required for the repository distribution strategy.

## How to work on tasks
For each task:
1. Read the relevant modules before editing.
2. Identify compatibility-sensitive areas (Win7, project schema, numerical analysis).
3. Implement the smallest safe change.
4. Add/update tests where appropriate.
5. Run relevant checks.
6. Summarize changed files, behavior, tests, and any remaining risks.

Do not claim a test, build, or compatibility check was performed unless it actually was.

## Git workflow

- Never commit directly to `main`.
- Use one working branch for each feature or bug fix, named `codex/...` by default.
- Commit and push all changes to that working branch.
- Open a Draft Pull Request targeting `main`.
- Do not merge the Pull Request unless explicitly instructed by the user.
- Do not mix unrelated changes in one Pull Request.
- Changes related to Windows 7 compatibility must preserve the pinned legacy dependencies and offline-build requirements described above.
