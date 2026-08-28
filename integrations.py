"""Privacy-safe audit and external alert adapters for VoiceGuard."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _positive_int(value: str, default: int) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default


class AuditStore:
    """Store incident metadata in SQLite; audio and filenames are never stored."""

    def __init__(self, path: Path, retention_days: int = 30) -> None:
        self.path = path
        self.retention_days = retention_days

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                detected_at TEXT NOT NULL,
                audio_sha256 TEXT NOT NULL,
                ai_risk REAL,
                combined_risk REAL NOT NULL,
                risk_level TEXT NOT NULL,
                scenario TEXT NOT NULL,
                call_origin TEXT NOT NULL,
                risk_flags TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                previous_hash TEXT,
                record_hash TEXT
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(incidents)")
        }
        if "previous_hash" not in columns:
            connection.execute("ALTER TABLE incidents ADD COLUMN previous_hash TEXT")
        if "record_hash" not in columns:
            connection.execute("ALTER TABLE incidents ADD COLUMN record_hash TEXT")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._repair_legacy_chain(connection)
        connection.commit()
        return connection

    def _repair_legacy_chain(self, connection: sqlite3.Connection) -> None:
        """Give pre-chain records hashes during a one-time schema migration."""

        missing = connection.execute(
            "SELECT COUNT(*) FROM incidents WHERE record_hash IS NULL"
        ).fetchone()[0]
        if not missing:
            return
        expected_previous = "GENESIS"
        rows = connection.execute(
            """
            SELECT rowid, incident_id, detected_at, audio_sha256, ai_risk,
                   combined_risk, risk_level, scenario, call_origin,
                   risk_flags, recommended_action, delivery_status
            FROM incidents ORDER BY rowid
            """
        ).fetchall()
        for row in rows:
            incident = {
                "incident_id": row[1],
                "detected_at": row[2],
                "audio_sha256": row[3],
                "ai_risk": row[4],
                "combined_risk": row[5],
                "risk_level": row[6],
                "scenario": row[7],
                "call_origin": row[8],
                "risk_flags": json.loads(row[9]),
                "recommended_action": row[10],
            }
            deliveries = json.loads(row[11])
            record_hash = hashlib.sha256(
                f"{expected_previous}:{self._chain_payload(incident, deliveries)}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                "UPDATE incidents SET previous_hash = ?, record_hash = ? WHERE rowid = ?",
                (expected_previous, record_hash, row[0]),
            )
            expected_previous = record_hash

    @staticmethod
    def _chain_payload(
        incident: dict[str, Any], deliveries: list[dict[str, Any]]
    ) -> str:
        return json.dumps(
            {"incident": incident, "deliveries": deliveries},
            sort_keys=True,
            separators=(",", ":"),
        )

    def record(
        self, incident: dict[str, Any], deliveries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                cutoff = f"-{self.retention_days} days"
                pruned_row = connection.execute(
                    """
                    SELECT record_hash FROM incidents
                    WHERE datetime(detected_at) < datetime('now', ?)
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (cutoff,),
                ).fetchone()
                if pruned_row and pruned_row[0]:
                    connection.execute(
                        "DELETE FROM incidents WHERE datetime(detected_at) < datetime('now', ?)",
                        (cutoff,),
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO audit_metadata(key, value) VALUES ('chain_anchor', ?)",
                        (pruned_row[0],),
                    )
                previous_row = connection.execute(
                    "SELECT record_hash FROM incidents ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                anchor_row = connection.execute(
                    "SELECT value FROM audit_metadata WHERE key = 'chain_anchor'"
                ).fetchone()
                previous_hash = (
                    previous_row[0]
                    if previous_row and previous_row[0]
                    else anchor_row[0]
                    if anchor_row
                    else "GENESIS"
                )
                payload = self._chain_payload(incident, deliveries)
                record_hash = hashlib.sha256(
                    f"{previous_hash}:{payload}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, detected_at, audio_sha256, ai_risk,
                        combined_risk, risk_level, scenario, call_origin,
                        risk_flags, recommended_action, delivery_status,
                        previous_hash, record_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident["incident_id"],
                        incident["detected_at"],
                        incident["audio_sha256"],
                        incident.get("ai_risk"),
                        incident["combined_risk"],
                        incident["risk_level"],
                        incident["scenario"],
                        incident["call_origin"],
                        json.dumps(incident["risk_flags"], separators=(",", ":")),
                        incident["recommended_action"],
                        json.dumps(deliveries, separators=(",", ":")),
                        previous_hash,
                        record_hash,
                    ),
                )
            return {
                "recorded": True,
                "record_hash": record_hash,
                "previous_hash": previous_hash,
                "algorithm": "sha256_chain_v1",
            }
        except (OSError, sqlite3.Error, KeyError, TypeError):
            return {"recorded": False, "record_hash": None, "previous_hash": None}
        finally:
            if connection is not None:
                connection.close()

    def summary(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"total": 0, "by_level": {}}
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            total = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            rows = connection.execute(
                "SELECT risk_level, COUNT(*) FROM incidents GROUP BY risk_level"
            ).fetchall()
            return {"total": total, "by_level": dict(rows)}
        except (OSError, sqlite3.Error):
            return {"total": 0, "by_level": {}, "unavailable": True}
        finally:
            if connection is not None:
                connection.close()

    def verify_chain(self) -> dict[str, Any]:
        """Verify internal ordering and hashes; an external last hash detects truncation."""

        if not self.path.exists():
            return {"valid": True, "records": 0, "latest_hash": None}
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT incident_id, detected_at, audio_sha256, ai_risk,
                       combined_risk, risk_level, scenario, call_origin,
                       risk_flags, recommended_action, delivery_status,
                       previous_hash, record_hash
                FROM incidents ORDER BY rowid
                """
            ).fetchall()
            anchor_row = connection.execute(
                "SELECT value FROM audit_metadata WHERE key = 'chain_anchor'"
            ).fetchone()
            expected_previous = anchor_row[0] if anchor_row else "GENESIS"
            for row in rows:
                incident = {
                    "incident_id": row[0],
                    "detected_at": row[1],
                    "audio_sha256": row[2],
                    "ai_risk": row[3],
                    "combined_risk": row[4],
                    "risk_level": row[5],
                    "scenario": row[6],
                    "call_origin": row[7],
                    "risk_flags": json.loads(row[8]),
                    "recommended_action": row[9],
                }
                deliveries = json.loads(row[10])
                stored_previous, stored_hash = row[11], row[12]
                expected_hash = hashlib.sha256(
                    f"{expected_previous}:{self._chain_payload(incident, deliveries)}".encode("utf-8")
                ).hexdigest()
                if (
                    stored_previous != expected_previous
                    or not stored_hash
                    or not hmac.compare_digest(stored_hash, expected_hash)
                ):
                    return {
                        "valid": False,
                        "records": len(rows),
                        "latest_hash": rows[-1][12] if rows else None,
                    }
                expected_previous = stored_hash
            return {
                "valid": True,
                "records": len(rows),
                "latest_hash": expected_previous if rows else None,
            }
        except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
            return {"valid": False, "records": 0, "latest_hash": None}
        finally:
            if connection is not None:
                connection.close()


class AlertDispatcher:
    """Deliver metadata-only alerts through optional webhook and SMTP adapters."""

    def __init__(self) -> None:
        self.webhook_url = os.getenv("VOICEGUARD_ALERT_WEBHOOK_URL", "").strip()
        self.smtp_host = os.getenv("VOICEGUARD_SMTP_HOST", "").strip()
        self.smtp_port = _positive_int(os.getenv("VOICEGUARD_SMTP_PORT", "587"), 587)
        self.smtp_username = os.getenv("VOICEGUARD_SMTP_USERNAME", "").strip()
        self.smtp_password = os.getenv("VOICEGUARD_SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("VOICEGUARD_ALERT_FROM", "").strip()
        self.smtp_to = os.getenv("VOICEGUARD_ALERT_TO", "").strip()

    def status(self) -> dict[str, Any]:
        return {
            "webhook_configured": bool(self.webhook_url),
            "email_configured": bool(self.smtp_host and self.smtp_from and self.smtp_to),
            "audio_shared": False,
        }

    def _send_webhook(self, incident: dict[str, Any]) -> None:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Webhook must use HTTPS.")
        message = (
            f"VoiceGuard {incident['risk_level']} incident: "
            f"{incident['combined_risk']}% combined risk ({incident['scenario']})."
        )
        body = json.dumps({
            "event": "voiceguard.high_risk",
            "text": message,
            "content": message,
            "incident": incident,
        }).encode()
        request = Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "VoiceGuard/2"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            if response.status < 200 or response.status >= 300:
                raise OSError(f"Webhook returned HTTP {response.status}.")

    def _send_email(self, incident: dict[str, Any]) -> None:
        message = EmailMessage()
        message["Subject"] = f"VoiceGuard {incident['risk_level']} incident"
        message["From"] = self.smtp_from
        message["To"] = self.smtp_to
        message.set_content(
            "VoiceGuard detected a high-risk screening event.\n\n"
            f"Incident: {incident['incident_id']}\n"
            f"AI risk: {incident.get('ai_risk')}%\n"
            f"Combined risk: {incident['combined_risk']}%\n"
            f"Scenario: {incident['scenario']}\n"
            f"Recommended action: {incident['recommended_action']}\n\n"
            "No audio or filename is included in this notification."
        )
        context = ssl.create_default_context()
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=8) as server:
            server.starttls(context=context)
            if self.smtp_username:
                server.login(self.smtp_username, self.smtp_password)
            server.send_message(message)

    def dispatch(self, incident: dict[str, Any]) -> list[dict[str, Any]]:
        if incident["risk_level"] not in {"HIGH", "CRITICAL"}:
            return [{"channel": "external", "status": "not_triggered"}]

        results: list[dict[str, Any]] = []
        channels = (("webhook", bool(self.webhook_url), self._send_webhook), (
            "email",
            bool(self.smtp_host and self.smtp_from and self.smtp_to),
            self._send_email,
        ))
        for name, configured, sender in channels:
            if not configured:
                results.append({"channel": name, "status": "not_configured"})
                continue
            try:
                sender(incident)
                results.append({"channel": name, "status": "delivered"})
            except Exception:
                # Do not expose credentials, hosts, or provider response bodies.
                results.append({"channel": name, "status": "failed"})
        return results
