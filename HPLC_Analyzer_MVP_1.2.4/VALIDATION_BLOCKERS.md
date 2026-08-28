# External blockers and deferred physical validation

Updated: 2026-08-28

This file records work that source changes and automated CI cannot complete alone. An unavailable physical or external gate is `BLOCKED — evidence required`; it is never treated as passed or silently marked not applicable. Release execution remains governed by `.github/RELEASE_PROCESS.md` and `.github/RELEASE_CHECKLIST.md`.

Version context: latest published Stable is `1.2.4`; current main development line is `1.3.0-dev.1`. No `1.3.0` tag or Release exists yet.

## Current hard blockers

| Area | Status | Why source work cannot finish it | Evidence or decision required |
|---|---|---|---|
| Public code-signing identity | BLOCKED — external account and identity required | Authenticode can be probed in code, but the project has no approved production certificate, subject, thumbprint, or timestamp service | Select Microsoft Artifact Signing where available, otherwise a public-CA OV Authenticode certificate; complete organization/identity verification; record expected signer subject/thumbprint and timestamp policy |
| Updater installer launch | BLOCKED — signing identity and signed fixture required | Download, SHA-256, and Authenticode foundations exist, but launch is intentionally always disabled | Verify a signed installer and manifest produced by the release pipeline; pin the approved signer identity; approve the user-confirmed launch workflow |
| Stable GitHub Release publication | BLOCKED — human release approval required | Tags and Releases are external publication actions and require completed evidence | Human approval of the exact verified commit, annotated tag, final assets, checksums, release notes, and signing status |

## Mandatory physical release gates not yet satisfied for the current candidate

| Gate | Current status | Required evidence |
|---|---|---|
| Windows 11 x64 installer | BLOCKED — evidence required | Build the installer from the candidate commit; clean install, launch, import, save, figure/report output, upgrade, uninstall, reinstall, and protected-data byte comparison |
| Windows 7 SP1 x86/Core 2 offline build | BLOCKED — physical hardware required | Build without network access on physical Windows 7 SP1 32-bit/Core 2; dependency isolation, normal/debug startup smoke tests, installer, import, save, and figure output |
| Installer upgrade/data preservation on both OS targets | BLOCKED — installers and machines required | Complete `.github/INSTALLER_UPGRADE_TEST.md` and one `.github/INSTALLER_UPGRADE_EVIDENCE.md` record per OS |
| Win11 ↔ Win7 project portability | BLOCKED — both OS builds required | Open/save representative projects in both directions and record schema/migration/display results |
| Physical print path | BLOCKED — printer evidence required | Print current view and A4 analysis report through a real Windows printer driver; record page layout and clipping results |
| Final Release asset re-download | BLOCKED — published draft assets required | Download installer/offline kit/`SHA256SUMS.txt` into a clean directory and run the documented checksum verification |

## Automated or offscreen evidence already available

These results reduce implementation risk but do not replace the physical gates above.

- The non-GUI suite passed 117 tests for the Issue #106 candidate, including backend-neutral trace/gradient/peak-overlay non-mutation, update metadata, bounded downloads, project migrations, numerical invariance, import, presets, and release-source contracts.
- The Windows/PySide6 offscreen GUI suite passed 112 tests for the Issue #106 candidate, including the production Matplotlib path consuming integration, baseline, fit, and retention-label scene specs.
- The Win11 isolated renderer benchmark measured about 2.25x improvement for the high-density PyQtGraph case; the parity probe reported all 11 representative features supported, including independent Y2 and B% axes and a 1000x700 snapshot.
- Windows 7 dependency pins, offline-build scripts, installer definitions, manifests when present, and source contracts are automatically checked. GitHub CI does not contain the ignored third-party offline payload and does not produce a Windows 7 executable.
- Historical Windows 7/Core 2 evidence established that NumPy 1.24.4 fails with `0xc000001d` and NumPy 1.20.3 starts. This supports the existing pin but is not evidence for the current candidate installer.

## Work that can continue without these blockers

- Release asset discoveryとfail-closed signer-policy基盤は実装済み。production signer thumbprintの設定はidentity取得後に継続し、installer launchはそれまで無効のまま維持する。
- Updater download progress/cancel/error presentation is implemented with a background Qt worker and synthetic fixtures, without offering execution. Connecting it to the end-user action remains blocked on the approved signer identity and signed release fixture.
- PyQtGraph production adapter behind an opt-in Windows 11 setting with Matplotlib fallback. Trace/Y-axis/legend/B%, integration, baseline, fit, and retention-label scene composition is backend-neutral and consumed by the current GUI; markers, fractions, free text, picking, and navigation still need extraction/adaptation. Changing defaults still requires compatibility evidence.
- Application-level features, tests, documentation, and project migrations that preserve the pinned Windows 7 dependency set.
- Release checklist dry runs that create no tag or public Release; `scripts/release_evidence_preflight.py` now distinguishes malformed records from explicit dry-run blockers and rejects both for Stable publication.

## Evidence handoff

When evidence becomes available, record the date/time, verifier, OS edition/architecture, hardware class, commit SHA, application/dependency versions, artifact filename and SHA-256, exact procedure, and result. Link the evidence Issue or Release draft here and change only the corresponding row; do not infer completion from a related automated test.
