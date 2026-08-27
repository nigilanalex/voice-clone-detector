"""FastAPI application for VoiceGuard AI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from model_engine import (
    AudioDecodeError,
    AudioValidationError,
    ModelLoadError,
    VoiceCloneDetector,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"
}
STREAM_SAMPLE_RATE = 16_000
STREAM_WINDOW_BYTES = 5 * STREAM_SAMPLE_RATE * 2
STREAM_HOP_BYTES = 2 * STREAM_SAMPLE_RATE * 2
MAX_STREAM_BUFFER_BYTES = 15 * STREAM_SAMPLE_RATE * 2
MAX_STREAM_FRAME_BYTES = 64 * 1024
MAX_STREAM_SETUP_BYTES = 2 * 1024


def positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_CONCURRENT_INFERENCES = positive_env_int(
    "VOICEGUARD_MAX_CONCURRENT_INFERENCES", 1
)
UPLOAD_RATE_LIMIT = positive_env_int("VOICEGUARD_UPLOADS_PER_5_MINUTES", 12)
WEBSOCKET_RATE_LIMIT = positive_env_int("VOICEGUARD_STREAMS_PER_MINUTE", 8)


class SlidingWindowRateLimiter:
    """Small single-worker limiter for expensive public demo endpoints."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        cutoff = time.monotonic() - window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(time.monotonic())
            return True

app = FastAPI(
    title="VoiceGuard AI",
    description="Probabilistic AI-generated voice screening for recordings and live microphone audio.",
    version="1.4.2",
)
detector = VoiceCloneDetector()
inference_slots = asyncio.Semaphore(MAX_CONCURRENT_INFERENCES)
rate_limiter = SlidingWindowRateLimiter()
model_warmup_task: asyncio.Task[None] | None = None

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def client_key(headers: Any, fallback: str | None) -> str:
    forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or fallback or "unknown"


async def warm_model() -> None:
    try:
        await run_inference(detector.load)
    except ModelLoadError:
        # Readiness exposes the load error without preventing the UI from starting.
        return


