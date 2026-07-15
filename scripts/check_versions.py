from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


EXPECTED_VERSION = "0.3.0"
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
CHANGELOG_PATTERN = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] - (?:Unreleased|\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)


def collect_versions(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    package_path = (
        root
        / "skills"
        / "agent-call-governor"
        / "scripts"
        / "agent_call_governor_runtime"
        / "__init__.py"
    )
    package_match = VERSION_PATTERN.search(package_path.read_text(encoding="utf-8"))
    if package_match is None:
        raise ValueError("package __version__ is missing")

    plugin = json.loads(
        (root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    if not isinstance(plugin, dict) or not isinstance(plugin.get("version"), str):
        raise ValueError("plugin version is missing or invalid")

    changelog_match = CHANGELOG_PATTERN.search(
        (root / "CHANGELOG.md").read_text(encoding="utf-8")
    )
    if changelog_match is None:
        raise ValueError("parseable changelog version is missing")

    return {
        "package": package_match.group(1),
        "plugin": plugin["version"],
        "changelog": changelog_match.group(1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Agent Call Governor release versions")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    versions = collect_versions(args.root)
    if versions != {
        "package": EXPECTED_VERSION,
        "plugin": EXPECTED_VERSION,
        "changelog": EXPECTED_VERSION,
    }:
        raise SystemExit(f"version mismatch: {json.dumps(versions, sort_keys=True)}")
    print(json.dumps(versions, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
