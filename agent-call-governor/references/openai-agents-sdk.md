# OpenAI Agents SDK integration

Install the optional integration separately:

```bash
python -m pip install -e ".[agents]"
```

The `agents` extra is pinned to `openai-agents>=0.18.2,<0.19`. The core policy,
ledger, runtime wrapper, and Codex hook do not import or require the SDK.

## Enforcement coverage

| Integration | Coverage | Can veto before execution? |
| --- | --- | --- |
| `GovernedRunner` | One complete `Runner.run` or `Runner.run_sync` workflow | Yes, through the application-owned wrapper |
| `build_function_tool_guardrail` | SDK `FunctionTool` input guardrails | Yes, through the SDK's supported guardrail result |
| `build_run_hooks` | Agent, LLM, local tool, and handoff lifecycle observation | No; use `observe` or `warn` only |

There is no claim of a universal SDK firewall. In particular, a function-tool
input guardrail does not govern every hosted tool family, handoff, or arbitrary
SDK extension. Wrap the whole run when those calls need a mandatory outer gate.

## Wrap a complete SDK run

```python
from agents import Agent
from agent_call_governor_runtime import (
    CallLedger,
    CallProposal,
    GovernedRuntime,
    GovernedRunner,
)

ledger = CallLedger(".governor/events.sqlite3")
runtime = GovernedRuntime(ledger, mode="enforce", failure_policy="fail-closed")
runner = GovernedRunner(runtime)

proposal = CallProposal(
    session_id="support-42",
    objective="Resolve one support request",
    route="agents-sdk:support",
    capability_gap="The workflow needs the support agent",
    expected_new_information="A final support answer",
    stop_condition="The SDK run returns",
    material_inputs={"request_id": "42"},
    quality_risk="medium",
)

agent = Agent(name="Support", instructions="Help the customer.")
result = runner.run_sync(proposal, agent, "The application-owned input")
```

`GovernedRunner` deliberately does not expose a streaming shortcut: a streamed
run continues after the initial object is returned, so completion must be tied
to the consumer's real stream lifecycle rather than recorded early.

## Add lifecycle observation

Create a fresh hook object per run so it receives a distinct session id:

```python
hooks = build_run_hooks(observe_runtime)
result = await Runner.run(agent, input_value, hooks=hooks)
```

The default mapper stores names, IDs, event kinds, and fingerprints. It does not
persist system prompts, model inputs, tool arguments, tool results, or agent
outputs. A custom `proposal_factory(SDKHookCall)` receives only a privacy-safe
descriptor.

## Guard a function tool

```python
guardrail = build_function_tool_guardrail(
    enforce_runtime,
    session_id="support-42",
)

@function_tool(tool_input_guardrails=[guardrail])
def lookup_order(order_id: str) -> str:
    ...
```

The default `reject_content` behavior prevents the function body from running
and gives the model a policy reason. Pass `blocked_behavior="raise_exception"`
to halt the run instead. An allowed input guardrail records a conservative
`started` event; an input guardrail alone cannot know the eventual tool result.

## Official SDK references

- [Running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [Lifecycle hooks API](https://openai.github.io/openai-agents-python/ref/lifecycle/)
- [Tool guardrails API](https://openai.github.io/openai-agents-python/ref/tool_guardrails/)
