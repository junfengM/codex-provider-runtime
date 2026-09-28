from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_HELPER = PROJECT_ROOT / "bin" / "codex-provider-support.py"


FAKE_MANAGER = """\
import os
import plistlib
import sys
from pathlib import Path

UPDATER_LABEL = "com.codex.provider-runtime.updater"

def install_support(project_root, install_root, official_codex, distribution=None):
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    updater = agents / (UPDATER_LABEL + ".plist")
    with updater.open("wb") as handle:
        plistlib.dump({"EnvironmentVariables": {"PATH": "/usr/bin:/bin"}}, handle)

def activate_support(project_root, install_root, official_codex, distribution=None):
    install_support(project_root, install_root, official_codex, distribution)
    updater = Path.home() / "Library" / "LaunchAgents" / (UPDATER_LABEL + ".plist")
    with updater.open("rb") as handle:
        payload = plistlib.load(handle)
    captured = os.environ["ACTIVATION_CAPTURE"]
    Path(captured).write_text(payload["EnvironmentVariables"]["PATH"], encoding="utf-8")

def main(argv=None):
    arguments = list(argv if argv is not None else sys.argv[1:])
    install_root = Path(arguments[arguments.index("--install-root") + 1])
    official_codex = Path(arguments[arguments.index("--official-codex") + 1])
    command = next(value for value in arguments if value in {"install-support", "activate-support"})
    hook = install_support if command == "install-support" else activate_support
    hook(Path(__file__).resolve().parent, install_root, official_codex)
    return 0
"""


class SupportPathTests(unittest.TestCase):
    def invoke(
        self,
        home: Path,
        manager: Path,
        install_root: Path,
        official: Path,
        *,
        path: str,
        capture: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "PATH": path,
                "ACTIVATION_CAPTURE": str(capture),
            }
        )
        return subprocess.run(
            [
                "/usr/bin/python3",
                str(SUPPORT_HELPER),
                str(manager),
                str(install_root),
                str(official),
                "activate-support",
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )

    def test_activation_patches_updater_before_bootstrap_and_reuses_saved_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-provider-support-path-") as temporary:
            root = Path(temporary)
            home = root / "home"
            runtime = root / "runtime"
            manager = runtime / "router_manager.py"
            manager.parent.mkdir(parents=True)
            manager.write_text(FAKE_MANAGER, encoding="utf-8")
            gh_directory = root / "nonstandard-homebrew" / "bin"
            gh_directory.mkdir(parents=True)
            gh = gh_directory / "gh"
            gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gh.chmod(0o755)
            official = root / "codex"
            capture = root / "activation-path.txt"

            first = self.invoke(
                home,
                manager,
                runtime,
                official,
                path=f"{gh_directory}:/usr/bin:/bin",
                capture=capture,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            saved_path = runtime / "bin" / "gh-bin-dir"
            self.assertEqual(saved_path.read_text(encoding="utf-8"), f"{gh_directory}\n")
            first_entries = capture.read_text(encoding="utf-8").split(os.pathsep)
            self.assertEqual(first_entries[0], str(gh_directory))
            self.assertEqual(first_entries.count(str(gh_directory)), 1)

            # With no gh on PATH, a newly generated updater plist still receives
            # the prior validated directory before activation can bootstrap it.
            second_capture = root / "activation-path-second.txt"
            second = self.invoke(
                home,
                manager,
                runtime,
                official,
                path="/usr/bin:/bin",
                capture=second_capture,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(saved_path.read_text(encoding="utf-8"), f"{gh_directory}\n")
            second_entries = second_capture.read_text(encoding="utf-8").split(os.pathsep)
            self.assertEqual(second_entries[0], str(gh_directory))
            self.assertEqual(second_entries.count(str(gh_directory)), 1)
            self.assertFalse(list((runtime / "bin").glob("*.tmp")))

    def test_discovered_control_character_path_is_rejected_before_support_hook(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-provider-invalid-gh-path-") as temporary:
            root = Path(temporary)
            home = root / "home"
            runtime = root / "runtime"
            manager = runtime / "router_manager.py"
            manager.parent.mkdir(parents=True)
            manager.write_text(FAKE_MANAGER, encoding="utf-8")
            invalid_directory = root / "invalid\ngh" / "bin"
            invalid_directory.mkdir(parents=True)
            gh = invalid_directory / "gh"
            gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gh.chmod(0o755)
            official = root / "codex"
            capture = root / "activation-path.txt"

            result = self.invoke(
                home,
                manager,
                runtime,
                official,
                path=f"{invalid_directory}:/usr/bin:/bin",
                capture=capture,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("safe executable directory", result.stderr)
            self.assertFalse((home / "Library" / "LaunchAgents").exists())
            self.assertFalse((runtime / "bin" / "gh-bin-dir").exists())


if __name__ == "__main__":
    unittest.main()
