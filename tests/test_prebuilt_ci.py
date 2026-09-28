from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

import prebuilt_ci


VERSION = "0.155.0-alpha.16.4"


def release(tag: str, asset_names: tuple[str, ...] = ()) -> dict:
    return {
        "tag_name": tag,
        "assets": [
            {"name": name, "size": 100, "state": "uploaded"} for name in asset_names
        ],
    }


def smoke_manifest() -> dict:
    return {
        "protocol_smoke": {
            "new_thread_routing": {
                "deepseek-flash": "deepseek",
                "deepseek-v4-flash": "deepseek",
                "deepseek-v4-pro": "deepseek",
                "gpt-5.6-sol": "openai",
            },
            "resumed_thread_routing": {"deepseek-flash": "deepseek"},
            "thread_list_visibility": {
                "omitted_null_empty_match": True,
                "seeded_providers": {
                    "gpt-5.6-sol": "openai",
                    "deepseek-flash": "deepseek",
                },
                "providers": {"openai": 1, "deepseek": 1},
            },
        }
    }


class ReleaseSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = mock.patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": prebuilt_ci.REPOSITORY,
                "GITHUB_REF": "refs/heads/main",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_plan_rejects_non_main_refs(self) -> None:
        with mock.patch.dict(
            os.environ, {"GITHUB_REF": "refs/pull/1/merge"}
        ), self.assertRaisesRegex(prebuilt_ci.PrebuiltCIError, "refs/heads/main"):
            prebuilt_ci.plan("schedule", "")

    def test_latest_scan_selects_first_recent_rust_release_only(self) -> None:
        page = [
            release("rusty-v8-v150.4.0"),
            release("rust-v0.155.0-alpha.16.4", (prebuilt_ci.CODEX_ASSET,)),
            release("rust-v0.155.0-alpha.16.3", prebuilt_ci.REQUIRED_UPSTREAM_ASSETS),
        ]
        with mock.patch.object(prebuilt_ci, "_api_json", return_value=page) as query:
            selected = prebuilt_ci._latest_upstream_release()
        self.assertEqual(selected, page[1])
        query.assert_called_once_with("repos/openai/codex/releases?per_page=20")

    def test_schedule_skips_latest_candidate_without_assets_without_falling_back(self) -> None:
        newest = release("rust-v0.155.0-alpha.16.4", (prebuilt_ci.CODEX_ASSET,))
        with (
            mock.patch.object(prebuilt_ci, "_latest_upstream_release", return_value=newest),
            mock.patch.object(prebuilt_ci, "_release_for_tag") as own_release,
        ):
            result = prebuilt_ci.plan("schedule", "")
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["build_required"], "false")
        self.assertIn("lacks required", result["reason"])
        own_release.assert_not_called()

    def test_dispatch_rejects_missing_official_assets(self) -> None:
        with mock.patch.object(
            prebuilt_ci,
            "_release_for_tag",
            return_value=release(f"rust-v{VERSION}", (prebuilt_ci.CODEX_ASSET,)),
        ):
            with self.assertRaisesRegex(prebuilt_ci.PrebuiltCIError, "missing required"):
                prebuilt_ci.plan("workflow_dispatch", VERSION)

    def test_release_skips_only_when_both_assets_are_present(self) -> None:
        assets = release(
            "codex-provider-v-test",
            (prebuilt_ci.ARCHIVE_ASSET, prebuilt_ci.ATTESTATION_ASSET),
        )
        self.assertTrue(prebuilt_ci._release_is_complete(assets))
        assets["assets"].pop()
        self.assertFalse(prebuilt_ci._release_is_complete(assets))

    def test_only_validates_safe_version_strings(self) -> None:
        self.assertEqual(prebuilt_ci._valid_version(VERSION), VERSION)
        for value in ("../main", "", "v0.1", "space value", "x" * 129):
            with self.subTest(value=value), self.assertRaises(prebuilt_ci.PrebuiltCIError):
                prebuilt_ci._valid_version(value)


