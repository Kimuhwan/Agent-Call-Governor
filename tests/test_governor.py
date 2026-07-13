import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "agent-call-governor" / "scripts" / "governor.py"
SPEC = importlib.util.spec_from_file_location("governor", SCRIPT)
assert SPEC and SPEC.loader
governor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governor)


def proposal(**overrides):
    value = {
        "objective": "Inspect auth failures",
        "route": "specialist-agent",
        "material_inputs": {"scope": "server/auth"},
        "capability_gap": "Several modules require inspection",
        "expected_new_information": "A source-backed root cause",
        "stop_condition": "A failing path is identified",
    }
    value.update(overrides)
    return value


class GovernorTests(unittest.TestCase):
    def test_allows_new_call_with_budget(self):
        result = governor.evaluate({"proposal": proposal(), "budget": {"limit": 2, "used": 0}})
        self.assertTrue(result["allowed"])
        self.assertEqual(result["remaining_after_call"], 1)

    def test_blocks_duplicate(self):
        current = proposal()
        result = governor.evaluate({
            "proposal": current,
            "history": [{"fingerprint": governor.fingerprint(current), "progress": "material_progress"}],
            "budget": {"limit": 2, "used": 1},
        })
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "duplicate_fingerprint")

    def test_fingerprint_ignores_key_and_list_order_and_case(self):
        left = proposal(material_inputs={"Files": ["B.py", "a.py"], "Query": " Find BUG "})
        right = proposal(
            objective="inspect AUTH failures",
            route="SPECIALIST-agent",
            material_inputs={"query": "find bug", "files": ["a.py", "b.py"]},
        )
        self.assertEqual(governor.fingerprint(left), governor.fingerprint(right))

    def test_blocks_exhausted_budget(self):
        result = governor.evaluate({"proposal": proposal(), "budget": {"limit": 1, "used": 1}})
        self.assertEqual(result["reason"], "budget_exhausted")

    def test_blocks_after_sufficient_result(self):
        result = governor.evaluate({
            "proposal": proposal(objective="Write the final report"),
            "history": [{"progress": "sufficient"}],
        })
        self.assertEqual(result["reason"], "stop_condition_already_satisfied")

    def test_requires_ledger_fields(self):
        with self.assertRaisesRegex(ValueError, "capability_gap"):
            governor.evaluate({"proposal": proposal(capability_gap="")})


if __name__ == "__main__":
    unittest.main()
