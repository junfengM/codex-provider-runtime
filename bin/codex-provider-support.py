#!/usr/bin/env python3
"""Run manager support hooks while preserving the installed GitHub CLI path."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional


GH_DIRECTORY_NAME = "gh-bin-dir"


def validated_gh_directory(value: str) -> Optional[Path]:
    """Return a safe absolute directory containing an executable gh, if valid."""
    if not value or any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in value
    ):
        return None
    if ":" in value:
        return None
    directory = Path(value)
    if not directory.is_absolute():
        return None
    candidate = directory / "gh"
    if not directory.is_dir() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return directory


def discover_gh_directory() -> tuple[bool, Optional[Path]]:
    discovered = shutil.which("gh")
    if not discovered:
        return False, None
    executable = Path(os.path.abspath(discovered))
    directory = validated_gh_directory(os.fspath(executable.parent))
    if directory is None:
        raise ValueError(
            f"discovered gh path is not a safe executable directory: {executable}"
        )
    return True, directory


def atomic_write_text(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=os.fspath(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def updater_plist_path(manager: object) -> Path:
    agents = Path.home() / "Library" / "LaunchAgents"
    return agents / f"{manager.UPDATER_LABEL}.plist"


def amend_updater_path(path: Path, gh_directory: Path) -> None:
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    environment = payload.setdefault("EnvironmentVariables", {})
    current_path = environment.get("PATH", "")
    entries = [os.fspath(gh_directory)]
    entries.extend(entry for entry in current_path.split(os.pathsep) if entry)
    deduplicated = list(dict.fromkeys(entries))
    environment["PATH"] = os.pathsep.join(deduplicated)

    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=os.fspath(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            plistlib.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_manager(manager_path: Path) -> object:
    resolved = manager_path.resolve()
    sys.path.insert(0, os.fspath(resolved.parent))
    spec = importlib.util.spec_from_file_location(
        "codex_provider_router_manager", resolved
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load router manager: {resolved}")
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    return manager


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            "usage: codex-provider-support.py MANAGER INSTALL_ROOT OFFICIAL_CODEX COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 2

    manager_path = Path(argv[0])
    install_root = Path(argv[1])
    official_codex = Path(argv[2])
    manager_arguments = [
        "--install-root",
        os.fspath(install_root),
        "--official-codex",
        os.fspath(official_codex),
        *argv[3:],
    ]
    manager = load_manager(manager_path)
    original_install_support = manager.install_support

    def install_support_with_gh_path(*arguments: object, **keywords: object) -> None:
        # Reject an unsafe discovered path before the manager unloads or replaces
        # any support files. If gh is absent, retain a previously verified path.
        discovered, gh_directory = discover_gh_directory()
        if not discovered:
            saved_path = install_root / "bin" / GH_DIRECTORY_NAME
            try:
                previous_value = saved_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                previous_value = ""
            if previous_value.endswith("\n"):
                previous_value = previous_value[:-1]
            gh_directory = validated_gh_directory(previous_value)

        original_install_support(*arguments, **keywords)
        if gh_directory is None:
            return

        saved_path = install_root / "bin" / GH_DIRECTORY_NAME
        updater_path = updater_plist_path(manager)
        amend_updater_path(updater_path, gh_directory)
        atomic_write_text(saved_path, os.fspath(gh_directory) + "\n")

    manager.install_support = install_support_with_gh_path
    return int(manager.main(manager_arguments))


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, plistlib.InvalidFileException, RuntimeError) as error:
        print(f"codex-provider-support: {error}", file=sys.stderr)
        raise SystemExit(2)
