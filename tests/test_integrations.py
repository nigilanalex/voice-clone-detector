import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations import AlertDispatcher, AuditStore


def incident() -> dict:
    return {
        "incident_id": "incident-1",
        "detected_at": "2026-08-28T10:00:00+00:00",
        "audio_sha256": "a" * 64,
        "ai_risk": 91.0,
        "combined_risk": 88.0,
        "risk_level": "CRITICAL",
        "scenario": "fund_transfer",
        "call_origin": "spoofed",
        "risk_flags": {"urgency": True},
        "recommended_action": "Escalate.",
    }


class AuditStoreTests(unittest.TestCase):
    def test_records_metadata_without_audio_or_filename_columns(self):
        path = Path(__file__).parent / ".audit-test.db"
        related_paths = [path, Path(f"{path}-shm"), Path(f"{path}-wal")]
        for candidate in related_paths:
            candidate.unlink(missing_ok=True)
        try:
            store = AuditStore(path)
            result = store.record(incident(), [{"channel": "email", "status": "delivered"}])
            self.assertTrue(result["recorded"])
            self.assertEqual(len(result["record_hash"]), 64)
            self.assertEqual(store.summary()["total"], 1)
            connection = sqlite3.connect(path)
            try:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(incidents)")}
            finally:
                connection.close()
            self.assertNotIn("audio", columns)
            self.assertNotIn("filename", columns)
            self.assertIn("audio_sha256", columns)
            self.assertTrue(store.verify_chain()["valid"])
        finally:
            for candidate in related_paths:
                candidate.unlink(missing_ok=True)

    def test_chain_detects_modified_incident_metadata(self):
        path = Path(__file__).parent / ".audit-tamper-test.db"
        related_paths = [path, Path(f"{path}-shm"), Path(f"{path}-wal")]
        for candidate in related_paths:
            candidate.unlink(missing_ok=True)
        try:
            store = AuditStore(path)
            store.record(incident(), [])
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE incidents SET combined_risk = 10 WHERE incident_id = 'incident-1'"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertFalse(store.verify_chain()["valid"])
        finally:
            for candidate in related_paths:
                candidate.unlink(missing_ok=True)


class AlertDispatcherTests(unittest.TestCase):
    def test_unconfigured_channels_are_reported_without_network_calls(self):
        keys = [
            "VOICEGUARD_ALERT_WEBHOOK_URL",
            "VOICEGUARD_SMTP_HOST",
            "VOICEGUARD_ALERT_FROM",
            "VOICEGUARD_ALERT_TO",
        ]
        with patch.dict(os.environ, {key: "" for key in keys}, clear=False):
            dispatcher = AlertDispatcher()
            results = dispatcher.dispatch(incident())
        self.assertEqual({item["status"] for item in results}, {"not_configured"})

    def test_webhook_rejects_unencrypted_remote_url(self):
        with patch.dict(
            os.environ,
            {"VOICEGUARD_ALERT_WEBHOOK_URL": "http://example.com/hook"},
            clear=False,
        ):
            dispatcher = AlertDispatcher()
            with self.assertRaises(ValueError):
                dispatcher._send_webhook(incident())


if __name__ == "__main__":
    unittest.main()
