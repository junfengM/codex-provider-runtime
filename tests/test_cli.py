from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "bin" / "codex-provider"


class CliTests(unittest.TestCase):
    def test_update_suspends_and_refreshes_support_around_the_build(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        update_case = script.split("    update)\n", 1)[1].split("\n    cleanup)\n", 1)[0]
        install_index = update_case.index("manager_support_run install-support")
        update_index = update_case.index("manager_run update")
        activate_index = update_case.index("manager_support_run activate-support")
        self.assertLess(install_index, update_index)
        self.assertLess(update_index, activate_index)

    def test_failed_update_restores_support_agents_and_preserves_distribution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-provider-cli-restore-test-") as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "bin").mkdir(parents=True)
            (project / "runtime").mkdir()
            (project / "config").mkdir()
            cli = project / "bin" / "codex-provider"
            cli.write_bytes(CLI.read_bytes())
            cli.chmod(0o755)
            support_helper = project / "bin" / "codex-provider-support.py"
            support_helper.write_bytes((PROJECT_ROOT / "bin" / "codex-provider-support.py").read_bytes())
            manager = project / "runtime" / "router_manager.py"
            manager.write_text(
                "import json, os, sys\n"
                "def install_support(*args, **kwargs):\n"
                "    return None\n"
                "def activate_support(*args, **kwargs):\n"
                "    return install_support(*args, **kwargs)\n"
                "def main(argv=None):\n"
                "    args = list(argv if argv is not None else sys.argv[1:])\n"
                "    with open(os.environ['MANAGER_CALL_LOG'], 'a', encoding='utf-8') as out:\n"
                "        out.write(json.dumps(args) + '\\n')\n"
                "    command = next((arg for arg in args if arg in {'install-support', 'update', 'smoke', 'activate-support'}), '')\n"
                "    if command == 'install-support': install_support()\n"
                "    if command == 'activate-support': activate_support()\n"
                "    return int(os.environ.get('MANAGER_UPDATE_EXIT', '0')) if command == 'update' else 0\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(main())\n",
                encoding="utf-8",
            )
            coexist = project / "config" / "coexist.sh"
            coexist.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            coexist.chmod(0o755)
            official = root / "codex"
            official.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            official.chmod(0o755)
            call_log = root / "manager-calls.jsonl"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root / "home"),
                    "PATH": "/usr/bin:/bin",
                    "CODEX_PROVIDER_RUNTIME_ROOT": str(root / "install"),
                    "CODEX_OFFICIAL_CLI_PATH": str(official),
                    "MANAGER_CALL_LOG": str(call_log),
                    "MANAGER_UPDATE_EXIT": "2",
                }
            )
            result = subprocess.run(
                [str(cli), "update", "--distribution", "prebuilt-only"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(call_log.exists(), result.stdout + result.stderr)
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            commands = [next(arg for arg in call if arg in {"install-support", "update", "activate-support"}) for call in calls]
            self.assertEqual(commands, ["install-support", "update", "activate-support"])
            for call in calls:
                if any(arg in call for arg in ("install-support", "update", "activate-support")):
                    self.assertIn("--distribution", call)
                    self.assertIn("prebuilt-only", call)
            self.assertFalse(any("smoke" in call for call in calls))

    def test_verify_runs_protocol_smoke_even_when_config_validation_warns(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        verify_case = script.split("    verify)\n", 1)[1].split("        ;;", 1)[0]
        self.assertIn('if ! bash "$config_tool" validate', verify_case)
        self.assertIn('if ! manager_run smoke', verify_case)
        self.assertIn('exit "$verify_failed"', verify_case)

    def test_sync_models_is_dispatched_and_chained_after_update(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        sync_case = script.split("    sync-models)\n", 1)[1].split("        ;;", 1)[0]
        self.assertIn('bash "$config_tool" sync-models', sync_case)
        update_case = script.split("    update)\n", 1)[1].split("\n    cleanup)\n", 1)[0]
        self.assertIn('bash "$config_tool" sync-models', update_case)
        self.assertIn("Re-run: codex-provider sync-models", update_case)

    def test_official_codex_resolution_honors_explicit_override(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        head = script.split("manager_run()", 1)[0]
        self.assertIn('official_codex="${CODEX_OFFICIAL_CLI_PATH:-}"', head)
        self.assertIn("resolve-official-codex", head)
        override_index = head.index("official_codex=\"${CODEX_OFFICIAL_CLI_PATH:-}\"")
        detect_index = head.index("resolve-official-codex")
        self.assertLess(override_index, detect_index)

    def test_skill_install_updates_both_living_skills_with_backups(self) -> None:
        script = CLI.read_text(encoding="utf-8")
        skill_case = script.split("    skill-install)\n", 1)[1].split("        ;;", 1)[0]
        self.assertIn("codex-model-coexist codex-provider-runtime", skill_case)
        self.assertIn("backups/skills", skill_case)
        self.assertIn('mv "$target_skill" "$backup_skill"', skill_case)
        self.assertIn("CODEX_SHARED_SKILLS_ROOT", skill_case)
        self.assertIn('mv "$shared_skill" "$shared_backup"', skill_case)

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
            "sync-models",
            "cleanup",
            "disable",
            "uninstall",
            "test-deepseek",
            "keychain-status",
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
