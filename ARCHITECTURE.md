# Architecture — Qwen LLM Gateway

**Companion to**: PRD.md (product requirements) · ARCHITECTURE-ESSENTIALS.md (condensed reference)
**Status**: Draft v2 (post design-review, see §12)
**Last updated**: 2026-09-03

This document is the complete technical design. If you're an agent working
in this repo and don't need the full detail, read ARCHITECTURE-ESSENTIALS.md
instead — it's the ≤150-line version of everything that actually constrains
day-to-day implementation decisions.

---

## 1. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API framework | **FastAPI** (already in use) | Async-native, matches existing `app.py`; no framework migration needed. |
| Rate limiting | **Redis** token bucket (Lua script) | Atomic cross-replica counters; sub-millisecond; the standard choice for this pattern. |
| Budget/usage hot path | **Redis** counters | Same store as rate limiting — one dependency, not two, for the hot path. |
| Durable usage log / tenant config | **SQLAlchemy** over **SQLite** (dev) / **Postgres** (prod, via `DATABASE_URL`) | Async writes off the hot path; SQLite needs zero infra for local dev/tests, Postgres is a one-env-var swap in Docker Compose. |
| Observability — metrics | **Prometheus** (`prometheus-client`) | Pull-based, pairs with Grafana, near-zero overhead. |
| Observability — tracing | **OpenTelemetry** (SDK + OTLP exporter), scoped to 2 spans/request | See §12(c) — deliberately not instrumenting everything. |
| Dashboards | **Grafana**, provisioned via config (not manual clicking) | Datasource + dashboard JSON checked into `monitoring/`. |
| Providers | **Ollama** (one adapter, 3 backend instances, heterogeneous models — §6.5) | Matches current deployment; see PRD §10(c) for why cloud providers aren't in v1. |
| Deploy | **Docker Compose** | The gateway/infra stack runs on one box; the 3 Ollama backends are separate hosts reached over the network (§10) — not containerized by this stack. See PRD §10(c) for why not Kubernetes. |
| Config | **pydantic-settings** | Typed env-var config for `gateway/`. `app.py`'s FastAPI app shell keeps a small, separate `config.Config` class (SSL, host/port, the legacy handler's default model) — see §9/§11. |

## 2. System Overview

```
                    ┌─────────────────────────────────────────┐
                    │              Callers                    │
                    │  - External web app (ITF/NAR form UI)    │
                    │    POST /generate-with-image (§9)        │
                    │  - Other external services (/v1/*)       │
                    └───────────────────┬───────────────────────┘
                                        │  Authorization: Bearer <key>
                                        ▼
┌───────────────────────────────────────────────────────────────────────┐
│                          GATEWAY  (FastAPI process)                    │
│                                                                          │
│   api/router.py                                                        │
│     POST /v1/chat/completions                                          │
│     POST /v1/generate-with-image   (legacy-shaped adapter)             │
│     GET  /v1/usage                                                     │
│     GET  /health  /healthz/ready  /metrics                             │
│         │                                                               │
│         ▼                                                               │
│   core/service.py :: GatewayService.handle_request()                   │
│         │                                                               │
│    1.   ▼  auth/api_keys.py         → resolve Tenant, or 401            │
│    2.   ▼  ratelimit/token_bucket.py → check+consume, or 429            │
│    3.   ▼  budget/tracker.py         → reserve estimate, or 402/warn    │
│    4.   ▼  routing/registry.py       → pick backend(s) for model        │
│    5.   ▼  providers/ollama.py       → call backend, retry on failure   │
│    6.   ▼  budget/tracker.py         → reconcile actual usage           │
│    7.   ▼  db (async)                → write UsageRecord                │
│    8.   ▼  observability/metrics.py  → record latency/outcome           │
│         │                                                               │
│         ▼  response (or typed GatewayError)                            │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │
              ┌─────────────┼──────────────┬───────────────┬───────────────┐
              ▼             ▼              ▼               ▼               ▼
        ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
        │  Redis   │  │ Postgres │  │Ollama:primary│ │Ollama:sec-1│  │Ollama:sec-2│
        │ (buckets,│  │ (SQLite  │  │172.17.0.1    │ │172.16.13.67│  │172.18.0.1  │
        │ budgets, │  │  in dev) │  │qwen3.5:9b    │ │qwen3.6:35b │  │qwen3.8:27b │
        │ breaker) │  │ tenants, │  └────────────┘  └────────────┘  │-q4_K_M     │
        │          │  │ usage    │                                  └────────────┘
        └──────────┘  └──────────┘                    (see §6.5 — different models,
                                                         not mirrored replicas)

        Prometheus scrapes /metrics  →  Grafana dashboards
```

