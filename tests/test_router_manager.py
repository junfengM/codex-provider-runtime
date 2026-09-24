from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))
import router_manager


PARENT = """mod process_exec_processor;
mod remote_control_processor;
"""

PARENT_WITH_PROJECTS = """mod process_exec_processor;
mod projects;
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

THREAD_WITH_PREPARED_RESUME = THREAD.replace(
    """
        // Derive a Config using the same logic as new conversation, honoring overrides if provided.
""",
    """
        let clear_reasoning_effort = !has_explicit_model_resume_override
            && persisted_metadata.is_some();
""",
)


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

    def test_applies_to_new_request_processor_module_layout(self) -> None:
        source = self.root / "source"
        parent = source / "codex-rs/app-server/src/request_processors.rs"
        parent.write_text(PARENT_WITH_PROJECTS, encoding="utf-8")

        self.assertEqual(router_manager.patch_source(source, self.patch_asset), "patched")
        self.assertIn(
            "mod process_exec_processor;\nmod provider_route;\nmod projects;",
            parent.read_text(encoding="utf-8"),
        )

    def test_applies_to_prepared_resume_config_layout(self) -> None:
        source = self.root / "source"
        thread = source / "codex-rs/app-server/src/request_processors/thread_processor.rs"
        thread.write_text(THREAD_WITH_PREPARED_RESUME, encoding="utf-8")

        self.assertEqual(router_manager.patch_source(source, self.patch_asset), "patched")
        patched = thread.read_text(encoding="utf-8")
        self.assertIn(router_manager.RESUME_CALL_MARKER, patched)
        self.assertLess(
            patched.index(router_manager.RESUME_CALL_MARKER),
            patched.index("let clear_reasoning_effort"),
        )

    def test_upgrades_the_legacy_new_thread_only_patch(self) -> None:
        source = self.root / "source"
        parent = source / "codex-rs/app-server/src/request_processors.rs"
        thread = source / "codex-rs/app-server/src/request_processors/thread_processor.rs"
        destination = source / "codex-rs/app-server/src/request_processors/provider_route.rs"
        parent.write_text(
            PARENT.replace(
                "mod remote_control_processor;",
                "mod provider_route;\nmod remote_control_processor;",
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
    def test_normalization_prefetches_before_offline_workspace_update(self) -> None:
        source = Path(router_manager.__file__).read_text(encoding="utf-8")
        block = source.split("def normalize_release_lock(", 1)[1].split(
            "\n\ndef ensure_source_checkout", 1
        )[0]
        fetch_index = block.index('run([cargo, "fetch"]')
        offline_index = block.index(
            'run([cargo, "update", "--workspace", "--offline"]'
        )
        diff_index = block.index('"diff", "--unified=0"')
        self.assertLess(fetch_index, offline_index)
        self.assertLess(offline_index, diff_index)

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

    def test_source_build_uses_bounded_memory_profile(self) -> None:
        source = Path(router_manager.__file__).read_text(encoding="utf-8")
        self.assertIn('build_env["CARGO_BUILD_JOBS"] = "1"', source)
        self.assertIn('build_env["CARGO_PROFILE_RELEASE_LTO"] = "false"', source)
        self.assertIn(
            'build_env["CARGO_PROFILE_RELEASE_CODEGEN_UNITS"] = "1"', source
        )
        self.assertIn('build_env["CARGO_PROFILE_RELEASE_DEBUG"] = "none"', source)
        self.assertIn(
            'build_env["CARGO_PROFILE_RELEASE_STRIP"] = "symbols"', source
        )


class ReleaseReuseTests(unittest.TestCase):
    COMMIT = "3d2ee51ca2d5db578f328aa75e20aa22c0197c9a"
    OTHER_COMMIT = "a30ec314bbd0e3721632234d07db7c99855db3b9"
    SMOKE = {
        "new_thread_routing": {"deepseek-flash": "deepseek"},
        "resumed_thread_routing": {"model_provider": "deepseek"},
        "thread_list_visibility": {"omitted_null_empty_match": True},
    }

    def write_executable(self, path: Path, body: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def fake_client(self, root: Path) -> Path:
        official = self.write_executable(
            root / "ChatGPT.app" / "codex", "#!/bin/sh\necho codex-cli 0.153.4\n"
        )
        self.write_executable(official.with_name("codex-code-mode-host"), "#!/bin/sh\nexit 0\n")
        return official

    def write_release(
        self,
        install_root: Path,
        name: str,
        patch_digest: str,
        *,
        commit: str = COMMIT,
        binary_version: str = "0.153.4",
        built_at: str = "2026-09-08T11:34:58+00:00",
    ) -> Path:
        release = install_root / "releases" / name
        binary = self.write_executable(
            release / "codex", f"#!/bin/sh\necho codex-cli {binary_version}\n"
        )
        manifest = {
            "schema": 1,
            "codex_version": "0.153.4",
            "source_tag": "rust-v0.153.4",
            "source_commit": commit,
            "official_binary": "/Applications/ChatGPT.app/Contents/Resources/codex",
            "official_sha256": "a" * 64,
            "patch_sha256": patch_digest,
            "custom_sha256": router_manager.sha256(binary),
            "code_mode_host_sha256": "b" * 64,
            "code_mode_host_source": "bundled-with-desktop",
            "patch": router_manager.PATCH_NAME,
            "built_at": built_at,
            "workspace_lock_versions_normalized": 149,
            "protocol_smoke": self.SMOKE,
            "tests": ["provider_route unit tests"],
        }
        (release / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return release

    def patch_asset(self) -> Path:
        return Path(router_manager.__file__).parent / "patches" / "provider_route.rs"

    def test_finds_certified_release_for_same_source_commit_and_patch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-reuse-test-") as temporary:
            install_root = Path(temporary)
            digest = router_manager.sha256(self.patch_asset())
            release = self.write_release(install_root, "0.153.4-old", digest)
            self.assertEqual(
                router_manager.find_certified_release(install_root, "0.153.4", digest),
                release,
            )

    def test_ignores_release_with_tampered_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-reuse-test-") as temporary:
            install_root = Path(temporary)
            digest = router_manager.sha256(self.patch_asset())
            release = self.write_release(install_root, "0.153.4-old", digest)
            (release / "codex").write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
            self.assertIsNone(
                router_manager.find_certified_release(install_root, "0.153.4", digest)
            )

    def test_ignores_release_with_mismatched_version_or_patch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-reuse-test-") as temporary:
            install_root = Path(temporary)
            digest = router_manager.sha256(self.patch_asset())
            self.write_release(
                install_root, "0.153.4-old", digest, binary_version="0.153.3"
            )
            self.assertIsNone(
                router_manager.find_certified_release(install_root, "0.153.4", digest)
            )
            self.assertIsNone(
                router_manager.find_certified_release(install_root, "0.153.4", "0" * 64)
            )

    def test_ambiguous_source_commit_falls_back_to_a_source_build(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-reuse-test-") as temporary:
            install_root = Path(temporary)
            digest = router_manager.sha256(self.patch_asset())
            self.write_release(install_root, "0.153.4-old", digest)
            self.write_release(
                install_root,
                "0.153.4-newer",
                digest,
                commit=self.OTHER_COMMIT,
                built_at="2026-09-10T10:24:42+00:00",
            )
            self.assertIsNone(
                router_manager.find_certified_release(install_root, "0.153.4", digest)
            )

    def test_update_reuses_cached_binary_without_cargo_or_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-reuse-test-") as temporary:
            install_root = Path(temporary)
            patch_asset = self.patch_asset()
            digest = router_manager.sha256(patch_asset)
            cached = self.write_release(install_root, "0.153.4-cached", digest)
            official = self.fake_client(install_root)
            with mock.patch.object(
                router_manager, "protocol_smoke_suite", return_value=self.SMOKE
            ), mock.patch.object(
                router_manager,
                "ensure_source_checkout",
                side_effect=AssertionError("source checkout must not be required"),
            ), mock.patch.object(
                router_manager,
                "find_cargo",
                side_effect=AssertionError("cargo must not be required"),
            ):
                release = router_manager.build_release(
                    install_root, official, patch_asset, non_interactive=True
                )
            official_digest = router_manager.sha256(official)
            self.assertEqual(
                release.name,
                f"0.153.4-{self.COMMIT[:12]}-{official_digest[:12]}-{digest[:12]}",
            )
            manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["official_sha256"], official_digest)
            self.assertEqual(manifest["reused_from"], cached.name)
            self.assertEqual(manifest["source_commit"], self.COMMIT)
            self.assertEqual(
                manifest["custom_sha256"],
                router_manager.sha256(cached / "codex"),
            )
            self.assertEqual(manifest["protocol_smoke"], self.SMOKE)
            self.assertTrue((release / "codex-code-mode-host").is_file())
            self.assertEqual(
                (install_root / "current").resolve(),
                release.resolve(),
            )

    def test_no_reuse_flag_forces_a_source_build(self) -> None:
        source = Path(router_manager.__file__).read_text(encoding="utf-8")
        update_block = source.split('if allow_reuse:', 1)[1].split("ensure_source_checkout", 1)[0]
        self.assertIn("find_certified_release", update_block)
        self.assertIn("reuse_release", update_block)


class UpdateStateTests(unittest.TestCase):
    def test_failed_update_marker_is_keyed_without_exposing_secrets(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="router-manager-failure-test-"
        ) as temporary:
            root = Path(temporary)
            official = root / "codex"
            official.write_text("#!/bin/sh\necho 'codex-cli 1.2.3'\n", encoding="utf-8")
            official.chmod(0o755)
            patch = root / "provider_route.rs"
            patch.write_text("safe patch\n", encoding="utf-8")
            fingerprint = router_manager.update_fingerprint(official, patch)
            marker = router_manager.record_failed_update(
                root, fingerprint, official, router_manager.RouterError("anchor changed")
            )
            self.assertEqual(marker, router_manager.failed_update_path(root, fingerprint))
            payload = marker.read_text(encoding="utf-8")
            self.assertIn('"codex_version": "1.2.3"', payload)
            self.assertIn("anchor changed", payload)
            router_manager.clear_failed_update(root, fingerprint)
            self.assertFalse(marker.exists())

    def test_prune_keeps_active_and_one_rollback_and_clears_build_state(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="router-manager-prune-test-"
        ) as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            old = releases / "old"
            active = releases / "active"
            newest = releases / "newest"
            for index, release in enumerate((old, active, newest), start=1):
                release.mkdir()
                (release / "codex").write_text("binary", encoding="utf-8")
                os.utime(release, (index, index))
            (root / "current").symlink_to(Path("releases") / active.name)
            (root / "builds" / "failed" / "source").mkdir(parents=True)
            (root / "cache" / "cargo-target" / "release").mkdir(parents=True)

            removed = router_manager.prune_runtime(root, keep_releases=2)

            self.assertFalse(old.exists())
            self.assertTrue(active.exists())
            self.assertTrue(newest.exists())
            self.assertFalse((root / "builds" / "failed").exists())
            self.assertFalse((root / "cache" / "cargo-target").exists())
            self.assertEqual(
                removed, {"releases": 1, "builds": 1, "cargo_targets": 1}
            )

if __name__ == "__main__":
    unittest.main()
