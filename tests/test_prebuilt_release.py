from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from prebuilt_release import (
    ARCHIVE_ASSET,
    REPOSITORY,
    SIGNER_WORKFLOW,
    SOURCE_REF,
    PrebuiltReleaseError,
    build_recipe_sha256,
    download_attested_release,
    ensure_supported_host,
    release_tag,
    resolve_source_tag_commit,
    validate_and_extract_archive,
    validate_manifest,
)


VERSION = "0.155.0-alpha.16.4"
SOURCE_COMMIT = "a" * 40
PATCH_NAME = "deepseek-flash-pro-route-resume-and-all-provider-history-v7"
PATCH_SHA = "b" * 64
RECIPE_SHA = "c" * 64
BINARY = b"prebuilt codex test bytes"


def manifest_for(binary: bytes = BINARY) -> dict:
    return {
        "schema": 1,
        "codex_version": VERSION,
        "source_tag": f"rust-v{VERSION}",
        "source_commit": SOURCE_COMMIT,
        "patch_name": PATCH_NAME,
        "patch_sha256": PATCH_SHA,
        "recipe_sha256": RECIPE_SHA,
        "platform": "macos",
        "architecture": "aarch64",
        "runtime_source_ref": SOURCE_REF,
        "runtime_source_commit": "d" * 40,
        "minimum_macos_version": "15.0",
        "binary_sha256": hashlib.sha256(binary).hexdigest(),
        "provenance": {
            "repository": REPOSITORY,
            "workflow": SIGNER_WORKFLOW,
            "source_ref": SOURCE_REF,
        },
    }