@app.on_event("startup")
async def start_model_warmup() -> None:
    global model_warmup_task
    model_warmup_task = asyncio.create_task(warm_model())


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Apply a conservative browser policy to every HTTP response."""

    if request.url.path == "/api/analyze-file":
        key = client_key(request.headers, request.client.host if request.client else None)
        if not await rate_limiter.allow(f"upload:{key}", UPLOAD_RATE_LIMIT, 300):
            return JSONResponse(
                status_code=429,
                content=error_payload(
                    "rate_limited",
                    "Too many analyses from this device. Wait a few minutes and try again.",
                ),
                headers={"Retry-After": "300"},
            )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self' blob:; connect-src 'self' ws: wss:; "
        "worker-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Request-ID"] = request.headers.get("X-Request-ID", str(uuid4()))
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    if forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


async def run_inference(function, *args):
    """Bound heavyweight model work so requests cannot create an unbounded queue."""

    async with inference_slots:
        return await asyncio.to_thread(function, *args)


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    """Reject browser WebSockets opened by an unrelated website."""

    origin = websocket.headers.get("origin")
    if not origin:
        return True
    origin_host = urlparse(origin).netloc.lower()
    request_host = websocket.headers.get("host") or ""
    request_host = request_host.split(",", 1)[0].strip().lower()
    return bool(origin_host and request_host and origin_host == request_host)


@app.get("/", include_in_schema=False)
async def get_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/privacy", include_in_schema=False)
async def get_privacy() -> FileResponse:
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/consent", include_in_schema=False)
async def get_consent() -> FileResponse:
    return FileResponse(STATIC_DIR / "consent.html")


@app.get("/disclaimer", include_in_schema=False)
async def get_disclaimer() -> FileResponse:
    return FileResponse(STATIC_DIR / "disclaimer.html")


@app.get("/sw.js", include_in_schema=False)
async def get_service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "VoiceGuard AI",
        "version": app.version,
        "model": detector.status(),
        "limits": {
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_duration_seconds": detector.MAX_AUDIO_SECONDS,
            "max_live_frame_kb": MAX_STREAM_FRAME_BYTES // 1024,
            "max_concurrent_inferences": MAX_CONCURRENT_INFERENCES,
            "uploads_per_5_minutes": UPLOAD_RATE_LIMIT,
            "streams_per_minute": WEBSOCKET_RATE_LIMIT,
        },
        "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        "stream_protocol": {
            "format": "pcm_s16le",
            "sample_rate": STREAM_SAMPLE_RATE,
            "channels": 1,
            "window_seconds": STREAM_WINDOW_BYTES // (STREAM_SAMPLE_RATE * 2),
            "hop_seconds": STREAM_HOP_BYTES // (STREAM_SAMPLE_RATE * 2),
        },
    }


@app.get("/api/ready")
async def readiness() -> JSONResponse:
    status = detector.status()
    ready = detector.is_ready
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "warming_up",
            "model": status,
        },
    )


@app.post("/api/analyze-file")
async def analyze_file(file: UploadFile = File(...)) -> JSONResponse:
    filename = file.filename or "recording"
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=error_payload(
                "unsupported_file_type",
                "Choose a WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, or WebM recording.",
            )["error"],
        )

    contents = bytearray()
    try:
        while chunk := await file.read(1024 * 1024):
            contents.extend(chunk)
            if len(contents) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=error_payload(
                        "file_too_large", "The recording must be 25 MB or smaller."
                    )["error"],
                )
    finally:
        await file.close()

    if not contents:
        raise HTTPException(
            status_code=400,
            detail=error_payload("empty_file", "The selected recording is empty.")["error"],
        )

    try:
        inference_started = time.perf_counter()
        result = await run_inference(
            detector.process_file_bytes, bytes(contents), filename
        )
        processing_ms = round((time.perf_counter() - inference_started) * 1000)
    except (AudioDecodeError, AudioValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=error_payload("invalid_audio", str(exc))["error"],
        ) from exc
    except ModelLoadError as exc:
        raise HTTPException(
            status_code=503,
            detail=error_payload("model_unavailable", str(exc))["error"],
        ) from exc

    return JSONResponse(
        {
            "filename": filename,
            "content_type": file.content_type,
            "analysis": result,
            "analysis_metadata": {
                "analyzed_at": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(contents).hexdigest(),
                "model": detector.model_name,
                "model_revision": detector.model_revision,
                "service_version": app.version,
                "processing_ms": processing_ms,
            },
        }
    )


async def send_ws_error(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json(
        {"type": "error", "error": {"code": code, "message": message}}
    )


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    if not websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return

    key = client_key(
        websocket.headers,
        websocket.client.host if websocket.client else None,
    )
    if not await rate_limiter.allow(f"stream:{key}", WEBSOCKET_RATE_LIMIT, 60):
        await websocket.close(code=1008, reason="Too many live sessions. Try again shortly.")
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "state",
            "state": "connected",
            "message": "Microphone stream connected. Waiting for speech.",
        }
    )

    buffer = bytearray()
    configured = False

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("text") is not None:
                if len(message["text"].encode("utf-8")) > MAX_STREAM_SETUP_BYTES:
                    await send_ws_error(
                        websocket,
                        "message_too_large",
                        "Stream setup message is too large.",
                    )
                    await websocket.close(code=1009)
                    return
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await send_ws_error(websocket, "invalid_json", "Invalid stream setup message.")
                    continue

                if payload.get("type") != "start":
                    await send_ws_error(websocket, "invalid_message", "Expected a start message.")
                    continue

                if (
                    payload.get("format") != "pcm_s16le"
                    or payload.get("sample_rate") != STREAM_SAMPLE_RATE
                    or payload.get("channels") != 1
                ):
                    await send_ws_error(
                        websocket,
                        "unsupported_audio_format",
                        "Live audio must be mono 16 kHz signed 16-bit PCM.",
                    )
                    await websocket.close(code=1003)
                    return

                configured = True
                await websocket.send_json(
                    {
                        "type": "state",
                        "state": "listening",
                        "message": "Listening for a clear speech sample...",
                    }
                )
                continue

            data = message.get("bytes")
            if data is None:
                continue
            if not configured:
                await send_ws_error(
                    websocket, "setup_required", "Send stream settings before PCM audio."
                )
                continue
            if not data or len(data) % 2:
                await send_ws_error(
                    websocket, "invalid_pcm", "Received an invalid PCM audio frame."
                )
                continue
            if len(data) > MAX_STREAM_FRAME_BYTES:
                await send_ws_error(
                    websocket,
                    "frame_too_large",
                    f"Live audio frames must be {MAX_STREAM_FRAME_BYTES // 1024} KB or smaller.",
                )
                await websocket.close(code=1009)
                return

            buffer.extend(data)
            if len(buffer) > MAX_STREAM_BUFFER_BYTES:
                # Keep only the newest audio if a client sends faster than inference.
                del buffer[:-STREAM_WINDOW_BYTES]

            while len(buffer) >= STREAM_WINDOW_BYTES:
                chunk = bytes(buffer[:STREAM_WINDOW_BYTES])
                del buffer[:STREAM_HOP_BYTES]
                if not detector.is_ready:
                    await websocket.send_json(
                        {
                            "type": "state",
                            "state": "loading_model",
                            "message": "Preparing the detector. The first run can take a few minutes...",
                        }
                    )

                try:
                    inference_started = time.perf_counter()
                    result = await run_inference(
                        detector.process_raw_pcm, chunk, STREAM_SAMPLE_RATE
                    )
                    result["processing_ms"] = round(
                        (time.perf_counter() - inference_started) * 1000
                    )
                except AudioValidationError as exc:
                    await send_ws_error(websocket, "invalid_audio", str(exc))
                    continue
                except ModelLoadError as exc:
                    await send_ws_error(websocket, "model_unavailable", str(exc))
                    await websocket.close(code=1011)
                    return

                await websocket.send_json({"type": "result", **result})

    except WebSocketDisconnect:
        return
    except Exception:
        # Avoid leaking internals while still ending the broken session.
        try:
            await send_ws_error(
                websocket,
                "stream_failure",
                "Live analysis stopped unexpectedly. Please start a new session.",
            )
            await websocket.close(code=1011)
        except Exception:
            pass
