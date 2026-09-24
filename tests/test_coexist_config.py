from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_TOOL = PROJECT_ROOT / "config" / "coexist.sh"

# 冒充 `codex debug models`：固定目录时返回固定目录，未固定时返回"服务端"清单并写缓存。
FAKE_CODEX = """#!/bin/sh
set -eu
if [ "${1:-}" != "debug" ] || [ "${2:-}" != "models" ]; then
  printf 'unsupported args: %s\\n' "$*" >&2
  exit 2
fi
home="${CODEX_HOME:-$HOME/.codex}"
catalog="$(awk -F'"' '/^model_catalog_json[[:space:]]*=/ { print $2; exit }' "$home/config.toml" 2>/dev/null || true)"
if [ -n "$catalog" ] && [ -f "$catalog" ]; then
  cat "$catalog"
  exit 0
fi
if [ "${FAKE_CODEX_FAIL:-0}" = "1" ]; then
  printf 'fake codex is offline\\n' >&2
  exit 1
fi
cp "$FAKE_CODEX_LIVE" "$home/models_cache.json"
cat "$FAKE_CODEX_LIVE"
"""


def model(slug: str) -> dict:
    return {
        "slug": slug,
        "display_name": slug,
        "visibility": "list",
        "base_instructions": "test instructions",
        "model_messages": {"instructions_template": "test instructions"},
        "support_verbosity": False,
        "use_responses_lite": True,
        "tool_mode": "code_mode_only",
    }


class ModelSyncTests(unittest.TestCase):
    """`sync-models` 必须能补上新发布的模型，同时不改变用户的默认模型。"""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="codex-sync-test-")
        self.codex_home = Path(self._temporary.name)
        self.live = {"models": [model("gpt-6-astra"), model("gpt-6-sol"), model("gpt-5.6-sol")]}
        self.live_file = self.codex_home / "live.json"
        self.live_file.write_text(json.dumps(self.live), encoding="utf-8")

        stale_flash = model("deepseek-flash")
        stale_flash.update({"context_window": 1000000, "auto_review_model_override": None})
        # 官方缓存里已有新模型，但固定目录还停在旧快照上。
        (self.codex_home / "models_cache.json").write_text(
            json.dumps({"models": [model("gpt-6-astra")]}), encoding="utf-8"
        )
        (self.codex_home / "models.json").write_text(
            json.dumps({"models": [stale_flash]}), encoding="utf-8"
        )
        (self.codex_home / "models-coexist.json").write_text(
            json.dumps({"models": [model("gpt-5.6-sol"), stale_flash]}), encoding="utf-8"
        )
        self.catalog = self.codex_home / "models-coexist.json"
        self.config = self.codex_home / "config.toml"
        self.config.write_text(
            'model = "deepseek-flash"\n'
            'model_reasoning_effort = "high"\n'
            f'model_catalog_json = "{self.catalog}"\n'
            "\n[model_providers.deepseek]\n"
            'name = "DeepSeek"\n'
            'base_url = "https://api.deepseek.com"\n'
            'wire_api = "responses"\n'
            'env_key = "DEEPSEEK_API_KEY"\n',
            encoding="utf-8",
        )
        self.stub = self.codex_home / "fake-codex"
        self.stub.write_text(FAKE_CODEX, encoding="utf-8")
        self.stub.chmod(0o755)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def run_tool(self, *args: str, fail: bool = False) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["COEXIST_CODEX_HOME"] = str(self.codex_home)
        env["CODEX_HOME"] = str(self.codex_home)
        env["CODEX_CLI_PATH"] = str(self.stub)
        env["FAKE_CODEX_LIVE"] = str(self.live_file)
        env["DEEPSEEK_API_KEY"] = "test-only"
        if fail:
            env["FAKE_CODEX_FAIL"] = "1"
        return subprocess.run(
            ["bash", str(CONFIG_TOOL), *args],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def catalog_slugs(self) -> list:
        return [entry["slug"] for entry in json.loads(self.catalog.read_text())["models"]]

    def test_sync_models_adds_new_models_without_touching_default_model(self) -> None:
        result = self.run_tool("sync-models")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        slugs = self.catalog_slugs()
        self.assertIn("gpt-6-astra", slugs)
        self.assertIn("gpt-6-sol", slugs)
        self.assertIn("gpt-5.6-sol", slugs)
        self.assertIn("deepseek-flash", slugs)
        flash = next(
            entry for entry in json.loads(self.catalog.read_text())["models"]
            if entry["slug"] == "deepseek-flash"
        )
        self.assertEqual(flash["context_window"], 1048576)
        self.assertEqual(flash["shell_type"], "shell_command")

        config = self.config.read_text()
        self.assertIn('model = "deepseek-flash"', config)
        self.assertIn('model_reasoning_effort = "high"', config)
        self.assertIn(f'model_catalog_json = "{self.catalog}"', config)

        self.assertIn("新增模型", result.stdout)
        self.assertIn("gpt-6-astra", result.stdout)
        self.assertFalse((self.codex_home / ".sync-models.lock").exists())

    def test_sync_models_rolls_back_config_when_refresh_fails(self) -> None:
        before = self.config.read_text()
        result = self.run_tool("sync-models", fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), before)
        self.assertIn("回滚", result.stderr)
        self.assertFalse((self.codex_home / ".sync-models.lock").exists())

    def test_sync_models_check_reports_drift_until_synced(self) -> None:
        drifted = self.run_tool("sync-models", "--check")
        self.assertEqual(drifted.returncode, 1, drifted.stdout + drifted.stderr)
        self.assertIn("gpt-6-astra", drifted.stdout)
        self.assertIn("缺少 1 个模型", drifted.stdout)

        self.assertEqual(self.run_tool("sync-models").returncode, 0)

        current = self.run_tool("sync-models", "--check")
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertIn("已包含官方缓存中的全部模型", current.stdout)


