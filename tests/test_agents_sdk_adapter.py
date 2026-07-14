from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_call_governor_runtime import CallLedger, CallProposal, GovernedRuntime, GovernanceBlocked
from agent_call_governor_runtime.agents_sdk import (
    GovernedRunner,
    build_function_tool_guardrail,
    build_run_hooks,
)


SDK_AVAILABLE = importlib.util.find_spec("agents") is not None


def proposal(session_id: str = "runner-session") -> CallProposal:
    return CallProposal(
        session_id=session_id,
        objective="Run the customer support workflow",
        route="agents-sdk:runner",
        capability_gap="The application needs an agent workflow",
        expected_new_information="A completed workflow result",
        stop_condition="The SDK run returns",
        material_inputs={"workflow": "support"},
        quality_risk="medium",
    )


class FakeRunner:
    def __init__(self) -> None:
        self.sync_calls = 0
        self.async_calls = 0

    def run_sync(self, starting_agent: object, input: object, **kwargs: object) -> str:
        self.sync_calls += 1
        return "sync-result"

    async def run(self, starting_agent: object, input: object, **kwargs: object) -> str:
        self.async_calls += 1
        return "async-result"


class AgentsSDKAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.ledger = CallLedger(root / "events.sqlite3", root / "events.jsonl")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_core_import_does_not_require_openai_agents(self) -> None:
        import agent_call_governor_runtime

        self.assertTrue(hasattr(agent_call_governor_runtime, "GovernedRuntime"))

    def test_missing_distribution_has_actionable_error(self) -> None:
        runtime = GovernedRuntime(self.ledger)
        with patch(
            "agent_call_governor_runtime.agents_sdk.metadata.version",
            side_effect=metadata.PackageNotFoundError,
        ):
            with self.assertRaisesRegex(RuntimeError, r"pip install .*\[agents\]"):
                build_run_hooks(runtime)

    def test_unsupported_distribution_version_has_actionable_error(self) -> None:
        runtime = GovernedRuntime(self.ledger)
        with patch(
            "agent_call_governor_runtime.agents_sdk.metadata.version",
            return_value="0.19.0",
        ):
            with self.assertRaisesRegex(RuntimeError, r">=0\.18\.2,<0\.19"):
                build_run_hooks(runtime)

    def test_governed_runner_wraps_sync_and_async_sdk_entrypoints(self) -> None:
        fake = FakeRunner()
        runtime = GovernedRuntime(self.ledger, mode="observe")
        runner = GovernedRunner(runtime, runner=fake)

        self.assertEqual(runner.run_sync(proposal("sync"), object(), "secret input"), "sync-result")
        self.assertEqual(
            asyncio.run(runner.run(proposal("async"), object(), "secret input")),
            "async-result",
        )

        self.assertEqual(fake.sync_calls, 1)
        self.assertEqual(fake.async_calls, 1)
        self.assertEqual(self.ledger.events("sync")[-1].phase, "completed")
        self.assertEqual(self.ledger.events("async")[-1].phase, "completed")
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret input", persisted)

    def test_governed_runner_enforces_before_invoking_sdk(self) -> None:
        fake = FakeRunner()
        runtime = GovernedRuntime(self.ledger, mode="enforce")
        runner = GovernedRunner(runtime, runner=fake)
        call = proposal()

        self.assertEqual(runner.run_sync(call, object(), "first"), "sync-result")
        with self.assertRaises(GovernanceBlocked):
            runner.run_sync(call, object(), "duplicate")

        self.assertEqual(fake.sync_calls, 1)

    @unittest.skipUnless(SDK_AVAILABLE, "openai-agents optional extra is not installed")
    def test_run_hooks_match_sdk_base_and_record_safe_lifecycle(self) -> None:
        import agents
        from agents.lifecycle import RunHooksBase

        runtime = GovernedRuntime(
            self.ledger,
            mode="observe",
            source="agents-sdk-hooks",
            warning_handler=lambda _message: None,
        )
        hooks = build_run_hooks(runtime, session_id="sdk-hooks-session")
        self.assertIsInstance(hooks, RunHooksBase)
        first = agents.Agent(name="triage")
        second = agents.Agent(name="specialist")
        context = SimpleNamespace()
        tool_context = SimpleNamespace(
            tool_call_id="tool-call-1",
            tool_arguments='{"query":"secret sdk input"}',
        )
        tool = SimpleNamespace(name="search")

        async def exercise() -> None:
            await hooks.on_agent_start(context, first)
            await hooks.on_llm_start(
                context,
                first,
                "secret system prompt",
                [{"content": "secret model input"}],
            )
            await hooks.on_llm_end(context, first, object())
            await hooks.on_tool_start(tool_context, first, tool)
            await hooks.on_tool_end(tool_context, first, tool, "secret tool result")
            await hooks.on_handoff(context, first, second)
            await hooks.on_agent_start(context, second)
            await hooks.on_agent_end(context, second, "secret agent output")

        asyncio.run(exercise())

        events = self.ledger.events("sdk-hooks-session")
        self.assertEqual(sum(event.phase == "started" for event in events), 4)
        self.assertEqual(sum(event.phase == "completed" for event in events), 4)
        self.assertTrue(any(event.metadata.get("sdk_event") == "handoff" for event in events))
        serialized = json.dumps([event.to_dict() for event in events])
        for secret in (
            "secret system prompt",
            "secret model input",
            "secret sdk input",
            "secret tool result",
            "secret agent output",
        ):
            self.assertNotIn(secret, serialized)

    @unittest.skipUnless(SDK_AVAILABLE, "openai-agents optional extra is not installed")
    def test_function_tool_guardrail_uses_supported_sdk_veto(self) -> None:
        import agents
        from agents.tool_context import ToolContext

        runtime = GovernedRuntime(self.ledger, mode="enforce")
        guardrail = build_function_tool_guardrail(
            runtime,
            session_id="guardrail-session",
        )
        agent = agents.Agent(name="support")
        context = ToolContext(
            context=None,
            tool_name="lookup_order",
            tool_call_id="tool-call-1",
            tool_arguments='{"email":"secret@example.com"}',
        )
        data = agents.ToolInputGuardrailData(context=context, agent=agent)

        allowed = asyncio.run(guardrail.run(data))
        blocked = asyncio.run(guardrail.run(data))

        self.assertEqual(allowed.behavior["type"], "allow")
        self.assertEqual(blocked.behavior["type"], "reject_content")
        self.assertEqual(
            [event.phase for event in self.ledger.events("guardrail-session")],
            ["proposed", "started", "proposed", "blocked"],
        )
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret@example.com", persisted)

    @unittest.skipUnless(SDK_AVAILABLE, "openai-agents optional extra is not installed")
    def test_run_hooks_reject_enforce_mode_in_favor_of_guardrails(self) -> None:
        with self.assertRaisesRegex(ValueError, "function-tool guardrail"):
            build_run_hooks(GovernedRuntime(self.ledger, mode="enforce"))

    @unittest.skipUnless(SDK_AVAILABLE, "openai-agents optional extra is not installed")
    def test_run_hooks_pair_tools_without_tool_call_id(self) -> None:
        import agents

        runtime = GovernedRuntime(self.ledger, mode="observe")
        hooks = build_run_hooks(runtime, session_id="no-tool-id")
        agent = agents.Agent(name="operator")
        context = SimpleNamespace()
        tool = SimpleNamespace(name="hosted_tool")

        async def exercise() -> None:
            await hooks.on_tool_start(context, agent, tool)
            await hooks.on_tool_end(context, agent, tool, "private result")

        asyncio.run(exercise())

        events = self.ledger.events("no-tool-id")
        self.assertEqual([event.phase for event in events], ["proposed", "started", "completed"])
        self.assertNotIn("private result", json.dumps([event.to_dict() for event in events]))


if __name__ == "__main__":
    unittest.main()
