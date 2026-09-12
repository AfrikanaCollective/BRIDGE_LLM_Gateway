# Qwen LLM Gateway

A production-oriented LLM gateway that every LLM request in this deployment
routes through, sitting in front of one or more Ollama backends, with
per-tenant auth, budgets, rate limiting, and provider failover. There is
no in-process consumer: the ITF/NAR medical-form extraction pipeline that
used to live in this repo (`agents/`, `clients/`, `prompts/`) now runs as
a **separate external web app**, calling this gateway over HTTP with a
real API key — the same as any other tenant.

**Status**: 🚧 Core gateway (auth, rate limiting, budgets, failover,
observability) is implemented and unit-tested. `app.py`'s
`/generate-with-image` handler calls `GatewayService.handle_request()` in
the same process instead of talking to Ollama directly, and requires a
real `Authorization: Bearer <api_key>` header — the "one code path to
Ollama, no bypass tenant" invariant holds end-to-end. `POST /v1/embeddings`
(`BAAI/bge-large-en-v1.5`) has been implemented, unit-tested, and verified
against a live gateway + Ollama backend. Reranking
(`BAAI/bge-reranker-v2-m3`) was evaluated and deliberately not built — see
`ARCHITECTURE.md` §14. See `PRD.md` §10 and `ARCHITECTURE.md` §12 for what
else is deliberately not built yet.

