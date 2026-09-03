"""Gateway HTTP routes.

See ARCHITECTURE.md §4.5/§9. /v1/chat/completions is the provider-agnostic
entrypoint; /v1/generate-with-image preserves the pre-gateway multipart
contract as a thin adapter onto the same GatewayService.handle_request()
call — there is exactly one function underneath both routes.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, UploadFile

from gateway.api.deps import authenticated_tenant, get_gateway_service
from gateway.config import settings
from gateway.core.exceptions import PayloadTooLarge
from gateway.core.service import GatewayService
from gateway.models.chat import ChatCompletionRequest, ChatCompletionResponse, ContentPart, Message
from gateway.models.tenant import ApiKey, Tenant

router = APIRouter(prefix="/v1", tags=["gateway"])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    tenant_and_key: tuple[Tenant, ApiKey] = Depends(authenticated_tenant),
    service: GatewayService = Depends(get_gateway_service),
) -> ChatCompletionResponse:
    tenant, api_key = tenant_and_key
    return await service.handle_request(tenant=tenant, api_key=api_key, request=request)


@router.post("/generate-with-image", response_model=ChatCompletionResponse)
async def generate_with_image(
    image: UploadFile = File(...),
    prompt: str = Form(...),
    model: str = Form(default="qwen3.5:9b"),
    tenant_and_key: tuple[Tenant, ApiKey] = Depends(authenticated_tenant),
    service: GatewayService = Depends(get_gateway_service),
) -> ChatCompletionResponse:
    """Back-compat adapter for the pre-gateway multipart contract
    (`image` + `prompt` form fields). Builds the same ChatCompletionRequest
    that /v1/chat/completions would and calls the same core path —
    ARCHITECTURE.md §9's "exactly one code path" guarantee."""
    tenant, api_key = tenant_and_key

    image_bytes = await image.read()
    max_bytes = settings.max_image_size_mb * 1024 * 1024
    if len(image_bytes) > max_bytes:
        # config.py's MAX_IMAGE_SIZE_MB existed pre-gateway but was never
        # enforced (PRD.md §10b) — this is the fix.
        raise PayloadTooLarge(
            f"Image is {len(image_bytes) / 1024 / 1024:.1f}MB, exceeds "
            f"{settings.max_image_size_mb}MB limit"
        )

    ext = (image.filename or "").rsplit(".", 1)[-1].lower()
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"

    chat_request = ChatCompletionRequest(
        model=model,
        messages=[
            Message(
                role="user",
                content=[
                    ContentPart(type="text", text=prompt),
                    ContentPart(
                        type="image_base64",
                        image_base64=base64.b64encode(image_bytes).decode("utf-8"),
                        media_type=media_type,
                    ),
                ],
            )
        ],
    )
    return await service.handle_request(tenant=tenant, api_key=api_key, request=chat_request)
