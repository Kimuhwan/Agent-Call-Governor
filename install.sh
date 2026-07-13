#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/agent-call-governor"
CODEX_ROOT=${CODEX_HOME:-"$HOME/.codex"}
DESTINATION="$CODEX_ROOT/skills/agent-call-governor"

mkdir -p "$DESTINATION"
cp -R "$SOURCE/." "$DESTINATION/"

printf 'Installed Agent Call Governor to %s\n' "$DESTINATION"
printf 'Restart Codex to load the skill.\n'
