"""
Monday.com write queue (outbox pattern) + contact cache + dead letter queue.

Migrated from SQLite to Cloud SQL PostgreSQL so queue state survives Cloud Run
restarts and does not rely on the ephemeral filesystem.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from google.cloud.sql.connector import Connector

logger = logging.getLogger(__name__)

INSTANCE_CONNECTION_NAME = os.getenv("CLOUDSQL_CONNECTION_NAME", "")
DB_USER = os.getenv("CLOUDSQL_DB_USER", "tonobot_app")
DB_PASS = os.getenv("CLOUDSQL_DB_PASS", "")
DB_NAME = os.getenv("CLOUDSQL_DB_NAME", "followupbot")


class MondayQueue:
    """
    Persistent write queue for Monday.com updates.
    Ensures no update is lost even if Monday API is down.
    """

    def __init__(self, _db_path: Optional[str] = None):
        self._pool: Optional[asyncpg.Pool] = None
        self._connector: Optional[Connector] = None
        self._running = False
        self._process_task: Optional[asyncio.Task] = None
        self._flush_event = asyncio.Event()

        self.process_interval = int(os.getenv("MONDAY_QUEUE_INTERVAL_SECONDS", "5"))
        self.max_retries = int(os.getenv("MONDAY_QUEUE_MAX_RETRIES", "10"))
        self.batch_size = int(os.getenv("MONDAY_QUEUE_BATCH_SIZE", "20"))

    async def init(self):
        """Create tables for outbox, DLQ, and contact cache."""
        if not INSTANCE_CONNECTION_NAME:
            raise RuntimeError("CLOUDSQL_CONNECTION_NAME is required for MondayQueue")

        self._connector = Connector()

        async def _getconn() -> asyncpg.Connection:
            return await self._connector.connect_async(
                INSTANCE_CONNECTION_NAME,
                "asyncpg",
                user=DB_USER,
                password=DB_PASS,
                db=DB_NAME,
            )

        self._pool = await asyncpg.create_pool(
            min_size=1,
            max_size=5,
            connect=_getconn,
        )

        await self._create_tables()
        logger.info("Monday Queue tables initialized in Cloud SQL")

    async def _create_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monday_outbox (
                    id SERIAL PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    retries INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    error TEXT
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monday_dlq (
                    id SERIAL PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    retries INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    moved_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contact_cache (
                    phone TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    name TEXT,
                    vehicle TEXT,
                    notes TEXT,
                    resumen TEXT,
                    status TEXT,
                    group_id TEXT,
                    group_title TEXT,
                    cached_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_monday_outbox_status_id
                ON monday_outbox(status, id)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contact_cache_status_cached_at
                ON contact_cache(status, cached_at)
                """
            )

    async def enqueue(self, item_id: str, operation: str, payload: dict):
        """Queue a Monday update for background processing."""
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO monday_outbox (item_id, operation, payload_json, created_at)
                VALUES ($1, $2, $3, $4)
                """,
                item_id or "",
                operation,
                json.dumps(payload, ensure_ascii=False),
                now,
            )
        logger.info("Queued: %s for item %s", operation, item_id)
        self._flush_event.set()

    async def get_pending(self, limit: int = 20) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, item_id, operation, payload_json, retries, error
                FROM monday_outbox
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT $1
                """,
                limit,
            )
        return [
            {
                "id": row["id"],
                "item_id": row["item_id"],
                "operation": row["operation"],
                "payload": json.loads(row["payload_json"]),
                "retries": row["retries"],
                "error": row["error"],
            }
            for row in rows
        ]

    async def mark_completed(self, queue_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM monday_outbox WHERE id = $1", queue_id)

    async def mark_retry(self, queue_id: int, error: str):
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE monday_outbox
                SET retries = retries + 1, last_attempt_at = $1, error = $2
                WHERE id = $3
                """,
                now,
                (error or "")[:500],
                queue_id,
            )

    async def move_to_dlq(self, queue_id: int, item: dict):
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO monday_dlq (
                    item_id, operation, payload_json, retries, error, created_at, moved_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                item["item_id"],
                item["operation"],
                json.dumps(item["payload"], ensure_ascii=False),
                item["retries"],
                item.get("error", ""),
                now,
                now,
            )
            await conn.execute("DELETE FROM monday_outbox WHERE id = $1", queue_id)
        logger.error(
            "DLQ: %s for item %s moved after %s retries. Error: %s",
            item["operation"],
            item["item_id"],
            item["retries"],
            item.get("error", "unknown"),
        )

    async def get_dlq_items(self, limit: int = 50) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, item_id, operation, payload_json, retries, error, moved_at
                FROM monday_dlq
                ORDER BY id DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            {
                "id": row["id"],
                "item_id": row["item_id"],
                "operation": row["operation"],
                "payload": json.loads(row["payload_json"]),
                "retries": row["retries"],
                "error": row["error"],
                "moved_at": row["moved_at"],
            }
            for row in rows
        ]

    async def retry_dlq_item(self, dlq_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT item_id, operation, payload_json FROM monday_dlq WHERE id = $1",
                dlq_id,
            )
            if not row:
                return False

            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT INTO monday_outbox (
                    item_id, operation, payload_json, retries, status, created_at
                )
                VALUES ($1, $2, $3, 0, 'pending', $4)
                """,
                row["item_id"],
                row["operation"],
                row["payload_json"],
                now,
            )
            await conn.execute("DELETE FROM monday_dlq WHERE id = $1", dlq_id)
        logger.info("DLQ item %s moved back to outbox for retry", dlq_id)
        return True

    async def get_queue_stats(self) -> Dict:
        async with self._pool.acquire() as conn:
            pending_count = await conn.fetchval(
                "SELECT COUNT(*) FROM monday_outbox WHERE status = 'pending'"
            )
            dlq_count = await conn.fetchval("SELECT COUNT(*) FROM monday_dlq")
            cache_count = await conn.fetchval("SELECT COUNT(*) FROM contact_cache")
        return {
            "outbox_pending": pending_count or 0,
            "dlq_count": dlq_count or 0,
            "cache_contacts": cache_count or 0,
            "queue_running": self._running,
        }

    async def get_funnel_stats(self) -> Dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt
                FROM contact_cache
                GROUP BY status
                ORDER BY COUNT(*) DESC
                """
            )
        return {(row["status"] or "Sin estado"): row["cnt"] for row in rows}

    async def get_pending_contacts(self, limit: int = 20) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT phone, name, vehicle, status, group_title
                FROM contact_cache
                WHERE status IN ('Pendiente', 'En Cola')
                ORDER BY cached_at ASC
                LIMIT $1
                """,
                limit,
            )
        result = []
        for i, row in enumerate(rows):
            phone = row["phone"] or ""
            masked = phone[:4] + "***" + phone[-4:] if len(phone) > 8 else phone
            result.append(
                {
                    "position": i + 1,
                    "phone": masked,
                    "name": row["name"] or "Contacto",
                    "vehicle": row["vehicle"] or "",
                    "status": row["status"] or "Pendiente",
                    "campaign": row["group_title"] or "",
                }
            )
        return result

    async def cache_contact(self, phone: str, contact: dict):
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO contact_cache (
                    phone, item_id, name, vehicle, notes, resumen, status,
                    group_id, group_title, cached_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT(phone) DO UPDATE SET
                    item_id = EXCLUDED.item_id,
                    name = EXCLUDED.name,
                    vehicle = EXCLUDED.vehicle,
                    notes = EXCLUDED.notes,
                    resumen = EXCLUDED.resumen,
                    status = EXCLUDED.status,
                    group_id = EXCLUDED.group_id,
                    group_title = EXCLUDED.group_title,
                    cached_at = EXCLUDED.cached_at
                """,
                phone,
                contact.get("item_id", ""),
                contact.get("name", ""),
                contact.get("vehicle", ""),
                contact.get("notes", ""),
                contact.get("resumen", ""),
                contact.get("status", ""),
                contact.get("group_id", ""),
                contact.get("group_title", ""),
                now,
            )

    async def cache_contacts_bulk(self, contacts: List[dict]):
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                c.get("phone", ""),
                c.get("item_id", ""),
                c.get("name", ""),
                c.get("vehicle", ""),
                c.get("notes", ""),
                c.get("resumen", ""),
                c.get("status", ""),
                c.get("group_id", ""),
                c.get("group_title", ""),
                now,
            )
            for c in contacts
            if c.get("phone", "")
        ]
        if not rows:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO contact_cache (
                    phone, item_id, name, vehicle, notes, resumen, status,
                    group_id, group_title, cached_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT(phone) DO UPDATE SET
                    item_id = EXCLUDED.item_id,
                    name = EXCLUDED.name,
                    vehicle = EXCLUDED.vehicle,
                    notes = EXCLUDED.notes,
                    resumen = EXCLUDED.resumen,
                    status = EXCLUDED.status,
                    group_id = EXCLUDED.group_id,
                    group_title = EXCLUDED.group_title,
                    cached_at = EXCLUDED.cached_at
                """,
                rows,
            )
        logger.info("Cached %s contacts", len(rows))

    async def get_cached_contact(self, phone: str) -> Optional[Dict]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT item_id, name, vehicle, notes, resumen, status, group_id, group_title
                FROM contact_cache
                WHERE phone = $1
                """,
                phone,
            )
            if row:
                return {
                    "item_id": row["item_id"],
                    "name": row["name"],
                    "vehicle": row["vehicle"],
                    "notes": row["notes"],
                    "resumen": row["resumen"],
                    "status": row["status"],
                    "group_id": row["group_id"],
                    "group_title": row["group_title"],
                    "phone": phone,
                    "source": "cache",
                }

            if len(phone) == 13 and phone.startswith("521"):
                ten_digits = phone[3:]
                row = await conn.fetchrow(
                    """
                    SELECT phone, item_id, name, vehicle, notes, resumen, status, group_id, group_title
                    FROM contact_cache
                    WHERE phone LIKE $1
                    LIMIT 1
                    """,
                    f"%{ten_digits}",
                )
                if row:
                    return {
                        "item_id": row["item_id"],
                        "name": row["name"],
                        "vehicle": row["vehicle"],
                        "notes": row["notes"],
                        "resumen": row["resumen"],
                        "status": row["status"],
                        "group_id": row["group_id"],
                        "group_title": row["group_title"],
                        "phone": row["phone"],
                        "source": "cache_fuzzy",
                    }

        return None

    async def update_cached_contact_fields(self, phone: str, fields: dict):
        if not fields:
            return
        set_clauses = []
        values: List[Any] = []
        idx = 1
        for key in ("name", "vehicle", "notes", "resumen", "status", "group_id", "group_title"):
            if key in fields:
                set_clauses.append(f"{key} = ${idx}")
                values.append(fields[key])
                idx += 1
        if not set_clauses:
            return
        set_clauses.append(f"cached_at = ${idx}")
        values.append(datetime.now(timezone.utc).isoformat())
        idx += 1
        values.append(phone)
        query = f"UPDATE contact_cache SET {', '.join(set_clauses)} WHERE phone = ${idx}"
        async with self._pool.acquire() as conn:
            await conn.execute(query, *values)

    def start_processor(self, monday_service, alert_callback=None):
        self._running = True
        self._process_task = asyncio.create_task(
            self._process_loop(monday_service, alert_callback)
        )
        logger.info("Queue processor started (interval: %ss)", self.process_interval)

    def stop_processor(self):
        self._running = False
        if self._process_task:
            self._process_task.cancel()

    async def _process_loop(self, monday_service, alert_callback=None):
        while self._running:
            try:
                items = await self.get_pending(limit=self.batch_size)
                if items:
                    logger.info("Processing %s queued Monday updates...", len(items))

                for item in items:
                    if not self._running:
                        break

                    try:
                        success = await self._execute_operation(
                            monday_service, item["operation"], item["payload"]
                        )
                        if success:
                            await self.mark_completed(item["id"])
                        else:
                            await self.mark_retry(item["id"], "Operation returned False")
                            item["error"] = "Operation returned False"
                            item["retries"] += 1
                            if item["retries"] >= self.max_retries:
                                await self.move_to_dlq(item["id"], item)
                                if alert_callback:
                                    await alert_callback(item)
                    except Exception as e:
                        error_msg = str(e)[:500]
                        await self.mark_retry(item["id"], error_msg)
                        item["error"] = error_msg
                        item["retries"] += 1
                        logger.error("Queue item %s failed: %s", item["id"], error_msg)
                        if item["retries"] >= self.max_retries:
                            await self.move_to_dlq(item["id"], item)
                            if alert_callback:
                                await alert_callback(item)

                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Queue processor error: %s", e)

            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=self.process_interval)
            except asyncio.TimeoutError:
                pass
            self._flush_event.clear()

    async def _execute_operation(self, monday, operation: str, payload: dict) -> bool:
        item_id = payload.get("item_id", "")

        if operation == "update_status":
            await monday.update_status(
                item_id,
                payload["status"],
                payload.get("extra_cols"),
            )
            return True
        if operation == "update_reply":
            await monday.update_reply(
                item_id,
                payload["status"],
                payload.get("reply_summary", ""),
                payload.get("resumen", ""),
            )
            return True
        if operation == "add_note":
            await monday.add_note(item_id, payload["body"])
            return True
        if operation == "mark_error":
            await monday.mark_error(item_id, payload.get("error", ""))
            return True
        if operation == "update_send_date":
            await monday.update_send_date(
                item_id,
                payload.get("normalized_phone", ""),
            )
            return True
        if operation == "create_item":
            await monday.create_item_in_group(
                payload["group_id"],
                payload["name"],
                payload.get("column_values", {}),
            )
            return True

        logger.warning("Unknown queue operation: %s", operation)
        return False

    async def close(self):
        self.stop_processor()
        if self._pool:
            await self._pool.close()
            self._pool = None
        if self._connector:
            await self._connector.close_async()
            self._connector = None
