"""Download and validate the attested macOS arm64 Codex runtime release."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


REPOSITORY = "junfengM/codex-provider-runtime"
SIGNER_WORKFLOW = (
    "junfengM/codex-provider-runtime/.github/workflows/prebuilt-codex.yml"
)
SOURCE_REF = "refs/heads/main"
SOURCE_REPOSITORY = "https://github.com/openai/codex.git"
ARCHIVE_ASSET = "codex-provider-macos-arm64.tar.gz"
ATTESTATION_ASSET = "codex-provider-macos-arm64.intoto.jsonl"
MAX_ARCHIVE_BYTES = 800 * 1024 * 1024
MAX_BINARY_BYTES = 600 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ATTESTATION_BYTES = 32 * 1024 * 1024
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PrebuiltReleaseError(RuntimeError):
    """A published prebuilt is unavailable or does not satisfy its contract."""


def release_tag(version: str, recipe_sha256: str) -> str:
    if not _VERSION_RE.fullmatch(version):
        raise PrebuiltReleaseError(f"Invalid Codex version for prebuilt lookup: {version!r}")
    if not _SHA256_RE.fullmatch(recipe_sha256):
        raise PrebuiltReleaseError("Invalid build recipe digest for prebuilt lookup")
    return f"codex-provider-v{version}-r{recipe_sha256[:12]}"


def build_recipe_sha256(runtime_root: Path) -> str:
    """Hash the code and patch that determine the patched binary's behavior."""
    digest = hashlib.sha256()
    for relative in (
        "router_manager.py",
        "prebuilt_release.py",
        "patches/provider_route.rs",
    ):
        path = runtime_root / relative
        if not path.is_file():
            raise PrebuiltReleaseError(f"Missing build recipe input: {path}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def resolve_source_tag_commit(version: str) -> str:
    """Resolve an upstream lightweight or annotated rust-v<version> tag."""
    if not _VERSION_RE.fullmatch(version):
        raise PrebuiltReleaseError(f"Invalid Codex version for source tag lookup: {version!r}")
    tag = f"rust-v{version}"
    try:
        result = subprocess.run(
            [
                "git",
                "ls-remote",
                SOURCE_REPOSITORY,
                f"refs/tags/{tag}",
                f"refs/tags/{tag}^{{}}",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", None)
        if isinstance(detail, str) and detail.strip():
            detail = detail.strip()[-1000:]
            raise PrebuiltReleaseError(f"Could not resolve upstream source tag {tag}: {detail}") from error
        raise PrebuiltReleaseError(f"Could not resolve upstream source tag {tag}: {error}") from error
    entries: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            entries[fields[1]] = fields[0]
    commit = entries.get(f"refs/tags/{tag}^{{}}") or entries.get(f"refs/tags/{tag}")
    if not commit or not _COMMIT_RE.fullmatch(commit):
        raise PrebuiltReleaseError(f"Could not resolve exact upstream source tag {tag}")
    return commit


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise PrebuiltReleaseError(f"Invalid macOS version in artifact manifest: {value!r}")
    result = tuple(int(part) for part in parts)
    return result + (0,) * max(0, 3 - len(result))


def running_macos_version() -> str:
    value = platform.mac_ver()[0]
    if not value:
        raise PrebuiltReleaseError("Could not determine the running macOS version")
    return value


def ensure_supported_host(system: Optional[str] = None, machine: Optional[str] = None) -> None:
    system = system or platform.system()
    machine = machine or platform.machine()
    if system != "Darwin" or machine not in {"arm64", "aarch64"}:
        raise PrebuiltReleaseError(
            f"Prebuilt Codex release supports macOS arm64; this host is {system}/{machine}"
        )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PrebuiltReleaseError(f"Prebuilt manifest contains duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_manifest(
    manifest: object,
    *,
    version: str,
    source_commit: str,
    patch_name: str,
    patch_sha256: str,
    recipe_sha256: str,
    platform_name: str = "macos",
    architecture: str = "aarch64",
    running_os: Optional[str] = None,
) -> dict:
    if not isinstance(manifest, dict):
        raise PrebuiltReleaseError("Prebuilt manifest must be a JSON object")
    required = {
        "schema": 1,
        "codex_version": version,
        "source_tag": f"rust-v{version}",
        "source_commit": source_commit,
        "patch_name": patch_name,
        "patch_sha256": patch_sha256,
        "recipe_sha256": recipe_sha256,
        "platform": platform_name,
        "architecture": architecture,
        "runtime_source_ref": SOURCE_REF,
    }
    mismatches = [
        f"{key}={manifest.get(key)!r} (expected {value!r})"
        for key, value in required.items()
        if manifest.get(key) != value
    ]
    if mismatches:
        raise PrebuiltReleaseError(
            "Prebuilt manifest contract mismatch: " + "; ".join(mismatches)
        )
    for key in (
        "source_commit",
        "runtime_source_commit",
        "patch_sha256",
        "recipe_sha256",
        "binary_sha256",
    ):
        value = manifest.get(key)
        expected = _COMMIT_RE if key.endswith("commit") else _SHA256_RE
        if not isinstance(value, str) or not expected.fullmatch(value):
            raise PrebuiltReleaseError(f"Prebuilt manifest has invalid {key}")
    minimum_os = manifest.get("minimum_macos_version")
    if not isinstance(minimum_os, str):
        raise PrebuiltReleaseError("Prebuilt manifest has no minimum_macos_version")
    if running_os is not None and _version_tuple(running_os) < _version_tuple(minimum_os):
        raise PrebuiltReleaseError(
            f"Prebuilt requires macOS {minimum_os} or later; this Mac is running {running_os}"
        )
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise PrebuiltReleaseError("Prebuilt manifest has no provenance record")
    expected_provenance = {
        "repository": REPOSITORY,
        "workflow": SIGNER_WORKFLOW,
        "source_ref": SOURCE_REF,
    }
    if any(provenance.get(key) != value for key, value in expected_provenance.items()):
        raise PrebuiltReleaseError(
            "Prebuilt manifest provenance does not match the trusted main workflow"
        )
    return manifest


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if member.size < 0 or member.size > limit:
        raise PrebuiltReleaseError(f"Archive member has an invalid size: {member.name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise PrebuiltReleaseError(f"Could not read archive member: {member.name}")
    with stream:
        value = stream.read(limit + 1)
    if len(value) != member.size:
        raise PrebuiltReleaseError(f"Archive member size does not match its header: {member.name}")
    return value


def _download_release_asset(tag: str, asset: str, destination: Path, limit: int) -> None:
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "codex-provider-runtime"},
    )
    temporary = destination.with_name(f".{destination.name}.download.{os.getpid()}")
    deadline = time.monotonic() + 900
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as target:
            while True:
                if time.monotonic() > deadline:
                    raise PrebuiltReleaseError(f"Timed out downloading Release asset {asset}")
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > limit:
                    raise PrebuiltReleaseError(f"Release asset {asset} exceeds the size limit")
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise PrebuiltReleaseError(f"Release asset {asset} is empty")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def validate_and_extract_archive(
    archive_path: Path,
    destination: Path,
    *,
    version: str,
    source_commit: str,
    patch_name: str,
    patch_sha256: str,
    recipe_sha256: str,
    running_os: Optional[str] = None,
) -> dict:
    """Validate the attested tar contents without general-purpose extraction."""
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PrebuiltReleaseError("Prebuilt archive is missing or exceeds the size limit")
    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as error:
        raise PrebuiltReleaseError(f"Prebuilt archive is not a valid gzip tar: {error}") from error
    with archive:
        members: list[tarfile.TarInfo] = []
        for member in archive:
            members.append(member)
            if len(members) > 2:
                raise PrebuiltReleaseError(
                    "Prebuilt archive must contain exactly codex and manifest.json"
                )
        if len(members) != 2:
            raise PrebuiltReleaseError("Prebuilt archive must contain exactly codex and manifest.json")
        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            if member.name not in {"codex", "manifest.json"}:
                raise PrebuiltReleaseError(f"Unexpected prebuilt archive member: {member.name!r}")
            if member.name in by_name:
                raise PrebuiltReleaseError(f"Duplicate prebuilt archive member: {member.name!r}")
            if not member.isreg() or member.linkname:
                raise PrebuiltReleaseError(f"Prebuilt archive member is not a plain file: {member.name}")
            by_name[member.name] = member
        if set(by_name) != {"codex", "manifest.json"}:
            raise PrebuiltReleaseError("Prebuilt archive does not contain its required files")
        manifest_bytes = _read_member(archive, by_name["manifest.json"], MAX_MANIFEST_BYTES)
        try:
            manifest_value = json.loads(
                manifest_bytes, object_pairs_hook=_unique_json_object
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise PrebuiltReleaseError(f"Prebuilt manifest is invalid JSON: {error}") from error
        manifest = validate_manifest(
            manifest_value,
            version=version,
            source_commit=source_commit,
            patch_name=patch_name,
            patch_sha256=patch_sha256,
            recipe_sha256=recipe_sha256,
            running_os=running_os,
        )
        binary_info = by_name["codex"]
        if binary_info.size > MAX_BINARY_BYTES:
            raise PrebuiltReleaseError("Prebuilt Codex binary exceeds the size limit")
        binary_stream = archive.extractfile(binary_info)
        if binary_stream is None:
            raise PrebuiltReleaseError("Could not read prebuilt Codex binary")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        temporary = destination.with_name(f".{destination.name}.next.{os.getpid()}")
        try:
            with binary_stream, temporary.open("wb") as target:
                while True:
                    block = binary_stream.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_BINARY_BYTES:
                        raise PrebuiltReleaseError("Prebuilt Codex binary exceeds the size limit")
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size != binary_info.size or digest.hexdigest() != manifest["binary_sha256"]:
                raise PrebuiltReleaseError("Prebuilt Codex binary size or SHA-256 does not match manifest")
            temporary.chmod(0o755)
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return manifest


def download_attested_release(
    version: str,
    destination: Path,
    *,
    patch_name: str,
    patch_sha256: str,
    recipe_sha256: str,
    running_os: Optional[str] = None,
) -> tuple[Path, dict]:
    """Fetch, attest, and validate one public Release asset before returning it."""
    ensure_supported_host()
    running_os = running_os or running_macos_version()
    tag = release_tag(version, recipe_sha256)
    commit = resolve_source_tag_commit(version)
    try:
        with tempfile.TemporaryDirectory(prefix="codex-provider-prebuilt-") as temporary:
            work = Path(temporary)
            archive_path = work / ARCHIVE_ASSET
            bundle_path = work / ATTESTATION_ASSET
            _download_release_asset(tag, ARCHIVE_ASSET, archive_path, MAX_ARCHIVE_BYTES)
            _download_release_asset(
                tag, ATTESTATION_ASSET, bundle_path, MAX_ATTESTATION_BYTES
            )
            if not archive_path.is_file() or not bundle_path.is_file():
                raise PrebuiltReleaseError("The release is missing its artifact or attestation bundle")
            subprocess.run(
                [
                    "gh",
                    "attestation",
                    "verify",
                    os.fspath(archive_path),
                    "--bundle",
                    os.fspath(bundle_path),
                    "--repo",
                    REPOSITORY,
                    "--signer-workflow",
                    SIGNER_WORKFLOW,
                    "--source-ref",
                    SOURCE_REF,
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            validated_binary = destination.with_name(f".{destination.name}.verified.{os.getpid()}")
            try:
                manifest = validate_and_extract_archive(
                    archive_path,
                    validated_binary,
                    version=version,
                    source_commit=commit,
                    patch_name=patch_name,
                    patch_sha256=patch_sha256,
                    recipe_sha256=recipe_sha256,
                    running_os=running_os,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(validated_binary, destination)
            finally:
                validated_binary.unlink(missing_ok=True)
            return destination, manifest
    except (OSError, urllib.error.URLError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", None)
        if isinstance(detail, str) and detail.strip():
            detail = detail.strip()[-1000:]
            raise PrebuiltReleaseError(f"Prebuilt release lookup/verification failed: {detail}") from error
        raise PrebuiltReleaseError(f"Prebuilt release lookup/verification failed: {error}") from error
