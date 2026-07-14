from __future__ import annotations

import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


def main() -> int:
    try:
        plugin_root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
        package_root = plugin_root / "skills" / "agent-call-governor" / "scripts"
        sys.path.insert(0, str(package_root))
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            from agent_call_governor_runtime.codex_hook import main as hook_main

            result = int(hook_main())
        if result != 0:
            raise RuntimeError
        sys.stdout.write(output.getvalue())
        return 0
    except BaseException as exc:
        print(f"agent-call-governor hook unavailable: {type(exc).__name__}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
