"""
Monday.com Write Queue (Outbox Pattern) + Contact Cache + Dead Letter Queue.

Propuestas 1, 2, y 4 del plan de integración empresarial:
1. Outbox Queue: Cola persistente de escrituras a Monday (SQLite)
2. Contact Cache: Caché local de contactos para lecturas rápidas
4. Dead Letter Queue: Registros que fallaron permanentemente + alertas

Flujo:
  Webhook → queue_update() → SQLite outbox
  Background task → process_queue() → Monday API
  Si falla 10+ veces → move to DLQ → alert owner
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

import aiosqlite
import pytz

logger = logging.getLogger(__name__)


class MondayQueue:
    """
    Persistent write queue for Monday.com updates.
    Ensures no update is lost even if Monday API is down.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._running = False
        self._process_task: Optional[asyncio.Task] = None

        # Config
        self.process_interval = int(os.getenv("MONDAY_QUEUE_INTERVAL_SECONDS", "10"))
        self.max_retries = int(os.getenv("MONDAY_QUEUE_MAX_RETRIES", "10"))
        self.batch_size = int(os.getenv("MONDAY_QUEUE_BATCH_SIZE", "20"))

    async def init(self):
        """Create tables for outbox and dead letter queue."""
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")

        # Outbox: pending writes to Monday
        await self._conn.execute("""
        CREATE TABLE IF NOT EXISTS monday_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            retries INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            last_attempt_at TEXT,
            error TEXT
        )
        """)

        # Dead Letter Queue: permanently failed writes
        await self._conn.execute("""
        CREATE TABLE IF NOT EXISTS monday_dlq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            retries INTEGER NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            moved_at TEXT NOT NULL
        )
        """)

        # Contact cache: local copy of Monday contacts
        await self._conn.execute("""
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
        """)

        await self._conn.commit()
        logger.info("✅ Monday Queue tables initialized")

    # ──────────────────────────────────────────────────────────
    # OUTBOX: Queue a write operation
    # ──────────────────────────────────────────────────────────
    async def enqueue(self, item_id: str, operation: str, payload: dict):
        """
        Queue a Monday update for background processing.

        Operations: 'update_status', 'update_reply', 'add_note',
                    'mark_error', 'update_send_date', 'create_item'
        """
        if not self._conn:
            logger.error("❌ Queue not initialized, cannot enqueue")
            return

        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        INSERT INTO monday_outbox (item_id, operation, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """, (
            item_id or "",
            operation,
            json.dumps(payload, ensure_ascii=False),
            now,
        ))
        await self._conn.commit()
        logger.info(f"📥 Queued: {operation} for item {item_id}")

    async def get_pending(self, limit: int = 20) -> List[Dict]:
        """Get pending items from outbox, oldest first."""
        cursor = await self._conn.execute("""
        SELECT id, item_id, operation, payload_json, retries, error
        FROM monday_outbox
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "item_id": r[1], "operation": r[2],
                "payload": json.loads(r[3]), "retries": r[4], "error": r[5],
            }
            for r in rows
        ]

    async def mark_completed(self, queue_id: int):
        """Mark a queued item as completed (will be cleaned up later)."""
        await self._conn.execute(
            "DELETE FROM monday_outbox WHERE id = ?", (queue_id,)
        )
        await self._conn.commit()

    async def mark_retry(self, queue_id: int, error: str):
        """Increment retry count and record error."""
        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        UPDATE monday_outbox
        SET retries = retries + 1, last_attempt_at = ?, error = ?
        WHERE id = ?
        """, (now, error[:500], queue_id))
        await self._conn.commit()

    async def move_to_dlq(self, queue_id: int, item: dict):
        """Move a permanently failed item to the Dead Letter Queue."""
        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        INSERT INTO monday_dlq (item_id, operation, payload_json, retries, error, created_at, moved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            item["item_id"], item["operation"],
            json.dumps(item["payload"], ensure_ascii=False),
            item["retries"], item.get("error", ""),
            now, now,
        ))
        await self._conn.execute(
            "DELETE FROM monday_outbox WHERE id = ?", (queue_id,)
        )
        await self._conn.commit()
        logger.error(
            f"💀 DLQ: {item['operation']} for item {item['item_id']} "
            f"moved to dead letter queue after {item['retries']} retries. "
            f"Error: {item.get('error', 'unknown')}"
        )

    # ──────────────────────────────────────────────────────────
    # DLQ: Dead Letter Queue queries
    # ──────────────────────────────────────────────────────────
    async def get_dlq_items(self, limit: int = 50) -> List[Dict]:
        """Get items from the dead letter queue."""
        cursor = await self._conn.execute("""
        SELECT id, item_id, operation, payload_json, retries, error, moved_at
        FROM monday_dlq
        ORDER BY id DESC
        LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "item_id": r[1], "operation": r[2],
                "payload": json.loads(r[3]), "retries": r[4],
                "error": r[5], "moved_at": r[6],
            }
            for r in rows
        ]

    async def retry_dlq_item(self, dlq_id: int) -> bool:
        """Move a DLQ item back to the outbox for retry."""
        cursor = await self._conn.execute(
            "SELECT item_id, operation, payload_json FROM monday_dlq WHERE id = ?",
            (dlq_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False

        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        INSERT INTO monday_outbox (item_id, operation, payload_json, retries, status, created_at)
        VALUES (?, ?, ?, 0, 'pending', ?)
        """, (row[0], row[1], row[2], now))
        await self._conn.execute("DELETE FROM monday_dlq WHERE id = ?", (dlq_id,))
        await self._conn.commit()
        logger.info(f"🔄 DLQ item {dlq_id} moved back to outbox for retry")
        return True

    async def get_queue_stats(self) -> Dict:
        """Get queue statistics for monitoring."""
        pending = await self._conn.execute(
            "SELECT COUNT(*) FROM monday_outbox WHERE status = 'pending'"
        )
        pending_count = (await pending.fetchone())[0]

        dlq = await self._conn.execute("SELECT COUNT(*) FROM monday_dlq")
        dlq_count = (await dlq.fetchone())[0]

        cache = await self._conn.execute("SELECT COUNT(*) FROM contact_cache")
        cache_count = (await cache.fetchone())[0]

        return {
            "outbox_pending": pending_count,
            "dlq_count": dlq_count,
            "cache_contacts": cache_count,
            "queue_running": self._running,
        }

    # ──────────────────────────────────────────────────────────
    # CONTACT CACHE: Local copy of Monday contacts
    # ──────────────────────────────────────────────────────────
    async def cache_contact(self, phone: str, contact: dict):
        """Upsert a contact into the local cache."""
        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        INSERT INTO contact_cache (phone, item_id, name, vehicle, notes, resumen, status, group_id, group_title, cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            item_id=excluded.item_id,
            name=excluded.name,
            vehicle=excluded.vehicle,
            notes=excluded.notes,
            resumen=excluded.resumen,
            status=excluded.status,
            group_id=excluded.group_id,
            group_title=excluded.group_title,
            cached_at=excluded.cached_at
        """, (
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
        ))
        await self._conn.commit()

    async def cache_contacts_bulk(self, contacts: List[dict]):
        """Bulk upsert contacts into cache (for periodic sync)."""
        now = datetime.utcnow().isoformat()
        for c in contacts:
            phone = c.get("phone", "")
            if not phone:
                continue
            await self._conn.execute("""
            INSERT INTO contact_cache (phone, item_id, name, vehicle, notes, resumen, status, group_id, group_title, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                item_id=excluded.item_id,
                name=excluded.name,
                vehicle=excluded.vehicle,
                notes=excluded.notes,
                resumen=excluded.resumen,
                status=excluded.status,
                group_id=excluded.group_id,
                group_title=excluded.group_title,
                cached_at=excluded.cached_at
            """, (
                phone,
                c.get("item_id", ""),
                c.get("name", ""),
                c.get("vehicle", ""),
                c.get("notes", ""),
                c.get("resumen", ""),
                c.get("status", ""),
                c.get("group_id", ""),
                c.get("group_title", ""),
                now,
            ))
        await self._conn.commit()
        logger.info(f"📦 Cached {len(contacts)} contacts")

    async def get_cached_contact(self, phone: str) -> Optional[Dict]:
        """Look up a contact in the local cache by phone."""
        # Try exact match first
        cursor = await self._conn.execute(
            "SELECT item_id, name, vehicle, notes, resumen, status, group_id, group_title "
            "FROM contact_cache WHERE phone = ?",
            (phone,)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "item_id": row[0], "name": row[1], "vehicle": row[2],
                "notes": row[3], "resumen": row[4], "status": row[5],
                "group_id": row[6], "group_title": row[7],
                "phone": phone, "source": "cache",
            }

        # Try phone variants (last 10 digits match)
        if len(phone) == 13 and phone.startswith("521"):
            ten_digits = phone[3:]
            cursor = await self._conn.execute(
                "SELECT phone, item_id, name, vehicle, notes, resumen, status, group_id, group_title "
                "FROM contact_cache WHERE phone LIKE ?",
                (f"%{ten_digits}",)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "item_id": row[1], "name": row[2], "vehicle": row[3],
                    "notes": row[4], "resumen": row[5], "status": row[6],
                    "group_id": row[7], "group_title": row[8],
                    "phone": row[0], "source": "cache_fuzzy",
                }

        return None

    async def update_cached_contact_fields(self, phone: str, fields: dict):
        """Update specific fields in the cache (e.g., after Monday update)."""
        if not fields:
            return
        set_clauses = []
        values = []
        for key in ("name", "vehicle", "notes", "resumen", "status", "group_id", "group_title"):
            if key in fields:
                set_clauses.append(f"{key} = ?")
                values.append(fields[key])
        if not set_clauses:
            return
        set_clauses.append("cached_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(phone)
        await self._conn.execute(
            f"UPDATE contact_cache SET {', '.join(set_clauses)} WHERE phone = ?",
            values,
        )
        await self._conn.commit()

    # ──────────────────────────────────────────────────────────
    # BACKGROUND PROCESSOR
    # ──────────────────────────────────────────────────────────
    def start_processor(self, monday_service, alert_callback=None):
        """Start the background queue processor."""
        self._running = True
        self._process_task = asyncio.create_task(
            self._process_loop(monday_service, alert_callback)
        )
        logger.info(f"🔄 Queue processor started (interval: {self.process_interval}s)")

    def stop_processor(self):
        """Stop the background processor."""
        self._running = False
        if self._process_task:
            self._process_task.cancel()

    async def _process_loop(self, monday_service, alert_callback=None):
        """Background loop that processes queued Monday updates."""
        while self._running:
            try:
                items = await self.get_pending(limit=self.batch_size)
                if items:
                    logger.info(f"📤 Processing {len(items)} queued Monday updates...")

                for item in items:
                    if not self._running:
                        break

                    try:
                        success = await self._execute_operation(
                            monday_service, item["operation"], item["payload"]
                        )
                        if success:
                            await self.mark_completed(item["id"])
                            logger.debug(f"✅ Queue item {item['id']} completed")
                        else:
                            await self.mark_retry(item["id"], "Operation returned False")
                            if item["retries"] + 1 >= self.max_retries:
                                await self.move_to_dlq(item["id"], item)
                                if alert_callback:
                                    await alert_callback(item)
                    except Exception as e:
                        error_msg = str(e)[:500]
                        await self.mark_retry(item["id"], error_msg)
                        logger.error(f"❌ Queue item {item['id']} failed: {error_msg}")
                        if item["retries"] + 1 >= self.max_retries:
                            await self.move_to_dlq(item["id"], item)
                            if alert_callback:
                                await alert_callback(item)

                    # Small delay between operations to avoid rate limits
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Queue processor error: {e}")

            await asyncio.sleep(self.process_interval)

    async def _execute_operation(self, monday, operation: str, payload: dict) -> bool:
        """Execute a single Monday operation. Returns True on success."""
        item_id = payload.get("item_id", "")

        if operation == "update_status":
            await monday.update_status(
                item_id,
                payload["status"],
                payload.get("extra_cols"),
            )
            return True

        elif operation == "update_reply":
            await monday.update_reply(
                item_id,
                payload["status"],
                payload.get("reply_summary", ""),
                payload.get("resumen", ""),
            )
            return True

        elif operation == "add_note":
            await monday.add_note(item_id, payload["body"])
            return True

        elif operation == "mark_error":
            await monday.mark_error(item_id, payload.get("error", ""))
            return True

        elif operation == "update_send_date":
            await monday.update_send_date(
                item_id,
                payload.get("normalized_phone", ""),
            )
            return True

        elif operation == "create_item":
            await monday.create_item_in_group(
                payload["group_id"],
                payload["name"],
                payload.get("column_values", {}),
            )
            return True

        else:
            logger.warning(f"⚠️ Unknown queue operation: {operation}")
            return False

    async def close(self):
        """Shut down processor and close DB."""
        self.stop_processor()
        if self._conn:
            await self._conn.close()
            self._conn = None
