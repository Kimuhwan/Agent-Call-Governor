from __future__ import annotations

import unittest

from agent_call_governor_runtime import CallProposal, fingerprint as policy_fingerprint
from agent_call_governor_runtime.fingerprint import FINGERPRINT_VERSION, build_fingerprint


class FingerprintTests(unittest.TestCase):
    def build(self, route: str, inputs: dict, *, cwd: str | None = None, state: str | None = None):
        return build_fingerprint(
            objective="Verify a release candidate",
            route=route,
            material_inputs=inputs,
            cwd=cwd,
            tool_version="host-v1",
            state_token=state,
        )

    @staticmethod
    def proposal(**overrides) -> CallProposal:
        values = {
            "session_id": "fingerprint-session",
            "objective": "Verify a release candidate",
            "route": "mcp:lookup",
            "capability_gap": "Release evidence is missing",
            "expected_new_information": "The verified release state",
            "stop_condition": "The release state is verified",
            "material_inputs": {"release": "v0.3.0"},
        }
        values.update(overrides)
        return CallProposal(**values)

    def test_object_key_order_is_equivalent(self) -> None:
        self.assertEqual(self.build("mcp:lookup", {"a": 1, "b": 2}).digest,
                         self.build("mcp:lookup", {"b": 2, "a": 1}).digest)

    def test_exact_file_read_repeats_same_fingerprint(self) -> None:
        first = self.build("Read", {"path": "src/app.py"}, cwd="C:/repo")
        second = self.build("Read", {"path": "src/app.py"}, cwd="C:/repo")
        self.assertEqual(first.digest, second.digest)

    def test_exact_bash_repeat_same_fingerprint(self) -> None:
        first = self.build("Bash", {"command": "git status --short"}, cwd="C:/repo")
        second = self.build("Bash", {"command": "git status --short"}, cwd="C:/repo")
        self.assertEqual(first.digest, second.digest)

    def test_list_order_case_and_whitespace_are_material(self) -> None:
        self.assertNotEqual(self.build("mcp:lookup", {"q": ["A", "b"]}).digest,
                            self.build("mcp:lookup", {"q": ["b", "A"]}).digest)
        self.assertNotEqual(self.build("Bash", {"command": "git status"}).digest,
                            self.build("Bash", {"command": "Git status"}).digest)
        self.assertNotEqual(self.build("Bash", {"command": "git  status"}).digest,
                            self.build("Bash", {"command": "git status"}).digest)

    def test_bash_direction_and_cwd_are_material(self) -> None:
        self.assertNotEqual(self.build("Bash", {"command": "git diff main..HEAD"}, cwd="C:/a").digest,
                            self.build("Bash", {"command": "git diff HEAD..main"}, cwd="C:/a").digest)
        self.assertNotEqual(self.build("Bash", {"command": "git status"}, cwd="C:/a").digest,
                            self.build("Bash", {"command": "git status"}, cwd="C:/b").digest)

    def test_exact_patch_and_state_token_are_material(self) -> None:
        first = self.build("apply_patch", {"patch": "*** Add File: a\n+x"}, cwd="C:/repo")
        second = self.build("apply_patch", {"patch": "*** Add File: a\n+y"}, cwd="C:/repo")
        replay = self.build("apply_patch", {"patch": "*** Add File: a\n+x"}, cwd="C:/repo", state="after-read")
        self.assertNotEqual(first.digest, second.digest)
        self.assertNotEqual(first.digest, replay.digest)

    def test_material_call_id_argument_is_not_stripped(self) -> None:
        first = self.build("mcp:lookup", {"call_id": "one", "business_id": "A"})
        second = self.build("mcp:lookup", {"call_id": "two", "business_id": "A"})
        self.assertNotEqual(first.digest, second.digest)

    def test_result_contains_only_digests_and_versions(self) -> None:
        result = self.build("mcp:lookup", {"query": "private customer"})
        self.assertEqual(result.version, FINGERPRINT_VERSION)
        self.assertEqual(FINGERPRINT_VERSION, 2)
        self.assertTrue(result.digest.startswith("sha256:"))
        self.assertTrue(result.input_digest.startswith("sha256:"))
        self.assertTrue(result.objective_digest.startswith("sha256:"))
        self.assertNotIn("private customer", repr(result))

    def test_optional_fingerprint_context_requires_text_or_none(self) -> None:
        for field_name in ("cwd", "tool_version", "state_token"):
            with self.subTest(boundary="builder", field=field_name):
                with self.assertRaisesRegex(ValueError, rf"{field_name} must be a string or null"):
                    build_fingerprint(
                        objective="Verify a release candidate",
                        route="mcp:lookup",
                        material_inputs={},
                        **{field_name: 1},
                    )
            with self.subTest(boundary="model", field=field_name):
                with self.assertRaisesRegex(ValueError, rf"{field_name} must be a string or null"):
                    self.proposal(metadata={field_name: 1})

    def test_public_builder_rejects_non_mapping_material_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "material_inputs must be an object"):
            build_fingerprint(
                objective="Verify a release candidate",
                route="mcp:lookup",
                material_inputs=[],
            )

    def test_public_builder_rejects_utf8_unencodable_text(self) -> None:
        base = {
            "objective": "Verify a release candidate",
            "route": "mcp:lookup",
            "material_inputs": {},
        }
        cases = {
            "objective": {**base, "objective": "\ud800"},
            "route": {**base, "route": "\ud800"},
            "cwd": {**base, "cwd": "\ud800"},
            "tool_version": {**base, "tool_version": "\ud800"},
            "state_token": {**base, "state_token": "\ud800"},
            "material_inputs": {**base, "material_inputs": {"nested": ["\ud800"]}},
        }
        for field_name, arguments in cases.items():
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(ValueError, rf"{field_name}.*UTF-8"):
                    build_fingerprint(**arguments)

    def test_call_proposal_rejects_utf8_unencodable_fingerprint_input(self) -> None:
        with self.assertRaisesRegex(ValueError, r"material_inputs.*UTF-8"):
            self.proposal(material_inputs={"nested": ["\ud800"]})
        with self.assertRaisesRegex(ValueError, r"metadata.*UTF-8"):
            self.proposal(metadata={"state_token": "\ud800"})

    def test_call_proposal_caches_metadata_aware_policy_fingerprint(self) -> None:
        metadata = {
            "cwd": "C:/repo",
            "tool_version": "host-v1",
            "state_token": "after-read",
        }
        proposal = self.proposal(metadata=metadata)

        self.assertIs(proposal.fingerprint_result, proposal.fingerprint_result)
        self.assertEqual(proposal.fingerprint, proposal.fingerprint_result.digest)
        self.assertEqual(proposal.fingerprint, policy_fingerprint(proposal.to_policy_proposal()))

        replacements = {
            "cwd": "C:/other",
            "tool_version": "host-v2",
            "state_token": "after-write",
        }
        for field_name, replacement in replacements.items():
            with self.subTest(field=field_name):
                changed = self.proposal(metadata={**metadata, field_name: replacement})
                self.assertNotEqual(proposal.fingerprint, changed.fingerprint)

    def test_v2_golden_digest_is_stable(self) -> None:
        result = build_fingerprint(
            objective="Verify a release candidate",
            route="Bash",
            material_inputs={
                "command": "git status",
                "paths": ["B.py", "a.py"],
                "call_id": "tool-7",
            },
            cwd="C:/repo",
            tool_version="host-v1",
            state_token="after-read",
        )

        self.assertEqual(
            result.digest,
            "sha256:b6064d6206c4d96bb94cd6dd3ad80a3f239d0af8f80e9d13a096a04b078f3f71",
        )


if __name__ == "__main__":
    unittest.main()
