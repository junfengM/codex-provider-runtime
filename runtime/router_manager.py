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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union


REPO_URL = "https://github.com/openai/codex.git"
DEFAULT_OFFICIAL_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
DEFAULT_INSTALL_ROOT = Path.home() / ".codex" / "provider-runtime"
VERSION_RE = re.compile(r"^codex-cli\s+(\S+)\s*$")
MODULE_MARKER = "mod provider_route;"
CALL_MARKER = "model_provider_for_new_thread(model.as_deref(), model_provider)"
HISTORY_CALL_MARKER = "model_provider_filter_for_thread_list(model_providers)"
RESUME_CALL_MARKER = "model_provider_for_resume("
ENVIRONMENT_LABEL = "com.codex.provider-runtime.environment"
UPDATER_LABEL = "com.codex.provider-runtime.updater"
RETIRED_GATEWAY_LABEL = "com.codex.provider-runtime.deepseek-gateway"
PATCH_NAME = "deepseek-flash-pro-route-resume-and-all-provider-history-v7"
LEGACY_SUPPORT_NAMES = {
    f"{RETIRED_GATEWAY_LABEL}.plist",
    "com.dudu.codex-deepseek-router-environment.plist",
    "com.dudu.codex-deepseek-router-updater.plist",
    "com.example.codex-provider-router.plist",
}


class RouterError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def update_fingerprint(official_codex: Path, patch_asset: Path) -> str:
    payload = "\n".join(
        (codex_version(official_codex), sha256(official_codex), sha256(patch_asset))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def failed_update_path(install_root: Path, fingerprint: str) -> Path:
    return install_root / "failed-updates" / f"{fingerprint}.json"


def record_failed_update(
    install_root: Path,
    fingerprint: str,
    official_codex: Path,
    error: BaseException,
) -> Path:
    destination = failed_update_path(install_root, fingerprint)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".json.next.{os.getpid()}")
    payload = {
        "schema": 1,
        "failed_at": utc_now(),
        "codex_version": codex_version(official_codex),
        "official_sha256": sha256(official_codex),
        "error": str(error)[:2000],
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, destination)
    return destination


def clear_failed_update(install_root: Path, fingerprint: str) -> None:
    marker = failed_update_path(install_root, fingerprint)
    if marker.exists():
        marker.unlink()


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


