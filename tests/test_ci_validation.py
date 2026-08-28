from pathlib import Path
import re
import tempfile
import unittest

from scripts.ci_validate import (
    read_exact_pins,
    validate_offline_assets,
    validate_requirement_pins,
    validate_version_and_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT


class CiValidationTests(unittest.TestCase):
    def test_current_pin_version_and_artifact_contracts_pass(self):
        self.assertEqual(validate_requirement_pins(ROOT), [])
        self.assertEqual(validate_version_and_artifacts(ROOT), [])

    def test_pin_drift_and_non_exact_requirements_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text(
                "numpy>=1.20\nPySide2==5.15.2.1\nPySide2==5.15.2.1\n",
                encoding="utf-8",
            )
            pins, errors = read_exact_pins(path)
            self.assertEqual(pins["pyside2"], "5.15.2.1")
            self.assertTrue(any("not an exact" in error for error in errors))
            self.assertTrue(any("duplicates pin" in error for error in errors))

    def test_offline_assets_absence_is_explicit_and_required_mode_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            errors, message = validate_offline_assets(root, "auto")
            self.assertEqual(errors, [])
            self.assertIn("[SKIP]", message)
            errors, message = validate_offline_assets(root, "required")
            self.assertTrue(errors)
            self.assertEqual(message, "")

    def test_workflow_is_read_only_pinned_and_bounded(self):
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "validation.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("timeout-minutes:", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertNotIn("@v", workflow)
        self.assertNotIn("actions/cache", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertIn("--offline-assets auto", workflow)
        self.assertNotIn("HPLC_Analyzer_MVP_1.2.4", workflow)
        action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
        self.assertTrue(action_refs)
        self.assertTrue(
            all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
        )

    def test_application_uses_the_repository_root_layout(self):
        for relative_path in (
            "README.md",
            "app.py",
            "hplc_app",
            "tests",
            "scripts",
            "installer",
        ):
            self.assertTrue((ROOT / relative_path).exists(), relative_path)


if __name__ == "__main__":
    unittest.main()
