"""OpenAI-compatible FastAPI application."""

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from youtube_ask_proxy.browser import (
    AskFeatureNotFoundError,
    AuthenticationRequiredError,
    BrowserAutomationError,
    BrowserController,
    ResponseTimeoutError,
)
from youtube_ask_proxy.config import settings
from youtube_ask_proxy.gemini import (
    GeminiAPIError,
    GeminiNotConfiguredError,
    GeminiSummarizationError,
    summarize_video,
)
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
    logger.info("Application starting up", version="0.2.0")

    # Pre-launch browser to fail fast on config issues (only if Gemini is not
    # configured, since Playwright will be the primary method in that case).
    if not settings.gemini_api_key:
        try:
            await _get_browser_controller()
            logger.info("Browser controller pre-launched successfully")
        except Exception as e:
            logger.error("Failed to pre-launch browser controller", error=str(e))
            # Don't raise — allow API to start, but requests will fail gracefully
    else:
        logger.info("Gemini API configured; browser controller lazy-loaded on first request")

    yield

    # Shutdown
    global _browser_controller
    if _browser_controller is not None:
        await _browser_controller.stop()
        _browser_controller = None
    logger.info("Application shut down")


app = FastAPI(
    title="YouTube Ask Proxy API",
    description="OpenAI-compatible API proxy for YouTube video summarization via Gemini API (primary) and browser automation (fallback).",
    version="0.2.0",
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


@app.exception_handler(GeminiAPIError)
async def gemini_error_handler(request: Request, exc: GeminiAPIError) -> JSONResponse:
    logger.error("Gemini API error", error=str(exc), path=request.url.path)
    error_detail = APIErrorDetail(
        message=str(exc),
        type="gemini_api_error",
        code="gemini_error",
    )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
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
# Internal helpers
# ---------------------------------------------------------------------------


def _build_completion_response(
    content: dict[str, object],
    model: str,
    prompt: str,
) -> ChatCompletionResponse:
    """Build an OpenAI-compatible ChatCompletionResponse from parsed data."""
    content_json = json.dumps(content, ensure_ascii=False, indent=2)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())

    return ChatCompletionResponse(
        id=completion_id,
        object="chat.completion",
        created=created_ts,
        model=model or settings.default_model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content=content_json,
                ),
                finish_reason="stop",
            )
        ],
        usage={
            "prompt_tokens": len(prompt.split()),
            "completion_tokens": len(content_json.split()),
            "total_tokens": len(prompt.split()) + len(content_json.split()),
        },
    )


def _build_unavailable_response(
    model: str,
    prompt: str,
) -> ChatCompletionResponse:
    """Build a graceful 'unavailable' response when both methods fail."""
    fallback_content = {
        "error": True,
        "message": "Summarization is not available for this video.",
        "details": (
            "The video may not be accessible, the Gemini API key is invalid, "
            "the Ask feature is not enabled for this video, or the service "
            "is temporarily unavailable. Please try again later or with a different video."
        ),
    }
    return _build_completion_response(fallback_content, model, prompt)


async def _summarize_with_gemini(
    video_url: str,
    prompt: str,
) -> dict[str, object] | None:
    """Try to summarize via Gemini API. Returns None on failure so caller can fall back."""
    if not settings.gemini_api_key or not settings.gemini_enabled:
        logger.debug("Gemini API not configured or disabled, skipping")
        return None

    try:
        result = await summarize_video(video_url, prompt)
        logger.info("Gemini summarization succeeded")
        return result
    except GeminiNotConfiguredError:
        logger.warning("Gemini API key not configured")
        return None
    except GeminiSummarizationError as exc:
        logger.warning("Gemini summarization failed", error=str(exc))
        return None
    except Exception as exc:
        logger.warning("Unexpected Gemini error", error=str(exc))
        return None


