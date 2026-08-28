from types import SimpleNamespace
import unittest

import numpy as np

from model_engine import AudioValidationError, ModelLoadError, VoiceCloneDetector


class VoiceCloneDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = VoiceCloneDetector()

    def test_resolves_fake_label_without_assuming_index_one(self):
        config = SimpleNamespace(
            num_labels=2,
            label2id={"fake": 0, "real": 1},
            id2label={0: "fake", 1: "real"},
        )
        self.assertEqual(self.detector._resolve_fake_label_index(config), 0)

    def test_rejects_non_binary_model(self):
        config = SimpleNamespace(
            num_labels=3,
            label2id={"fake": 1},
            id2label={0: "real", 1: "fake", 2: "other"},
        )
        with self.assertRaises(ModelLoadError):
            self.detector._resolve_fake_label_index(config)

    def test_rejects_odd_pcm_payload(self):
        with self.assertRaises(AudioValidationError):
            self.detector.process_raw_pcm(b"\x00")

    def test_silence_returns_no_verdict_without_loading_model(self):
        audio = np.zeros(VoiceCloneDetector.TARGET_SAMPLE_RATE * 2, dtype=np.float32)
        result = self.detector.predict(audio)
        self.assertEqual(result["status"], "INSUFFICIENT AUDIO")
        self.assertIsNone(result["risk_score"])
        self.assertFalse(self.detector.is_ready)

    def test_pure_tone_returns_no_voice_verdict(self):
        sr = VoiceCloneDetector.TARGET_SAMPLE_RATE
        time = np.arange(sr * 2, dtype=np.float32) / sr
        audio = 0.2 * np.sin(2 * np.pi * 220 * time)
        result = self.detector.predict(audio)
        self.assertEqual(result["status"], "INSUFFICIENT AUDIO")
        self.assertEqual(result["audio_quality"]["quality"], "non_speech")
        self.assertIsNone(result["risk_score"])

    def test_classification_boundaries(self):
        self.assertEqual(self.detector._classification(0.39)[0], "LIKELY HUMAN")
        self.assertEqual(self.detector._classification(0.40)[0], "UNCERTAIN")
        self.assertEqual(self.detector._classification(0.70)[0], "LIKELY SYNTHETIC")

    def test_uses_median_score_and_reports_decision_strength(self):
        self.detector._audio_quality = lambda audio, sr: {
            "quality": "good",
            "message": "ok",
            "duration_seconds": 6.0,
        }
        self.detector._extract_dsp_features = lambda audio, sr: {}
        self.detector._select_windows = lambda audio, sr: [audio, audio, audio]
        self.detector._infer_probabilities = lambda windows, sr: [0.1, 0.8, 0.9]

        audio = np.full(
            VoiceCloneDetector.TARGET_SAMPLE_RATE * 6, 0.1, dtype=np.float32
        )
        result = self.detector.predict(audio)

        self.assertEqual(result["risk_score"], 80.0)
        self.assertEqual(result["decision_strength"], 60.0)
        self.assertEqual(result["score_spread"], 80.0)
        self.assertEqual(result["status"], "LIKELY SYNTHETIC")

    def test_long_recording_uses_bounded_windows(self):
        seconds = 90
        audio = np.ones(
            VoiceCloneDetector.TARGET_SAMPLE_RATE * seconds, dtype=np.float32
        )
        windows = self.detector._select_windows(
            audio, VoiceCloneDetector.TARGET_SAMPLE_RATE
        )
        self.assertEqual(len(windows), VoiceCloneDetector.MAX_FILE_WINDOWS)
        self.assertTrue(
            all(
                len(window)
                == VoiceCloneDetector.FILE_WINDOW_SECONDS
                * VoiceCloneDetector.TARGET_SAMPLE_RATE
                for window in windows
            )
        )

    def test_dsp_features_include_prosody_measurements(self):
        sr = VoiceCloneDetector.TARGET_SAMPLE_RATE
        timeline = np.arange(sr * 2, dtype=np.float32) / sr
        audio = 0.2 * np.sin(2 * np.pi * 180 * timeline)
        metrics = self.detector._extract_dsp_features(audio, sr)
        self.assertIn("pitch_median_hz", metrics)
        self.assertIn("pitch_variation", metrics)
        self.assertIn("pause_ratio", metrics)


if __name__ == "__main__":
    unittest.main()
