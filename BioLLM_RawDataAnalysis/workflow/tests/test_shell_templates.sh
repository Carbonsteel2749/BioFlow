#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMMON="$PROJECT_ROOT/workflow/bin/lib/common.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

assert_contains() {
  local file="$1"
  local expected="$2"
  grep -F -- "$expected" "$file" >/dev/null || {
    echo "ASSERTION FAILED: $file does not contain: $expected" >&2
    return 1
  }
}

assert_json_value() {
  local file="$1"
  local key="$2"
  local expected="$3"
  python3 - "$file" "$key" "$expected" <<'PY'
import json
import sys
path, key, expected = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)[key]
if str(value) != expected:
    raise SystemExit(f"{path}: expected {key}={expected!r}, got {value!r}")
PY
}

test_success_status_and_log() {
  local root="$TMP_ROOT/success"
  mkdir -p "$root"
  PIPELINE_TASK_ID="task-success" \
  PIPELINE_SAMPLE_ID="sample-01" \
  PIPELINE_LOG_DIR="$root/logs" \
  PIPELINE_STATUS_DIR="$root/status" \
  bash -c '
    set -Eeuo pipefail
    source "$1"
    step_prepare "fastp"
    step_start "quality filtering started"
    printf "result\n" > "$2"
    step_success "quality filtering completed" "$2"
  ' _ "$COMMON" "$root/result.fastq"

  assert_json_value "$root/status/sample-01.fastp.json" status succeeded
  assert_json_value "$root/status/sample-01.fastp.json" exit_code 0
  assert_contains "$root/logs/sample-01.fastp.log" "[INFO]"
  assert_contains "$root/logs/sample-01.fastp.log" "quality filtering completed"
}

test_failure_status_has_command_and_reason() {
  local root="$TMP_ROOT/failure"
  mkdir -p "$root"
  set +e
  PIPELINE_TASK_ID="task-failure" \
  PIPELINE_SAMPLE_ID="sample-02" \
  PIPELINE_LOG_DIR="$root/logs" \
  PIPELINE_STATUS_DIR="$root/status" \
  bash -c '
    set -Eeuo pipefail
    source "$1"
    step_prepare "host_depletion"
    trap '\''step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"'\'' ERR
    step_start "host removal started"
    run_tool bash -c "echo simulated-tool-error >&2; exit 7"
  ' _ "$COMMON"
  local status=$?
  set -e

  [[ "$status" -eq 7 ]]
  assert_json_value "$root/status/sample-02.host_depletion.json" status failed
  assert_json_value "$root/status/sample-02.host_depletion.json" exit_code 7
  assert_contains "$root/status/sample-02.host_depletion.json" "bash -c"
  assert_contains "$root/logs/sample-02.host_depletion.log" "[ERROR]"
  assert_contains "$root/logs/sample-02.host_depletion.log" "simulated-tool-error"
}

test_resume_checkpoint_skips_completed_output() {
  local root="$TMP_ROOT/resume"
  mkdir -p "$root"
  local output="$root/final.txt"
  PIPELINE_TASK_ID="task-resume" \
  PIPELINE_SAMPLE_ID="sample-03" \
  PIPELINE_LOG_DIR="$root/logs" \
  PIPELINE_STATUS_DIR="$root/status" \
  bash -c '
    set -Eeuo pipefail
    source "$1"
    step_prepare "taxonomy"
    step_start "taxonomy started"
    printf "complete\n" > "$2"
    step_success "taxonomy completed" "$2"
  ' _ "$COMMON" "$output"

  PIPELINE_TASK_ID="task-resume" \
  PIPELINE_SAMPLE_ID="sample-03" \
  PIPELINE_LOG_DIR="$root/logs" \
  PIPELINE_STATUS_DIR="$root/status" \
  PIPELINE_RESUME=1 \
  bash -c '
    set -Eeuo pipefail
    source "$1"
    step_prepare "taxonomy"
    checkpoint_valid "$2"
  ' _ "$COMMON" "$output"

  assert_contains "$root/logs/sample-03.taxonomy.log" "checkpoint_hit"
  assert_json_value "$root/status/sample-03.taxonomy.json" status succeeded
}

test_success_status_and_log
test_failure_status_has_command_and_reason
test_resume_checkpoint_skips_completed_output
printf "shell lifecycle tests passed\n"
