#!/usr/bin/env bash
#
# End-to-end verification for a running ai-router-poc stack.
#
# Host-side only: talks to the published API (:8000) and Mailpit (:8025) ports
# and to the Ollama container via `docker compose exec`. Run from the repository
# root after `docker compose up -d`.
#
# Usage:
#   scripts/verify_e2e.sh [--message TEXT] [--sender EMAIL] [--expected EMAIL]
#                         [--failure-check] [--timeout SECONDS]
#
# `python3` parses the Mailpit JSON; if it is unavailable, install the Xcode
# command line tools or substitute `uv run python` in the parser below.

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
MAILPIT_BASE_URL="${MAILPIT_BASE_URL:-http://localhost:8025}"
MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

MESSAGE="Nie działa mi komputer"
SENDER="jan.nowak@example.com"
EXPECTED="help-desk@example.com"
FAILURE_CHECK=0
TIMEOUT_SECONDS="${E2E_TIMEOUT_SECONDS:-300}"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: scripts/verify_e2e.sh [options]

Options:
  --message TEXT      Message body to route (default: "Nie działa mi komputer")
  --sender EMAIL      Sender email (default: jan.nowak@example.com)
  --expected EMAIL    Expected recipient (default: help-desk@example.com)
  --failure-check     Stop mailpit, assert HTTP 503, then restart it
  --timeout SECONDS   Max seconds to wait for the API and model (default: 300)
  -h, --help          Show this help
EOF
}

log() {
  printf '[verify_e2e] %s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message) MESSAGE="$2"; shift 2 ;;
    --sender) SENDER="$2"; shift 2 ;;
    --expected) EXPECTED="$2"; shift 2 ;;
    --failure-check) FAILURE_CHECK=1; shift ;;
    --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

require_cmd curl
require_cmd docker
require_cmd python3

DEADLINE=$(( $(date +%s) + TIMEOUT_SECONDS ))

wait_for_health() {
  log "waiting for ${API_BASE_URL}/health"
  until curl -fsS "${API_BASE_URL}/health" >/dev/null 2>&1; do
    if [[ "$(date +%s)" -ge "${DEADLINE}" ]]; then
      echo "Timed out waiting for the API health endpoint" >&2
      exit 1
    fi
    sleep 2
  done
}

wait_for_model() {
  log "waiting for the Ollama model ${MODEL}"
  until docker compose exec -T ollama ollama list 2>/dev/null | grep -q "${MODEL}"; do
    if [[ "$(date +%s)" -ge "${DEADLINE}" ]]; then
      echo "Timed out waiting for model ${MODEL}" >&2
      exit 1
    fi
    sleep 5
  done
}

assert_docs() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "${API_BASE_URL}/api/v1/docs")"
  if [[ "${code}" != "200" ]]; then
    echo "Expected /api/v1/docs to return 200, got ${code}" >&2
    exit 1
  fi
  log "/api/v1/docs -> 200"
}

post_request() {
  local out_file="$1"
  local body
  body="$(python3 -c 'import json, sys; print(json.dumps({"email": sys.argv[1], "message": sys.argv[2]}))' "${SENDER}" "${MESSAGE}")"
  curl -sS -o "${out_file}" -w '%{http_code}' -X POST "${API_BASE_URL}/api/v1/route" \
    -H 'Content-Type: application/json' \
    --data "${body}"
}

assert_one_message() {
  local payload_file="$1"
  python3 - "${payload_file}" "${EXPECTED}" "${SENDER}" <<'PY'
import json
import sys

path, expected, sender = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as handle:
    data = json.load(handle)

messages = data.get("messages", [])
total = data.get("total", len(messages))
if len(messages) != 1 or total != 1:
    print(f"expected exactly one captured message, found {len(messages)} (total={total})", file=sys.stderr)
    print(json.dumps(data, indent=2), file=sys.stderr)
    sys.exit(1)


def first_address(field: str) -> str | None:
    values = messages[0].get(field) or []
    return values[0].get("Address") if values else None


to = first_address("To")
reply_to = first_address("ReplyTo")
if to != expected:
    print(f"expected To {expected!r}, got {to!r}", file=sys.stderr)
    sys.exit(1)
if reply_to != sender:
    print(f"expected Reply-To {sender!r}, got {reply_to!r}", file=sys.stderr)
    sys.exit(1)
print(f"captured message To={to} ReplyTo={reply_to}")
PY
}

wait_for_health
wait_for_model
assert_docs

log "clearing the mailbox"
curl -fsS -X DELETE "${MAILPIT_BASE_URL}/api/v1/messages" >/dev/null

log "POST /api/v1/route message=${MESSAGE}"
route_file="${TMP_DIR}/route.json"
route_code="$(post_request "${route_file}")"
if [[ "${route_code}" != "200" ]]; then
  echo "Expected HTTP 200 from /api/v1/route, got ${route_code}" >&2
  cat "${route_file}" >&2
  exit 1
fi
cat "${route_file}"

messages_file="${TMP_DIR}/messages.json"
curl -fsS "${MAILPIT_BASE_URL}/api/v1/messages" -o "${messages_file}"
assert_one_message "${messages_file}"

if [[ "${FAILURE_CHECK}" == "1" ]]; then
  log "failure check: stopping mailpit"
  docker compose stop mailpit >/dev/null
  restart_mailpit() {
    log "failure check: restarting mailpit"
    docker compose start mailpit >/dev/null
  }
  trap 'restart_mailpit; cleanup' EXIT

  failure_file="${TMP_DIR}/failure.json"
  failure_code="$(post_request "${failure_file}")"
  if [[ "${failure_code}" != "503" ]]; then
    echo "Expected HTTP 503 while mailpit is down, got ${failure_code}" >&2
    cat "${failure_file}" >&2
    exit 1
  fi
  log "failure check: got HTTP 503"
  restart_mailpit
  trap cleanup EXIT
fi

log "OK"
