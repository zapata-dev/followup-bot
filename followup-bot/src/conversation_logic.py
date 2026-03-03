"""
Conversation logic for Followup Bot.
Handles AI responses when contacts reply to outbound messages.
Different from Tono-Bot: focused on re-engagement, not initial sale.
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import httpx
import pytz
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ============================================================
# LLM CONFIG
# ============================================================
_LLM_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

openai_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    max_retries=0,
    timeout=_LLM_TIMEOUT,
)
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# ============================================================
# TIME
# ============================================================
def get_mexico_time() -> Tuple[datetime, str]:
    try:
        tz = pytz.timezone("America/Mexico_City")
        now = datetime.now(tz)
        return now, now.strftime("%A %I:%M %p")
    except Exception:
        now = datetime.now()
        return now, now.strftime("%A %I:%M %p")


# ============================================================
# SYSTEM PROMPT — SEGUIMIENTO (configurable via env vars)
# ============================================================
BOT_NAME = os.getenv("BOT_NAME", "Tu asesor")
COMPANY_NAME = os.getenv("COMPANY_NAME", "La empresa")
COMPANY_LOCATION = os.getenv("COMPANY_LOCATION", "la sucursal")
COMPANY_PRODUCT = os.getenv("COMPANY_PRODUCT", "vehículos comerciales")
COMPANY_URL = os.getenv("COMPANY_URL", "")

# ============================================================
# CAMPAIGN TYPES — Each has a different follow-up context
# ============================================================
# The campaign type is derived from the Monday group name.
# Map keywords in group name → campaign type.
CAMPAIGN_TYPE_KEYWORDS = {
    "interesado": "lost_lead",
    "perdido": "lost_lead",
    "sin interes": "lost_lead",
    "fin negativo": "lost_lead",
    "recuperacion": "lost_lead",
    "asignado": "assigned_lead",
    "cotizacion": "assigned_lead",
    "cotización": "assigned_lead",
    "cita atendida": "attended_appointment",
    "cita_atendida": "attended_appointment",
    "encuesta": "attended_appointment",
    "métricas": "attended_appointment",
    "metricas": "attended_appointment",
}


def detect_campaign_type(group_title: str) -> str:
    """Detect campaign type from Monday group name."""
    title_lower = (group_title or "").lower()
    for keyword, ctype in CAMPAIGN_TYPE_KEYWORDS.items():
        if keyword in title_lower:
            return ctype
    return "generic"


# ============================================================
# SYSTEM PROMPTS PER CAMPAIGN TYPE
# ============================================================
_COMMON_RULES = """
REGLAS DE COMUNICACION (CRITICAS — sigue TODAS sin excepcion):

1. MAXIMO 1-2 oraciones por mensaje. WhatsApp es chat, no correo.
2. NUNCA digas "ya no te contactaremos", "disculpa la molestia", "quedamos atentos",
   "estamos a tus ordenes", "agradecemos su disposicion" ni frases institucionales.
3. NUNCA abandones la conversacion ante friccion o quejas. Una queja es una OPORTUNIDAD,
   no una senal de salida. El cliente que se queja aun le importa.
4. NUNCA repitas el mismo mensaje dos veces.
5. SIEMPRE termina con una pregunta concreta o una micro-decision (opcion A o B).
   NO dejes la conversacion abierta con "en que puedo ayudarte?".
6. Si el cliente muestra CUALQUIER senal de compra (pregunta por documentos, precios,
   disponibilidad, citas, financiamiento, requisitos) — eso es un lead CALIENTE.
   Facilita inmediatamente. NUNCA lo despaches.
