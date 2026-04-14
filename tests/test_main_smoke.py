import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "followup-bot"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


os.environ.setdefault("EVOLUTION_API_URL", "https://example.test")
os.environ.setdefault("EVOLUTION_API_KEY", "test-key")


class FakeRequest:
    def __init__(self, payload=None, should_fail=False):
        self.payload = payload or {}
        self.should_fail = should_fail

    async def json(self):
        if self.should_fail:
            raise ValueError("invalid json")
        return self.payload


class MainSmokeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = importlib.import_module("src.main")

    async def test_health_returns_expected_payload(self):
        fake_queue = type("FakeQueue", (), {"get_queue_stats": AsyncMock(return_value={"outbox_pending": 2})})()

        with patch.object(self.main.state, "monday_queue", fake_queue), \
             patch.object(self.main.state, "startup_time", 100.0), \
             patch.object(self.main.state, "processed_ids", {"a", "b"}), \
             patch.object(self.main.time, "time", return_value=145.0), \
             patch.object(self.main.sender, "get_status", return_value={"running": True}), \
             patch.object(self.main.monday_followup, "is_configured", return_value=True):
            data = await self.main.health()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["bot"], "followup-bot")
        self.assertEqual(data["processed_messages"], 2)
        self.assertEqual(data["sender"], {"running": True})
        self.assertEqual(data["monday_queue"], {"outbox_pending": 2})
        self.assertTrue(data["monday_configured"])

    async def test_webhook_rejects_invalid_json(self):
        response = await self.main.webhook(FakeRequest(should_fail=True))
        self.assertEqual(response, {"status": "invalid_json"})

    async def test_webhook_accepts_supported_event_and_schedules_background_task(self):
        created = []

        def fake_create_task(coro):
            created.append(coro)
            coro.close()
            return object()

        with patch.object(self.main.asyncio, "create_task", side_effect=fake_create_task) as create_task:
            response = await self.main.webhook(
                FakeRequest(payload={"event": "MESSAGES_UPSERT", "data": {}})
            )

        self.assertEqual(response, {"status": "received"})
        create_task.assert_called_once()
        self.assertEqual(len(created), 1)


if __name__ == "__main__":
    unittest.main()
