# VoiceGuard hackathon demonstration

## Before presenting

1. Start FastAPI and wait until the header says **Detector ready**.
2. Start Cloudflare Tunnel and open its HTTPS address on the presentation device.
3. Keep one clear human recording and one consented AI-generated recording ready.
4. Analyze both recordings once before the presentation.
5. Keep a screen recording or screenshots as an offline backup.

## Two-minute demonstration

1. Explain that VoiceGuard provides screening signals, not forensic proof.
2. Select **Load high-risk scenario** under Security context.
3. Upload the prepared AI-generated recording and select **Analyze recording**.
4. Show the model risk, decision strength, pitch, pauses, window timeline, and file fingerprint.
5. Show the combined impersonation risk and recommended security response.
6. Explain that notifications and transaction holds are labelled simulations; audio analysis and policy scoring are real.
7. Download the evidence report to show the complete JSON audit record.
8. Open **Dashboard view** to show the real backend, model, compute, transport, and version state.

## Optional reference comparison

Add a consented trusted voice recording before analysis. Describe the returned score as an experimental MFCC acoustic comparison, not biometric identity verification.

## Important fallback

Live microphone mode is experimental and replay through a room or phone speaker can hide synthetic artifacts. Use uploaded audio as the primary demonstration and live mode as a secondary experiment.

## Short explanation

> VoiceGuard uses a pinned Wav2Vec2 model to screen recorded and near-live speech for synthetic patterns. It adds acoustic diagnostics and transparent contextual rules to turn a model signal into an actionable impersonation-risk workflow. External alerts and transaction controls are simulated for the hackathon, while the audio analysis, evidence report, and policy calculation are real.
