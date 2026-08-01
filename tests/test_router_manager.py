from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))
import router_manager


PARENT = """mod process_exec_processor;
mod remote_control_processor;
"""

THREAD = """use super::*;

fn example(params: ThreadStartParams) {
        let ThreadStartParams {
            model,
            model_provider,
            environments,
        } = params;
        if matches!(history_mode, Some(Example)) {}
}
"""


class PatchSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="router-manager-test-")
        self.root = Path(self.temporary.name)
        source = self.root / "source" / "codex-rs" / "app-server" / "src"
        processors = source / "request_processors"
        processors.mkdir(parents=True)
        (source / "request_processors.rs").write_text(PARENT, encoding="utf-8")
        (processors / "thread_processor.rs").write_text(THREAD, encoding="utf-8")
        self.patch_asset = self.root / "provider_route.rs"
        self.patch_asset.write_text("pub(super) fn placeholder() {}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_applies_and_is_idempotent(self) -> None:
        source = self.root / "source"
        self.assertEqual(router_manager.patch_source(source, self.patch_asset), "patched")
        self.assertEqual(
            router_manager.patch_source(source, self.patch_asset), "already-patched"
        )
        parent = (source / "codex-rs/app-server/src/request_processors.rs").read_text()
        thread = (
            source
            / "codex-rs/app-server/src/request_processors/thread_processor.rs"
        ).read_text()
        self.assertIn("mod provider_route;", parent)
        self.assertIn(router_manager.CALL_MARKER, thread)

    def test_refuses_changed_anchor(self) -> None:
        source = self.root / "source"
        parent = source / "codex-rs/app-server/src/request_processors.rs"
        parent.write_text("mod something_new;\n", encoding="utf-8")
        with self.assertRaises(router_manager.RouterError):
            router_manager.patch_source(source, self.patch_asset)

    def test_refuses_partial_patch(self) -> None:
        source = self.root / "source"
        parent = source / "codex-rs/app-server/src/request_processors.rs"
        parent.write_text(PARENT + "mod provider_route;\n", encoding="utf-8")
        with self.assertRaises(router_manager.RouterError):
            router_manager.patch_source(source, self.patch_asset)


class LockDiffTests(unittest.TestCase):
    def test_accepts_only_workspace_version_changes(self) -> None:
        diff = """--- a/codex-rs/Cargo.lock
+++ b/codex-rs/Cargo.lock
@@ -10 +10 @@
-version = "0.0.0"
+version = "0.146.0-alpha.9.2"
"""
        self.assertEqual(
            router_manager.validate_workspace_lock_diff(diff, "0.146.0-alpha.9.2"),
            1,
        )

    def test_rejects_external_dependency_changes(self) -> None:
        diff = """--- a/codex-rs/Cargo.lock
+++ b/codex-rs/Cargo.lock
@@ -10 +10 @@
-version = "1.0.103"
+version = "1.0.104"
"""
        with self.assertRaises(router_manager.RouterError):
            router_manager.validate_workspace_lock_diff(diff, "0.146.0-alpha.9.2")

    def test_rejects_unbalanced_workspace_changes(self) -> None:
        with self.assertRaises(router_manager.RouterError):
            router_manager.validate_workspace_lock_diff(
                '-version = "0.0.0"\n', "0.146.0-alpha.9.2"
            )


class SupportMetadataTests(unittest.TestCase):
    def test_launch_agent_labels_are_machine_independent(self) -> None:
        environment = router_manager.plist_environment(Path("/tmp/codex-provider"))
        updater = router_manager.plist_updater(
            Path("/tmp/router_manager.py"),
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path("/tmp/provider-runtime"),
        )
        self.assertEqual(environment["Label"], "com.codex.provider-runtime.environment")
        self.assertEqual(updater["Label"], "com.codex.provider-runtime.updater")
        self.assertIn("/tmp/provider-runtime", updater["ProgramArguments"])

    def test_legacy_agent_names_cover_previous_installations(self) -> None:
        self.assertIn(
            "com.dudu.codex-deepseek-router-updater.plist",
            router_manager.LEGACY_SUPPORT_NAMES,
        )


if __name__ == "__main__":
    unittest.main()
