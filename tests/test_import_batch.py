from __future__ import annotations

from pathlib import Path
import hashlib
import tempfile
from types import SimpleNamespace
import unittest

from hplc_app.import_batch import (
    discover_chromatogram_files,
    discover_chromatogram_files_with_report,
    discover_reload_candidates,
)


class ImportBatchTests(unittest.TestCase):
    def test_discovery_is_case_insensitive_stable_and_non_recursive_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "B.TXT").write_text("b", encoding="utf-8")
            (root / "a.gCd").write_text("a", encoding="utf-8")
            (root / "ignored.csv").write_text("ignored", encoding="utf-8")
            nested = root / "Sub"
            nested.mkdir()
            (nested / "c.txt").write_text("c", encoding="utf-8")
            (nested / "A.GCD").write_text("nested", encoding="utf-8")

            non_recursive = discover_chromatogram_files(root)
            recursive = discover_chromatogram_files(root, recursive=True)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in non_recursive],
                ["a.gCd", "B.TXT"],
            )
            self.assertEqual(
                [path.relative_to(root).as_posix() for path in recursive],
                ["a.gCd", "B.TXT", "Sub/A.GCD", "Sub/c.txt"],
            )

    def test_missing_directory_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaisesRegex(ValueError, "does not exist"):
                discover_chromatogram_files(missing)

    def test_blank_directory_fails_instead_of_scanning_the_working_directory(self):
        for value in ("", "   "):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                discover_chromatogram_files(value, True)

    def test_discovery_prefers_matching_gcd_in_the_same_directory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Sample.GCD").write_bytes(b"gcd")
            (root / "sample.txt").write_bytes(b"txt")
            (root / "txt-only.TXT").write_bytes(b"txt-only")
            nested = root / "nested"
            nested.mkdir()
            (nested / "SAMPLE.txt").write_bytes(b"nested-txt")

            report = discover_chromatogram_files_with_report(
                root, recursive=True
            )

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in report.files],
                ["nested/SAMPLE.txt", "Sample.GCD", "txt-only.TXT"],
            )
            self.assertEqual(
                [
                    path.relative_to(root).as_posix()
                    for path in report.skipped_txt_files
                ],
                ["sample.txt"],
            )

    def test_reload_classifies_new_duplicate_and_changed_files_across_directories(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir) / "first.gcd"
            duplicate = Path(second_dir) / "duplicate.TXT"
            changed = Path(second_dir) / "changed.gcd"
            first.write_bytes(b"new-content")
            duplicate.write_bytes(b"new-content")
            changed.write_bytes(b"changed-content")
            directories = [
                SimpleNamespace(path=first_dir, label="pac1", recursive=False, enabled=True),
                SimpleNamespace(path=second_dir, label="pac2", recursive=False, enabled=True),
            ]
            existing = [
                SimpleNamespace(
                    original_path=str(changed),
                    sha256=hashlib.sha256(b"old-content").hexdigest(),
                )
            ]

            candidates, duplicate_count, changed_paths, errors, skipped_txt_count = (
                discover_reload_candidates(directories, existing)
            )

            self.assertEqual(candidates, [(str(first), "pac1")])
            self.assertEqual(duplicate_count, 1)
            self.assertEqual(changed_paths, [str(changed)])
            self.assertEqual(errors, [])
            self.assertEqual(skipped_txt_count, 0)

    def test_reload_uses_the_same_gcd_preference_as_batch_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gcd = root / "Run.GCD"
            gcd.write_bytes(b"gcd")
            (root / "run.txt").write_bytes(b"txt")
            directories = [
                SimpleNamespace(
                    path=directory,
                    label="batch",
                    recursive=False,
                    enabled=True,
                )
            ]

            candidates, duplicates, changed, errors, skipped_txt_count = (
                discover_reload_candidates(directories, [])
            )

            self.assertEqual(candidates, [(str(gcd), "batch")])
            self.assertEqual((duplicates, changed, errors), (0, [], []))
            self.assertEqual(skipped_txt_count, 1)


if __name__ == "__main__":
    unittest.main()
