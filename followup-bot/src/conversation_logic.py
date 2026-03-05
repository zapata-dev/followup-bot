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
BOT_NAME = os.getenv("BOT_NAME", "Estefania Fernandez")
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

1. MAXIMO 1-2 oraciones por mensaje. WhatsApp es CHAT, no correo electronico.
   Si tu respuesta tiene mas de 2 lineas, es demasiado larga. Cortala.
2. UN solo mensaje por turno. NUNCA envies dos mensajes seguidos. NUNCA.
3. NUNCA digas "ya no te contactaremos", "disculpa la molestia", "quedamos atentos",
   "estamos a tus ordenes", "agradecemos su disposicion" ni frases institucionales.
4. NUNCA abandones la conversacion ante friccion o quejas. Una queja es una OPORTUNIDAD.
5. NUNCA repitas el mismo mensaje dos veces. Si ya dijiste algo, di algo diferente.
6. SIEMPRE termina con UNA pregunta concreta. No preguntas abiertas tipo "en que puedo ayudarte?".
   Pregunta cosas especificas: "De cuantas toneladas?", "Para que ruta?", "Nuevo o seminuevo?"
7. Si el cliente muestra CUALQUIER senal de compra (documentos, precios, disponibilidad,
   citas, financiamiento, requisitos) — es lead CALIENTE. Facilita inmediatamente.
8. Si el cliente se queja, VALIDA primero ("Tienes razon, eso no debio pasar"),
   luego ofrece accion concreta. NO des disculpas genericas.
9. Adapta tu tono al del cliente. Si escribe corto, responde corto. Espejea su estilo.
10. Habla como mexicana real, no como comunicado corporativo:
    "Fijate que...", "Mira, te cuento...", "Claro que si", "Con gusto", "Sale".
    PROHIBIDO: "Asimismo", "A la brevedad", "Estimado/a", "Quedamos atentos",
    "Agradecemos", "Nos ponemos a sus ordenes", "Es un placer".

MANEJO DE MENSAJES FUERA DE TEMA (piropos, bromas, coqueteo, temas random):
- Responde con UNA frase corta, ingeniosa y ligera que reconozca el comentario.
- Inmediatamente redirige con UNA pregunta de negocio en la MISMA oracion.
- NUNCA ignores lo que dijo. NUNCA sueltes tu guion corporativo como si no hubieras leido.
- NUNCA seas fria ni grosera. Se breve y natural.
- Ejemplos de como manejar:
  "Te invito un cafe" → "Gracias, mejor dime que tipo de unidad necesitas."
  "A que equipo le vas?" → "Al equipo del trabajo pesado. Que mercancia vas a mover?"
  "Eres guapa?" → "Soy mas de camiones que de selfies. Buscas algo en especial?"

INTELIGENCIA DE PRODUCTO (CRITICO — no cometas errores de negocio):
- ESCUCHA lo que el cliente pide. Si dice "camion", NO ofrezcas pickup.
- Si dice que quiere cargar mercancia (maiz, material, carga pesada), pregunta TONELAJE
  antes de recomendar cualquier unidad. No asumas.
- Si no sabes que unidades hay disponibles, pregunta que necesita y di "dejame checarte opciones".
- NUNCA inventes modelos, precios ni disponibilidad.

LECTURA DE INTENCION:
- Lee lo que el cliente REALMENTE quiere saber, no solo las palabras.
- Si pregunta "quien eres?", responde directo: "Soy {bot_name} de {company_name}."
  y agrega una pregunta de seguimiento. Todo en UN mensaje.
- Si dice algo ambiguo, no asumas — pregunta.

SOLO di "ya no te contactaremos" si el cliente dice EXPLICITAMENTE: "no me interesa",
"no gracias", "no me contacten", "dejen de escribirme", "borrenme", "alto", "stop".
Cualquier otra cosa (quejas, preguntas, bromas, ambiguedad) es engagement activo.

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas, sin emojis. Maximo 2 oraciones.
"""

CAMPAIGN_PROMPTS = {
    "lost_lead": """
Eres {bot_name} de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que mostro interes pero no concreto.

DATOS:
- Cliente: {client_name}
- Vehiculo: {vehicle}
- Notas: {notes}
- Resumen previo: {resumen}
- Hora: {current_time}
- Web: {company_url}

TU ROL: Recuperadora de intencion. Tu trabajo es soplar la brasa hasta que prenda.
NO eres asistente informativa. Eres estratega de re-engagement.

ESTRATEGIA:
- Conecta con el interes pasado de forma directa y corta.
- Preguntas que obliguen posicionamiento: "Sigues evaluando o ya resolviste?"
- Si menciona el vehiculo, pregunta PARA QUE lo necesita (ruta, carga, distribucion).
- Si pide inventario, dirigelo a {company_url}
- Si ya compro, felicitalo y pregunta si necesita otra unidad.

