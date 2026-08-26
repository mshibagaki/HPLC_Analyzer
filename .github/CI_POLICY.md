# Level 2 CI Policy

`workflows/validation.yml` runs for pull requests, updates to `main`, and manual dispatch. It is a validation workflow, not a release builder.

## Responsibilities

The Ubuntu source-contract job checks Python 3.8 grammar, exact dependency pins, the canonical application version, build/installer naming, installer definitions, sample fixture presence, and CI failure behavior. The Windows Server 2022 job installs the fully pinned Windows 11 dependency set and runs imports plus the complete core/offscreen GUI suite, including existing sample ASCII import, project round-trip, migration, scientific invariance, and export tests.

The job does not produce or upload executables, installers, offline kits, caches, or test artifacts. This reduces private-repository storage and avoids treating CI outputs as release assets. Redundant runs on the same pull request are cancelled, jobs have time limits, and the token has only `contents: read`. Third-party actions are pinned to full commit SHAs; the accompanying version comments are informational. Dependabot or a dedicated reviewed maintenance change should update action SHAs.

## Windows 7 offline assets

`win7_offline/` is intentionally ignored because it contains large third-party installers and wheels. A normal GitHub-hosted checkout therefore prints an explicit `[SKIP]` for binary manifest/wheelhouse verification while still enforcing the Win7 requirements pins and offline build source contracts.

Do not place third-party binaries, download credentials, private URLs, or encryption keys in workflow YAML, repository secrets echoed to logs, Actions caches, or uploaded artifacts. To verify the binary payload, obtain it from the laboratory's approved restricted store on a trusted preparation machine, place it at `HPLC_Analyzer_MVP_1.2.4/win7_offline/`, and run:

```text
python scripts/ci_validate.py --offline-assets required
```

This required mode fails if the payload is absent and validates both `MANIFEST.sha256` and the pinned CPython 3.8 win32 wheel dependency closure when present. The physical Windows 7 offline build retains its own mandatory checks before installation or compilation.

## Release boundary

A failed required workflow blocks Release readiness. A successful workflow proves only source-level and Windows Server test contracts. It does not replace:

- a Windows 11 x64 installer build and manual test on Windows 11;
- the fully offline build and installer test on physical Windows 7 SP1 32-bit/Core 2 hardware;
- upgrade/data-preservation, cross-OS project, physical printing, checksum, signing-state, or human approval gates;
- code signing, automatic GitHub Release publication, or an updater.

Branch protection should require both `Source contracts (Python 3.8 / release inputs)` and `Windows source, core, and offscreen GUI tests` after the workflow succeeds on the repository's private-Action budget.
