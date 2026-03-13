"""
Sender Service — Motor de envío outbound con rate limiting.
Lee contactos pendientes de Monday, envía por Evolution API, actualiza estatus.

Estrategia anti-baneo para volumen alto (300-1000+ contactos):
- Envía en lotes (batches) con descanso entre lotes
- Delay aleatorio variable entre mensajes (no uniforme)
- Límite por hora y por día
- Horario diferenciado: L-V 9-18, Sáb 9-14, Dom no envía
"""
import os
import re
import asyncio
import random
import logging
from datetime import datetime
from typing import Optional, Dict, List

import httpx
import pytz

from src.phone_utils import normalize_phone, phone_for_evolution
from src.monday_service import monday_followup

logger = logging.getLogger(__name__)

# Day of week constants (datetime.weekday())
MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)


def get_mexico_now() -> datetime:
    """Get current time in Mexico City timezone."""
    try:
        tz = pytz.timezone("America/Mexico_City")
        return datetime.now(tz)
    except Exception:
        return datetime.now()


def is_office_hours(now: datetime = None) -> bool:
    """
    Check if current time is within office hours.
    L-V: 9:00-18:00, Sáb: 9:00-14:00, Dom: closed.
    """
    if now is None:
        now = get_mexico_now()
    day = now.weekday()
    current_time = now.strftime("%H:%M")

    if day == SUNDAY:
        return False
    if day == SATURDAY:
        return "09:00" <= current_time <= "14:00"
    # Monday - Friday
    return "09:00" <= current_time <= "18:00"


def get_today_schedule(now: datetime = None) -> Optional[tuple]:
    """
    Returns (start, end) for today's send window, or None if no sends today.
    """
    if now is None:
        now = get_mexico_now()
    day = now.weekday()

    if day == SUNDAY:
        return None
    if day == SATURDAY:
        return ("09:00", "14:00")
    return ("09:00", "18:00")


