---
title: VoiceGuard AI
emoji: 🛡️
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
---

# VoiceGuard AI

VoiceGuard is a full-stack AI voice-clone screening MVP. It supports:

- Uploaded WAV, MP3, M4A, WebM, AAC, FLAC, OGG, and OPUS recordings
- Direct in-browser microphone recording for mobile and desktop
- Rolling live microphone analysis through a WebSocket
- Audio-quality checks for silence, low volume, and clipping
- Probabilistic labels: **Likely human**, **Uncertain**, and **Likely synthetic**
- A responsive, installable Progressive Web App (PWA)
- Per-window forensic timeline and consistency assessment
- SHA-256 evidence fingerprint and downloadable JSON report
- Private, metadata-only scan history stored in the user's browser

The detector is decision support, not proof that a speaker is genuine or fraudulent.

## Run locally

Python 3.11 or 3.12 is recommended.

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    uvicorn app:app --reload

Open http://127.0.0.1:8000. API documentation is available at
http://127.0.0.1:8000/docs.

## Deploy with Docker

The included production container uses Python 3.12, one Uvicorn worker, CPU-only
PyTorch, and downloads the pinned model during the image build so the first public
analysis does not need to fetch 1.26 GB of weights.

    docker build -t voiceguard-ai .
    docker run --rm -p 7860:7860 voiceguard-ai

Open http://127.0.0.1:7860. The same repository can be published as a Hugging Face
Docker Space; its required Space metadata is included at the top of this README.

The first real analysis downloads the pinned
[Wav2Vec2 deepfake voice detector](https://huggingface.co/garystafford/wav2vec2-deepfake-voice-detector).
Its weights are approximately 1.26 GB, so the first result can take several minutes.
Later starts use the local Hugging Face cache.

## How it works

1. The server converts audio to mono 16 kHz.
2. Uploaded recordings are capped at 25 MB and 30 minutes.
3. Up to five six-second windows are selected across a long recording.
4. The pinned Wav2Vec2 classifier scores each window using the checkpoint's declared
   fake label; VoiceGuard uses the median score to reduce the effect of one outlier.
5. Basic signal measurements report whether the sample is too quiet or clipping.

The displayed decision strength is the score's distance from the 50% decision
boundary. It is not a calibrated statistical confidence interval. Window variation
shows the spread between the lowest and highest analyzed window scores.

The acoustic measurements are diagnostics only. They do not alter the model score.
The 40% and 70% display thresholds are product defaults and must be calibrated against
representative phone, language, codec, noise, replay, and unseen-TTS data before
production use.

## Live stream protocol

Connect to ws://HOST/ws/stream (wss:// under HTTPS), then send:

    {
      "type": "start",
      "format": "pcm_s16le",
      "sample_rate": 16000,
      "channels": 1
    }

After the handshake, send mono signed 16-bit little-endian PCM binary frames no larger than 64 KB. The
server analyzes rolling five-second windows with a two-second hop and returns typed
state, result, or error JSON messages.

Browser WebSocket connections are restricted to the page's own origin. Heavy model
work is bounded to one concurrent inference by default so parallel requests cannot
exhaust CPU or GPU memory.

## Install on mobile

Deploy behind HTTPS, open the site in a supported mobile browser, and choose
**Install app** or **Add to Home Screen**. Microphone access works only in a secure
context (HTTPS or localhost), and the PWA must remain in the foreground.

### Important call-audio limitation

An ordinary website or installed PWA cannot intercept normal cellular-call audio.
Android and iOS protect that audio at the operating-system level. For this MVP:

- Put the call on speaker and run VoiceGuard on a second device, or
- Upload an exported recording after obtaining all required consent.

Direct in-call analysis is only practical when your own native application owns the
VoIP audio pipeline. In that architecture, send decoded remote-party PCM to the same
stream protocol (or run an optimized model on-device) before playback.

## Configuration

Optional environment variables:

    VOICEGUARD_MODEL_NAME=garystafford/wav2vec2-deepfake-voice-detector
    VOICEGUARD_MODEL_REVISION=c66306024a7ede0be291e9c4558b37634782dc4e
    VOICEGUARD_MAX_CONCURRENT_INFERENCES=1

The revision is pinned by default so the reviewed class mapping and model weights do
not silently change.

## Production checklist

- Serve only through HTTPS and an authenticated reverse proxy.
- Add per-user upload and WebSocket rate limits.
- Keep the included same-origin WebSocket policy enabled at the reverse proxy.
- Publish a retention and consent policy.
- Run inference in a dedicated worker or GPU service.
- Evaluate and calibrate the detector on the exact languages and call codecs in use.
- Keep a human verification path; never block or accuse someone from one score alone.

## Project layout

    app.py                     FastAPI routes and WebSocket protocol
    model_engine.py            Audio decoding, validation, DSP, and model inference
    requirements.txt           Python runtime dependencies
    static/index.html          Accessible application UI
    static/styles.css          Responsive offline-ready design
    static/app.js              Upload, live audio, and PWA client logic
    static/pcm-worklet.js      16 kHz PCM microphone resampler
    static/manifest.webmanifest
    static/sw.js               App-shell service worker
    static/icon.svg            PWA icon
    tests/                     Fast model-independent checks