async def _summarize_with_playwright(
    video_url: str,
    prompt: str,
) -> dict[str, object] | None:
    """Try to summarize via Playwright / YouTube Ask. Returns None on failure."""
    try:
        controller = await _get_browser_controller()
        result = await controller.ask(video_url, prompt)
        logger.info("Playwright summarization succeeded")
        return result
    except AskFeatureNotFoundError as exc:
        logger.warning("Ask feature not found", error=str(exc))
        return None
    except ResponseTimeoutError as exc:
        logger.warning("Response timeout", error=str(exc))
        return None
    except AuthenticationRequiredError as exc:
        logger.warning("Authentication required", error=str(exc))
        return None
    except BrowserAutomationError as exc:
        logger.warning("Browser automation failed", error=str(exc))
        return None
    except Exception as exc:
        logger.warning("Unexpected Playwright error", error=str(exc))
        return None


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

    Strategy:
        1. Try Gemini API first (fast, no browser needed).
        2. If Gemini fails or is not configured, fall back to Playwright / YouTube Ask.
        3. If both fail, return a graceful "unavailable" message.
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

    # Phase 1: Gemini API (primary)
    result = await _summarize_with_gemini(video_url, prompt)

    # Phase 2: Playwright fallback
    if result is None:
        logger.info("Gemini failed or unavailable, falling back to Playwright")
        result = await _summarize_with_playwright(video_url, prompt)

    # Phase 3: Graceful degradation
    if result is None:
        logger.error("Both Gemini and Playwright failed; returning unavailable response")
        return _build_unavailable_response(request.model or settings.default_model, prompt)

    return _build_completion_response(result, request.model or settings.default_model, prompt)


async def _stream_chat_completion(
    video_url: str,
    prompt: str,
    model: str,
) -> AsyncIterator[str]:
    """Stream chat completion responses as Server-Sent Events.

    Note: Since neither Gemini nor YouTube Ask natively stream tokens,
    we simulate streaming by yielding the full response as a single chunk.
    """
    result = await _summarize_with_gemini(video_url, prompt)
    method = "gemini"

    if result is None:
        logger.info("Gemini failed or unavailable, falling back to Playwright (stream)")
        result = await _summarize_with_playwright(video_url, prompt)
        method = "playwright"

    if result is None:
        logger.error("Both methods failed; returning unavailable response (stream)")
        result = {
            "error": True,
            "message": "Summarization is not available for this video.",
            "details": (
                "The video may not be accessible, the Gemini API key is invalid, "
                "the Ask feature is not enabled for this video, or the service "
                "is temporarily unavailable."
            ),
        }

    content = json.dumps(result, ensure_ascii=False)
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


# ---------------------------------------------------------------------------
# Test / Comparison Endpoints
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class _TestSummarizeRequest(BaseModel):
    """Request body for test comparison endpoints."""

    video_url: str
    prompt: str | None = None


class _TestSummarizeResponse(BaseModel):
    """Response from a test comparison endpoint."""

    method: str
    success: bool
    content: dict[str, object] | None = None
    error: str | None = None
    duration_ms: int


@app.post("/v1/test/gemini", response_model=_TestSummarizeResponse)
async def test_gemini(
    request: _TestSummarizeRequest,
    _: None = Security(_verify_api_key),
) -> _TestSummarizeResponse:
    """Test summarization using the Gemini API only."""
    import time

    start = time.time()
    prompt = request.prompt or build_ask_prompt([], request.video_url)[0]

    try:
        result = await summarize_video(request.video_url, prompt)
        return _TestSummarizeResponse(
            method="gemini",
            success=True,
            content=result,
            duration_ms=int((time.time() - start) * 1000),
        )
    except Exception as exc:
        return _TestSummarizeResponse(
            method="gemini",
            success=False,
            error=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )


@app.post("/v1/test/playwright", response_model=_TestSummarizeResponse)
async def test_playwright(
    request: _TestSummarizeRequest,
    _: None = Security(_verify_api_key),
) -> _TestSummarizeResponse:
    """Test summarization using Playwright / YouTube Ask only."""
    import time

    start = time.time()
    prompt = request.prompt or build_ask_prompt([], request.video_url)[0]

    try:
        controller = await _get_browser_controller()
        result = await controller.ask(request.video_url, prompt)
        return _TestSummarizeResponse(
            method="playwright",
            success=True,
            content=result,
            duration_ms=int((time.time() - start) * 1000),
        )
    except Exception as exc:
        return _TestSummarizeResponse(
            method="playwright",
            success=False,
            error=str(exc),
            duration_ms=int((time.time() - start) * 1000),
        )
