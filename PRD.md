# PRD — Qwen LLM Gateway

**Status**: Draft v3 (revised after design review, see §10; revised again
after the ITF/NAR pipeline was extracted into a separate repo, see §2/§3)
**Owner**: Platform/Backend
**Last updated**: 2026-09-03

**v3 note**: v2 of this document assumed the ITF/NAR medical-form
extraction pipeline (`agents/`, `clients/`, `prompts/`) would stay in this
repo as an in-process consumer, authenticating as a reserved internal
service tenant. That pipeline has since been removed from this repo
entirely and now runs as a separate external web app, calling this
gateway over HTTP with a real API key like any other tenant. §2 and §3
below describe the current shape; §1's problem statement is unchanged
(the reasons this gateway needed to exist don't depend on where its first
consumer's code lives).

---

## 1. Problem Statement

Historically, `qwen-api-service` was a single-purpose FastAPI wrapper
around one Ollama instance: `app.py` opened one `aiohttp` session at
startup, and `/generate-with-image` streamed straight to
`OLLAMA_BASE_URL`. It was built to serve one consumer — an ITF/NAR
medical-form extraction pipeline that lived in this repo's `agents/`
directory at the time (since removed; it now runs as a separate external
web app — see §2) — and it showed:

- **No auth.** Anyone who can reach the port can burn GPU time.
- **No rate limiting beyond a bare `asyncio.Semaphore(10)`.** Requests past
  the limit queue forever instead of getting a 429 — there is no backpressure
  signal to callers.
- **No budgets.** There is no way to answer "how many tokens did team X use
  this month" short of grepping logs.
- **No failover.** `OLLAMA_BASE_URL` is one host. If that box's GPU driver
  hangs, restarts, or is mid-model-swap, every caller gets connection errors
  or a hung request until `REQUEST_TIMEOUT` (600s) fires.
- **No observability beyond text logs.** No metrics, no dashboards, no way to
  see p50/p95 latency, error rate, or token throughput without reading logs.
- **Silent failure modes.** A timeout returns HTTP 200 with
  `"response": "Time out error"` in the body (`app.py:414-425`) — callers that
  don't inspect the payload treat a failed request as a successful one.

As more internal consumers want LLM access (not just the ITF/NAR pipeline),
routing every call through ad-hoc `aiohttp` sessions per-service stops
scaling: each new consumer would re-implement auth, retries, and rate
limiting badly, and nobody can answer "what is our total LLM spend/load"
across all of them.

## 2. What We're Building

A **single LLM gateway** that every LLM request in this deployment — internal
(the ITF/NAR pipeline) and external (other services) — is routed through.
The gateway is the only thing allowed to hold an open connection to Ollama.
It owns:

1. **AuthN** — API-key-based tenant identification.
2. **Rate limiting** — Redis token bucket, per tenant, with burst + sustained
   caps, returning `429` + `Retry-After` instead of queuing indefinitely.
3. **Budgets** — per-tenant token/request caps over a rolling period (daily
   or monthly), enforced before the request is dispatched and reconciled
   against actual usage after.
4. **Provider failover** — routes each model to an ordered list of Ollama
   backends; on failure/timeout/unhealthy, retries the next backend in the
   list before failing the caller.
5. **Observability** — Prometheus metrics + Grafana dashboards for latency,
   error rate, token throughput, rate-limit/budget rejections, and failover
   events; OpenTelemetry tracing across the gateway → provider hop.
6. **Usage accounting** — a durable, queryable record of every request
   (tenant, model, backend, tokens, latency, outcome).

The ITF/NAR form-extraction pipeline that originally motivated this
gateway (`agents/`, `clients/`, `prompts/`) has been **removed from this
repo** and now runs as a separate external web app. It is the gateway's
first real consumer, but it reaches the gateway the same way every other
consumer does — over HTTP, with a real API key
(`POST /generate-with-image`, a legacy-shaped compatibility endpoint kept
specifically so that web app's existing request/response contract doesn't
need to change; see ARCHITECTURE.md §9). There is no in-process caller and
no internal/implicit tenant: **every** request, from any consumer,
authenticates the same way and goes through exactly one code path —
`GatewayService.handle_request()` — that ever talks to Ollama.

## 3. Who This Is For

| Persona | Need |
|---|---|
| **External web app** (ITF/NAR form extraction UI, HTTP consumer) | A stable `/generate-with-image` contract it doesn't have to change, plus resilience to a single Ollama box going down. Currently missing: it needs to start sending `Authorization: Bearer <api_key>` — see §10a of this revision. |
| **Other external services** (future HTTP consumers) | A single documented endpoint + API key to call any served model, without reimplementing retries/rate limits. |
| **Platform/on-call engineer** | One dashboard to see gateway health, per-backend health, error budgets, and who's consuming what. Alerting when a backend is down or a tenant is near its budget. |
| **Team/tenant lead** | Answer "what did my team use this month" without asking platform to grep logs. |

## 4. Functional Requirements

### 4.1 Unified request API
- `POST /v1/chat/completions` — provider-agnostic chat/vision request
  (messages + optional image parts), OpenAI-shaped enough that a future
  non-Ollama provider is a drop-in.
- `POST /v1/generate-with-image` — preserves the existing multipart
  contract (`image` + `prompt` form fields) so current callers don't need to
  change their request shape on day one. Internally it's a thin adapter onto
  the same core path as `/v1/chat/completions`, returning the canonical
  `ChatCompletionResponse` shape.
- `POST /generate-with-image` (no `/v1` prefix) — the pre-gateway legacy
  path, kept **only** because the external web app is already hardcoded to
  it (ARCHITECTURE.md §9). Same multipart contract, same core path, but
  returns the pre-gateway response shape (`{response, model, timestamp,
  metrics}`) instead of `ChatCompletionResponse`, so that app's existing
  parsing code doesn't break. New consumers should use `/v1/*`, not this.
- All three paths require a bearer API key, are rate-limited,
  budget-checked, and routed through the same failover logic — no
  exceptions for the legacy path.

### 4.2 AuthN
- Bearer API key in `Authorization` header, resolved to a `Tenant`.
- Keys are stored hashed (SHA-256), never logged or returned after creation.
- v1: tenants/keys are seeded from a config file/DB migration, not a
  self-serve API (see §9 non-goals — an admin CRUD API is future work).

### 4.3 Rate limiting
- Token bucket per tenant, backed by Redis, atomic (Lua script — no
  read-then-write race across gateway replicas).
- Two configurable numbers per tenant: sustained rate (`requests_per_minute`)
  and burst capacity.
- On exceed: `429` with `Retry-After` header. No unbounded queuing.
- If Redis is unreachable: **fail closed for rate limiting, fail open for
  serving** — see §10(a) for the reasoning; this is a deliberate, documented
  tradeoff, not an oversight.

### 4.4 Budgets
- Per-tenant budget policy: max tokens and/or max requests over a period
  (`daily` or `monthly`), with `on_exceed: block | warn`.
- Enforcement is a **reserve → reconcile** pattern, not exact pre-checking:
  token count isn't known until Ollama's `done` chunk arrives, so the
  gateway reserves an upper bound (`num_predict` cap) against the budget
  before dispatch and reconciles down to the actual `eval_count` after. This
  means budgets can be overshot by at most one in-flight request's worst
  case — documented, not silently wrong (see §10a).
- Usage is queryable per tenant (`GET /v1/usage`) and exported to Prometheus
  for dashboards/alerts at e.g. 80% of budget.

### 4.5 Provider failover
- Each model maps to an **ordered list of Ollama backends**. In this
  deployment that's 3 real hosts, and — this is a real, deliberate
  constraint, not a simplification for the docs — they run **different
  models**: primary `qwen3.5:9b`, secondary 1 `qwen3.6:35b`, secondary 2
  `qwen3.8:27b-q4_K_M`, on three different machines. Failover is "stay
  answering, on whatever's available," not "identical output from a
  backup." No cloud fallback in v1 (see §10c — scoped down from the
  original "any provider" idea).
