#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/agent-call-governor"
CODEX_ROOT=${CODEX_HOME:-"$HOME/.codex"}
DESTINATION="$CODEX_ROOT/skills/agent-call-governor"

rm -rf -- "$DESTINATION"
mkdir -p "$DESTINATION"
(cd "$SOURCE" && tar \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.egg-info' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  -cf - .) | (cd "$DESTINATION" && tar -xf -)

printf 'Installed Agent Call Governor to %s\n' "$DESTINATION"
printf 'Restart Codex to load the skill.\n'