**Full docs**: [PRD.md](./PRD.md) (product) ·
[ARCHITECTURE.md](./ARCHITECTURE.md) (full technical design) ·
[ARCHITECTURE-ESSENTIALS.md](./ARCHITECTURE-ESSENTIALS.md) (condensed
reference — start here if you're implementing) ·
[CLAUDE.md](./CLAUDE.md) / [AGENTS.md](./AGENTS.md) (agent instructions).

---

## What it does

- **One choke point to Ollama.** `GatewayService` is the only code path
  allowed to call a model backend — `handle_request()` for chat/vision,
  `handle_embedding_request()` for embeddings, both built on one shared
  pipeline. Every HTTP route, including the legacy `/generate-with-image`
  endpoint, calls through one of these.
- **Two model capabilities today: chat/vision and embeddings.**
  `POST /v1/chat/completions` for chat/vision, `POST /v1/embeddings` for
  embeddings (currently `BAAI/bge-large-en-v1.5`, served by Ollama as
  `qllama/bge-large-en-v1.5:latest`). Both go through the same auth, rate
  limiting, and budget enforcement — see `ARCHITECTURE.md` §13. A
  reranking capability (`BAAI/bge-reranker-v2-m3`) was evaluated and
  deliberately not built — see "What's intentionally not here" below and
  `ARCHITECTURE.md` §14.
- **Per-tenant auth, no exceptions.** Bearer API keys, hashed at rest,
  resolved to a real tenant. There is no internal/implicit tenant — every
  caller, including this repo's own legacy endpoint, authenticates the
  same way.
- **Rate limiting.** Redis token bucket per tenant/model, atomic via Lua,
  `429 Retry-After` on exceed — no unbounded queuing.
- **Budgets.** Per-tenant/model token or request caps over a daily or
  monthly period, enforced with a reserve-then-reconcile pattern (exact
  token counts aren't known until generation completes).
- **Provider failover.** Each model maps to an ordered list of Ollama
  backends; a background health check (real generation, not just a
  liveness ping) and circuit breaker route around a down/degraded backend.
  This deployment's chain is 3 hosts running 3 *different* models
  (`qwen3.5:9b` → `qwen3.6:35b` → `qwen3.8:27b-q4_K_M` — see
  `gateway/admin/routing.yaml`), not identical replicas — the response
  always reports which model actually answered.
- **Observability.** Prometheus metrics, OpenTelemetry tracing (2 spans per
  request: inbound + per provider attempt), Grafana dashboards provisioned
  out of the box.
- **Usage accounting.** Every request produces a durable, queryable
  `UsageRecord`.

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI (async) |
| Rate limiting | Redis, token bucket, Lua script for atomicity |
| Budget/usage hot path | Redis counters |
| Durable store | SQLAlchemy → SQLite (dev) / Postgres (prod, `DATABASE_URL`) |
| Metrics | Prometheus (`prometheus-client`) |
| Tracing | OpenTelemetry (OTLP exporter) |
| Dashboards | Grafana (provisioned config, not manual setup) |
| Providers | Ollama (pluggable `LLMProvider`/`EmbeddingProvider` interfaces; only `OllamaProvider`, implementing both, ships in v1) |
| Deploy | Docker Compose |

Full rationale for each choice: `ARCHITECTURE.md` §1.

## Project structure

```
qwen-api-service/
├── app.py                       # FastAPI entrypoint; mounts gateway router + legacy adapter
├── config.py                    # App-shell config (SSL, host/port, legacy handler's default model)
│
├── gateway/                     # The LLM gateway
│   ├── config.py                #   pydantic-settings, env-driven gateway config
│   ├── api/                     #   FastAPI routers
│   │   ├── router.py            #     /v1/chat/completions, /v1/embeddings, /v1/generate-with-image
│   │   ├── admin_router.py      #     /v1/usage (read-only in v1)
│   │   └── deps.py              #     shared FastAPI dependencies (auth, service)
│   ├── core/                    #   Orchestration
│   │   ├── bootstrap.py         #     Wires GatewayService + deps from settings
│   │   ├── service.py           #     GatewayService — handle_request()/handle_embedding_request(), shared _dispatch()
│   │   └── exceptions.py        #     Typed GatewayError hierarchy
│   ├── auth/                    #   API key → Tenant resolution
│   ├── ratelimit/                #   Redis token bucket (Lua script)
│   ├── budget/                  #   Reserve/reconcile budget tracker
│   ├── routing/                 #   Backend registry (chat + embedding sections), circuit breaker, health poller
│   ├── providers/               #   LLMProvider ABC + EmbeddingProvider ABC + OllamaProvider (implements both)
│   ├── models/                  #   Pydantic schemas: tenant, policy, usage, provider, chat, embedding
│   ├── db/                      #   SQLAlchemy session + Alembic migrations
│   ├── observability/           #   Prometheus metrics, OTel tracing, structured logging
│   └── admin/                   #   routing.yaml, tenants.yaml, seed.py — tenant/backend provisioning
│
├── utils/
│   └── clean_gen_response_from_image.py  # Generic LLM-response JSON repair for the legacy endpoint
├── tests/
│   └── gateway/                 # Gateway unit/integration tests (token bucket, budget, failover, auth)
│
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
│       ├── provisioning/        # datasource + dashboard provisioning
│       └── dashboards/          # gateway-overview.json
│
├── docker-compose.yml           # gateway + redis + postgres + prometheus + grafana
├── Dockerfile
├── requirements.txt
├── .env.example
│
├── PRD.md
├── ARCHITECTURE.md
├── ARCHITECTURE-ESSENTIALS.md
├── CLAUDE.md
└── AGENTS.md
```

`agents/`, `clients/`, `prompts/` no longer exist in this repo — the
ITF/NAR extraction pipeline that used to live there now runs as a separate
external web app (see "Consumers" below and ARCHITECTURE.md §9).

## Running locally

### Prerequisites
- Python 3.11+
- Docker + Docker Compose
- The Ollama backends in `gateway/admin/routing.yaml` running and
  reachable, each with its configured model pulled:
  - primary — `172.17.0.1:11434` — `qwen3.5:9b` (chat) and
    `qllama/bge-large-en-v1.5:latest` (embeddings — `ollama pull
    qllama/bge-large-en-v1.5:latest`)
  - secondary 1 — `172.16.13.67:11434` — `qwen3.6:35b`
  - secondary 2 — `172.18.0.1:11434` — `qwen3.8:27b-q4_K_M`
  - The gateway degrades to reduced capacity (fewer failover hops) if a
    secondary is unreachable, not a hard failure — but for full parity
    with the configured chain, all three should be up. The embedding
    model has no failover backend configured today (single entry on
    `ollama-primary`) — see `ARCHITECTURE.md` §13.3.

### Infra only, app on host
```bash
cp .env.example .env      # edit as needed
docker compose up -d redis postgres prometheus grafana
pip install -r requirements.txt
python app.py
```

### Full stack in Docker
```bash
cp .env.example .env
docker compose up -d --build
```

- Gateway: `http://localhost:8000` (or `API_PORT` from `.env`)
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (dashboard provisioned automatically)

### Provisioning a tenant

Nothing can authenticate until at least one tenant is seeded — there is no
internal/implicit tenant and no self-serve signup (v1 scope, PRD §10c):

```bash
# Edit gateway/admin/tenants.yaml first (plaintext_key, rate limits, budget)
python -m gateway.admin.seed
```

This prints each tenant's plaintext API key exactly once — copy it
immediately, it's not recoverable afterward (only the hash is stored).
Give that key to whatever's calling the gateway (e.g. the external web
app's `QWEN_SERVICE_API_KEY` config) as an `Authorization: Bearer <key>`
header.

### Tests
```bash
pytest tests/ -v
```

## API (target shape — see ARCHITECTURE.md §4.5 for full schemas)

```
POST /generate-with-image        # legacy multipart contract — kept for the
                                  # external web app (ARCHITECTURE.md §9)
POST /v1/chat/completions        # provider-agnostic chat/vision request
POST /v1/embeddings              # embedding request (ARCHITECTURE.md §13)
POST /v1/generate-with-image     # legacy multipart contract, canonical response shape
GET  /v1/usage                   # current tenant usage vs. budget
GET  /health                     # liveness
GET  /metrics                    # Prometheus scrape endpoint
```

Every route except `/health` and `/metrics` requires
`Authorization: Bearer <api_key>` — including `/generate-with-image`.
There is no anonymous or internal-tenant path.

## Consumers

The only consumer of this gateway today is a **separate external web app**
(an ITF/NAR medical-form extraction tool) that calls
`POST /generate-with-image` with multipart `image`/`prompt` fields and
expects the legacy `{response, model, timestamp, metrics}` JSON shape
back. See ARCHITECTURE.md §9 for the full integration contract, including
a known gap: as currently observed, that web app does **not** yet send an
`Authorization` header, so every call from it will get `401` until it's
updated to send `Authorization: Bearer <api_key>` using a key provisioned
per "Provisioning a tenant" above.

## What's intentionally not here (v1)

Non-Ollama/cloud providers, caller-facing streaming, an admin CRUD API/UI
for tenants, Kubernetes manifests, semantic response caching, per-request
USD billing, fine-grained RBAC within a tenant. These were evaluated and
cut in the design review — see `PRD.md` §10 and `ARCHITECTURE.md` §12
before re-adding any of them.

**Reranking** (`BAAI/bge-reranker-v2-m3`) specifically: evaluated on
request, not built. It's a cross-encoder with no Ollama-servable form (no
`/api/rerank`), so serving it needs exactly the non-Ollama backend the
first bullet above excludes — a scope decision, not a missed feature. Full
analysis and a concrete implementation plan if that scope decision changes:
`ARCHITECTURE.md` §14.

## Contributing

See `CLAUDE.md` (and `AGENTS.md`) for the working rules this codebase
expects an agent (or a human) to follow — most importantly: there is
exactly one code path to Ollama, no endpoint returns `200` on failure, and
unconfigured tenant/model pairs default to denied, not unlimited.
