"""Backend registry: model -> ordered list of healthy ProviderBackends.

See ARCHITECTURE.md §6.1. Backend/model config is static YAML
(gateway/admin/routing.yaml), loaded once at startup — no hot-reload in v1.
Health/circuit-breaker state is looked up per call so routing reflects the
latest known state without needing to reload config.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.core.exceptions import AllProvidersUnavailable
from gateway.models.provider import ProviderBackend
from gateway.routing.circuit_breaker import CircuitBreaker


class BackendRegistry:
    def __init__(self, backends_by_model: dict[str, list[ProviderBackend]], circuit_breaker: CircuitBreaker):
        self._backends_by_model = backends_by_model
        self._circuit_breaker = circuit_breaker

    @classmethod
    def from_yaml(cls, path: str | Path, circuit_breaker: CircuitBreaker) -> "BackendRegistry":
        raw = yaml.safe_load(Path(path).read_text())

        models_by_backend: dict[str, list[str]] = {}
        for model, entries in raw.get("models", {}).items():
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

        backends_by_model: dict[str, list[ProviderBackend]] = {}
        for model, entries in raw.get("models", {}).items():
            ordered = sorted(entries, key=lambda e: e.get("priority", 0))
            backends_by_model[model] = [
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

        return cls(backends_by_model, circuit_breaker)

    @property
    def backends_by_model(self) -> dict[str, list[ProviderBackend]]:
        """Raw model -> configured-backends map (unfiltered by circuit
        breaker state). Used by gateway/routing/health.py to know what to
        poll — get_backends() below is the routing-time, breaker-filtered
        view used by GatewayService."""
        return self._backends_by_model

    async def get_backends(self, model: str) -> list[ProviderBackend]:
        """Ordered, circuit-breaker-filtered list of backends for `model`.

        Raises AllProvidersUnavailable if the model is unconfigured or every
        configured backend is currently circuit-open.
        """
        candidates = [b for b in self._backends_by_model.get(model, []) if b.enabled]
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
