# Architecture Essentials

Quick-reference only. Full detail: ARCHITECTURE.md. Product rationale: PRD.md.
Read this first; open the full doc only when you need the "why."

## What this repo is

A single **LLM gateway** (FastAPI) that every LLM request must pass
through to reach Ollama. It owns auth, rate limiting, per-tenant budgets,
provider failover, and observability. There is no in-process consumer
anymore — the ITF/NAR medical-form pipeline that used to live here
(`agents/`, `clients/`, `prompts/`) was removed and now runs as a
**separate external web app**, calling this gateway over HTTP
(`POST /generate-with-image`, a legacy-shaped compatibility endpoint —
ARCHITECTURE.md §9) with a real API key like any other tenant. Generic
JSON-repair utilities that endpoint needs live in
`utils/clean_gen_response_from_image.py`.

## Tech stack (do not swap without updating both docs)

- FastAPI (async) · Redis (token bucket + budget counters, Lua for atomicity)
- SQLAlchemy → SQLite (dev/tests) / Postgres (prod, `DATABASE_URL`) for
  durable usage records + tenant/key config
- Prometheus (`prometheus-client`) for metrics · Grafana (provisioned, not
  hand-configured) for dashboards
- OpenTelemetry — **2 spans per request only** (inbound + per provider
  attempt), not full instrumentation
- Providers: **Ollama only**, 3 backend instances for failover — and they
  run **different models**, not copies of each other (primary
  `qwen3.5:9b` @ `172.17.0.1`, then `qwen3.6:35b` @ `172.16.13.67`, then
  `qwen3.8:27b-q4_K_M` @ `172.18.0.1`; see `gateway/admin/routing.yaml` and
  ARCHITECTURE.md §6.5). No cloud provider in v1. `LLMProvider` ABC stays
  pluggable for later.
- Deploy: Docker Compose only (gateway, redis, postgres, prometheus,
  grafana). No Kubernetes. All 3 Ollama backends run on separate real
  hosts, not containerized by this stack.

## One code path to Ollama

`GatewayService.handle_request()` (`gateway/core/service.py`) is the *only*
function allowed to call a provider. Every route calls it —
`/v1/chat/completions`, `/v1/generate-with-image`, and `app.py`'s legacy
`/generate-with-image` alike, all requiring a real, seeded tenant's API
key (there is no internal/implicit tenant — see ARCHITECTURE.md §9.4). If
you're adding a new caller of Ollama anywhere in this repo, it goes
through this function — never open a new `aiohttp` session to a model
backend directly.

## Request pipeline (in order — do not reorder)

1. Auth: API key → Tenant (401 if invalid; no anonymous path)
2. Rate limit: Redis token bucket, atomic Lua script (429 + Retry-After)
3. Budget: reserve worst-case tokens against tenant+model+period counter
   (default **deny** if no policy exists for that tenant+model)
4. Route: ordered healthy backends for the model (503 if none)
5. Call provider, retry next backend on pre-output failure only (never
   retry after partial output was returned — avoids duplicate billing/work)
6. Reconcile budget with actual token usage from the response
7. Async-write `UsageRecord` (never blocks the response)
8. Emit Prometheus metrics, return response or typed `GatewayError`

## Non-negotiable invariants (from the design review)

- **No 200-with-error-body.** Every failure is a real HTTP status + typed
  `GatewayError` JSON body. (Old bug: `app.py` returned 200 on timeout.)
- **No unbounded queuing.** Rate limit exceeded → 429 immediately, never
  queue-and-wait. (Old bug: bare `asyncio.Semaphore`.)
- **`MAX_IMAGE_SIZE_MB` must actually be enforced** (413) — it was defined
  in `config.py` but never checked anywhere. Don't repeat that.
- **Health checks must run a cheap real generation**, not just ping
  `/api/tags` — a backend can be reachable but have evicted the model from
  VRAM.
- **Failover cold-start latency is expected, not a bug** — track it
  (`gateway_model_load_seconds`), don't try to eliminate it.
- **Default deny**, not default allow, for tenant/model pairs with no
  policy configured.
- **Gateway-generic code never parses domain JSON.** Response repair
  (`repair_trailing_bare_strings` etc., generic LLM-output cleanup, not
  gateway logic) lives in `utils/clean_gen_response_from_image.py`, applied
  *after* the gateway returns a raw string — never inside `gateway/`.
- **No internal/implicit tenant.** Every caller — including `app.py`'s own
  legacy `/generate-with-image` handler — authenticates with a real API
  key via `gateway.api.deps.authenticated_tenant`. Don't add a
  code-provisioned "trusted" tenant as a shortcut; provision a real one
  with `python -m gateway.admin.seed` instead (ARCHITECTURE.md §9.3).
- **Redis down → fail closed on rate limiting** (reject with 429), not
  fail open. A rate limiter that silently stops limiting under a Redis
  outage is worse than brief unavailability.
- **Rate limiting/budgets are keyed on the requested model, not whichever
  model actually answered.** The failover chain is heterogeneous (§6.5) —
  a tenant's `qwen3.5:9b` budget governs the request even when a
  secondary running `qwen3.6:35b` served it. `ChatCompletionResponse.model`
  and `UsageRecord.served_model` report the actual model; `UsageRecord.model`
  and every rate-limit/budget lookup stay on the requested one.
- **`GATEWAY_MAX_FAILOVER_ATTEMPTS` must be `>=` the longest chain in
  `routing.yaml`** (currently 3) or trailing backends are silently never
  tried. If you extend a chain, bump this too.

## Explicitly out of scope for v1 — don't build these unasked

Cloud/non-Ollama providers · caller-facing streaming · admin CRUD API/UI for
tenants (config-seeded instead) · Kubernetes manifests · semantic caching ·
per-request USD billing · fine-grained RBAC within a tenant.

## Folder map

```
gateway/
  api/          FastAPI routers (chat completions, legacy adapter, usage, health)
  core/         GatewayService orchestration + GatewayError types
  auth/         API key → Tenant resolution
  ratelimit/    Redis token bucket (Lua script)
  budget/       Reserve/reconcile budget tracker
  routing/      Backend registry, circuit breaker, health poller
  providers/    LLMProvider ABC + OllamaProvider
  models/       Pydantic schemas (tenant, policy, usage, provider, chat)
  db/           SQLAlchemy session + migrations
  observability/ Prometheus metrics, OTel tracing, structured logging
  admin/        Tenant/API-key seeding from config
monitoring/     prometheus.yml, grafana provisioning + dashboards
utils/          clean_gen_response_from_image.py — generic LLM JSON repair
                for app.py's legacy /generate-with-image handler
```

`agents/`, `clients/`, `prompts/` no longer exist in this repo — the
ITF/NAR pipeline that used to live there is now a separate external web
app (ARCHITECTURE.md §9). Don't recreate them here; that pipeline's home
is elsewhere now.

## When in doubt

If a change would add a second way to reach Ollama, add unbounded queuing,
return 200 on failure, or default-allow an unconfigured tenant/model pair —
stop and check ARCHITECTURE.md §12 / PRD.md §10 before proceeding; these
were explicit review findings, not oversights.