def make_archive(path: Path, entries: list[tuple[str, bytes, str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "codex"
                info.size = 0
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(data))


class PrebuiltReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="prebuilt-release-test-")
        self.root = Path(self.temporary.name)
        self.archive = self.root / ARCHIVE_ASSET
        self.destination = self.root / "codex"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, archive: Path | None = None, **overrides: object) -> dict:
        arguments = {
            "version": VERSION,
            "source_commit": SOURCE_COMMIT,
            "patch_name": PATCH_NAME,
            "patch_sha256": PATCH_SHA,
            "recipe_sha256": RECIPE_SHA,
            "running_os": "15.4.1",
        }
        arguments.update(overrides)
        return validate_and_extract_archive(
            archive or self.archive, self.destination, **arguments
        )

    def good_entries(self, binary: bytes = BINARY) -> list[tuple[str, bytes, str]]:
        encoded = (json.dumps(manifest_for(binary), sort_keys=True) + "\n").encode()
        return [("codex", binary, "file"), ("manifest.json", encoded, "file")]

    def test_release_tag_is_version_scoped(self) -> None:
        self.assertEqual(
            release_tag(VERSION, RECIPE_SHA),
            f"codex-provider-v{VERSION}-r{RECIPE_SHA[:12]}",
        )
        self.assertNotEqual(
            release_tag(VERSION, RECIPE_SHA), release_tag(VERSION, "e" * 64)
        )
        with self.assertRaises(PrebuiltReleaseError):
            release_tag("../latest", RECIPE_SHA)

    def test_requires_apple_silicon_macos(self) -> None:
        ensure_supported_host("Darwin", "arm64")
        with self.assertRaisesRegex(PrebuiltReleaseError, "macOS arm64"):
            ensure_supported_host("Darwin", "x86_64")
        with self.assertRaisesRegex(PrebuiltReleaseError, "macOS arm64"):
            ensure_supported_host("Linux", "aarch64")

    def test_uses_peeled_annotated_source_tag_commit(self) -> None:
        annotated = "e" * 40
        commit = "f" * 40
        result = mock.Mock(stdout=f"{annotated}\trefs/tags/rust-v{VERSION}\n{commit}\trefs/tags/rust-v{VERSION}^{{}}\n")
        with mock.patch("prebuilt_release.subprocess.run", return_value=result) as run:
            self.assertEqual(resolve_source_tag_commit(VERSION), commit)
        self.assertIn(f"refs/tags/rust-v{VERSION}^{{}}", run.call_args.args[0])

    def test_wraps_source_tag_network_errors(self) -> None:
        with mock.patch(
            "prebuilt_release.subprocess.run",
            side_effect=TimeoutError("network timed out"),
        ):
            with self.assertRaisesRegex(PrebuiltReleaseError, "Could not resolve upstream"):
                resolve_source_tag_commit(VERSION)

    def test_accepts_exact_contract_and_extracts_binary(self) -> None:
        make_archive(self.archive, self.good_entries())
        manifest = self.validate()
        self.assertEqual(manifest["codex_version"], VERSION)
        self.assertEqual(self.destination.read_bytes(), BINARY)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o755)

    def test_rejects_version_patch_recipe_source_or_platform_mismatch(self) -> None:
        for key, value in (
            ("codex_version", "0.155.0"),
            ("patch_sha256", "0" * 64),
            ("recipe_sha256", "0" * 64),
            ("source_commit", "0" * 40),
            ("architecture", "x86_64"),
            ("runtime_source_ref", "refs/heads/agent/test"),
        ):
            with self.subTest(key=key):
                value_manifest = manifest_for()
                value_manifest[key] = value
                with self.assertRaises(PrebuiltReleaseError):
                    validate_manifest(
                        value_manifest,
                        version=VERSION,
                        source_commit=SOURCE_COMMIT,
                        patch_name=PATCH_NAME,
                        patch_sha256=PATCH_SHA,
                        recipe_sha256=RECIPE_SHA,
                        running_os="15.4.1",
                    )

    def test_rejects_incompatible_minimum_macos_version(self) -> None:
        make_archive(self.archive, self.good_entries())
        with self.assertRaisesRegex(PrebuiltReleaseError, "requires macOS 15.0"):
            self.validate(running_os="14.7.2")
        self.assertFalse(self.destination.exists())

    def test_rejects_wrong_binary_checksum_before_writing_destination(self) -> None:
        wrong = b"tampered binary"
        value_manifest = manifest_for()
        encoded = (json.dumps(value_manifest) + "\n").encode()
        make_archive(self.archive, [("codex", wrong, "file"), ("manifest.json", encoded, "file")])
        with self.assertRaisesRegex(PrebuiltReleaseError, "SHA-256"):
            self.validate()
        self.assertFalse(self.destination.exists())

    def test_attestation_failure_never_extracts_or_executes_archive(self) -> None:
        def download_asset(_tag: str, asset: str, destination: Path, _limit: int) -> None:
            if asset.endswith(".tar.gz"):
                make_archive(destination, self.good_entries())
            else:
                destination.write_text("{}\n")

        with (
            mock.patch("prebuilt_release.resolve_source_tag_commit", return_value=SOURCE_COMMIT),
            mock.patch("prebuilt_release._download_release_asset", side_effect=download_asset),
            mock.patch(
                "prebuilt_release.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1, ["gh", "attestation", "verify"], stderr="attestation rejected"
                ),
            ) as gh_run,
            mock.patch("prebuilt_release.validate_and_extract_archive") as extract,
        ):
            with self.assertRaisesRegex(PrebuiltReleaseError, "attestation rejected"):
                download_attested_release(
                    VERSION,
                    self.destination,
                    patch_name=PATCH_NAME,
                    patch_sha256=PATCH_SHA,
                    recipe_sha256=RECIPE_SHA,
                    running_os="15.4.1",
                )
        self.assertEqual(gh_run.call_count, 1)
        command = gh_run.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "attestation", "verify"])
        self.assertIn("--repo", command)
        self.assertIn(REPOSITORY, command)
        self.assertIn("--signer-workflow", command)
        self.assertIn(SIGNER_WORKFLOW, command)
        self.assertIn("--source-ref", command)
        self.assertIn(SOURCE_REF, command)
        extract.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_recipe_digest_is_stable_for_installed_runtime_layout(self) -> None:
        runtime_root = self.root / "runtime-source"
        (runtime_root / "patches").mkdir(parents=True)
        for relative, content in (
            ("router_manager.py", b"manager"),
            ("prebuilt_release.py", b"release helper"),
            ("patches/provider_route.rs", b"route patch"),
        ):
            (runtime_root / relative).write_bytes(content)
        installed_root = self.root / "installed" / "lib"
        (installed_root / "patches").mkdir(parents=True)
        for relative in (
            "router_manager.py",
            "prebuilt_release.py",
            "patches/provider_route.rs",
        ):
            (installed_root / relative).write_bytes((runtime_root / relative).read_bytes())
        self.assertEqual(build_recipe_sha256(runtime_root), build_recipe_sha256(installed_root))

    def test_rejects_traversal_symlink_duplicate_and_unexpected_members(self) -> None:
        manifest = (json.dumps(manifest_for()) + "\n").encode()
        cases = (
            [("../codex", BINARY, "file"), ("manifest.json", manifest, "file")],
            [("codex", b"", "symlink"), ("manifest.json", manifest, "file")],
            [("codex", BINARY, "file"), ("codex", BINARY, "file"), ("manifest.json", manifest, "file")],
            [("codex", BINARY, "file"), ("manifest.json", manifest, "file"), ("extra", b"x", "file")],
        )
        for entries in cases:
            with self.subTest(entries=[name for name, _, _ in entries]):
                make_archive(self.archive, entries)
                with self.assertRaises(PrebuiltReleaseError):
                    self.validate()
                self.destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
