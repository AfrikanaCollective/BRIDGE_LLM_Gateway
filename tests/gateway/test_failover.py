"""Circuit breaker / backend registry tests.

See ARCHITECTURE.md §6.3: closed -> open after N consecutive failures ->
(cooldown expiry) -> closed again. And §6.1: a model with all backends
circuit-open must raise AllProvidersUnavailable, not silently return an
empty list for routing to mishandle.
"""

import uuid

import pytest
from fakeredis.aioredis import FakeRedis

from gateway.config import settings
from gateway.core.exceptions import AllProvidersUnavailable
from gateway.models.provider import ProviderBackend
from gateway.routing.circuit_breaker import CircuitBreaker
from gateway.routing.registry import BackendRegistry


@pytest.fixture
async def redis():
    r = FakeRedis()
    yield r
    await r.aclose()


@pytest.fixture
def backends():
    return [
        ProviderBackend(id="primary", base_url="http://a", models=["m"], priority=0),
        ProviderBackend(id="secondary", base_url="http://b", models=["m"], priority=1),
    ]


@pytest.mark.asyncio
async def test_circuit_opens_after_failure_threshold(redis):
    breaker = CircuitBreaker(redis, failure_threshold=3, cooldown_seconds=30)
    assert await breaker.is_available("primary", "m") is True

    for _ in range(3):
        await breaker.record_failure("primary", "m")

    assert await breaker.is_available("primary", "m") is False


@pytest.mark.asyncio
async def test_circuit_stays_closed_below_threshold(redis):
    breaker = CircuitBreaker(redis, failure_threshold=3, cooldown_seconds=30)
    await breaker.record_failure("primary", "m")
    await breaker.record_failure("primary", "m")
    assert await breaker.is_available("primary", "m") is True


@pytest.mark.asyncio
async def test_success_resets_failure_count(redis):
    breaker = CircuitBreaker(redis, failure_threshold=3, cooldown_seconds=30)
    await breaker.record_failure("primary", "m")
    await breaker.record_failure("primary", "m")
    await breaker.record_success("primary", "m")
    await breaker.record_failure("primary", "m")
    # Only 1 failure since the reset — must still be well under threshold.
    assert await breaker.is_available("primary", "m") is True


@pytest.mark.asyncio
async def test_registry_filters_open_circuits(redis, backends):
    breaker = CircuitBreaker(redis, failure_threshold=1, cooldown_seconds=30)
    registry = BackendRegistry({"m": backends}, breaker)

    available = await registry.get_backends("m")
    assert [b.id for b in available] == ["primary", "secondary"]

    await breaker.record_failure("primary", "m")
    available = await registry.get_backends("m")
    assert [b.id for b in available] == ["secondary"]


@pytest.mark.asyncio
async def test_registry_raises_when_all_backends_down(redis, backends):
    breaker = CircuitBreaker(redis, failure_threshold=1, cooldown_seconds=30)
    registry = BackendRegistry({"m": backends}, breaker)

    for backend in backends:
        await breaker.record_failure(backend.id, "m")

    with pytest.raises(AllProvidersUnavailable):
        await registry.get_backends("m")


@pytest.mark.asyncio
async def test_registry_raises_for_unconfigured_model(redis, backends):
    breaker = CircuitBreaker(redis, failure_threshold=1, cooldown_seconds=30)
    registry = BackendRegistry({"m": backends}, breaker)

    with pytest.raises(AllProvidersUnavailable):
        await registry.get_backends("no-such-model")


@pytest.mark.asyncio
async def test_real_routing_yaml_resolves_heterogeneous_model_chain(redis):
    """gateway/admin/routing.yaml's actual chain fails over qwen3.5:9b to
    TWO DIFFERENT models (qwen3.6:35b, qwen3.8:27b-q4_K_M) on different
    hosts — not just the same model mirrored on a backup box. Confirms
    BackendRegistry.from_yaml resolves each entry's target_model correctly
    and defaults it to the logical model name when no override is given
    (see gateway/models/provider.py's target_model, ARCHITECTURE.md §6.5).
    """
    breaker = CircuitBreaker(redis, failure_threshold=3, cooldown_seconds=30)
    registry = BackendRegistry.from_yaml(settings.routing_config_path, breaker)

    backends = await registry.get_backends("qwen3.5:9b")
    assert [b.id for b in backends] == ["ollama-primary", "ollama-secondary-1", "ollama-secondary-2"]

    by_id = {b.id: b for b in backends}
    # Primary mirrors the requested model (no override in the YAML).
    assert by_id["ollama-primary"].target_model == "qwen3.5:9b"
    assert by_id["ollama-primary"].base_url == "http://172.17.0.1:11434"
    # Secondaries run different models entirely.
    assert by_id["ollama-secondary-1"].target_model == "qwen3.6:35b"
    assert by_id["ollama-secondary-1"].base_url == "http://172.16.13.67:11434"
    assert by_id["ollama-secondary-2"].target_model == "qwen3.8:27b-q4_K_M"
    assert by_id["ollama-secondary-2"].base_url == "http://172.18.0.1:11434"


@pytest.mark.asyncio
async def test_target_model_defaults_to_logical_model_when_unset():
    """A ProviderBackend with no explicit target_model (constructed
    directly, not via the YAML loader) means "same as requested" —
    gateway/providers/ollama.py falls back to request.model in that case."""
    backend = ProviderBackend(id="b", base_url="http://x", models=["m"])
    assert backend.target_model is None
