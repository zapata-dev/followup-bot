import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "followup-bot"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def acquire(self):
        return FakeAcquire(self.conn)

    async def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def connect_async(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()

    async def close_async(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.execute_calls = []
        self.fetchrow_results = []
        self.fetch_results = []
        self.fetchval_results = []
        self.executemany_calls = []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))

    async def fetchrow(self, query, *args):
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    async def fetch(self, query, *args):
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def fetchval(self, query, *args):
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return 0

    async def executemany(self, query, rows):
        self.executemany_calls.append((query, rows))


class MemoryStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.memory_store_module = importlib.import_module("src.memory_store")

    async def test_init_requires_connection_name(self):
        store = self.memory_store_module.MemoryStore()
        with patch.object(self.memory_store_module, "INSTANCE_CONNECTION_NAME", ""):
            with self.assertRaisesRegex(RuntimeError, "CLOUDSQL_CONNECTION_NAME"):
                await store.init()

    async def test_init_creates_pool_and_tables(self):
        conn = FakeConnection()
        pool = FakePool(conn)
        connector = FakeConnector()
        created_kwargs = {}

        async def fake_create_pool(**kwargs):
            created_kwargs.update(kwargs)
            return pool

        with patch.object(self.memory_store_module, "INSTANCE_CONNECTION_NAME", "project:region:instance"), \
             patch.object(self.memory_store_module, "DB_USER", "user"), \
             patch.object(self.memory_store_module, "DB_PASS", "pass"), \
             patch.object(self.memory_store_module, "DB_NAME", "followupbot"), \
             patch.object(self.memory_store_module, "Connector", return_value=connector), \
             patch.object(self.memory_store_module.asyncpg, "create_pool", side_effect=fake_create_pool):
            store = self.memory_store_module.MemoryStore()
            await store.init()
            await created_kwargs["connect"]()

        self.assertIs(store._pool, pool)
        self.assertIs(store._connector, connector)
        self.assertEqual(created_kwargs["min_size"], 1)
        self.assertEqual(created_kwargs["max_size"], 5)
        self.assertEqual(len(conn.execute_calls), 4)
        self.assertEqual(connector.calls[0][0][:2], ("project:region:instance", "asyncpg"))

    async def test_get_parses_context_json(self):
        conn = FakeConnection()
        conn.fetchrow_results.append(
            {"phone": "5215550001111", "state": "continue", "context_json": "{\"history\": [1]}"}
        )
        store = self.memory_store_module.MemoryStore()
        store._pool = FakePool(conn)

        data = await store.get("5215550001111")

        self.assertEqual(data["phone"], "5215550001111")
        self.assertEqual(data["state"], "continue")
        self.assertEqual(data["context"], {"history": [1]})

    async def test_is_silenced_removes_expired_rows(self):
        conn = FakeConnection()
        conn.fetchrow_results.append({"silenced_until": 1.0})
        store = self.memory_store_module.MemoryStore()
        store._pool = FakePool(conn)

        with patch.object(self.memory_store_module.time, "time", return_value=5.0):
            result = await store.is_silenced("5215550001111")

        self.assertFalse(result)
        self.assertIn("DELETE FROM silenced_users", conn.execute_calls[-1][0])

    async def test_close_shuts_down_pool_and_connector(self):
        store = self.memory_store_module.MemoryStore()
        pool = FakePool(FakeConnection())
        connector = FakeConnector()
        store._pool = pool
        store._connector = connector

        await store.close()

        self.assertTrue(pool.closed)
        self.assertTrue(connector.closed)


class MondayQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.monday_queue_module = importlib.import_module("src.monday_queue")

    async def test_init_requires_connection_name(self):
        queue = self.monday_queue_module.MondayQueue()
        with patch.object(self.monday_queue_module, "INSTANCE_CONNECTION_NAME", ""):
            with self.assertRaisesRegex(RuntimeError, "CLOUDSQL_CONNECTION_NAME"):
                await queue.init()

    async def test_enqueue_serializes_payload_and_sets_flush_event(self):
        conn = FakeConnection()
        queue = self.monday_queue_module.MondayQueue()
        queue._pool = FakePool(conn)

        await queue.enqueue("123", "update_status", {"status": "sent"})

        self.assertTrue(queue._flush_event.is_set())
        query, args = conn.execute_calls[-1]
        self.assertIn("INSERT INTO monday_outbox", query)
        self.assertEqual(args[0], "123")
        self.assertEqual(args[1], "update_status")
        self.assertIn("\"status\": \"sent\"", args[2])

    async def test_get_queue_stats_aggregates_counts(self):
        conn = FakeConnection()
        conn.fetchval_results.extend([3, 1, 12])
        queue = self.monday_queue_module.MondayQueue()
        queue._pool = FakePool(conn)
        queue._running = True

        stats = await queue.get_queue_stats()

        self.assertEqual(
            stats,
            {
                "outbox_pending": 3,
                "dlq_count": 1,
                "cache_contacts": 12,
                "queue_running": True,
            },
        )

    async def test_execute_operation_routes_update_reply(self):
        queue = self.monday_queue_module.MondayQueue()
        monday = SimpleNamespace()
        recorded = {}

        async def fake_update_reply(item_id, status, reply_summary, resumen):
            recorded["call"] = (item_id, status, reply_summary, resumen)

        monday.update_reply = fake_update_reply

        result = await queue._execute_operation(
            monday,
            "update_reply",
            {
                "item_id": "99",
                "status": "Respondio",
                "reply_summary": "cliente interesado",
                "resumen": "seguir mañana",
            },
        )

        self.assertTrue(result)
        self.assertEqual(
            recorded["call"],
            ("99", "Respondio", "cliente interesado", "seguir mañana"),
        )


if __name__ == "__main__":
    unittest.main()