class SenderService:
    """
    Motor de envío outbound con protecciones anti-baneo.

    Estrategia para campañas grandes:
    - Lotes de 15 mensajes, luego pausa de 3-5 min
    - Delay entre mensajes: 15-45s (aleatorio)
    - Máximo 40/hora, 200/día
    - L-V: 9am-6pm, Sáb: 9am-2pm, Dom: no envía
    """

    def __init__(self):
        # Evolution API config
        self.evo_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.evo_key = os.getenv("EVOLUTION_API_KEY", "")
        self.evo_instance = os.getenv("EVO_INSTANCE", "Seguimiento")

        # Rate limiting — conservative defaults for WhatsApp safety
        self.delay_min = int(os.getenv("SEND_DELAY_MIN", "15"))
        self.delay_max = int(os.getenv("SEND_DELAY_MAX", "45"))
        self.max_per_hour = int(os.getenv("MAX_SENDS_PER_HOUR", "40"))
        self.max_per_day = int(os.getenv("MAX_SENDS_PER_DAY", "200"))

        # Batch settings — send N messages, then rest
        self.batch_size = int(os.getenv("BATCH_SIZE", "15"))
        self.batch_pause_min = int(os.getenv("BATCH_PAUSE_MIN", "180"))   # 3 min
        self.batch_pause_max = int(os.getenv("BATCH_PAUSE_MAX", "300"))   # 5 min

        # Default template (can be overridden per contact in Monday)
        self.default_template = os.getenv(
            "DEFAULT_TEMPLATE",
            "Hola {nombre}, te saluda {bot_name} de {company_name}. {mensaje}"
        )
        self.bot_name = os.getenv("BOT_NAME", "Estefania Fernandez")
        self.company_name = os.getenv("COMPANY_NAME", "La empresa")
        self.company_url = os.getenv("COMPANY_URL", "")

        # Campaign-specific templates (derived from Monday group name keywords)
        # IMPORTANT: Keep templates SHORT (1-3 lines max). WhatsApp = chat, not email.
        # Each template ends with ONE clear question to invite response.
        self.campaign_templates = {
            "lost_lead": os.getenv("TEMPLATE_LOST_LEAD", (
                "Hola {nombre}, te escribo de {company_name}. "
                "Hace un tiempo preguntaste por el {vehiculo} y queria saber "
                "si sigues evaluando esa opcion o si ya resolviste tu compra."
            )),
            "assigned_lead": os.getenv("TEMPLATE_ASSIGNED_LEAD", (
                "Hola {nombre}, soy {bot_name} de {company_name}. "
                "Te pudieron resolver tu consulta sobre el {vehiculo}?"
            )),
            "attended_appointment": os.getenv("TEMPLATE_ATTENDED_APPOINTMENT", (
                "Hola {nombre}, te escribo de {company_name}. "
                "Que tal te parecio el {vehiculo} cuando viniste a verlo?"
            )),
            "customer_service": os.getenv("TEMPLATE_CUSTOMER_SERVICE", (
                "Hola {nombre}, soy {bot_name} de {company_name}. "
                "Vi que te interesa el {vehiculo}, como te han atendido con esa unidad?"
            )),
        }

        # Runtime state
        self._sends_this_hour = 0
        self._sends_today = 0
        self._hour_reset_at = None
        self._day_reset_at = None
        self._active_campaigns: Dict[str, bool] = {}  # group_id → is_running
        self._paused_campaigns: set = set()

        # Auto-resume: campaigns interrupted by end of office hours
        # These will be automatically restarted when office hours begin again.
        self._interrupted_campaigns: set = set()  # group_ids to resume
        self._auto_resume_task: Optional[asyncio.Task] = None
        self._auto_resume_deps: Dict = {}  # memory_store, monday_queue, bot_sent_ids

    # ──────────────────────────────────────────────────────────
    # RATE LIMIT CHECKS
    # ──────────────────────────────────────────────────────────
    def _check_hourly_limit(self) -> bool:
        """Check if we're within the hourly send limit."""
        now = get_mexico_now()
        current_hour = now.strftime("%Y-%m-%d-%H")

        if self._hour_reset_at != current_hour:
            self._hour_reset_at = current_hour
            self._sends_this_hour = 0

        return self._sends_this_hour < self.max_per_hour

    def _check_daily_limit(self) -> bool:
        """Check if we're within the daily send limit."""
        now = get_mexico_now()
        current_day = now.strftime("%Y-%m-%d")

        if self._day_reset_at != current_day:
            self._day_reset_at = current_day
            self._sends_today = 0

        return self._sends_today < self.max_per_day

    # ──────────────────────────────────────────────────────────
    # TEMPLATE PERSONALIZATION
    # ──────────────────────────────────────────────────────────
    def _has_bot_presentation(self, text: str) -> bool:
        """
        Detect if a template already includes the bot's name/presentation.
        Checks for the bot's first name (case-insensitive) in common intro patterns.
        """
        text_lower = text.lower()
        bot_first_name = self.bot_name.lower().split()[0]  # e.g. "estefania"

        # Direct name mention
        if bot_first_name in text_lower:
            return True

        # Common presentation patterns (even without name)
        presentation_patterns = [
            r'\bsoy\s+\w+',           # "soy [nombre]"
            r'\bte saluda\s+\w+',     # "te saluda [nombre]"
            r'\bte escribe\s+\w+',    # "te escribe [nombre]"
            r'\bmi nombre es\s+\w+',  # "mi nombre es [nombre]"
            r'\ble habla\s+\w+',      # "le habla [nombre]"
        ]
        for pattern in presentation_patterns:
            if re.search(pattern, text_lower):
                return True

        return False

    def _inject_bot_intro(self, template: str, contact_name: str) -> str:
        """
        Inject a natural bot presentation at the beginning of a template
        that doesn't already include the bot's name.
        Finds the right insertion point (after the initial greeting).
        """
        # Patterns for common greetings at the start
        greeting_pattern = re.compile(
            r'^(hola[,.]?\s*(?:buen(?:os)?\s+(?:días|dias|día|dia|tardes?|noches?))?[,.]?\s*'
            r'|buen(?:os)?\s+(?:días|dias|día|dia|tardes?|noches?)[,.]?\s*'
            r'|¿?cómo\s+(?:te\s+encuentras|estás|estas)\??[,.]?\s*)',
            re.IGNORECASE,
        )

        match = greeting_pattern.match(template)
        if match:
            greeting = match.group(0).rstrip().rstrip(",").rstrip(".")
            rest = template[match.end():].lstrip()
            # Natural join: "Hola, soy Estefania..." not "Hola Soy Estefania..."
            intro = f"{greeting}, soy {self.bot_name} de {self.company_name}.\n{rest}"
        else:
            # No greeting found — prepend full intro
            intro = f"Hola, soy {self.bot_name} de {self.company_name}.\n{template}"

        return intro

    def _personalize_message(self, contact: Dict) -> str:
        """
        Build personalized message from template + contact data.
        Priority: contact-level template (Monday) > campaign template > default template.

        If a Monday template doesn't include the bot's name, a natural
        presentation is injected so the client always knows who's writing.
        """
        per_contact_template = contact.get("template", "").strip()

        if per_contact_template:
            template = per_contact_template
            # Monday templates may not include bot name — inject intro if missing
            if not self._has_bot_presentation(template):
                raw_name = contact.get("name", "").split("|")[0].strip() or "cliente"
                template = self._inject_bot_intro(template, raw_name)
        else:
            from src.conversation_logic import detect_campaign_type
            campaign_type = detect_campaign_type(contact.get("group_title", ""))
            template = self.campaign_templates.get(campaign_type, self.default_template)

        raw_name = contact.get("name", "").split("|")[0].strip() or "cliente"

        msg = template.replace("{nombre}", raw_name)
        msg = msg.replace("{vehiculo}", contact.get("vehicle", "tu unidad de interés"))
        msg = msg.replace("{bot_name}", self.bot_name)
        msg = msg.replace("{company_name}", self.company_name)
        msg = msg.replace("{company_url}", self.company_url)
        msg = msg.replace("{notas}", contact.get("notes", ""))
        msg = msg.replace("{resumen}", contact.get("resumen", ""))

        msg = re.sub(r'\{mensaje\}', '', msg)
        msg = re.sub(r'\s+', ' ', msg).strip()

        return msg

    # ──────────────────────────────────────────────────────────
    # EVOLUTION API — SEND MESSAGE
    # ──────────────────────────────────────────────────────────
    async def _send_whatsapp(self, phone: str, text: str, http_client: httpx.AsyncClient) -> Dict:
        """Send a WhatsApp text message via Evolution API."""
        jid = phone_for_evolution(phone)
        if not jid:
            return {"success": False, "error": f"Invalid phone: {phone}"}

        url = f"{self.evo_url}/message/sendText/{self.evo_instance}"
        headers = {"apikey": self.evo_key, "Content-Type": "application/json"}
        body = {"number": jid, "text": text}

        for attempt in range(3):
            try:
                r = await http_client.post(url, json=body, headers=headers)

                if r.status_code == 429:
                    wait = 2 ** (attempt + 2)  # 4, 8, 16 seconds
                    logger.warning(f"⏳ Evolution 429, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                if r.status_code >= 400:
                    return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}

                # Extract sent message ID for passive handoff tracking
                sent_id = ""
                try:
                    resp_data = r.json()
                    sent_id = (
                        resp_data.get("key", {}).get("id", "")
                        or resp_data.get("messageId", "")
                    )
                except Exception:
                    pass
                return {"success": True, "error": None, "msg_id": sent_id}

            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return {"success": False, "error": str(e)[:200]}

        return {"success": False, "error": "Max retries exceeded"}

    # ──────────────────────────────────────────────────────────
    # CAMPAIGN EXECUTION
    # ──────────────────────────────────────────────────────────
    async def start_campaign(self, group_id: str, memory_store=None, force: bool = False, monday_queue=None, bot_sent_ids=None) -> Dict:
        """Start sending messages to all pending contacts in a Monday group."""
        if group_id in self._active_campaigns and self._active_campaigns[group_id]:
            return {"status": "already_running", "group_id": group_id}

        self._paused_campaigns.discard(group_id)
        self._active_campaigns[group_id] = True

        asyncio.create_task(self._run_campaign(group_id, memory_store, force=force, monday_queue=monday_queue, bot_sent_ids=bot_sent_ids))

        return {"status": "started", "group_id": group_id, "force": force}

    async def pause_campaign(self, group_id: str) -> Dict:
        """Pause an active campaign."""
        self._paused_campaigns.add(group_id)
        self._active_campaigns[group_id] = False
        return {"status": "paused", "group_id": group_id}

    async def _run_campaign(self, group_id: str, memory_store=None, force: bool = False, monday_queue=None, bot_sent_ids=None):
        """
        Background task: iterate through pending contacts and send messages.

        Anti-ban strategy:
        1. Send in batches of 15, then pause 3-5 min
        2. Random delay 15-45s between each message
        3. Max 40/hour, 200/day
        4. L-V: 9-18, Sáb: 9-14, Dom: no envía
        5. Stops at end of send window, resumes next time campaign is started
        """
        logger.info(f"🚀 Campaign started for group: {group_id} (force={force})")
        sent = 0
        errors = 0
        batch_count = 0

        try:
            contacts = await monday_followup.get_pending_contacts(group_id, limit=1000)
            logger.info(f"📋 Got {len(contacts)} pending contacts for group {group_id}")

            if not contacts:
                logger.info(f"📭 No pending contacts in group {group_id}")
                self._active_campaigns[group_id] = False
                return

            now = get_mexico_now()
            schedule = get_today_schedule(now)
            schedule_label = f"{schedule[0]}-{schedule[1]}" if schedule else "CERRADO"
            day_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
            logger.info(
                f"📅 Today: {day_names[now.weekday()]} | Schedule: {schedule_label} | "
                f"Contacts: {len(contacts)}"
            )

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                for i, contact in enumerate(contacts):
                    # ── Check stop conditions ──
                    if group_id in self._paused_campaigns:
                        logger.info(f"⏸️ Campaign {group_id} paused after {sent} sends")
                        break

                    # Check schedule (skip if force=True)
                    if not force and not is_office_hours():
                        remaining = len(contacts) - i
                        logger.info(
                            f"🕐 Outside office hours, stopping. "
                            f"Sent {sent} today. Remaining: {remaining}"
                        )
                        # Mark for auto-resume next business day
                        if remaining > 0:
                            self._interrupted_campaigns.add(group_id)
                            logger.info(
                                f"⏰ Campaign {group_id} saved for auto-resume "
                                f"({remaining} contacts pending)"
                            )
                        break

                    # Check hourly limit — wait instead of stopping
                    if not self._check_hourly_limit():
                        logger.warning(
                            f"🛑 Hourly limit ({self.max_per_hour}) reached. "
                            f"Waiting 60s... Sent so far: {sent}"
                        )
                        await asyncio.sleep(60)
                        continue

                    # Check daily limit
                    if not self._check_daily_limit():
                        logger.warning(
                            f"🛑 Daily limit ({self.max_per_day}) reached. "
                            f"Campaign stops. Sent today: {sent}"
                        )
                        break

                    # ── Batch pause: rest every N messages ──
                    if batch_count >= self.batch_size:
                        pause = random.uniform(self.batch_pause_min, self.batch_pause_max)
                        logger.info(
                            f"☕ Batch pause: {sent} sent, resting {pause:.0f}s "
                            f"before next batch..."
                        )
                        await asyncio.sleep(pause)
                        batch_count = 0

                    # ── Get phone ──
                    phone = normalize_phone(contact.get("phone", ""))
                    if not phone:
                        logger.warning(f"⚠️ Skip contact {contact['item_id']}: invalid phone")
                        await monday_followup.mark_error(contact["item_id"], "Teléfono inválido")
                        errors += 1
                        continue

                    # ── Personalize message ──
                    message = self._personalize_message(contact)

                    # ── Send ──
                    logger.info(f"📤 Sending to {phone[:6]}*** ({sent + 1}/{len(contacts)})")
                    result = await self._send_whatsapp(phone, message, http_client)

                    if result["success"]:
                        # Track bot-sent message ID for passive handoff detection
                        if bot_sent_ids and result.get("msg_id"):
                            bot_sent_ids.add(result["msg_id"])
                        # Update Monday via queue if available, otherwise direct
                        if monday_queue:
                            await monday_queue.enqueue(contact["item_id"], "update_send_date", {
                                "item_id": contact["item_id"],
                                "normalized_phone": phone,
                            })
                            # Also update cache
                            await monday_queue.cache_contact(phone, {
                                **contact,
                                "status": "Enviado",
                            })
                        else:
                            await monday_followup.update_send_date(contact["item_id"], normalized_phone=phone)

                        if memory_store:
                            await memory_store.log_send(
                                phone, contact.get("group_title", group_id), "sent"
                            )
                            # Save outbound message to conversation history
                            # so the AI knows what was said first when client replies
                            session = await memory_store.get(phone)
                            history = []
                            if session and session.get("context", {}).get("history"):
                                history = session["context"]["history"]
                            history.append({"role": "assistant", "content": message})
                            await memory_store.upsert(phone, "sent", {"history": history})

                        sent += 1
                        batch_count += 1
                        self._sends_this_hour += 1
                        self._sends_today += 1
                    else:
                        logger.error(f"❌ Failed to send to {phone[:6]}***: {result['error']}")
                        if monday_queue:
                            await monday_queue.enqueue(contact["item_id"], "mark_error", {
                                "item_id": contact["item_id"],
                                "error": result["error"],
                            })
                        else:
                            await monday_followup.mark_error(contact["item_id"], result["error"])

                        if memory_store:
                            await memory_store.log_send(
                                phone, contact.get("group_title", group_id), "error", result["error"]
                            )
                        errors += 1

                    # ── Anti-ban delay between messages ──
                    delay = random.uniform(self.delay_min, self.delay_max)
                    logger.debug(f"⏳ Waiting {delay:.1f}s before next send...")
                    await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"❌ Campaign {group_id} crashed: {e}")
        finally:
            self._active_campaigns[group_id] = False
            logger.info(f"✅ Campaign {group_id} finished: {sent} sent, {errors} errors")

    # ──────────────────────────────────────────────────────────
    # AUTO-RESUME — restart interrupted campaigns at next office hours
    # ──────────────────────────────────────────────────────────
    def start_auto_resume_scheduler(self, memory_store=None, monday_queue=None, bot_sent_ids=None):
        """
        Start background task that checks every 60s if office hours started
        and resumes any campaigns that were interrupted by end of office hours.
        Call this once during app startup.
        """
        self._auto_resume_deps = {
            "memory_store": memory_store,
            "monday_queue": monday_queue,
            "bot_sent_ids": bot_sent_ids,
        }
        self._auto_resume_task = asyncio.create_task(self._auto_resume_loop())
        logger.info("✅ Auto-resume scheduler started")

    async def _auto_resume_loop(self):
        """Check every 60s if we should resume interrupted campaigns."""
        was_office_hours = is_office_hours()
        while True:
            await asyncio.sleep(60)
            try:
                now_office_hours = is_office_hours()

                # Trigger resume when office hours START (transition from off → on)
                if now_office_hours and not was_office_hours:
                    await self._resume_interrupted_campaigns()

                was_office_hours = now_office_hours
            except Exception as e:
                logger.error(f"❌ Auto-resume loop error: {e}")

    async def _resume_interrupted_campaigns(self):
        """Resume all campaigns that were interrupted by end of office hours."""
        if not self._interrupted_campaigns:
            return

        to_resume = list(self._interrupted_campaigns)
        logger.info(
            f"⏰ Office hours started — auto-resuming {len(to_resume)} "
            f"interrupted campaign(s): {to_resume}"
        )

        for group_id in to_resume:
            # Don't resume if it was manually paused or is already running
            if group_id in self._paused_campaigns:
                logger.info(f"⏸️ Skipping {group_id} — manually paused")
                self._interrupted_campaigns.discard(group_id)
                continue
            if self._active_campaigns.get(group_id):
                logger.info(f"▶️ Skipping {group_id} — already running")
                self._interrupted_campaigns.discard(group_id)
                continue

            self._interrupted_campaigns.discard(group_id)
            logger.info(f"🔄 Auto-resuming campaign {group_id}")
            await self.start_campaign(
                group_id,
                memory_store=self._auto_resume_deps.get("memory_store"),
                monday_queue=self._auto_resume_deps.get("monday_queue"),
                bot_sent_ids=self._auto_resume_deps.get("bot_sent_ids"),
            )

    def get_status(self) -> Dict:
        """Get current sender status."""
        now = get_mexico_now()
        schedule = get_today_schedule(now)
        day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

        return {
            "active_campaigns": {k: v for k, v in self._active_campaigns.items() if v},
            "paused_campaigns": list(self._paused_campaigns),
            "interrupted_campaigns": list(self._interrupted_campaigns),
            "sends_this_hour": self._sends_this_hour,
            "max_per_hour": self.max_per_hour,
            "sends_today": self._sends_today,
            "max_per_day": self.max_per_day,
            "today": day_names[now.weekday()],
            "schedule_today": f"{schedule[0]}-{schedule[1]}" if schedule else "CERRADO (Domingo)",
            "is_office_hours": is_office_hours(now),
            "current_time_mx": now.strftime("%H:%M"),
            "delay_range": f"{self.delay_min}-{self.delay_max}s",
            "batch_size": self.batch_size,
            "batch_pause": f"{self.batch_pause_min}-{self.batch_pause_max}s",
        }


# Singleton
sender = SenderService()
