# ai-router-poc

Proof of Concept (PoC) AI microservice that acts as an intelligent message
router: it accepts a user request, interprets it with a local LLM (Ollama), and
forwards the message to the right department by email. The agent sends mail only
through its `send_email` tool (function calling).

## Architecture decisions

- **API**: FastAPI + Uvicorn (`app/`), with `POST /api/v1/route` accepting
  `{email, message}` and returning `{recipient, reason}`. Swagger/OpenAPI is
  served at `/api/v1/docs`; `GET /health` is the liveness probe.
- **Agent**: `pydantic-ai` running against local Ollama (`llama3.1:8b`, CPU) with
  `temperature=0` so routing is deterministic. Polish and English are treated
  identically; the decision is based on meaning only.
- **Routing policy**: exactly five departments — `kadry@example.com` (leave,
  absences, personnel records), `human-resources@example.com` (recruitment,
  training, performance, workplace relations), `help-desk@example.com` (end-user
  devices and tooling), `it@example.com` (systems, infrastructure, accounts,
  security), and `other@example.com` (fallback). The policy text lives in
  `app/agent/policy.py` and can be overridden via `AGENT_SYSTEM_PROMPT`.
- **Email delivery**: `SmtpEmailTransport` (`app/tools/smtp.py`) delivers with
  `aiosmtplib`, so an in-flight send is cancellable, and retries transient
  failures with `tenacity`. `From` is the service address
  (`noreply@ai-router.local`); `Reply-To` is pinned to the request sender and can
  never be overridden by the model.
- **Mail catcher**: Mailpit receives the messages (SMTP `:1025`, web UI `:8025`).
- **Resilience**: Ollama HTTP calls are retried by a bounded `tenacity` transport
  (`OLLAMA_MAX_RETRIES + 1` attempts for transient errors and `429/502/503/504`);
  a run that produces no `send_email` call is retried up to
  `AGENT_NO_SEND_MAX_ATTEMPTS` times. Any downstream failure is surfaced as an
  explicit `503` instead of a silent drop.
- **Configuration**: all settings come from the environment via
  `pydantic-settings` (see `.env.example`); no secrets are hardcoded.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2
- [uv](https://docs.astral.sh/uv/) for local development and tooling

## Quick start

```bash
docker compose up -d
```

The first start pulls the default Ollama model (`llama3.1:8b`, ~5 GB) into a
named volume, which can take several minutes depending on network speed. Later
starts detect the model and skip the pull.

Wait until the API and the model are ready before sending requests:

```bash
until curl -fsS http://localhost:8000/health >/dev/null 2>&1; do sleep 2; done
until docker compose exec -T ollama ollama list 2>/dev/null | grep -q 'llama3.1:8b'; do sleep 5; done
```

## Services

| Service | Host port | Purpose |
|---------|-----------|---------|
| API     | 8000      | FastAPI application (`GET /health`) |
| Mailpit | 8025      | Web UI for captured mail |
| Mailpit | 1025      | SMTP endpoint |
| Ollama  | 11434     | Local LLM API |

## Quick test

The stack must be running with Ollama up and the `llama3.1:8b` model available.

```bash
curl -s -X POST http://localhost:8000/api/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"email":"jan.nowak@example.com","message":"Nie działa mi komputer"}'
```

The routing agent interprets the message (Polish or English) and forwards it to
the matching department by calling its `send_email` tool. The response reports
the chosen recipient and a short reason:

```json
{
  "recipient": "help-desk@example.com",
  "reason": "The message reports a computer problem, which is handled by help-desk."
}
```

Unrecognized or ambiguous messages fall back to `other@example.com`.

Interactive OpenAPI documentation is served at `http://localhost:8000/api/v1/docs`.

## Manual verification (Mailpit)

```bash
curl -s -X DELETE http://localhost:8025/api/v1/messages >/dev/null
curl -s -X POST http://localhost:8000/api/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"email":"jan.nowak@example.com","message":"Nie działa mi komputer"}'
# expect: {"recipient":"help-desk@example.com", ...}
```

Open `http://localhost:8025` and confirm **exactly one** message was delivered
with `To` equal to the recipient reported by the API (the agent-chosen
department, `help-desk@example.com` for the example above) and `Reply-To` equal
to the request sender (`jan.nowak@example.com`). The service `From` address is
`noreply@ai-router.local`.

## End-to-end verification

`scripts/verify_e2e.sh` is a repeatable host-side check of the running stack. It
waits for `/health` and for `llama3.1:8b`, asserts `/api/v1/docs` returns `200`,
clears Mailpit, POSTs a request, and asserts via the Mailpit REST API that
exactly one message exists with the expected `To` and `Reply-To`.

```bash
scripts/verify_e2e.sh
scripts/verify_e2e.sh --message "I would like to request annual leave next week" \
  --expected kadry@example.com
scripts/verify_e2e.sh --failure-check   # stops mailpit and asserts HTTP 503
```

The live DoD evidence (environment, commands, outputs, and the PL/EN routing
matrix) was captured with this script against a fresh clone of the repository.

## Failure handling

When a downstream service fails (Ollama unreachable, model error, or SMTP
delivery failure), the API returns an explicit `503` with a user-safe `detail`
instead of silently dropping the message:

```bash
docker compose stop mailpit
curl -s -X POST http://localhost:8000/api/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"email":"jan.nowak@example.com","message":"Nie działa mi komputer"}'
# expect: HTTP 503 with a user-safe detail
docker compose start mailpit
```

SMTP delivery is at-least-once in the PoC: a retry can duplicate a message if a
send succeeds but the acknowledgement is lost. Mailpit has no deduplication;
this is an accepted PoC limitation.

## Configuration

Settings are read from the environment (Compose passes them to the `api`
service); see `.env.example` for the full contract.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model name |
| `OLLAMA_TIMEOUT_SECONDS` | `30` | Per-request model timeout |
| `OLLAMA_MAX_RETRIES` | `2` | Transient HTTP retries (`+1` attempts) |
| `OLLAMA_RETRY_BACKOFF_SECONDS` | `0.1` | Exponential retry multiplier |
| `SMTP_HOST` / `SMTP_PORT` | `mailpit` / `1025` | SMTP target |
| `SMTP_FROM` | `noreply@ai-router.local` | Service `From` address |
| `SMTP_TIMEOUT_SECONDS` | `10` | Per-attempt SMTP timeout |
| `SMTP_MAX_RETRIES` | `3` | SMTP retries (`+1` attempts) |
| `SMTP_RETRY_BACKOFF_SECONDS` | `0.1` | SMTP retry backoff |
| `AGENT_TIMEOUT_SECONDS` | `180` | Wall-clock bound per request (covers a cold CPU model load) |
| `AGENT_RETRY_BACKOFF_SECONDS` | `0.1` | No-send retry backoff |
| `AGENT_REASON_MAX_LENGTH` | `300` | Max length of the returned reason |
| `AGENT_NO_SEND_MAX_ATTEMPTS` | `2` | Retries when the model sends nothing |
| `MESSAGE_MAX_LENGTH` | `2000` | Max validated message length |

**Advanced override:** `AGENT_SYSTEM_PROMPT` replaces the pinned routing policy.
It defaults to `app/agent/policy.py` and is documented (commented) in
`.env.example`; it is intentionally not wired through Compose. Only set it to
experiment — the default policy is what the routing matrix verifies.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

Tests run offline against an in-process fake SMTP server and `FunctionModel`
doubles, so no Docker or Ollama is required. Integration tests are marked
`integration` and excluded by default.
