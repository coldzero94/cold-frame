#!/usr/bin/env bash
# Coldframe TDD test-gate (Stop hook): block turn-end if the CORE suite fails.
# Safe no-op until tests exist / env is set up — does NOT interfere with planning turns.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 0
cd "$ROOT" || exit 0

# Gate only once there are tests to run.
shopt -s nullglob
tests=( tests/test_*.py cold_frame/eval/datasets/*.yaml )
shopt -u nullglob
[ ${#tests[@]} -eq 0 ] && exit 0
command -v uv >/dev/null 2>&1 || exit 0

out="$(uv run pytest -q -m 'not slow and not live' 2>&1)"; code=$?
# Block ONLY on real test failures (pytest exit code 1).
# 0=pass · 5=no tests · 2=interrupted/collection-error · 3=internal · 4=usage → all allow (infra, not a red suite).
[ "$code" -ne 1 ] && exit 0

{
  echo "❌ test-gate: CORE tests are failing — fix before ending the turn (TDD: never leave a red suite)."
  echo "--- pytest (last 25 lines) ---"
  echo "$out" | tail -25
} >&2
exit 2   # exit 2 = block the Stop and feed stderr back to Claude
