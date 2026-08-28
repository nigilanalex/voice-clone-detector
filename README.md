---
title: VoiceGuard AI
emoji: 🛡️
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
suggested_hardware: cpu-basic
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
- System dashboard with real backend, model, device, transport, and version state
- Measured server processing time and local result-quality feedback
- Pitch, pause, and spectral diagnostics for an explainable prosody demo
- Optional trusted-reference voice comparison using an experimental MFCC heuristic
- Context-aware impersonation scoring for transfer, credential, and data scenarios
- Optional real email/webhook alerts plus simulated transaction-hold workflows
- Tamper-evident SHA-256 audit chain with a verifiable hash in every report

The detector is decision support, not proof that a speaker is genuine or fraudulent.
Live microphone detection is experimental: replay, echo, call codecs, compression,
and microphone processing can produce false negatives.

For a reliable presentation sequence, prepared audio guidance, and a two-minute
script, see [DEMO.md](DEMO.md).

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

The container starts the web interface immediately and warms the pinned model in the
background. Use `/api/health` for liveness and `/api/ready` for model readiness; the
readiness endpoint returns HTTP 503 until the model can perform inference.

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
5. Signal measurements report audio quality, spectrum, pitch variation, and pauses.
6. Optional MFCC similarity compares the sample with a consented reference recording.
7. A transparent policy combines the AI score with caller origin, urgency, workflow,
   sensitive-request, and new-beneficiary indicators.

## Hackathon security workflow

The upload screen includes an end-to-end impersonation-response demonstration. Its
audio model, DSP measurements, file fingerprint, contextual policy calculation, and
SQLite metadata audit are real. Configured SMTP email and HTTPS webhook notifications
are also real. Transaction holds and telephony integration remain simulated because
no bank or telecom platform is connected. Every simulated event is labelled in the
interface and JSON evidence report.

The optional voice-reference score is a lightweight acoustic similarity heuristic,
not biometric authentication. Language selection records evaluation context but does
not claim that the model has been calibrated for that language or accent.

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
    VOICEGUARD_UPLOADS_PER_5_MINUTES=12
    VOICEGUARD_STREAMS_PER_MINUTE=8
    VOICEGUARD_POLICY_MEDIUM=25
    VOICEGUARD_POLICY_HIGH=45
    VOICEGUARD_POLICY_CRITICAL=70

### Audit database and real external alerts

VoiceGuard now writes metadata-only incident records to `data/voiceguard.db` using
SQLite. The default retention period is 30 days. The database never stores audio or
filenames and is excluded from Git. Configure a different writable path or retention:

    $env:VOICEGUARD_AUDIT_DB="D:\voiceguard-data\voiceguard.db"
    $env:VOICEGUARD_AUDIT_RETENTION_DAYS="30"

HIGH and CRITICAL events can send real metadata-only notifications through an HTTPS
webhook, SMTP email, or both. Analysis continues even if a provider is unavailable.

    $env:VOICEGUARD_ALERT_WEBHOOK_URL="https://your-automation-endpoint.example/hook"
    $env:VOICEGUARD_SMTP_HOST="smtp.example.com"
    $env:VOICEGUARD_SMTP_PORT="587"
    $env:VOICEGUARD_SMTP_USERNAME="voiceguard@example.com"
    $env:VOICEGUARD_SMTP_PASSWORD="use-a-provider-app-password"
    $env:VOICEGUARD_ALERT_FROM="voiceguard@example.com"
    $env:VOICEGUARD_ALERT_TO="security@example.com"

Set environment variables in the same terminal before starting Uvicorn. `.env.example`
documents every integration variable; `.env` is ignored by Git. Never commit secrets.
Webhook payloads include `text` and `content` fields plus a structured incident object,
making them suitable for common automation, Slack-compatible, and Discord-compatible
HTTPS endpoints.

Each audit row is cryptographically linked to the previous row using SHA-256. The
result screen and downloaded report expose the incident's chain hash, while the system
dashboard verifies internal chain consistency. Modification, reordering, or deletion
inside the retained chain causes verification to fail. Keeping a downloaded report or
external webhook copy of the latest hash also provides an external anchor that can
reveal database truncation. This is tamper evidence, not prevention: an administrator
with full server access can still replace the entire database and its external anchors.

The revision is pinned by default so the reviewed class mapping and model weights do
not silently change.

## Production checklist

- Serve only through HTTPS and an authenticated reverse proxy.
- Replace the included single-worker IP rate limiter with a shared Redis-backed
  limiter if the service is scaled to multiple replicas.
- Keep the included same-origin WebSocket policy enabled at the reverse proxy.
- Replace the generic legal pages with the deployment owner's contact and
  jurisdiction-specific terms before a commercial launch.
- Run inference in a dedicated worker or GPU service.
- Evaluate and calibrate the detector on the exact languages and call codecs in use.
- Keep a human verification path; never block or accuse someone from one score alone.
- Protect and back up the SQLite audit path, or replace it with PostgreSQL when
  deploying multiple application replicas.

## Project layout

    app.py                     FastAPI routes and WebSocket protocol
    model_engine.py            Audio decoding, validation, DSP, and model inference
    integrations.py            SQLite audit, HTTPS webhook, and SMTP alert adapters
    requirements.txt           Python runtime dependencies
    static/index.html          Accessible application UI
    static/styles.css          Responsive offline-ready design
    static/app.js              Upload, live audio, and PWA client logic
    static/pcm-worklet.js      16 kHz PCM microphone resampler
    static/manifest.webmanifest
    static/sw.js               App-shell service worker
    static/icon.svg            PWA icon
    tests/                     Fast model-independent checks
