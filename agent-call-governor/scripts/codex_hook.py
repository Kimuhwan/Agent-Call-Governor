#!/usr/bin/env python3
"""Backward-compatible script entrypoint for the packaged Codex hook adapter."""

from agent_call_governor_runtime.codex_hook import main


if __name__ == "__main__":
    raise SystemExit(main())
