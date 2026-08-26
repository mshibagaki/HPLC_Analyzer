from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from hplc_app.import_batch import discover_chromatogram_files


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


if __name__ == "__main__":
    unittest.main()