## 3. Request Lifecycle

1. **Auth** — extract `Authorization: Bearer <key>`, hash it, look up
   `ApiKey` → `Tenant`. Missing/invalid/revoked key → `401`. No key
   resolves to no tenant; there is no anonymous path.
2. **Rate limit** — `token_bucket.check_and_consume(tenant_id, model)`
   against Redis via a single atomic Lua script (read-compute-write in one
   round trip — avoids the classic read-then-write race under concurrent
   requests from the same tenant). Exceeded → `429` + `Retry-After` computed
   from the bucket's refill rate.
3. **Budget check (reserve)** — look up the tenant's `BudgetPolicy` for this
   model. If none exists, **default deny** (PRD §10b). Compute a worst-case
   token estimate (`prompt_tokens_estimate + num_predict`) and reserve it
   against the period counter in Redis (`INCRBY`, with the period key
   embedding the boundary, e.g. `budget:{tenant}:{model}:2026-09`). If the
   reservation would exceed `max_tokens`/`max_requests`:
   - `on_exceed=block` → `402 Payment Required` style `BudgetExceeded` error.
   - `on_exceed=warn` → proceed, emit a `budget_warning` metric/log.
4. **Routing** — `registry.get_backends(model)` returns the ordered,
   circuit-breaker-filtered list of healthy backends for the model. Empty
   list → `503 AllProvidersUnavailable`.
5. **Provider call** — call backend #1 with the configured timeout. On
   connection error/timeout/5xx **before any output was produced**, mark
   that backend's breaker failure count, and retry backend #2 (up to
   `MAX_FAILOVER_ATTEMPTS`, default 2). A failure **after** partial output
   was already returned to the caller is *not* retried — it's surfaced as an
   error (see PRD §10b, no duplicate-generation retries).
6. **Reconcile** — on success, replace the reserved estimate with the actual
   `prompt_eval_count + eval_count` from Ollama's final chunk
   (`INCRBY` the delta, which may be negative). On failure, release the
   full reservation.
7. **Durable log** — enqueue a `UsageRecord` write (async task / background
   queue), never blocking the response on the DB write.
8. **Metrics + response** — record Prometheus observations, return the
   response (or a typed `GatewayError` mapped to the right HTTP status).

## 4. Data Models

All models live in `gateway/models/`. Pydantic for request/response and API
validation; SQLAlchemy ORM classes for the durable store (Postgres/SQLite) —
kept as parallel, intentionally simple, not double-mapped through a single
mega-class. Redis holds only counters/state, not modeled as Python classes.

### 4.1 Tenant (`gateway/models/tenant.py`)

```python
class Tenant(BaseModel):
    id: UUID
    name: str
    status: Literal["active", "suspended"]
    created_at: datetime

class ApiKey(BaseModel):
    id: UUID
    tenant_id: UUID
    key_hash: str          # sha256 hex digest, never the plaintext key
    prefix: str             # e.g. "gw_live_ab12" — safe to display/log
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
```

### 4.2 Policies (`gateway/models/policy.py`)

