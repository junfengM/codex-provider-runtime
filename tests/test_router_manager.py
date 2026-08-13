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

        let model_provider_filter = match model_providers {
            Some(providers) => {
                if providers.is_empty() {
                    None
                } else {
                    Some(providers)
                }
            }
            None if relation_filter.is_some() => None,
            None => Some(vec![self.config.model_provider_id.clone()]),
        };
}

async fn resume_example(params: ThreadResumeParams) {
        let ThreadResumeParams {
            model,
            model_provider,
        } = params;
        let mut typesafe_overrides = ConfigOverrides::default();
        let persisted_metadata = self
            .load_and_apply_persisted_resume_metadata(
                &thread_history,
                &mut request_overrides,
                &mut typesafe_overrides,
            )
            .await;

        // Derive a Config using the same logic as new conversation, honoring overrides if provided.
        let _ = (model, model_provider, persisted_metadata);
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
        self.assertIn(router_manager.HISTORY_CALL_MARKER, thread)
        self.assertIn(router_manager.RESUME_CALL_MARKER, thread)
        self.assertNotIn(
            "Some(vec![self.config.model_provider_id.clone()])",
            thread,
        )

    def test_upgrades_the_legacy_new_thread_only_patch(self) -> None:
        source = self.root / "source"
        parent = source / "codex-rs/app-server/src/request_processors.rs"
        thread = source / "codex-rs/app-server/src/request_processors/thread_processor.rs"
        destination = source / "codex-rs/app-server/src/request_processors/provider_route.rs"
        parent.write_text(
            PARENT.replace(
                "mod process_exec_processor;\nmod remote_control_processor;",
                "mod process_exec_processor;\nmod provider_route;\nmod remote_control_processor;",
            ),
            encoding="utf-8",
        )
        legacy_thread = THREAD.replace(
            "use super::*;\n",
            "use super::*;\nuse super::provider_route::model_provider_for_new_thread;\n",
            1,
        ).replace(
            "            environments,\n        } = params;\n        if matches!(",
            "            environments,\n"
            "        } = params;\n"
            "        let model_provider =\n"
            "            model_provider_for_new_thread(model.as_deref(), model_provider);\n"
            "        if matches!(",
            1,
        )
        thread.write_text(legacy_thread, encoding="utf-8")
        destination.write_bytes(self.patch_asset.read_bytes())

        self.assertEqual(router_manager.patch_source(source, self.patch_asset), "patched")
        upgraded = thread.read_text(encoding="utf-8")
        self.assertIn(router_manager.CALL_MARKER, upgraded)
        self.assertIn(router_manager.HISTORY_CALL_MARKER, upgraded)
        self.assertIn(router_manager.RESUME_CALL_MARKER, upgraded)
        self.assertIn("model_provider_filter_for_thread_list", upgraded)

    def test_updates_changed_patch_asset_in_an_existing_patched_tree(self) -> None:
        source = self.root / "source"
        self.assertEqual(router_manager.patch_source(source, self.patch_asset), "patched")
        self.patch_asset.write_text(
            "pub(super) fn updated_placeholder() {}\n", encoding="utf-8"
        )
        self.assertEqual(
            router_manager.patch_source(source, self.patch_asset),
            "updated-patch-asset",
        )
        installed = source / "codex-rs/app-server/src/request_processors/provider_route.rs"
        self.assertEqual(installed.read_bytes(), self.patch_asset.read_bytes())

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
    def test_patch_asset_covers_routing_and_history_contracts(self) -> None:
        patch_asset = PROJECT_ROOT / "runtime/patches/provider_route.rs"
        text = patch_asset.read_text(encoding="utf-8")
        self.assertIn("model_provider_for_new_thread", text)
        self.assertIn("model_provider_for_resume", text)
        self.assertIn("model_provider_filter_for_thread_list", text)
        self.assertIn('"deepseek-v4-pro"', text)
        self.assertIn("routes_pro_for_new_and_resumed_threads", text)
        self.assertIn("omitted_thread_list_filter_includes_all_providers", text)
        self.assertIn("explicit_thread_list_filter_remains_authoritative", text)

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

    def test_bundled_code_mode_host_resolves_next_to_official_codex(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-manager-host-test-") as temporary:
            resources = Path(temporary)
            official = resources / "codex"
            host = resources / "codex-code-mode-host"
            official.touch()
            host.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            host.chmod(0o755)
            self.assertEqual(router_manager.bundled_code_mode_host(official), host)

    def test_bundled_code_mode_host_must_be_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-manager-host-test-") as temporary:
            official = Path(temporary) / "codex"
            official.touch()
            with self.assertRaises(router_manager.RouterError):
                router_manager.bundled_code_mode_host(official)

    def test_release_build_reuses_bundled_code_mode_host(self) -> None:
        source = Path(router_manager.__file__).read_text(encoding="utf-8")
        build_block = source.split('cargo,\n            "build"', 1)[1].split(
            "built_codex =", 1
        )[0]
        self.assertIn('"codex-cli"', build_block)
        self.assertNotIn('"codex-code-mode-host"', build_block)
        self.assertIn(
            'built_host = bundled_code_mode_host(official_codex)',
            source,
        )


if __name__ == "__main__":
    unittest.main()
