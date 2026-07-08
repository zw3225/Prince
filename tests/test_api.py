import json
import unittest

from backend.server import TrendRadarHandler


class FakeHandler(TrendRadarHandler):
    def __init__(self):
        pass


class ApiHelpersTest(unittest.TestCase):
    def test_json_payload_can_be_encoded(self):
        payload = {"query": "home office wellness", "markets": ["US"], "window_days": 7}
        encoded = json.dumps(payload).encode("utf-8")
        self.assertGreater(len(encoded), 0)


if __name__ == "__main__":
    unittest.main()
