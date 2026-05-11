"""OpenAI-compatible FastAPI application."""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from youtube_ask_proxy.browser import (
    AuthenticationRequiredError,
    BrowserAutomationError,
    BrowserController,
)
from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import configure_logging, get_logger
from youtube_ask_proxy.models import (
    APIErrorDetail,
    APIErrorResponse,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamChoice,
    ChatCompletionStreamResponse,
    ModelData,
    ModelListResponse,
)
from youtube_ask_proxy.prompts import build_ask_prompt

logger = get_logger(__name__)
security = HTTPBearer(auto_error=False)

# Global browser controller instance (managed via lifespan)
_browser_controller: BrowserController | None = None


async def _get_browser_controller() -> BrowserController:
    """Get or initialize the global browser controller."""
    global _browser_controller
    if _browser_controller is None or getattr(_browser_controller, "_closed", True):
        _browser_controller = BrowserController()
        await _browser_controller.start()
    return _browser_controller


_bearer_security = Security(security)


async def _verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = _bearer_security,
) -> None:
    """Verify API key if one is configured."""
    if settings.api_key is None:
        return
    if credentials is None or credentials.credentials != settings.api_key:
        logger.warning("Unauthorized API request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: startup and shutdown."""
    configure_logging()
    logger.info("Application starting up", version="0.1.0")

    # Pre-launch browser to fail fast on config issues
    try:
        await _get_browser_controller()
        logger.info("Browser controller pre-launched successfully")
    except Exception as e:
        logger.error("Failed to pre-launch browser controller", error=str(e))
        # Don't raise — allow API to start, but requests will fail gracefully

    yield

    # Shutdown
    global _browser_controller
    if _browser_controller is not None:
        await _browser_controller.stop()
        _browser_controller = None
    logger.info("Application shut down")


app = FastAPI(
    title="YouTube Ask Proxy API",
    description="OpenAI-compatible API proxy for the YouTube Ask feature",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------


@app.exception_handler(BrowserAutomationError)
async def browser_error_handler(request: Request, exc: BrowserAutomationError) -> JSONResponse:
    logger.error("Browser automation error", error=str(exc), path=request.url.path)
    error_detail = APIErrorDetail(
        message=str(exc),
        type="browser_automation_error",
        code="browser_error",
    )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=APIErrorResponse(error=error_detail).model_dump(),
    )


@app.exception_handler(AuthenticationRequiredError)
async def auth_error_handler(request: Request, exc: AuthenticationRequiredError) -> JSONResponse:
    logger.error("Authentication error", error=str(exc), path=request.url.path)
    error_detail = APIErrorDetail(
        message=str(exc),
        type="authentication_error",
        code="auth_required",
    )
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=APIErrorResponse(error=error_detail).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error_detail = APIErrorDetail(
        message=exc.detail,
        type="invalid_request_error",
        code=str(exc.status_code),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=APIErrorResponse(error=error_detail).model_dump(),
        headers=getattr(exc, "headers", None) or {},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", path=request.url.path)
    error_detail = APIErrorDetail(
        message="An internal server error occurred.",
        type="internal_server_error",
        code="internal_error",
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=APIErrorResponse(error=error_detail).model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/models", response_model=ModelListResponse)
async def list_models(
    _: None = Security(_verify_api_key),
) -> ModelListResponse:
    """List available models (OpenAI-compatible)."""
    return ModelListResponse(
        data=[
            ModelData(
                id=settings.default_model,
                object="model",
                created=0,
                owned_by="youtube-ask-proxy",
            )
        ]
    )


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: ChatCompletionRequest,
    _: None = Security(_verify_api_key),
) -> ChatCompletionResponse | StreamingResponse:
    """Create a chat completion (OpenAI-compatible).

    Internally delegates to the YouTube Ask feature via browser automation.
    """
    prompt, video_url = build_ask_prompt(request.messages, request.video_url)

    if not video_url:
        logger.error("No video URL provided or inferred")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No YouTube video URL found. Provide 'video_url' in the request or include a URL in the messages.",
        )

    if request.stream:
        return StreamingResponse(
            _stream_chat_completion(video_url, prompt, request.model),
            media_type="text/event-stream",
        )

    controller = await _get_browser_controller()
    response_data = await controller.ask(video_url, prompt)

    # Convert parsed dict to JSON string for content
    import json

    content = json.dumps(response_data, ensure_ascii=False, indent=2)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())

    return ChatCompletionResponse(
        id=completion_id,
        object="chat.completion",
        created=created_ts,
        model=request.model or settings.default_model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content=content,
                ),
                finish_reason="stop",
            )
        ],
        usage={
            "prompt_tokens": len(prompt.split()),
            "completion_tokens": len(content.split()),
            "total_tokens": len(prompt.split()) + len(content.split()),
        },
    )


async def _stream_chat_completion(
    video_url: str,
    prompt: str,
    model: str,
) -> AsyncIterator[str]:
    """Stream chat completion responses as Server-Sent Events.

    Note: Since YouTube Ask does not natively stream, we simulate streaming
    by yielding the full response as a single chunk.
    """
    import json

    controller = await _get_browser_controller()
    response_data = await controller.ask(video_url, prompt)
    content = json.dumps(response_data, ensure_ascii=False)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    model_name = model or settings.default_model

    # Yield the single content chunk
    chunk = ChatCompletionStreamResponse(
        id=completion_id,
        object="chat.completion.chunk",
        created=created_ts,
        model=model_name,
        choices=[
            ChatCompletionStreamChoice(
                index=0,
                delta=ChatCompletionMessage(role="assistant", content=content),
                finish_reason=None,
            )
        ],
    )
    yield f"data: {chunk.model_dump_json()}\n\n"

    # Yield final chunk with stop reason
    final_chunk = ChatCompletionStreamResponse(
        id=completion_id,
        object="chat.completion.chunk",
        created=created_ts,
        model=model_name,
        choices=[
            ChatCompletionStreamChoice(
                index=0,
                delta=ChatCompletionMessage(role="assistant", content=""),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {final_chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"
