"""
Followup Bot — Main FastAPI Application
Outbound WhatsApp bot for customer follow-up campaigns.

Endpoints:
  GET  /health                    → Health check + metrics
  POST /webhook                   → Evolution API webhook (incoming replies)
  GET  /admin                     → Admin dashboard UI
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
from fastapi.responses import HTMLResponse
from pydantic_settings import BaseSettings

from src.memory_store import MemoryStore
from src.monday_service import monday_followup
from src.monday_queue import MondayQueue
from src.sender_service import sender, is_office_hours, get_mexico_now, get_today_schedule
from src.conversation_logic import handle_reply, detect_stop, detect_campaign_type, generate_conversation_resumen, detect_location
from src.phone_utils import normalize_phone
from src.dashboard import DASHBOARD_HTML
from src.media_processor import process_media_message

# ============================================================
# BOT / AUTO-RESPONDER DETECTION
# ============================================================
# Phrases that indicate the message is from another business's auto-responder
_BOT_INDICATORS = [
    "bienvenido a", "welcome to", "gracias por comunicarte con",
    "nuestro horario de atencion", "en un momento te atendemos",
    "respuesta automatica", "auto-reply", "fuera de horario",
    "menu principal", "selecciona una opcion", "presiona 1",
    "catalogo disponible", "visita nuestra tienda", "conoce nuestros",
    "go on", "chatea con nosotros", "powered by",
]


def _is_auto_responder(text: str, response_time_ms: int = 0) -> bool:
    """
    Detect if a message is likely from another bot/auto-responder.
    Signals: known bot phrases, URLs in first message, instant response time.
    """
    t = text.lower().strip()
    for indicator in _BOT_INDICATORS:
        if indicator in t:
            return True
    return False

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
    BOT_NAME: str = "Estefania Fernandez"
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

    # Message accumulation — seconds to wait for additional messages before replying
    MESSAGE_ACCUMULATION_SECONDS: float = 8.0

    # Reply to contacts not found in Monday (useful for testing)
    REPLY_TO_UNKNOWN_CONTACTS: bool = True

    # Monday Queue
    MONDAY_QUEUE_ENABLED: bool = True
    MONDAY_CACHE_SYNC_MINUTES: int = 10
    MONDAY_ORPHAN_GROUP_ID: str = ""  # Monday group ID for unknown contacts inbox

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
        self.monday_queue: Optional[MondayQueue] = None
        self.processed_ids = BoundedOrderedSet(4000)
        self.bot_sent_ids = BoundedOrderedSet(2000)  # msg IDs sent by bot (for passive handoff)
        self.silenced_users: Dict[str, float] = {}  # phone → silenced_until_ts
        self.startup_time = time.time()
        # Message accumulation buffer: phone → {"texts": [...], "task": asyncio.Task}
        self.message_buffers: Dict[str, Dict] = {}
        # Background tasks
        self._cache_sync_task: Optional[asyncio.Task] = None

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
    # Restore silenced users from DB (survive restarts)
    state.silenced_users = await state.memory.load_silenced_users()
    if state.silenced_users:
        logger.info(f"✅ SQLite initialized — restored {len(state.silenced_users)} silenced users")
    else:
        logger.info("✅ SQLite initialized")
    
    # LLM smoke test — Gemini primary, OpenAI fallback
    from src.conversation_logic import gemini_client, _GEMINI_MODEL, openai_client, FALLBACK_MODEL
    if os.getenv("GEMINI_API_KEY"):
        try:
            _test = await gemini_client.chat.completions.create(
                model=_GEMINI_MODEL,
                messages=[{"role": "user", "content": "Responde solo 'OK'"}],
                max_tokens=5,
                temperature=0,
            )
            logger.info(f"✅ Gemini smoke test OK — model: {_GEMINI_MODEL}, response: {_test.choices[0].message.content.strip()}")
        except Exception as e:
            logger.error(f"❌ Gemini smoke test FAILED: {e}")
            logger.warning("⚠️ Gemini no disponible — se usará OpenAI como fallback")
    else:
        logger.info("ℹ️ GEMINI_API_KEY not set — using OpenAI only")

    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("⚠️ OPENAI_API_KEY not set — AI replies will fail if Gemini also fails")
    else:
        logger.info(f"✅ OpenAI fallback configured — model: {FALLBACK_MODEL}")

    # Monday check
    if monday_followup.is_configured():
        logger.info("✅ Monday.com configured")
    else:
        logger.warning("⚠️ Monday.com NOT configured — sender won't work")

    # Monday Queue (Outbox + Cache + DLQ)
    if settings.MONDAY_QUEUE_ENABLED and monday_followup.is_configured():
        state.monday_queue = MondayQueue(settings.SQLITE_PATH)
        await state.monday_queue.init()

        # Start background queue processor with DLQ alert callback
        async def _dlq_alert(item):
            """Alert owner when a Monday update permanently fails."""
            if settings.OWNER_PHONE:
                msg = (
                    f"⚠️ ERROR CRITICO Monday:\n"
                    f"Operación: {item['operation']}\n"
                    f"Item: {item['item_id']}\n"
                    f"Error: {item.get('error', 'desconocido')[:200]}\n"
                    f"Reintentos: {item['retries']}\n"
                    f"Revisa /admin/queue para más detalles."
                )
                await _send_reply(settings.OWNER_PHONE, msg)

        state.monday_queue.start_processor(monday_followup, alert_callback=_dlq_alert)

        # Initial cache sync
        asyncio.create_task(_sync_contacts_cache())
        # Periodic cache sync
        state._cache_sync_task = asyncio.create_task(
            _periodic_cache_sync(settings.MONDAY_CACHE_SYNC_MINUTES)
        )
        logger.info("✅ Monday Queue + Cache initialized")
    else:
        logger.info("ℹ️ Monday Queue disabled or Monday not configured")

    # Auto-resume scheduler — resumes interrupted campaigns when office hours start
    sender.start_auto_resume_scheduler(
        memory_store=state.memory,
        monday_queue=state.monday_queue,
        bot_sent_ids=state.bot_sent_ids,
    )

    logger.info(f"✅ Evolution instance: {settings.EVO_INSTANCE}")
    logger.info(f"✅ Bot identity: {settings.BOT_NAME} @ {settings.COMPANY_NAME}")
    logger.info("🟢 Followup Bot ready!")

    yield

    # Shutdown
    logger.info("🔴 Shutting down...")
    if state.monday_queue:
        await state.monday_queue.close()
    if state.memory:
        await state.memory.close()
    if state.http_client:
        await state.http_client.aclose()
    if state._cache_sync_task:
        state._cache_sync_task.cancel()
    if sender._auto_resume_task:
        sender._auto_resume_task.cancel()


app = FastAPI(title="Followup Bot", lifespan=lifespan)


# ============================================================
# CACHE SYNC
# ============================================================
async def _sync_contacts_cache():
    """Sync Monday contacts to local SQLite cache."""
    if not state.monday_queue or not monday_followup.is_configured():
        return
    try:
        contacts = await monday_followup.get_all_contacts_for_cache()
        await state.monday_queue.cache_contacts_bulk(contacts)
        logger.info(f"✅ Cache synced: {len(contacts)} contacts")
    except Exception as e:
        logger.error(f"❌ Cache sync failed: {e}")


async def _periodic_cache_sync(interval_minutes: int):
    """Periodically sync Monday contacts to cache."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await _sync_contacts_cache()
        except Exception as e:
            logger.error(f"❌ Periodic cache sync error: {e}")