def replace_one_of(
    text: str,
    replacements: Sequence[tuple[str, str]],
    *,
    description: str,
) -> str:
    matches = [(old, new) for old, new in replacements if text.count(old) == 1]
    if len(matches) != 1:
        raise RouterError(
            f"Patch anchors for {description} matched {len(matches)} candidates; refusing to guess"
        )
    old, new = matches[0]
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
    fully_patched = (
        MODULE_MARKER in parent_text
        and CALL_MARKER in thread_text
        and HISTORY_CALL_MARKER in thread_text
        and RESUME_CALL_MARKER in thread_text
        and destination.is_file()
    )
    if fully_patched:
        if destination.read_bytes() == patch_asset.read_bytes():
            return "already-patched"
        shutil.copy2(patch_asset, destination)
        return "updated-patch-asset"

    legacy_router_patch = (
        MODULE_MARKER in parent_text
        and CALL_MARKER in thread_text
        and HISTORY_CALL_MARKER not in thread_text
        and destination.is_file()
    )
    existing_patch_needs_resume = (
        MODULE_MARKER in parent_text
        and CALL_MARKER in thread_text
        and HISTORY_CALL_MARKER in thread_text
        and RESUME_CALL_MARKER not in thread_text
        and destination.is_file()
    )
    fresh_source = (
        MODULE_MARKER not in parent_text
        and CALL_MARKER not in thread_text
        and HISTORY_CALL_MARKER not in thread_text
        and not destination.exists()
    )
    if not fresh_source and not legacy_router_patch and not existing_patch_needs_resume:
        raise RouterError("Source contains a partial provider patch; refusing mixed state")

    if fresh_source:
        parent_text = replace_one_of(
            parent_text,
            (
                (
                    "mod process_exec_processor;\nmod remote_control_processor;",
                    "mod process_exec_processor;\nmod provider_route;\nmod remote_control_processor;",
                ),
                (
                    "mod process_exec_processor;\nmod projects;",
                    "mod process_exec_processor;\nmod provider_route;\nmod projects;",
                ),
            ),
            description="request processor module declaration",
        )

        use_anchor = "use super::*;\n"
        use_replacement = (
            "use super::*;\n"
            "use super::provider_route::model_provider_filter_for_thread_list;\n"
            "use super::provider_route::model_provider_for_new_thread;\n"
            "use super::provider_route::model_provider_for_resume;\n"
        )
        thread_text = replace_once(
            thread_text,
            use_anchor,
            use_replacement,
            description="thread processor imports",
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
    elif legacy_router_patch:
        legacy_import = "use super::provider_route::model_provider_for_new_thread;\n"
        upgraded_import = (
            "use super::provider_route::model_provider_filter_for_thread_list;\n"
            "use super::provider_route::model_provider_for_new_thread;\n"
            "use super::provider_route::model_provider_for_resume;\n"
        )
        thread_text = replace_once(
            thread_text,
            legacy_import,
            upgraded_import,
            description="legacy provider patch import upgrade",
        )
    else:
        resume_import_anchor = (
            "use super::provider_route::model_provider_for_new_thread;\n"
        )
        resume_import_replacement = (
            resume_import_anchor
            + "use super::provider_route::model_provider_for_resume;\n"
        )
        thread_text = replace_once(
            thread_text,
            resume_import_anchor,
            resume_import_replacement,
            description="resume provider normalization import",
        )

    history_anchor = """        let model_provider_filter = match model_providers {
            Some(providers) => {
                if providers.is_empty() {
                    None
                } else {
                    Some(providers)
                }
            }
            None if relation_filter.is_some() => None,
            None => Some(vec![self.config.model_provider_id.clone()]),
        };
"""
    history_replacement = (
        "        let model_provider_filter =\n"
        "            model_provider_filter_for_thread_list(model_providers);\n"
    )
    if HISTORY_CALL_MARKER not in thread_text:
        thread_text = replace_once(
            thread_text,
            history_anchor,
            history_replacement,
            description="all-provider thread-list default",
        )

    resume_anchor = """        let persisted_metadata = self
            .load_and_apply_persisted_resume_metadata(
                &thread_history,
                &mut request_overrides,
                &mut typesafe_overrides,
            )
            .await;"""
    resume_replacement = resume_anchor + """
        typesafe_overrides.model_provider = model_provider_for_resume(
            typesafe_overrides.model.as_deref(),
            typesafe_overrides.model_provider.clone(),
        );"""
    if RESUME_CALL_MARKER not in thread_text:
        thread_text = replace_once(
            thread_text,
            resume_anchor,
            resume_replacement,
            description="resumed-thread provider normalization",
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
    # lock can still contain the development sentinel 0.0.0. Fetch the exact
    # locked dependencies first so a newly introduced registry or git source is
    # available on a clean machine. Cargo may normalize the workspace versions
    # during fetch; the final diff check below still rejects every external
    # dependency change before returning to --locked builds.
    run([cargo, "fetch"], cwd=cargo_root, env=env)
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


def read_json_message(process: subprocess.Popen, deadline: float) -> dict:
    assert process.stdout is not None
    while time.monotonic() < deadline:
        wait_for = max(0.0, min(0.5, deadline - time.monotonic()))
        ready, _, _ = select.select([process.stdout], [], [], wait_for)
        if not ready:
            if process.poll() is not None:
                raise RouterError("app-server exited while waiting for a notification")
            continue
        line = process.stdout.readline()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            raise RouterError(f"app-server emitted non-JSON stdout: {line[:200]!r}") from error
    raise RouterError("timed out waiting for app-server notification")


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
                    (2, "deepseek-flash", "deepseek"),
                    (3, "deepseek-v4-flash", "deepseek"),
                    (4, "deepseek-v4-pro", "deepseek"),
                    (5, "gpt-5.6-sol", "openai"),
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


def thread_list_visibility_smoke(binary: Path) -> dict:
    """Prove that the public omitted filter has the same semantics as []."""
    with tempfile.TemporaryDirectory(prefix="codex-thread-list-smoke-") as temporary:
        codex_home = Path(temporary)
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
                                "name": "codex-provider-thread-list-smoke",
                                "title": "Codex provider thread-list smoke",
                                "version": "1",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                )
                read_response(process, 1)
                send_json_line(process, {"method": "initialized"})

                request_id = 2
                results: dict[str, dict] = {}
                for attempt in range(3):
                    results = {}
                    for name, include_filter, provider_filter in (
                        ("omitted", False, None),
                        ("null", True, None),
                        ("empty", True, []),
                        ("deepseek", True, ["deepseek"]),
                    ):
                        params: dict[str, object] = {
                            "limit": 100,
                            "sortKey": "created_at",
                            "sortDirection": "desc",
                            "useStateDbOnly": True,
                        }
                        if include_filter:
                            params["modelProviders"] = provider_filter
                        send_json_line(
                            process,
                            {
                                "method": "thread/list",
                                "id": request_id,
                                "params": params,
                            },
                        )
                        result = read_response(process, request_id)
                        request_id += 1
                        data = result.get("data")
                        if not isinstance(data, list):
                            raise RouterError(f"thread/list {name} returned invalid data")
                        results[name] = result

                    omitted_ids = [item.get("id") for item in results["omitted"]["data"]]
                    null_ids = [item.get("id") for item in results["null"]["data"]]
                    empty_ids = [item.get("id") for item in results["empty"]["data"]]
                    cursors = {
                        results[name].get("nextCursor")
                        for name in ("omitted", "null", "empty")
                    }
                    if omitted_ids == null_ids == empty_ids and len(cursors) == 1:
                        break
                    if attempt == 2:
                        raise RouterError(
                            "thread/list omitted/null/empty modelProviders pages diverged "
                            "across three attempts: "
                            f"omitted={omitted_ids[:20]!r}, null={null_ids[:20]!r}, "
                            f"empty={empty_ids[:20]!r}, cursors={cursors!r}"
                        )

                deepseek_items = results["deepseek"]["data"]
                unexpected = [
                    item.get("modelProvider")
                    for item in deepseek_items
                    if item.get("modelProvider") != "deepseek"
                ]
                if unexpected:
                    raise RouterError(
                        "thread/list explicit DeepSeek filter returned other providers: "
                        f"{unexpected!r}"
                    )

                provider_counts: dict[str, int] = {}
                for item in results["empty"]["data"]:
                    provider = str(item.get("modelProvider") or "unknown")
                    provider_counts[provider] = provider_counts.get(provider, 0) + 1
                return {
                    "omitted_null_empty_match": True,
                    "all_provider_page_size": len(empty_ids),
                    "providers": provider_counts,
                    "deepseek_page_size": len(deepseek_items),
                }
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


def protocol_smoke_suite(binary: Path) -> dict:
    return {
        "new_thread_routing": protocol_smoke(binary),
        "resumed_thread_routing": resumed_thread_provider_smoke(binary),
        "thread_list_visibility": thread_list_visibility_smoke(binary),
    }


def resumed_thread_provider_smoke(binary: Path) -> dict:
    """Prove a cold app-server resume retains DeepSeek provider identity."""
    catalog = configured_model_catalog()
    with tempfile.TemporaryDirectory(prefix="codex-resume-router-smoke-") as temporary:
        codex_home = Path(temporary)
        config = (
            'model = "gpt-5.6-sol"\n'
            f"model_catalog_json = {json.dumps(os.fspath(catalog))}\n\n"
            "[model_providers.deepseek]\n"
            'name = "DeepSeek"\n'
            # Keep this smoke test offline.  The turn is submitted only to
            # force a durable rollout; the local closed port prevents any
            # provider request from reaching a real API.
            'base_url = "http://127.0.0.1:1"\n'
            'wire_api = "responses"\n'
        )
        (codex_home / "config.toml").write_text(config, encoding="utf-8")
        env = os.environ.copy()
        env["CODEX_HOME"] = os.fspath(codex_home)
        env["CODEX_CLI_PATH"] = os.fspath(binary)
        thread_id: Optional[str] = None

        def start_server(phase: str) -> subprocess.Popen[str]:
            stderr_path = codex_home / f"{phase}.stderr.log"
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
                                    "name": "codex-provider-runtime-resume-smoke",
                                    "title": "Codex provider runtime resume smoke",
                                    "version": "1",
                                },
                                "capabilities": {"experimentalApi": True},
                            },
                        },
                    )
                    read_response(process, 1)
                    send_json_line(process, {"method": "initialized"})
                    return process
                except Exception:
                    process.kill()
                    process.wait(timeout=5)
                    raise

        def stop_server(process: subprocess.Popen[str]) -> None:
            # Closing the JSON-RPC input lets app-server drain and persist its
            # thread state before exiting.  A hard signal can leave the
            # rollout uncommitted, making the next cold-resume assertion fail
            # for an unrelated reason.
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.kill()
                process.wait(timeout=10)

        first = start_server("start")
        try:
            send_json_line(
                first,
                {
                    "method": "thread/start",
                    "id": 2,
                    "params": {
                        "model": "deepseek-flash",
                        "cwd": "/private/tmp",
                        "approvalPolicy": "never",
                        "sandbox": "read-only",
                        "environments": [],
                    },
                },
            )
            started = read_response(first, 2)
            thread_id = started.get("thread", {}).get("id")
            if not thread_id:
                raise RouterError("resume smoke thread/start returned no thread id")
            if started.get("modelProvider") != "deepseek":
                raise RouterError(
                    "resume smoke thread/start routed DeepSeek to "
                    f"{started.get('modelProvider')!r}"
                )
            send_json_line(
                first,
                {
                    "method": "turn/start",
                    "id": 3,
                    "params": {
                        "threadId": thread_id,
                        "input": [
                            {
                                "type": "text",
                                "text": "resume routing persistence probe",
                            }
                        ],
                        "model": "deepseek-flash",
                    },
                },
            )
            read_response(first, 3)
        finally:
            stop_server(first)

        resumed = start_server("resume")
        try:
            send_json_line(
                resumed,
                {
                    "method": "thread/resume",
                    "id": 2,
                    "params": {
                        "threadId": thread_id,
                        "model": "deepseek-flash",
                        "excludeTurns": True,
                    },
                },
            )
            result = read_response(resumed, 2)
            provider = result.get("modelProvider")
            thread_provider = result.get("thread", {}).get("modelProvider")
            if provider != "deepseek" or thread_provider != "deepseek":
                raise RouterError(
                    "resume smoke routed DeepSeek incorrectly: "
                    f"response={provider!r}, thread={thread_provider!r}"
                )
            return {
                "model": "deepseek-flash",
                "model_provider": provider,
                "thread_model_provider": thread_provider,
                "cold_resume": True,
            }
        finally:
            stop_server(resumed)


