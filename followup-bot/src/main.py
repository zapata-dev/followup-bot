"""
Followup Bot — Main FastAPI Application
Outbound WhatsApp bot for customer follow-up campaigns.

Endpoints:
  GET  /health                    → Health check + metrics
  POST /webhook                   → Evolution API webhook (incoming replies)
  GET  /admin/groups              → List campaign groups from Monday
  POST /admin/start/{group_id}    → Start sending for a campaign group
  POST /admin/pause/{group_id}    → Pause a running campaign
  GET  /admin/status              → Sender status (active campaigns, limits)
"""
import os
import json
import logging
import asyncio
import random
import time
from contextlib import asynccontextmanager
from collections import OrderedDict
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from pydantic_settings import BaseSettings

from src.memory_store import MemoryStore
from src.monday_service import monday_followup
from src.sender_service import sender, is_office_hours, get_mexico_now
from src.conversation_logic import handle_reply, detect_stop, detect_campaign_type
from src.phone_utils import normalize_phone

# ============================================================
# CONFIG
# ============================================================
class Settings(BaseSettings):
    # Required
    EVOLUTION_API_URL: str
    EVOLUTION_API_KEY: str

    # Instance
    EVO_INSTANCE: str = "Seguimiento"

    # Bot identity (used in prompts and templates)
    BOT_NAME: str = "Tu asesor"
    COMPANY_NAME: str = "La empresa"
    COMPANY_LOCATION: str = "la sucursal"
    COMPANY_PRODUCT: str = "vehículos"

    # Owner alerts
    OWNER_PHONE: Optional[str] = None

    # SQLite
    SQLITE_PATH: str = "/app/followup-bot/db/memory.db"

    # Handoff
    TEAM_NUMBERS: str = ""
    AUTO_REACTIVATE_MINUTES: int = 60
    HUMAN_DETECTION_WINDOW_SECONDS: int = 3

    # Message accumulation
    MESSAGE_ACCUMULATION_SECONDS: float = 4.0

    # Reply to contacts not found in Monday (useful for testing)
    REPLY_TO_UNKNOWN_CONTACTS: bool = True

    # Off-hours schedule messages (set to empty "" to disable)
    OFF_HOURS_MSG_SUNDAY: str = "Nuestro horario de atencion es de lunes a viernes de 9am a 6pm y sabados de 9am a 2pm."
    OFF_HOURS_MSG_SATURDAY: str = "Nuestro horario sabatino es de 9am a 2pm. Te atendemos el lunes a primera hora."
    OFF_HOURS_MSG_WEEKNIGHT: str = "Nuestro horario de atencion es de 9am a 6pm. Te atendemos a primera hora."

    class Config:
        env_file = ".env"
        extra = "ignore"


try:
    settings = Settings()
except Exception as e:
    print(f"❌ FATAL: Config error: {e}")
    raise

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FollowupBot")


class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())


# ============================================================
# GLOBAL STATE
# ============================================================
class BoundedOrderedSet:
    """Set with O(1) lookup and FIFO eviction."""
    def __init__(self, maxlen: int):
        self._data: OrderedDict = OrderedDict()
        self._maxlen = maxlen

    def add(self, key):
        if key in self._data:
            self._data.move_to_end(key)
            return
        self._data[key] = True
        while len(self._data) > self._maxlen:
            self._data.popitem(last=False)

    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)


class GlobalState:
    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.memory: Optional[MemoryStore] = None
        self.processed_ids = BoundedOrderedSet(4000)
        self.silenced_users: Dict[str, float] = {}  # phone → silenced_until_ts
        self.startup_time = time.time()

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.startup_time


state = GlobalState()


# ============================================================
# LIFECYCLE
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Followup Bot starting up...")
    
    # HTTP client
    state.http_client = httpx.AsyncClient(timeout=30.0)
    
    # SQLite
    state.memory = MemoryStore(settings.SQLITE_PATH)
    await state.memory.init()
    logger.info("✅ SQLite initialized")
    
    # OpenAI check
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("⚠️ OPENAI_API_KEY not set — AI replies will fail")
    else:
        logger.info("✅ OpenAI API key configured")

    # Monday check
    if monday_followup.is_configured():
        logger.info("✅ Monday.com configured")
    else:
        logger.warning("⚠️ Monday.com NOT configured — sender won't work")
    
    logger.info(f"✅ Evolution instance: {settings.EVO_INSTANCE}")
    logger.info(f"✅ Bot identity: {settings.BOT_NAME} @ {settings.COMPANY_NAME}")
    logger.info("🟢 Followup Bot ready!")

    yield

    # Shutdown
    logger.info("🔴 Shutting down...")
    if state.memory:
        await state.memory.close()
    if state.http_client:
        await state.http_client.aclose()


app = FastAPI(title="Followup Bot", lifespan=lifespan)


