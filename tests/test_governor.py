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
    def test_balanced_profile_is_default(self):
        result = governor.evaluate({"proposal": proposal()})
        self.assertTrue(result["allowed"])
        self.assertEqual(result["profile"], "balanced")
        self.assertEqual(result["effective_limit"], 2)

    def test_explicit_limit_above_profile_floor_is_preserved(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "profile": "strict",
            "quality_risk": "low",
            "budget": {"limit": 4, "used": 1},
        })
        self.assertEqual(result["effective_limit"], 4)
        self.assertFalse(result["budget_floor_applied"])
        self.assertEqual(result["remaining_after_call"], 2)

    def test_high_risk_floor_prevents_under_call(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "profile": "strict",
            "quality_risk": "high",
            "budget": {"limit": 0, "used": 0},
        })
        self.assertTrue(result["allowed"])
        self.assertEqual(result["effective_limit"], 2)
        self.assertTrue(result["budget_floor_applied"])

    def test_low_risk_zero_agent_budget_is_not_raised(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "profile": "balanced",
            "quality_risk": "low",
            "budget": {"limit": 0, "used": 0},
        })
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "budget_exhausted")
        self.assertEqual(result["effective_limit"], 0)

    def test_direct_tool_and_agent_limits_are_separate(self):
        agent = governor.evaluate({"proposal": proposal(), "quality_risk": "high"})
        direct = governor.evaluate({
            "proposal": proposal(route="direct-tool"),
            "quality_risk": "high",
            "budget": {"kind": "direct-tool"},
        })
        self.assertEqual(agent["effective_limit"], 3)
        self.assertEqual(direct["effective_limit"], 8)

    def test_blocks_duplicate(self):
        current = proposal()
        result = governor.evaluate({
            "proposal": current,
            "history": [{"fingerprint": governor.fingerprint(current), "progress": "material_progress"}],
        })
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "duplicate_fingerprint")

    def test_duplicate_precedes_mandatory_exception(self):
        current = proposal(mandatory_reason="user_requested_action")
        result = governor.evaluate({
            "proposal": current,
            "history": [{"fingerprint": governor.fingerprint(current), "progress": "material_progress"}],
        })
        self.assertEqual(result["reason"], "duplicate_fingerprint")

    def test_mandatory_exception_can_exceed_budget(self):
        result = governor.evaluate({
            "proposal": proposal(mandatory_reason="high_stakes"),
            "profile": "strict",
            "budget": {"limit": 1, "used": 5},
        })
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "mandatory_exception")

    def test_fingerprint_ignores_key_and_list_order_and_case(self):
        left = proposal(material_inputs={"Files": ["B.py", "a.py"], "Query": " Find BUG "})
        right = proposal(
            objective="inspect AUTH failures",
            route="SPECIALIST-agent",
            material_inputs={"query": "find bug", "files": ["a.py", "b.py"]},
        )
        self.assertEqual(governor.fingerprint(left), governor.fingerprint(right))

    def test_blocks_exhausted_effective_budget(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "profile": "strict",
            "quality_risk": "low",
            "budget": {"limit": 1, "used": 1},
        })
        self.assertEqual(result["reason"], "budget_exhausted")

    def test_requires_changed_strategy_after_low_progress(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "history": [{"progress": "low_progress"}],
        })
        self.assertEqual(result["reason"], "changed_strategy_required")

    def test_balanced_allows_one_changed_strategy_retry(self):
        result = governor.evaluate({
            "proposal": proposal(changed_strategy="Use a different authoritative source"),
            "history": [{"progress": "low_progress"}],
        })
        self.assertTrue(result["allowed"])

    def test_strict_blocks_after_first_low_progress(self):
        result = governor.evaluate({
            "proposal": proposal(changed_strategy="Use a different source"),
            "profile": "strict",
            "history": [{"progress": "low_progress"}],
        })
        self.assertEqual(result["reason"], "low_progress_retry_exhausted")

    def test_quality_first_allows_two_changed_strategy_retries(self):
        result = governor.evaluate({
            "proposal": proposal(changed_strategy="Recalculate from raw inputs"),
            "profile": "quality-first",
            "history": [{"progress": "low_progress"}, {"progress": "low_progress"}],
        })
        self.assertTrue(result["allowed"])

    def test_blocks_after_sufficient_result(self):
        result = governor.evaluate({
            "proposal": proposal(objective="Write the final report"),
            "history": [{"progress": "sufficient"}],
        })
        self.assertEqual(result["reason"], "stop_condition_already_satisfied")

    def test_requires_ledger_fields(self):
        with self.assertRaisesRegex(ValueError, "capability_gap"):
            governor.evaluate({"proposal": proposal(capability_gap="")})

    def test_rejects_unknown_profile(self):
        with self.assertRaisesRegex(ValueError, "profile must be one of"):
            governor.evaluate({"proposal": proposal(), "profile": "maximum"})


if __name__ == "__main__":
    unittest.main()
