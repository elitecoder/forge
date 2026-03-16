#!/bin/bash
# PostToolUse hook: validate judge-verdict.json has required fields.
#
# When the judge agent writes judge-verdict.json, this hook validates
# the JSON schema. Exit 2 tells the agent to fix the file.
#
# Stdin: JSON with tool_input.file_path and tool_input.content
# Exit 0: valid, Exit 2: invalid (stderr shown to agent)

set -eo pipefail

# Only run in forge executor mode
if [ "${FORGE_MODE:-}" != "executor" ]; then
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")
if [ "$BASENAME" != "judge-verdict.json" ]; then
  exit 0
fi

CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // empty')
if [ -z "$CONTENT" ]; then
  exit 0
fi

# Validate required fields
VERDICT=$(echo "$CONTENT" | jq -r '.verdict // empty')
if [ -z "$VERDICT" ]; then
  echo "INVALID judge-verdict.json: missing 'verdict' field. Must be 'PASS' or 'FAIL'." >&2
  exit 2
fi

if [ "$VERDICT" != "PASS" ] && [ "$VERDICT" != "FAIL" ]; then
  echo "INVALID judge-verdict.json: 'verdict' must be 'PASS' or 'FAIL', got '$VERDICT'." >&2
  exit 2
fi

SCENARIOS=$(echo "$CONTENT" | jq '.scenarios // empty')
if [ -z "$SCENARIOS" ] || [ "$SCENARIOS" = "null" ]; then
  echo "INVALID judge-verdict.json: missing 'scenarios' array." >&2
  exit 2
fi

# When verdict is FAIL, failure_type is required
if [ "$VERDICT" = "FAIL" ]; then
  FAILURE_TYPE=$(echo "$CONTENT" | jq -r '.failure_type // empty')
  if [ -z "$FAILURE_TYPE" ]; then
    echo "INVALID judge-verdict.json: verdict is FAIL but 'failure_type' is missing. Must be 'script_issue' or 'code_issue'." >&2
    exit 2
  fi
  if [ "$FAILURE_TYPE" != "script_issue" ] && [ "$FAILURE_TYPE" != "code_issue" ]; then
    echo "INVALID judge-verdict.json: 'failure_type' must be 'script_issue' or 'code_issue', got '$FAILURE_TYPE'." >&2
    exit 2
  fi

  FIX_HINTS=$(echo "$CONTENT" | jq -r '.fix_hints // empty')
  if [ -z "$FIX_HINTS" ]; then
    echo "INVALID judge-verdict.json: verdict is FAIL but 'fix_hints' is missing. Provide specific guidance on what to fix." >&2
    exit 2
  fi
fi

exit 0
