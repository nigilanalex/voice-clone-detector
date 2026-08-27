import unittest

from app import ALLOWED_EXTENSIONS, websocket_origin_allowed


class DummyWebSocket:
    def __init__(self, headers):
        self.headers = headers


class AppSecurityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
