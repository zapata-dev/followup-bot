"""
Cloud SQL (PostgreSQL) memory store for followup bot conversations.
Migrated from SQLite/aiosqlite to asyncpg + Cloud SQL connector.

Tables:
  - sessions: conversation history per phone
  - send_log: outbound send tracking
  - silenced_users: handoff/stop users that survive restarts
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from google.cloud.sql.connector import Connector

logger = logging.getLogger(__name__)

INSTANCE_CONNECTION_NAME = os.getenv("CLOUDSQL_CONNECTION_NAME", "")
DB_USER = os.getenv("CLOUDSQL_DB_USER", "tonobot_app")
DB_PASS = os.getenv("CLOUDSQL_DB_PASS", "")
DB_NAME = os.getenv("CLOUDSQL_DB_NAME", "followupbot")


class MemoryStore:
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._connector: Optional[Connector] = None

    async def init(self):
        """Initialize Cloud SQL connection pool and create tables."""
        if not INSTANCE_CONNECTION_NAME:
            raise RuntimeError("CLOUDSQL_CONNECTION_NAME is required for MemoryStore")

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
        logger.info("MemoryStore (Cloud SQL) initialized - DB: %s", DB_NAME)

    async def _create_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    phone TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS send_log (
                    id SERIAL PRIMARY KEY,
                    phone TEXT NOT NULL,
                    campaign_group TEXT,
                    status TEXT NOT NULL DEFAULT 'sent',
                    sent_at TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS silenced_users (
                    phone TEXT PRIMARY KEY,
                    silenced_until DOUBLE PRECISION NOT NULL,
                    reason TEXT DEFAULT 'handoff'
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_send_log_sent_at
                ON send_log(sent_at)
                """
            )

    async def silence_user(self, phone: str, until_ts: float, reason: str = "handoff"):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO silenced_users(phone, silenced_until, reason)
                VALUES($1, $2, $3)
                ON CONFLICT(phone) DO UPDATE SET
                    silenced_until = EXCLUDED.silenced_until,
                    reason = EXCLUDED.reason
                """,
                phone,
                until_ts,
                reason,
            )

    async def is_silenced(self, phone: str) -> bool:
        now = time.time()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT silenced_until FROM silenced_users WHERE phone=$1",
                phone,
            )
            if not row:
                return False
            if now >= row["silenced_until"]:
                await conn.execute("DELETE FROM silenced_users WHERE phone=$1", phone)
                return False
            return True

    async def unsilence_user(self, phone: str):
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM silenced_users WHERE phone=$1", phone)

    async def load_silenced_users(self) -> Dict[str, float]:
        now = time.time()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM silenced_users WHERE silenced_until <= $1",
                now,
            )
            rows = await conn.fetch(
                "SELECT phone, silenced_until FROM silenced_users"
            )
        return {row["phone"]: row["silenced_until"] for row in rows}

    async def get(self, phone: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT phone, state, context_json FROM sessions WHERE phone=$1",
                phone,
            )
        if not row:
            return None
        data = {
            "phone": row["phone"],
            "state": row["state"],
            "context_json": row["context_json"],
        }
        try:
            data["context"] = json.loads(data["context_json"] or "{}")
        except Exception:
            data["context"] = {}
        return data

    async def upsert(self, phone: str, state: str, context: Dict[str, Any]):
        now = datetime.now(timezone.utc).isoformat()
        ctx_json = json.dumps(context, ensure_ascii=False)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions(phone, state, context_json, updated_at)
                VALUES($1, $2, $3, $4)
                ON CONFLICT(phone) DO UPDATE SET
                    state = EXCLUDED.state,
                    context_json = EXCLUDED.context_json,
                    updated_at = EXCLUDED.updated_at
                """,
                phone,
                state,
                ctx_json,
                now,
            )

    async def log_send(
        self,
        phone: str,
        campaign_group: str,
        status: str = "sent",
        error: str = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO send_log(phone, campaign_group, status, sent_at, error)
                VALUES($1, $2, $3, $4, $5)
                """,
                phone,
                campaign_group,
                status,
                now,
                error,
            )

    async def get_velocity_stats(self) -> Dict:
        ten_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        async with self._pool.acquire() as conn:
            row_10 = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM send_log "
                "WHERE sent_at >= $1 AND status = 'sent'",
                ten_min_ago,
            )
            row_5 = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM send_log "
                "WHERE sent_at >= $1 AND status = 'sent'",
                five_min_ago,
            )
        sends_10min = row_10["cnt"]
        sends_5min = row_5["cnt"]
        msgs_per_min = round(sends_10min / 10, 2)
        avg_interval_sec = round(600 / sends_10min) if sends_10min > 0 else 0
        return {
            "sends_last_10min": sends_10min,
            "sends_last_5min": sends_5min,
            "msgs_per_min": msgs_per_min,
            "avg_interval_sec": avg_interval_sec,
        }

    async def get_send_log_today(self) -> Dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) as cnt FROM send_log "
                "WHERE sent_at >= $1 GROUP BY status",
                today,
            )
        counts = {row["status"]: row["cnt"] for row in rows}
        return {
            "total": sum(counts.values()),
            "sent": counts.get("sent", 0),
            "error": counts.get("error", 0),
            "by_status": counts,
        }

    async def get_recent_sends(self, limit: int = 25) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, phone, campaign_group, status, sent_at, error "
                "FROM send_log ORDER BY id DESC LIMIT $1",
                limit,
            )
        result = []
        for row in rows:
            phone = row["phone"] or ""
            masked = phone[:4] + "***" + phone[-4:] if len(phone) > 8 else phone
            result.append(
                {
                    "id": row["id"],
                    "phone": masked,
                    "campaign_group": row["campaign_group"] or "",
                    "status": row["status"],
                    "sent_at": row["sent_at"],
                    "error": row["error"],
                }
            )
        return result

    async def close(self):
        if self._pool:
            await self._pool.close()
        if self._connector:
            await self._connector.close_async()
        logger.info("MemoryStore closed.")