- Background health checks per backend; a circuit breaker opens after N
  consecutive failures and half-opens after a cooldown before rejoining
  rotation.
- On a request failure (timeout, connection error, 5xx), the gateway retries
  the *next* backend in the list, up to a configurable max, before returning
  an error to the caller. Retries are **not** transparent when the failure
  happened after tokens were already streamed back — see §10b (no
  double-billing / no silent duplicate generation).
- Health checks must confirm the model actually responds to a cheap
  generation, not just that `/api/tags` is reachable — a backend that's up
  but has evicted the model from VRAM must not be reported healthy (§10a).

### 4.6 Observability
- Prometheus metrics: request count/latency histogram by tenant/model/
  backend/status, rate-limit rejections, budget rejections, failover count,
  per-backend health gauge, token throughput.
- Grafana dashboard(s) provisioned by default: gateway overview (traffic,
  errors, latency), backend health, per-tenant usage.
- OpenTelemetry tracing scoped to the request lifecycle: one span for the
  inbound request, one child span per provider attempt (including retries) —
  not instrumenting every internal function (see §10c, this was cut back
  from a heavier design).
- Structured JSON logs (not the current emoji-decorated text logs) so they're
  greppable/parseable in aggregate.

### 4.7 Usage accounting
- Every completed or failed request produces a durable `UsageRecord`
  (tenant, api key, model, backend, status, prompt/completion tokens,
  latency, failover count, timestamp), written asynchronously so it's never
  on the hot path.