# ============================================================
# HEALTH
# ============================================================
@app.get("/health")
async def health():
    queue_stats = None
    if state.monday_queue:
        try:
            queue_stats = await state.monday_queue.get_queue_stats()
        except Exception:
            pass
    return {
        "status": "ok",
        "bot": "followup-bot",
        "company": settings.COMPANY_NAME,
        "instance": settings.EVO_INSTANCE,
        "uptime_seconds": round(state.uptime_seconds),
        "processed_messages": len(state.processed_ids),
        "sender": sender.get_status(),
        "monday_configured": monday_followup.is_configured(),
        "monday_queue": queue_stats,
    }


# ============================================================
# WEBHOOK — INCOMING REPLIES
# ============================================================
@app.post("/webhook")
@app.post("/webhook/messages")
async def webhook(request: Request):
    """
    Receives incoming WhatsApp messages from Evolution API.
    Processes replies from contacts who received outbound messages.
    Accepts both /webhook and /webhook/messages paths.
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
    """
    Main webhook processor.
    Extracts phone + text, then buffers the message.
    If multiple messages arrive within MESSAGE_ACCUMULATION_SECONDS,
    they are combined into a single reply (more human-like).
    """
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

    # Handle outgoing messages (fromMe=true)
    # If the bot sent it → skip. If a HUMAN sent it → silence the bot (passive handoff).
    if from_me:
        if msg_id and msg_id not in state.bot_sent_ids:
            # A human sent this message from WhatsApp Web/phone — passive handoff
            phone_raw = remote_jid.replace("@s.whatsapp.net", "").replace("@lid", "")
            human_phone = normalize_phone(phone_raw)
            if human_phone and human_phone not in (settings.OWNER_PHONE or ""):
                silence_until = time.time() + (settings.AUTO_REACTIVATE_MINUTES * 60)
                state.silenced_users[human_phone] = silence_until
                if state.memory:
                    await state.memory.silence_user(human_phone, silence_until, reason="human_takeover")
                logger.info(
                    f"🤫 Passive handoff: human sent message to {human_phone[:6]}***, "
                    f"bot silenced for {settings.AUTO_REACTIVATE_MINUTES}min"
                )
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

    # Extract text and detect media type
    msg_content = message.get("message", {})
    text = (
        msg_content.get("conversation", "")
        or msg_content.get("extendedTextMessage", {}).get("text", "")
        or ""
    ).strip()

    # Detect media messages: try Gemini multimodal first, fall back to placeholder
    media_type = None
    if not text:
        # Try to process audio/image/video with Gemini multimodal
        if any(k in msg_content for k in ("audioMessage", "imageMessage", "videoMessage")):
            try:
                media_result = await process_media_message(
                    msg_content=msg_content,
                    message_obj=message,
                    evolution_url=settings.EVOLUTION_API_URL,
                    api_key=settings.EVOLUTION_API_KEY,
                    instance=settings.EVO_INSTANCE,
                )
            except Exception as e:
                logger.error(f"❌ Media processing error: {e}")
                media_result = None

            if media_result:
                media_type = media_result["type"]
                processed_text = media_result["text"]
                # Build context so the AI knows what was sent
                if media_type == "audio":
                    text = f"[Mensaje de voz transcrito: \"{processed_text}\"]"
                elif media_type == "imagen":
                    caption = msg_content.get("imageMessage", {}).get("caption", "").strip()
                    text = f"[Foto del cliente — contenido: {processed_text}]"
                    if caption:
                        text += f" Texto del cliente: {caption}"
                elif media_type == "video":
                    caption = msg_content.get("videoMessage", {}).get("caption", "").strip()
                    text = f"[Video del cliente — contenido: {processed_text}]"
                    if caption:
                        text += f" Texto del cliente: {caption}"
                logger.info(f"🧠 Media processed ({media_type}) from {phone[:6]}***: {text[:120]}")

        # Fallback placeholders for media that Gemini can't process or other types
        if not text:
            if "audioMessage" in msg_content:
                media_type = "audio"
                text = "[El cliente envió un mensaje de voz]"
            elif "imageMessage" in msg_content:
                media_type = "imagen"
                caption = msg_content["imageMessage"].get("caption", "").strip()
                text = f"[El cliente envió una foto]{': ' + caption if caption else ''}"
            elif "videoMessage" in msg_content:
                media_type = "video"
                caption = msg_content["videoMessage"].get("caption", "").strip()
                text = f"[El cliente envió un video]{': ' + caption if caption else ''}"
            elif "documentMessage" in msg_content:
                media_type = "documento"
                filename = msg_content["documentMessage"].get("fileName", "").strip()
                text = f"[El cliente envió un documento]{': ' + filename if filename else ''}"
            elif "stickerMessage" in msg_content:
                media_type = "sticker"
                text = "[El cliente envió un sticker]"
            elif "contactMessage" in msg_content:
                media_type = "contacto"
                display = msg_content["contactMessage"].get("displayName", "").strip()
                text = f"[El cliente compartió un contacto]{': ' + display if display else ''}"
            elif "locationMessage" in msg_content:
                media_type = "ubicacion"
                text = "[El cliente compartió su ubicación]"
            else:
                logger.info(f"📎 Unsupported message type from {phone[:6]}***, skipping")
                return
            logger.info(f"📎 Media fallback ({media_type}) from {phone[:6]}***: {text}")

    # Filter out auto-responders from other businesses/bots
    if _is_auto_responder(text):
        logger.info(f"🤖 Auto-responder detected from {phone[:6]}***, ignoring: {text[:80]}")
        return

    # Check if user is silenced (human took over)
    if phone in state.silenced_users:
        if time.time() < state.silenced_users[phone]:
            return
        else:
            del state.silenced_users[phone]
            # Clean up expired entry from SQLite
            if state.memory:
                await state.memory.unsilence_user(phone)

    # ── Buffer message for accumulation ──
    # If the client sends multiple messages quickly (e.g. "Hola" then "Bien"),
    # we wait a few seconds to group them into a single AI response.
    await _buffer_message(phone, text)


async def _buffer_message(phone: str, text: str):
    """
    Accumulate messages from the same phone within a time window.
    Resets the timer on each new message. When the timer fires,
    all accumulated texts are combined and processed as one.
    """
    buf = state.message_buffers.get(phone)

    if buf:
        # Add to existing buffer, cancel the pending flush
        buf["texts"].append(text)
        buf["task"].cancel()
        logger.info(f"📦 Buffered message #{len(buf['texts'])} from {phone[:6]}***: {text[:60]}")
    else:
        # First message — create new buffer
        buf = {"texts": [text]}
        state.message_buffers[phone] = buf
        logger.info(f"📩 New message from {phone[:6]}***: {text[:80]}")

    # Start/restart the flush timer
    buf["task"] = asyncio.create_task(
        _flush_buffer(phone, settings.MESSAGE_ACCUMULATION_SECONDS)
    )


async def _flush_buffer(phone: str, delay: float):
    """Wait for accumulation window, then process all buffered messages."""
    await asyncio.sleep(delay)

    buf = state.message_buffers.pop(phone, None)
    if not buf or not buf["texts"]:
        return

    # Combine all buffered texts into one
    texts = buf["texts"]
    if len(texts) > 1:
        combined = "\n".join(texts)
        logger.info(f"📦 Combined {len(texts)} messages from {phone[:6]}***: {combined[:120]}")
    else:
        combined = texts[0]

    # Process the combined message
    try:
        await _process_reply(phone, combined)
    except Exception as e:
        logger.error(f"❌ Reply processing error for {phone[:6]}***: {e}")


async def _process_reply(phone: str, text: str):
    """Process a (potentially combined) message and send a single reply."""
    # Determine if we're outside office hours (slower response)
    _off_hours = not is_office_hours()
    if _off_hours:
        logger.info(f"🌙 Off-hours reply from {phone[:6]}***: {text[:80]}")
    else:
        logger.info(f"📩 Processing reply from {phone[:6]}***: {text[:80]}")

    # Look up contact: cache first (microseconds), then Monday API (seconds)
    contact = None
    unknown_contact = False

    # Try local cache first (Propuesta 2)
    if state.monday_queue:
        contact = await state.monday_queue.get_cached_contact(phone)
        if contact:
            logger.info(
                f"🔍 Found in CACHE ({contact.get('source', 'cache')}): "
                f"item_id={contact['item_id']}, name={contact.get('name', 'N/A')}"
            )

    # Fall back to Monday API if not in cache
    if not contact:
        contact = await monday_followup.find_by_phone(phone)
        if contact:
            logger.info(
                f"🔍 Found in Monday API: item_id={contact['item_id']}, "
                f"name={contact.get('name', 'N/A')}, status={contact.get('status', 'N/A')}"
            )
            # Update cache with fresh data
            if state.monday_queue:
                await state.monday_queue.cache_contact(phone, contact)

    if not contact:
        if not settings.REPLY_TO_UNKNOWN_CONTACTS:
            logger.info(f"🔍 Phone {phone[:6]}*** not found in Monday nor cache, ignoring")
            return
        logger.info(f"🔍 Phone {phone[:6]}*** not found, replying anyway (REPLY_TO_UNKNOWN_CONTACTS=true)")
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
    pending_location = False
    if session:
        if session.get("context", {}).get("history"):
            history = session["context"]["history"]
        pending_location = session.get("context", {}).get("pending_location", False)

    # Detect campaign type from Monday group
    campaign_type = detect_campaign_type(contact.get("group_title", ""))

    # ── LOG: Monday data being used for AI context ──
    contact_vehicle = contact.get("vehicle", "")
    contact_notes = contact.get("notes", "")
    contact_resumen = contact.get("resumen", "")
    logger.info(
        f"📋 MONDAY DATA for {phone[:6]}***:\n"
        f"   Name: {contact.get('name', 'N/A')}\n"
        f"   Vehicle: {contact_vehicle or '(VACÍO)'}\n"
        f"   Notes: {contact_notes[:150] or '(VACÍO)'}\n"
        f"   Resumen: {contact_resumen[:150] or '(VACÍO)'}\n"
        f"   Group: {contact.get('group_title', 'N/A')}\n"
        f"   Campaign: {campaign_type}"
    )

    # Process with AI
    contact_data = {
        "name": contact.get("name", ""),
        "vehicle": contact_vehicle,
        "notes": contact_notes,
        "resumen": contact_resumen,
        "last_contact": contact.get("last_contact", ""),
    }
    result = await handle_reply(
        user_text=text,
        contact_data=contact_data,
        conversation_history=history,
        campaign_type=campaign_type,
        pending_location=pending_location,
    )

    reply_text = result["reply"]
    action = result["action"]
    summary = result["summary"]

    # ── LOG: What the bot will respond ──
    logger.info(
        f"🤖 BOT REPLY to {phone[:6]}***:\n"
        f"   Action: {action}\n"
        f"   Reply: {reply_text[:300]}"
    )

    # Off-hours: add schedule notice ONLY on the FIRST reply in this conversation.
    # Avoids repeating "Nuestro horario es de 9am a 6pm" on every single message
    # which sounds robotic. Only shown once when there's no prior conversation.
    if _off_hours and action != "stop":
        # Check if we've already sent a schedule notice in this conversation
        schedule_already_sent = False
        if history:
            for msg in history:
                if msg.get("role") == "assistant":
                    msg_lower = msg.get("content", "").lower()
                    if "horario" in msg_lower and ("9am" in msg_lower or "9:00" in msg_lower):
                        schedule_already_sent = True
                        break

        if not schedule_already_sent:
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

    # Save to SQLite (include pending_location flag if waiting for location)
    context_to_save = {"history": history}
    if action == "pending_location":
        context_to_save["pending_location"] = True
    await state.memory.upsert(phone, action, context_to_save)

    # Handle unknown contacts — Propuesta 3: create orphan in Monday
    if unknown_contact:
        if state.monday_queue and settings.MONDAY_ORPHAN_GROUP_ID:
            # Queue creation of orphan contact in Monday
            await state.monday_queue.enqueue("", "create_item", {
                "group_id": settings.MONDAY_ORPHAN_GROUP_ID,
                "name": f"Lead Entrante - {phone}",
                "column_values": {
                    monday_followup.dedupe_col_id: phone,
                    monday_followup.status_col_id: {"label": "Respondió"},
                    monday_followup.reply_col_id: {"text": f"Cliente: {text[:500]}"},
                    monday_followup.resumen_col_id: {"text": f"Contacto desconocido escribió: {text[:300]}. Bot respondió: {reply_text[:200]}"},
                },
            })
            logger.info(f"📥 Orphan contact queued for Monday: {phone[:6]}***")
        else:
            logger.info(f"📝 Unknown contact reply sent: action={action}")
        return

    # Generate AI conversation summary for Monday resumen column
    resumen = ""
    try:
        resumen = await generate_conversation_resumen(
            conversation_history=history,
            user_text=text,
            bot_reply=reply_text,
            contact_data={
                "name": contact.get("name", ""),
                "vehicle": contact.get("vehicle", ""),
            },
            previous_resumen=contact.get("resumen", ""),
        )
        logger.info(f"📝 Resumen generated for {phone[:6]}***: {resumen[:80]}...")
    except Exception as e:
        logger.error(f"❌ Resumen generation failed: {e}")

    # Update Monday — via queue (Propuesta 1) or direct
    item_id = contact["item_id"]
    queue = state.monday_queue

    # Map action → status label and note
    status_map = {
        "stop": "STOP",
        "handoff": "Handoff",
        "interested": "Interesado",
        "continue": "Respondió",
        "pending_location": "Interesado",
    }
    new_status = status_map.get(action, "Respondió")

    note_icons = {"stop": "🛑", "handoff": "🤝", "interested": "🟢", "continue": "💬", "pending_location": "📍"}
    icon = note_icons.get(action, "💬")

    if action == "continue":
        note_body = f"{icon} Cliente: {text[:200]}\n\nBot: {reply_text[:200]}\n\nResumen: {resumen}" if resumen else f"{icon} {summary}"
    else:
        note_body = f"{icon} {summary}\n\nResumen: {resumen}" if resumen else f"{icon} {summary}"

    # Extract detected location from AI result (if any)
    detected_location = result.get("location")

    if queue:
        # Queue updates (guaranteed delivery even if Monday is down)
        await queue.enqueue(item_id, "update_reply", {
            "item_id": item_id,
            "status": new_status,
            "reply_summary": summary,
            "resumen": resumen,
        })
        await queue.enqueue(item_id, "add_note", {
            "item_id": item_id,
            "body": note_body,
        })
        # Update location dropdown in Monday if detected
        if detected_location:
            await queue.enqueue(item_id, "update_status", {
                "item_id": item_id,
                "status": new_status,
                "extra_cols": {
                    monday_followup.location_col_id: {"labels": [detected_location]}
                },
            })
            logger.info(f"📍 Location '{detected_location}' queued for {phone[:6]}***: item={item_id}")
        # Update local cache immediately
        await queue.update_cached_contact_fields(phone, {
            "status": new_status,
            "resumen": resumen,
        })
        logger.info(f"📥 Monday updates queued for {phone[:6]}***: item={item_id}, action={action}")
    else:
        # Direct Monday calls (fallback if queue disabled)
        try:
            await monday_followup.update_reply(item_id, new_status, summary, resumen=resumen)
            await monday_followup.add_note(item_id, note_body)
            # Update location dropdown in Monday if detected
            if detected_location:
                await monday_followup.update_status(item_id, new_status, {
                    monday_followup.location_col_id: {"labels": [detected_location]}
                })
                logger.info(f"📍 Location '{detected_location}' updated for {phone[:6]}***: item={item_id}")
            logger.info(f"✅ Monday updated for {phone[:6]}***: item={item_id}, action={action}")
        except Exception as e:
            logger.error(f"❌ Monday update FAILED for {phone[:6]}***: item={item_id}, action={action}, error={e}")

    # Actions that need immediate side effects (not queued)
    if action == "handoff":
        silence_until = time.time() + (settings.AUTO_REACTIVATE_MINUTES * 60)
        state.silenced_users[phone] = silence_until
        # Persist to SQLite so handoff survives restarts
        await state.memory.silence_user(phone, silence_until, reason="handoff")
        if settings.OWNER_PHONE:
            # Build a rich handoff alert with all useful context
            now_mx = get_mexico_now()
            fecha = now_mx.strftime("%d/%m/%Y %I:%M %p")
            vehicle_info = contact.get("vehicle", "").strip()
            campaign_group = contact.get("group_title", "").strip()

            alert_lines = [f"🤝 HANDOFF en seguimiento"]
            alert_lines.append(f"📅 {fecha}")
            alert_lines.append(f"👤 {contact['name']}")
            alert_lines.append(f"📞 {phone}")
            if detected_location:
                alert_lines.append(f"📍 Ubicacion: {detected_location}")
            if vehicle_info:
                alert_lines.append(f"🚛 Vehiculo: {vehicle_info}")
            if campaign_group:
                alert_lines.append(f"📋 Campaña: {campaign_group}")
            if resumen:
                alert_lines.append(f"📝 Resumen: {resumen}")
            elif text and text.strip():
                alert_lines.append(f"💬 Ultimo mensaje: {text[:200]}")

            alert = "\n".join(alert_lines)
            await _send_reply(settings.OWNER_PHONE, alert)
    elif action == "interested":
        if settings.OWNER_PHONE:
            now_mx = get_mexico_now()
            fecha = now_mx.strftime("%d/%m/%Y %I:%M %p")
            vehicle_info = contact.get("vehicle", "").strip()

            alert_lines = [f"🟢 LEAD INTERESADO en seguimiento"]
            alert_lines.append(f"📅 {fecha}")
            alert_lines.append(f"👤 {contact['name']}")
            alert_lines.append(f"📞 {phone}")
            if vehicle_info:
                alert_lines.append(f"🚛 Vehiculo: {vehicle_info}")
            if resumen:
                alert_lines.append(f"📝 Resumen: {resumen}")
            elif text and text.strip():
                alert_lines.append(f"💬 Ultimo mensaje: {text[:200]}")

            alert = "\n".join(alert_lines)
            await _send_reply(settings.OWNER_PHONE, alert)


async def _send_reply(phone: str, text: str, slow: bool = False):
    """Send a WhatsApp message via Evolution API."""
    normalized = normalize_phone(phone)
    if not normalized:
        return

    jid = f"{normalized}@s.whatsapp.net"
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{settings.EVO_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY, "Content-Type": "application/json"}
    body = {"number": jid, "text": text}

    # Typing delay — simulate human reading + typing
    # Longer messages get slightly longer "typing" time
    char_count = len(text)
    if slow:
        # Off-hours: respond slower to look more natural
        delay = random.uniform(15, 30)
        logger.info(f"🌙 Off-hours reply, waiting {delay:.0f}s")
    else:
        # Base: 4-8s, plus ~1s per 80 chars (reading speed), capped at 15s
        base = random.uniform(4, 8)
        typing_extra = min(char_count / 80, 7)
        delay = base + typing_extra
    await asyncio.sleep(delay)

    try:
        client = state.http_client
        if not client:
            logger.error("❌ HTTP client not initialized")
            return
        r = await client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            logger.error(f"❌ Evolution send error: {r.status_code} {r.text[:200]}")
        else:
            # Track this message ID so we can distinguish bot vs human sends
            try:
                resp_data = r.json()
                sent_id = (
                    resp_data.get("key", {}).get("id", "")
                    or resp_data.get("messageId", "")
                )
                if sent_id:
                    state.bot_sent_ids.add(sent_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"❌ Evolution send failed: {e}")


# ============================================================
# ADMIN ENDPOINTS
# ============================================================
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """Serve the admin dashboard UI."""
    return DASHBOARD_HTML


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

    result = await sender.start_campaign(
        group_id, memory_store=state.memory, force=force,
        monday_queue=state.monday_queue, bot_sent_ids=state.bot_sent_ids,
    )
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


# ============================================================
# QUEUE & DLQ ENDPOINTS
# ============================================================
@app.get("/admin/queue")
async def queue_status():
    """Get Monday queue stats: pending, DLQ, cache size."""
    if not state.monday_queue:
        return {"error": "Monday queue not enabled"}
    stats = await state.monday_queue.get_queue_stats()
    pending = await state.monday_queue.get_pending(limit=10)
    return {"stats": stats, "next_pending": pending}


@app.get("/admin/queue/dlq")
async def queue_dlq():
    """View dead letter queue — updates that permanently failed."""
    if not state.monday_queue:
        return {"error": "Monday queue not enabled"}
    items = await state.monday_queue.get_dlq_items(limit=50)
    return {"dlq_count": len(items), "items": items}


@app.post("/admin/queue/dlq/{dlq_id}/retry")
async def retry_dlq_item(dlq_id: int):
    """Move a DLQ item back to the outbox for retry."""
    if not state.monday_queue:
        return {"error": "Monday queue not enabled"}
    success = await state.monday_queue.retry_dlq_item(dlq_id)
    return {"success": success, "dlq_id": dlq_id}


@app.post("/admin/cache/sync")
async def force_cache_sync():
    """Force a cache sync from Monday."""
    if not state.monday_queue:
        return {"error": "Monday queue not enabled"}
    await _sync_contacts_cache()
    stats = await state.monday_queue.get_queue_stats()
    return {"status": "synced", "cache_contacts": stats["cache_contacts"]}
