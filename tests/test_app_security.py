import unittest

from app import (
    ALLOWED_EXTENSIONS,
    SlidingWindowRateLimiter,
    assess_impersonation_risk,
    client_key,
    websocket_origin_allowed,
)


class DummyWebSocket:
    def __init__(self, headers):
        self.headers = headers


class AppSecurityTests(unittest.TestCase):
    def test_context_policy_escalates_high_impact_synthetic_call(self):
        result = assess_impersonation_risk(
            90,
            scenario="fund_transfer",
            call_origin="spoofed",
            urgency=True,
            sensitive_request=True,
            new_beneficiary=True,
            speaker_similarity=40,
        )
        self.assertEqual(result["risk_level"], "CRITICAL")
        self.assertTrue(result["simulation"])
        self.assertGreaterEqual(result["combined_risk_score"], 70)

    def test_context_policy_keeps_low_risk_call_low(self):
        result = assess_impersonation_risk(10, call_origin="known")
        self.assertEqual(result["risk_level"], "LOW")

    def test_browser_websocket_accepts_same_origin(self):
        websocket = DummyWebSocket(
            {"origin": "https://voiceguard.example", "host": "voiceguard.example"}
        )
        self.assertTrue(websocket_origin_allowed(websocket))

    def test_browser_websocket_rejects_cross_origin(self):
        websocket = DummyWebSocket(
            {"origin": "https://attacker.example", "host": "voiceguard.example"}
        )
        self.assertFalse(websocket_origin_allowed(websocket))

    def test_native_websocket_without_origin_is_allowed(self):
        self.assertTrue(websocket_origin_allowed(DummyWebSocket({"host": "localhost"})))

    def test_browser_recording_format_is_supported(self):
        self.assertIn(".webm", ALLOWED_EXTENSIONS)

    def test_forwarded_client_key_uses_first_address(self):
        self.assertEqual(
            client_key({"x-forwarded-for": "203.0.113.4, 10.0.0.2"}, "127.0.0.1"),
            "203.0.113.4",
        )


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_requests_over_limit(self):
        limiter = SlidingWindowRateLimiter()
        self.assertTrue(await limiter.allow("client", 2, 60))
        self.assertTrue(await limiter.allow("client", 2, 60))
        self.assertFalse(await limiter.allow("client", 2, 60))


if __name__ == "__main__":
    unittest.main()
