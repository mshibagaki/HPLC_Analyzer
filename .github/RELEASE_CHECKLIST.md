# HPLC Analyzer Release Checklist

Copy this checklist into the GitHub Release draft or a linked release-evidence Issue. Replace every placeholder and check every applicable box. A blank required field blocks Stable publication.

## Release identity

- [ ] Candidate version: `__________`
- [ ] Stable version: `__________`
- [ ] `VERIFIED_RC_COMMIT` (40-character SHA): `________________________________________`
- [ ] `VERIFIED_STABLE_COMMIT` containing the Stable `APP_VERSION` (40-character SHA): `________________________________________`
- [ ] Final RC annotated tag: `__________`
- [ ] Stable annotated tag: `__________`
- [ ] RC GitHub Release has the prerelease flag enabled.
- [ ] Stable GitHub Release will have the prerelease flag disabled.
- [ ] `git rev-parse "FINAL_RC_TAG^{commit}"` equals `VERIFIED_RC_COMMIT`.
- [ ] `git rev-parse "STABLE_TAG^{commit}"` equals `VERIFIED_STABLE_COMMIT`; it is expected to differ from the RC commit because `APP_VERSION` changed from RC to Stable.
- [ ] Every required gate was repeated on assets built from `VERIFIED_STABLE_COMMIT` before the Stable tag was created.
- [ ] If the RC changed, a new RC number was used and every gate was repeated.

## Versions and formats

- [ ] `hplc_app/version.py` contains the intended application version.
- [ ] `scripts/read_version.py` reports valid SemVer and Windows numeric versions.
- [ ] Project format major: `____`
- [ ] Project schema: `____`
- [ ] Preset format: `____`
- [ ] Lab database schema: `____`
- [ ] GUI/About, saved project, preset, DB, build output names, and installer metadata agree.
- [ ] If any schema/format changed, backup instructions and a downgrade warning are in Upgrade notes.

## Automated validation

- [ ] Python 3.8 grammar check passes for changed Python files.
- [ ] Core and GUI test suites pass on the Windows 11 supported environment.
- [ ] Core and GUI test suites pass with Python 3.8.10 x86, PySide2 5.15.2.1, NumPy 1.20.3, and Matplotlib 3.7.5.
- [ ] Sample ASCII/GCD import and project save→reload tests pass.
- [ ] Win11↔Win7 project compatibility and migration tests pass.
- [ ] Raw arrays and scientific result invariance are confirmed or an intentional change is documented.

## Windows 11 x64 evidence

| Field | Evidence |
|---|---|
| Verifier / date | |
| Windows edition/build | |
| Machine / architecture | |
| Python / Qt / NumPy / Matplotlib | |
| Commit SHA | |
| Installer filename / SHA-256 | |
| Build and installer result | |
| Launch, import, analyze, save/reopen, export, print, uninstall | |
| Japanese / English UI | |

- [ ] Windows 11 installer upgrade from the previous Stable version preserves user data.

## Windows 7 SP1 x86 evidence

| Field | Evidence |
|---|---|
| Verifier / date | |
| Windows edition/build | |
| Core 2 machine / x86 confirmation | |
| Python 3.8.10 x86 / PySide2 / NumPy 1.20.3 / Matplotlib 3.7.5 | |
| Offline status and commit SHA | |
| Installer filename / SHA-256 | |
| Build and installer result | |
| Launch, import, analyze, save/reopen, export, print, uninstall | |
| Japanese / English UI | |

- [ ] The build ran on physical Windows 7 SP1 32-bit/Core 2 hardware without network access.
- [ ] Windows 7 installer upgrade from the previous Stable version preserves user data.

## Upgrade and data preservation

- [ ] Fresh install works on both supported OS targets.
- [ ] Same-version repair/update install works.
- [ ] Previous-Stable→candidate upgrade works.
- [ ] Projects, embedded raw data, peaks/integrations, annotations, presets, settings, saved UI language, and lab SQLite database are preserved.
- [ ] Uninstall behavior is recorded; user data is not silently deleted.
- [ ] Backup and restore of projects, settings/presets, and database were exercised.
- [ ] Downgrade behavior is tested or an explicit warning is provided.

## Assets, notes, and approval

- [ ] Windows 11 x64 installer attached.
- [ ] Windows 7 x86 installer attached.
- [ ] Windows 7 Offline Build Kit attached.
- [ ] `SHA256SUMS.txt` covers every distributed binary/archive.
- [ ] Every checksum verifies before upload.
- [ ] Release notes use `RELEASE_TEMPLATE.md` and list Highlights, Added, Changed, Fixed, Compatibility, Known issues, and Upgrade notes.
- [ ] Windows 11, Windows 7, project compatibility, all format/schema versions, and updater exclusion are explicit.
- [ ] Signing state is explicit; unsigned installer warning is present when applicable.
- [ ] No disallowed Blocker/High issue remains; accepted lower-severity issues have impact, workaround, and approver.
- [ ] Release approver: `__________`  Approval date/time: `__________`

## Post-publication spot check

- [ ] Stable GitHub Release is not Draft and not prerelease.
- [ ] From a clean directory, re-download one installer, the Offline Build Kit, and `SHA256SUMS.txt`.
- [ ] Re-downloaded sizes and SHA-256 values match the published list.
- [ ] Spot-check verifier / date / result: `________________________________________`
- [ ] Release evidence link: `________________________________________`
