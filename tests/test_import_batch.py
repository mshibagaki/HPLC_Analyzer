from __future__ import annotations

from pathlib import Path
import hashlib
import tempfile
from types import SimpleNamespace
import unittest

from hplc_app.import_batch import (
    _parse_measurement_identity,
    dataset_measurement_identity,
    discover_chromatogram_files,
    discover_chromatogram_files_with_report,
    discover_reload_candidates,
)
from hplc_app.parser import load_ascii_file

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "210601.TXT"
SAMPLE_GCD_PATH = b"C:\\PACsolution\\Data1\\Ch1\\2026_05_07\\210601.gcd"
SAMPLE_ACQUIRED = b"2026/05/07 19:33:54"


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



class SameMeasurementTests(unittest.TestCase):
    """Issue #338: a GCD and its TXT export are one measurement."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def write_txt(self, relative, gcd_path, acquired=None):
        raw = SAMPLE.read_bytes().replace(SAMPLE_GCD_PATH, gcd_path.encode("ascii"))
        if acquired:
            raw = raw.replace(SAMPLE_ACQUIRED, acquired.encode("ascii"))
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def write_gcd(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic gcd " + relative.encode("ascii"))
        return path

    def entries(self, *relatives, recursive=False):
        return [
            SimpleNamespace(
                path=str(self.root / relative),
                label=relative,
                recursive=recursive,
                enabled=True,
            )
            for relative in relatives
        ]

    def loader(self, gcd_identities, calls=None):
        def load(path):
            if calls is not None:
                calls.append(path.name)
            if path.suffix.lower() == ".gcd":
                return gcd_identities.get(path.relative_to(self.root).as_posix())
            return _parse_measurement_identity(path)

        return load

    def identity(self, channel, stem, acquired="2026-05-07T19:33:54"):
        return (channel, stem, acquired)

    def test_existing_txt_hides_a_later_gcd_of_the_same_measurement(self):
        txt = self.write_txt("pac/Run1.txt", r"C:\Data1\Ch1\2026_05_07\Run1.gcd")
        self.write_gcd("pac/Run1.gcd")
        existing = [load_ascii_file(str(txt))]
        result = discover_reload_candidates(
            self.entries("pac"),
            existing,
            self.loader({"pac/Run1.gcd": self.identity(1, "run1")}),
        )
        self.assertEqual(result[0], [])
        self.assertEqual(result.same_measurement_skipped, 1)

    def test_gcd_and_txt_in_different_folders_propose_only_the_gcd(self):
        gcd = self.write_gcd("data/Ch1/Run2.gcd")
        self.write_txt("export/Run2.txt", r"C:\Data1\Ch1\2026_05_07\Run2.gcd")
        identities = {"data/Ch1/Run2.gcd": self.identity(1, "run2")}
        for order in (("data", "export"), ("export", "data")):
            with self.subTest(order=order):
                result = discover_reload_candidates(
                    self.entries(*order, recursive=True),
                    [],
                    self.loader(identities),
                )
                self.assertEqual(result[0], [(str(gcd), "data")])
                self.assertEqual(result.same_measurement_skipped, 1)

    def test_existing_gcd_hides_a_txt_export_from_another_folder(self):
        txt = self.write_txt("export/Run3.txt", r"C:\Data1\Ch1\2026_05_07\Run3.gcd")
        stored = load_ascii_file(str(txt))
        stored.original_filename = "Run3.gcd"
        stored.original_path = str(self.root / "data" / "Ch1" / "Run3.gcd")
        stored.sha256 = "0" * 64
        result = discover_reload_candidates(self.entries("export"), [stored])
        self.assertEqual(result[0], [])
        self.assertEqual(result.same_measurement_skipped, 1)

    def test_same_name_in_different_channels_is_never_merged(self):
        first = self.write_gcd("Ch1/X.gcd")
        second = self.write_gcd("Ch2/X.gcd")
        identities = {
            "Ch1/X.gcd": self.identity(1, "x"),
            "Ch2/X.gcd": self.identity(2, "x"),
        }
        result = discover_reload_candidates(
            self.entries("Ch1", "Ch2"), [], self.loader(identities)
        )
        self.assertEqual(
            [path for path, _label in result[0]], [str(first), str(second)]
        )
        self.assertEqual(result.same_measurement_skipped, 0)
        report = discover_chromatogram_files_with_report(
            self.root, True, self.loader(identities)
        )
        self.assertEqual(report.files, [first, second])
        self.assertEqual(report.same_measurement_skipped, 0)

    def test_same_name_with_another_acquisition_time_is_kept(self):
        self.write_gcd("a/Y.gcd")
        self.write_txt(
            "b/Y.txt", r"C:\Data1\Ch1\2026_05_07\Y.gcd", "2026/05/08 08:00:00"
        )
        result = discover_reload_candidates(
            self.entries("a", "b"),
            [],
            self.loader({"a/Y.gcd": self.identity(1, "y")}),
        )
        self.assertEqual(len(result[0]), 2)
        self.assertEqual(result.same_measurement_skipped, 0)

    def test_unknown_acquisition_time_is_never_dropped(self):
        self.write_gcd("a/Z.gcd")
        self.write_txt("b/Z.txt", r"C:\Data1\Ch1\2026_05_07\Z.gcd")
        result = discover_reload_candidates(
            self.entries("a", "b"), [], self.loader({"a/Z.gcd": None})
        )
        self.assertEqual(len(result[0]), 2)
        self.assertEqual(result.same_measurement_skipped, 0)

    def test_only_files_with_a_colliding_stem_are_parsed(self):
        self.write_gcd("a/Only1.gcd")
        self.write_gcd("a/Only2.gcd")
        self.write_gcd("a/Both.gcd")
        self.write_txt("b/Both.txt", r"C:\Data1\Ch1\2026_05_07\Both.gcd")
        calls = []
        result = discover_reload_candidates(
            self.entries("a", "b"),
            [],
            self.loader({"a/Both.gcd": self.identity(1, "both")}, calls),
        )
        self.assertEqual(sorted(calls), ["Both.gcd", "Both.txt"])
        self.assertEqual(result.same_measurement_skipped, 1)
        self.assertEqual(len(result[0]), 3)

    def test_recursive_import_drops_the_txt_export_of_a_gcd_in_another_folder(self):
        gcd = self.write_gcd("Ch1/R.gcd")
        self.write_txt("export/R.txt", r"C:\Data1\Ch1\2026_05_07\R.gcd")
        report = discover_chromatogram_files_with_report(
            self.root, True, self.loader({"Ch1/R.gcd": self.identity(1, "r")})
        )
        self.assertEqual(report.files, [gcd])
        self.assertEqual(report.same_measurement_skipped, 1)

    def test_deciding_never_changes_the_files_or_their_identity(self):
        txt = self.write_txt("export/Q.txt", r"C:\Data1\Ch1\2026_05_07\Q.gcd")
        before = hashlib.sha256(txt.read_bytes()).hexdigest()
        identity = dataset_measurement_identity(load_ascii_file(str(txt)))
        self.assertEqual(identity, (1, "q", "2026-05-07T19:33:54"))
        discover_reload_candidates(self.entries("export"), [])
        self.assertEqual(hashlib.sha256(txt.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
