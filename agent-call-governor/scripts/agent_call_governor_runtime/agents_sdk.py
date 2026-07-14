"""Optional OpenAI Agents SDK adapters loaded only when explicitly requested."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

from .models import CallHandle, CallProposal
from .runtime import GovernedRuntime, GovernanceBlocked


_SUPPORTED_VERSION = re.compile(r"^0\.18\.(\d+)(?:$|[.+-])")


@dataclass(frozen=True)
class SDKHookCall:
    """Privacy-safe descriptor passed to a custom lifecycle proposal factory."""

    kind: str
    session_id: str
    call_id: str
    route: str
    agent_name: str
    material_fingerprint: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class GovernedRunner:
    """Application-owned enforcement around complete Agents SDK runs."""

    def __init__(self, runtime: GovernedRuntime, *, runner: Any | None = None) -> None:
        self.runtime = runtime
        if runner is None:
            agents, _lifecycle = _load_agents()
            runner = agents.Runner()
        self.runner = runner

    async def run(
        self,
        proposal: CallProposal,
        starting_agent: Any,
        input: Any,
        **kwargs: Any,
    ) -> Any:
        async def invoke() -> Any:
            return await self.runner.run(starting_agent, input, **kwargs)

        return await self.runtime.run_async(proposal, invoke)

    def run_sync(
        self,
        proposal: CallProposal,
        starting_agent: Any,
        input: Any,
        **kwargs: Any,
    ) -> Any:
        return self.runtime.run(
            proposal,
            lambda: self.runner.run_sync(starting_agent, input, **kwargs),
        )


def build_run_hooks(
    runtime: GovernedRuntime,
    proposal_factory: Callable[[SDKHookCall], CallProposal] | None = None,
    *,
    session_id: str | None = None,
) -> Any:
    """Build SDK lifecycle observation hooks for one run.

    Lifecycle callbacks are not treated as a stable veto surface. Use a
    ``GovernedRunner`` for whole-run enforcement or a function-tool guardrail for
    the SDK's supported per-tool veto.
    """
    if runtime.mode == "enforce":
        raise ValueError(
            "Agents SDK run hooks are observe/warn only; use GovernedRunner or the "
            "function-tool guardrail for enforcement"
        )
    _agents, lifecycle = _load_agents()
    hooks_session_id = _nonempty(session_id, "session_id") if session_id else f"agents-sdk:{uuid.uuid4()}"
    factory = proposal_factory or _default_hook_proposal
    RunHooksBase = lifecycle.RunHooksBase

    class GovernorRunHooks(RunHooksBase):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self._agent_handles: dict[int, list[CallHandle]] = {}
            self._llm_handles: dict[int, list[CallHandle]] = {}
            self._tool_handles: dict[str, list[CallHandle]] = {}

        async def on_agent_start(self, context: Any, agent: Any) -> None:
            name = _agent_name(agent)
            call = SDKHookCall(
                kind="agent",
                session_id=hooks_session_id,
                call_id=f"agents-sdk-agent:{uuid.uuid4()}",
                route=f"agent:{name}",
                agent_name=name,
                material_fingerprint=_hash_text(name),
                metadata={"sdk_kind": "agent", "sdk_agent": name},
            )
            _push(self._agent_handles, id(agent), self._begin(call))

        async def on_agent_end(
            self,
            context: Any,
            agent: Any,
            output: Any,
        ) -> None:
            handle = _pop(self._agent_handles, id(agent))
            if handle is not None:
                runtime.complete(
                    handle,
                    progress="material_progress",
                    metadata={"sdk_event": "agent_end"},
                    source="agents-sdk-hooks",
                )

        async def on_handoff(
            self,
            context: Any,
            from_agent: Any,
            to_agent: Any,
        ) -> None:
            handle = _pop(self._agent_handles, id(from_agent))
            if handle is not None:
                runtime.complete(
                    handle,
                    progress="material_progress",
                    metadata={
                        "sdk_event": "handoff",
                        "from_agent": _agent_name(from_agent),
                        "to_agent": _agent_name(to_agent),
                    },
                    source="agents-sdk-hooks",
                )

        async def on_llm_start(
            self,
            context: Any,
            agent: Any,
            system_prompt: str | None,
            input_items: list[Any],
        ) -> None:
            name = _agent_name(agent)
            shape = {
                "has_system_prompt": bool(system_prompt),
                "input_count": len(input_items),
                "input_types": [type(item).__name__ for item in input_items],
            }
            call = SDKHookCall(
                kind="llm",
                session_id=hooks_session_id,
                call_id=f"agents-sdk-llm:{uuid.uuid4()}",
                route=f"llm:{name}",
                agent_name=name,
                material_fingerprint=_hash_json(shape),
                metadata={"sdk_kind": "llm", "sdk_agent": name},
            )
            _push(self._llm_handles, id(agent), self._begin(call))

        async def on_llm_end(
            self,
            context: Any,
            agent: Any,
            response: Any,
        ) -> None:
            handle = _pop(self._llm_handles, id(agent))
            if handle is not None:
                runtime.complete(
                    handle,
                    progress="material_progress",
                    metadata={"sdk_event": "llm_end"},
                    source="agents-sdk-hooks",
                )

        async def on_tool_start(
            self,
            context: Any,
            agent: Any,
            tool: Any,
        ) -> None:
            tool_name = _tool_name(tool, context)
            supplied_id = _optional_string(getattr(context, "tool_call_id", None))
            call_id = f"agents-sdk-tool:{supplied_id or uuid.uuid4()}"
            storage_key = _tool_storage_key(supplied_id, agent, tool_name)
            arguments = getattr(context, "tool_arguments", None)
            argument_fingerprint = _hash_text(arguments) if isinstance(arguments, str) else None
            call = SDKHookCall(
                kind="tool",
                session_id=hooks_session_id,
                call_id=call_id,
                route=f"tool:{tool_name}",
                agent_name=_agent_name(agent),
                material_fingerprint=argument_fingerprint,
                metadata={
                    "sdk_kind": "tool",
                    "sdk_agent": _agent_name(agent),
                    "sdk_tool": tool_name,
                },
            )
            _push(self._tool_handles, storage_key, self._begin(call))

        async def on_tool_end(
            self,
            context: Any,
            agent: Any,
            tool: Any,
            result: object,
        ) -> None:
            supplied_id = _optional_string(getattr(context, "tool_call_id", None))
            tool_name = _tool_name(tool, context)
            storage_key = _tool_storage_key(supplied_id, agent, tool_name)
            handle = _pop(self._tool_handles, storage_key)
            if handle is not None:
                runtime.complete(
                    handle,
                    progress="material_progress",
                    metadata={"sdk_event": "tool_end"},
                    source="agents-sdk-hooks",
                )

        def _begin(self, call: SDKHookCall) -> CallHandle:
            candidate = factory(call)
            if not isinstance(candidate, CallProposal):
                raise TypeError("proposal_factory must return CallProposal")
            return runtime.begin(candidate, call_id=call.call_id, source="agents-sdk-hooks")

    return GovernorRunHooks()


def build_function_tool_guardrail(
    runtime: GovernedRuntime,
    proposal_factory: Callable[[Any], CallProposal] | None = None,
    *,
    session_id: str | None = None,
    blocked_behavior: str = "reject_content",
) -> Any:
    """Build a supported FunctionTool input guardrail backed by the governor."""
    if blocked_behavior not in {"reject_content", "raise_exception"}:
        raise ValueError("blocked_behavior must be reject_content or raise_exception")
    agents, _lifecycle = _load_agents()
    guardrail_session_id = (
        _nonempty(session_id, "session_id") if session_id else f"agents-sdk-guardrail:{uuid.uuid4()}"
    )

    async def govern_tool_input(data: Any) -> Any:
        if proposal_factory is None:
            candidate = _default_guardrail_proposal(data, guardrail_session_id)
        else:
            candidate = proposal_factory(data)
        if not isinstance(candidate, CallProposal):
            raise TypeError("proposal_factory must return CallProposal")
        tool_context = data.context
        supplied_id = _optional_string(getattr(tool_context, "tool_call_id", None))
        call_id = f"agents-sdk-guardrail:{supplied_id or uuid.uuid4()}"
        try:
            handle = runtime.begin(candidate, call_id=call_id, source="agents-sdk-guardrail")
        except GovernanceBlocked as exc:
            info = {
                "governor_allowed": False,
                "reason": exc.decision.reason,
                "fingerprint": exc.decision.fingerprint,
            }
            if blocked_behavior == "raise_exception":
                return agents.ToolGuardrailFunctionOutput.raise_exception(output_info=info)
            return agents.ToolGuardrailFunctionOutput.reject_content(
                "Agent Call Governor blocked this function-tool call: "
                f"{exc.decision.reason}",
                output_info=info,
            )
        return agents.ToolGuardrailFunctionOutput.allow(
            output_info={
                "governor_allowed": True,
                "reason": handle.decision.reason,
                "fingerprint": handle.decision.fingerprint,
            }
        )

    return agents.ToolInputGuardrail(
        guardrail_function=govern_tool_input,
        name="agent_call_governor",
    )


def _default_hook_proposal(call: SDKHookCall) -> CallProposal:
    budget_kind = "agent" if call.kind == "agent" else "direct-tool"
    kind_label = {"agent": "agent", "llm": "model", "tool": "tool"}.get(call.kind, call.kind)
    material_inputs: dict[str, Any] = {"route": call.route}
    if call.material_fingerprint is not None:
        material_inputs["material_sha256"] = call.material_fingerprint
    return CallProposal(
        session_id=call.session_id,
        objective=f"OpenAI Agents SDK {kind_label} call: {call.route}",
        route=call.route,
        capability_gap=f"The workflow selected an external {kind_label} capability",
        expected_new_information=f"A result from {call.route}",
        stop_condition=f"The {kind_label} callback ends",
        material_inputs=material_inputs,
        budget_kind=budget_kind,
        profile="balanced",
        quality_risk="medium",
        metadata=dict(call.metadata),
    )


def _default_guardrail_proposal(data: Any, session_id: str) -> CallProposal:
    context = data.context
    tool_name = _tool_name(None, context)
    arguments = getattr(context, "tool_arguments", "")
    return CallProposal(
        session_id=session_id,
        objective=f"OpenAI Agents SDK function-tool call: {tool_name}",
        route=f"tool:{tool_name}",
        capability_gap="The workflow selected a local function-tool capability",
        expected_new_information=f"A result from {tool_name}",
        stop_condition="The function tool returns",
        material_inputs={
            "tool_name": tool_name,
            "arguments_sha256": _hash_text(arguments if isinstance(arguments, str) else ""),
        },
        budget_kind="direct-tool",
        profile="balanced",
        quality_risk="medium",
        metadata={
            "sdk_kind": "function-tool-guardrail",
            "sdk_agent": _agent_name(data.agent),
            "sdk_tool": tool_name,
        },
    )


def _load_agents() -> tuple[Any, Any]:
    try:
        version = metadata.version("openai-agents")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            'OpenAI Agents SDK support requires: pip install ".[agents]"'
        ) from exc
    match = _SUPPORTED_VERSION.match(version)
    if match is None or int(match.group(1)) < 2:
        raise RuntimeError(
            f"Unsupported openai-agents {version}; install >=0.18.2,<0.19"
        )
    try:
        agents = importlib.import_module("agents")
        lifecycle = importlib.import_module("agents.lifecycle")
    except ImportError as exc:
        raise RuntimeError(
            'OpenAI Agents SDK support requires: pip install ".[agents]"'
        ) from exc
    return agents, lifecycle


def _push(storage: dict[Any, list[CallHandle]], key: Any, handle: CallHandle) -> None:
    storage.setdefault(key, []).append(handle)


def _pop(storage: dict[Any, list[CallHandle]], key: Any) -> CallHandle | None:
    handles = storage.get(key)
    if not handles:
        return None
    handle = handles.pop()
    if not handles:
        storage.pop(key, None)
    return handle


def _agent_name(agent: Any) -> str:
    return _optional_string(getattr(agent, "name", None)) or type(agent).__name__


def _tool_name(tool: Any | None, context: Any) -> str:
    from_tool = _optional_string(getattr(tool, "name", None))
    from_context = _optional_string(getattr(context, "tool_name", None))
    if from_tool is not None:
        return from_tool
    if from_context is not None:
        return from_context
    return type(tool).__name__ if tool is not None else "unknown-tool"


def _tool_storage_key(supplied_id: str | None, agent: Any, tool_name: str) -> str:
    if supplied_id is not None:
        return f"id:{supplied_id}"
    return f"fallback:{id(agent)}:{tool_name}"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _hash_text(encoded)


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nonempty(value: str, name: str) -> str:
    normalized = _optional_string(value)
    if normalized is None:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized
