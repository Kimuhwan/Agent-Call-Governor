from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        plugin_root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
        package_root = plugin_root / "skills" / "agent-call-governor" / "scripts"
        sys.path.insert(0, str(package_root))
        from agent_call_governor_runtime.codex_hook import main as hook_main

        return int(hook_main())
    except BaseException as exc:
        print(f"agent-call-governor hook unavailable: {type(exc).__name__}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