# ============================================================
# HEALTH
# ============================================================
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "bot": "followup-bot",
        "company": settings.COMPANY_NAME,
        "instance": settings.EVO_INSTANCE,
        "uptime_seconds": round(state.uptime_seconds),
        "processed_messages": len(state.processed_ids),
        "sender": sender.get_status(),
        "monday_configured": monday_followup.is_configured(),
    }


# ============================================================
# WEBHOOK — INCOMING REPLIES
# ============================================================
@app.post("/webhook")
async def webhook(request: Request):
    """
    Receives incoming WhatsApp messages from Evolution API.
    Processes replies from contacts who received outbound messages.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "invalid_json"}

    # Process in background (return 200 immediately)
    asyncio.create_task(_process_webhook_safe(body))
    return {"status": "received"}


async def _process_webhook_safe(body: dict):
    """Wrapper with error handling."""
    try:
        await _process_webhook(body)
    except Exception as e:
        logger.error(f"❌ Webhook processing error: {e}")


async def _process_webhook(body: dict):
    """Main webhook processor."""
    event = body.get("event", "")
    
    # Only process incoming messages
    if event not in ("messages.upsert", "MESSAGES_UPSERT", "messages"):
        return

    data = body.get("data", {})
    
    # Handle different Evolution API payload formats
    message = data
    if isinstance(data, list):
        message = data[0] if data else {}
    elif "message" in data:
        message = data

    # Extract key (message ID for dedup)
    key_data = message.get("key", {})
    msg_id = key_data.get("id", "")
    from_me = key_data.get("fromMe", False)
    remote_jid = key_data.get("remoteJid", "")

    # Skip our own messages
    if from_me:
        return

    # Dedup
    if not msg_id or msg_id in state.processed_ids:
        return
    state.processed_ids.add(msg_id)

    # Extract phone
    phone_raw = remote_jid.replace("@s.whatsapp.net", "").replace("@lid", "")
    phone = normalize_phone(phone_raw)
    if not phone:
        return

    # Extract text
    msg_content = message.get("message", {})
    text = (
        msg_content.get("conversation", "")
        or msg_content.get("extendedTextMessage", {}).get("text", "")
        or ""
    ).strip()

    if not text:
        # Could be audio/image — for now, skip
        logger.info(f"📎 Non-text message from {phone[:6]}***, skipping")
        return

    # Check if user is silenced (human took over)
    if phone in state.silenced_users:
        if time.time() < state.silenced_users[phone]:
            return
        else:
            del state.silenced_users[phone]

    # Determine if we're outside office hours (slower response)
    _off_hours = not is_office_hours()
    if _off_hours:
        logger.info(f"🌙 Off-hours reply from {phone[:6]}***: {text[:80]}")
    else:
        logger.info(f"📩 Reply from {phone[:6]}***: {text[:80]}")

    # Look up contact in Monday
    contact = await monday_followup.find_by_phone(phone)
    unknown_contact = False

    if not contact:
        if not settings.REPLY_TO_UNKNOWN_CONTACTS:
            logger.info(f"🔍 Phone {phone[:6]}*** not found in Monday board, ignoring")
            return
        # Reply to unknown contacts (useful for testing)
        logger.info(f"🔍 Phone {phone[:6]}*** not found in Monday board, replying anyway (REPLY_TO_UNKNOWN_CONTACTS=true)")
        contact = {
            "item_id": None,
            "name": "",
            "vehicle": "",
            "notes": "",
            "resumen": "",
            "last_contact": "",
            "group_title": "",
        }
        unknown_contact = True

    # Load conversation history from SQLite
    session = await state.memory.get(phone)
    history = []
    if session and session.get("context", {}).get("history"):
        history = session["context"]["history"]

    # Detect campaign type from Monday group
    campaign_type = detect_campaign_type(contact.get("group_title", ""))
    logger.info(f"📋 Contact in campaign type: {campaign_type} (group: {contact.get('group_title', 'N/A')})")

    # Process with AI
    result = await handle_reply(
        user_text=text,
        contact_data={
            "name": contact.get("name", ""),
            "vehicle": contact.get("vehicle", ""),
            "notes": contact.get("notes", ""),
            "resumen": contact.get("resumen", ""),
            "last_contact": contact.get("last_contact", ""),
        },
        conversation_history=history,
        campaign_type=campaign_type,
    )

    reply_text = result["reply"]
    action = result["action"]
    summary = result["summary"]

    # Off-hours: add schedule notice to reply (except STOP responses)
    # Configurable via env vars: OFF_HOURS_MSG_SUNDAY, OFF_HOURS_MSG_SATURDAY, OFF_HOURS_MSG_WEEKNIGHT
    # Set any to "" (empty) in Render to disable that specific message
    if _off_hours and action != "stop":
        now_mx = get_mexico_now()
        if now_mx.weekday() == 6:  # Sunday
            schedule_note = settings.OFF_HOURS_MSG_SUNDAY
        elif now_mx.weekday() == 5:  # Saturday after 2pm
            schedule_note = settings.OFF_HOURS_MSG_SATURDAY
        else:  # Weekday after 6pm
            schedule_note = settings.OFF_HOURS_MSG_WEEKNIGHT
        if schedule_note:
            reply_text = f"{reply_text}\n\n{schedule_note}"

    # Send reply via Evolution (slower if off-hours)
    await _send_reply(phone, reply_text, slow=_off_hours)

    # Update conversation history
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply_text})

    # Truncate history to last 10 exchanges
    if len(history) > 20:
        history = history[-20:]

    # Save to SQLite
    await state.memory.upsert(phone, action, {"history": history})

    # Skip Monday updates if contact was not found in Monday
    if unknown_contact:
        logger.info(f"📝 Unknown contact reply sent: action={action}")
        return

    # Update Monday based on action
    if action == "stop":
        await monday_followup.update_reply(contact["item_id"], "STOP", summary)
        await monday_followup.add_note(contact["item_id"], f"🛑 {summary}")

    elif action == "handoff":
        await monday_followup.update_reply(contact["item_id"], "Handoff", summary)
        await monday_followup.add_note(contact["item_id"], f"🤝 {summary}")
        # Silence bot for this user
        state.silenced_users[phone] = time.time() + (settings.AUTO_REACTIVATE_MINUTES * 60)
        # Alert owner
        if settings.OWNER_PHONE:
            alert = f"🤝 HANDOFF en seguimiento:\n{contact['name']}\nTel: {phone}\nDijo: {text[:200]}"
            await _send_reply(settings.OWNER_PHONE, alert)

    elif action == "interested":
        await monday_followup.update_reply(contact["item_id"], "Interesado", summary)
        await monday_followup.add_note(contact["item_id"], f"🟢 {summary}")
        # Also alert owner for hot leads
        if settings.OWNER_PHONE:
            alert = f"🟢 LEAD INTERESADO en seguimiento:\n{contact['name']}\nVehículo: {contact.get('vehicle', 'N/A')}\nDijo: {text[:200]}"
            await _send_reply(settings.OWNER_PHONE, alert)

    else:  # continue
        await monday_followup.update_reply(contact["item_id"], "Respondió", summary)


async def _send_reply(phone: str, text: str, slow: bool = False):
    """Send a WhatsApp message via Evolution API."""
    normalized = normalize_phone(phone)
    if not normalized:
        return

    jid = f"{normalized}@s.whatsapp.net"
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{settings.EVO_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    body = {"number": jid, "text": text}

    # Typing delay (simulate human)
    # Off-hours: respond slower (15-30s) to look more natural
    if slow:
        delay = random.uniform(15, 30)
        logger.info(f"🌙 Off-hours reply, waiting {delay:.0f}s")
    else:
        delay = random.uniform(3, 7)
    await asyncio.sleep(delay)

    try:
        client = state.http_client
        if not client:
            logger.error("❌ HTTP client not initialized")
            return
        r = await client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            logger.error(f"❌ Evolution send error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Evolution send failed: {e}")


# ============================================================
# ADMIN ENDPOINTS
# ============================================================
@app.get("/admin/groups")
async def list_groups():
    """List all campaign groups from Monday board."""
    if not monday_followup.is_configured():
        return {"error": "Monday.com not configured"}
    
    groups = await monday_followup.get_groups()
    return {"groups": groups}


@app.post("/admin/start/{group_id}")
async def start_campaign(group_id: str, force: bool = False):
    """
    Start sending messages to pending contacts in a Monday group.
    Use ?force=true to bypass the send window check (for testing).
    """
    if not monday_followup.is_configured():
        return {"error": "Monday.com not configured"}

    result = await sender.start_campaign(group_id, memory_store=state.memory, force=force)
    return result


@app.post("/admin/pause/{group_id}")
async def pause_campaign(group_id: str):
    """Pause an active campaign."""
    result = await sender.pause_campaign(group_id)
    return result


@app.get("/admin/status")
async def sender_status():
    """Get current sender status."""
    return sender.get_status()


@app.get("/admin/debug/columns")
async def debug_columns():
    """Show board structure and column IDs for debugging."""
    if not monday_followup.is_configured():
        return {"error": "Monday.com not configured"}
    structure = await monday_followup.get_board_structure()
    return structure


@app.get("/admin/debug/items/{group_id}")
async def debug_items(group_id: str):
    """Show all items in a group with their raw column values."""
    if not monday_followup.is_configured():
        return {"error": "Monday.com not configured"}
    items = await monday_followup._get_group_items(group_id)
    result = []
    for item in items:
        col_map = {cv["id"]: cv.get("text", "") for cv in item.get("column_values", [])}
        result.append({
            "item_id": item["id"],
            "name": item.get("name", ""),
            "status": col_map.get(monday_followup.status_col_id, ""),
            "phone_dedupe": col_map.get(monday_followup.dedupe_col_id, ""),
            "phone_display": col_map.get(monday_followup.phone_col_id, ""),
            "vehicle": col_map.get(monday_followup.vehicle_col_id, ""),
        })
    return {"group_id": group_id, "total_items": len(result), "items": result}
