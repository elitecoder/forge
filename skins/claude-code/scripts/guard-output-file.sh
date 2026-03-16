#!/bin/bash
# PostToolUse hook: detect writes to files outside an agent's allowlist.
#
# As PostToolUse, the write has already happened. Exit 2 gives the agent feedback
# so it knows to undo/correct the unauthorized write.
#
# Env vars (set per agent by planner/executor drivers):
#   FORGE_MODE        — "planner" or "executor" (unset = no-op)
#   FORGE_OUTPUT_FILE — comma-separated allowlist of basenames (empty = unrestricted)
#
# Stdin: JSON with tool_input.file_path
# Exit 0: allow, Exit 2: report violation (stderr shown to agent)

set -eo pipefail

# No-op outside forge runs
if [ -z "${FORGE_MODE:-}" ]; then
  exit 0
fi

# Unrestricted if no allowlist set
if [ -z "${FORGE_OUTPUT_FILE:-}" ]; then
  exit 0
fi

FILE_PATH=$(cat | jq -r '.tool_input.file_path // empty')
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

# Check if basename is in the comma-separated allowlist
IFS=',' read -ra ALLOWED <<< "$FORGE_OUTPUT_FILE"
for entry in "${ALLOWED[@]}"; do
  if [ "$BASENAME" = "$entry" ]; then
    exit 0
  fi
done

echo "VIOLATION: You wrote to '$BASENAME' which is outside your allowlist: $FORGE_OUTPUT_FILE. Undo this write — only write to allowed files." >&2
exit 2
