# HPLC Analyzer Release Process

This document defines the release-candidate (RC) to Stable lifecycle. Japanese is the primary language for the release record; the fixed English section headings in `RELEASE_TEMPLATE.md` make each release easy to scan internationally.

## 1. Release identities

- Development versions use SemVer pre-release names such as `1.3.0-dev.1`. They are not tagged or published as Releases. Increment the development identifier when a distributed development build must be distinguished from an earlier one.
- RC versions use SemVer pre-release names such as `1.3.0-rc.1`; tags use `v1.3.0-rc.1`.
- Stable versions use `MAJOR.MINOR.PATCH`; tags use names such as `v1.3.0`.
- An RC GitHub Release must have **Set as a pre-release** enabled. A Stable GitHub Release must have it disabled and must not be marked as a draft when published.
- Tags are annotated tags. Lightweight tags and moving, deleting, or reusing a published tag are prohibited.
- `hplc_app/version.py` is the machine-readable application-version authority. Release documentation is reviewed separately; historical version references are not blindly replaced.

## 2. Commit and RC rules

1. Select a release commit already on `main` and record its full 40-character SHA as `VERIFIED_COMMIT`.
2. Run automated tests from that exact commit, build the RC assets, and create the annotated RC tag.
3. Create a GitHub prerelease from the RC tag and attach the candidate assets and checksums.
4. Perform Windows 11 and Windows 7 verification using assets built from `VERIFIED_COMMIT`.
5. If any source, dependency lock, build/installer input, migration, or release asset content must change, do not promote that RC. Merge the fix, increment the RC number, and restart all gates from the new commit.
6. After an RC is accepted, change only the canonical application version and directly related current-version documentation to the Stable version, for example `1.3.0`. This creates a new Stable-candidate commit because an `rc` commit cannot also contain the Stable `APP_VERSION`.
7. Run the complete automated, build, installer, upgrade/data-preservation, cross-OS, signing, checksum, and physical-machine gates again on artifacts built from that exact Stable-candidate commit. RC evidence informs this run but does not substitute for it.
8. If every Stable gate passes without another commit change, create the Stable annotated tag on that exact verified Stable-candidate commit. Never tag the earlier `rc` commit as Stable.

Example tag commands (replace values; do not run from an unclean worktree):

```text
git tag -a v1.3.0-rc.1 VERIFIED_COMMIT -m "HPLC Analyzer v1.3.0-rc.1"
git tag -a v1.3.0 VERIFIED_STABLE_COMMIT -m "HPLC Analyzer v1.3.0"
git rev-parse "v1.3.0-rc.1^{commit}"
git rev-parse "v1.3.0^{commit}"
git rev-parse VERIFIED_STABLE_COMMIT
```

The Stable tag and `VERIFIED_STABLE_COMMIT` results must be identical before publication. The RC tag is expected to resolve to an earlier commit with an RC `APP_VERSION`. Tag creation, tag push, and GitHub Release publication require explicit human approval.

## 3. Stable gates

Stable publication requires a completed copy of `RELEASE_CHECKLIST.md` and the release notes template. The release approver must confirm:

- application version, project format/schema, preset format, and database schema are recorded;
- the full automated suite passes in the supported Windows 11 and pinned Windows 7 Python/Qt environments;
- the Windows 11 x64 build, installer, and manual workflow pass on a Windows 11 x64 machine;
- the Windows 7 x86 build is performed offline on a Windows 7 SP1 32-bit/Core 2 machine, followed by installer and manual workflow verification on that machine;
- install-over-current-version and install-over-previous-version tests preserve projects, raw embedded sources, integrations, annotations, settings, presets, language choice, and lab database content;
- Win11-created projects open correctly on Win7 and Win7-created projects open correctly on Win11, within the documented backward-compatibility contract;
- raw arrays and scientific analysis results are unchanged unless the release explicitly declares and validates a scientific behavior change;
- Japanese and English UI smoke tests pass;
- every distributed asset has a final SHA-256 entry and the checksums have been independently checked;
- signing status is explicit. Until code signing is implemented, release notes must warn that installers are unsigned and may trigger Windows warnings;
- no Blocker/High issue involving security, data loss, scientific interpretation, project compatibility, build/install failure, or unbounded resource use remains open. A lower-severity known issue requires a documented impact, workaround, and approver acceptance.

Windows 7 verification cannot be replaced by a GitHub-hosted runner or a modern Windows compatibility mode.

## 4. Schema and downgrade policy

Every release record lists:

| Contract | Required value |
|---|---|
| Application version | Exact SemVer |
| Project format major | Integer |
| Project schema | Integer |
| Preset format | Integer |
| Lab database schema | Integer |

If project format/schema, preset format, or database schema changes, the release is blocked until all of the following are present:

- backward-loading or migration tests and Win11↔Win7 round trips;
- a user-visible backup instruction before upgrade;
- an explicit downgrade warning explaining that files saved or migrated by the new version may not reopen correctly in an older version;
- verified backup/restore steps for project files, `presets.json`, application settings, and the SQLite lab database.

## 5. Assets and checksums

Expected Stable assets are:

- Windows 11 x64 installer;
- Windows 7 x86 installer;
- Windows 7 complete offline build kit;
- `SHA256SUMS.txt` and the offline checksum checker when available;
- release notes and relevant license/notices bundled by the installers.

Generate final checksums only after filenames and bytes are final. After publishing Stable, download at least one installer, the offline kit, and `SHA256SUMS.txt` from GitHub Releases into a clean directory and verify them again. Record downloader, timestamp, asset sizes, and results in the release evidence.

## 6. Evidence and approval

Keep the completed checklist and release notes in the GitHub Release draft or a linked release-evidence Issue. Record the verifier's name, date/time, OS edition/architecture, hardware class, Python/Qt/NumPy versions, commit SHA, artifact filename/hash, and result. The release approver must be a human and must link the evidence when publishing Stable.

The updater is outside the v1.3.0 scope. A user installs v1.3.0 with the installer following the documented upgrade/data-preservation procedure.

## 7. Checklist dry run

Before the first RC of a minor release, copy `RELEASE_CHECKLIST.md` into a temporary release-evidence Issue marked `DRY RUN`. Fill the identity/schema fields with a valid RC example, walk through every command and evidence handoff without creating or pushing a tag, and confirm that no required gate can be mistaken for optional. Record unavailable physical-machine gates as `BLOCKED — evidence required`, not as passed or silently `N/A`. Update the templates before starting the real RC if the dry run exposes an ambiguous owner, artifact, or result field.

Run the read-only preflight against the filled Markdown copy. It changes no Git or GitHub state:

```text
python scripts/release_evidence_preflight.py path/to/evidence.md --mode dry-run
python scripts/release_evidence_preflight.py path/to/evidence.md --mode stable --json
```

Dry-run mode reports unavailable non-identity evidence as blockers while still rejecting malformed identities and placeholders. Stable mode treats every unchecked gate, blank evidence cell, placeholder, and explicit blocker as an error and exits nonzero until the record is complete.
