import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COEXIST_SKILL = ROOT / "integrations/codex-skill/codex-model-coexist"
RUNTIME_SKILL = ROOT / "integrations/codex-skill/codex-provider-runtime"


class SkillEvolutionContractTests(unittest.TestCase):
    def test_coexist_skill_separates_invariants_from_dated_baseline(self) -> None:
        body = (COEXIST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("living operational guide", body)
        self.assertIn("revisable implementation choices", body)
        self.assertIn("Current verified baseline", body)
        self.assertIn("dated evidence, not a permanent prohibition", body)
        self.assertIn("reusable runtime as source of truth", body)

    def test_runtime_skill_requires_evidence_driven_evolution(self) -> None:
        body = (RUNTIME_SKILL / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(body.split())
        self.assertIn("Recheck official documentation and live wire behavior", normalized)
        self.assertIn("update runtime code", normalized)
        self.assertIn("dated baseline, not a permanent prohibition", normalized)

    def test_coexist_skill_does_not_duplicate_runtime_scripts(self) -> None:
        scripts = COEXIST_SKILL / "scripts"
        self.assertFalse(scripts.exists(), "Use the reusable project CLI as source of truth")


if __name__ == "__main__":
    unittest.main()