```python
class RateLimitPolicy(BaseModel):
    tenant_id: UUID
    model: str                      # "*" = applies to all models
    requests_per_minute: int
    burst: int                      # token bucket capacity

class BudgetPolicy(BaseModel):
    tenant_id: UUID
    model: str                      # "*" = applies to all models
    period: Literal["daily", "monthly"]
    max_tokens: int | None
    max_requests: int | None
    max_cost_usd: float | None      # unused in v1 (no paid providers); kept
                                     # so adding one later isn't a migration
    on_exceed: Literal["block", "warn"]
    alert_threshold_pct: int = 80
```

A tenant+model pair with **no** matching policy is treated as **not
entitled to that model** (default deny — PRD §10b), not unlimited access.

### 4.3 Usage (`gateway/models/usage.py`)

```python
class UsageRecord(BaseModel):
    id: UUID
    request_id: UUID                # correlates to the trace span
    tenant_id: UUID
    api_key_id: UUID
    model: str                      # requested model — the rate-limit/budget key
    served_model: str | None        # actual model that answered, may differ on
                                     # failover (§6.5); None if nothing succeeded
    backend_id: str                 # which Ollama instance actually served it
    status: Literal[
        "success", "provider_error", "timeout",
        "rate_limited", "budget_exceeded", "all_providers_down",
    ]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    failover_attempts: int          # 0 = served by the first backend tried
    budget_period_key: str          # e.g. "2026-09" — the period this was
                                     # billed against, fixed at request start
                                     # (PRD §10b: no cross-boundary leakage)
    created_at: datetime
```

### 4.4 Providers/routing (`gateway/models/provider.py`)

```python
class ProviderBackend(BaseModel):
    id: str                          # e.g. "ollama-gpu1"
    base_url: str
    models: list[str]                # models this backend can serve
    priority: int                    # lower = tried first, within a model
    enabled: bool = True
    keep_alive: str = "5m"
    target_model: str | None = None  # actual model requested from THIS
                                      # backend; None = same as requested
                                      # (§6.5 — chains may be heterogeneous)

class BackendHealth(BaseModel):
    backend_id: str
    healthy: bool
    consecutive_failures: int
    circuit_state: Literal["closed", "open", "half_open"]
    last_checked_at: datetime
    last_error: str | None
```

`ProviderBackend` list is static config (env/YAML, loaded at startup —
no hot-reload in v1). `BackendHealth` is runtime state cached in Redis and
refreshed by a background poller (§6.2).

### 4.5 Chat request/response (`gateway/models/chat.py`)

Provider-agnostic shape, intentionally close to the OpenAI chat schema so a
future non-Ollama provider adapter doesn't need a new request contract:

```python
class ContentPart(BaseModel):
    type: Literal["text", "image_base64"]
    text: str | None = None
    image_base64: str | None = None
    media_type: str | None = None    # e.g. "image/png"

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[ContentPart]

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 1
    seed: int | None = 42            # matches current deterministic defaults
    num_predict: int = 4096          # hard cap; also the budget reservation basis
    stream: bool = False             # v1: caller-facing streaming not supported (§7)

class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletionResponse(BaseModel):
    id: UUID                          # == request_id
    model: str                        # model that ACTUALLY answered — may
                                       # differ from the request's model on
                                       # a heterogeneous-chain failover (§6.5)
    backend_used: str
    content: str
    finish_reason: str
    usage: ChatCompletionUsage
    latency_ms: int
```

### 4.6 Errors (`gateway/core/exceptions.py`)

```python
class GatewayError(Exception):
    http_status: int

class AuthenticationError(GatewayError):     http_status = 401
class RateLimitExceeded(GatewayError):       http_status = 429  # + Retry-After
class BudgetExceeded(GatewayError):          http_status = 402
class ModelNotEntitled(GatewayError):        http_status = 403
class AllProvidersUnavailable(GatewayError): http_status = 503
class ProviderTimeout(GatewayError):         http_status = 504
class PayloadTooLarge(GatewayError):         http_status = 413
```

No handler ever returns `200` with an error string in the body (PRD §10a) —
every failure mode above maps to a real status code and a typed JSON error
body: `{"error": {"type": "...", "message": "...", "request_id": "..."}}`.

## 5. Rate Limiting Design

