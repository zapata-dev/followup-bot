"""
SQLite memory store for followup bot conversations.
Stores conversation history per phone for GPT context.
Also persists silenced users so handoffs survive restarts.
"""
import aiosqlite
import json
import os
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SQLITE_PATH", "/app/followup-bot/db/memory.db")


class MemoryStore:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        # WAL mode for better concurrent access
        await self._conn.execute("PRAGMA journal_mode=WAL")

        # Conversations table (same as Tono-Bot)
        await self._conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            context_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # Send queue tracking (backup to Monday, for resilience)
        await self._conn.execute("""
        CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            campaign_group TEXT,
            status TEXT NOT NULL DEFAULT 'sent',
            sent_at TEXT NOT NULL,
            error TEXT
        )
        """)

        # Silenced users — persists across restarts
        await self._conn.execute("""
        CREATE TABLE IF NOT EXISTS silenced_users (
            phone TEXT PRIMARY KEY,
            silenced_until REAL NOT NULL,
            reason TEXT DEFAULT 'handoff'
        )
        """)

        await self._conn.commit()

    # ── Silence management ──

    async def silence_user(self, phone: str, until_ts: float, reason: str = "handoff"):
        """Silence a user until the given timestamp."""
        await self._conn.execute("""
        INSERT INTO silenced_users(phone, silenced_until, reason)
        VALUES(?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            silenced_until=excluded.silenced_until,
            reason=excluded.reason
        """, (phone, until_ts, reason))
        await self._conn.commit()

    async def is_silenced(self, phone: str) -> bool:
        """Check if a user is currently silenced. Auto-cleans expired entries."""
        now = time.time()
        cursor = await self._conn.execute(
            "SELECT silenced_until FROM silenced_users WHERE phone=?", (phone,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        if now >= row[0]:
            # Expired — clean up
            await self._conn.execute("DELETE FROM silenced_users WHERE phone=?", (phone,))
            await self._conn.commit()
            return False
        return True

    async def unsilence_user(self, phone: str):
        """Remove silence for a user."""
        await self._conn.execute("DELETE FROM silenced_users WHERE phone=?", (phone,))
        await self._conn.commit()

    async def load_silenced_users(self) -> Dict[str, float]:
        """Load all active silenced users (for in-memory cache on startup)."""
        now = time.time()
        # Clean expired entries
        await self._conn.execute("DELETE FROM silenced_users WHERE silenced_until <= ?", (now,))
        await self._conn.commit()
        # Load active ones
        cursor = await self._conn.execute("SELECT phone, silenced_until FROM silenced_users")
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    # ── Conversation management ──

    async def get(self, phone: str) -> Optional[Dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT phone, state, context_json FROM sessions WHERE phone=?", (phone,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = {"phone": row[0], "state": row[1], "context_json": row[2]}
        try:
            data["context"] = json.loads(data["context_json"] or "{}")
        except Exception:
            data["context"] = {}
        return data

    async def upsert(self, phone: str, state: str, context: Dict[str, Any]):
        now = datetime.utcnow().isoformat()
        ctx_json = json.dumps(context, ensure_ascii=False)
        await self._conn.execute("""
        INSERT INTO sessions(phone, state, context_json, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            state=excluded.state,
            context_json=excluded.context_json,
            updated_at=excluded.updated_at
        """, (phone, state, ctx_json, now))
        await self._conn.commit()

    async def log_send(self, phone: str, campaign_group: str, status: str = "sent", error: str = None):
        now = datetime.utcnow().isoformat()
        await self._conn.execute("""
        INSERT INTO send_log(phone, campaign_group, status, sent_at, error)
        VALUES(?, ?, ?, ?, ?)
        """, (phone, campaign_group, status, now, error))
        await self._conn.commit()

    async def get_velocity_stats(self) -> Dict:
        """Calculate actual send velocity from recent send_log entries."""
        from datetime import timedelta
        ten_min_ago = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE sent_at >= ? AND status = 'sent'",
            (ten_min_ago,)
        )
        sends_10min = (await cursor.fetchone())[0]
        cursor2 = await self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE sent_at >= ? AND status = 'sent'",
            (five_min_ago,)
        )
        sends_5min = (await cursor2.fetchone())[0]
        msgs_per_min = round(sends_10min / 10, 2)
        avg_interval_sec = round(600 / sends_10min) if sends_10min > 0 else 0
        return {
            "sends_last_10min": sends_10min,
            "sends_last_5min": sends_5min,
            "msgs_per_min": msgs_per_min,
            "avg_interval_sec": avg_interval_sec,
        }

    async def get_send_log_today(self) -> Dict:
        """Get send log counts for today (UTC date prefix match)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cursor = await self._conn.execute(
            "SELECT status, COUNT(*) FROM send_log WHERE sent_at >= ? GROUP BY status",
            (today,)
        )
        rows = await cursor.fetchall()
        counts = {row[0]: row[1] for row in rows}
        return {
            "total": sum(counts.values()),
            "sent": counts.get("sent", 0),
            "error": counts.get("error", 0),
            "by_status": counts,
        }

    async def get_recent_sends(self, limit: int = 25) -> List[Dict]:
        """Get the most recent send log entries."""
        cursor = await self._conn.execute(
            "SELECT id, phone, campaign_group, status, sent_at, error "
            "FROM send_log ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            phone = row[1] or ""
            masked = phone[:4] + "***" + phone[-4:] if len(phone) > 8 else phone
            result.append({
                "id": row[0],
                "phone": masked,
                "campaign_group": row[2] or "",
                "status": row[3],
                "sent_at": row[4],
                "error": row[5],
            })
        return result

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
