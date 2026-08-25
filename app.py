"""FastAPI application for VoiceGuard AI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
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
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
STREAM_SAMPLE_RATE = 16_000
STREAM_WINDOW_BYTES = 5 * STREAM_SAMPLE_RATE * 2
STREAM_HOP_BYTES = 2 * STREAM_SAMPLE_RATE * 2
MAX_STREAM_BUFFER_BYTES = 15 * STREAM_SAMPLE_RATE * 2

app = FastAPI(
    title="VoiceGuard AI",
    description="Probabilistic AI-generated voice screening for recordings and live microphone audio.",
    version="1.0.0",
)
detector = VoiceCloneDetector()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


@app.get("/", include_in_schema=False)
async def get_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
        "model": detector.status(),
        "limits": {
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_duration_seconds": detector.MAX_AUDIO_SECONDS,
        },
    }


@app.post("/api/analyze-file")
async def analyze_file(file: UploadFile = File(...)) -> JSONResponse:
    filename = file.filename or "recording"
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=error_payload(
                "unsupported_file_type",
                "Choose a WAV, MP3, M4A, AAC, FLAC, OGG, or OPUS recording.",
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
        result = await asyncio.to_thread(
            detector.process_file_bytes, bytes(contents), filename
        )
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
        }
    )


async def send_ws_error(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json(
        {"type": "error", "error": {"code": code, "message": message}}
    )


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
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
                        "message": "Listening for a clear speech sample…",
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
                            "message": "Preparing the detector. The first run can take a few minutes…",
                        }
                    )

                try:
                    result = await asyncio.to_thread(
                        detector.process_raw_pcm, chunk, STREAM_SAMPLE_RATE
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
