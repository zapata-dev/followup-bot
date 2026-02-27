"""
Sender Service — Motor de envío outbound con rate limiting.
Lee contactos pendientes de Monday, envía por Evolution API, actualiza estatus.
"""
import os
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


class SenderService:
    """
    Motor de envío outbound con protecciones anti-baneo.
    
    Flujo:
    1. Lee contactos con status "Pendiente" de un grupo de Monday
    2. Personaliza el template con datos del contacto
    3. Envía vía Evolution API con delay aleatorio
    4. Actualiza Monday a "Enviado"
    5. Respeta ventana horaria y rate limits
    """

    def __init__(self):
        # Evolution API config
        self.evo_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.evo_key = os.getenv("EVOLUTION_API_KEY", "")
        self.evo_instance = os.getenv("EVO_INSTANCE", "Seguimiento")

        # Rate limiting
        self.delay_min = int(os.getenv("SEND_DELAY_MIN", "10"))
        self.delay_max = int(os.getenv("SEND_DELAY_MAX", "20"))
        self.max_per_hour = int(os.getenv("MAX_SENDS_PER_HOUR", "60"))
        self.send_window_start = os.getenv("SEND_WINDOW_START", "09:00")
        self.send_window_end = os.getenv("SEND_WINDOW_END", "14:00")

        # Default template (can be overridden per contact in Monday)
        self.default_template = os.getenv(
            "DEFAULT_TEMPLATE",
            "Hola {nombre}, te saluda {bot_name} de {company_name}. {mensaje}"
        )
        self.bot_name = os.getenv("BOT_NAME", "Tu asesor")
        self.company_name = os.getenv("COMPANY_NAME", "La empresa")
        self.company_url = os.getenv("COMPANY_URL", "")

        # Campaign-specific templates (derived from Monday group name keywords)
        self.campaign_templates = {
            "lost_lead": os.getenv("TEMPLATE_LOST_LEAD", (
                "Hola {nombre}, como te encuentras?\n\n"
                "Notamos que no recibimos respuesta respecto a la promocion de "
                "tractocamiones que compartimos en nuestras plataformas de Instagram y "
                "Facebook. Agradecemos mucho que te hayas dado el tiempo de visitarnos.\n\n"
                "Si deseas conocer nuestro inventario disponible, te invitamos a "
                "consultar nuestra plataforma y pagina web:\n"
                "{company_url}\n"
                "donde podras ver todas las opciones disponibles.\n\n"
                "Y si ya se concreto tu operacion con el grupo, nos daria mucho gusto "
                "que nos lo hagas saber, ya que tenemos algo especial para ti.\n\n"
                "Quedo al pendiente!"
            )),
            "assigned_lead": os.getenv("TEMPLATE_ASSIGNED_LEAD", (
                "Hola {nombre}, como te ha ido?\n\n"
                "Agradecemos mucho que nos hayas contactado a traves de nuestras "
                "plataformas de Facebook o Instagram. Vemos que ya tienes asignado a uno "
                "de nuestros vendedores y queremos asegurarnos de que la atencion este "
                "siendo la adecuada.\n\n"
                "Nos gustaria saber como ha sido tu experiencia hasta ahora y si el "
                "vendedor te ha brindado la informacion y el seguimiento correctos, o si "
                "hay algo adicional en lo que podamos apoyarte directamente.\n\n"
                "Quedamos atentos a tus comentarios y esperamos poder atenderte como te "
                "mereces."
            )),
            "attended_appointment": os.getenv("TEMPLATE_ATTENDED_APPOINTMENT", (
                "Hola {nombre}, agradecemos su asistencia a la cita programada para conocer el vehiculo. "
                "Esperamos que haya sido de su agrado o, en su caso, que haya encontrado "
                "alguna otra opcion que cubra sus expectativas.\n\n"
                "Le avisamos que, al momento de cerrar su operacion, tenemos un regalo "
                "especial para usted. No olvide contactarnos cuando la concrete para "
                "poder entregarselo.\n\n"
                "Asimismo, nos gustaria conocer su experiencia. En una escala del 1 al 5, "
                "donde 5 es excelente, que calificacion nos daria por la atencion "
                "recibida?"
            )),
        }

        # Runtime state
        self._sends_this_hour = 0
        self._hour_reset_at = None
        self._active_campaigns: Dict[str, bool] = {}  # group_id → is_running
        self._paused_campaigns: set = set()

    # ──────────────────────────────────────────────────────────
    # TIME CHECKS
    # ──────────────────────────────────────────────────────────
    def _get_mexico_now(self) -> datetime:
        try:
            tz = pytz.timezone("America/Mexico_City")
            return datetime.now(tz)
        except Exception:
            return datetime.now()

    def _is_within_send_window(self) -> bool:
        """Check if current time is within the allowed send window."""
        now = self._get_mexico_now()
        current_time = now.strftime("%H:%M")
        return self.send_window_start <= current_time <= self.send_window_end

    def _check_hourly_limit(self) -> bool:
        """Check if we're within the hourly send limit."""
        now = self._get_mexico_now()
        current_hour = now.strftime("%Y-%m-%d-%H")

        if self._hour_reset_at != current_hour:
            self._hour_reset_at = current_hour
            self._sends_this_hour = 0

        return self._sends_this_hour < self.max_per_hour

    # ──────────────────────────────────────────────────────────
    # TEMPLATE PERSONALIZATION
    # ──────────────────────────────────────────────────────────
    def _personalize_message(self, contact: Dict) -> str:
        """
        Build personalized message from template + contact data.
        Priority: contact-level template (Monday) > campaign template > default template.
        
        Campaign type is auto-detected from the Monday group title.
        """
        # 1. Per-contact template override (if set in Monday column)
        per_contact_template = contact.get("template", "").strip()
        
        if per_contact_template:
            template = per_contact_template
        else:
            # 2. Detect campaign type from group title
            from src.conversation_logic import detect_campaign_type
            campaign_type = detect_campaign_type(contact.get("group_title", ""))
            
            # 3. Use campaign-specific template or default
            template = self.campaign_templates.get(campaign_type, self.default_template)

        # Extract name (clean Monday item name: "David Rojas | 5213131073749" → "David Rojas")
        raw_name = contact.get("name", "").split("|")[0].strip() or "cliente"
        
        # Build message
        msg = template.replace("{nombre}", raw_name)
        msg = msg.replace("{vehiculo}", contact.get("vehicle", "tu unidad de interés"))
        msg = msg.replace("{bot_name}", self.bot_name)
        msg = msg.replace("{company_name}", self.company_name)
        msg = msg.replace("{company_url}", self.company_url)
        msg = msg.replace("{notas}", contact.get("notes", ""))
        msg = msg.replace("{resumen}", contact.get("resumen", ""))

        # Clean up any unreplaced placeholders
        import re
        msg = re.sub(r'\{mensaje\}', '', msg)
        msg = re.sub(r'\s+', ' ', msg).strip()

        return msg

    # ──────────────────────────────────────────────────────────
    # EVOLUTION API — SEND MESSAGE
    # ──────────────────────────────────────────────────────────
    async def _send_whatsapp(self, phone: str, text: str, http_client: httpx.AsyncClient) -> Dict:
        """
        Send a WhatsApp text message via Evolution API.
        Returns: {"success": True/False, "error": "..."}
        """
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

                return {"success": True, "error": None}

            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return {"success": False, "error": str(e)[:200]}

        return {"success": False, "error": "Max retries exceeded"}

    # ──────────────────────────────────────────────────────────
    # CAMPAIGN EXECUTION
    # ──────────────────────────────────────────────────────────
    async def start_campaign(self, group_id: str, memory_store=None) -> Dict:
        """
        Start sending messages to all pending contacts in a Monday group.
        Runs as a background task with rate limiting.
        
        Returns immediately with campaign status.
        """
        if group_id in self._active_campaigns and self._active_campaigns[group_id]:
            return {"status": "already_running", "group_id": group_id}

        # Remove from paused if it was
        self._paused_campaigns.discard(group_id)
        self._active_campaigns[group_id] = True

        # Launch background task
        asyncio.create_task(self._run_campaign(group_id, memory_store))

        return {"status": "started", "group_id": group_id}

    async def pause_campaign(self, group_id: str) -> Dict:
        """Pause an active campaign."""
        self._paused_campaigns.add(group_id)
        self._active_campaigns[group_id] = False
        return {"status": "paused", "group_id": group_id}

    async def _run_campaign(self, group_id: str, memory_store=None):
        """
        Background task: iterate through pending contacts and send messages.
        """
        logger.info(f"🚀 Campaign started for group: {group_id}")
        sent = 0
        errors = 0

        try:
            contacts = await monday_followup.get_pending_contacts(group_id, limit=200)

            if not contacts:
                logger.info(f"📭 No pending contacts in group {group_id}")
                self._active_campaigns[group_id] = False
                return

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                for contact in contacts:
                    # Check if paused
                    if group_id in self._paused_campaigns:
                        logger.info(f"⏸️ Campaign {group_id} paused after {sent} sends")
                        break

                    # Check send window
                    if not self._is_within_send_window():
                        logger.info(f"🕐 Outside send window ({self.send_window_start}-{self.send_window_end}), stopping")
                        break

                    # Check hourly limit
                    if not self._check_hourly_limit():
                        logger.warning(f"🛑 Hourly limit ({self.max_per_hour}) reached, stopping")
                        break

                    # Get phone
                    phone = normalize_phone(contact.get("phone", ""))
                    if not phone:
                        logger.warning(f"⚠️ Skip contact {contact['item_id']}: invalid phone")
                        await monday_followup.mark_error(contact["item_id"], "Teléfono inválido")
                        errors += 1
                        continue

                    # Personalize message
                    message = self._personalize_message(contact)

                    # Send
                    logger.info(f"📤 Sending to {phone[:6]}*** ({sent + 1}/{len(contacts)})")
                    result = await self._send_whatsapp(phone, message, http_client)

                    if result["success"]:
                        # Update Monday → "Enviado"
                        await monday_followup.update_send_date(contact["item_id"])
                        
                        # Log in SQLite if available
                        if memory_store:
                            await memory_store.log_send(
                                phone, contact.get("group_title", group_id), "sent"
                            )
                        
                        sent += 1
                        self._sends_this_hour += 1
                    else:
                        logger.error(f"❌ Failed to send to {phone[:6]}***: {result['error']}")
                        await monday_followup.mark_error(contact["item_id"], result["error"])
                        
                        if memory_store:
                            await memory_store.log_send(
                                phone, contact.get("group_title", group_id), "error", result["error"]
                            )
                        errors += 1

                    # Anti-ban delay
                    delay = random.uniform(self.delay_min, self.delay_max)
                    logger.debug(f"⏳ Waiting {delay:.1f}s before next send...")
                    await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"❌ Campaign {group_id} crashed: {e}")
        finally:
            self._active_campaigns[group_id] = False
            logger.info(f"✅ Campaign {group_id} finished: {sent} sent, {errors} errors")

    def get_status(self) -> Dict:
        """Get current sender status."""
        return {
            "active_campaigns": {k: v for k, v in self._active_campaigns.items() if v},
            "paused_campaigns": list(self._paused_campaigns),
            "sends_this_hour": self._sends_this_hour,
            "max_per_hour": self.max_per_hour,
            "send_window": f"{self.send_window_start} - {self.send_window_end}",
            "is_within_window": self._is_within_send_window(),
            "delay_range": f"{self.delay_min}-{self.delay_max}s",
        }


# Singleton
sender = SenderService()
