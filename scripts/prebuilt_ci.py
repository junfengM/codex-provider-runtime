#!/usr/bin/env python3
"""Build, package, attest, and publish the macOS arm64 Codex prebuilt."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))

import router_manager  # noqa: E402
import prebuilt_release  # noqa: E402


UPSTREAM_REPOSITORY = "openai/codex"
REPOSITORY = prebuilt_release.REPOSITORY
CODEX_ASSET = "codex-aarch64-apple-darwin.tar.gz"
HOST_ASSET = "codex-code-mode-host-aarch64-apple-darwin.tar.gz"
REQUIRED_UPSTREAM_ASSETS = (CODEX_ASSET, HOST_ASSET)
ARCHIVE_ASSET = prebuilt_release.ARCHIVE_ASSET
ATTESTATION_ASSET = prebuilt_release.ATTESTATION_ASSET
VERSION_RE = re.compile(r"^\d+\.\d+[A-Za-z0-9.+_-]{0,124}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_UPSTREAM_ASSET_BYTES = 1024 * 1024 * 1024
MAX_CACHE_BYTES = 6 * 1024 * 1024 * 1024
MINIMUM_MACOS_VERSION = "13.0"


class PrebuiltCIError(RuntimeError):
    """A prebuilt build or publication input failed its contract."""


def _valid_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise PrebuiltCIError(f"Invalid Codex version: {version!r}")
    return version


def _version_parts(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise PrebuiltCIError(f"Invalid macOS version in build manifest: {value!r}")
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0])[:3])


def _version_at_most(value: object, ceiling: str) -> bool:
    return _version_parts(value) <= _version_parts(ceiling)


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _api_json(path: str) -> Any:
    url = f"https://api.github.com/{path.lstrip('/')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "codex-provider-runtime-prebuilt-ci",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise PrebuiltCIError(f"GitHub API resource was not found: {path}") from error
        raise PrebuiltCIError(f"GitHub API request failed ({error.code}): {path}") from error
    except (OSError, urllib.error.URLError) as error:
        raise PrebuiltCIError(f"Could not query GitHub API resource {path}: {error}") from error
    if len(raw) > 16 * 1024 * 1024:
        raise PrebuiltCIError(f"GitHub API response was unexpectedly large: {path}")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PrebuiltCIError(f"GitHub API returned invalid JSON for {path}") from error


def _release_for_tag(repository: str, tag: str) -> dict | None:
    try:
        value = _api_json(
            f"repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"
        )
    except PrebuiltCIError as error:
        if "was not found" in str(error):
            return None
        raise
    if not isinstance(value, dict):
        raise PrebuiltCIError(f"Release API returned an invalid object for {tag}")
    return value


def _assets_by_name(release: dict) -> dict[str, dict]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PrebuiltCIError("GitHub release response has no assets array")
    by_name: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if isinstance(name, str):
            if name in by_name:
                raise PrebuiltCIError(f"GitHub release contains duplicate asset {name!r}")
            by_name[name] = asset
    return by_name


def _require_upstream_assets(release: dict, *, required: bool) -> dict[str, dict] | None:
    assets = _assets_by_name(release)
    missing = [
        name
        for name in REQUIRED_UPSTREAM_ASSETS
        if name not in assets
        or not isinstance(assets[name].get("size"), int)
        or assets[name]["size"] <= 0
        or assets[name].get("state", "uploaded") != "uploaded"
    ]
    if missing:
        if required:
            raise PrebuiltCIError(
                "Official upstream release is missing required macOS arm64 assets: "
                + ", ".join(missing)
            )
        return None
    return assets


def _latest_upstream_release() -> dict | None:
    # Upstream also publishes rusty-v8 helper releases. Look through a bounded
    # recent window for the first Codex rust-v release, then inspect only that
    # candidate. Never fall back to an older Codex release without assets.
    values = _api_json(f"repos/{UPSTREAM_REPOSITORY}/releases?per_page=20")
    if not isinstance(values, list):
        raise PrebuiltCIError("Upstream release API did not return a list")
    for release in values:
        if isinstance(release, dict) and str(release.get("tag_name", "")).startswith("rust-v"):
            return release
    return None


def _write_outputs(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    rendered = "".join(f"{key}={value}\n" for key, value in values.items())
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        sys.stdout.write(rendered)


def _current_recipe(root: Path = RUNTIME) -> str:
    return prebuilt_release.build_recipe_sha256(root)


def _release_is_complete(release: dict | None) -> bool:
    if release is None:
        return False
    assets = _assets_by_name(release)
    for name in (ARCHIVE_ASSET, ATTESTATION_ASSET):
        asset = assets.get(name)
        if (
            asset is None
            or not isinstance(asset.get("size"), int)
            or asset["size"] <= 0
            or asset.get("state", "uploaded") != "uploaded"
        ):
            return False
    return True


def plan(event: str, input_version: str, runtime_root: Path = RUNTIME) -> dict[str, str]:
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    ref = os.environ.get("GITHUB_REF", "refs/heads/main")
    if repository != REPOSITORY:
        raise PrebuiltCIError(f"Prebuilt releases may only publish from {REPOSITORY}")
    if ref != "refs/heads/main":
        raise PrebuiltCIError("Prebuilt releases may only publish from refs/heads/main")

    if event == "workflow_dispatch":
        version = _valid_version(input_version)
        upstream = _release_for_tag(UPSTREAM_REPOSITORY, f"rust-v{version}")
        if upstream is None:
            raise PrebuiltCIError(f"Official upstream release rust-v{version} was not found")
        _require_upstream_assets(upstream, required=True)
    elif event in {"schedule", "push"}:
        upstream = _latest_upstream_release()
        if upstream is None:
            return {"version": "", "build_required": "false", "reason": "no upstream release"}
        tag_name = upstream.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name.startswith("rust-v"):
            return {
                "version": "",
                "build_required": "false",
                "reason": "latest upstream release has no rust-v tag",
            }
        version = _valid_version(tag_name[len("rust-v") :])
        if _require_upstream_assets(upstream, required=False) is None:
            return {
                "version": version,
                "build_required": "false",
                "reason": "latest upstream release lacks required macOS arm64 assets",
            }
    else:
        raise PrebuiltCIError(f"Unsupported prebuilt workflow event: {event}")

    recipe = _current_recipe(runtime_root)
    tag = prebuilt_release.release_tag(version, recipe)
    existing = _release_for_tag(REPOSITORY, tag)
    complete = _release_is_complete(existing)
    return {
        "version": version,
        "recipe_sha256": recipe,
        "release_tag": tag,
        "build_required": "false" if complete else "true",
        "reason": "matching release assets are already complete" if complete else "build required",
    }


def _download_asset(asset: dict, destination: Path) -> str:
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        raise PrebuiltCIError("GitHub release asset has no download URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise PrebuiltCIError("Refusing an upstream asset URL outside github.com")
    declared_size = asset.get("size")
    if not isinstance(declared_size, int) or not 0 < declared_size <= MAX_UPSTREAM_ASSET_BYTES:
        raise PrebuiltCIError(f"Upstream asset has an invalid size: {asset.get('name')!r}")
    # Release binaries are public. Never send the workflow token through the
    # browser-download redirect to GitHub's asset CDN.
    headers = {"User-Agent": "codex-provider-runtime-prebuilt-ci"}
    request = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as target:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_UPSTREAM_ASSET_BYTES:
                    raise PrebuiltCIError("Official release asset exceeded the download limit")
                digest.update(block)
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
    except (OSError, urllib.error.URLError) as error:
        destination.unlink(missing_ok=True)
        raise PrebuiltCIError(f"Could not download upstream asset {asset.get('name')}: {error}") from error
    if total == 0 or total != declared_size:
        destination.unlink(missing_ok=True)
        raise PrebuiltCIError(f"Downloaded size does not match upstream metadata for {asset.get('name')}")
    expected_digest = asset.get("digest")
    if isinstance(expected_digest, str) and expected_digest.startswith("sha256:"):
        if digest.hexdigest() != expected_digest.removeprefix("sha256:"):
            destination.unlink(missing_ok=True)
            raise PrebuiltCIError(f"SHA-256 does not match GitHub metadata for {asset.get('name')}")
    return digest.hexdigest()


def _safe_extract_upstream_binary(
    archive_path: Path, destination: Path, allowed_names: set[str]
) -> None:
    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as error:
        raise PrebuiltCIError(f"Official upstream asset is not a gzip tar: {error}") from error
    with archive:
        candidates: list[tarfile.TarInfo] = []
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or not member.name or any(part in {"", ".", ".."} for part in path.parts):
                raise PrebuiltCIError(f"Unsafe path in official release archive: {member.name!r}")
            if not member.isreg() or member.linkname:
                raise PrebuiltCIError(f"Non-regular member in official release archive: {member.name!r}")
            if member.size < 0 or member.size > MAX_UPSTREAM_ASSET_BYTES:
                raise PrebuiltCIError(f"Oversized member in official release archive: {member.name!r}")
            if path.name in allowed_names:
                candidates.append(member)
        if len(candidates) != 1:
            raise PrebuiltCIError(
                f"Expected one executable named {sorted(allowed_names)!r} in {archive_path.name}, "
                f"found {len(candidates)}"
            )
        stream = archive.extractfile(candidates[0])
        if stream is None:
            raise PrebuiltCIError("Could not read executable from official upstream archive")
        with stream, destination.open("wb") as output:
            shutil.copyfileobj(stream, output, length=1024 * 1024)
        destination.chmod(0o755)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_arm64(binary: Path, description: str) -> str:
    file_tool = shutil.which("file") or "/usr/bin/file"
    result = subprocess.run(
        [file_tool, "-b", os.fspath(binary)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = result.stdout.strip()
    if "Mach-O" not in output or not re.search(r"\barm64\b|\baarch64\b", output, re.I):
        raise PrebuiltCIError(f"Official {description} is not a macOS arm64 binary: {output}")
    return output


def verify_official_assets(version: str, destination: Path) -> dict:
    version = _valid_version(version)
    release = _release_for_tag(UPSTREAM_REPOSITORY, f"rust-v{version}")
    if release is None:
        raise PrebuiltCIError(f"Official upstream release rust-v{version} was not found")
    assets = _require_upstream_assets(release, required=True)
    assert assets is not None
    destination.mkdir(parents=True, exist_ok=True)
    codex_archive = destination / CODEX_ASSET
    host_archive = destination / HOST_ASSET
    codex_hash = _download_asset(assets[CODEX_ASSET], codex_archive)
    host_hash = _download_asset(assets[HOST_ASSET], host_archive)
    codex_binary = destination / ".official-codex"
    host_binary = destination / ".official-code-mode-host"
    _safe_extract_upstream_binary(
        codex_archive,
        codex_binary,
        {"codex-aarch64-apple-darwin", "codex"},
    )
    _safe_extract_upstream_binary(
        host_archive,
        host_binary,
        {"codex-code-mode-host-aarch64-apple-darwin", "codex-code-mode-host"},
    )
    codex_description = _assert_arm64(codex_binary, "Codex CLI")
    host_description = _assert_arm64(host_binary, "code-mode host")
    version_result = subprocess.run(
        [os.fspath(codex_binary), "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    match = re.fullmatch(r"codex-cli\s+(\S+)\s*", version_result.stdout)
    if not match or match.group(1) != version:
        raise PrebuiltCIError(
            f"Official Codex asset reports version {version_result.stdout.strip()!r}; expected {version!r}"
        )
    evidence = {
        "source_tag": f"rust-v{version}",
        "codex_asset": CODEX_ASSET,
        "codex_asset_sha256": codex_hash,
        "codex_binary_file": codex_description,
        "codex_binary_version": version,
        "code_mode_host_asset": HOST_ASSET,
        "code_mode_host_asset_sha256": host_hash,
        "code_mode_host_binary_file": host_description,
    }
    codex_binary.unlink(missing_ok=True)
    host_binary.unlink(missing_ok=True)
    return evidence


def _verify_smoke(manifest: dict) -> None:
    smoke = manifest.get("protocol_smoke")
    if not isinstance(smoke, dict):
        raise PrebuiltCIError("Build manifest has no app-server protocol smoke result")
    new_thread = smoke.get("new_thread_routing")
    if not isinstance(new_thread, dict):
        raise PrebuiltCIError("Protocol smoke has no new-thread routing result")
    expected = {
        "deepseek-flash": "deepseek",
        "deepseek-v4-flash": "deepseek",
        "deepseek-v4-pro": "deepseek",
        "gpt-5.6-sol": "openai",
    }
    if new_thread != expected:
        raise PrebuiltCIError(f"Protocol smoke routing does not match the contract: {new_thread!r}")
    resumed = smoke.get("resumed_thread_routing")
    if not isinstance(resumed, dict) or not resumed:
        raise PrebuiltCIError("Protocol smoke has no resumed-thread routing evidence")
    visibility = smoke.get("thread_list_visibility")
    if not isinstance(visibility, dict) or visibility.get("omitted_null_empty_match") is not True:
        raise PrebuiltCIError("Protocol smoke did not prove all-provider thread/list visibility")
    seeded = visibility.get("seeded_providers")
    if not isinstance(seeded, dict) or seeded.get("gpt-5.6-sol") != "openai" or seeded.get("deepseek-flash") != "deepseek":
        raise PrebuiltCIError("Protocol smoke did not seed both GPT and DeepSeek thread history")
    providers = visibility.get("providers")
    if not isinstance(providers, dict) or not {"openai", "deepseek"}.issubset(providers):
        raise PrebuiltCIError("Protocol smoke history page did not include GPT and DeepSeek providers")


def _package_archive(binary: Path, manifest_path: Path, archive_path: Path) -> None:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise PrebuiltCIError("Build output codex is missing or not executable")
    if not manifest_path.is_file():
        raise PrebuiltCIError("Build output manifest.json is missing")
    manifest_data = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_data)
    except json.JSONDecodeError as error:
        raise PrebuiltCIError("Build output manifest.json is invalid JSON") from error
    if not isinstance(manifest, dict):
        raise PrebuiltCIError("Build output manifest.json must be an object")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with __import__("gzip").GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, source, mode in (
                    ("codex", binary, 0o755),
                    ("manifest.json", manifest_path, 0o644),
                ):
                    data = source.read_bytes()
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = mode
                    entry.uid = entry.gid = 0
                    entry.uname = entry.gname = ""
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(data))
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if [member.name for member in members] != ["codex", "manifest.json"]:
            raise PrebuiltCIError("Packaged archive does not have the exact two-file contract")
        if any(not member.isreg() for member in members):
            raise PrebuiltCIError("Packaged archive contains a non-regular member")


def build(version: str, install_root: Path, output_dir: Path, runtime_root: Path = RUNTIME) -> dict:
    version = _valid_version(version)
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    ref = os.environ.get("GITHUB_REF", "")
    commit = os.environ.get("GITHUB_SHA", "")
    if repository != REPOSITORY or ref != "refs/heads/main" or not COMMIT_RE.fullmatch(commit):
        raise PrebuiltCIError("Prebuilt builds require this repository's 40-character main commit")

    official_evidence = verify_official_assets(version, install_root / "official-release")
    output_dir.mkdir(parents=True, exist_ok=True)
    manager = runtime_root / "router_manager.py"
    command = [
        sys.executable,
        os.fspath(manager),
        "--install-root",
        os.fspath(install_root),
        "prebuild",
        "--version",
        version,
        "--output",
        os.fspath(output_dir),
    ]
    build_env = os.environ.copy()
    build_env["CARGO_NET_GIT_FETCH_WITH_CLI"] = "true"
    toolchain = build_env.get("PREBUILT_TOOLCHAIN", "")
    if not toolchain or not re.fullmatch(r"[A-Za-z0-9._+-]+", toolchain):
        raise PrebuiltCIError("Exact-tag Rust toolchain was not prepared")
    build_env["RUSTUP_TOOLCHAIN"] = toolchain
    subprocess.run(command, check=True, env=build_env)

    manifest_path = output_dir / "manifest.json"
    binary = output_dir / "codex"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PrebuiltCIError(f"Builder did not produce a valid manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise PrebuiltCIError("Builder manifest must be a JSON object")
    source_commit = prebuilt_release.resolve_source_tag_commit(version)
    patch_path = runtime_root / "patches" / "provider_route.rs"
    patch_sha = _sha256_file(patch_path)
    recipe_sha = prebuilt_release.build_recipe_sha256(runtime_root)
    expected = {
        "schema": 1,
        "codex_version": version,
        "source_tag": f"rust-v{version}",
        "source_commit": source_commit,
        "patch_name": router_manager.PATCH_NAME,
        "patch_sha256": patch_sha,
        "recipe_sha256": recipe_sha,
        "platform": "macos",
        "architecture": "aarch64",
        "runtime_source_ref": "refs/heads/main",
        "runtime_source_commit": commit,
        "minimum_macos_version": MINIMUM_MACOS_VERSION,
        "binary_sha256": _sha256_file(binary),
    }
    mismatches = [
        f"{key}={manifest.get(key)!r}, expected {value!r}"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    if mismatches:
        raise PrebuiltCIError("Builder manifest contract mismatch: " + "; ".join(mismatches))
    if not _version_at_most(
        manifest.get("binary_minimum_macos_version"), MINIMUM_MACOS_VERSION
    ):
        raise PrebuiltCIError("Built Mach-O minimum macOS version exceeds 13.0")
    prebuilt_release.validate_manifest(
        manifest,
        version=version,
        source_commit=source_commit,
        patch_name=router_manager.PATCH_NAME,
        patch_sha256=patch_sha,
        recipe_sha256=recipe_sha,
    )
    _verify_smoke(manifest)
    if manifest.get("tests") is None or not any(
        "protocol smoke" in str(value).lower() for value in manifest["tests"]
    ):
        raise PrebuiltCIError("Builder manifest does not record the protocol smoke suite")

    manifest["official_release_assets"] = official_evidence
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    archive_path = output_dir / ARCHIVE_ASSET
    _package_archive(binary, manifest_path, archive_path)
    recipe = manifest["recipe_sha256"]
    candidate = {
        "schema": 1,
        "release_tag": prebuilt_release.release_tag(version, recipe),
        "codex_version": version,
        "recipe_sha256": recipe,
        "archive_asset": ARCHIVE_ASSET,
        "archive_sha256": _sha256_file(archive_path),
        "source_tag": manifest["source_tag"],
        "source_commit": manifest["source_commit"],
        "runtime_source_commit": commit,
    }
    (output_dir / "release-candidate.json").write_text(
        json.dumps(candidate, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    # The synthetic, isolated model catalog is build evidence only, never part
    # of the downloadable release artifact.
    (output_dir / ".synthetic-smoke-models.json").unlink(missing_ok=True)
    return candidate


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
        for name in files:
            file_path = root_path / name
            try:
                if not file_path.is_symlink():
                    total += file_path.stat().st_size
            except OSError:
                continue
    return total


def cache_metadata(version: str, runtime_root: Path = RUNTIME) -> dict[str, str]:
    version = _valid_version(version)
    lock = _raw_upstream_file(version, "codex-rs/Cargo.lock", max_bytes=64 * 1024 * 1024)
    if not lock or len(lock) > 64 * 1024 * 1024:
        raise PrebuiltCIError("Exact-tag Cargo.lock is empty or unexpectedly large")
    toolchain_data = None
    for relative in ("codex-rs/rust-toolchain.toml", "rust-toolchain.toml"):
        try:
            toolchain_data = _raw_upstream_file(version, relative, max_bytes=64 * 1024)
            break
        except PrebuiltCIError as error:
            if "HTTP 404" not in str(error):
                raise
    if toolchain_data is None:
        raise PrebuiltCIError("Exact-tag source has no rust-toolchain.toml")
    channel = _toolchain_channel(toolchain_data.decode("utf-8", errors="strict"))
    return {
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
        "recipe_sha256": _current_recipe(runtime_root),
        "toolchain": channel,
        "target": "aarch64-apple-darwin",
    }


def _raw_upstream_file(version: str, relative: str, *, max_bytes: int) -> bytes:
    url = (
        "https://raw.githubusercontent.com/openai/codex/"
        f"rust-v{urllib.parse.quote(version, safe='')}/{relative}"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "codex-provider-runtime-prebuilt-ci"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise PrebuiltCIError(f"Exact-tag source file returned HTTP 404: {relative}") from error
        raise PrebuiltCIError(
            f"Could not read exact-tag source file {relative}: HTTP {error.code}"
        ) from error
    except (OSError, urllib.error.URLError) as error:
        raise PrebuiltCIError(f"Could not read exact-tag source file {relative}: {error}") from error
    if len(data) > max_bytes:
        raise PrebuiltCIError(f"Exact-tag source file is unexpectedly large: {relative}")
    return data


def _toolchain_channel(data: str) -> str:
    match = re.search(r"(?m)^\s*channel\s*=\s*['\"]([^'\"]+)['\"]\s*(?:#.*)?$", data)
    if not match:
        raise PrebuiltCIError("Exact-tag rust-toolchain.toml has no channel")
    channel = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", channel):
        raise PrebuiltCIError(f"Invalid Rust toolchain channel: {channel!r}")
    return channel


def guard_cache(paths: Iterable[Path], maximum: int = MAX_CACHE_BYTES) -> dict[str, str]:
    total = sum(_directory_bytes(path) for path in paths)
    if total > maximum:
        return {"bytes": str(total), "within_limit": "false"}
    return {"bytes": str(total), "within_limit": "true"}


def test_route_asset(runtime_root: Path = RUNTIME) -> int:
    with tempfile.TemporaryDirectory(prefix="prebuilt-route-tests-") as temporary:
        return router_manager.run_provider_route_asset_tests(
            runtime_root / "patches" / "provider_route.rs", Path(temporary)
        )


def _validate_archive_and_manifest(archive_path: Path, candidate_path: Path, runtime_root: Path) -> dict:
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, json.JSONDecodeError, tarfile.TarError) as error:
        raise PrebuiltCIError(f"Release candidate artifact is invalid: {error}") from error
    if not isinstance(candidate, dict):
        raise PrebuiltCIError("Release candidate metadata must be an object")
    with archive:
        members = archive.getmembers()
        if len(members) != 2 or {member.name for member in members} != {"codex", "manifest.json"}:
            raise PrebuiltCIError("Release archive must contain exactly codex and manifest.json")
        by_name = {member.name: member for member in members}
        if any(not member.isreg() or member.linkname for member in members):
            raise PrebuiltCIError("Release archive members must be regular files")
        manifest_stream = archive.extractfile(by_name["manifest.json"])
        if manifest_stream is None:
            raise PrebuiltCIError("Release archive manifest cannot be read")
        try:
            manifest = json.loads(manifest_stream.read(128 * 1024 + 1))
        except json.JSONDecodeError as error:
            raise PrebuiltCIError("Release archive manifest is invalid JSON") from error
    if not isinstance(manifest, dict):
        raise PrebuiltCIError("Release archive manifest must be an object")
    version = candidate.get("codex_version")
    source_commit = prebuilt_release.resolve_source_tag_commit(version)
    patch_path = runtime_root / "patches" / "provider_route.rs"
    patch_sha = _sha256_file(patch_path)
    recipe_sha = prebuilt_release.build_recipe_sha256(runtime_root)
    prebuilt_release.validate_manifest(
        manifest,
        version=version,
        source_commit=source_commit,
        patch_name=router_manager.PATCH_NAME,
        patch_sha256=patch_sha,
        recipe_sha256=recipe_sha,
    )
    if candidate.get("release_tag") != prebuilt_release.release_tag(version, recipe_sha):
        raise PrebuiltCIError("Release candidate tag does not match the source recipe")
    if candidate.get("archive_sha256") != _sha256_file(archive_path):
        raise PrebuiltCIError("Release candidate archive SHA-256 does not match")
    if manifest.get("runtime_source_commit") != candidate.get("runtime_source_commit"):
        raise PrebuiltCIError("Release candidate source commit does not match its manifest")
    _verify_smoke(manifest)
    candidate["minimum_macos_version"] = manifest.get("minimum_macos_version", "13.0")
    candidate["source_tag"] = manifest.get("source_tag")
    candidate["source_commit"] = manifest.get("source_commit")
    return candidate


def publish_plan(release_tag: str) -> dict[str, str]:
    release = _release_for_tag(REPOSITORY, release_tag)
    return {
        "already_complete": "true" if _release_is_complete(release) else "false",
    }


def verify_bundle(archive: Path, bundle: Path) -> None:
    if not archive.is_file() or not bundle.is_file() or bundle.stat().st_size == 0:
        raise PrebuiltCIError("Archive or artifact-attestation bundle is missing")
    subprocess.run(
        [
            "gh",
            "attestation",
            "verify",
            os.fspath(archive),
            "--bundle",
            os.fspath(bundle),
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            prebuilt_release.SIGNER_WORKFLOW,
            "--source-ref",
            prebuilt_release.SOURCE_REF,
        ],
        check=True,
    )


def publish(artifact_dir: Path, runtime_root: Path = RUNTIME) -> None:
    archive = artifact_dir / ARCHIVE_ASSET
    candidate_path = artifact_dir / "release-candidate.json"
    candidate = _validate_archive_and_manifest(archive, candidate_path, runtime_root)
    release_tag = candidate["release_tag"]
    notes_path = artifact_dir / ".release-notes.md"
    notes = (
        f"Patched Codex CLI {candidate['codex_version']} for macOS arm64.\n\n"
        f"Minimum macOS version: `{candidate['minimum_macos_version']}`\n"
        f"Upstream source: `{candidate['source_tag']}` (`{candidate['source_commit']}`).\n"
        f"Runtime source commit: `{candidate['runtime_source_commit']}`.\n"
        f"Build recipe SHA-256: `{candidate['recipe_sha256']}`.\n"
        "The tar archive contains only `codex` and `manifest.json`; the archive is covered by the attached GitHub artifact attestation.\n"
    )
    notes_path.write_text(notes, encoding="utf-8")
    # Verify the signature before creating a GitHub Release. A bad bundle must
    # never leave behind an empty or misleading release record.
    verify_bundle(archive, artifact_dir / ATTESTATION_ASSET)
    existing = _release_for_tag(REPOSITORY, release_tag)
    if _release_is_complete(existing):
        return
    if existing is None:
        command = [
            "gh",
            "release",
            "create",
            release_tag,
            "--repo",
            REPOSITORY,
            "--target",
            os.environ["GITHUB_SHA"],
            "--title",
            release_tag,
            "--notes-file",
            os.fspath(notes_path),
        ]
        subprocess.run(command, check=True)

    command = ["gh", "release", "upload", release_tag]
    command.extend(
        os.fspath(path)
        for path in (archive, artifact_dir / ATTESTATION_ASSET)
    )
    command.extend(["--repo", REPOSITORY, "--clobber"])
    subprocess.run(command, check=True)


def _default_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prebuilt_ci.py")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    plan_parser.add_argument("--version", default=os.environ.get("PREBUILT_VERSION_INPUT", ""))
    plan_parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    cache_parser = commands.add_parser("cache-metadata")
    cache_parser.add_argument("--version", default=os.environ.get("PREBUILT_VERSION", ""))
    cache_parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    guard_parser = commands.add_parser("guard-cache")
    guard_parser.add_argument("--path", type=Path, action="append", required=True)
    guard_parser.add_argument("--maximum-bytes", type=int, default=MAX_CACHE_BYTES)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--version", default=os.environ.get("PREBUILT_VERSION", ""))
    build_parser.add_argument("--install-root", type=Path, required=True)
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    publish_plan_parser = commands.add_parser("publish-plan")
    publish_plan_parser.add_argument(
        "--release-tag", default=os.environ.get("PREBUILT_RELEASE_TAG", "")
    )
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--artifact-dir", type=Path, required=True)
    publish_parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    route_parser = commands.add_parser("test-route")
    route_parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _default_parser().parse_args(argv)
    try:
        if args.command == "plan":
            _write_outputs(plan(args.event, args.version, args.runtime_root))
        elif args.command == "cache-metadata":
            _write_outputs(cache_metadata(args.version, args.runtime_root))
        elif args.command == "guard-cache":
            _write_outputs(guard_cache(args.path, args.maximum_bytes))
        elif args.command == "build":
            result = build(args.version, args.install_root, args.output_dir, args.runtime_root)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "publish-plan":
            if not args.release_tag:
                raise PrebuiltCIError("A release tag is required")
            _write_outputs(publish_plan(args.release_tag))
        elif args.command == "publish":
            publish(args.artifact_dir, args.runtime_root)
        elif args.command == "test-route":
            print(f"provider_route pure-Rust tests passed: {test_route_asset(args.runtime_root)}")
        else:
            raise PrebuiltCIError(f"Unknown command: {args.command}")
    except (PrebuiltCIError, prebuilt_release.PrebuiltReleaseError, router_manager.RouterError) as error:
        print(f"prebuilt CI error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
