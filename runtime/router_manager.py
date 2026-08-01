#!/usr/bin/env python3
"""Build and operate a fail-closed Codex app-server DeepSeek router.

The manager never edits ChatGPT.app. It builds the exact public Codex tag that
matches the app's bundled backend and atomically switches a user-owned symlink
only after tests and smoke checks succeed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import plistlib
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Union


REPO_URL = "https://github.com/openai/codex.git"
DEFAULT_OFFICIAL_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
DEFAULT_INSTALL_ROOT = Path.home() / ".codex" / "provider-runtime"
VERSION_RE = re.compile(r"^codex-cli\s+(\S+)\s*$")
MODULE_MARKER = "mod provider_route;"
CALL_MARKER = "model_provider_for_new_thread(model.as_deref(), model_provider)"
ENVIRONMENT_LABEL = "com.codex.provider-runtime.environment"
UPDATER_LABEL = "com.codex.provider-runtime.updater"
LEGACY_SUPPORT_NAMES = {
    "com.dudu.codex-deepseek-router-environment.plist",
    "com.dudu.codex-deepseek-router-updater.plist",
    "com.example.codex-provider-router.plist",
}


class RouterError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run(
    argv: Sequence[Union[str, os.PathLike]],
    *,
    cwd: Optional[Path] = None,
    capture: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(item) for item in argv]
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )


def output(
    argv: Sequence[Union[str, os.PathLike]], *, cwd: Optional[Path] = None
) -> str:
    result = run(argv, cwd=cwd, capture=True)
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def codex_version(binary: Path) -> str:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RouterError(f"Codex binary is unavailable or not executable: {binary}")
    raw = output([binary, "--version"])
    match = VERSION_RE.match(raw)
    if not match:
        raise RouterError(f"Unexpected Codex version output from {binary}: {raw!r}")
    return match.group(1)


def replace_once(text: str, old: str, new: str, *, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RouterError(
            f"Patch anchor for {description} occurred {count} times; refusing to guess"
        )
    return text.replace(old, new, 1)


def patch_source(source_root: Path, patch_asset: Path) -> str:
    app_server = source_root / "codex-rs" / "app-server" / "src"
    parent = app_server / "request_processors.rs"
    thread = app_server / "request_processors" / "thread_processor.rs"
    destination = app_server / "request_processors" / "provider_route.rs"
    for required in (parent, thread, patch_asset):
        if not required.is_file():
            raise RouterError(f"Required patch input is missing: {required}")

    parent_text = parent.read_text(encoding="utf-8")
    thread_text = thread.read_text(encoding="utf-8")
    already_patched = (
        MODULE_MARKER in parent_text
        and CALL_MARKER in thread_text
        and destination.is_file()
    )
    if already_patched:
        return "already-patched"
    if MODULE_MARKER in parent_text or CALL_MARKER in thread_text or destination.exists():
        raise RouterError("Source contains a partial router patch; refusing mixed state")

    parent_anchor = "mod process_exec_processor;\nmod remote_control_processor;"
    parent_replacement = (
        "mod process_exec_processor;\nmod provider_route;\nmod remote_control_processor;"
    )
    parent_text = replace_once(
        parent_text,
        parent_anchor,
        parent_replacement,
        description="request processor module declaration",
    )

    use_anchor = "use super::*;\n"
    use_replacement = (
        "use super::*;\nuse super::provider_route::model_provider_for_new_thread;\n"
    )
    thread_text = replace_once(
        thread_text,
        use_anchor,
        use_replacement,
        description="thread processor import",
    )

    call_anchor = "            environments,\n        } = params;\n        if matches!("
    call_replacement = (
        "            environments,\n"
        "        } = params;\n"
        "        let model_provider =\n"
        "            model_provider_for_new_thread(model.as_deref(), model_provider);\n"
        "        if matches!("
    )
    thread_text = replace_once(
        thread_text,
        call_anchor,
        call_replacement,
        description="new-thread provider normalization",
    )

    parent.write_text(parent_text, encoding="utf-8")
    thread.write_text(thread_text, encoding="utf-8")
    shutil.copy2(patch_asset, destination)
    return "patched"


def find_cargo() -> Path:
    candidates: Iterable[Optional[str]] = (
        os.environ.get("CARGO"),
        os.fspath(Path.home() / ".cargo" / "bin" / "cargo"),
        shutil.which("cargo"),
    )
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise RouterError(
        "cargo is not installed; install rustup and the repository-pinned toolchain first"
    )


def validate_workspace_lock_diff(diff: str, version: str) -> int:
    """Allow only release-time workspace version changes in Cargo.lock."""
    removed = 0
    added = 0
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")) or not line.startswith(("-", "+")):
            continue
        if line == '-version = "0.0.0"':
            removed += 1
        elif line == f'+version = "{version}"':
            added += 1
        else:
            raise RouterError(
                "Cargo.lock normalization changed an external dependency or unexpected field: "
                + line[:200]
            )
    if removed != added:
        raise RouterError(
            f"Cargo.lock workspace version changes are unbalanced: -{removed} +{added}"
        )
    return added


def normalize_release_lock(
    cargo: Path, cargo_root: Path, source: Path, version: str, env: Dict[str, str]
) -> int:
    # Public release tags update [workspace.package].version, while the checked-in
    # lock can still contain the development sentinel 0.0.0. Ask Cargo to update
    # only path/workspace packages and stay offline, then strictly prove that no
    # registry or git dependency changed before returning to --locked builds.
    run([cargo, "update", "--workspace", "--offline"], cwd=cargo_root, env=env)
    diff = output(
        ["git", "-C", source, "diff", "--unified=0", "--", "codex-rs/Cargo.lock"]
    )
    changed = validate_workspace_lock_diff(diff, version)
    print(f"Cargo.lock workspace versions normalized: {changed}")
    return changed


def ensure_source_checkout(install_root: Path, version: str) -> tuple[Path, str, str]:
    cache_root = install_root / "cache"
    repo = cache_root / "codex"
    cache_root.mkdir(parents=True, exist_ok=True)
    tag = f"rust-v{version}"
    if not (repo / ".git").exists():
        run(["git", "clone", "--filter=blob:none", "--no-checkout", REPO_URL, repo])
    elif output(["git", "-C", repo, "remote", "get-url", "origin"]) != REPO_URL:
        raise RouterError(f"Unexpected source remote in {repo}")

    run(["git", "-C", repo, "fetch", "--depth=1", "origin", "tag", tag])
    commit = output(["git", "-C", repo, "rev-list", "-n", "1", tag])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RouterError(f"Could not resolve exact source commit for {tag}")

    worktree = install_root / "builds" / f"{version}-{commit[:12]}" / "source"
    if worktree.exists():
        actual = output(["git", "-C", worktree, "rev-parse", "HEAD"])
        if actual != commit:
            raise RouterError(f"Existing worktree has unexpected commit: {worktree}")
    else:
        worktree.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "-C", repo, "worktree", "add", "--detach", worktree, commit])
    return worktree, tag, commit


def sign_if_available(binary: Path) -> None:
    codesign = shutil.which("codesign")
    if not codesign:
        return
    run([codesign, "--force", "--sign", "-", binary])
    run([codesign, "--verify", "--verbose=2", binary])


def send_json_line(process: subprocess.Popen, payload: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()


def read_response(process: subprocess.Popen, request_id: int, timeout: float = 30.0) -> dict:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wait_for = max(0.0, min(0.5, deadline - time.monotonic()))
        ready, _, _ = select.select([process.stdout], [], [], wait_for)
        if not ready:
            if process.poll() is not None:
                raise RouterError(f"app-server exited before response {request_id}")
            continue
        line = process.stdout.readline()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            raise RouterError(f"app-server emitted non-JSON stdout: {line[:200]!r}") from error
        if message.get("id") == request_id:
            if "error" in message:
                raise RouterError(f"app-server request {request_id} failed: {message['error']}")
            return message.get("result", {})
    raise RouterError(f"timed out waiting for app-server response {request_id}")


def configured_model_catalog() -> Path:
    config = Path.home() / ".codex" / "config.toml"
    if not config.is_file():
        raise RouterError(f"Codex config is missing: {config}")
    match = re.search(
        r'^model_catalog_json\s*=\s*"([^"]+)"\s*$',
        config.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RouterError("model_catalog_json is not configured; cannot run protocol smoke test")
    catalog = Path(match.group(1)).expanduser()
    if not catalog.is_file():
        raise RouterError(f"Configured model catalog is missing: {catalog}")
    return catalog


def protocol_smoke(binary: Path) -> dict:
    catalog = configured_model_catalog()
    with tempfile.TemporaryDirectory(prefix="codex-router-smoke-") as temporary:
        codex_home = Path(temporary)
        config = (
            'model = "gpt-5.6-sol"\n'
            f"model_catalog_json = {json.dumps(os.fspath(catalog))}\n\n"
            "[model_providers.deepseek]\n"
            'name = "DeepSeek"\n'
            'base_url = "https://api.deepseek.com"\n'
            'wire_api = "responses"\n'
        )
        (codex_home / "config.toml").write_text(config, encoding="utf-8")
        stderr_path = codex_home / "stderr.log"
        env = os.environ.copy()
        env["CODEX_HOME"] = os.fspath(codex_home)
        env["CODEX_CLI_PATH"] = os.fspath(binary)
        with stderr_path.open("w+", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [os.fspath(binary), "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                bufsize=1,
                env=env,
            )
            try:
                send_json_line(
                    process,
                    {
                        "method": "initialize",
                        "id": 1,
                        "params": {
                            "clientInfo": {
                                "name": "codex-deepseek-router-smoke",
                                "title": "Codex DeepSeek router smoke",
                                "version": "1",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                )
                read_response(process, 1)
                send_json_line(process, {"method": "initialized"})
                providers = {}
                for request_id, model, expected in (
                    (2, "deepseek-v4-flash", "deepseek"),
                    (3, "gpt-5.6-sol", "openai"),
                ):
                    send_json_line(
                        process,
                        {
                            "method": "thread/start",
                            "id": request_id,
                            "params": {
                                "model": model,
                                "cwd": "/private/tmp",
                                "approvalPolicy": "never",
                                "sandbox": "read-only",
                                "ephemeral": True,
                                "environments": [],
                            },
                        },
                    )
                    result = read_response(process, request_id)
                    provider = result.get("modelProvider")
                    thread_provider = result.get("thread", {}).get("modelProvider")
                    if provider != expected or thread_provider != expected:
                        raise RouterError(
                            f"protocol smoke mismatch for {model}: response={provider!r} "
                            f"thread={thread_provider!r}, expected={expected!r}"
                        )
                    providers[model] = provider
                return providers
            except Exception as error:
                stderr.flush()
                stderr.seek(0)
                detail = stderr.read()[-4000:]
                if detail:
                    raise RouterError(f"{error}\napp-server stderr:\n{detail}") from error
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def activate_release(install_root: Path, release_name: str) -> None:
    current = install_root / "current"
    temporary = install_root / f".current.next.{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(Path("releases") / release_name)
    os.replace(temporary, current)


def verify_existing_release(release: Path, manifest: dict, version: str) -> None:
    custom = release / "codex"
    host = release / "codex-code-mode-host"
    if sha256(custom) != manifest.get("custom_sha256"):
        raise RouterError(f"Existing custom Codex checksum mismatch: {custom}")
    if sha256(host) != manifest.get("code_mode_host_sha256"):
        raise RouterError(f"Existing code-mode-host checksum mismatch: {host}")
    if codex_version(custom) != version:
        raise RouterError(f"Existing custom Codex version mismatch: {custom}")
    run([host, "--help"], capture=True)
    smoke_name = "app-server DeepSeek/GPT new-thread protocol smoke"
    if smoke_name not in manifest.get("tests", []):
        smoke_result = protocol_smoke(custom)
        manifest.setdefault("tests", []).append(smoke_name)
        manifest["protocol_smoke"] = smoke_result
        manifest["certified_at"] = utc_now()
        temporary = release / f"manifest.json.next.{os.getpid()}"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, release / "manifest.json")
        print("Certified existing release protocol routing")


def build_release(
    install_root: Path,
    official_codex: Path,
    patch_asset: Path,
    *,
    non_interactive: bool,
) -> Path:
    del non_interactive  # Reserved for future notification policy; builds are always deterministic.
    version = codex_version(official_codex)
    official_digest = sha256(official_codex)
    source, tag, commit = ensure_source_checkout(install_root, version)
    release_name = f"{version}-{commit[:12]}-{official_digest[:12]}"
    release = install_root / "releases" / release_name

    if release.is_dir():
        manifest_path = release / "manifest.json"
        if not manifest_path.is_file():
            raise RouterError(f"Existing release has no manifest: {release}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "codex_version": version,
            "source_commit": commit,
            "official_sha256": official_digest,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RouterError(f"Existing release manifest does not match: {release}")
        verify_existing_release(release, manifest, version)
        activate_release(install_root, release_name)
        return release

    patch_state = patch_source(source, patch_asset)
    print(f"Patch state: {patch_state}")
    cargo = find_cargo()
    cargo_root = source / "codex-rs"
    cargo_target = install_root / "cache" / "cargo-target"
    cargo_target.mkdir(parents=True, exist_ok=True)
    build_env = os.environ.copy()
    build_env["CARGO_TARGET_DIR"] = os.fspath(cargo_target)
    lock_versions_normalized = normalize_release_lock(
        cargo, cargo_root, source, version, build_env
    )
    run(
        [cargo, "test", "--locked", "-p", "codex-app-server", "provider_route", "--lib"],
        cwd=cargo_root,
        env=build_env,
    )
    run(
        [
            cargo,
            "build",
            "--release",
            "--locked",
            "-p",
            "codex-cli",
            "-p",
            "codex-code-mode-host",
        ],
        cwd=cargo_root,
        env=build_env,
    )

    built_codex = cargo_target / "release" / "codex"
    built_host = cargo_target / "release" / "codex-code-mode-host"
    if codex_version(built_codex) != version:
        raise RouterError("Built Codex version does not match the bundled client version")
    run([built_host, "--help"], capture=True)
    smoke_result = protocol_smoke(built_codex)
    print("Protocol smoke:", json.dumps(smoke_result, ensure_ascii=False, sort_keys=True))
    sign_if_available(built_codex)
    sign_if_available(built_host)

    staging = install_root / "releases" / f".staging-{release_name}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    shutil.copy2(built_codex, staging / "codex")
    shutil.copy2(built_host, staging / "codex-code-mode-host")
    for binary in (staging / "codex", staging / "codex-code-mode-host"):
        binary.chmod(0o755)
    manifest = {
        "schema": 1,
        "codex_version": version,
        "source_tag": tag,
        "source_commit": commit,
        "official_binary": os.fspath(official_codex),
        "official_sha256": official_digest,
        "custom_sha256": sha256(staging / "codex"),
        "code_mode_host_sha256": sha256(staging / "codex-code-mode-host"),
        "patch": "new-thread-deepseek-provider-route-v1",
        "built_at": utc_now(),
        "workspace_lock_versions_normalized": lock_versions_normalized,
        "protocol_smoke": smoke_result,
        "tests": [
            "provider_route unit tests",
            "binary version smoke",
            "code-mode-host help",
            "app-server DeepSeek/GPT new-thread protocol smoke",
        ],
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    release.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, release)
    activate_release(install_root, release_name)
    return release


def acquire_update_lock(install_root: Path):
    install_root.mkdir(parents=True, exist_ok=True)
    lock_path = install_root / "update.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def plist_environment(launcher: Path) -> dict:
    return {
        "Label": ENVIRONMENT_LABEL,
        "ProgramArguments": [
            "/bin/launchctl",
            "setenv",
            "CODEX_CLI_PATH",
            os.fspath(launcher),
        ],
        "RunAtLoad": True,
    }


def plist_updater(manager: Path, official_codex: Path, install_root: Path) -> dict:
    path_entries = [
        os.fspath(Path.home() / ".cargo" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    return {
        "Label": UPDATER_LABEL,
        "ProgramArguments": [
            "/usr/bin/python3",
            os.fspath(manager),
            "--install-root",
            os.fspath(install_root),
            "--official-codex",
            os.fspath(official_codex),
            "update",
            "--non-interactive",
        ],
        "EnvironmentVariables": {"PATH": ":".join(path_entries)},
        "RunAtLoad": True,
        "StartInterval": 900,
        "WatchPaths": [os.fspath(official_codex)],
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": os.fspath(install_root / "logs" / "updater.log"),
        "StandardErrorPath": os.fspath(install_root / "logs" / "updater.log"),
    }


def install_support(project_root: Path, install_root: Path, official_codex: Path) -> None:
    lib = install_root / "lib"
    bin_dir = install_root / "bin"
    logs = install_root / "logs"
    for directory in (lib / "patches", lib / "templates", bin_dir, logs):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root / "router_manager.py", lib / "router_manager.py")
    shutil.copy2(
        project_root / "patches" / "provider_route.rs",
        lib / "patches" / "provider_route.rs",
    )
    shutil.copy2(
        project_root / "templates" / "codex-router",
        lib / "templates" / "codex-router",
    )
    launcher = bin_dir / "codex-router"
    shutil.copy2(project_root / "templates" / "codex-router", launcher)
    launcher.chmod(0o755)

    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    definitions = {
        agents / f"{ENVIRONMENT_LABEL}.plist": plist_environment(launcher),
        agents / f"{UPDATER_LABEL}.plist": plist_updater(
            lib / "router_manager.py", official_codex, install_root
        ),
    }
    for path, payload in definitions.items():
        temporary = path.with_suffix(path.suffix + f".next.{os.getpid()}")
        with temporary.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=True)
        os.replace(temporary, path)
        print(f"Installed {path}")
    print("Support files installed. Load the LaunchAgents only after resolving legacy CODEX_CLI_PATH agents.")


def launchctl_allow_failure(arguments: Sequence[str]) -> subprocess.CompletedProcess:
    command = ["/bin/launchctl"] + list(arguments)
    print("+", " ".join(command), flush=True)
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def activate_support(project_root: Path, install_root: Path, official_codex: Path) -> None:
    manifest = read_manifest(install_root)
    if not manifest:
        raise RouterError("No built router release is available; run update first")
    version = codex_version(official_codex)
    if manifest.get("codex_version") != version:
        raise RouterError("Custom release does not match the current bundled Codex version")
    verify_existing_release(install_root / "current", manifest, version)
    install_support(project_root, install_root, official_codex)

    domain = f"gui/{os.getuid()}"
    backups = install_root / "backups" / "launchagents"
    backups.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    legacy_agents = sorted(set(legacy_cli_path_agents()) | set(legacy_support_agents()))
    for legacy_name in legacy_agents:
        legacy = Path(legacy_name)
        try:
            with legacy.open("rb") as handle:
                label = plistlib.load(handle).get("Label")
        except (OSError, plistlib.InvalidFileException):
            label = None
        if label:
            launchctl_allow_failure(["bootout", f"{domain}/{label}"])
        destination = backups / f"{legacy.stem}.{timestamp}{legacy.suffix}"
        shutil.move(os.fspath(legacy), os.fspath(destination))
        print(f"Backed up legacy CODEX_CLI_PATH agent: {destination}")

    agents = Path.home() / "Library" / "LaunchAgents"
    environment_agent = agents / f"{ENVIRONMENT_LABEL}.plist"
    updater_agent = agents / f"{UPDATER_LABEL}.plist"
    for label, path in (
        (ENVIRONMENT_LABEL, environment_agent),
        (UPDATER_LABEL, updater_agent),
    ):
        launchctl_allow_failure(["bootout", f"{domain}/{label}"])
        run(["/bin/launchctl", "bootstrap", domain, path])

    launcher = install_root / "bin" / "codex-router"
    run(["/bin/launchctl", "setenv", "CODEX_CLI_PATH", launcher])
    active = active_cli_path()
    if active != os.fspath(launcher):
        raise RouterError(
            f"launchctl CODEX_CLI_PATH verification failed: expected {launcher}, got {active!r}"
        )
    print("Router support activated. Restart ChatGPT/Codex when ready to use the new backend.")


def read_manifest(install_root: Path) -> Optional[dict]:
    manifest = install_root / "current" / "manifest.json"
    if not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8"))


def legacy_cli_path_agents() -> list[str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    if not agents.is_dir():
        return []
    current_name = f"{ENVIRONMENT_LABEL}.plist"
    found: list[str] = []
    for path in sorted(agents.glob("*.plist")):
        if path.name == current_name:
            continue
        try:
            if "CODEX_CLI_PATH" in path.read_text(encoding="utf-8"):
                found.append(os.fspath(path))
        except (OSError, UnicodeDecodeError):
            continue
    return found


def legacy_support_agents() -> list[str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    if not agents.is_dir():
        return []
    return [
        os.fspath(path)
        for name in sorted(LEGACY_SUPPORT_NAMES)
        if (path := agents / name).is_file()
    ]


def active_cli_path() -> Optional[str]:
    launchctl = Path("/bin/launchctl")
    try:
        value = output([launchctl, "getenv", "CODEX_CLI_PATH"])
    except subprocess.CalledProcessError:
        return None
    return value or None


def status(install_root: Path, official_codex: Path) -> int:
    payload: dict[str, object] = {
        "install_root": os.fspath(install_root),
        "official_binary": os.fspath(official_codex),
        "checked_at": utc_now(),
    }
    try:
        payload["official_version"] = codex_version(official_codex)
        payload["official_sha256"] = sha256(official_codex)
    except RouterError as error:
        payload["official_error"] = str(error)
    manifest = read_manifest(install_root)
    disabled = (install_root / "disabled").exists()
    payload["current_release"] = manifest
    payload["disabled"] = disabled
    payload["active_CODEX_CLI_PATH"] = active_cli_path()
    payload["legacy_cli_path_agents"] = legacy_cli_path_agents()
    payload["legacy_support_agents"] = legacy_support_agents()
    payload["support_agents"] = {
        "environment": os.fspath(
            Path.home() / "Library" / "LaunchAgents" / f"{ENVIRONMENT_LABEL}.plist"
        ),
        "updater": os.fspath(
            Path.home() / "Library" / "LaunchAgents" / f"{UPDATER_LABEL}.plist"
        ),
    }
    try:
        payload["cargo"] = os.fspath(find_cargo())
    except RouterError:
        payload["cargo"] = None
    payload["router_will_activate"] = bool(
        not disabled
        and manifest
        and manifest.get("codex_version") == payload.get("official_version")
        and (install_root / "current" / "codex").is_file()
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["router_will_activate"] else 1


def set_disabled(install_root: Path, disabled: bool) -> None:
    marker = install_root / "disabled"
    if disabled:
        install_root.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"disabled_at": utc_now(), "reason": "manual fail-safe"}) + "\n",
            encoding="utf-8",
        )
        print(f"Custom backend disabled; launcher will use the official backend: {marker}")
    elif marker.exists():
        marker.unlink()
        print("Custom backend enabled; version checks still apply")
    else:
        print("Custom backend was already enabled")


def uninstall_support(install_root: Path) -> None:
    """Unload support jobs without deleting releases, config, or credentials."""
    set_disabled(install_root, True)
    domain = f"gui/{os.getuid()}"
    agents = Path.home() / "Library" / "LaunchAgents"
    backups = install_root / "backups" / "launchagents"
    backups.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    for label in (ENVIRONMENT_LABEL, UPDATER_LABEL):
        launchctl_allow_failure(["bootout", f"{domain}/{label}"])
        path = agents / f"{label}.plist"
        if path.is_file():
            destination = backups / f"{path.stem}.disabled-{timestamp}{path.suffix}"
            shutil.move(os.fspath(path), os.fspath(destination))
            print(f"Support LaunchAgent moved to recoverable backup: {destination}")

    launcher = install_root / "bin" / "codex-router"
    current = active_cli_path()
    if current == os.fspath(launcher):
        run(["/bin/launchctl", "unsetenv", "CODEX_CLI_PATH"])
    elif current:
        print(f"CODEX_CLI_PATH belongs to another tool; leaving unchanged: {current}")
    print("Provider runtime support uninstalled; releases and credentials were preserved")


def verify_patch(source: Path, patch_asset: Path) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="codex-router-verify-") as temporary:
        fixture = Path(temporary)
        relative_files = (
            Path("codex-rs/app-server/src/request_processors.rs"),
            Path("codex-rs/app-server/src/request_processors/thread_processor.rs"),
        )
        for relative in relative_files:
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        state = patch_source(fixture, patch_asset)
        print(f"Patch verification succeeded: {state}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-root",
        type=Path,
        default=DEFAULT_INSTALL_ROOT,
        help=f"versioned install root (default: {DEFAULT_INSTALL_ROOT})",
    )
    parser.add_argument(
        "--official-codex",
        type=Path,
        default=DEFAULT_OFFICIAL_CODEX,
        help=f"bundled backend (default: {DEFAULT_OFFICIAL_CODEX})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    update = subparsers.add_parser("update")
    update.add_argument("--non-interactive", action="store_true")
    verify = subparsers.add_parser("verify-patch")
    verify.add_argument("--source", type=Path, required=True)
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--binary", type=Path, required=True)
    subparsers.add_parser("install-support")
    subparsers.add_parser("activate-support")
    subparsers.add_parser("disable")
    subparsers.add_parser("enable")
    subparsers.add_parser("uninstall-support")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project_root = Path(__file__).resolve().parent
    patch_asset = project_root / "patches" / "provider_route.rs"
    try:
        if args.command == "status":
            return status(args.install_root, args.official_codex)
        if args.command == "verify-patch":
            verify_patch(args.source.resolve(), patch_asset)
            return 0
        if args.command == "smoke":
            print(json.dumps(protocol_smoke(args.binary.resolve()), ensure_ascii=False, indent=2))
            return 0
        if args.command == "install-support":
            install_support(project_root, args.install_root, args.official_codex)
            return 0
        if args.command == "activate-support":
            activate_support(project_root, args.install_root, args.official_codex)
            return 0
        if args.command == "disable":
            set_disabled(args.install_root, True)
            return 0
        if args.command == "enable":
            set_disabled(args.install_root, False)
            return 0
        if args.command == "uninstall-support":
            uninstall_support(args.install_root)
            return 0
        if args.command == "update":
            lock = acquire_update_lock(args.install_root)
            if lock is None:
                print("Another router update is already running")
                return 0
            try:
                release = build_release(
                    args.install_root,
                    args.official_codex,
                    patch_asset,
                    non_interactive=args.non_interactive,
                )
                print(f"Activated router release: {release}")
                return 0
            finally:
                lock.close()
    except (RouterError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as error:
        print(f"router-manager: {error}", file=sys.stderr)
        return 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
