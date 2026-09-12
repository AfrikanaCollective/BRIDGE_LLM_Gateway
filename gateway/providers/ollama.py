"""Ollama provider adapter.

Talks to a single Ollama backend's /api/generate (chat) and /api/embed
(embedding) endpoints. Request/response handling here mirrors the
streaming-NDJSON consumption that used to live directly in app.py, but:
(1) errors raise typed GatewayErrors instead of returning
200-with-error-body, and (2) this only returns raw model output — it does
not know about any caller's expected JSON shape (CLAUDE.md: gateway code
stays domain-agnostic; response repair for the legacy endpoint lives in
utils/clean_gen_response_from_image.py, applied to `ProviderResult.content`
after the gateway returns it).

OllamaProvider implements both LLMProvider and EmbeddingProvider because
Ollama itself natively serves both request shapes. It does NOT implement a
reranking capability — Ollama has no cross-encoder/rerank endpoint, so
BAAI/bge-reranker-v2-m3 (or any reranker) needs a different provider type
serving a different backend; that's a separate, not-yet-built piece (see
ARCHITECTURE-ESSENTIALS.md "explicitly out of scope for v1").
"""

from __future__ import annotations

import json
import time

import aiohttp

from gateway.core.exceptions import ProviderTimeout, AllProvidersUnavailable
from gateway.models.chat import ChatCompletionRequest, ContentPart
from gateway.models.embedding import EmbeddingRequest
from gateway.models.provider import ProviderBackend
from gateway.providers.base import EmbeddingProvider, EmbeddingProviderResult, LLMProvider, ProviderResult


def _flatten_prompt_and_images(request: ChatCompletionRequest) -> tuple[str, list[str]]:
    """Ollama's /api/generate is a flat prompt + images list, not a chat
    array. Concatenate message text in order; collect any image parts."""
    text_parts: list[str] = []
    images: list[str] = []

    for message in request.messages:
        if isinstance(message.content, str):
            text_parts.append(message.content)
            continue
        for part in message.content:
            part: ContentPart
            if part.type == "text" and part.text:
                text_parts.append(part.text)
            elif part.type == "image_base64" and part.image_base64:
                images.append(part.image_base64)

    return "\n".join(text_parts), images


class OllamaProvider(LLMProvider, EmbeddingProvider):
    def __init__(self, session: aiohttp.ClientSession, *, request_timeout_seconds: int):
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)

    def _build_payload(self, backend: ProviderBackend, request: ChatCompletionRequest) -> dict:
        prompt, images = _flatten_prompt_and_images(request)
        # backend.target_model lets a failover backend run a different
        # actual model than the tenant requested (e.g. qwen3.5:9b's chain
        # falling over to qwen3.6:35b) — see gateway/models/provider.py.
        payload: dict = {
            "model": backend.target_model or request.model,
            "prompt": prompt,
            "stream": True,
            "think": False,
            "keep_alive": backend.keep_alive,
            "options": {
                "temperature": request.temperature,
                "top_k": request.top_k,
                "top_p": request.top_p,
                "repeat_penalty": request.repeat_penalty,
                "repeat_last_n": request.repeat_last_n,
                "seed": request.seed,
                "num_ctx": request.num_ctx,
                "num_predict": request.num_predict,
            },
        }
        if images:
            payload["images"] = images
        return payload

    async def generate(
        self, *, backend: ProviderBackend, request: ChatCompletionRequest
    ) -> ProviderResult:
        started = time.monotonic()
        payload = self._build_payload(backend, request)

        try:
            async with self._session.post(
                f"{backend.base_url}/api/generate", json=payload, timeout=self._timeout
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise AllProvidersUnavailable(
                        f"Backend {backend.id} returned {response.status}: {body[:300]}"
                    )

                full_response = ""
                final_chunk: dict = {}
                async for line in response.content:
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    full_response += chunk.get("response", "")
                    if chunk.get("done"):
                        final_chunk = chunk
                        break

        except TimeoutError as exc:
            raise ProviderTimeout(f"Backend {backend.id} timed out") from exc
        except aiohttp.ClientError as exc:
            raise AllProvidersUnavailable(f"Backend {backend.id} unreachable: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return ProviderResult(
            content=full_response.strip(),
            finish_reason=final_chunk.get("done_reason", "unknown"),
            prompt_tokens=final_chunk.get("prompt_eval_count", 0),
            completion_tokens=final_chunk.get("eval_count", 0),
            latency_ms=latency_ms,
            served_model=payload["model"],
        )

    async def health_check(self, *, backend: ProviderBackend, model: str) -> None:
        probe = ChatCompletionRequest(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            num_predict=1,
        )
        await self.generate(backend=backend, request=probe)

    async def embed(
        self, *, backend: ProviderBackend, request: EmbeddingRequest
    ) -> EmbeddingProviderResult:
        started = time.monotonic()
        target_model = backend.target_model or request.model
        payload = {"model": target_model, "input": request.inputs}

        try:
            async with self._session.post(
                f"{backend.base_url}/api/embed", json=payload, timeout=self._timeout
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise AllProvidersUnavailable(
                        f"Backend {backend.id} returned {response.status}: {body[:300]}"
                    )
                data = await response.json()
        except TimeoutError as exc:
            raise ProviderTimeout(f"Backend {backend.id} timed out") from exc
        except aiohttp.ClientError as exc:
            raise AllProvidersUnavailable(f"Backend {backend.id} unreachable: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return EmbeddingProviderResult(
            embeddings=data.get("embeddings", []),
            prompt_tokens=data.get("prompt_eval_count", 0),
            latency_ms=latency_ms,
            served_model=target_model,
        )

    async def embedding_health_check(self, *, backend: ProviderBackend, model: str) -> None:
        probe = EmbeddingRequest(model=model, input="ping")
        await self.embed(backend=backend, request=probe)