- **Algorithm**: token bucket, one bucket per `(tenant_id, model)`.
- **Storage**: Redis hash `ratelimit:{tenant_id}:{model}` with fields
  `tokens` and `last_refill_ts`.
- **Atomicity**: a single Lua script does refill-compute + consume-if-available
  in one round trip (`EVALSHA`), so concurrent requests from the same tenant
  can't both read "1 token left" and both proceed.
- **Response on exceed**: `429`, `Retry-After: <seconds until next token>`.
- **Redis-down behavior**: fail closed — reject with `429` and a
  `Retry-After: 5` hint. Rationale (PRD §10a): a gateway that goes
  fail-open on its rate limiter under a Redis outage turns "Redis is down"
  into "there is no rate limiting," which is worse than brief unavailability.
  This is revisited if Redis HA (Sentinel/Cluster) is added — out of scope
  for v1's single-instance Redis.

## 6. Provider Failover Design

### 6.1 Backend registry

Static config in `gateway/admin/routing.yaml`. The real chain in this
deployment is 3 backends deep, and — this matters, see §6.5 — the two
secondaries run **different models**, not copies of the primary:

```yaml
backends:
  ollama-primary:
    base_url: http://172.17.0.1:11434
    keep_alive: 5m
  ollama-secondary-1:
    base_url: http://172.16.13.67:11434
    keep_alive: 5m
  ollama-secondary-2:
    base_url: http://172.18.0.1:11434
    keep_alive: 5m

models:
  "qwen3.5:9b":
    - backend: ollama-primary
      priority: 0
      # no model_name override — mirrors the requested model
    - backend: ollama-secondary-1
      model_name: "qwen3.6:35b"
      priority: 1
    - backend: ollama-secondary-2
      model_name: "qwen3.8:27b-q4_K_M"
      priority: 2
```

`GATEWAY_MAX_FAILOVER_ATTEMPTS` must be `>=` the longest chain here (3) or
trailing entries are silently never tried — see `gateway/config.py`.

### 6.2 Health checks

A background task polls each backend every `HEALTH_CHECK_INTERVAL_S`
(default 15s) with a **cheap real generation** against each model it's
configured to serve (e.g. `num_predict=1`), not just `GET /api/tags`. A
backend that's reachable but has evicted the model from VRAM must fail this
check — a liveness-only ping would report it healthy right up until a real
request hangs for the full timeout (PRD §10a).

### 6.3 Circuit breaker

Per `(backend_id, model)`:
- `closed` (normal) → after `N` (default 3) consecutive failures → `open`
  (skipped by routing for `COOLDOWN_S`, default 30s) → `half_open` (one
  probe request allowed) → `closed` on success or back to `open` on failure.
- State lives in Redis so it's shared across gateway replicas.

### 6.4 Failover on request failure

- Only failures that occur **before any content was returned to the
  caller** trigger a retry on the next backend. A mid-stream failure is
  reported as an error, not retried — retrying after partial output risks
  double-charging the budget and duplicating GPU work for output the caller
  may have already partially received (PRD §10b).
- Max attempts: `MAX_FAILOVER_ATTEMPTS` (default 3, matching the real
  3-backend chain in `routing.yaml` — see §6.1).
- Failing over to a cold backend (model not resident in VRAM) trades an
  outage for a load-time latency spike — this is expected, not a bug.
  `gateway_model_load_seconds` is a named metric specifically so this is
  visible on the dashboard instead of looking like an unexplained latency
  outlier (PRD §10a). Mitigation: keep `OLLAMA_KEEP_ALIVE` high on every
  backend in a model's failover chain.

### 6.5 Heterogeneous models across a failover chain

The failover chain in this deployment is **not** the same model mirrored
on backup hardware — it's three different models on three different
hosts:

| Priority | Backend | Host | Model |
|---|---|---|---|
| 0 (primary) | `ollama-primary` | `172.17.0.1:11434` | `qwen3.5:9b` |
| 1 | `ollama-secondary-1` | `172.16.13.67:11434` | `qwen3.6:35b` |
| 2 | `ollama-secondary-2` | `172.18.0.1:11434` | `qwen3.8:27b-q4_K_M` |