7. Si el cliente se queja de mala atencion, VALIDA su sentimiento primero ("Tiene razon,
   eso no debio pasar"), luego ofrece accion concreta. NO des disculpas genericas.
8. Adapta tu tono al del cliente. Si escribe corto, responde corto.
   Si es formal, se formal. Si es casual, se casual.
9. Habla como persona, no como comunicado. Usa lenguaje natural mexicano:
   "Fijate que...", "Mira, te cuento...", "Claro que si", "Con gusto".
   Evita: "Asimismo", "A la brevedad", "Estimado", "Quedamos atentos".
10. UN solo mensaje por turno. NUNCA envies dos mensajes seguidos.
11. Responde SIEMPRE en espanol.
12. NUNCA inventes informacion que no tengas.
13. Si no sabes algo, di "dejame confirmarte eso" y ofrece conectar con alguien.
14. SOLO di "ya no te contactaremos" si el cliente dice EXPLICITAMENTE alguna de estas
    frases: "no me interesa", "no gracias", "no me contacten", "dejen de escribirme",
    "borrenme", "alto", "stop". Cualquier otra cosa (quejas, preguntas, ambiguedad)
    es engagement activo — MANTENTE en la conversacion.

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas, sin emojis.
"""

CAMPAIGN_PROMPTS = {
    "lost_lead": """
Eres "{bot_name}" de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que mostro interes pero no concreto su compra.

CLIENTE: {client_name}
VEHICULO DE INTERES: {vehicle}
NOTAS: {notes}
RESUMEN PREVIO: {resumen}
HORA: {current_time}
WEB: {company_url}

TU ROL: Eres un recuperador estrategico de intencion, no un asistente que informa.
Tu trabajo es soplar la brasa hasta que vuelva a prender.

ESTRATEGIA DE CONVERSACION:
- Conecta el presente con el interes pasado: "Cuando preguntaste por el {vehicle}..."
- Usa preguntas que obliguen posicionamiento: "Sigues evaluando opciones o ya resolviste?"
- Muestra conocimiento experto del producto sin dar descuentos.
- Si menciona el vehiculo, pregunta para que lo necesita (ruta larga, distribucion, etc.)
- Si pide info de inventario, dirigelo a {company_url}
- Si ya compro, felicitalo y menciona que tenemos algo especial para el.

{common_rules}
""",

    "assigned_lead": """
Eres "{bot_name}" de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que ya tiene vendedor asignado.
Es seguimiento de CALIDAD DE SERVICIO.

CLIENTE: {client_name}
VEHICULO DE INTERES: {vehicle}
NOTAS: {notes}
RESUMEN PREVIO: {resumen}
HORA: {current_time}

TU ROL: Eres el guardian de la experiencia del cliente. Si algo fallo, tu lo resuelves.

ESTRATEGIA DE CONVERSACION:
- Si dice que todo bien: agradece y pregunta si necesita algo mas para avanzar.
- Si se queja de mala atencion: valida ("Tiene razon, eso no debio pasar"),
  toma responsabilidad ("Yo me encargo de que no vuelva a pasar") y ofrece
  accion concreta ("Le parece si retomamos ahora y lo hacemos bien?").
- Si pregunta por otro vehiculo o tiene dudas: ayudalo directamente.
- Siempre busca llevar la conversacion hacia un siguiente paso claro.

{common_rules}
""",

    "attended_appointment": """
Eres "{bot_name}" de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que ya visito para ver un vehiculo.
Es seguimiento POST-VISITA.

CLIENTE: {client_name}
VEHICULO DE INTERES: {vehicle}
NOTAS: {notes}
RESUMEN PREVIO: {resumen}
HORA: {current_time}

TU ROL: Generar engagement post-visita y mover al cliente hacia decision.

ESTRATEGIA DE CONVERSACION:
- Pregunta que le parecio la unidad de forma casual: "Que tal te parecio el {vehicle}?"
- Si da calificacion alta (4-5): pregunta que fue lo que mas le gusto y si esta
  listo para avanzar.
- Si da calificacion baja (1-3): pregunta especificamente que podrian mejorar,
  NO te despidas. Usa: "Para convertir ese [numero] en un 5, que podriamos mejorar?"
- Si muestra interes de compra: facilita el siguiente paso (documentos, cita, etc.)
  y mencionarle que al cerrar operacion hay un regalo especial.
- Si aun no decide: pregunta "Es para ruta larga o distribucion?" o similar
  para reactivar el interes.

{common_rules}
""",

    "generic": """
Eres "{bot_name}" de {company_name} en {company_location}.
Hablas por WhatsApp dando seguimiento a un cliente que mostro interes previamente.

CLIENTE: {client_name}
VEHICULO DE INTERES: {vehicle}
NOTAS: {notes}
RESUMEN PREVIO: {resumen}
HORA: {current_time}
PRODUCTO: {company_product}

TU ROL: Re-enganchar al cliente y llevarlo hacia una decision.

ESTRATEGIA:
- Recuerda su interes pasado y pregunta si sigue evaluando.
- Usa preguntas de micro-decision: "Lo retomamos esta semana o lo vemos mas adelante?"
- Muestra conocimiento del producto para generar confianza.
- Si hay queja, validala y ofrece solucion concreta.

{common_rules}
""",
}

# ============================================================
# STOP DETECTION
# ============================================================
STOP_PHRASES_EXACT = {
    "alto", "stop", "basta", "ya basta", "unsubscribe", "no", "no gracias",
}

STOP_PHRASES_CONTAINS = {
    "no me escriban", "no me contacten", "deja de escribirme",
    "no quiero mensajes", "quita mi número", "quita mi numero",
    "ya no me manden", "no me molesten", "dejen de escribirme",
    "no me interesa", "borrenme de su lista", "ya no me contacten",
    "dejen de mandarme", "no me manden mas",
}


def detect_stop(text: str) -> bool:
    """
    Detect if user EXPLICITLY wants to stop receiving messages.
    Conservative: only triggers on clear rejection phrases.
    Complaints, questions, and ambiguous messages are NOT stop signals.
    """
    t = text.lower().strip()
    # Remove punctuation for matching
    t_clean = re.sub(r'[¿?¡!.,;:]', '', t).strip()

    # Exact match (short phrases)
    if t_clean in STOP_PHRASES_EXACT:
        return True
    # Contains (longer explicit phrases)
    for phrase in STOP_PHRASES_CONTAINS:
        if phrase in t:
            return True
    return False


# ============================================================
# INTEREST DETECTION
# ============================================================
INTEREST_PHRASES = {
    # Direct interest
    "sí me interesa", "si me interesa", "me interesa", "si quiero",
    "sí quiero", "estoy interesado", "estoy interesada",
    # Price / financing
    "cuánto cuesta", "cuanto cuesta", "precio", "financiamiento",
    "crédito", "credito", "enganche", "mensualidades", "pagos",
    "cotización", "cotizacion", "presupuesto",
    # Purchase intent (CRITICAL — these are HOT signals, never dismiss them)
    "documentos", "requisitos", "papeles", "qué necesito para comprar",
    "que necesito para comprar", "como le hago para comprar",
    "quiero comprar", "listo para comprar", "vamos a cerrar",
    # Visit / availability
    "quiero verlo", "puedo ir", "dónde están", "donde estan",
    "tienen disponible", "aún lo tienen", "aun lo tienen",
    "quiero ir a verlo", "puedo visitarlos", "horarios",
    # Info request
    "envíame información", "enviame informacion", "ficha técnica",
    "ficha tecnica", "quiero más información", "quiero mas informacion",
    "me mandas info", "mandame info", "pasame info",
    # Re-engagement signals
    "quiero retomar", "sigamos", "vamos a retomar", "me interesa retomar",
    "dispuesto a intentar", "quiero intentar de nuevo",
}


def detect_interest(text: str) -> bool:
    """Detect if user shows buying interest."""
    t = text.lower().strip()
    for phrase in INTEREST_PHRASES:
        if phrase in t:
            return True
    return False


# ============================================================
# HANDOFF DETECTION
# ============================================================
HANDOFF_PHRASES = {
    "quiero hablar con alguien", "pásame con un asesor", "pasame con un asesor",
    "quiero hablar con una persona", "asesor humano", "persona real",
    "necesito hablar con alguien", "me pueden llamar", "llámame", "llamame",
    "quiero agendar", "quiero una cita", "puedo ir a verlo",
}


def detect_handoff(text: str) -> bool:
    """Detect if user wants to talk to a human."""
    t = text.lower().strip()
    for phrase in HANDOFF_PHRASES:
        if phrase in t:
            return True
    return False


# ============================================================
# MAIN HANDLER
# ============================================================
async def handle_reply(
    user_text: str,
    contact_data: Dict[str, Any],
    conversation_history: List[Dict[str, str]],
    campaign_type: str = "generic",
) -> Dict[str, Any]:
    """
    Handle a reply from a followup contact.
    
    Args:
        user_text: The message text from the user
        contact_data: Dict with name, vehicle, notes, resumen from Monday
        conversation_history: List of {"role": "user"|"assistant", "content": "..."} dicts
        campaign_type: "lost_lead" | "assigned_lead" | "attended_appointment" | "generic"
    
    Returns:
        {
            "reply": "Bot response text",
            "action": "continue" | "stop" | "handoff" | "interested" | "appointment",
            "summary": "Brief summary of the exchange"
        }
    """
    # 1. STOP detection — only on EXPLICIT rejection
    if detect_stop(user_text):
        return {
            "reply": "Entendido, no te vuelvo a escribir. Si en algun momento necesitas algo, aqui estamos.",
            "action": "stop",
            "summary": "Cliente pidió no ser contactado",
        }

    # 2. Determine action hints
    action = "continue"
    if detect_handoff(user_text):
        action = "handoff"
    elif detect_interest(user_text):
        action = "interested"

    # 3. Build system prompt with campaign-specific template
    _, time_str = get_mexico_time()
    
    prompt_template = CAMPAIGN_PROMPTS.get(campaign_type, CAMPAIGN_PROMPTS["generic"])
    
    system_prompt = prompt_template.format(
        bot_name=BOT_NAME,
        company_name=COMPANY_NAME,
        client_name=contact_data.get("name", "cliente").split("|")[0].strip(),
        vehicle=contact_data.get("vehicle", "unidad de interés"),
        last_contact=contact_data.get("last_contact", "reciente"),
        notes=contact_data.get("notes", "Sin notas"),
        resumen=contact_data.get("resumen", "Sin resumen previo"),
        company_product=COMPANY_PRODUCT,
        company_location=COMPANY_LOCATION,
        company_url=COMPANY_URL,
        current_time=time_str,
        common_rules=_COMMON_RULES,
    )

    # 4. Build messages for GPT
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (truncated to last ~4000 chars)
    total_chars = 0
    for msg in reversed(conversation_history):
        msg_len = len(msg.get("content", ""))
        if total_chars + msg_len > 4000:
            break
        messages.insert(1, msg)
        total_chars += msg_len

    # Add current user message
    messages.append({"role": "user", "content": user_text})

    # 5. Call GPT
    try:
        response = await openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            max_tokens=200,
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ GPT error: {e}")
        reply = f"Gracias por tu respuesta. Te paso con un asesor para darte mejor atención."
        action = "handoff"

    # 6. Post-process: if GPT response hints at handoff
    reply_lower = reply.lower()
    if any(w in reply_lower for w in ["te paso con", "te comunico con", "un asesor te"]):
        if action == "continue":
            action = "handoff"

    # 7. Generate brief summary
    summary = _summarize_exchange(user_text, reply, action)

    return {
        "reply": reply,
        "action": action,
        "summary": summary,
    }


def _summarize_exchange(user_text: str, bot_reply: str, action: str) -> str:
    """Generate a brief summary of the exchange for Monday notes."""
    t = user_text[:100]
    if action == "stop":
        return f"STOP: Cliente pidió no ser contactado. Dijo: '{t}'"
    if action == "handoff":
        return f"HANDOFF: Cliente pidió asesor. Dijo: '{t}'"
    if action == "interested":
        return f"INTERESADO: Cliente mostró interés. Dijo: '{t}'"
    return f"Respondió: '{t}'"
