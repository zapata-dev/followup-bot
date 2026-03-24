"""
Sender Service — Motor de envío outbound con rate limiting.
Lee contactos pendientes de Monday, envía por Evolution API, actualiza estatus.

Estrategia anti-baneo para volumen alto (300-1000+ contactos):
- Semáforo GLOBAL compartido entre todas las campañas activas (no se pueden solapar envíos)
- Variación de mensajes (message spinning) para que ningún texto sea idéntico
- Delay aleatorio variable entre mensajes (no uniforme)
- Límite por hora y por día compartido entre campañas
- Horario diferenciado: L-V 9-18, Sáb 9-14, Dom no envía
- Abort inmediato si WhatsApp se desconecta (Connection Closed)
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

# ──────────────────────────────────────────────────────────
# MESSAGE SPINNING — Variaciones para evitar mensajes idénticos
# Cada lista tiene variantes semánticamente equivalentes.
# ──────────────────────────────────────────────────────────

_SPIN_GREETINGS = [
    "Hola {nombre}",
    "Hola {nombre}!",
    "Hola {nombre}, ¿cómo estás?",
    "Buenas {nombre}",
    "Qué tal {nombre}",
    "Hola {nombre}, buen día",
]

_SPIN_INTRO_LOST = [
    "te escribo de {company_name}.",
    "te contacto desde {company_name}.",
    "soy {bot_name} de {company_name}.",
    "te habla {bot_name} de {company_name}.",
    "me comunico de {company_name}.",
]

_SPIN_FOLLOW_LOST = [
    "Hace un tiempo nos preguntaste por el {vehiculo} y quería saber si sigues evaluando esa opción o si ya resolviste tu compra.",
    "Anteriormente mostraste interés en el {vehiculo}, ¿todavía lo estás considerando o ya tomaste una decisión?",
    "Vi que en su momento preguntaste por el {vehiculo}. ¿Sigues buscando o ya lo resolviste?",
    "Hace rato tenías interés en el {vehiculo}. ¿Qué tal va esa búsqueda, ya encontraste algo?",
    "Recuerdo que preguntaste por el {vehiculo}. ¿Aún está en tus planes o ya cerraste algo?",
]

_SPIN_FOLLOW_ASSIGNED = [
    "¿Te pudieron resolver tu consulta sobre el {vehiculo}?",
    "¿Quedaste bien atendido con lo del {vehiculo}?",
    "¿Cómo te fue con la consulta del {vehiculo}?",
    "¿Te dieron respuesta sobre el {vehiculo}?",
    "¿Te atendieron bien con lo del {vehiculo}?",
]

_SPIN_FOLLOW_APPOINTMENT = [
    "¿Qué tal te pareció el {vehiculo} cuando viniste a verlo?",
    "¿Cómo te fue con la visita para ver el {vehiculo}?",
    "¿Qué impresión te llevaste del {vehiculo}?",
    "¿Te convenció el {vehiculo} cuando lo viste?",
    "Después de ver el {vehiculo}, ¿qué te pareció?",
]

_SPIN_FOLLOW_SERVICE = [
    "¿Cómo te han atendido con esa unidad?",
    "¿Cómo va todo con el {vehiculo}?",
    "¿Te han dado buen servicio con el {vehiculo}?",
    "¿Qué tal la atención que has recibido?",
    "¿Cómo ha sido tu experiencia con el {vehiculo}?",
]


def _spin(options: List[str], **kwargs) -> str:
    """Pick a random variant and format with kwargs."""
    return random.choice(options).format(**kwargs)


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
    L-V: 9:00-19:00, Sáb: 9:00-14:00, Dom: closed.
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
    return "09:00" <= current_time <= "19:00"


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
    return ("09:00", "19:00")


class SenderService:
    """
    Motor de envío outbound con protecciones anti-baneo.

    Estrategia anti-baneo:
    - Semáforo global: solo UNA campaña puede enviar a la vez (no solapamiento)
    - Spinning: ningún mensaje es idéntico a otro
    - Lotes de 10, pausa 3-6 min entre lotes
    - Delay entre mensajes: 20-60s (aleatorio)
    - Máximo 25/hora, 120/día
    - L-V: 9am-6pm, Sáb: 9am-2pm, Dom: no envía
    - Abort inmediato en Connection Closed (WhatsApp desconectado)
    """

    # Semáforo de clase — compartido entre TODAS las instancias/campañas
    _global_send_lock: asyncio.Lock = None

    @classmethod
    def _get_global_lock(cls) -> asyncio.Lock:
        """Lazy init del lock global (debe crearse dentro de un event loop)."""
        if cls._global_send_lock is None:
            cls._global_send_lock = asyncio.Lock()
        return cls._global_send_lock

    def __init__(self):
        # Evolution API config
        self.evo_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.evo_key = os.getenv("EVOLUTION_API_KEY", "")
        self.evo_instance = os.getenv("EVO_INSTANCE", "Seguimiento")

        # Rate limiting — conservador para evitar ban de WhatsApp
        self.delay_min = int(os.getenv("SEND_DELAY_MIN", "20"))
        self.delay_max = int(os.getenv("SEND_DELAY_MAX", "60"))
        self.max_per_hour = int(os.getenv("MAX_SENDS_PER_HOUR", "25"))
        self.max_per_day = int(os.getenv("MAX_SENDS_PER_DAY", "120"))

        # Batch settings — send N messages, then rest
        self.batch_size = int(os.getenv("BATCH_SIZE", "10"))
        self.batch_pause_min = int(os.getenv("BATCH_PAUSE_MIN", "180"))   # 3 min
        self.batch_pause_max = int(os.getenv("BATCH_PAUSE_MAX", "360"))   # 6 min

        # Default template (can be overridden per contact in Monday)
        self.default_template = os.getenv(
            "DEFAULT_TEMPLATE",
            "Hola {nombre}, te saluda {bot_name} de {company_name}. {mensaje}"
        )
        self.bot_name = os.getenv("BOT_NAME", "Estefania Fernandez")
        self.company_name = os.getenv("COMPANY_NAME", "La empresa")
        self.company_url = os.getenv("COMPANY_URL", "")

        # Runtime state
        self._sends_this_hour = 0
        self._sends_today = 0
        self._hour_reset_at = None
        self._day_reset_at = None
        self._active_campaigns: Dict[str, bool] = {}  # group_id → is_running
        self._paused_campaigns: set = set()

        # Flag global: WhatsApp desconectado → abortar todo
        self._whatsapp_disconnected: bool = False

        # Circuit breaker: N errores consecutivos → parar campaña
        self.max_consecutive_errors = int(os.getenv("MAX_CONSECUTIVE_ERRORS", "3"))
        self._consecutive_errors: int = 0

        # Auto-resume: campaigns interrupted by end of office hours
        self._interrupted_campaigns: set = set()
        self._auto_resume_task: Optional[asyncio.Task] = None
        self._auto_resume_deps: Dict = {}

    # ──────────────────────────────────────────────────────────
    # RATE LIMIT CHECKS
    # ──────────────────────────────────────────────────────────
    def _check_hourly_limit(self) -> bool:
        now = get_mexico_now()
        current_hour = now.strftime("%Y-%m-%d-%H")
        if self._hour_reset_at != current_hour:
            self._hour_reset_at = current_hour
            self._sends_this_hour = 0
        return self._sends_this_hour < self.max_per_hour

    def _check_daily_limit(self) -> bool:
        now = get_mexico_now()
        current_day = now.strftime("%Y-%m-%d")
        if self._day_reset_at != current_day:
            self._day_reset_at = current_day
            self._sends_today = 0
        return self._sends_today < self.max_per_day

    # ──────────────────────────────────────────────────────────
    # SPINTAX — resolve [opcion1|opcion2|opcion3] in templates
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_spintax(text: str) -> str:
        """
        Resolve spintax blocks: [option1|option2|option3] → picks one randomly.
        Works for user-defined templates from Monday or .env.
        Example: "[Hola|Buen día] {nombre}" → "Buen día {nombre}"
        """
        pattern = re.compile(r'\[([^\[\]]+\|[^\[\]]+)\]')
        while True:
            match = pattern.search(text)
            if not match:
                break
            options = match.group(1).split('|')
            text = text[:match.start()] + random.choice(options) + text[match.end():]
        return text

    # ──────────────────────────────────────────────────────────
    # MESSAGE SPINNING — nunca dos mensajes iguales
    # ──────────────────────────────────────────────────────────
    def _spin_message(self, campaign_type: str, contact: Dict) -> str:
        """
        Genera un mensaje con variación aleatoria según el tipo de campaña.
        Combina greeting + intro + cuerpo de forma que ningún par sea idéntico.
        """
        raw_name = contact.get("name", "").split("|")[0].strip() or "cliente"
        vehicle = contact.get("vehicle", "").strip() or "tu unidad de interés"
        ctx = {
            "nombre": raw_name,
            "vehiculo": vehicle,
            "bot_name": self.bot_name,
            "company_name": self.company_name,
            "company_url": self.company_url,
        }

        if campaign_type == "lost_lead":
            greeting = _spin(_SPIN_GREETINGS, **ctx)
            intro = _spin(_SPIN_INTRO_LOST, **ctx)
            body = _spin(_SPIN_FOLLOW_LOST, **ctx)
            return f"{greeting}, {intro}\n{body}"

        if campaign_type == "assigned_lead":
            greeting = _spin(_SPIN_GREETINGS, **ctx)
            intro = _spin(_SPIN_INTRO_LOST, **ctx)
            body = _spin(_SPIN_FOLLOW_ASSIGNED, **ctx)
            return f"{greeting}, {intro}\n{body}"

        if campaign_type == "attended_appointment":
            greeting = _spin(_SPIN_GREETINGS, **ctx)
            intro = _spin(_SPIN_INTRO_LOST, **ctx)
            body = _spin(_SPIN_FOLLOW_APPOINTMENT, **ctx)
            return f"{greeting}, {intro}\n{body}"

        if campaign_type == "customer_service":
            greeting = _spin(_SPIN_GREETINGS, **ctx)
            intro = _spin(_SPIN_INTRO_LOST, **ctx)
            body = _spin(_SPIN_FOLLOW_SERVICE, **ctx)
            return f"{greeting}, {intro}\n{body}"

        # Fallback: default template
        msg = self.default_template
        msg = msg.replace("{nombre}", raw_name)
        msg = msg.replace("{vehiculo}", vehicle)
        msg = msg.replace("{bot_name}", self.bot_name)
        msg = msg.replace("{company_name}", self.company_name)
        msg = msg.replace("{company_url}", self.company_url)
        msg = re.sub(r'\{mensaje\}', '', msg)
        return re.sub(r'\s+', ' ', msg).strip()

    def _has_bot_presentation(self, text: str) -> bool:
        text_lower = text.lower()
        bot_first_name = self.bot_name.lower().split()[0]
        if bot_first_name in text_lower:
            return True
        patterns = [
            r'\bsoy\s+\w+', r'\bte saluda\s+\w+', r'\bte escribe\s+\w+',
            r'\bmi nombre es\s+\w+', r'\ble habla\s+\w+',
        ]
        return any(re.search(p, text_lower) for p in patterns)

    def _inject_bot_intro(self, template: str, contact_name: str) -> str:
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
            intro = f"{greeting}, soy {self.bot_name} de {self.company_name}.\n{rest}"
        else:
            intro = f"Hola, soy {self.bot_name} de {self.company_name}.\n{template}"
        return intro

    def _personalize_message(self, contact: Dict) -> str:
        """
        Build personalized message.
        Priority:
        1. Per-contact template from Monday (with spinning on name/vehicle only)
        2. Spun campaign template based on group name
        3. Default template
        """
        per_contact_template = contact.get("template", "").strip()

        if per_contact_template:
            # Monday template provided — apply variable substitution
            template = per_contact_template
            if not self._has_bot_presentation(template):
                raw_name = contact.get("name", "").split("|")[0].strip() or "cliente"
                template = self._inject_bot_intro(template, raw_name)

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
            # Apply spintax for user-defined templates: [opcion1|opcion2]
            return self._resolve_spintax(msg)

        # No per-contact template → use spinning
        from src.conversation_logic import detect_campaign_type
        campaign_type = detect_campaign_type(contact.get("group_title", ""))
        return self._spin_message(campaign_type, contact)

    # ──────────────────────────────────────────────────────────
    # EVOLUTION API — SEND MESSAGE
    # ──────────────────────────────────────────────────────────
    async def _send_whatsapp(self, phone: str, text: str, http_client: httpx.AsyncClient) -> Dict:
        """Send a WhatsApp text message via Evolution API."""
        jid = phone_for_evolution(phone)
        if not jid:
            return {"success": False, "error": f"Invalid phone: {phone}"}

        headers = {"apikey": self.evo_key, "Content-Type": "application/json"}

        # ── Simular "escribiendo..." antes de enviar ──
        # Meta analiza si los mensajes aparecen instantáneamente (= bot).
        # Un humano abre el chat, se ve "escribiendo..." unos segundos, y luego envía.
        try:
            presence_url = f"{self.evo_url}/chat/sendPresence/{self.evo_instance}"
            typing_ms = min(len(text) * 80, 8000)  # ~80ms por carácter, max 8s
            presence_body = {"number": jid, "presence": "composing", "delay": typing_ms}
            await http_client.post(presence_url, json=presence_body, headers=headers)
            # Esperar a que se muestre el estado "escribiendo" antes de enviar
            await asyncio.sleep(typing_ms / 1000.0)
        except Exception as e:
            logger.debug(f"Presence composing failed (non-critical): {e}")

        url = f"{self.evo_url}/message/sendText/{self.evo_instance}"
        body = {"number": jid, "text": text}

        for attempt in range(3):
            try:
                r = await http_client.post(url, json=body, headers=headers)

                if r.status_code == 429:
                    wait = 2 ** (attempt + 2)
                    logger.warning(f"⏳ Evolution 429, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                if r.status_code >= 400:
                    error_body = r.text[:300]
                    # Detectar desconexión de WhatsApp
                    if "Connection Closed" in error_body or "connection closed" in error_body.lower():
                        return {"success": False, "error": error_body, "disconnected": True}
                    return {"success": False, "error": f"HTTP {r.status_code}: {error_body}"}

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

        # Limpiar flag de desconexión si se arranca manualmente
        self._whatsapp_disconnected = False

        self._paused_campaigns.discard(group_id)
        self._active_campaigns[group_id] = True

        asyncio.create_task(self._run_campaign(
            group_id, memory_store, force=force,
            monday_queue=monday_queue, bot_sent_ids=bot_sent_ids
        ))

        return {"status": "started", "group_id": group_id, "force": force}

    async def pause_campaign(self, group_id: str) -> Dict:
        """Pause an active campaign."""
        self._paused_campaigns.add(group_id)
        self._active_campaigns[group_id] = False
        return {"status": "paused", "group_id": group_id}

    def _abort_all_campaigns(self, reason: str):
        """Mark all campaigns as stopped (no send lock releases needed)."""
        logger.error(f"🛑 ABORT ALL CAMPAIGNS — {reason}")
        self._whatsapp_disconnected = True
        for gid in list(self._active_campaigns.keys()):
            self._active_campaigns[gid] = False

    async def _run_campaign(self, group_id: str, memory_store=None, force: bool = False, monday_queue=None, bot_sent_ids=None):
        """
        Background task: iterate through pending contacts and send messages.

        Anti-ban strategy:
        1. Semáforo global — solo un mensaje a la vez entre todas las campañas
        2. Spinning — ningún mensaje es idéntico
        3. Batches de 10, pausa 3-6 min entre lotes
        4. Delay aleatorio 20-60s entre mensajes
        5. Max 25/hora, 120/día (compartido)
        6. L-V: 9-18, Sáb: 9-14, Dom: no envía
        7. Abort inmediato si WhatsApp se desconecta
        """
        logger.info(f"🚀 Campaign started for group: {group_id} (force={force})")
        sent = 0
        errors = 0
        batch_count = 0
        consecutive_errors = 0

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

            lock = self._get_global_lock()

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                for i, contact in enumerate(contacts):
                    # ── Check stop conditions ──
                    if group_id in self._paused_campaigns:
                        logger.info(f"⏸️ Campaign {group_id} paused after {sent} sends")
                        break

                    if self._whatsapp_disconnected:
                        logger.error(f"🛑 Campaign {group_id} aborted — WhatsApp disconnected")
                        break

                    if not force and not is_office_hours():
                        remaining = len(contacts) - i
                        logger.info(
                            f"🕐 Outside office hours, stopping. "
                            f"Sent {sent} today. Remaining: {remaining}"
                        )
                        if remaining > 0:
                            self._interrupted_campaigns.add(group_id)
                            logger.info(f"⏰ Campaign {group_id} saved for auto-resume ({remaining} pending)")
                        break

                    # Hourly limit — esperar en vez de cortar
                    if not self._check_hourly_limit():
                        logger.warning(
                            f"🛑 Hourly limit ({self.max_per_hour}) reached. "
                            f"Waiting 60s... Sent so far: {sent}"
                        )
                        await asyncio.sleep(60)
                        continue

                    if not self._check_daily_limit():
                        logger.warning(
                            f"🛑 Daily limit ({self.max_per_day}) reached. "
                            f"Campaign stops. Sent today: {sent}"
                        )
                        break

                    # ── Batch pause ──
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

                    # ── Personalize (with spinning) ──
                    message = self._personalize_message(contact)

                    # ── Send — con semáforo global ──
                    logger.info(f"📤 Sending to {phone[:6]}*** ({sent + 1}/{len(contacts)})")

                    async with lock:
                        result = await self._send_whatsapp(phone, message, http_client)

                        # Delay DENTRO del lock: garantiza separación mínima entre envíos
                        # aunque haya múltiples campañas activas
                        if not result.get("disconnected"):
                            delay = random.uniform(self.delay_min, self.delay_max)
                            logger.debug(f"⏳ Waiting {delay:.1f}s (global lock held)...")
                            await asyncio.sleep(delay)

                    # ── Handle result ──
                    if result.get("disconnected"):
                        logger.error(
                            f"❌ WhatsApp disconnected! "
                            f"Aborting all campaigns. Last contact: {phone[:6]}***"
                        )
                        self._abort_all_campaigns("Connection Closed from Evolution API")
                        if monday_queue:
                            await monday_queue.enqueue(contact["item_id"], "mark_error", {
                                "item_id": contact["item_id"],
                                "error": "WhatsApp desconectado",
                            })
                        else:
                            await monday_followup.mark_error(contact["item_id"], "WhatsApp desconectado")
                        break

                    if result["success"]:
                        consecutive_errors = 0  # Reset circuit breaker
                        if bot_sent_ids and result.get("msg_id"):
                            bot_sent_ids.add(result["msg_id"])

                        if monday_queue:
                            await monday_queue.enqueue(contact["item_id"], "update_send_date", {
                                "item_id": contact["item_id"],
                                "normalized_phone": phone,
                            })
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
                        consecutive_errors += 1

                        # ── Circuit breaker: N errores seguidos → algo está mal ──
                        if consecutive_errors >= self.max_consecutive_errors:
                            logger.error(
                                f"🔌 Circuit breaker: {consecutive_errors} consecutive errors "
                                f"in campaign {group_id}. Stopping all campaigns."
                            )
                            self._abort_all_campaigns(
                                f"{consecutive_errors} consecutive send errors"
                            )
                            break

        except Exception as e:
            logger.error(f"❌ Campaign {group_id} crashed: {e}")
        finally:
            self._active_campaigns[group_id] = False
            logger.info(f"✅ Campaign {group_id} finished: {sent} sent, {errors} errors")

    # ──────────────────────────────────────────────────────────
    # AUTO-RESUME
    # ──────────────────────────────────────────────────────────
    def start_auto_resume_scheduler(self, memory_store=None, monday_queue=None, bot_sent_ids=None):
        """
        Start background task that checks every 60s if office hours started
        and resumes any campaigns that were interrupted by end of office hours.
        """
        self._auto_resume_deps = {
            "memory_store": memory_store,
            "monday_queue": monday_queue,
            "bot_sent_ids": bot_sent_ids,
        }
        self._auto_resume_task = asyncio.create_task(self._auto_resume_loop())
        logger.info("✅ Auto-resume scheduler started")

    async def _auto_resume_loop(self):
        was_office_hours = is_office_hours()
        while True:
            await asyncio.sleep(60)
            try:
                now_office_hours = is_office_hours()
                if now_office_hours and not was_office_hours:
                    # Nueva sesión de horario — limpiar flag de desconexión
                    self._whatsapp_disconnected = False
                    await self._resume_interrupted_campaigns()
                was_office_hours = now_office_hours
            except Exception as e:
                logger.error(f"❌ Auto-resume loop error: {e}")

    async def _resume_interrupted_campaigns(self):
        if not self._interrupted_campaigns:
            return

        to_resume = list(self._interrupted_campaigns)
        logger.info(
            f"⏰ Office hours started — auto-resuming {len(to_resume)} "
            f"interrupted campaign(s): {to_resume}"
        )

        for group_id in to_resume:
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
        now = get_mexico_now()
        schedule = get_today_schedule(now)
        day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

        return {
            "active_campaigns": {k: v for k, v in self._active_campaigns.items() if v},
            "paused_campaigns": list(self._paused_campaigns),
            "interrupted_campaigns": list(self._interrupted_campaigns),
            "whatsapp_disconnected": self._whatsapp_disconnected,
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
            "circuit_breaker_limit": self.max_consecutive_errors,
        }


# Singleton
sender = SenderService()