def app_server_deepseek_tool_smoke(
    binary: Path, cwd: Path, model: str = "deepseek-flash"
) -> dict:
    """Exercise the same public app-server path used by a new Remote thread."""
    if not cwd.is_dir():
        raise RouterError(f"app-server smoke cwd is not a directory: {cwd}")
    if model not in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"}:
        raise RouterError(f"unsupported DeepSeek smoke model: {model}")
    with tempfile.TemporaryDirectory(prefix="codex-app-server-live-smoke-") as temporary:
        temporary_path = Path(temporary)
        challenge_path = temporary_path / "challenge.bin"
        challenge = os.urandom(32)
        challenge_path.write_bytes(challenge)
        expected_hash = hashlib.sha256(challenge).hexdigest()
        command = f"shasum -a 256 {challenge_path}"
        prompt = (
            "You must use the available shell execution tool to run exactly this command: "
            f"{command} After the tool result, reply with only the first whitespace-separated "
            "field from the command output. Do not guess or compute it yourself."
        )
        stderr_path = temporary_path / "stderr.log"
        env = os.environ.copy()
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
                                "name": "codex-provider-runtime-app-server-smoke",
                                "title": "Codex provider runtime app-server smoke",
                                "version": "1",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                )
                read_response(process, 1)
                send_json_line(process, {"method": "initialized"})
                send_json_line(
                    process,
                    {
                        "method": "thread/start",
                        "id": 2,
                        "params": {
                            "model": model,
                            "cwd": os.fspath(cwd),
                            "approvalPolicy": "never",
                            "sandbox": "read-only",
                            "ephemeral": True,
                        },
                    },
                )
                started = read_response(process, 2)
                thread_id = started.get("thread", {}).get("id")
                if not thread_id:
                    raise RouterError("app-server thread/start returned no thread id")
                if started.get("modelProvider") != "deepseek":
                    raise RouterError(
                        f"app-server routed DeepSeek model to {started.get('modelProvider')!r}"
                    )
                send_json_line(
                    process,
                    {
                        "method": "turn/start",
                        "id": 3,
                        "params": {
                            "threadId": thread_id,
                            "input": [{"type": "text", "text": prompt}],
                            "cwd": os.fspath(cwd),
                            "approvalPolicy": "never",
                        },
                    },
                )

                deadline = time.monotonic() + 180.0
                turn_accepted = False
                turn_terminal = False
                turn_status: Optional[str] = None
                turn_error: object = None
                command_completed = False
                command_output = ""
                structured_tool_completed = False
                structured_tool_type: Optional[str] = None
                agent_text = ""
                item_types: list[str] = []
                recent_events: list[dict[str, object]] = []
                while time.monotonic() < deadline and not turn_terminal:
                    try:
                        message = read_json_message(process, deadline)
                    except RouterError as error:
                        raise RouterError(
                            f"{error}; recent app-server events: "
                            + json.dumps(recent_events[-20:], ensure_ascii=False)
                        ) from error
                    if message.get("id") == 3:
                        if "error" in message:
                            raise RouterError(f"app-server turn/start failed: {message['error']}")
                        turn_accepted = True
                        continue
                    method = message.get("method")
                    params = message.get("params") if isinstance(message.get("params"), dict) else {}
                    item = params.get("item") if isinstance(params.get("item"), dict) else {}
                    item_type = item.get("type")
                    recent_events.append(
                        {
                            "method": method,
                            "item_type": item_type,
                            "item_status": item.get("status"),
                            "thread_status": params.get("status"),
                        }
                    )
                    if isinstance(item_type, str) and item_type not in item_types:
                        item_types.append(item_type)
                    if method == "item/completed" and item_type == "commandExecution":
                        command_completed = item.get("status") == "completed"
                        command_output = str(item.get("aggregatedOutput") or "")
                    if method == "item/completed" and item_type in {
                        "commandExecution",
                        "mcpToolCall",
                        "dynamicToolCall",
                    }:
                        serialized_item = json.dumps(item, ensure_ascii=False)
                        if item.get("status") == "completed" and expected_hash in serialized_item:
                            structured_tool_completed = True
                            structured_tool_type = str(item_type)
                    if method == "item/completed" and item_type == "agentMessage":
                        agent_text += str(item.get("text") or "")
                    if method == "turn/completed":
                        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                        turn_status = str(turn.get("status") or "unknown")
                        turn_error = turn.get("error")
                        turn_terminal = True
                    if method == "thread/status/changed":
                        status = params.get("status")
                        status_type = status.get("type") if isinstance(status, dict) else status
                        if status_type == "idle" and agent_text:
                            # Some ephemeral app-server sessions in this build
                            # transition to idle after final item/completed but
                            # omit the redundant turn/completed notification.
                            turn_status = "completed"
                            turn_terminal = True

                if not turn_accepted:
                    raise RouterError("app-server did not acknowledge turn/start")
                if not turn_terminal:
                    raise RouterError("app-server DeepSeek turn did not reach a terminal event")
                if turn_status != "completed":
                    raise RouterError(
                        f"app-server DeepSeek turn ended with status={turn_status!r}, "
                        f"error={turn_error!r}"
                    )
                if not command_completed or expected_hash not in command_output:
                    raise RouterError(
                        "app-server DeepSeek did not complete commandExecution with the hidden "
                        f"SHA-256 result; item_types={item_types!r}, "
                        f"recent_events={recent_events[-20:]!r}"
                    )
                if not structured_tool_completed or structured_tool_type != "commandExecution":
                    raise RouterError(
                        "app-server DeepSeek did not expose the hidden SHA-256 result through "
                        f"a structured commandExecution; item_types={item_types!r}"
                    )
                if expected_hash not in agent_text:
                    raise RouterError(
                        "app-server DeepSeek final agentMessage did not match the hidden SHA-256 "
                        f"execution challenge; item_types={item_types!r}, "
                        f"agent_text={agent_text[:1000]!r}, "
                        f"recent_events={recent_events[-20:]!r}"
                    )
                return {
                    "thread_id": thread_id,
                    "model": model,
                    "model_provider": started.get("modelProvider"),
                    "structured_tool": structured_tool_type,
                    "structured_command": command_completed,
                    "execution_proof": "hidden SHA-256 challenge matched",
                    "final_message": expected_hash,
                    "item_types": item_types,
                }
            except Exception as error:
                stderr.flush()
                stderr.seek(0)
                detail = stderr.read()[-8000:]
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


