from pathlib import Path
import unittest


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
            "VERIFIED_COMMIT",
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


if __name__ == "__main__":
    unittest.main()
