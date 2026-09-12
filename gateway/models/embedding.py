"""Embedding request/response models.

See ARCHITECTURE.md §4.5 for the chat schema this mirrors. Embeddings are a
deliberately separate schema, not a variant of ChatCompletionRequest —
there's no meaningful "messages"/"temperature"/"num_predict" for an
embedding call, and the response carries vectors instead of generated text.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _reject_empty(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and not value:
            raise ValueError("input must not be an empty list")
        return value

    @property
    def inputs(self) -> list[str]:
        """Always-a-list view of `input`, since Ollama's /api/embed and the
        rest of the pipeline only care about "one or more strings"."""
        return [self.input] if isinstance(self.input, str) else self.input


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        # No completion tokens for an embedding call — kept as a named
        # property (rather than reusing ChatCompletionUsage) so this stays
        # correct even if that schema grows completion-only fields later.
        return self.prompt_tokens


class EmbeddingResponse(BaseModel):
    id: UUID
    model: str
    """The model that actually produced `embeddings` — see
    ChatCompletionResponse.model docstring for why this may differ from the
    requested model on a failover chain (ARCHITECTURE.md §6.5)."""
    backend_used: str
    embeddings: list[list[float]]
    usage: EmbeddingUsage
    latency_ms: int
