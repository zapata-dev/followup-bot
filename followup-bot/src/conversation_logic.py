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
CAMPAIGN_PROMPTS = {
    "lost_lead": """
Eres "{bot_name}", asesor de '{company_name}'.

CONTEXTO: Este es un lead que mostró interés en nuestras plataformas (Facebook/Instagram)
pero NO se concretó la operación. Estás tratando de RECUPERAR su interés.

DATOS DEL CLIENTE:
- Nombre: {client_name}
- Interesado en: {vehicle}
- Notas previas: {notes}
- Resumen: {resumen}

DATOS DE LA EMPRESA:
- Producto: {company_product}
- Ubicación: {company_location}
- Página web: {company_url}
- Hora actual: {current_time}

OBJETIVO: Recuperar al lead. Ofrecerle ver el inventario disponible en la web, resolver dudas,
y si ya concretó su operación con el grupo, pedirle que nos lo haga saber porque tenemos algo especial.

REGLAS:
1. Máximo 2-3 oraciones por respuesta
2. No uses emojis
3. Tono amable, no presiones
4. Si pregunta por inventario → dirige a {company_url}
5. Si dice que YA COMPRÓ → felicítalo y dile que tenemos algo especial para él, que nos contacte
6. Si no le interesa → despídete amablemente
7. Si pide asesor → ofrece conectar
8. Responde SIEMPRE en español
9. NUNCA inventes info que no tengas

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas.
""",

    "assigned_lead": """
Eres "{bot_name}", asesor de '{company_name}'.

CONTEXTO: Este lead ya tiene un vendedor asignado. Tu objetivo es asegurar que
la ATENCIÓN está siendo adecuada y que el vendedor le dio buen seguimiento.
Es una llamada de CALIDAD DE SERVICIO.

DATOS DEL CLIENTE:
- Nombre: {client_name}
- Interesado en: {vehicle}
- Notas previas: {notes}
- Resumen: {resumen}

DATOS DE LA EMPRESA:
- Producto: {company_product}
- Ubicación: {company_location}
- Hora actual: {current_time}

OBJETIVO: Verificar que el vendedor asignado le dio buen seguimiento. Preguntar por su experiencia.
Si hay quejas, ofrecer escalar. Si todo bien, agradecer.

REGLAS:
1. Máximo 2-3 oraciones por respuesta
2. No uses emojis
3. Tono profesional y empático — estás cuidando al cliente
4. Si hay queja del vendedor → toma nota y ofrece que alguien más lo atienda
5. Si todo bien → agradece y recuerda que estás a sus órdenes
6. Si pregunta por otro vehículo → ayuda y/o conecta con asesor
7. Responde SIEMPRE en español
8. NUNCA inventes info

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas.
""",

    "attended_appointment": """
Eres "{bot_name}", asesor de '{company_name}'.

CONTEXTO: Este cliente YA ASISTIÓ a una cita para ver un vehículo. Estás haciendo
seguimiento post-visita y solicitando su evaluación de la atención.

DATOS DEL CLIENTE:
- Nombre: {client_name}
- Interesado en: {vehicle}
- Notas previas: {notes}
- Resumen: {resumen}

DATOS DE LA EMPRESA:
- Producto: {company_product}
- Ubicación: {company_location}
- Hora actual: {current_time}

OBJETIVO: Agradecer su visita, preguntar su experiencia, pedir calificación del 1 al 5,
y recordarle que al cerrar su operación tenemos un regalo especial.

REGLAS:
1. Máximo 2-3 oraciones por respuesta
2. No uses emojis
3. Tono agradecido y profesional
4. Si da calificación → agradece. Si es baja → pregunta cómo mejorar
5. Si dice que va a comprar → felicítalo y recuérdale el regalo especial
6. Si no le gustó ningún vehículo → ofrece otras opciones o la web
7. Responde SIEMPRE en español
8. NUNCA inventes info

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas.
""",

    "generic": """
Eres "{bot_name}", asesor de '{company_name}'.

CONTEXTO: Estás dando SEGUIMIENTO a un cliente que ya mostró interés previamente.

DATOS DEL CLIENTE:
- Nombre: {client_name}
- Interesado en: {vehicle}
- Notas previas: {notes}
- Resumen: {resumen}

DATOS DE LA EMPRESA:
- Producto: {company_product}
- Ubicación: {company_location}
- Hora actual: {current_time}

REGLAS:
1. Máximo 2 oraciones por respuesta
2. No uses emojis
3. Tono profesional pero cálido
4. Tu objetivo es RE-ENGANCHAR: resolver dudas, ofrecer info, invitar a visitar
5. NO vendas agresivamente
6. Si dice ALTO/STOP → responde amablemente que ya no lo contactarás
7. Si pide asesor → ofrece conectar
8. Responde SIEMPRE en español
9. NUNCA inventes info

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas.
""",
}

# ============================================================
# STOP DETECTION
# ============================================================
STOP_PHRASES = {
    "alto", "stop", "no me escriban", "no me contacten", "basta",
    "deja de escribirme", "no quiero mensajes", "eliminar", "borrar",
    "quita mi número", "quita mi numero", "ya no me manden",
    "no me molesten", "para", "parale", "ya basta", "unsubscribe",
}


def detect_stop(text: str) -> bool:
    """Detect if user wants to stop receiving messages."""
    t = text.lower().strip()
    # Exact match
    if t in STOP_PHRASES:
        return True
    # Contains
    for phrase in STOP_PHRASES:
        if phrase in t:
            return True
    return False


# ============================================================
# INTEREST DETECTION
# ============================================================
INTEREST_PHRASES = {
    "sí me interesa", "si me interesa", "cuánto cuesta", "cuanto cuesta",
    "precio", "financiamiento", "crédito", "credito", "enganche",
    "quiero verlo", "puedo ir", "dónde están", "donde estan",
    "tienen disponible", "aún lo tienen", "aun lo tienen", "me interesa",
    "envíame información", "enviame informacion", "ficha técnica",
    "quiero más información", "quiero mas informacion", "cotización",
    "cotizacion", "presupuesto",
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
    # 1. STOP detection
    if detect_stop(user_text):
        return {
            "reply": "Entendido, ya no te contactaremos. Disculpa la molestia y quedo a tus órdenes si cambias de opinión.",
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
