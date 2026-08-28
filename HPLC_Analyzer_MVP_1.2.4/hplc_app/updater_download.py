"""Bounded, non-launching download and verification helpers for updates."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Dict, Iterable, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .update_check import DEFAULT_REPOSITORY


MAX_INSTALLER_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
_SHA256_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+[*]?(.+?)$")
_CERTIFICATE_THUMBPRINT = re.compile(r"^[0-9A-F]{40}$")


class DownloadCancelled(Exception):
    """Raised when the caller cooperatively cancels an updater download."""


def official_release_asset_url(url: str, repository: str = DEFAULT_REPOSITORY) -> bool:
    parsed = urlparse(str(url))
    prefix = "/%s/releases/download/" % repository
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "github.com"
        and parsed.path.startswith(prefix)
        and len(parsed.path) > len(prefix)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def select_canonical_release_assets(release, version: str, repository: str = DEFAULT_REPOSITORY):
    """Select one exact Win11 installer and checksum manifest, failing closed."""

    installer_name = "HPLC_Analyzer_Setup_%s_Windows11_x64.exe" % str(version)
    expected = (installer_name, "SHA256SUMS.txt")
    if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
        raise ValueError("Release assets are missing")
    selected = {}
    for asset in release["assets"]:
        if not isinstance(asset, dict) or asset.get("name") not in expected:
            continue
        name = asset["name"]
        if name in selected:
            raise ValueError("Release contains duplicate canonical asset: %s" % name)
        url = asset.get("browser_download_url", "")
        size = asset.get("size")
        if not official_release_asset_url(url, repository):
            raise ValueError("Canonical asset URL is not trusted: %s" % name)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("Canonical asset size is invalid: %s" % name)
        limit = MAX_INSTALLER_BYTES if name == installer_name else MAX_MANIFEST_BYTES
        if size > limit:
            raise ValueError("Canonical asset exceeds the size limit: %s" % name)
        selected[name] = {"name": name, "url": url, "size": size}
    missing = [name for name in expected if name not in selected]
    if missing:
        raise ValueError("Release is missing canonical asset: %s" % ", ".join(missing))
    return {"installer": selected[installer_name], "manifest": selected["SHA256SUMS.txt"]}


def normalize_signer_thumbprints(values: Iterable[str]):
    """Validate explicit SHA-1 certificate thumbprints without inventing defaults."""

    normalized = []
    for value in values:
        thumbprint = re.sub(r"[ :\-]", "", str(value)).upper()
        if not _CERTIFICATE_THUMBPRINT.fullmatch(thumbprint):
            raise ValueError("Signer thumbprint must be 40 hexadecimal characters")
        if thumbprint not in normalized:
            normalized.append(thumbprint)
    return tuple(normalized)


def signer_policy_result(authenticode, allowed_thumbprints=()):
    """Authorize only a Valid signature whose configured identity matches."""

    allowed = normalize_signer_thumbprints(allowed_thumbprints)
    if not allowed:
        return {"launch_allowed": False, "reason": "No approved signer identity is configured"}
    if not isinstance(authenticode, dict) or authenticode.get("status") != "valid":
        return {"launch_allowed": False, "reason": "Authenticode signature is not valid"}
    actual = re.sub(r"[ :\-]", "", str(authenticode.get("thumbprint", ""))).upper()
    if actual not in allowed:
        return {"launch_allowed": False, "reason": "Signer identity is not approved"}
    return {"launch_allowed": True, "reason": ""}


def parse_sha256_manifest(payload: bytes, filename: str) -> str:
    """Return one exact filename digest from a sha256sum-style manifest."""

    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise ValueError("Unsafe installer filename")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Manifest is not UTF-8") from exc
    matches = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SHA256_LINE.fullmatch(line)
        if match is None:
            raise ValueError("Malformed SHA-256 manifest line")
        if match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("Manifest must contain the installer exactly once")
    return matches[0]


def fetch_bounded(
    url: str,
    limit: int,
    timeout: float,
    progress: Optional[Callable[[int, Optional[int]], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    opener: Optional[Callable] = None,
    chunk_size: int = 64 * 1024,
) -> bytes:
    """Stream one response within ``limit`` with progress and cancellation."""

    if limit < 0 or chunk_size <= 0:
        raise ValueError("Download size parameters must be positive")
    request = Request(url, headers={"User-Agent": "HPLC-Analyzer-updater"})
    open_url = opener or urlopen
    with open_url(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        total = None
        if declared:
            try:
                total = int(declared)
            except (TypeError, ValueError) as exc:
                raise ValueError("Download Content-Length is invalid") from exc
            if total < 0 or total > limit:
                raise ValueError("Download exceeds the size limit")
        chunks = []
        received = 0
        if progress is not None:
            progress(received, total)
        while True:
            if cancelled is not None and cancelled():
                raise DownloadCancelled("Download canceled")
            chunk = response.read(min(chunk_size, limit + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise ValueError("Download exceeds the size limit")
            chunks.append(chunk)
            if progress is not None:
                progress(received, total)
        if total is not None and received != total:
            raise ValueError("Download size does not match Content-Length")
    return b"".join(chunks)


def _fetch_bounded(url: str, limit: int, timeout: float) -> bytes:
    """Compatibility wrapper for the original bounded fetch seam."""

    return fetch_bounded(url, limit, timeout)


def probe_authenticode(path, runner: Optional[Callable] = None) -> Dict[str, str]:
    """Read Authenticode status without loading or executing the installer."""

    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "[pscustomobject]@{Status=[string]$s.Status;"
        "StatusMessage=[string]$s.StatusMessage;"
        "Thumbprint=[string]$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"
    )
    execute = runner or subprocess.run
    try:
        completed = execute(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, str(Path(path))],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            return {"status": "error", "thumbprint": "", "reason": completed.stderr.strip()}
        result = json.loads(completed.stdout)
        status = str(result.get("Status", ""))
        return {
            "status": "valid" if status == "Valid" else "invalid",
            "thumbprint": str(result.get("Thumbprint", "") or "").upper(),
            "reason": str(result.get("StatusMessage", "") or status),
        }
    except Exception as exc:
        return {"status": "error", "thumbprint": "", "reason": str(exc)}


def stage_verified_installer(
    installer_url: str,
    manifest_url: str,
    installer_filename: str,
    temporary_directory,
    repository: str = DEFAULT_REPOSITORY,
    timeout: float = 15.0,
    fetch: Optional[Callable[[str, int, float], bytes]] = None,
    authenticode_probe: Optional[Callable] = None,
    progress: Optional[Callable[[str, int, Optional[int]], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """Download and verify an installer, but never execute it."""

    result = {
        "status": "error",
        "path": "",
        "sha256": "",
        "authenticode": {"status": "not_tested", "thumbprint": "", "reason": ""},
        "reason": "",
        "launch_allowed": False,
    }
    destination = None
    manifest_path = None
    created_paths = []
    try:
        if not official_release_asset_url(installer_url, repository):
            raise ValueError("Installer URL is not an official Release asset")
        if not official_release_asset_url(manifest_url, repository):
            raise ValueError("Manifest URL is not an official Release asset")
        if Path(installer_filename).name != installer_filename or Path(installer_filename).suffix.lower() != ".exe":
            raise ValueError("Unsafe installer filename")
        root = Path(temporary_directory).resolve()
        if not root.is_dir():
            raise ValueError("Temporary directory does not exist")
        destination = root / installer_filename
        manifest_path = root / (installer_filename + ".sha256")
        if destination.exists() or manifest_path.exists():
            raise ValueError("Temporary destination already exists")
        def download(asset, url, limit):
            if cancelled is not None and cancelled():
                raise DownloadCancelled("Download canceled")
            if fetch is None:
                return fetch_bounded(
                    url,
                    limit,
                    timeout,
                    progress=(
                        (lambda received, total: progress(asset, received, total))
                        if progress is not None
                        else None
                    ),
                    cancelled=cancelled,
                )
            if progress is not None:
                progress(asset, 0, None)
            payload = fetch(url, limit, timeout)
            if len(payload) > limit:
                raise ValueError("Download exceeds the size limit")
            if progress is not None:
                progress(asset, len(payload), len(payload))
            if cancelled is not None and cancelled():
                raise DownloadCancelled("Download canceled")
            return payload

        manifest = download("manifest", manifest_url, MAX_MANIFEST_BYTES)
        expected = parse_sha256_manifest(manifest, installer_filename)
        installer = download("installer", installer_url, MAX_INSTALLER_BYTES)
        digest = hashlib.sha256(installer).hexdigest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError("Installer SHA-256 does not match the manifest")
        with manifest_path.open("xb") as handle:
            created_paths.append(manifest_path)
            handle.write(manifest)
        with destination.open("xb") as handle:
            created_paths.append(destination)
            handle.write(installer)
        auth = (authenticode_probe or probe_authenticode)(destination)
        result.update(
            status="verified" if auth.get("status") == "valid" else "held",
            path=str(destination),
            sha256=digest,
            authenticode=auth,
            reason="" if auth.get("status") == "valid" else "Authenticode signature is not valid",
        )
        return result
    except DownloadCancelled as exc:
        result["status"] = "canceled"
        result["reason"] = str(exc)
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return result
    except Exception as exc:
        result["reason"] = str(exc)
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return result