This changes what "failover" means and touches several parts of the
design that assumed (or would naturally default to) identical models
across a chain:

- **Requested model vs. served model are tracked separately.**
  `ProviderBackend.target_model` (`gateway/models/provider.py`) is the
  model name actually sent to a given backend — set per routing-table
  entry via `model_name` in `routing.yaml`, defaulting to the requested
  model name when omitted (the "mirrored" case). `OllamaProvider` sends
  `target_model` to Ollama, not the tenant's requested model name, when
  the two differ.
- **`ChatCompletionResponse.model` reports what actually answered, not
  what was requested** — consistent with how most chat-completion APIs
  use that field. A request for `qwen3.5:9b` that fails over to
  `ollama-secondary-1` gets `model="qwen3.6:35b"` back, so the caller
  (and `app.py`'s legacy handler, which surfaces it in the response's
  `metrics.backend_used` alongside it) can tell a fallback model answered.
- **Rate limiting and budgeting stay keyed on the requested model**, not
  the served one — a tenant's `RateLimitPolicy`/`BudgetPolicy` for
  `qwen3.5:9b` governs the request regardless of which backend/model
  actually served it. Charging budget against whichever model happened to
  answer would make a tenant's budget depend on backend health, which is
  not a knob they control.
- **`UsageRecord` carries both**: `model` (requested, the
  rate-limit/budget key) and `served_model` (actual, nullable — set only
  on success). This is the field to query when auditing "how often did
  tenant X actually get answered by the 35B fallback instead of the 9B
  primary."
- **Expect materially different latency and output characteristics on
  failover** — `qwen3.6:35b` and `qwen3.8:27b-q4_K_M` are not
  drop-in equivalents of `qwen3.5:9b` in speed or extraction behavior (the
  external web app's form-extraction use case cares about this, even
  though that logic no longer lives in this repo — §9). This is a
  deliberate scope note, not something to "fix" by trying to make the
  models equivalent — the whole point of this chain is "stay up on
  whatever hardware is available,"
  not "guarantee identical output."

## 7. Why No Caller-Facing Streaming in v1

Ollama's API streams NDJSON internally, and the gateway's provider adapter
consumes that stream today (mirroring the current `app.py` behavior) — but
the gateway's public response contract is buffer-and-return, not
server-sent-events to the caller. Reasons:
- Budget reconciliation needs the final `eval_count`, which only arrives in
  the last chunk anyway — streaming to the caller wouldn't let budget
  enforcement happen any earlier.
- Rate limiting and failover both need a clean "did this request succeed or
  fail" boundary; retrying a partially-streamed response to a caller who
  already received tokens is the double-generation problem from §6.4.
- The only current consumer (the external web app, via `/generate-with-image`
  — §9) already buffers the full response before parsing JSON out of it —
  nothing today needs streaming.

This is a deliberate v1 scope cut, not a limitation of the design — the
provider interface (`LLMProvider.generate`) is a natural place to add a
`generate_stream` method later without restructuring routing/budget/auth.

## 8. Observability

### 8.1 Metrics (`gateway/observability/metrics.py`, Prometheus)

| Metric | Type | Labels |
|---|---|---|
| `gateway_requests_total` | Counter | `tenant`, `model`, `status` |
| `gateway_request_latency_seconds` | Histogram | `tenant`, `model` |
| `gateway_tokens_total` | Counter | `tenant`, `model`, `kind` (prompt/completion) |
| `gateway_rate_limit_rejections_total` | Counter | `tenant` |
| `gateway_budget_rejections_total` | Counter | `tenant`, `model` |
| `gateway_failover_attempts_total` | Counter | `model`, `from_backend`, `to_backend` |
| `gateway_backend_health` | Gauge (0/1) | `backend_id`, `model` |
| `gateway_model_load_seconds` | Histogram | `backend_id`, `model` |

