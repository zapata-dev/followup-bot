"""
SQLite memory store for followup bot conversations.
Stores conversation history per phone for GPT context.
"""
import aiosqlite
import json
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any

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
        
        await self._conn.commit()

    async def get(self, phone: str) -> Optional[Dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cursor = await self._conn.execute(
            "SELECT phone, state, context_json FROM sessions WHERE phone=?", (phone,)
        )
        row = await cursor.fetchone()
        self._conn.row_factory = None
        if not row:
            return None
        data = dict(row)
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

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
