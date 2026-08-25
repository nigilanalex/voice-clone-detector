"""Inference engine for uploaded and streaming voice-clone detection.

The detector is intentionally lazy-loaded. The selected checkpoint is large, and
loading it while FastAPI imports would make health checks and the web interface
unavailable during the initial download.
"""

from __future__ import annotations

import io
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

# Some packaged librosa/Numba combinations try to cache beside read-only
# site-packages. Give them an explicit writable runtime cache instead.
VOICEGUARD_NUMBA_CACHE = Path(tempfile.gettempdir()) / "voiceguard-numba-cache"
VOICEGUARD_NUMBA_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NUMBA_CACHE_DIR", str(VOICEGUARD_NUMBA_CACHE))

import librosa
import numpy as np
import torch
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification


class AudioDecodeError(ValueError):
    """Raised when an upload cannot be decoded as supported audio."""


class AudioValidationError(ValueError):
    """Raised when decoded audio is too short, too long, or otherwise unusable."""


class ModelLoadError(RuntimeError):
    """Raised when the remote/local model checkpoint cannot be loaded."""


class VoiceCloneDetector:
    """Wav2Vec2 voice-spoof detector with lightweight acoustic diagnostics."""

    DEFAULT_MODEL_NAME = "garystafford/wav2vec2-deepfake-voice-detector"
    # Pin the published model revision so label semantics and weights are stable.
    DEFAULT_MODEL_REVISION = "c66306024a7ede0be291e9c4558b37634782dc4e"
    TARGET_SAMPLE_RATE = 16_000
    MIN_AUDIO_SECONDS = 1.0
    MAX_AUDIO_SECONDS = 10 * 60
    FILE_WINDOW_SECONDS = 6.0
    MAX_FILE_WINDOWS = 5

    def __init__(
        self,
        model_name: str | None = None,
        model_revision: str | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv(
            "VOICEGUARD_MODEL_NAME", self.DEFAULT_MODEL_NAME
        )
        self.model_revision = model_revision or os.getenv(
            "VOICEGUARD_MODEL_REVISION", self.DEFAULT_MODEL_REVISION
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.feature_extractor: Any | None = None
        self.model: Any | None = None
        self.fake_label_index: int | None = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._loading = False
        self._last_load_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.model is not None and self.feature_extractor is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.is_ready,
            "loading": self.is_loading,
            "device": self.device,
            "model": self.model_name,
            "revision": self.model_revision,
            "last_error": self._last_load_error,
        }

    @staticmethod
    def _normalise_label(label: object) -> str:
        return str(label).strip().lower().replace("-", "_").replace(" ", "_")

    def _resolve_fake_label_index(self, config: Any) -> int:
        """Find the synthetic/fake class from checkpoint metadata."""

        if int(getattr(config, "num_labels", 0)) != 2:
            raise ModelLoadError("The detector requires a binary real/fake model.")

        fake_names = {
            "fake",
            "spoof",
            "synthetic",
            "deepfake",
            "ai",
            "ai_generated",
            "generated",
        }

        label2id = getattr(config, "label2id", None) or {}
        for label, index in label2id.items():
            if self._normalise_label(label) in fake_names:
                return int(index)

        id2label = getattr(config, "id2label", None) or {}
        for index, label in id2label.items():
            if self._normalise_label(label) in fake_names:
                return int(index)

        raise ModelLoadError(
            "The model does not declare a recognizable fake/synthetic output label."
        )

    def load(self) -> None:
        """Load the checkpoint once, safely across concurrent requests."""

        if self.is_ready:
            return

        with self._load_lock:
            if self.is_ready:
                return

            self._loading = True
            self._last_load_error = None
            try:
                try:
                    extractor = AutoFeatureExtractor.from_pretrained(
                        self.model_name,
                        revision=self.model_revision,
                        local_files_only=True,
                    )
                except OSError:
                    extractor = AutoFeatureExtractor.from_pretrained(
                        self.model_name,
                        revision=self.model_revision,
                    )
                try:
                    model = AutoModelForAudioClassification.from_pretrained(
                        self.model_name,
                        revision=self.model_revision,
                        local_files_only=True,
                    ).to(self.device)
                except OSError:
                    model = AutoModelForAudioClassification.from_pretrained(
                        self.model_name,
                        revision=self.model_revision,
                    ).to(self.device)
                model.eval()

                self.fake_label_index = self._resolve_fake_label_index(model.config)
                self.feature_extractor = extractor
                self.model = model
            except Exception as exc:
                self._last_load_error = str(exc)
                raise ModelLoadError(
                    "The detection model could not be loaded. On the first run, "
                    "check the internet connection and allow the model download."
                ) from exc
            finally:
                self._loading = False

    @staticmethod
    def _ensure_mono_float(audio_data: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio_data, dtype=np.float32).squeeze()
        if audio.ndim != 1 or audio.size == 0:
            raise AudioValidationError("No usable mono audio was found.")
        if not np.all(np.isfinite(audio)):
            raise AudioValidationError("The audio contains invalid sample values.")
        return np.clip(audio, -1.0, 1.0)

    def _audio_quality(self, audio_data: np.ndarray, sr: int) -> dict[str, Any]:
        rms = float(np.sqrt(np.mean(np.square(audio_data), dtype=np.float64)))
        peak = float(np.max(np.abs(audio_data)))
        rms_db = 20.0 * math.log10(max(rms, 1e-8))
        clipping_ratio = float(np.mean(np.abs(audio_data) >= 0.995))

        spectrum = np.abs(librosa.stft(audio_data, n_fft=512, hop_length=160))
        bandwidth = librosa.feature.spectral_bandwidth(S=spectrum, sr=sr)
        flatness = librosa.feature.spectral_flatness(S=spectrum)
        frame_rms = librosa.feature.rms(
            y=audio_data, frame_length=512, hop_length=160
        )
        mean_frame_rms = float(np.mean(frame_rms))
        energy_variation = float(np.std(frame_rms) / max(mean_frame_rms, 1e-8))
        mean_bandwidth = float(np.mean(bandwidth))
        mean_flatness = float(np.mean(flatness))

        # This conservative gate catches obvious hold tones and stationary noise.
        # It is not a replacement for a separately evaluated production VAD.
        looks_like_tone = (
            mean_bandwidth < 320
            and mean_flatness < 0.03
        )
        looks_like_stationary_noise = mean_flatness > 0.45 and energy_variation < 0.10

        if rms_db < -50 or peak < 0.006:
            quality = "too_quiet"
            message = "Very little speech was detected. Try a louder, clearer sample."
        elif looks_like_tone or looks_like_stationary_noise:
            quality = "non_speech"
            message = "This sounds like a tone or steady noise, not a clear speech sample."
        elif clipping_ratio > 0.01:
            quality = "clipping"
            message = "The recording is clipping, which can reduce detection accuracy."
        else:
            quality = "good"
            message = "Audio level is suitable for screening."

        return {
            "quality": quality,
            "message": message,
            "duration_seconds": round(len(audio_data) / sr, 2),
            "rms_db": round(rms_db, 2),
            "peak": round(peak, 4),
            "clipping_percent": round(clipping_ratio * 100, 2),
            "spectral_flatness": round(mean_flatness, 4),
            "spectral_bandwidth": round(mean_bandwidth, 2),
        }

    @staticmethod
    def _extract_dsp_features(audio_data: np.ndarray, sr: int) -> dict[str, float]:
        """Return explainable signal measurements; these are not verdicts."""

        rolloff = float(
            np.mean(
                librosa.feature.spectral_rolloff(
                    y=audio_data, sr=sr, roll_percent=0.85
                )
            )
        )
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y=audio_data)))
        centroid = float(
            np.mean(librosa.feature.spectral_centroid(y=audio_data, sr=sr))
        )

        return {
            "spectral_rolloff": round(rolloff, 2),
            "zero_crossing_rate": round(zcr, 4),
            "spectral_centroid": round(centroid, 2),
        }

    def _select_windows(self, audio_data: np.ndarray, sr: int) -> list[np.ndarray]:
        window_size = int(self.FILE_WINDOW_SECONDS * sr)
        if len(audio_data) <= window_size:
            return [audio_data]

        window_count = min(
            self.MAX_FILE_WINDOWS,
            max(2, math.ceil(len(audio_data) / window_size)),
        )
        starts = np.linspace(0, len(audio_data) - window_size, window_count, dtype=int)
        return [audio_data[start : start + window_size] for start in starts]

    def _infer_probabilities(self, windows: Iterable[np.ndarray], sr: int) -> list[float]:
        self.load()
        assert self.feature_extractor is not None
        assert self.model is not None
        assert self.fake_label_index is not None

        window_list = list(windows)
        inputs = self.feature_extractor(
            window_list,
            sampling_rate=sr,
            return_tensors="pt",
            padding=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}

        # Avoid overlapping heavyweight forwards and GPU memory spikes.
        with self._inference_lock, torch.inference_mode():
            logits = self.model(**inputs).logits
            probabilities = torch.softmax(logits, dim=-1)[:, self.fake_label_index]

        return [float(value) for value in probabilities.detach().cpu()]

    @staticmethod
    def _classification(fake_probability: float) -> tuple[str, str]:
        if fake_probability >= 0.70:
            return (
                "LIKELY SYNTHETIC",
                "This sample contains patterns associated with generated or cloned speech.",
            )
        if fake_probability >= 0.40:
            return (
                "UNCERTAIN",
                "The detector found mixed signals. Verify the speaker another way.",
            )
        return (
            "LIKELY HUMAN",
            "This sample is more consistent with human speech, but no detector is certain.",
        )

    def predict(self, audio_data: np.ndarray, sr: int = TARGET_SAMPLE_RATE) -> dict[str, Any]:
        """Run one or more model windows plus acoustic quality diagnostics."""

        audio = self._ensure_mono_float(audio_data)
        if sr != self.TARGET_SAMPLE_RATE:
            audio = librosa.resample(
                audio,
                orig_sr=sr,
                target_sr=self.TARGET_SAMPLE_RATE,
                res_type="kaiser_fast",
            )
            sr = self.TARGET_SAMPLE_RATE

        duration = len(audio) / sr
        if duration < self.MIN_AUDIO_SECONDS:
            raise AudioValidationError(
                f"Use at least {self.MIN_AUDIO_SECONDS:.0f} second of speech."
            )
        if duration > self.MAX_AUDIO_SECONDS:
            raise AudioValidationError("Audio must be 10 minutes or shorter.")

        quality = self._audio_quality(audio, sr)
        dsp_metrics = self._extract_dsp_features(audio, sr)
        if quality["quality"] in {"too_quiet", "non_speech"}:
            return {
                "status": "INSUFFICIENT AUDIO",
                "risk_score": None,
                "confidence_score": None,
                "summary": quality["message"],
                "analysis_windows": 0,
                "window_scores": [],
                "dsp_metrics": dsp_metrics,
                "audio_quality": quality,
                "disclaimer": "Screening result only; do not use as identity proof.",
            }

        windows = self._select_windows(audio, sr)
        probabilities = self._infer_probabilities(windows, sr)
        # Mean aggregation is less vulnerable to one noisy window than max scoring.
        fake_probability = float(np.mean(probabilities))
        status, summary = self._classification(fake_probability)
        confidence = max(fake_probability, 1.0 - fake_probability)

        return {
            "status": status,
            "risk_score": round(fake_probability * 100, 1),
            "confidence_score": round(confidence * 100, 1),
            "summary": summary,
            "analysis_windows": len(probabilities),
            "window_scores": [round(value * 100, 1) for value in probabilities],
            "dsp_metrics": dsp_metrics,
            "audio_quality": quality,
            "disclaimer": "Screening result only; do not use as identity proof.",
        }

    def process_file_bytes(self, file_bytes: bytes, filename: str = "audio.wav") -> dict[str, Any]:
        """Decode a recording, preserving its suffix for optional FFmpeg codecs."""

        suffix = Path(filename).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
            suffix = ".audio"

        try:
            # A named file lets librosa/audioread use FFmpeg for M4A when installed.
            try:
                audio_data, sr = librosa.load(
                    io.BytesIO(file_bytes),
                    sr=self.TARGET_SAMPLE_RATE,
                    mono=True,
                    duration=self.MAX_AUDIO_SECONDS + 1,
                )
            except Exception:
                try:
                    with tempfile.TemporaryDirectory(prefix="voiceguard-") as temp_dir:
                        audio_path = Path(temp_dir) / f"upload{suffix}"
                        audio_path.write_bytes(file_bytes)
                        audio_data, sr = librosa.load(
                            audio_path,
                            sr=self.TARGET_SAMPLE_RATE,
                            mono=True,
                            duration=self.MAX_AUDIO_SECONDS + 1,
                        )
                except Exception:
                    audio_data, sr = self._decode_with_bundled_ffmpeg(file_bytes)
        except Exception as exc:
            raise AudioDecodeError(
                "The recording could not be decoded. Try WAV, MP3, M4A, FLAC, or OGG."
            ) from exc

        return self.predict(audio_data, sr=sr)

    def _decode_with_bundled_ffmpeg(self, file_bytes: bytes) -> tuple[np.ndarray, int]:
        """Decode mobile formats through imageio-ffmpeg's bundled executable."""

        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise AudioDecodeError(
                "M4A/AAC decoding needs the imageio-ffmpeg package. Install project requirements."
            ) from exc

        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-t",
            str(self.MAX_AUDIO_SECONDS + 1),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.TARGET_SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command,
                input=file_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AudioDecodeError("The recording decoder could not process this file.") from exc

        if completed.returncode != 0 or len(completed.stdout) < 4:
            raise AudioDecodeError("The recording decoder rejected this audio format.")
        usable_bytes = len(completed.stdout) - (len(completed.stdout) % 4)
        audio = np.frombuffer(completed.stdout[:usable_bytes], dtype="<f4").copy()
        return audio, self.TARGET_SAMPLE_RATE

    def process_raw_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> dict[str, Any]:
        """Process mono signed 16-bit little-endian PCM from the browser."""

        if len(pcm_bytes) % 2:
            raise AudioValidationError("PCM payload must contain complete 16-bit samples.")
        audio_data = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        return self.predict(audio_data, sr=sample_rate)
