# AGENTS.md

This file follows the [agents.md](https://agents.md) convention: instructions
for any coding agent (not just Claude) working in this repository.

**This repo's canonical agent instructions live in [CLAUDE.md](./CLAUDE.md).**
Everything in that file applies regardless of which agent is reading it —
the hard rules (single path to Ollama, no internal/implicit tenant, no
200-on-error, default-deny, domain-agnostic gateway code, health checks
that exercise the model), working conventions, and escalation guidance are
not Claude-specific.

Read, in order:

1. `ARCHITECTURE-ESSENTIALS.md` — condensed decisions, read before writing
   gateway code.
2. `CLAUDE.md` — full operating rules for this repo.
3. `PRD.md` / `ARCHITECTURE.md` — full product/technical detail, as needed.

## Quick orientation

- This is an LLM gateway (FastAPI, Redis, Postgres/SQLite, Prometheus,
  Grafana, OpenTelemetry) in front of Ollama. There is no in-process
  consumer — a separate external web app (an ITF/NAR medical-form
  extraction tool) calls it over HTTP with a real API key, same as any
  other tenant. See ARCHITECTURE-ESSENTIALS.md "What this repo is" for the
  one-paragraph version.
- `gateway/core/service.py :: GatewayService` is the only thing allowed to
  reach a provider — `handle_request()` for chat/vision, or
  `handle_embedding_request()` for embeddings, both built on a shared
  `_dispatch()` pipeline (ARCHITECTURE.md §13.5). Every HTTP route —
  including `app.py`'s legacy `/generate-with-image` — calls through one of
  these, and every caller authenticates with a real, seeded tenant's API
  key. There is no bypass tenant.
- Two model capabilities ship today: chat/vision (`POST
  /v1/chat/completions`) and embeddings (`POST /v1/embeddings`, currently
  `qllama/bge-large-en-v1.5:latest`). Reranking (`BAAI/bge-reranker-v2-m3`)
  was evaluated and deliberately not built — ARCHITECTURE.md §14 has the
  full analysis; don't re-derive it from scratch or build it without
  checking that section first.
- Local dev: `pip install -r requirements.txt`, `docker compose up redis
  postgres prometheus grafana` for infra, then run the app per
  `README.md`. Full stack: `docker compose up`. Before anything can
  authenticate, provision a tenant: `python -m gateway.admin.seed`.
- Tests: `pytest tests/ -v` (includes `tests/gateway/`).

## Scope discipline

Before adding a dependency, a new provider type, an admin UI, streaming to
callers, or a Kubernetes manifest, check the "explicitly out of scope for
v1" list in ARCHITECTURE-ESSENTIALS.md. These are recorded cuts from an
explicit design review (PRD.md §10, ARCHITECTURE.md §12), not gaps —
building them without checking in first is very likely to be un-done.
