"""Validate a filled release-evidence checklist without publishing anything."""

from __future__ import print_function

import argparse
import json
import re
from pathlib import Path


PLACEHOLDER_RE = re.compile(r"_{2,}|\b(?:TBD|TODO)\b", re.IGNORECASE)
CHECKBOX_RE = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<label>.+)$")
SHA_FIELDS = ("VERIFIED_RC_COMMIT", "VERIFIED_STABLE_COMMIT")
IDENTITY_SECTIONS = ("Release identity", "Versions and formats")
REQUIRED_SECTIONS = (
    "Release identity",
    "Versions and formats",
    "Automated validation",
    "Windows 11 x64 evidence",
    "Windows 7 SP1 x86 evidence",
    "Upgrade and data preservation",
    "Assets, notes, and approval",
    "Post-publication spot check",
)
REQUIRED_FIELD_LABELS = (
    "Candidate version",
    "Stable version",
    "VERIFIED_RC_COMMIT",
    "VERIFIED_STABLE_COMMIT",
    "Final RC annotated tag",
    "Stable annotated tag",
    "Project format major",
    "Project schema",
    "Preset format",
    "Lab database schema",
    "Release approver",
    "Release evidence link",
)


def _finding(kind, line, section, message):
    return {
        "kind": kind,
        "line": line,
        "section": section,
        "message": message,
    }


def inspect_checklist(text, mode="dry-run"):
    """Return deterministic error/blocker findings for checklist Markdown."""
    if mode not in ("dry-run", "stable"):
        raise ValueError("mode must be 'dry-run' or 'stable'")

    findings = []
    section = ""
    sections = set()
    in_evidence_table = False
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            sections.add(section)
            in_evidence_table = section.endswith("evidence")
            continue

        checkbox = CHECKBOX_RE.match(line)
        if checkbox and checkbox.group("mark") == " ":
            kind = "error" if mode == "stable" or section in IDENTITY_SECTIONS else "blocker"
            findings.append(
                _finding(kind, line_number, section, "required checkbox is not checked")
            )

        if PLACEHOLDER_RE.search(line):
            findings.append(
                _finding("error", line_number, section, "placeholder has not been replaced")
            )

        if "BLOCKED — evidence required" in line:
            kind = "error" if mode == "stable" else "blocker"
            findings.append(
                _finding(kind, line_number, section, "required evidence is explicitly blocked")
            )

        if in_evidence_table and line.startswith("|") and not line.startswith("|---"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 2 and cells[0] != "Field" and not cells[1]:
                kind = "error" if mode == "stable" else "blocker"
                findings.append(
                    _finding(kind, line_number, section, "evidence field is blank: " + cells[0])
                )

    for required_section in REQUIRED_SECTIONS:
        if required_section not in sections:
            findings.append(
                _finding("error", 0, required_section, "required section is missing")
            )

    for required_label in REQUIRED_FIELD_LABELS:
        if required_label not in text:
            findings.append(
                _finding("error", 0, "Document structure", "required field is missing: " + required_label)
            )

    for field in SHA_FIELDS:
        match = re.search(
            r"^.*`?" + re.escape(field) + r"`?.*?:\s*`?([0-9a-fA-F_]+)`?\s*$",
            text,
            re.MULTILINE,
        )
        if match and not re.fullmatch(r"[0-9a-fA-F]{40}", match.group(1)):
            findings.append(
                _finding("error", text[: match.start()].count("\n") + 1, "Release identity", field + " must be a 40-character hexadecimal SHA")
            )

    unique = []
    seen = set()
    for finding in findings:
        key = tuple(finding[key] for key in ("kind", "line", "message"))
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def result_document(path, mode, findings):
    return {
        "path": str(path),
        "mode": mode,
        "ready": not findings,
        "errors": sum(item["kind"] == "error" for item in findings),
        "blockers": sum(item["kind"] == "blocker" for item in findings),
        "findings": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checklist", type=Path, help="filled checklist Markdown file")
    parser.add_argument("--mode", choices=("dry-run", "stable"), default="dry-run")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        text = args.checklist.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        parser.error(str(exc))
    findings = inspect_checklist(text, mode=args.mode)
    result = result_document(args.checklist, args.mode, findings)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif findings:
        for item in findings:
            print("{kind}: line {line} [{section}] {message}".format(**item))
        print("NOT READY: {errors} error(s), {blockers} blocker(s)".format(**result))
    else:
        print("READY: no missing or blocked release evidence found")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