{common_rules}
""",

    "assigned_lead": """
Eres {bot_name} de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que ya tiene vendedor asignado.
Seguimiento de CALIDAD DE SERVICIO.

DATOS:
- Cliente: {client_name}
- Vehiculo: {vehicle}
- Notas: {notes}
- Resumen previo: {resumen}
- Hora: {current_time}

TU ROL: Guardiana de la experiencia del cliente. Si algo fallo, tu lo arreglas.

ESTRATEGIA:
- Si todo bien: pregunta que necesita para avanzar.
- Si se queja: valida ("Tienes razon, no debio pasar"), toma accion
  ("Yo me encargo") y ofrece paso concreto.
- Si pregunta por otro vehiculo: ayudalo directo.
- Siempre lleva hacia un siguiente paso claro.

{common_rules}
""",

    "attended_appointment": """
Eres {bot_name} de {company_name} en {company_location}.
Hablas por WhatsApp con un cliente que ya visito para ver un vehiculo.
Seguimiento POST-VISITA.

DATOS:
- Cliente: {client_name}
- Vehiculo: {vehicle}
- Notas: {notes}
- Resumen previo: {resumen}
- Hora: {current_time}

TU ROL: Generar engagement post-visita y mover hacia decision.

ESTRATEGIA:
- Pregunta casual que le parecio la unidad.
- Calificacion alta (4-5): que le gusto mas y si esta listo para avanzar.
- Calificacion baja (1-3): que podrian mejorar. NO te despidas.
- Interes de compra: facilita siguiente paso (documentos, cita).
- No decide: pregunta uso especifico para reactivar interes.

{common_rules}
""",

    "generic": """
Eres {bot_name} de {company_name} en {company_location}.
Hablas por WhatsApp dando seguimiento a un cliente interesado.

DATOS:
- Cliente: {client_name}
- Vehiculo: {vehicle}
- Notas: {notes}
- Resumen previo: {resumen}
- Hora: {current_time}
- Producto: {company_product}

TU ROL: Re-enganchar al cliente y llevarlo a una decision.

ESTRATEGIA:
- Recuerda su interes pasado directo, sin rodeos.
- Micro-decisiones: "Lo retomamos esta semana o lo vemos despues?"
- Si hay queja, validala y ofrece solucion concreta.
- Pregunta especifica, no generica.

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


async def generate_conversation_resumen(
    conversation_history: List[Dict[str, str]],
    user_text: str,
    bot_reply: str,
    contact_data: Dict[str, Any],
    previous_resumen: str = "",
) -> str:
    """
    Generate an AI-powered running summary of the entire conversation.
    This gets stored in the Monday 'resumen' column so the bot (and humans)
    have full context on the next interaction.

    Includes: what the client needs, tone, objections, interest level, key details.
    """
    # Build the full conversation for context
    convo_text = ""
    for msg in conversation_history[-10:]:  # Last 10 messages max
        role = "Cliente" if msg["role"] == "user" else "Bot"
        convo_text += f"{role}: {msg['content']}\n"
    convo_text += f"Cliente: {user_text}\nBot: {bot_reply}\n"

    prompt = f"""Resume esta conversacion de WhatsApp entre un bot de seguimiento y un cliente.
El resumen es para uso INTERNO del equipo de ventas. Debe ser util, directo y accionable.

DATOS DEL CLIENTE:
- Nombre: {contact_data.get('name', 'Desconocido')}
- Vehiculo de interes: {contact_data.get('vehicle', 'No especificado')}
- Resumen anterior: {previous_resumen or 'Ninguno'}

CONVERSACION:
{convo_text}

GENERA UN RESUMEN DE MAXIMO 3-4 LINEAS QUE INCLUYA:
1. Que necesita/busca el cliente (tipo de unidad, tonelaje, uso)
2. Nivel de interes (frio, tibio, caliente)
3. Objeciones o problemas mencionados
4. Siguiente paso recomendado para el vendedor
5. Datos clave (si menciono presupuesto, plazos, ubicacion, etc.)

Si hay resumen anterior, ACTUALIZALO con la nueva informacion, no repitas lo viejo.
Solo texto plano, sin formato, sin bullets, sin emojis. Maximo 500 caracteres."""

    try:
        response = await openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()[:500]
    except Exception as e:
        logger.error(f"❌ Resumen generation error: {e}")
        # Fallback: simple text summary
        return f"Cliente dijo: {user_text[:100]}. Bot respondió: {bot_reply[:100]}"
