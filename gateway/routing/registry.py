"""Backend registry: model -> ordered list of healthy ProviderBackends.

See ARCHITECTURE.md §6.1. Backend/model config is static YAML
(gateway/admin/routing.yaml), loaded once at startup — no hot-reload in v1.
Health/circuit-breaker state is looked up per call so routing reflects the
latest known state without needing to reload config.

Chat and embedding models are kept in separate top-level YAML sections
(`models:` / `embedding_models:`) rather than one list with a per-entry
"kind" flag — a logical model is either a chat model or an embedding
model, never both, so two parallel sections are simpler than a
discriminated union and keep the existing `models:` schema (and its
tests) untouched.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.core.exceptions import AllProvidersUnavailable
from gateway.models.provider import ProviderBackend
from gateway.routing.circuit_breaker import CircuitBreaker


class BackendRegistry:
    def __init__(
        self,
        backends_by_model: dict[str, list[ProviderBackend]],
        circuit_breaker: CircuitBreaker,
        embedding_backends_by_model: dict[str, list[ProviderBackend]] | None = None,
    ):
        self._backends_by_model = backends_by_model
        self._circuit_breaker = circuit_breaker
        self._embedding_backends_by_model = embedding_backends_by_model or {}

    @classmethod
    def from_yaml(cls, path: str | Path, circuit_breaker: CircuitBreaker) -> "BackendRegistry":
        raw = yaml.safe_load(Path(path).read_text())

        models_section = raw.get("models", {})
        embedding_models_section = raw.get("embedding_models", {})

        models_by_backend: dict[str, list[str]] = {}
        for section in (models_section, embedding_models_section):
            for model, entries in section.items():
                for entry in entries:
                    models_by_backend.setdefault(entry["backend"], []).append(model)

        backends_by_id = {
            backend_id: ProviderBackend(
                id=backend_id,
                base_url=cfg["base_url"],
                models=models_by_backend.get(backend_id, []),
                enabled=cfg.get("enabled", True),
                keep_alive=cfg.get("keep_alive", "5m"),
            )
            for backend_id, cfg in raw.get("backends", {}).items()
        }

        def _resolve(section: dict) -> dict[str, list[ProviderBackend]]:
            resolved: dict[str, list[ProviderBackend]] = {}
            for model, entries in section.items():
                ordered = sorted(entries, key=lambda e: e.get("priority", 0))
                resolved[model] = [
                    backends_by_id[entry["backend"]].model_copy(
                        update={
                            "priority": entry.get("priority", 0),
                            # "model_name" lets a failover entry run a DIFFERENT
                            # actual model than what the tenant requested (e.g.
                            # qwen3.5:9b's chain falls over to qwen3.6:35b on a
                            # different host) — omit it when the backend mirrors
                            # the same model as the logical key.
                            "target_model": entry.get("model_name", model),
                        }
                    )
                    for entry in ordered
                    if entry["backend"] in backends_by_id
                ]
            return resolved

        return cls(_resolve(models_section), circuit_breaker, _resolve(embedding_models_section))

    @property
    def backends_by_model(self) -> dict[str, list[ProviderBackend]]:
        """Raw model -> configured-backends map (unfiltered by circuit
        breaker state). Used by gateway/routing/health.py to know what to
        poll — get_backends() below is the routing-time, breaker-filtered
        view used by GatewayService."""
        return self._backends_by_model

    @property
    def embedding_backends_by_model(self) -> dict[str, list[ProviderBackend]]:
        """Same as backends_by_model, for the `embedding_models:` section."""
        return self._embedding_backends_by_model

    async def get_backends(self, model: str) -> list[ProviderBackend]:
        """Ordered, circuit-breaker-filtered list of backends for `model`.

        Raises AllProvidersUnavailable if the model is unconfigured or every
        configured backend is currently circuit-open.
        """
        return await self._filtered(self._backends_by_model, model)

    async def get_embedding_backends(self, model: str) -> list[ProviderBackend]:
        """Same as get_backends(), against the `embedding_models:` section."""
        return await self._filtered(self._embedding_backends_by_model, model)

    async def _filtered(
        self, backends_by_model: dict[str, list[ProviderBackend]], model: str
    ) -> list[ProviderBackend]:
        candidates = [b for b in backends_by_model.get(model, []) if b.enabled]
        if not candidates:
            raise AllProvidersUnavailable(f"No backends configured for model={model!r}")

        available = [
            b for b in candidates if await self._circuit_breaker.is_available(b.id, model)
        ]
        if not available:
            raise AllProvidersUnavailable(
                f"All backends for model={model!r} are circuit-open"
            )
        return available
