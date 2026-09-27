#!/bin/sh
set -eu

OLLAMA_BIN="$(command -v ollama || true)"
if [ -z "$OLLAMA_BIN" ]; then
  OLLAMA_BIN=/bin/ollama
fi

MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

echo "Starting ollama server"
"$OLLAMA_BIN" serve &
SERVER_PID=$!

echo "Waiting for ollama server to become ready"
attempt=0
until "$OLLAMA_BIN" list >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "ollama server did not become ready in time" >&2
    exit 1
  fi
  sleep 2
done

if "$OLLAMA_BIN" list | awk 'NR > 1 { print $1 }' | grep -qxF "$MODEL"; then
  echo "Model $MODEL already present, skipping pull"
else
  echo "Model $MODEL not found, pulling"
  pull_attempt=0
  until "$OLLAMA_BIN" pull "$MODEL"; do
    pull_attempt=$((pull_attempt + 1))
    if [ "$pull_attempt" -ge 3 ]; then
      echo "Failed to pull model $MODEL after $pull_attempt attempts" >&2
      exit 1
    fi
    echo "Pull attempt $pull_attempt failed, retrying"
    sleep 5
  done
  echo "Model $MODEL pulled"
fi

wait "$SERVER_PID"
