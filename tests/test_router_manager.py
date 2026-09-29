from __future__ import annotations

import json
import os
import plistlib
import subprocess
import shutil
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
        self.assertIn('build_env["CARGO_NET_GIT_FETCH_WITH_CLI"] = "true"', source)
        self.assertIn('build_env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"', source)
        self.assertIn('"test",\n            "--release",', source)

    def test_install_support_persists_policy_and_copies_matching_recipe(self) -> None:
        project_root = Path(router_manager.__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="router-support-policy-test-") as temporary:
            root = Path(temporary)
            home = root / "home"
            install_root = root / "provider-runtime"
            official = root / "codex"
            with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
                router_manager, "launchctl_allow_failure"
            ):
                router_manager.install_support(
                    project_root, install_root, official, "prebuilt-only"
                )
            self.assertEqual(
                router_manager.load_distribution_policy(install_root), "prebuilt-only"
            )
            installed_lib = install_root / "lib"
            self.assertEqual(
                router_manager.build_recipe_sha256(project_root),
                router_manager.build_recipe_sha256(installed_lib),
            )
            agents = home / "Library" / "LaunchAgents"
            updater = plistlib.loads(
                (agents / f"{router_manager.UPDATER_LABEL}.plist").read_bytes()
            )
            self.assertIn("--distribution", updater["ProgramArguments"])
            self.assertIn("prebuilt-only", updater["ProgramArguments"])

    def test_prebuilt_only_prerequisites_do_not_require_rust_toolchain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-prebuilt-prereq-test-") as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command_name in ("dirname", "git", "gh", "jq", "sqlite3", "rg"):
                destination = fake_bin / command_name
                body = (
                    '#!/bin/sh\nexec /usr/bin/dirname "$@"\n'
                    if command_name == "dirname"
                    else "#!/bin/sh\nexit 0\n"
                )
                destination.write_text(body, encoding="utf-8")
                destination.chmod(0o755)
            official = root / "codex"
            official.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            official.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = os.fspath(fake_bin)
            env["CODEX_OFFICIAL_CLI_PATH"] = os.fspath(official)
            result = subprocess.run(
                [
                    os.fspath(PROJECT_ROOT / "bin" / "codex-provider"),
                    "prerequisites",
                    "--distribution",
                    "prebuilt-only",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("cargo", result.stdout)
            self.assertNotIn("rustup", result.stdout)


class OfficialCodexLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="router-manager-layout-test-")
        self.resources = Path(self.temporary.name) / "Resources"
        self.resources.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, path: Path, body: str = "#!/bin/sh\necho codex-cli 0.158.0-alpha.2.1\n") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def write_layout(self, package: dict, entrypoint: Path) -> Path:
        layout = self.resources / "codex-cli"
        self.write(entrypoint)
        (layout / "codex-package.json").write_text(
            json.dumps(package), encoding="utf-8"
        )
        return layout

    def test_prefers_legacy_binary_when_present(self) -> None:
        legacy = self.write(self.resources / "codex")
        self.write_layout(
            {"layoutVersion": 1, "entrypoint": "bin/codex"},
            self.resources / "codex-cli" / "bin" / "codex",
        )
        self.assertEqual(
            router_manager.resolve_official_codex(None, self.resources), legacy
        )

    def test_resolves_codex_cli_layout_entrypoint(self) -> None:
        self.write_layout(
            {"layoutVersion": 1, "entrypoint": "bin/codex"},
            self.resources / "codex-cli" / "bin" / "codex",
        )
        self.assertEqual(
            router_manager.resolve_official_codex(None, self.resources),
            self.resources / "codex-cli" / "bin" / "codex",
        )

    def test_resolves_codex_cli_layout_without_package_json(self) -> None:
        app_binary = self.write(
            self.resources / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex"
        )
        self.assertEqual(
            router_manager.resolve_official_codex(None, self.resources), app_binary
        )

    def test_missing_bundled_cli_reports_legacy_path(self) -> None:
        self.assertEqual(
            router_manager.resolve_official_codex(None, self.resources),
            self.resources / "codex",
        )

    def test_explicit_override_wins_over_detection(self) -> None:
        explicit = self.resources / "elsewhere" / "codex"
        self.write(self.resources / "codex")
        self.assertEqual(
            router_manager.resolve_official_codex(explicit, self.resources), explicit
        )

    def test_code_mode_host_resolves_inside_codex_cli_layout(self) -> None:
        entrypoint = self.write(
            self.resources / "codex-cli" / "bin" / "codex"
        )
        host = self.write(self.resources / "codex-cli" / "bin" / "codex-code-mode-host", "#!/bin/sh\nexit 0\n")
        self.assertEqual(router_manager.bundled_code_mode_host(entrypoint), host)

    def test_code_mode_host_resolves_beside_app_bundle_backend(self) -> None:
        backend = self.write(
            self.resources / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex"
        )
        host = self.write(self.resources / "codex-cli" / "bin" / "codex-code-mode-host", "#!/bin/sh\nexit 0\n")
        self.assertEqual(router_manager.bundled_code_mode_host(backend), host)

    def test_updater_watch_paths_include_codex_cli_layout(self) -> None:
        entrypoint = self.write(self.resources / "codex-cli" / "bin" / "codex")
        (self.resources / "codex-cli" / "codex-package.json").write_text(
            json.dumps({"layoutVersion": 1, "entrypoint": "bin/codex"}),
            encoding="utf-8",
        )
        paths = router_manager.updater_watch_paths(entrypoint)
        self.assertIn(os.fspath(entrypoint), paths)
        self.assertIn(os.fspath(self.resources / "codex-cli"), paths)


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
        recipe_digest: str | None = None,
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
            "recipe_sha256": recipe_digest or router_manager.build_recipe_sha256(
                Path(router_manager.__file__).resolve().parent
            ),
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
                f"0.153.4-{self.COMMIT[:12]}-{official_digest[:12]}-{digest[:12]}-"
                f"{router_manager.build_recipe_sha256(Path(router_manager.__file__).resolve().parent)[:12]}",
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

    def test_no_reuse_skips_cached_binary_carryover_but_keeps_auto_distribution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-no-reuse-test-") as temporary:
            root = Path(temporary)
            install_root = root / "install"
            official = self.fake_client(root)
            patch_asset = self.patch_asset()
            result_path = root / "new-release"
            with mock.patch.object(
                router_manager,
                "build_release",
                return_value=result_path,
            ) as build:
                result = router_manager.main(
                    [
                        "--install-root",
                        str(install_root),
                        "--official-codex",
                        str(official),
                        "update",
                        "--no-reuse",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertFalse(build.call_args.kwargs["allow_reuse"])
            self.assertEqual(build.call_args.kwargs["distribution"], "auto")
            self.assertTrue(build.call_args.kwargs["allow_prebuilt"])

    def test_legacy_release_without_recipe_is_not_reused_for_current_recipe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-legacy-reuse-test-") as temporary:
            install_root = Path(temporary)
            digest = router_manager.sha256(self.patch_asset())
            legacy = self.write_release(install_root, "0.153.4-legacy", digest)
            manifest_path = legacy / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("recipe_sha256")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            current_recipe = router_manager.build_recipe_sha256(
                Path(router_manager.__file__).resolve().parent
            )
            self.assertIsNone(
                router_manager.find_certified_release(
                    install_root, "0.153.4", digest, recipe_digest=current_recipe
                )
            )


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

    def test_prebuilt_lookup_has_a_short_retry_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-prebuilt-backoff-test-") as temporary:
            root = Path(temporary)
            fingerprint = "b" * 64
            self.assertTrue(router_manager.prebuilt_check_is_due(root, fingerprint, now=2000))
            marker = router_manager.record_prebuilt_check(root, fingerprint)
            payload = json.loads(marker.read_text(encoding="utf-8"))
            attempted_at = payload["attempted_at_epoch"]
            self.assertFalse(
                router_manager.prebuilt_check_is_due(
                    root, fingerprint, now=attempted_at + router_manager.PREBUILT_RETRY_SECONDS - 1
                )
            )
            self.assertTrue(
                router_manager.prebuilt_check_is_due(
                    root, fingerprint, now=attempted_at + router_manager.PREBUILT_RETRY_SECONDS
                )
            )
            router_manager.clear_prebuilt_check(root, fingerprint)
            self.assertFalse(marker.exists())

    def test_prebuilt_only_mode_does_not_memoize_source_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-prebuilt-only-test-") as temporary:
            root = Path(temporary)
            official = root / "codex"
            official.write_text("#!/bin/sh\necho 'codex-cli 1.2.3'\n", encoding="utf-8")
            official.chmod(0o755)
            fingerprint = router_manager.update_fingerprint(
                official, Path(router_manager.__file__).resolve().parent / "patches" / "provider_route.rs"
            )
            with mock.patch.object(
                router_manager,
                "build_prebuilt_release",
                side_effect=router_manager.RouterError("attested binary failed protocol smoke"),
            ) as prebuilt:
                result = router_manager.main(
                    [
                        "--install-root",
                        str(root / "install"),
                        "--official-codex",
                        str(official),
                        "update",
                        "--non-interactive",
                        "--distribution",
                        "prebuilt-only",
                    ]
                )
            self.assertEqual(result, 0)
            prebuilt.assert_called_once()
            self.assertFalse(router_manager.failed_update_path(root / "install", fingerprint).exists())
            self.assertTrue(router_manager.prebuilt_check_path(root / "install", fingerprint).is_file())

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
            self.assertTrue((root / "cache" / "cargo-target").exists())
            self.assertEqual(
                removed, {"releases": 1, "builds": 1, "cargo_targets": 0}
            )
            cleared = router_manager.prune_runtime(
                root, keep_releases=2, clear_cargo_target=True
            )
            self.assertFalse((root / "cache" / "cargo-target").exists())
            self.assertEqual(cleared["cargo_targets"], 1)

    def test_success_cleanup_keeps_cargo_target_until_size_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="router-cargo-cache-test-") as temporary:
            root = Path(temporary)
            target = root / "cache" / "cargo-target"
            target.mkdir(parents=True)
            oversized = target / "oversized.cache"
            with oversized.open("wb") as handle:
                handle.truncate(router_manager.cargo_target_max_bytes() + 1)
            removed = router_manager.prune_runtime(root)
            self.assertFalse(target.exists())
            self.assertEqual(removed["cargo_targets"], 1)

if __name__ == "__main__":
    unittest.main()