### 8.2 Tracing (OpenTelemetry, OTLP exporter)

Two spans per request, deliberately scoped down from instrumenting every
internal call (PRD §10c):
1. `gateway.request` — the whole `handle_request()` call, tagged with
   tenant/model/status.
2. `gateway.provider_call` — one child span per backend attempt (so a
   failover shows up as two sibling spans, not one misleading span).

### 8.3 Dashboards (Grafana, provisioned)

`monitoring/grafana/dashboards/gateway-overview.json` — traffic, error rate,
p50/p95/p99 latency, token throughput, rate-limit/budget rejection rate,
per-backend health and failover events. Provisioned via
`monitoring/grafana/provisioning/` so `docker compose up` produces a working
dashboard with no manual clicking.

## 9. External Consumers: the Web App and `/generate-with-image`

**This section describes the second real revision of this repo's shape.**
The ITF/NAR medical-form extraction pipeline (`agents/`, `clients/`,
`prompts/`, and their tests) — which the earlier revision of this document
described integrating in-process via a reserved internal service tenant —
**has been removed from this repo entirely.** It now lives in a separate
web app repo that talks to this gateway over HTTP, like any other tenant.
There is no in-process consumer left, and no internal/implicit tenant:
**every** caller of this gateway authenticates with a real, seeded API key
(CLAUDE.md hard rule — no bypass, not even for "trusted" callers).

### 9.1 What actually calls this gateway now

The external web app calls `POST {QWEN_SERVICE_URL}/generate-with-image`
with multipart form data (`image` + `prompt` fields) and reads back a JSON
body — the exact shape it's always gotten:

```python
async with aiohttp.ClientSession(connector=connector) as session:
    async with session.post(
        f"{settings.QWEN_SERVICE_URL}/generate-with-image",
        data=data,
        timeout=timeout_obj,
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            return {"error": f"API error {response.status}", "details": error_text}
        result = await response.json()
        return result
```

Two things worth flagging about this snippet as observed (not assumed):

- **It doesn't send an `Authorization` header.** `/generate-with-image`
  now requires one (§9.2) — this is an integration gap on the *web app*
  side that needs fixing there, not something this repo can silently paper
  over. Until the web app sends `Authorization: Bearer <api_key>`, every
  call from it will get a `401`.
- **It treats a non-200 response as `{"error": ..., "details": ...}`**,
  not a raised exception — so a `401`/`413`/`429`/`503` from the gateway
  surfaces as that dict, not a crash. Worth knowing when debugging "why did
  the web app say X" — check for this shape before assuming the gateway is
  down.

### 9.2 How `/generate-with-image` works now

`app.py`'s `/generate-with-image` handler is a **legacy-shaped adapter**,
not a special/internal path:

- It requires `Authorization: Bearer <api_key>`, resolved via
  `gateway.api.deps.authenticated_tenant` — the exact same dependency
  `/v1/chat/completions` and `/v1/generate-with-image` use
  (`gateway/api/router.py`). A missing/invalid/revoked key gets `401`,
  same as everywhere else.
- It builds a `ChatCompletionRequest` from the uploaded image + prompt and
  calls `GatewayService.handle_request()` in the same process (a Python
  function call, not a second HTTP hop) — this is still the one function
  capable of reaching Ollama (§2).
- It returns the **legacy response shape**
  (`{response, model, timestamp, metrics}`), not the `ChatCompletionResponse`
  shape `/v1/generate-with-image` returns — kept specifically so the
  external web app's existing JSON-parsing code doesn't need to change,
  only its request needs an `Authorization` header added.
- Generic JSON-repair post-processing (`strip_markdown_code_blocks`,
  `repair_trailing_bare_strings`, `repair_unescaped_quotes`,
  `clean_json_string` — `utils/clean_gen_response_from_image.py`) is
  applied to `GatewayService`'s raw string output *after* the call
  returns. This is **not** gateway code and never was ITF/NAR-specific in
  a way that mattered to the gateway — it's generic "make LLM output
  parseable JSON" cleanup for this one legacy-shaped endpoint. The gateway
  itself never interprets response content (PRD §10a).
