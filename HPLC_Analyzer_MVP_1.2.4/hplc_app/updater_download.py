"""Bounded, non-launching download and verification helpers for updates."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Dict, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .update_check import DEFAULT_REPOSITORY


MAX_INSTALLER_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
_SHA256_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+[*]?(.+?)$")


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


def _fetch_bounded(url: str, limit: int, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": "HPLC-Analyzer-updater"})
    with urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            raise ValueError("Download exceeds the size limit")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Download exceeds the size limit")
    return payload


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
        download = fetch or _fetch_bounded
        manifest = download(manifest_url, MAX_MANIFEST_BYTES, timeout)
        expected = parse_sha256_manifest(manifest, installer_filename)
        installer = download(installer_url, MAX_INSTALLER_BYTES, timeout)
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
    except Exception as exc:
        result["reason"] = str(exc)
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return result
