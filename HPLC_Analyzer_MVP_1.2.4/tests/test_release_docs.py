from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts.release_evidence_preflight import inspect_checklist


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent


class ReleaseDocumentationTests(unittest.TestCase):
    def test_release_process_defines_rc_to_stable_gates(self):
        text = (REPOSITORY_ROOT / ".github" / "RELEASE_PROCESS.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "Set as a pre-release",
            "annotated tag",
            'v1.3.0-rc.1^{commit}',
            'v1.3.0^{commit}',
            "1.3.0-dev.1",
            "VERIFIED_STABLE_COMMIT",
            "Never tag the earlier `rc` commit as Stable",
            "Windows 11 x64",
            "Windows 7 SP1 32-bit/Core 2",
            "SHA256SUMS.txt",
            "downgrade warning",
            "unsigned",
            "updater is outside the v1.3.0 scope",
            "Checklist dry run",
        ):
            self.assertIn(required, text)

    def test_release_checklist_has_required_evidence_fields(self):
        text = (REPOSITORY_ROOT / ".github" / "RELEASE_CHECKLIST.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "VERIFIED_RC_COMMIT",
            "VERIFIED_STABLE_COMMIT",
            "Project schema",
            "Preset format",
            "Lab database schema",
            "Windows 11 x64 evidence",
            "Windows 7 SP1 x86 evidence",
            "Upgrade and data preservation",
            "Post-publication spot check",
            "Release approver",
        ):
            self.assertIn(required, text)

    def test_release_notes_template_covers_supported_contracts(self):
        text = (REPOSITORY_ROOT / ".github" / "RELEASE_TEMPLATE.md").read_text(
            encoding="utf-8"
        )
        for heading in (
            "## Highlights",
            "## Added",
            "## Changed",
            "## Fixed",
            "## Compatibility",
            "## Known issues",
            "## Upgrade notes",
        ):
            self.assertIn(heading, text)
        self.assertIn("Windows 11 x64", text)
        self.assertIn("Windows 7 SP1 32-bit", text)
        self.assertIn("Project compatibility", text)
        self.assertIn("Code-signing status", text)

    def test_tracked_requirements_status_keeps_remaining_scope_explicit(self):
        text = (ROOT / "REQUIREMENTS_STATUS.md").read_text(encoding="utf-8")
        for required in (
            "Spectrum-domain selection and retention labels",
            "Replace the interactive Matplotlib screen renderer",
            "Updater download/error UI",
            "VALIDATION_BLOCKERS.md",
            "none is considered passed from CI or offscreen tests",
        ):
            self.assertIn(required, text)
        self.assertIn("Run ID and timestamp separated from label", text)
        self.assertIn("all/visible/selected report scopes", text)
        self.assertIn("Multiple project-owned work directories", text)

    def test_release_evidence_preflight_rejects_untouched_template(self):
        checklist = (REPOSITORY_ROOT / ".github" / "RELEASE_CHECKLIST.md").read_text(
            encoding="utf-8"
        )
        dry_run = inspect_checklist(checklist, mode="dry-run")
        stable = inspect_checklist(checklist, mode="stable")
        self.assertTrue(any(item["kind"] == "error" for item in dry_run))
        self.assertTrue(any(item["kind"] == "blocker" for item in dry_run))
        self.assertTrue(stable)
        self.assertTrue(all(item["kind"] == "error" for item in stable))
        self.assertTrue(
            any("40-character hexadecimal SHA" in item["message"] for item in stable)
        )

    def test_release_evidence_preflight_distinguishes_dry_run_blocker(self):
        text = """# Evidence
## Release identity
- [x] Candidate version: `1.3.0-rc.1`
- [x] Stable version: `1.3.0`
- [x] `VERIFIED_RC_COMMIT` (40-character SHA): `1111111111111111111111111111111111111111`
- [x] `VERIFIED_STABLE_COMMIT` containing Stable version: `2222222222222222222222222222222222222222`
- [x] Final RC annotated tag: `v1.3.0-rc.1`
- [x] Stable annotated tag: `v1.3.0`
## Versions and formats
- [x] Project format major: `1`
- [x] Project schema: `5`
- [x] Preset format: `1`
- [x] Lab database schema: `1`
## Automated validation
## Windows 11 x64 evidence
| Field | Evidence |
|---|---|
| Verifier / date | BLOCKED — evidence required |
- [ ] Physical test passes.
## Windows 7 SP1 x86 evidence
## Upgrade and data preservation
## Assets, notes, and approval
- [x] Release approver: `Dry Run` Approval date/time: `2026-08-28T03:00:00+09:00`
## Post-publication spot check
- [x] Release evidence link: `DRY RUN`
"""
        dry_run = inspect_checklist(text, mode="dry-run")
        stable = inspect_checklist(text, mode="stable")
        self.assertTrue(dry_run)
        self.assertTrue(all(item["kind"] == "blocker" for item in dry_run))
        self.assertTrue(all(item["kind"] == "error" for item in stable))

    def test_release_evidence_preflight_cli_emits_json(self):
        with tempfile.TemporaryDirectory() as directory:
            checklist = Path(directory) / "evidence.md"
            checklist.write_text("# Filled evidence\n", encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "release_evidence_preflight.py"),
                    str(checklist),
                    "--mode",
                    "stable",
                    "--json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(process.returncode, 1, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ready"])
            self.assertEqual(result["errors"], len(result["findings"]))
            self.assertEqual(result["blockers"], 0)


if __name__ == "__main__":
    unittest.main()
