#!/usr/bin/env bash

pipeline_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

step_prepare() {
  if [[ $# -ne 1 || -z "${1:-}" ]]; then
    echo "step_prepare requires one non-empty step name" >&2
    return 64
  fi
  STEP_NAME="$1"
  PIPELINE_TASK_ID="${PIPELINE_TASK_ID:-manual}"
  PIPELINE_SAMPLE_ID="${PIPELINE_SAMPLE_ID:-global}"
  PIPELINE_LOG_DIR="${PIPELINE_LOG_DIR:-./logs}"
  PIPELINE_STATUS_DIR="${PIPELINE_STATUS_DIR:-./status}"
  mkdir -p "$PIPELINE_LOG_DIR" "$PIPELINE_STATUS_DIR"
  STEP_LOG_FILE="$PIPELINE_LOG_DIR/${PIPELINE_SAMPLE_ID}.${STEP_NAME}.log"
  STEP_STATUS_FILE="$PIPELINE_STATUS_DIR/${PIPELINE_SAMPLE_ID}.${STEP_NAME}.json"
  STEP_STARTED_AT=""
  export STEP_NAME STEP_LOG_FILE STEP_STATUS_FILE STEP_STARTED_AT
}

log_message() {
  local level="$1"
  shift
  local message="$*"
  local timestamp
  timestamp="$(pipeline_now)"
  printf '%s [%s] [task=%s] [sample=%s] [step=%s] %s\n' \
    "$timestamp" "$level" "$PIPELINE_TASK_ID" "$PIPELINE_SAMPLE_ID" "$STEP_NAME" "$message" \
    | tee -a "$STEP_LOG_FILE"
}

status_write() {
  local status="$1"
  local exit_code="$2"
  local message="$3"
  local failed_command="${4:-}"
  local failed_line="${5:-}"
  shift 5 || true
  local finished_at=""
  if [[ "$status" != "running" ]]; then
    finished_at="$(pipeline_now)"
  fi
  python3 - "$STEP_STATUS_FILE" "$PIPELINE_TASK_ID" "$PIPELINE_SAMPLE_ID" "$STEP_NAME" \
    "$status" "$STEP_STARTED_AT" "$finished_at" "$exit_code" "$message" "$failed_command" \
    "$failed_line" "$STEP_LOG_FILE" "$@" <<'PY'
import json
import os
import sys
from pathlib import Path
(
    path, task_id, sample_id, step, status, started_at, finished_at,
    exit_code, message, failed_command, failed_line, log_file, *outputs
) = sys.argv[1:]
payload = {
    "task_id": task_id,
    "sample_id": sample_id,
    "step": step,
    "status": status,
    "started_at": started_at or None,
    "finished_at": finished_at or None,
    "exit_code": int(exit_code),
    "message": message,
    "failed_command": failed_command or None,
    "failed_line": int(failed_line) if failed_line else None,
    "log_file": log_file,
    "outputs": outputs,
}
target = Path(path)
temporary = target.with_suffix(target.suffix + ".tmp")
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, target)
PY
}

step_start() {
  local message="${1:-step started}"
  STEP_STARTED_AT="$(pipeline_now)"
  export STEP_STARTED_AT
  : > "$STEP_LOG_FILE"
  log_message INFO "$message"
  status_write running 0 "$message" "" ""
}

step_success() {
  local message="$1"
  shift
  local output
  for output in "$@"; do
    if [[ ! -e "$output" ]]; then
      step_fail 66 "$LINENO" "missing expected output: $output"
    fi
    if [[ -f "$output" && ! -s "$output" ]]; then
      step_fail 66 "$LINENO" "empty expected output: $output"
    fi
  done
  log_message INFO "$message"
  status_write succeeded 0 "$message" "" "" "$@"
}

step_fail() {
  local exit_code="${1:-1}"
  local failed_line="${2:-0}"
  local failed_command="${3:-unknown command}"
  trap - ERR
  log_message ERROR "command_failed exit_code=$exit_code line=$failed_line command=$failed_command"
  status_write failed "$exit_code" "command failed" "$failed_command" "$failed_line"
  exit "$exit_code"
}

run_tool() {
  local rendered=""
  local part
  for part in "$@"; do
    printf -v part '%q' "$part"
    rendered+="${rendered:+ }$part"
  done
  CURRENT_COMMAND="$rendered"
  export CURRENT_COMMAND
  log_message INFO "command=$rendered"
  set +e
  "$@" > >(tee -a "$STEP_LOG_FILE") 2> >(tee -a "$STEP_LOG_FILE" >&2)
  local exit_code=$?
  set -e
  return "$exit_code"
}

checkpoint_valid() {
  [[ "${PIPELINE_RESUME:-0}" == "1" ]] || return 1
  [[ -s "$STEP_STATUS_FILE" ]] || return 1
  python3 - "$STEP_STATUS_FILE" <<'PY' || return 1
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    raise SystemExit(0 if json.load(handle).get("status") == "succeeded" else 1)
PY
  local output
  for output in "$@"; do
    [[ -e "$output" ]] || return 1
    if [[ -f "$output" ]]; then
      [[ -s "$output" ]] || return 1
    fi
  done
  log_message INFO "checkpoint_hit outputs_verified=$#"
  return 0
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || {
    log_message ERROR "missing_dependency command=$command_name"
    return 127
  }
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || {
    log_message ERROR "missing_input file=$path"
    return 66
  }
}

require_dir() {
  local path="$1"
  [[ -d "$path" ]] || {
    log_message ERROR "missing_input directory=$path"
    return 66
  }
}

require_value() {
  local name="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || {
    log_message ERROR "missing_parameter name=$name"
    return 64
  }
}
