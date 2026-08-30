# AGENTS.md — HPLC Analyzer

## Project purpose
HPLC Analyzer is a desktop application for importing, visualizing, analyzing, integrating, annotating, exporting, and saving chromatographic data exported as Shimadzu GCsolution/LCsolution ASCII text.

The repository currently targets two Windows environments:
- Windows 11 x64: primary/high-quality build
- Windows 7 SP1 32-bit: legacy/offline/lightweight build for old laboratory PCs

Preserve the scientific meaning of imported and saved data. Avoid silent changes that could alter numerical results, project compatibility, or chromatogram interpretation.

## Repository structure
The tracked application source lives directly at the repository root. Keep
`README.md`, `app.py`, `hplc_app/`, `tests/`, build inputs, and installer inputs
there; do not introduce a version-numbered source wrapper directory. Record
versions in `hplc_app/version.py`, SemVer tags, and GitHub Releases instead.

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

Release tags and GitHub Releases require explicit human approval. Use annotated tags and never move or reuse a published tag. An RC commit contains an RC `APP_VERSION`; after acceptance, create a Stable-version candidate commit and repeat every gate on its rebuilt assets. The final Stable tag must resolve to that exact verified Stable-candidate commit, not the earlier RC commit. Do not publish Stable until the repository Release checklist has complete Windows 11 and physical Windows 7 evidence, upgrade/data-preservation results, final checksums, release notes, and signing status.

## Generated/binary files
Do not add normal build outputs or local environments to source control.
Common exclusions include:
- `build/`
- `dist/`
- `__pycache__/`
- virtual environments
- temporary logs/cache files

Large Win7 offline installers/wheels should normally remain outside ordinary Git history unless explicitly required for the repository distribution strategy.

## Shared agent documents
Every assistant working in this repository follows the same documents. `CLAUDE.md` is only an index to them and holds no rules of its own.

- `AGENTS.md` (this file) — repository rules; nothing below overrides it.
- `REQUIREMENTS_STATUS.md` — the ledger of implemented and remaining requirements.
- `VALIDATION_BLOCKERS.md` — external and physical validation gates.
- `WIN11_FEEDBACK_WORKFLOW.md` — the current work plan for the Windows 11 field-feedback requests: batch definitions, order and dependencies, per-batch acceptance criteria, verification commands, and the technical findings collected before implementation.
- `CODEX_BRIEF.md` — the work order for those batches: what is startable now, the per-issue procedure, and the constraints worth repeating. A hand-off brief rather than a rule file; it applies to any assistant despite its name.
- `ACKNOWLEDGEMENTS.md` — people whose original design, method, or algorithm became a feature. Keep it distinct from `THIRD_PARTY_NOTICES.txt`, which carries third-party software license notices. Confirm with the person before changing how their name is written.

Do not create assistant-specific rule files. Repository rules belong here; current work-plan detail belongs in the workflow document; the current work order belongs in the brief.

## How to work on tasks
For each task:
1. Read `REQUIREMENTS_STATUS.md`, `VALIDATION_BLOCKERS.md`, `WIN11_FEEDBACK_WORKFLOW.md`, and the relevant modules before editing. Treat the tracked status files, the workflow document, and current `main` as authoritative over local/untracked planning notes.
2. Identify compatibility-sensitive areas (Win7, project schema, numerical analysis).
3. Implement the smallest safe change.
4. Add/update tests where appropriate.
5. Run relevant checks.
6. Summarize changed files, behavior, tests, and any remaining risks.

Do not claim a test, build, or compatibility check was performed unless it actually was.

## Git workflow

- Never commit directly to `main`.
- Treat one GitHub Issue as one delivery unit: one working branch, one Pull Request, and one merge by default.
- Before starting an Issue, fetch the remote state and confirm that the Issue, branch, commit, or equivalent implementation is not already open or merged.
- Update `REQUIREMENTS_STATUS.md` in the same PR whenever a requirement moves between remaining, implemented, or externally blocked states.
- Start each independent Issue from the latest `origin/main`, using a `codex/...` branch by default. Never start the next independent Issue from an unmerged feature branch.
- Keep the branch limited to that Issue. Commit and push the verified change, then open a Pull Request targeting `main`.
- Use a Draft Pull Request only while required implementation or verification is still in progress. Mark it ready, or create a non-draft Pull Request, once the Issue's checks pass.
- After checks pass and no unresolved review finding, conflict, compatibility risk, or branch-protection requirement remains, merge the Pull Request before starting the next independent Issue. The repository owner has given Codex standing authorization to perform this routine merge.
- After merging, fetch and fast-forward local `main`, confirm the Issue branch is contained in `origin/main`, and use that updated `main` as the next Issue's base.
- Stack branches or Pull Requests only when there is a real code/data/schema dependency that prevents independent delivery. Record the dependency in both PRs, set the dependent PR's base deliberately, and merge in dependency order.
- Do not accumulate unrelated completed branches for a later integration PR. Do not mix unrelated changes in one Pull Request.
- Stop before merging when tests fail, the PR is not mergeable, review findings remain, or the change has an unresolved scientific, migration, security, or user-data risk. Report the blocker instead of beginning another Issue on top.
- Changes related to Windows 7 compatibility must preserve the pinned legacy dependencies and offline-build requirements described above.