class BuildContractTests(unittest.TestCase):
    def test_protocol_smoke_requires_gpt_and_deepseek_route_evidence(self) -> None:
        prebuilt_ci._verify_smoke(smoke_manifest())
        bad = smoke_manifest()
        bad["protocol_smoke"]["new_thread_routing"]["gpt-5.6-sol"] = "deepseek"
        with self.assertRaisesRegex(prebuilt_ci.PrebuiltCIError, "routing does not match"):
            prebuilt_ci._verify_smoke(bad)

    def test_manifest_os_floor_is_13_and_actual_binary_minimum_can_be_lower(self) -> None:
        self.assertEqual(prebuilt_ci._version_parts("11.0"), (11, 0, 0))
        self.assertTrue(prebuilt_ci._version_at_most("11.0", "13.0"))
        self.assertTrue(prebuilt_ci._version_at_most("13.0", "13.0"))
        self.assertFalse(prebuilt_ci._version_at_most("14.0", "13.0"))

    def test_cache_identity_uses_exact_tag_lock_and_toolchain(self) -> None:
        with (
            mock.patch.object(
                prebuilt_ci,
                "_raw_upstream_file",
                side_effect=[b"lock-bytes", b'channel = "1.95.0"\n'],
            ) as raw_file,
            mock.patch.object(prebuilt_ci, "_current_recipe", return_value="a" * 64),
        ):
            identity = prebuilt_ci.cache_metadata(VERSION)
        self.assertEqual(identity["lock_sha256"], hashlib.sha256(b"lock-bytes").hexdigest())
        self.assertEqual(identity["toolchain"], "1.95.0")
        self.assertEqual(identity["target"], "aarch64-apple-darwin")
        self.assertEqual(raw_file.call_args_list[0].args, (VERSION, "codex-rs/Cargo.lock"))
        self.assertEqual(raw_file.call_args_list[1].args, (VERSION, "codex-rs/rust-toolchain.toml"))

    def test_reads_toolchain_channel_without_fixing_a_future_release(self) -> None:
        self.assertEqual(prebuilt_ci._toolchain_channel('channel = "1.96.0"\n'), "1.96.0")
        with self.assertRaises(prebuilt_ci.PrebuiltCIError):
            prebuilt_ci._toolchain_channel('channel = "1.96.0; touch /tmp/pwned"\n')

    def test_public_asset_download_never_forwards_workflow_token(self) -> None:
        payload = b"asset"

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset.tar.gz"
            request_seen = []
            with (
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}),
                mock.patch.object(
                    prebuilt_ci.urllib.request,
                    "urlopen",
                    side_effect=lambda request, **_kwargs: (request_seen.append(request) or Response(payload)),
                ),
            ):
                digest = prebuilt_ci._download_asset(
                    {
                        "name": "asset.tar.gz",
                        "size": len(payload),
                        "browser_download_url": "https://github.com/openai/codex/releases/download/v/asset.tar.gz",
                    },
                    destination,
                )
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(destination.read_bytes(), payload)
            self.assertNotIn("Authorization", dict(request_seen[0].header_items()))

    def test_archive_contains_exactly_codex_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "codex"
            manifest = root / "manifest.json"
            archive_path = root / prebuilt_ci.ARCHIVE_ASSET
            binary.write_bytes(b"binary")
            binary.chmod(0o755)
            manifest.write_text(json.dumps({"schema": 1}) + "\n", encoding="utf-8")
            prebuilt_ci._package_archive(binary, manifest, archive_path)
            with tarfile.open(archive_path, "r:gz") as archive:
                self.assertEqual(archive.getnames(), ["codex", "manifest.json"])
                self.assertEqual(archive.extractfile("codex").read(), b"binary")
                self.assertEqual(json.load(archive.extractfile("manifest.json")), {"schema": 1})
            self.assertNotIn("code-mode-host", archive_path.read_bytes().decode("latin1"))

    def test_cache_guard_reports_the_bounded_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = root / "cargo-cache"
            cached.mkdir()
            (cached / "registry.bin").write_bytes(b"12345")
            self.assertEqual(
                prebuilt_ci.guard_cache([cached], maximum=8),
                {"bytes": "5", "within_limit": "true"},
            )
            self.assertEqual(
                prebuilt_ci.guard_cache([cached], maximum=4),
                {"bytes": "5", "within_limit": "false"},
            )


class PublicationTests(unittest.TestCase):
    def candidate(self) -> dict:
        return {
            "release_tag": f"codex-provider-v{VERSION}-r{'a' * 12}",
            "codex_version": VERSION,
            "minimum_macos_version": "13.0",
            "source_tag": f"rust-v{VERSION}",
            "source_commit": "b" * 40,
            "runtime_source_commit": "c" * 40,
            "recipe_sha256": "a" * 64,
        }

    def test_incomplete_release_can_be_repaired_after_bundle_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / prebuilt_ci.ARCHIVE_ASSET
            bundle = directory / prebuilt_ci.ATTESTATION_ASSET
            candidate_file = directory / "release-candidate.json"
            archive.write_bytes(b"archive")
            bundle.write_bytes(b"bundle")
            candidate_file.write_text("{}", encoding="utf-8")
            events: list[str] = []
            partial = release(
                self.candidate()["release_tag"], (prebuilt_ci.ARCHIVE_ASSET,)
            )
            with (
                mock.patch.dict(os.environ, {"GITHUB_SHA": "d" * 40}),
                mock.patch.object(
                    prebuilt_ci,
                    "_validate_archive_and_manifest",
                    return_value=self.candidate(),
                ),
                mock.patch.object(
                    prebuilt_ci,
                    "verify_bundle",
                    side_effect=lambda *_args: events.append("verify"),
                ),
                mock.patch.object(
                    prebuilt_ci,
                    "_release_for_tag",
                    side_effect=lambda *_args: (events.append("lookup") or partial),
                ),
                mock.patch.object(prebuilt_ci.subprocess, "run", side_effect=lambda cmd, **_kwargs: events.append(cmd)),
            ):
                prebuilt_ci.publish(directory)
            self.assertEqual(events[0], "verify")
            upload = events[-1]
            self.assertEqual(
                upload[1:5],
                ["release", "upload", self.candidate()["release_tag"], str(archive)],
            )
            self.assertIn(str(bundle), upload)
            self.assertIn("--clobber", upload)

    def test_complete_release_is_never_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / prebuilt_ci.ARCHIVE_ASSET).write_bytes(b"archive")
            (directory / prebuilt_ci.ATTESTATION_ASSET).write_bytes(b"bundle")
            (directory / "release-candidate.json").write_text("{}", encoding="utf-8")
            complete = release(
                self.candidate()["release_tag"],
                (prebuilt_ci.ARCHIVE_ASSET, prebuilt_ci.ATTESTATION_ASSET),
            )
            with (
                mock.patch.object(
                    prebuilt_ci,
                    "_validate_archive_and_manifest",
                    return_value=self.candidate(),
                ),
                mock.patch.object(prebuilt_ci, "verify_bundle"),
                mock.patch.object(prebuilt_ci, "_release_for_tag", return_value=complete),
                mock.patch.object(prebuilt_ci.subprocess, "run") as run,
            ):
                prebuilt_ci.publish(directory)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