- If the gateway package fails to import, or `GatewayRuntime` fails to
  build at startup (Redis/DB unreachable), the handler returns `503`
  rather than falling back to a second, unmetered path to Ollama — see the
  `GATEWAY MOUNTING` comment block at the top of `app.py`.

### 9.3 Provisioning the web app as a tenant

Before the web app (or anything else) can call this gateway, it needs a
seeded tenant + API key:

```bash
python -m gateway.admin.seed   # reads gateway/admin/tenants.yaml
```

`gateway/admin/tenants.yaml` ships a `web-app` entry as a starting point —
edit its `plaintext_key` (and rate-limit/budget policy) before running
this in anything but local dev, then hand the resulting key to the web
app's config (e.g. as `QWEN_SERVICE_API_KEY`) so it can set the
`Authorization` header. There is no auto-provisioning at app startup
(PRD §10c — no admin CRUD API in v1); this is a deliberate, one-time
operator step per tenant.

### 9.4 What this means for `gateway/`'s "single choke point" guarantee

`GatewayService.handle_request()` is still the only function capable of
reaching a provider (ARCHITECTURE-ESSENTIALS.md "One code path to
Ollama") — that invariant doesn't depend on who's calling it. What changed
is that there is no longer a caller that gets to skip authentication:
previously the in-process ITF/NAR pipeline authenticated as a
code-provisioned internal tenant; now every caller, including this repo's
own legacy `/generate-with-image` handler, resolves a real tenant from the
`Authorization` header via the same dependency. This is a strictly
stronger security posture than the revision it replaces, not a
regression — the earlier "internal tenant" design was already documented
as a concession to having an in-process caller (§9 of the prior revision);
now that concession is gone because the caller it was for is gone.

### 9.5 Verification status

Not yet exercised against a live Ollama backend, a live external web app,
or `docker compose up`. Verified via: unit tests (`tests/gateway/`,
including DB-backed auth tests against unknown/revoked/suspended-tenant
keys — `tests/gateway/test_auth.py`), and an end-to-end smoke test using
`fakeredis` + SQLite + a stub provider that exercises the full path
`resolve_tenant() → GatewayService.handle_request() → utils/
clean_gen_response_from_image.py post-processing`, confirming a real
seeded tenant (not an internal/default one) is what ends up attached to
the request and the usage record.

## 10. Deployment (Docker Compose)

Services: `gateway` (this app), `redis`, `postgres`, `prometheus`,
`grafana`. Ollama itself is **not** containerized by this stack — all
three backends in the failover chain are separate real hosts, reached
directly over the network (`172.17.0.1`, `172.16.13.67`, `172.18.0.1`;
see §6.1/§6.5), not spun up by this compose file.
`gateway/admin/routing.yaml`, not `.env.example`, is the source of truth
for that topology — `.env.example`'s `OLLAMA_BASE_URL`/`MODEL_NAME` only
configure the primary/default model for `app.py`'s legacy handler, not the
failover chain itself.

See ARCHITECTURE-ESSENTIALS.md for the compose service list and
AGENTS.md/CLAUDE.md for how to run it locally.

## 11. What Changed, File by File

| File | Change |
|---|---|
| `app.py` | **Done.** Mounts `gateway/api/router.py` (adds `/v1/chat/completions`, `/v1/generate-with-image`, `/metrics`). `lifespan()` calls `gateway.core.bootstrap.build_gateway_service()` and stores the result on `app_state`/`app.state`. The `/generate-with-image` handler no longer talks to Ollama directly and no longer authenticates as an internal tenant — it requires a real `Authorization: Bearer <api_key>` header (`gateway.api.deps.authenticated_tenant`), builds a `ChatCompletionRequest`, and calls `GatewayService.handle_request()` in-process; returns `503` if the gateway didn't initialize, `401` if auth fails. SSL setup unchanged. |
| `config.py` | **Trimmed.** `agents/`/`clients/`/`prompts/` are gone, so their settings (`OLLAMA_BASE_URL`, `OLLAMA_KEEP_ALIVE`, `REQUEST_TIMEOUT`, `PROMPTS_DIR`/`PROMPT_NAMING_FORMAT`/`DEFAULT_PROMPT_*`, `TRACE_DIR`/`LOG_LLM_TRACE`, `ensure_directories()`) were removed — they were dead code, not just unused. What's left: `MODEL_NAME` (legacy handler's default), API host/port/title/version, SSL, `MAX_CONCURRENT_REQUESTS`, image size limit, log level. Gateway config stays separate in `gateway/config.py` (`pydantic-settings`). |
| `agents/`, `clients/`, `prompts/` | **Removed entirely.** The ITF/NAR extraction pipeline now lives in a separate web app repo — see §9. |
| `utils/clean_gen_response_from_image.py` | **New home for generic JSON-repair.** `strip_markdown_code_blocks`, `clean_json_string`, `repair_trailing_bare_strings`, `repair_unescaped_quotes` — imported by `app.py`'s legacy handler, applied to `GatewayService`'s raw output. Not gateway code (§9.2). |
| `gateway/admin/seed.py` | `ensure_internal_tenant()` removed — there's no internal tenant anymore. `seed_from_config()` is now the only way any tenant, including the external web app, gets provisioned (§9.3). |
| `gateway/core/bootstrap.py` | `GatewayRuntime` no longer carries `internal_tenant`/`internal_api_key` — every caller supplies its own via `authenticated_tenant`. |
| `Dockerfile` | Fixed: no longer `COPY`s `agents/`, `clients/`, `prompts/` (would fail — those directories don't exist). Copies `gateway/`, `utils/`, `tests/` instead. |
| `tests/` | `test_multi_form_generation.py`, `test_prompt_management.py`, `test_schema_loading.py`, and `test_data/` (orphaned ITF/NAR sample images) removed along with the code they tested. `tests/gateway/` (new) covers rate limiting, budgets, failover/circuit-breaker, and — now load-bearing since there's no internal tenant — DB-backed auth tests against unknown/revoked/suspended-tenant keys. |
| `docker-compose.yml` | Adds `redis`, `postgres`, `prometheus`, `grafana` services. |
| `requirements.txt` | Adds `redis`, `sqlalchemy`, `greenlet` (SQLAlchemy's async engine needs it at runtime — missing from the original dependency list), `alembic`, `pydantic-settings`, `prometheus-client`, `opentelemetry-*`. |

## 12. Design Review — Hard Questions (technical detail)

Product-level rationale for each item lives in PRD.md §10; this is the
technical mechanism behind each fix, for implementers.

**(a) What breaks** → reserve/reconcile budget accounting (§3 step 3/6, §4.3
note on `budget_period_key`), real-generation health checks (§6.2), typed
`GatewayError`s replacing 200-with-error-body (§4.6), Redis fail-closed for
rate limiting (§5).

**(b) Missing edge cases** → `MAX_IMAGE_SIZE_MB` enforcement becomes a real
`413 PayloadTooLarge` check in `api/router.py` before the request reaches
the core service (was defined in `config.py`, never enforced); rate limiting
produces `429` instead of unbounded semaphore queuing (§5); no-retry-after-
partial-output rule prevents duplicate generation on failover (§6.4);
budget period is fixed at request start and carried in `UsageRecord.
budget_period_key` so a request spanning a period boundary reconciles
against the period it started in (§4.3); default-deny for
tenant/model pairs with no policy (§4.2).

**(c) Over-engineering cut** → no second provider adapter ships (§1, only
`OllamaProvider`); OTel scoped to 2 spans/request instead of full internal
instrumentation (§8.2); no Kubernetes manifests (§10, Compose only); no
admin CRUD API for tenants (config-seeded, §4.1); no streaming to callers
in v1 (§7); no cost/billing logic despite the schema field existing (§4.2).
