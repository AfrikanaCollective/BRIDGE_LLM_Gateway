"""Provider-agnostic chat/vision request and response models.

See ARCHITECTURE.md §4.5. Shaped close enough to the OpenAI chat schema
that a future non-Ollama provider adapter doesn't need a new request
contract. ``/v1/generate-with-image`` (legacy multipart contract) is
translated into a ChatCompletionRequest at the API boundary, not a
separate schema.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ContentPartType = Literal["text", "image_base64"]
Role = Literal["system", "user", "assistant"]


class ContentPart(BaseModel):
    type: ContentPartType
    text: str | None = None
    image_base64: str | None = None
    media_type: str | None = Field(default=None, description='e.g. "image/png"')

    @model_validator(mode="after")
    def _check_fields_for_type(self) -> "ContentPart":
        if self.type == "text" and self.text is None:
            raise ValueError('content part of type "text" requires "text"')
        if self.type == "image_base64" and self.image_base64 is None:
            raise ValueError('content part of type "image_base64" requires "image_base64"')
        return self


class Message(BaseModel):
    role: Role
    content: str | list[ContentPart]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 1
    seed: int | None = 42
    num_predict: int = Field(
        default=4096,
        gt=0,
        description="Hard cap on generated tokens; also the basis for the budget reservation.",
    )
    repeat_penalty: float = Field(
        default=1.0, description="1.0 = no repeat penalty; matches the tuned deterministic defaults."
    )
    repeat_last_n: int = 256
    num_ctx: int = 8192
    stream: bool = Field(
        default=False,
        description="Caller-facing streaming is not supported in v1 — see ARCHITECTURE.md §7.",
    )
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_streaming(self) -> "ChatCompletionRequest":
        if self.stream:
            raise ValueError("stream=True is not supported in v1 (ARCHITECTURE.md §7)")
        return self


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatCompletionResponse(BaseModel):
    id: UUID
    model: str
    """The model that actually generated `content` — NOT necessarily the
    model the tenant requested. A request for "qwen3.5:9b" that fails over
    to a secondary backend running "qwen3.6:35b" gets `model="qwen3.6:35b"`
    back, so the caller knows what really answered (this repo's failover
    chain deliberately mixes model sizes across backends — see
    ARCHITECTURE.md §6.5). Rate limiting and budgeting stay keyed on the
    *requested* model, tracked separately — see UsageRecord.model vs.
    UsageRecord.served_model."""
    backend_used: str
    content: str
    finish_reason: str
    usage: ChatCompletionUsage
    latency_ms: int
