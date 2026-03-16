#!/bin/bash
# PostToolUse hook: lint and format files after Edit/Write.
# Reads tool_input.file_path from stdin JSON, runs eslint --fix + prettier --write.
# Non-blocking: always exits 0.
#
# Config:
#   FORGE_ESLINT_CONFIG  — path to eslint config (for repos with non-standard locations)

set -eo pipefail

# Only run for executor agents that edit code (code, fix, code-review-fix)
if [ "${FORGE_MODE:-}" != "executor" ]; then
  exit 0
fi
case "${FORGE_AGENT:-}" in
  code|fix|code-review-fix) ;;
  *) exit 0 ;;
esac

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only lint TypeScript/JavaScript files
case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs) ;;
  *) exit 0 ;;
esac

if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# Build eslint args
ESLINT_ARGS=(--fix)
if [ -n "${FORGE_ESLINT_CONFIG:-}" ]; then
  ESLINT_ARGS+=(--config "$FORGE_ESLINT_CONFIG")
fi
ESLINT_ARGS+=("$FILE_PATH")

# eslint --fix
if command -v eslint &>/dev/null; then
  eslint "${ESLINT_ARGS[@]}" 2>/dev/null || true
elif command -v npx &>/dev/null; then
  npx --no-install eslint "${ESLINT_ARGS[@]}" 2>/dev/null || true
fi

# prettier --write
if command -v prettier &>/dev/null; then
  prettier --write "$FILE_PATH" 2>/dev/null || true
elif command -v npx &>/dev/null; then
  npx --no-install prettier --write "$FILE_PATH" 2>/dev/null || true
fi

exit 0