## 5. Non-Functional Requirements

- **Gateway overhead**: p50 added latency (auth + rate limit + budget check,
  excluding the actual model call) should stay under ~15ms.
- **Availability**: the gateway itself must not become a new single point of
  failure worse than "one Ollama box down" — a single backend outage must
  degrade to slower/queued, not a full outage, as long as ≥1 backend per
  model is healthy.
- **Security**: API keys hashed at rest; no prompt/response bodies in logs
  by default (opt-in trace logging only, same as today's `TRACE_DIR`
  behavior which already writes raw extraction output to disk).
- **Backwards compatibility**: existing `/generate-with-image` callers (the
  ITF/NAR pipeline, any existing internal scripts) keep working without
  request-shape changes.
- **Extensibility**: adding a second provider *type* (not just another
  Ollama host) should mean writing one adapter class, not touching routing,
  budget, or rate-limit code.

## 6. Out of Scope (v1)

- Self-serve tenant/API-key management UI or CRUD API (seed via config for
  now).
- Non-Ollama providers (cloud fallback) — interface is pluggable, no second
  adapter is shipped (per design decision, §10c).
- Semantic response caching.
- Streaming responses to the *caller* (the gateway still streams internally
  from Ollama, but v1's public contract returns a complete response — see
  ARCHITECTURE.md §7 for why).
- Per-model cost tracking in USD (meaningless for local Ollama; the field
  exists in the budget model for when a cloud provider is added).
- Multi-region / Kubernetes deployment (single Docker Compose stack on one
  GPU host, matching the actual current deployment target).
- Fine-grained RBAC (tenant-level budgets/limits only, no per-user-within-
  tenant scoping).

## 7. Success Metrics

- 100% of LLM calls (internal + external) flow through the gateway — zero
  direct-to-Ollama `aiohttp` calls left anywhere in the codebase.
- A single Ollama backend can be stopped without any caller-visible error
  rate increase (verified by a chaos test in CI or manually).
- Rate-limit and budget rejections are visible in Grafana within one scrape
  interval (15s) of occurring.
- Time to answer "what did tenant X consume last month" drops from "grep
  logs" to one dashboard query.

## 8. Risks / Open Questions

- **VRAM contention on failover**: failing over to a cold backend (model not
  in VRAM) trades an outage for a large latency spike. Mitigated by keeping
  `keep_alive` high on all backends and documenting this as expected
  behavior, not a bug — see ARCHITECTURE.md §6.3.
- **Output quality/latency varies across the failover chain by design**:
  the two secondaries run a 35B and a quantized 27B model, not copies of
  the 9B primary. For the ITF/NAR pipeline specifically, extraction
  accuracy on a failover response has not been validated against the
  35B/27B models — if a secondary serves enough traffic to matter, that
  needs a real accuracy check, not an assumption it's "close enough." The
  gateway reports which model actually answered (`ChatCompletionResponse.
  model`, `UsageRecord.served_model` — ARCHITECTURE.md §6.5) specifically
  so this is measurable instead of invisible.
- **Redis as a hard dependency**: rate limiting and budget enforcement both
  need it. See §10a for the fail-open/fail-closed decision and its
  consequences.
- **Multi-page/multi-image requests**: today's pipeline sends one image per
  request; the gateway's request schema supports multiple image parts per
  message for future use, but only single-image requests are tested in v1.

## 9. Personas Explicitly Not Served (v1)

- External/public API consumers outside this deployment (no public-internet
  exposure, no billing integration).
- Anonymous/keyless access — every request must resolve to a tenant.

## 10. Design Review — Hard Questions

This section records the review pass required before scaffolding, and what
changed in this doc as a result. See ARCHITECTURE.md §12 for the technical
detail behind each point.

### (a) What will break

- **The external web app doesn't send an `Authorization` header** (observed
  directly in its current `/generate-with-image` call — see
  ARCHITECTURE.md §9.1). Now that `/generate-with-image` requires a real
  tenant's API key (there's no more internal tenant to fall back to since
  the pipeline moved out of this repo — §2), every call from that app will
  get `401` until it's updated to send one. This is a real, currently-true
  integration gap, not a hypothetical — flagged here so it isn't lost as a
  "someone else's problem" footnote. **Not fixed by this repo**: the web
  app repo needs the change; this repo can only provision the key
  (`python -m gateway.admin.seed`, README "Provisioning a tenant") and
  document the requirement.
- **Streaming vs. exact budget enforcement**: Ollama only reports
  `eval_count` in the final chunk. A naive "check budget, deduct exact
  tokens, then call" design is impossible — token count isn't known until
  after generation. **Fixed by**: reserve-then-reconcile (§4.4), documented
  as an approximate bound, not exact.
