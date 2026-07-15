from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_call_governor_runtime import __version__
from scripts.build_plugin_archive import (
    _is_reparse,
    build_archive,
    collect_release_files,
)
from scripts.check_versions import collect_versions
from scripts.write_checksums import write_checksums


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def test_all_product_versions_are_exactly_equal(self) -> None:
        self.assertEqual(
            collect_versions(ROOT),
            {"package": "0.3.0", "plugin": "0.3.0", "changelog": "0.3.0"},
        )
        self.assertEqual(__version__, "0.3.0")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.3.0] - 2026-07-15", changelog)
        self.assertNotIn("## [0.3.0] - Unreleased", changelog)

    def test_plugin_release_metadata_is_explicitly_observe_warn(self) -> None:
        plugin = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertIn("observe/warn", plugin["description"].lower())
        self.assertIn("observe", plugin["interface"]["shortDescription"].lower())
        self.assertIn("warn", plugin["interface"]["shortDescription"].lower())
        self.assertIn("observe/warn", plugin["interface"]["longDescription"].lower())

    def test_plugin_archive_is_tracked_only_deterministic_and_safe(self) -> None:
        canary = ROOT / "hooks" / ".acg-release-test-canary.txt"
        self.assertFalse(canary.exists(), "test must not overwrite an existing canary")
        try:
            canary.write_text("non-secret release test canary", encoding="utf-8")
            with tempfile.TemporaryDirectory() as directory:
                first = Path(directory) / "first.zip"
                second = Path(directory) / "second.zip"

                build_archive(ROOT, first)
                build_archive(ROOT, second)

                self.assertEqual(first.read_bytes(), second.read_bytes())
                self.assertNotIn(b"non-secret release test canary", first.read_bytes())
                with zipfile.ZipFile(first) as archive:
                    names = archive.namelist()
                    metadata = archive.infolist()
                    self.assertIsNone(archive.testzip())

                expected = {
                    "agent-call-governor/.agents/plugins/marketplace.json",
                    "agent-call-governor/.codex-plugin/plugin.json",
                    "agent-call-governor/hooks/dispatch.py",
                    "agent-call-governor/hooks/hooks.json",
                    "agent-call-governor/skills/agent-call-governor/SKILL.md",
                    "agent-call-governor/skills/agent-call-governor/agents/openai.yaml",
                    "agent-call-governor/schemas/event-v2.schema.json",
                    "agent-call-governor/schemas/policy-facts-v1.schema.json",
                    "agent-call-governor/docs/architecture.md",
                    "agent-call-governor/docs/releases/v0.3.0.md",
                    "agent-call-governor/README.md",
                    "agent-call-governor/README.ko.md",
                    "agent-call-governor/LICENSE",
                    "agent-call-governor/SECURITY.md",
                    "agent-call-governor/install.ps1",
                    "agent-call-governor/install.sh",
                }
                self.assertTrue(expected.issubset(set(names)))
                self.assertNotIn("agent-call-governor/hooks/.acg-release-test-canary.txt", names)
                self.assertEqual(names, sorted(names))
                self.assertEqual(len(names), len(set(names)))
                self.assertTrue(all(name.startswith("agent-call-governor/") for name in names))
                self.assertTrue(
                    all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
                )
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in metadata))
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in metadata))
                self.assertTrue(all(info.create_system == 3 for info in metadata))
                self.assertTrue(all(info.extra == b"" and info.comment == b"" for info in metadata))
                self.assertFalse(
                    any(
                        part in {".git", ".github", ".superpowers", "superpowers", "dist", "build", "__pycache__"}
                        for name in names
                        for part in Path(name).parts
                    )
                )
                self.assertFalse(
                    any(
                        name.lower().endswith(
                            (".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".jsonl", ".env", "-wal", "-shm")
                        )
                        for name in names
                    )
                )
        finally:
            canary.unlink(missing_ok=True)

    def test_plugin_archive_uses_canonical_index_bytes_across_eol_checkouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            root = sandbox / "repo"
            output_root = sandbox / "artifacts"
            root.mkdir()
            output_root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "core.autocrlf", "false"],
                check=True,
                capture_output=True,
            )
            readme = root / "README.md"
            readme.write_bytes(b"line one\nline two\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "--", "README.md"],
                check=True,
                capture_output=True,
            )

            first = output_root / "lf.zip"
            build_archive(root, first)
            readme.write_bytes(b"line one\r\nline two\r\n")
            second = output_root / "crlf.zip"
            build_archive(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(second) as archive:
                self.assertEqual(
                    archive.read("agent-call-governor/README.md"),
                    b"line one\nline two\n",
                )

    def test_plugin_archive_pins_one_index_snapshot_during_concurrent_adds(self) -> None:
        archive_module = importlib.import_module("scripts.build_plugin_archive")
        real_blob = archive_module._git_blob_bytes
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            root = sandbox / "repo"
            output_root = sandbox / "artifacts"
            docs = root / "docs"
            docs.mkdir(parents=True)
            output_root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "core.autocrlf", "false"],
                check=True,
                capture_output=True,
            )
            readme = root / "README.md"
            architecture = docs / "architecture.md"
            readme.write_bytes(b"A-old\n")
            architecture.write_bytes(b"B-old\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "--", "README.md", "docs/architecture.md"],
                check=True,
                capture_output=True,
            )

            call_count = 0

            def mutate_index_between_blob_reads(repo_root: Path, token: object) -> bytes:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    readme.write_bytes(b"A-new\n")
                    subprocess.run(
                        ["git", "-C", str(root), "add", "--", "README.md"],
                        check=True,
                        capture_output=True,
                    )
                elif call_count == 2:
                    readme.write_bytes(b"A-old\n")
                    architecture.write_bytes(b"B-new\n")
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(root),
                            "add",
                            "--",
                            "README.md",
                            "docs/architecture.md",
                        ],
                        check=True,
                        capture_output=True,
                    )
                return real_blob(repo_root, token)

            output = output_root / "plugin.zip"
            with mock.patch(
                "scripts.build_plugin_archive._git_blob_bytes",
                side_effect=mutate_index_between_blob_reads,
            ):
                build_archive(root, output)

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("agent-call-governor/README.md"), b"A-old\n")
                self.assertEqual(
                    archive.read("agent-call-governor/docs/architecture.md"),
                    b"B-old\n",
                )

    def test_git_enumeration_is_nul_delimited_and_shell_free(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=b"README.md\0", stderr=b"")
        with mock.patch("scripts.build_plugin_archive.subprocess.run", return_value=completed) as run, mock.patch(
            "scripts.build_plugin_archive._git_index_snapshot",
            return_value=[SimpleNamespace(path="README.md", object_id="a" * 40)],
        ), mock.patch("scripts.build_plugin_archive._git_blob_bytes", return_value=b"canonical README bytes"):
            files = collect_release_files(ROOT)

        self.assertEqual(files, [ROOT / "README.md"])
        run.assert_called_once_with(
            ["git", "-C", str(ROOT.resolve()), "ls-files", "-z", "--"],
            shell=False,
            check=True,
            capture_output=True,
        )

    def test_excluded_and_private_state_names_are_filtered_before_io(self) -> None:
        names = [
            "README.md",
            ".github/workflows/validate.yml",
            "docs/superpowers/private.md",
            "hooks/__pycache__/dispatch.pyc",
            "hooks/state.sqlite3",
            "hooks/state.db-wal",
            "hooks/state.db-shm",
            "hooks/events.jsonl",
            "hooks/config.env",
            "skills/agent-call-governor/.env",
            "skills/agent-call-governor/private.key",
            "dist/stale.whl",
        ]

        self.assertEqual(collect_release_files(ROOT, tracked_names=names), [ROOT / "README.md"])

    def test_candidate_validation_rejects_unsafe_missing_and_duplicate_names(self) -> None:
        unsafe = (
            "../README.md",
            "/absolute.txt",
            "C:/absolute.txt",
            "./README.md",
            "hooks//dispatch.py",
            "hooks/../README.md",
            "hooks\\..\\README.md",
            "hooks/bad\nname.py",
        )
        for name in unsafe:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    collect_release_files(ROOT, tracked_names=[name])

        with self.assertRaisesRegex(ValueError, "duplicate"):
            collect_release_files(ROOT, tracked_names=["README.md", "readme.md"])
        with self.assertRaisesRegex(ValueError, "missing"):
            collect_release_files(ROOT, tracked_names=["hooks/missing.py"])
        with self.assertRaisesRegex(ValueError, "regular file"):
            collect_release_files(ROOT, tracked_names=["hooks"])

    def test_candidate_validation_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            root = sandbox / "repo"
            hooks = root / "hooks"
            hooks.mkdir(parents=True)
            outside = sandbox / "outside.py"
            outside.write_text("outside", encoding="utf-8")
            link = hooks / "link.py"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "link or reparse"):
                collect_release_files(root, tracked_names=["hooks/link.py"])

    def test_candidate_validation_rejects_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks = root / "hooks"
            hooks.mkdir()
            regular = hooks / "regular.py"
            regular.write_text("safe", encoding="utf-8")
            with mock.patch("scripts.build_plugin_archive._is_reparse", return_value=True):
                with self.assertRaisesRegex(ValueError, "link or reparse"):
                    collect_release_files(root, tracked_names=["hooks/regular.py"])

        marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        self.assertTrue(_is_reparse(SimpleNamespace(st_file_attributes=marker)))

    def test_archive_output_cannot_alias_a_release_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks = root / "hooks"
            hooks.mkdir()
            source = hooks / "dispatch.py"
            source.write_bytes(b"tracked source")
            with mock.patch(
                "scripts.build_plugin_archive._git_tracked_names",
                return_value=["hooks/dispatch.py"],
            ), mock.patch(
                "scripts.build_plugin_archive._git_index_snapshot",
                return_value=[
                    SimpleNamespace(path="hooks/dispatch.py", object_id="a" * 40)
                ],
            ), mock.patch(
                "scripts.build_plugin_archive._git_blob_bytes",
                return_value=b"tracked source",
            ):
                with self.assertRaisesRegex(ValueError, "output cannot replace"):
                    build_archive(root, source)
            self.assertEqual(source.read_bytes(), b"tracked source")

    def test_failed_archive_build_preserves_final_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plugin.zip"
            output.write_bytes(b"existing-good-archive")
            with mock.patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    build_archive(ROOT, output)

            self.assertEqual(output.read_bytes(), b"existing-good-archive")
            self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])

    def test_checksum_asset_is_sorted_exact_and_has_no_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.whl"
            second = root / "b.tar.gz"
            output = root / "SHA256SUMS.txt"
            first.write_bytes(b"wheel")
            second.write_bytes(b"source")

            write_checksums([second, first], output)

            expected = (
                f"{hashlib.sha256(b'wheel').hexdigest()}  a.whl\n"
                f"{hashlib.sha256(b'source').hexdigest()}  b.tar.gz\n"
            )
            self.assertEqual(output.read_bytes(), expected.encode("utf-8"))
            self.assertNotIn("SHA256SUMS.txt", output.read_text(encoding="utf-8"))

    def test_checksum_accepts_an_atomically_published_plugin_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "agent-call-governor-plugin-0.3.0.zip"
            output = root / "SHA256SUMS.txt"
            built = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_plugin_archive.py",
                    "--output",
                    str(archive),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            checksummed = subprocess.run(
                [
                    sys.executable,
                    "scripts/write_checksums.py",
                    "--output",
                    str(output),
                    str(archive),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(checksummed.returncode, 0, checksummed.stderr)

            expected = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"{expected}  {archive.name}\n",
            )

    def test_checksum_rejects_input_changed_after_validation(self) -> None:
        checksum_module = importlib.import_module("scripts.write_checksums")
        real_validate = checksum_module._validated_inputs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.whl"
            artifact.write_bytes(b"original")
            output = root / "SHA256SUMS.txt"

            def validate_then_mutate(files: object, destination: Path) -> object:
                validated = real_validate(files, destination)
                artifact.write_bytes(b"mutated-after-validation")
                return validated

            with mock.patch(
                "scripts.write_checksums._validated_inputs",
                side_effect=validate_then_mutate,
            ):
                with self.assertRaisesRegex(ValueError, "changed"):
                    write_checksums([artifact], output)
            self.assertFalse(output.exists())

    def test_checksum_revalidates_all_inputs_after_every_hash(self) -> None:
        checksum_module = importlib.import_module("scripts.write_checksums")
        real_sha256 = checksum_module._sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.whl"
            second = root / "b.tar.gz"
            output = root / "SHA256SUMS.txt"
            first.write_bytes(b"first-original")
            second.write_bytes(b"second-original")

            def mutate_first_before_hashing_second(item: object) -> str:
                if item.path == second:
                    first.write_bytes(b"first-mutated-after-hash")
                return real_sha256(item)

            with mock.patch(
                "scripts.write_checksums._sha256",
                side_effect=mutate_first_before_hashing_second,
            ):
                with self.assertRaisesRegex(ValueError, "changed"):
                    write_checksums([first, second], output)
            self.assertFalse(output.exists())

    def test_checksum_refuses_output_inputs_duplicates_missing_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.whl"
            artifact.write_bytes(b"artifact")
            output = root / "SHA256SUMS.txt"
            output.write_text("old", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "output"):
                write_checksums([artifact, output], output)
            with self.assertRaisesRegex(ValueError, "duplicate input"):
                write_checksums([artifact, artifact], output)
            with self.assertRaisesRegex(ValueError, "missing"):
                write_checksums([root / "missing.whl"], output)
            with self.assertRaisesRegex(ValueError, "regular file"):
                write_checksums([root], output)

            first_dir = root / "one"
            second_dir = root / "two"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "same.whl").write_bytes(b"one")
            (second_dir / "same.whl").write_bytes(b"two")
            with self.assertRaisesRegex(ValueError, "duplicate basename"):
                write_checksums([first_dir / "same.whl", second_dir / "same.whl"], output)

            self.assertEqual(output.read_text(encoding="utf-8"), "old")

    def test_failed_checksum_replace_preserves_final_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.whl"
            artifact.write_bytes(b"artifact")
            output = root / "SHA256SUMS.txt"
            output.write_bytes(b"existing-good-checksums")

            with mock.patch("scripts.write_checksums.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    write_checksums([artifact], output)

            self.assertEqual(output.read_bytes(), b"existing-good-checksums")
            self.assertEqual(list(root.glob(f".{output.name}.*.tmp")), [])

    def test_failed_checksum_write_preserves_final_and_cleans_temporary_file(self) -> None:
        real_fdopen = os.fdopen

        class FailingWriter:
            def __init__(self, descriptor: int, *args: object, **kwargs: object) -> None:
                self.handle = real_fdopen(descriptor, *args, **kwargs)

            def __enter__(self) -> "FailingWriter":
                return self

            def __exit__(self, *args: object) -> None:
                self.handle.close()

            def writelines(self, lines: object) -> None:
                raise OSError("forced checksum write failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.whl"
            artifact.write_bytes(b"artifact")
            output = root / "SHA256SUMS.txt"
            output.write_bytes(b"existing-good-checksums")

            with mock.patch("scripts.write_checksums.os.fdopen", side_effect=FailingWriter):
                with self.assertRaisesRegex(OSError, "forced checksum write failure"):
                    write_checksums([artifact], output)

            self.assertEqual(output.read_bytes(), b"existing-good-checksums")
            self.assertEqual(list(root.glob(f".{output.name}.*.tmp")), [])

    def test_release_notes_state_exact_scope_privacy_and_evidence_limits(self) -> None:
        notes = (ROOT / "docs" / "releases" / "v0.3.0.md").read_text(encoding="utf-8")
        for heading in (
            "## Highlights",
            "## Compatibility",
            "## Privacy",
            "## Measured validation",
            "## Known limitations",
            "## Install/update",
            "## Checksums",
        ):
            self.assertIn(heading, notes)

        self.assertIn("local-first observe/warn plugin", notes)
        self.assertIn("not a firewall", notes)
        self.assertIn("10/10", notes)
        self.assertIn(
            "Policy replay is deferred to v0.4 after schema-v2 logs exist.",
            notes,
        )
        self.assertIn(
            "Current deterministic, instrumentation, and small matched A/B results are directional evidence, not production benchmarks.",
            notes,
        )
        self.assertIn("legacy-v1 rows", notes)
        self.assertIn("budget and progress", notes)
        self.assertIn("new exact-duplicate epoch", notes)
        self.assertIn("host session/call/turn/parent identifiers", notes)
        self.assertNotIn("transcripts, identifiers,", notes)
        self.assertIn("SHA256SUMS.txt", notes)
        self.assertIn("exact hook hash", notes)
        self.assertIn("marketplace entry follows mutable `main`", notes)
        self.assertIn("Immutability applies to the `v0.3.0` tag and release assets", notes)
        self.assertIn("--db <path-to-events.sqlite3>", notes)
        self.assertNotIn("private vulnerability reporting is enabled", notes.lower())

    def test_packaged_docs_do_not_use_links_to_excluded_archive_files(self) -> None:
        for path in (
            ROOT / "README.md",
            ROOT / "README.ko.md",
            ROOT / "docs" / "releases" / "v0.3.0.md",
        ):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("](pyproject.toml)", text)
                self.assertNotIn("](evals/", text)
                self.assertNotIn("](../../evals/", text)

    def test_both_readmes_include_the_instrumentation_validation_command(self) -> None:
        for path in (ROOT / "README.md", ROOT / "README.ko.md"):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("python evals/run_instrumentation_evals.py", text)

    def test_ci_runs_release_contract_on_current_action_majors(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn('"3.14"', workflow)
        self.assertIn("python evals/run_instrumentation_evals.py", workflow)
        self.assertIn("python scripts/check_versions.py", workflow)
        self.assertIn("tests.test_release_package", workflow)
        self.assertIn("python scripts/build_plugin_archive.py", workflow)
        for artifact in (
            "dist/agent_call_governor_runtime-0.3.0-py3-none-any.whl",
            "dist/agent_call_governor_runtime-0.3.0.tar.gz",
            "dist/agent-call-governor-plugin-0.3.0.zip",
        ):
            self.assertIn(artifact, workflow)


if __name__ == "__main__":
    unittest.main()
