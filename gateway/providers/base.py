"""LLMProvider / EmbeddingProvider interfaces.

See ARCHITECTURE.md §1 and PRD.md §10c: these ABCs exist specifically so
that adding a second provider TYPE later (not just another Ollama host) is
one new class, without touching routing, budget, or rate-limit code. Only
OllamaProvider ships in v1, implementing both — do not add a second
implementation speculatively (CLAUDE.md "explicitly out of scope").

EmbeddingProvider is a separate ABC from LLMProvider, not an extra method
bolted onto it: not every provider that can generate chat completions can
also embed (and vice versa — see the reranker note in
gateway/providers/ollama.py), so a provider type declares each capability
it actually implements rather than inheriting a method it can't fulfill.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from gateway.models.chat import ChatCompletionRequest
from gateway.models.embedding import EmbeddingRequest
from gateway.models.provider import ProviderBackend


@dataclass
class ProviderResult:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    served_model: str
    """The actual model name that generated `content` — may differ from the
    tenant's requested model when this result came from a failover backend
    running a different model (gateway/models/provider.py's target_model).
    GatewayService reports this back to the caller as the response's
    `model` field, matching the convention most chat-completion APIs use
    (the field means "what generated this," not "what was requested")."""


@dataclass
class EmbeddingProviderResult:
    embeddings: list[list[float]]
    prompt_tokens: int
    latency_ms: int
    served_model: str
    """Same convention as ProviderResult.served_model — see above."""


class LLMProvider(ABC):
    """One adapter per provider type. A provider serves N backends (e.g. N
    Ollama hosts); `backend` identifies which one a given call targets."""

    @abstractmethod
    async def generate(
        self, *, backend: ProviderBackend, request: ChatCompletionRequest
    ) -> ProviderResult:
        """Run the request against `backend`.

        Must raise ProviderTimeout / a GatewayError subclass on failure —
        never return a "successful" result carrying an error message as
        content (CLAUDE.md hard rule, PRD.md §10a).

        Streaming to the CALLER is not supported in v1 (ARCHITECTURE.md
        §7); this method buffers and returns the complete result even if it
        streams from the backend internally.
        """

    @abstractmethod
    async def health_check(self, *, backend: ProviderBackend, model: str) -> None:
        """Raise a GatewayError if `backend` cannot currently serve `model`.

        Must exercise a real (if minimal) generation, not just a liveness
        ping — see gateway/routing/health.py module docstring.
        """


class EmbeddingProvider(ABC):
    """One adapter per provider type that can serve embedding models. See
    LLMProvider above for the same "one adapter per type" rationale."""

    @abstractmethod
    async def embed(
        self, *, backend: ProviderBackend, request: EmbeddingRequest
    ) -> EmbeddingProviderResult:
        """Run the embedding request against `backend`.

        Must raise ProviderTimeout / a GatewayError subclass on failure —
        same no-200-with-error-body rule as LLMProvider.generate.
        """

    @abstractmethod
    async def embedding_health_check(self, *, backend: ProviderBackend, model: str) -> None:
        """Raise a GatewayError if `backend` cannot currently embed with
        `model`. Must exercise a real (if minimal) embed call, not just a
        liveness ping — same rationale as LLMProvider.health_check.

        Named distinctly from LLMProvider.health_check (rather than just
        overloading that name) because OllamaProvider implements both ABCs
        on one class — a single provider instance can serve chat models on
        some backends and embedding models on others, and the health
        poller needs to call the right one per model."""