- **False-healthy backends**: a backend can accept TCP connections and
  answer `/api/tags` while the target model has been evicted from VRAM
  (idle beyond `keep_alive`) or is stuck loading — a health check that only
  pings `/api/tags` will route traffic to a backend that then hangs for the
  full `num_predict` generation before failing. **Fixed by**: health checks
  must include a cheap real generation against the actual model, not just a
  liveness ping (§4.5).
- **Failover cold-start spike**: when the gateway fails over to a backup
  backend, if that backend hasn't served this model recently, the model load
  itself can take tens of seconds — a failover can look like the request got
  *slower*, not more reliable, on the first hit. **Fixed by**: documented as
  expected, `keep_alive` tuned high on all backends in the failover chain,
  and this latency is a named Prometheus metric (`gateway_model_load_seconds`)
  so it's visible instead of mysterious.
- **Silent success-shaped failures**: the current code returns HTTP 200 with
  an error message as the body content on timeout (`app.py:419-425`). Any
  new gateway response contract must make failure structurally
  distinguishable (non-2xx status + typed error body), or every existing
  failure-blind caller carries the bug forward. **Fixed by**: explicit
  `GatewayError` taxonomy (ARCHITECTURE.md §5.6), no 200-with-error-string.
- **Redis single point of failure**: if the token-bucket/budget store is
  down, every gated request needs a policy, not a crash. **Fixed by**: fail
  closed on rate limiting (reject with 429, safer to be briefly unavailable
  than to let an outage become an uncapped free-for-all) but fail open on
  *serving* if Redis is down and a request has no budget policy configured
  at all (avoids the gateway becoming a harder single point of failure than
  the thing it replaced). This tradeoff is explicit, not accidental.

### (b) Edge cases that were missing

- **Image size limit was defined but never enforced.** `config.py` already
  has `MAX_IMAGE_SIZE_MB` and it is never checked in `app.py`. The gateway
  must actually enforce it (413 response) — carried into the functional
  requirements as a bug-fix, not a new feature.
- **Unbounded queuing under load.** The current `asyncio.Semaphore(10)`
  queues excess requests indefinitely instead of rejecting. Folded into
  §4.3 — rate limiting must produce backpressure (429), not just gate
  concurrency.
- **Duplicate generation on failover.** If a backend fails *after* it has
  already produced (and possibly the caller has started receiving) partial
  output, blindly retrying on the next backend duplicates GPU work and can
  double-count usage. v1 constrains retries to failures that occur before
  any output was returned to the caller; a failure mid-stream is surfaced as
  an error, not silently retried. Called out explicitly in §4.5.
- **Budget period boundaries.** A request in flight when a daily/monthly
  budget resets must be reconciled against the period it started in, not
  the one it finishes in — otherwise usage leaks across boundaries or gets
  double-counted. Added to ARCHITECTURE.md §5.4.
- **Model allow-list per tenant was absent.** Nothing stopped any tenant
  from requesting any model. Added: `RateLimitPolicy`/`BudgetPolicy` are
  tenant+model scoped, and a tenant with no policy for a model cannot use
  it (default deny), not default allow.

### (c) What was over-engineered (cut from v1)

- **Cloud-provider failover** was in the original tech-stack brainstorm but
  isn't needed given this deployment is on-prem/GPU-box-only; building a
  generic multi-cloud adapter layer for zero real cloud consumers is
  speculative. **Cut**: the `LLMProvider` interface stays pluggable (one
  ABC, one method set) so a second provider *type* is one class away, but
  only `OllamaProvider` ships in v1.
- **Full OpenTelemetry instrumentation of every internal function** (rate
  limiter, budget tracker, DB writes, etc.) adds overhead and noise for a
  gateway that's one hop deep. **Cut to**: two spans per request (inbound +
  per provider attempt), Prometheus carries the metrics load instead.
- **Kubernetes manifests** were already present in the old README as
  aspirational content with no cluster to run them on. **Cut**: v1 ships
  Docker Compose only, matching the actual single-GPU-box deployment;
  Kubernetes is not re-added until there's a second box that needs one.
- **Self-serve tenant management API/UI.** Building CRUD + auth + audit for
  an admin UI before there's more than a handful of internal tenants is
  premature. **Cut to**: tenants/keys seeded via a config file read at
  startup/migration; revisit if the tenant count outgrows that.
- **Semantic caching, PII redaction, per-request cost billing in USD** —
  none apply to a single local model with no paying customers yet. Left out
  entirely rather than built-and-unused; the budget model keeps a
  nullable `max_cost_usd` field so adding a paid provider later doesn't
  require a schema migration, but no cost logic is implemented in v1.