class DeepSeekCatalogContractTests(unittest.TestCase):
    def test_refresh_reconciles_stale_catalog_to_official_codex_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-coexist-test-") as temporary:
            codex_home = Path(temporary)
            (codex_home / "config.toml").write_text(
                'model = "gpt-test"\n'
                '[model_providers.deepseek]\n'
                'name = "DeepSeek"\n'
                'base_url = "https://api.deepseek.com"\n'
                'wire_api = "responses"\n'
                'env_key = "DEEPSEEK_API_KEY"\n',
                encoding="utf-8",
            )
            base_model = {
                "slug": "gpt-test",
                "display_name": "GPT Test",
                "visibility": "list",
                "base_instructions": "test instructions",
                "model_messages": {"instructions_template": "test instructions"},
                "support_verbosity": False,
                "use_responses_lite": True,
                "tool_mode": "code_mode_only",
            }
            (codex_home / "models_cache.json").write_text(
                json.dumps({"models": [base_model]}), encoding="utf-8"
            )
            stale = dict(base_model)
            stale.update(
                {
                    "slug": "deepseek-flash",
                    "context_window": 1000000,
                    "auto_review_model_override": None,
                }
            )
            stale_pro = dict(stale)
            stale_pro["slug"] = "deepseek-v4-pro"
            (codex_home / "models.json").write_text(
                json.dumps({"models": [stale, stale_pro]}), encoding="utf-8"
            )

            env = os.environ.copy()
            env["COEXIST_CODEX_HOME"] = str(codex_home)
            result = subprocess.run(
                ["bash", str(CONFIG_TOOL), "refresh"],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            catalog = json.loads((codex_home / "models-coexist.json").read_text())
            models = {model["slug"]: model for model in catalog["models"]}
            flash = models["deepseek-flash"]
            self.assertNotIn("deepseek-v4-pro", models)
            self.assertEqual(flash["context_window"], 1048576)
            self.assertTrue(flash["support_verbosity"])
            self.assertIsNone(flash["tool_mode"])
            self.assertFalse(flash["use_responses_lite"])
            self.assertEqual(flash["shell_type"], "shell_command")
            self.assertEqual(flash["display_name"], "DeepSeek-V4.1-Flash")
            self.assertEqual(flash["input_modalities"], ["text", "image"])
            self.assertEqual(flash["auto_review_model_override"], "deepseek-flash")
            self.assertEqual(
                [level["effort"] for level in flash["supported_reasoning_levels"]],
                ["low", "high", "max"],
            )


if __name__ == "__main__":
    unittest.main()
