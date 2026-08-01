from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "bin" / "codex-provider"


class CliTests(unittest.TestCase):
    def test_update_suspends_and_refreshes_support_around_the_build(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        update_case = script.split("    update)\n", 1)[1].split("        ;;", 1)[0]
        install_index = update_case.index("manager_run install-support")
        update_index = update_case.index("manager_run update")
        activate_index = update_case.index("manager_run activate-support")
        self.assertLess(install_index, update_index)
        self.assertLess(update_index, activate_index)

    def test_verify_runs_protocol_smoke_even_when_config_validation_warns(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        verify_case = script.split("    verify)\n", 1)[1].split("        ;;", 1)[0]
        self.assertIn('if ! bash "$config_tool" validate', verify_case)
        self.assertIn('if ! manager_run smoke', verify_case)
        self.assertIn('exit "$verify_failed"', verify_case)

    def test_skill_install_updates_both_living_skills_with_backups(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        skill_case = script.split("    skill-install)\n", 1)[1].split("        ;;", 1)[0]
        self.assertIn("codex-model-coexist codex-provider-runtime", skill_case)
        self.assertIn("backups/skills", skill_case)
        self.assertIn('mv "$target_skill" "$backup_skill"', skill_case)

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
