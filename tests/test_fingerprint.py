from __future__ import annotations

import unittest

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

    def test_object_key_order_is_equivalent(self) -> None:
        self.assertEqual(self.build("mcp:lookup", {"a": 1, "b": 2}).digest,
                         self.build("mcp:lookup", {"b": 2, "a": 1}).digest)

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


if __name__ == "__main__":
    unittest.main()
