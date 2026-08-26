# Installer Upgrade and User-Data Preservation Test

This is a release gate for both supported installer targets. It is not replaced by unit tests. Run it with a dedicated Windows test account and backed-up, non-production data. Windows 7 evidence must come from a physical Windows 7 SP1 32-bit/Core 2 machine.

## Protected contract

The installer must not create, move, rewrite, or delete any of the following user-owned state:

- the complete `HKEY_CURRENT_USER\Software\Research Tools\HPLC Analyzer` QSettings tree, including UI language, import/save/last-used paths, rendering quality, and database path;
- `presets.json`, condition presets, gradient presets, and their metadata;
- the laboratory SQLite database;
- `.hplcproj` files and their embedded raw source bytes;
- original raw ASCII files;
- user export files.

The application may migrate supported data only after it is launched and only under its documented format rules. The installer itself must leave every protected byte unchanged.

## Required matrix

Run every row independently on Windows 11 x64 and Windows 7 SP1 x86:

| Case | Starting state | Operation | Required result |
|---|---|---|---|
| Clean install | No installed application; prepared user fixture exists | Install candidate | Application installs; all protected state is byte-identical |
| v1.2.4 → v1.3.0 upgrade | v1.2.4 installed and configured | Install v1.3.0 over v1.2.4 | Same AppId upgrades in place; settings and data are byte-identical before first v1.3.0 launch |
| Uninstall | Candidate installed | Uninstall candidate | Program files are removed; all protected state remains byte-identical |
| Reinstall | Candidate was uninstalled | Install candidate again | Application returns; all protected state remains byte-identical |

Same-version repair/install-over is also required for Stable release evidence.

## Prepare the fixture

1. Close HPLC Analyzer and ensure no SQLite writer remains open.
2. Create a new fixture directory outside the installation directory. Never point this command at a production data directory.

```bat
python scripts\create_upgrade_test_fixture.py --root C:\HPLC_Upgrade_Test\fixture
```

3. In the baseline application, set language to English, rendering quality to a non-default value for that OS, import folder to `raw-ascii`, save folder to `projects`, and database path to `laboratory.sqlite3`. Save and close the application.
4. Copy the fixture's `user-config\presets.json` to the normal HPLC Analyzer preset location for the dedicated test account. Open the baseline application once and confirm both named canary presets, then close it.

## Capture and verify each installer step

Use all five `--path` lines printed by the fixture command. Capture the baseline before running an installer or uninstaller:

```bat
python scripts\installer_data_guard.py snapshot --snapshot C:\HPLC_Upgrade_Test\evidence\before.json --path "presets=C:\...\presets.json" --path "database=C:\...\laboratory.sqlite3" --path "projects=C:\...\projects" --path "raw=C:\...\raw-ascii" --path "exports=C:\...\user-exports"
```

After each clean install, upgrade, uninstall, repair, and reinstall operation—and before launching the newly installed application—run:

```bat
python scripts\installer_data_guard.py verify --snapshot C:\HPLC_Upgrade_Test\evidence\before.json --path "presets=C:\...\presets.json" --path "database=C:\...\laboratory.sqlite3" --path "projects=C:\...\projects" --path "raw=C:\...\raw-ascii" --path "exports=C:\...\user-exports"
```

Any changed registry value, byte, filename, or directory is a failed gate. Do not replace the baseline with a new snapshot after a failure.

After byte preservation passes, launch the candidate and confirm the saved English UI, rendering quality, import/save paths, database path, condition/gradient preset contents and metadata, project load, embedded raw trace, SQLite records, raw ASCII import, and existing user export. Save/reopen a copy of the project without overwriting the protected canary.

## Evidence and acceptance

Copy `INSTALLER_UPGRADE_EVIDENCE.md` for each OS and attach the completed records to the release evidence Issue. Record exact old/new installer filenames and hashes, commit, OS/hardware, every guard result, application-level checks, and uninstall behavior.

Automated tests may approve the framework and installer policy, but Issue acceptance and Stable release remain blocked until both OS records are complete. In particular, Windows 7 requires the physical target machine; a VM, compatibility mode, or modern CI runner is not sufficient.
