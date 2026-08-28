"""Non-mutating update checks against public GitHub Release metadata."""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, Optional, Tuple
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "mshibagaki/HPLC_Analyzer"
MAX_RESPONSE_BYTES = 512 * 1024
_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_stable_version(value: str) -> Optional[Tuple[int, int, int]]:
    """Return a comparable stable SemVer tuple, excluding prereleases."""

    match = _SEMVER.fullmatch(str(value).strip())
    if match is None or match.group(4) is not None:
        return None
    return tuple(int(match.group(index)) for index in (1, 2, 3))


def parse_semver(value: str):
    """Parse Stable or prerelease SemVer for current-application comparison."""

    match = _SEMVER.fullmatch(str(value).strip())
    if match is None:
        return None
    core = tuple(int(match.group(index)) for index in (1, 2, 3))
    raw_prerelease = match.group(4)
    if raw_prerelease is None:
        return core, None
    identifiers = raw_prerelease.split(".")
    if any(
        not identifier
        or (identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"))
        for identifier in identifiers
    ):
        return None
    return core, tuple(identifiers)


def compare_semver(left: str, right: str) -> int:
    """Return negative/zero/positive using SemVer precedence rules."""

    parsed_left = parse_semver(left)
    parsed_right = parse_semver(right)
    if parsed_left is None or parsed_right is None:
        raise ValueError("Invalid SemVer comparison")
    left_core, left_pre = parsed_left
    right_core, right_pre = parsed_right
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _fetch_release_json(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "HPLC-Analyzer-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub response exceeds the size limit")
    return payload


def check_for_updates(
    current_version: str,
    repository: str = DEFAULT_REPOSITORY,
    timeout: float = 5.0,
    fetch: Optional[Callable[[str, float], bytes]] = None,
) -> Dict[str, object]:
    """Return update metadata; network and data errors are non-fatal results."""

    current = parse_semver(current_version)
    if current is None:
        return _error("Current application version is not valid SemVer")
    if not _REPOSITORY.fullmatch(repository):
        return _error("Invalid GitHub repository name")
    url = "https://api.github.com/repos/%s/releases?per_page=20" % repository
    try:
        payload = (fetch or _fetch_release_json)(url, float(timeout))
        releases = json.loads(payload.decode("utf-8"))
        if not isinstance(releases, list):
            raise ValueError("GitHub response is not a release list")
        valid = []
        expected_prefix = "https://github.com/%s/releases/" % repository
        for release in releases:
            if not isinstance(release, dict):
                continue
            if release.get("draft") or release.get("prerelease"):
                continue
            version = parse_stable_version(release.get("tag_name", ""))
            release_url = release.get("html_url", "")
            if version is None or not isinstance(release_url, str):
                continue
            if not release_url.startswith(expected_prefix):
                continue
            valid.append((version, release))
        if not valid:
            return {
                "status": "no_release",
                "current_version": str(current_version),
                "latest_version": "",
                "release_url": "",
                "published_at": "",
                "reason": "No valid stable release was found",
            }
        latest, release = max(valid, key=lambda item: item[0])
        return {
            "status": (
                "update_available"
                if compare_semver(".".join(str(part) for part in latest), current_version) > 0
                else "current"
            ),
            "current_version": str(current_version),
            "latest_version": ".".join(str(part) for part in latest),
            "release_url": release["html_url"],
            "published_at": str(release.get("published_at") or ""),
            "reason": "",
        }
    except Exception as exc:  # network/API failures must not reach application startup
        return _error("Update check failed: %s" % exc, current_version)


def _error(reason: str, current_version: str = "") -> Dict[str, object]:
    return {
        "status": "error",
        "current_version": str(current_version),
        "latest_version": "",
        "release_url": "",
        "published_at": "",
        "reason": str(reason),
    }
