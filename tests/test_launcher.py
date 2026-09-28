from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "runtime" / "templates" / "codex-router"


class LauncherEnvironmentTests(unittest.TestCase):
    def test_background_manager_gets_tool_path_but_official_codex_keeps_gui_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="codex-launcher-path-test-"
        ) as temporary:
            root = Path(temporary)
            home = root / "home"
            runtime = root / "runtime"
            manager = runtime / "lib" / "router_manager.py"
            current = runtime / "current"
            manager_path_capture = root / "manager-path.txt"
            official_path_capture = root / "official-path.txt"
            official = root / "codex"
            custom_gh_bin = root / "homebrew-tools" / "bin"
            (home / ".cargo" / "bin").mkdir(parents=True)
            manager.parent.mkdir(parents=True)
            (runtime / "bin").mkdir(parents=True)
            current.mkdir(parents=True)
            custom_gh_bin.mkdir(parents=True)
            gh = custom_gh_bin / "gh"
            gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gh.chmod(0o755)
            (runtime / "bin" / "gh-bin-dir").write_text(
                f"{custom_gh_bin}\n", encoding="utf-8"
            )
            (current / "manifest.json").write_text(
                '{"codex_version":"0.1.0"}\n', encoding="utf-8"
            )
            manager.write_text(
                "import os\n"
                "from pathlib import Path\n"
                "destination = Path(os.environ['MANAGER_PATH_CAPTURE'])\n"
                "temporary = destination.with_name("
                "destination.name + f'.tmp.{os.getpid()}')\n"
                "temporary.write_text(os.environ['PATH'], encoding='utf-8')\n"
                "temporary.replace(destination)\n",
                encoding="utf-8",
            )
            official.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = --version ]; then\n"
                "    printf 'codex 9.9.9\\n'\n"
                "else\n"
                "    printf '%s' \"$PATH\" > \"$OFFICIAL_PATH_CAPTURE\"\n"
                "fi\n",
                encoding="utf-8",
            )
            official.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin",
                    "CODEX_PROVIDER_RUNTIME_ROOT": str(runtime),
                    "CODEX_OFFICIAL_CLI_PATH": str(official),
                    "MANAGER_PATH_CAPTURE": str(manager_path_capture),
                    "OFFICIAL_PATH_CAPTURE": str(official_path_capture),
                }
            )
            result = subprocess.run(
                ["/bin/sh", str(LAUNCHER), "prompt"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            deadline = time.monotonic() + 2
            while not manager_path_capture.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(
                manager_path_capture.is_file(), "background manager did not run"
            )
            manager_path = manager_path_capture.read_text(
                encoding="utf-8"
            ).split(os.pathsep)
            self.assertEqual(
                manager_path,
                [
                    str(custom_gh_bin),
                    str(home / ".cargo" / "bin"),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                ],
            )
            self.assertEqual(
                official_path_capture.read_text(encoding="utf-8"), "/usr/bin:/bin"
            )


if __name__ == "__main__":
    unittest.main()