def prune_runtime(install_root: Path, *, keep_releases: int = 2) -> dict[str, int]:
    """Bound version-coupled state after a certified release is activated."""
    if keep_releases < 1:
        raise RouterError("keep_releases must be at least one")

    removed = {"releases": 0, "builds": 0, "cargo_targets": 0}
    releases_root = install_root / "releases"
    current = install_root / "current"
    active = current.resolve() if current.is_symlink() else None
    releases = (
        sorted(
            (
                path
                for path in releases_root.iterdir()
                if path.is_dir() and not path.name.startswith(".staging-")
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if releases_root.is_dir()
        else []
    )
    keep: set[Path] = set()
    if active is not None:
        keep.add(active)
    for release in releases:
        if len(keep) >= keep_releases:
            break
        keep.add(release.resolve())
    for release in releases:
        if release.resolve() not in keep:
            shutil.rmtree(release)
            removed["releases"] += 1

    builds_root = install_root / "builds"
    repo = install_root / "cache" / "codex"
    if builds_root.is_dir():
        for build in list(builds_root.iterdir()):
            if not build.is_dir():
                continue
            source = build / "source"
            if source.is_dir() and (repo / ".git").exists():
                subprocess.run(
                    [
                        "git",
                        "-C",
                        os.fspath(repo),
                        "worktree",
                        "remove",
                        "--force",
                        os.fspath(source),
                    ],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            if build.exists():
                shutil.rmtree(build)
            removed["builds"] += 1
        if (repo / ".git").exists():
            subprocess.run(
                ["git", "-C", os.fspath(repo), "worktree", "prune"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    cargo_target = install_root / "cache" / "cargo-target"
    if cargo_target.is_dir():
        shutil.rmtree(cargo_target)
        removed["cargo_targets"] = 1
    return removed


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
    smoke_name = "app-server routing and all-provider thread-list protocol smoke"
    if smoke_name not in manifest.get("tests", []):
        smoke_result = protocol_smoke_suite(custom)
        manifest.setdefault("tests", []).append(smoke_name)
        manifest["protocol_smoke"] = smoke_result
        manifest["certified_at"] = utc_now()
        temporary = release / f"manifest.json.next.{os.getpid()}"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, release / "manifest.json")
        print("Certified existing release protocol routing")


def bundled_code_mode_host(official_codex: Path) -> Path:
    host = official_codex.with_name("codex-code-mode-host")
    if not host.is_file() or not os.access(host, os.X_OK):
        raise RouterError(
            f"Bundled code-mode host is unavailable or not executable: {host}"
        )
    return host


def certified_release_candidates(
    install_root: Path,
) -> List[Tuple[Path, Dict[str, object]]]:
    releases = install_root / "releases"
    if not releases.is_dir():
        return []
    candidates: List[Tuple[Path, Dict[str, object]]] = []
    for release in sorted(releases.iterdir()):
        if not release.is_dir() or release.name.startswith(".staging-"):
            continue
        manifest_path = release / "manifest.json"
        binary = release / "codex"
        if not manifest_path.is_file() or not binary.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        candidates.append((release, manifest))
    return candidates


def find_certified_release(
    install_root: Path,
    version: str,
    patch_digest: str,
    *,
    official_digest: Optional[str] = None,
) -> Optional[Path]:
    """Return the newest cached release build certified for this version and patch.

    The custom binary is built from the public source tag plus the patch asset,
    so an identical source commit and patch digest describe an identical binary
    even when the bundled client digest moves. Candidates must still prove their
    recorded checksum and reported version, and an ambiguous source commit makes
    the caller fall back to a source build instead of guessing.
    """
    matches: Dict[str, Tuple[str, Path]] = {}
    for release, manifest in certified_release_candidates(install_root):
        if manifest.get("codex_version") != version:
            continue
        if manifest.get("patch_sha256") != patch_digest:
            continue
        if (
            official_digest is not None
            and manifest.get("official_sha256") != official_digest
        ):
            continue
        commit = manifest.get("source_commit")
        if not isinstance(commit, str) or not commit:
            continue
        binary = release / "codex"
        if sha256(binary) != manifest.get("custom_sha256"):
            continue
        try:
            if codex_version(binary) != version:
                continue
        except (RouterError, subprocess.CalledProcessError, OSError):
            continue
        built_at = str(manifest.get("built_at") or "")
        previous = matches.get(commit)
        if previous is None or built_at > previous[0]:
            matches[commit] = (built_at, release)
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))[1]


def stage_release(
    install_root: Path,
    release_name: str,
    custom_binary: Path,
    host: Path,
    manifest_fields: Dict[str, object],
) -> Path:
    staging = install_root / "releases" / f".staging-{release_name}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copy2(custom_binary, staging / "codex")
        shutil.copy2(host, staging / "codex-code-mode-host")
        for binary in (staging / "codex", staging / "codex-code-mode-host"):
            binary.chmod(0o755)
        manifest: Dict[str, object] = {
            "schema": 1,
            "custom_sha256": sha256(staging / "codex"),
            "code_mode_host_sha256": sha256(staging / "codex-code-mode-host"),
            "code_mode_host_source": "bundled-with-desktop",
            "built_at": utc_now(),
        }
        manifest.update(manifest_fields)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        release = install_root / "releases" / release_name
        if release.exists():
            raise RouterError(f"Refusing to overwrite an existing release: {release}")
        os.replace(staging, release)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return release


def reuse_release(
    install_root: Path,
    official_codex: Path,
    patch_asset: Path,
    cached_release: Path,
    version: str,
    official_digest: str,
    patch_digest: str,
) -> Optional[Path]:
    """Re-certify and activate a cached binary when only the client digest moved."""
    manifest_path = cached_release / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not commit:
        return None
    binary = cached_release / "codex"
    try:
        host = bundled_code_mode_host(official_codex)
        run([host, "--help"], capture=True)
        smoke_result = protocol_smoke_suite(binary)
    except (RouterError, subprocess.CalledProcessError, OSError) as error:
        print(
            f"Cached release {cached_release.name} failed reuse checks; "
            f"rebuilding from source: {error}",
            file=sys.stderr,
        )
        return None
    print(
        "Reusing certified custom binary:",
        json.dumps(
            {
                "cached_release": cached_release.name,
                "source_commit": commit,
                "patch": os.fspath(patch_asset),
                "patch_sha256": patch_digest,
                "protocol_smoke": smoke_result,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    release_name = (
        f"{version}-{commit[:12]}-{official_digest[:12]}-{patch_digest[:12]}"
    )
    return stage_release(
        install_root,
        release_name,
        binary,
        host,
        {
            "codex_version": version,
            "source_tag": manifest.get("source_tag") or f"rust-v{version}",
            "source_commit": commit,
            "official_binary": os.fspath(official_codex),
            "official_sha256": official_digest,
            "patch_sha256": patch_digest,
            "patch": PATCH_NAME,
            "workspace_lock_versions_normalized": manifest.get(
                "workspace_lock_versions_normalized"
            ),
            "protocol_smoke": smoke_result,
            "reused_from": cached_release.name,
            "tests": [
                "reused certified custom binary for an unchanged source commit and patch",
                "binary version smoke",
                "bundled code-mode-host help and checksum",
                "app-server new/resumed routing and all-provider thread-list protocol smoke",
            ],
        },
    )


def build_release(
    install_root: Path,
    official_codex: Path,
    patch_asset: Path,
    *,
    non_interactive: bool,
    allow_reuse: bool = True,
) -> Path:
    del non_interactive  # Reserved for future notification policy; builds are always deterministic.
    version = codex_version(official_codex)
    official_digest = sha256(official_codex)
    patch_digest = sha256(patch_asset)

    existing = find_certified_release(
        install_root, version, patch_digest, official_digest=official_digest
    )
    if existing is not None:
        manifest = json.loads(
            (existing / "manifest.json").read_text(encoding="utf-8")
        )
        verify_existing_release(existing, manifest, version)
        activate_release(install_root, existing.name)
        return existing

    if allow_reuse:
        reusable = find_certified_release(install_root, version, patch_digest)
        if reusable is not None:
            reused = reuse_release(
                install_root,
                official_codex,
                patch_asset,
                reusable,
                version,
                official_digest,
                patch_digest,
            )
            if reused is not None:
                activate_release(install_root, reused.name)
                return reused

    source, tag, commit = ensure_source_checkout(install_root, version)
    release_name = (
        f"{version}-{commit[:12]}-{official_digest[:12]}-{patch_digest[:12]}"
    )
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
            "patch_sha256": patch_digest,
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
    # The Codex release graph is large enough for Cargo's default parallelism
    # and thin-LTO link to exhaust memory on desktop Macs. Prefer a predictable,
    # low-memory build; protocol and binary-version smokes remain authoritative.
    build_env["CARGO_BUILD_JOBS"] = "1"
    build_env["CARGO_PROFILE_RELEASE_LTO"] = "false"
    build_env["CARGO_PROFILE_RELEASE_CODEGEN_UNITS"] = "1"
    build_env["CARGO_PROFILE_RELEASE_DEBUG"] = "none"
    build_env["CARGO_PROFILE_RELEASE_STRIP"] = "symbols"
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
        ],
        cwd=cargo_root,
        env=build_env,
    )

    built_codex = cargo_target / "release" / "codex"
    built_host = bundled_code_mode_host(official_codex)
    if codex_version(built_codex) != version:
        raise RouterError("Built Codex version does not match the bundled client version")
    run([built_host, "--help"], capture=True)
    smoke_result = protocol_smoke_suite(built_codex)
    print("Protocol smoke:", json.dumps(smoke_result, ensure_ascii=False, sort_keys=True))
    sign_if_available(built_codex)

    release = stage_release(
        install_root,
        release_name,
        built_codex,
        built_host,
        {
            "codex_version": version,
            "source_tag": tag,
            "source_commit": commit,
            "official_binary": os.fspath(official_codex),
            "official_sha256": official_digest,
            "patch_sha256": patch_digest,
            "patch": PATCH_NAME,
            "workspace_lock_versions_normalized": lock_versions_normalized,
            "protocol_smoke": smoke_result,
            "tests": [
                "provider_route unit tests",
                "binary version smoke",
                "bundled code-mode-host help and checksum",
                "app-server new/resumed routing and all-provider thread-list protocol smoke",
            ],
        },
    )
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
    # Stop the scheduled updater before replacing its manager and patch asset.
    # Otherwise a process using the previous asset can reactivate an older
    # release between a successful manual build and support activation.
    domain = f"gui/{os.getuid()}"
    launchctl_allow_failure(["bootout", f"{domain}/{UPDATER_LABEL}"])

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
    # Refresh support first so any subsequent scheduled run uses the same patch
    # asset and manager that certified the active release.
    install_support(project_root, install_root, official_codex)
    manifest = read_manifest(install_root)
    if not manifest:
        raise RouterError("No built router release is available; run update first")
    version = codex_version(official_codex)
    if manifest.get("codex_version") != version:
        raise RouterError("Custom release does not match the current bundled Codex version")
    verify_existing_release(install_root / "current", manifest, version)
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

    for label in (RETIRED_GATEWAY_LABEL, ENVIRONMENT_LABEL, UPDATER_LABEL):
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
            Path("codex-rs/app-server/src/request_processors/provider_route.rs"),
        )
        for relative in relative_files:
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_file = source / relative
            if source_file.is_file():
                shutil.copy2(source_file, destination)
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
    update.add_argument(
        "--no-reuse",
        action="store_true",
        help="always rebuild from source instead of reusing a certified binary",
    )
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--keep-releases", type=int, default=2)
    verify = subparsers.add_parser("verify-patch")
    verify.add_argument("--source", type=Path, required=True)
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--binary", type=Path, required=True)
    live_smoke = subparsers.add_parser("app-server-live-smoke")
    live_smoke.add_argument("--binary", type=Path, required=True)
    live_smoke.add_argument("--cwd", type=Path, default=Path.cwd())
    live_smoke.add_argument(
        "--model",
        choices=("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"),
        default="deepseek-flash",
    )
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
        if args.command == "cleanup":
            print(
                json.dumps(
                    prune_runtime(args.install_root, keep_releases=args.keep_releases),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "verify-patch":
            verify_patch(args.source.resolve(), patch_asset)
            return 0
        if args.command == "smoke":
            print(
                json.dumps(
                    protocol_smoke_suite(args.binary.resolve()),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "app-server-live-smoke":
            print(
                json.dumps(
                    app_server_deepseek_tool_smoke(
                        args.binary.resolve(), args.cwd.resolve(), args.model
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
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
                fingerprint = update_fingerprint(args.official_codex, patch_asset)
                previous_failure = failed_update_path(args.install_root, fingerprint)
                if args.non_interactive and previous_failure.is_file():
                    print(
                        "Skipping unchanged failed update; retry when the Codex binary or "
                        f"provider patch changes: {previous_failure}"
                    )
                    return 0
                try:
                    release = build_release(
                        args.install_root,
                        args.official_codex,
                        patch_asset,
                        non_interactive=args.non_interactive,
                        allow_reuse=not args.no_reuse,
                    )
                except (
                    RouterError,
                    subprocess.CalledProcessError,
                    OSError,
                    json.JSONDecodeError,
                ) as error:
                    if args.non_interactive:
                        marker = record_failed_update(
                            args.install_root,
                            fingerprint,
                            args.official_codex,
                            error,
                        )
                        print(
                            f"Recorded unchanged-version backoff: {marker}",
                            file=sys.stderr,
                        )
                    raise
                clear_failed_update(args.install_root, fingerprint)
                removed = prune_runtime(args.install_root)
                print(f"Activated router release: {release}")
                print(
                    "Pruned version-coupled state: "
                    f"{json.dumps(removed, sort_keys=True)}"
                )
                return 0
            finally:
                lock.close()
    except (RouterError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as error:
        print(f"router-manager: {error}", file=sys.stderr)
        return 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
