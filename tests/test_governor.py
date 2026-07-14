import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "agent-call-governor" / "scripts" / "governor.py"
sys.path.insert(0, str(SCRIPT.parent))
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
    def test_packaged_policy_matches_legacy_module(self):
        from agent_call_governor_runtime import evaluate, fingerprint

        self.assertIs(evaluate, governor.evaluate)
        self.assertIs(fingerprint, governor.fingerprint)
        self.assertEqual(fingerprint(proposal()), governor.fingerprint(proposal()))

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

    def test_fingerprint_ignores_only_object_key_order(self):
        left = proposal(material_inputs={"files": ["B.py", "a.py"], "query": "Find BUG"})
        right = proposal(material_inputs={"query": "Find BUG", "files": ["B.py", "a.py"]})
        self.assertEqual(governor.fingerprint(left), governor.fingerprint(right))

    def test_fingerprint_preserves_list_order_and_string_case(self):
        ordered = proposal(material_inputs={"steps": ["drop-old", "create-new"]})
        reversed_steps = proposal(material_inputs={"steps": ["create-new", "drop-old"]})
        upper_case = proposal(material_inputs={"branch": "Feature/A"})
        lower_case = proposal(material_inputs={"branch": "feature/a"})

        self.assertNotEqual(governor.fingerprint(ordered), governor.fingerprint(reversed_steps))
        self.assertNotEqual(governor.fingerprint(upper_case), governor.fingerprint(lower_case))

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

    def test_strict_high_risk_allows_one_changed_strategy_retry(self):
        result = governor.evaluate({
            "proposal": proposal(changed_strategy="Use an independent authoritative source"),
            "profile": "strict",
            "quality_risk": "high",
            "history": [{"progress": "low_progress"}],
        })
        self.assertTrue(result["allowed"])
        self.assertEqual(result["effective_limit"], 2)
        self.assertEqual(result["matching_history_count"], 1)
        self.assertEqual(result["remaining_after_call"], 0)

    def test_history_count_defaults_budget_used_and_blocks_overrun(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "history": [
                {"progress": "material_progress"},
                {"progress": "material_progress"},
            ],
        })
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "budget_exhausted")
        self.assertEqual(result["matching_history_count"], 2)

    def test_rejects_budget_used_lower_than_history_count(self):
        with self.assertRaisesRegex(ValueError, "lower than matching history count"):
            governor.evaluate({
                "proposal": proposal(),
                "history": [{"progress": "material_progress"}],
                "budget": {"used": 0},
            })

    def test_history_budget_kind_keeps_ledgers_separate(self):
        result = governor.evaluate({
            "proposal": proposal(),
            "history": [
                {"budget_kind": "direct-tool", "progress": "material_progress"},
                {"budget_kind": "agent", "progress": "material_progress"},
            ],
        })
        self.assertEqual(result["matching_history_count"], 1)
        self.assertTrue(result["allowed"])

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
