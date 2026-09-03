# CLAUDE.md

Instructions for Claude (or any coding agent) working in this repository.

## Read this first

1. **ARCHITECTURE-ESSENTIALS.md** — the condensed set of decisions that
   constrain implementation. Read it before writing gateway code.
2. **PRD.md** and **ARCHITECTURE.md** — full detail, read the relevant
   section when ARCHITECTURE-ESSENTIALS.md isn't enough (e.g. §12/§10
   "Design Review" sections explain *why* a constraint exists, which
   matters if you're tempted to work around it).

Don't re-derive the architecture from scratch by reading source files first.
The docs above are the source of truth for intent; the code is the source
of truth for current state. If they disagree, say so — don't silently pick
one.

## What this repo is

An LLM gateway (FastAPI + Redis + Postgres/SQLite + Prometheus/Grafana +
OpenTelemetry) that fronts one or more Ollama backends with per-tenant auth,
rate limiting, budgets, and failover. It has no in-process consumers —
the ITF/NAR medical-form extraction pipeline that used to live here
(`agents/`, `clients/`, `prompts/`) was removed and now runs as a separate
external web app, calling this gateway over HTTP with a real API key like
any other tenant (`POST /generate-with-image` — a legacy-shaped
compatibility endpoint kept for that app specifically; see
ARCHITECTURE.md §9). Don't recreate `agents/`/`clients/`/`prompts/` here —
that pipeline's home is elsewhere now.

## Hard rules (from the design review — see PRD.md §10 / ARCHITECTURE.md §12)

- **Every path to Ollama goes through `GatewayService.handle_request()`**
  (`gateway/core/service.py`). If you're adding code that opens a new
  `aiohttp` session to a model backend, you're doing it wrong — route
  through the gateway service instead.
- **No internal/implicit tenant.** There used to be one (for the in-process
  ITF/NAR pipeline); it's gone along with that pipeline. Every caller —
  including `app.py`'s own legacy `/generate-with-image` handler —
  authenticates with a real `Authorization: Bearer <api_key>` header via
  `gateway.api.deps.authenticated_tenant`. Don't add a code-provisioned
  "trusted" tenant as a shortcut for a new caller; provision a real one
  with `python -m gateway.admin.seed` (`gateway/admin/tenants.yaml`).
- **No endpoint returns HTTP 200 with an error in the body.** Use the typed
  `GatewayError` subclasses in `gateway/core/exceptions.py`. This was a real
  bug in the pre-gateway code (`app.py` timeout handling) — don't
  reintroduce it.
- **No unbounded queuing.** If a limit is hit (rate limit, concurrency,
  budget), reject with the right status code and `Retry-After` where
  applicable. Don't add another bare `asyncio.Semaphore`-style silent queue.
- **Default deny.** A tenant with no `RateLimitPolicy`/`BudgetPolicy` for a
  model cannot use it. Don't make "no policy" mean "no limit."
- **Gateway code stays domain-agnostic.** `gateway/` must never parse or
  assume anything about a caller's response-shape expectations or apply
  JSON-repair logic. That belongs in `utils/clean_gen_response_from_image.py`
  (used by `app.py`'s legacy handler), applied to the raw string the
  gateway returns — not inside `gateway/`.
- **Health checks must exercise the model**, not just check that a host is
  reachable. See `gateway/routing/health.py`.
- **Never key rate limiting or budgets on the served model.** The failover
  chain is heterogeneous — a request for `qwen3.5:9b` may be answered by
  `qwen3.6:35b` or `qwen3.8:27b-q4_K_M` (see `gateway/admin/routing.yaml`,
  ARCHITECTURE.md §6.5). Policies stay keyed on `request.model` (what the
  tenant asked for); `served_model` (`ProviderResult`, `UsageRecord`,
  `ChatCompletionResponse.model`) is for reporting/audit only.
- Before adding a new provider type, dependency, or infra service (a
  message queue, a second database, a caching layer), check
  ARCHITECTURE-ESSENTIALS.md's "explicitly out of scope for v1" list. If
  what you're about to build is on it, stop and ask rather than building it.

## Working conventions

- **Python**: 3.11, type hints required on new code, Pydantic v2 for
  schemas. `gateway/` uses structured JSON logging
  (`gateway/observability/logging.py`), not the emoji-decorated text logs
  in `app.py`'s FastAPI-app-shell code (that style is fine to keep
  matching there — it's pre-existing, not gateway code).
- **Config**: gateway settings live in `gateway/config.py`
  (`pydantic-settings`, env-driven). Don't add gateway config to the
  legacy `config.py` `Config` class — that stays scoped to `app.py`'s app
  shell (SSL, host/port, the legacy handler's default model).
- **Tests**: `tests/gateway/` for gateway unit/integration tests (token
  bucket math, budget reserve/reconcile, failover/circuit-breaker
  transitions, DB-backed auth against unknown/revoked/suspended-tenant
  keys — this path is now the *only* gate on every caller, so bugs here
  are auth bypasses, treat test coverage here as load-bearing). Run with
  `pytest tests/ -v`. New gateway logic needs tests before it's considered
  done — especially anything touching Redis atomicity or budget
  accounting, since those are exactly where the design review (PRD.md
  §10) found the sharpest edge cases.
- **Migrations**: schema changes to the durable store (`gateway/db/`) go
  through Alembic migrations in `gateway/db/migrations/`, not manual SQL or
  `create_all()` in production paths.
- **Secrets**: API keys are stored as SHA-256 hashes (`ApiKey.key_hash`).
  Never log a plaintext key, and never add a code path that returns one
  after creation-time.
- **Docker**: `docker-compose.yml` is the deployment target (gateway,
  redis, postgres, prometheus, grafana). Verify changes work under
  `docker compose up` before considering infra changes done — don't assume
  a change to a Dockerfile/compose file is correct without running it.

## When you're not sure

- If a request seems to call for a second LLM provider, streaming to
  callers, an admin UI, Kubernetes manifests, or anything else listed as
  "out of scope for v1," treat that as a signal to ask before building —
  those were explicit cuts from a design review, not gaps nobody noticed.
- If PRD.md/ARCHITECTURE.md and the actual code disagree, flag the
  discrepancy to the user rather than quietly reconciling it one way.

## See also

- **AGENTS.md** — same operating rules, framework-agnostic entry point for
  non-Claude agents; defers to this file for anything gateway-specific.
