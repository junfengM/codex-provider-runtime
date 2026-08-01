from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "bin" / "codex-provider"


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = "/private/tmp/codex-provider-test-home"
        return subprocess.run(
            [str(CLI), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_help_lists_lifecycle_commands(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "install",
            "doctor",
            "update",
            "disable",
            "uninstall",
            "test-deepseek",
            "appserver-smoke",
        ):
            self.assertIn(command, result.stdout)

    def test_unknown_command_fails_without_mutation(self) -> None:
        result = self.run_cli("definitely-not-a-command")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown command", result.stderr)

    def test_invalid_log_line_count_is_rejected(self) -> None:
        result = self.run_cli("logs", "not-a-number")
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
