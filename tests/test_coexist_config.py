from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_TOOL = PROJECT_ROOT / "config" / "coexist.sh"


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
                    "slug": "deepseek-v4-flash",
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
            flash = models["deepseek-v4-flash"]
            self.assertNotIn("deepseek-v4-pro", models)
            self.assertEqual(flash["context_window"], 1048576)
            self.assertTrue(flash["support_verbosity"])
            self.assertIsNone(flash["tool_mode"])
            self.assertFalse(flash["use_responses_lite"])
            self.assertEqual(flash["shell_type"], "shell_command")
            self.assertEqual(flash["auto_review_model_override"], "deepseek-v4-flash")
            self.assertEqual(
                [level["effort"] for level in flash["supported_reasoning_levels"]],
                ["low", "high", "max"],
            )


if __name__ == "__main__":
    unittest.main()
