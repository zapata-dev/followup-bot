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
# LLM CONFIG — Gemini primary, OpenAI fallback
# ============================================================
_LLM_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Primary: Gemini 2.5 Flash Lite via OpenAI-compatible endpoint
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

gemini_client = AsyncOpenAI(
    api_key=_GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    max_retries=0,
    timeout=_LLM_TIMEOUT,
) if _GEMINI_API_KEY else None

# Fallback: OpenAI
openai_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    max_retries=0,
    timeout=_LLM_TIMEOUT,
)
FALLBACK_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


async def _llm_completion(messages: list, max_tokens: int = 200, temperature: float = 0.7) -> str:
    """Call LLM with Gemini primary → OpenAI fallback."""
    # Try Gemini first
    if gemini_client:
        try:
            response = await gemini_client.chat.completions.create(
                model=_GEMINI_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            logger.debug(f"✅ Gemini response OK")
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Gemini failed, falling back to OpenAI: {e}")

    # Fallback to OpenAI
    response = await openai_client.chat.completions.create(
        model=FALLBACK_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    logger.debug(f"✅ OpenAI fallback response OK")
    return response.choices[0].message.content.strip()


# ============================================================
# TIME
# ============================================================
_DIAS_SEMANA = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo",
}


def get_mexico_time() -> Tuple[datetime, str]:
    try:
        tz = pytz.timezone("America/Mexico_City")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    dia_en = now.strftime("%A")
    dia_es = _DIAS_SEMANA.get(dia_en, dia_en)
    time_str = f"{dia_es} {now.strftime('%d/%m/%Y')} {now.strftime('%I:%M %p')}"
    return now, time_str


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
    "servicio": "customer_service",
    "atencion": "customer_service",
    "atención": "customer_service",
    "customer service": "customer_service",
    "calidad": "customer_service",
    "seguimiento vendedor": "customer_service",
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

0. PRESENTACION: Si el primer mensaje de la conversacion (el template) NO incluye
   tu nombre, presentate NATURALMENTE al inicio de tu primera respuesta.
   Ejemplo: "Soy {bot_name}, fijate que vi que te interesa el [vehiculo]..."
   NO lo hagas si ya te presentaste antes. Fusiona la presentacion con tu respuesta
   de forma natural, no como algo separado.

1. MAXIMO 1-2 oraciones por mensaje. WhatsApp es CHAT, no correo electronico.
   Si tu respuesta tiene mas de 2 lineas, es demasiado larga. Cortala.
   Escribe TODO CORRIDO, sin saltos de linea ni parrafos separados.
   Los humanos en WhatsApp NO usan Enter para hacer parrafos bonitos.
2. UN solo mensaje por turno. NUNCA envies dos mensajes seguidos. NUNCA.
3. NUNCA digas "ya no te contactaremos", "disculpa la molestia", "quedamos atentos",
   "estamos a tus ordenes", "agradecemos su disposicion" ni frases institucionales.
4. NUNCA abandones la conversacion ante friccion o quejas. Una queja es una OPORTUNIDAD.
5. NUNCA repitas el mismo mensaje dos veces. Si ya dijiste algo, di algo diferente.
6. SIEMPRE termina con UNA pregunta concreta. No preguntas abiertas tipo "en que puedo ayudarte?".
   Pregunta cosas especificas: "De cuantas toneladas?", "Para que ruta?", "Nuevo o seminuevo?"
7. Si el cliente muestra CUALQUIER senal de compra (documentos, precios, disponibilidad,
   citas, visitas, financiamiento, requisitos, pide cotizacion, pide info de precios)
   — es lead CALIENTE. Haz HANDOFF inmediatamente (pregunta sucursal).
   NO intentes facilitar tu misma. NO des precios ni cotizaciones. Transfiere al asesor.
8. Si el cliente se queja, VALIDA primero ("Tienes razon, eso no debio pasar"),
   luego ofrece accion concreta. NO des disculpas genericas.
   Si la queja es seria (mal vendedor, estafa, mal trato), SIEMPRE transfiere
   al gerente/asesor. NUNCA intentes resolver tu sola una queja grave.
9. Adapta tu tono al del cliente. Si escribe corto, responde corto. Espejea su estilo.
   Usa "calor" latino: se cercana, directa, de confianza. El trato en la industria
   de camiones es de negocios pero con mucha camaraderia.
10. Habla como mexicana real, no como comunicado corporativo:
    "Fijate que...", "Mira, te cuento...", "Claro que si", "Con gusto", "Sale".
    PROHIBIDO: "Asimismo", "A la brevedad", "Estimado/a", "Quedamos atentos",
    "Agradecemos", "Nos ponemos a sus ordenes", "Es un placer".

REGLA #1 DE HUMANIZACION — ESCUCHA ANTES DE HABLAR (CRITICA):
- SIEMPRE lee y responde PRIMERO a lo que el cliente acaba de decir.
- NUNCA ignores el mensaje del cliente para seguir tu guion de ventas.
- Si el cliente dice algo, tu respuesta DEBE reconocer lo que dijo antes de hacer
  cualquier pregunta o seguir con la conversacion.
- Ejemplos de lo que NUNCA debes hacer:
  * Cliente: "Estoy manejando" → Tu: "Sigues interesado en el Cascadia?" (MAL)
  * Cliente: "Ya compre uno" → Tu: "Todavia buscas opciones?" (MAL)
  * Cliente: "Me atiende la Srta Flor" → Tu: "Te ofrezco un Cascadia 2016" (MAL)

DETECCION DE CONTEXTO (CRITICA — responde segun la situacion):
- CLIENTE OCUPADO ("estoy manejando", "ahorita no puedo", "estoy en junta",
  "luego te marco", "despues hablamos", "no puedo ahorita"):
  Responde ULTRA CORTO: "Dale, con cuidado, aqui estoy cuando puedas."
  o "Perfecto, sin prisa, aqui andamos." NO le mandes mas preguntas ni info.
  NO intentes seguir vendiendo. Respeta su tiempo.
- CLIENTE YA COMPRO ("ya compre", "ya tengo camion", "ya resolvi", "ya lo consegui",
  "compre con un particular", "compre por otro lado"):
  Responde con FELICITACION genuina y pregunta curiosa:
  "Que buena noticia! Y que modelo te llevaste?" o "Felicidades! Como te ha ido con la unidad?"
  NO sigas vendiendo. NO preguntes si sigue buscando (ya te dijo que compro).
  Si ya lo felicitaste, cierra amablemente: "Si en algun momento necesitas algo, aqui andamos."
- CLIENTE ATENDIDO POR ALGUIEN MAS ("me atiende [nombre]", "ya hable con [nombre]",
  "el vendedor [nombre] me esta ayudando"):
  Reconoce: "Ah perfecto, estas con [nombre], te dejo en buenas manos."
  o "Sale, si necesitas algo mas aqui andamos." NO intentes venderle por encima.
- CLIENTE PREGUNTA ALGO ESPECIFICO ("tienen tractos con fierros grandes?",
  "tienen caja refrigerante?", "tienen de 5 toneladas?"):
  RESPONDE LA PREGUNTA PRIMERO. Despues haz tu pregunta de seguimiento.
  Ejemplo: "Si tenemos! Que tipo de carga vas a mover?" NO ignores su pregunta
  para ofrecer otro modelo.

NO REPITAS COMO ROBOT (CRITICA):
- NUNCA repitas el nombre completo del vehiculo en cada mensaje.
  Primera vez: "Freightliner Cascadia 2020". Despues: "el Cascadia", "la unidad",
  "ese modelo", "el tracto", "el camion". Varia SIEMPRE.
- NUNCA te vuelvas a presentar si ya lo hiciste. Con una vez al inicio basta.
  Despues ve directo al punto, sin "Hola soy Estefania de..." otra vez.
- NUNCA repitas el horario de atencion. Si ya lo mencionaste, no lo vuelvas a decir.
  Solo mencionalo si el cliente PREGUNTA por horarios o si se esta agendando cita.
- NUNCA saludes de nuevo si ya saludaste. Despues del primer "Hola", ve directo.

MANEJO DE MENSAJES FUERA DE TEMA (piropos, bromas, coqueteo, temas random):
- Responde con UNA frase corta, ingeniosa y ligera que reconozca el comentario.
- Inmediatamente redirige con UNA pregunta de negocio en la MISMA oracion.
- NUNCA ignores lo que dijo. NUNCA sueltes tu guion corporativo como si no hubieras leido.
- NUNCA seas fria ni grosera. Se breve y natural.
- Ejemplos de como manejar:
  "Te invito un cafe" → "Gracias, mejor dime que tipo de unidad necesitas."
  "A que equipo le vas?" → "Al equipo del trabajo pesado. Que mercancia vas a mover?"
  "Eres guapa?" → "Soy mas de camiones que de selfies. Buscas algo en especial?"

USO DE DATOS DEL CLIENTE (CRITICO — personaliza SIEMPRE):
- Si tienes el vehiculo de interes, mencionalo por nombre la PRIMERA vez.
  Despues VARIA: "el Cascadia", "la unidad", "ese modelo", "el tracto", "el camion".
  NUNCA repitas "Freightliner Cascadia 2020" en cada mensaje.
- Si el campo Vehiculo dice "Sin dato" o esta vacio, NO inventes ningun modelo.
  Pregunta: "Que unidad te interesa?" o "Que tipo de camion buscas?"
- Si tienes notas o resumen previo, USALOS. No preguntes cosas que ya sabes.
- Si el primer mensaje fue muy largo o generico, COMPENSA siendo ultra-directo y corto.

INTELIGENCIA DE PRODUCTO (CRITICO — no cometas errores de negocio):
- ESCUCHA lo que el cliente pide. Si dice "camion", NO ofrezcas pickup.
- Si dice que quiere cargar mercancia (maiz, material, carga pesada), pregunta TONELAJE
  antes de recomendar cualquier unidad. No asumas.
- Si no sabes que unidades hay disponibles, pregunta que necesita y di "dejame checarte opciones".
- NUNCA inventes modelos, precios ni disponibilidad.

PRECIOS, COTIZACIONES Y FINANCIAMIENTO (CRITICO — NUNCA LO VIOLES):
- NUNCA menciones precios, montos, cifras de dinero, cotizaciones, promociones,
  enganches, mensualidades, plazos de financiamiento ni condiciones de pago.
- NUNCA digas cosas como "$990,000", "precio especial", "sin enganche",
  "financiamiento a 24 meses", "promocion", "descuento" ni nada similar.
- Si el cliente pide info de precios, cotizacion o financiamiento, NO le des datos.
  En su lugar, lleva la conversacion al flujo de HANDOFF (pregunta sucursal).
  Ejemplo: "Con gusto, para darte la info mas actualizada dime en que sucursal
  te gustaria que te atendieran." y lista las opciones.
- TU NO TIENES ACCESO A PRECIOS. Cualquier precio que generes es INVENTADO y FALSO.
  Solo un asesor humano puede dar precios reales.

LECTURA DE INTENCION:
- Lee lo que el cliente REALMENTE quiere saber, no solo las palabras.
- Si pregunta "quien eres?", responde directo: "Soy {bot_name} de {company_name}."
  y agrega una pregunta de seguimiento. Todo en UN mensaje.
- Si dice algo ambiguo, no asumas — pregunta.

SOLO di "ya no te contactaremos" si el cliente dice EXPLICITAMENTE: "no me interesa",
"no gracias", "no me contacten", "dejen de escribirme", "borrenme", "alto", "stop".
Cualquier otra cosa (quejas, preguntas, bromas, ambiguedad) es engagement activo.

MENSAJES MULTIMEDIA (audio, foto, video, documento, sticker, ubicacion):
- Los mensajes de audio, fotos y videos se procesan automaticamente.
  El contenido aparecera entre corchetes, por ejemplo:
  [Mensaje de voz transcrito: "quiero ver el camion el sabado"]
  [Foto del cliente — contenido: cotizacion de un vehiculo]
  [Video del cliente — contenido: recorrido de un camion en carretera]
- Cuando recibas un mensaje transcrito/descrito, responde AL CONTENIDO como si el
  cliente lo hubiera escrito. NO menciones que fue un audio o una foto.
  Ejemplo: si el audio dice "quiero ver el camion", responde sobre la visita, no digas
  "escuche tu audio".
- Si el mensaje dice "[El cliente envió un mensaje de voz]" SIN transcripcion,
  significa que no se pudo procesar. Pide que lo escriba por texto.
- Si el mensaje dice "[El cliente envió una foto]" SIN descripcion,
  pide que describa que es.
- Para documentos sin procesar, pide que explique de que se trata.
- Stickers y ubicaciones: ignora y continua la conversacion normalmente.
- Si el mensaje incluye un texto/caption ademas del medio, responde al texto.

REGLA DE ORO — HANDOFF (CRITICO — NUNCA LA VIOLES):
- Tu trabajo es CALENTAR el lead, NO cerrar la venta ni agendar citas ni visitas.
- NUNCA intentes agendar una cita, llamada o visita tu misma.
- NUNCA confirmes una hora de visita. NUNCA digas "te espero a las X".
- NUNCA digas "pasa a la oficina", "te espero en la sucursal" ni nada similar.
  TU NO ESTAS EN NINGUNA OFICINA. Eres un bot, no puedes recibir a nadie.
- NUNCA des direcciones exactas de sucursales.
- NUNCA inventes horarios de citas (ej: "a las 5:30 PM", "manana a las 10").
  NO digas: "Que dia y hora te funciona?", "Agendamos una llamada?",
  "Te espero el martes a las 10am", "Ven a vernos el jueves",
  "Lo retomamos esta semana o la proxima?", "Lo vemos mas adelante?",
  "Esta semana o prefieres despues?" (esto es agendar disfrazado).
- NUNCA le des al cliente la opcion de POSPONER. No ofrezcas "verlo despues"
  ni "la proxima semana". Siempre lleva hacia ACCION AHORA: conectar con asesor,
  preguntar que necesita, o ir al flujo de sucursal.

CUANDO EL CLIENTE MUESTRA INTENCION DE COMPRA/VISITA — FLUJO DE SUCURSAL:
- Cuando el cliente quiere IR A VER la unidad, VISITAR, agendar cita, cotizar,
  cerrar compra, pide hablar con alguien, que lo llamen, o dice cualquier cosa
  POSITIVA hacia la compra:
  1. PRIMERO pregunta en cual sucursal quiere ser atendido.
  2. Listale las opciones de forma natural:
     "Tenemos puntos de venta en Tlalnepantla, Texcoco, Cuautitlan, Queretaro,
     Celaya, Leon, en Guadalajara (Occidente y Mariano Otero), Tampico y Monterrey.
     Dime cual te queda mas comoda y con gusto vemos el tema contigo."
  3. Si el cliente dice solo "Guadalajara" sin especificar, pregunta:
     "En Guadalajara tenemos Occidente y Mariano Otero, cual te queda mejor?"
  4. Una vez que el cliente CONFIRMA la sucursal, ENTONCES haz el handoff:
     "Perfecto! En breve nos ponemos de acuerdo para atenderte en [sucursal]."
     o variaciones naturales similares.
- Si el cliente se queja de un vendedor o tiene miedo de estafa,
  haz handoff DIRECTO sin preguntar sucursal.

HORARIO DE ATENCION (referencia, NO para que TU agendes):
- Lunes a Viernes: 9:00 AM a 6:00 PM
- Sabados: 9:00 AM a 2:00 PM
- Domingos: CERRADO
- La hora y fecha actuales estan en el campo "Hora" arriba. USALAS para saber que dia y hora es.
- NUNCA menciones el horario en tu respuesta a menos que el cliente PREGUNTE
  explicitamente "a que hora atienden?", "cual es su horario?" o similar.
  El horario se maneja automaticamente fuera de tu respuesta, NO lo incluyas tu.
- Si el cliente pregunta por horarios, informale y transfiere al asesor para la cita.
- NO inventes disponibilidad de horarios especificos (ej: "a las 10:30 hay espacio").

FORMATO: Solo texto del mensaje. Sin prefijos, sin comillas, sin emojis. Maximo 2 oraciones.
Todo corrido, sin saltos de linea.
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
- Si responde positivo, ofrece conectar con asesor: "Te conecto con alguien para darte detalles?"
- NUNCA le des opcion de posponer ("lo vemos despues", "la proxima semana").
- Si menciona el vehiculo, pregunta PARA QUE lo necesita (ruta, carga, distribucion).
- Si pide inventario, dirigelo a {company_url}
- Si ya compro, felicitalo genuinamente y pregunta que modelo se llevo.
  NO sigas ofreciendo. Si ya lo felicitaste, cierra con "aqui andamos si necesitas algo".
- Si esta ocupado (manejando, en junta, etc), responde corto y deja que el te busque.

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
- Si dice que ya lo atiende alguien, reconocelo y no insistas.
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

    "customer_service": """
Eres {bot_name} de {company_name} en {company_location}.
Hablas por WhatsApp para dar seguimiento de SERVICIO AL CLIENTE.
El cliente ya fue atendido por un vendedor. Tu trabajo es verificar calidad.

DATOS:
- Cliente: {client_name}
- Vehiculo de interes: {vehicle}
- Notas: {notes}
- Resumen previo: {resumen}
- Hora: {current_time}

TU ROL: Seguimiento de calidad. Eres cercana, directa y empática.

REGLA CRITICA — USA LOS DATOS:
- SIEMPRE menciona el vehiculo ESPECIFICO del cliente usando el dato de arriba.
  NUNCA digas "vehiculo comercial", "tu unidad" ni nada generico si tienes el dato.
- Si el campo Vehiculo dice "Sin dato", NO inventes ningun modelo. Pregunta cual le interesa.
- Si hay notas o resumen, usalo para contextualizar. No preguntes cosas que ya sabes.
- Si el resumen dice que ya cotizo, no preguntes "que te interesa?" — pregunta
  "como te fue con la cotizacion del [vehiculo]?"

ESTRATEGIA:
- Pregunta directa sobre la atencion que recibio con ESE vehiculo especifico.
- Si todo bien: pregunta que necesita para dar el siguiente paso.
- Si se queja: valida ("Tienes razon"), toma accion ("Yo me encargo").
- Si ya no le interesa ese vehiculo, pregunta que cambio.
- Si dice que lo atiende otra persona, reconocelo y no insistas.
- Siempre lleva hacia un siguiente paso claro.

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
- Si responde positivo o saluda, pregunta sobre su interes en el vehiculo
  y ofrece conectar con un asesor: "Te conecto con un asesor para que te de los detalles?"
- NUNCA le des la opcion de posponer ("lo vemos despues", "la proxima semana").
- Si ya compro, felicitalo y cierra amablemente. No sigas vendiendo.
- Si esta ocupado, respeta su tiempo y responde corto.
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
# BUSY / ALREADY-BOUGHT / ATTENDED-BY-SOMEONE DETECTION
# ============================================================
BUSY_PHRASES = {
    "estoy manejando", "estoy conduciendo", "voy manejando", "voy al volante",
    "ahorita no puedo", "no puedo ahorita", "ahorita estoy ocupado",
    "ahorita estoy ocupada", "estoy en junta", "estoy en reunion",
    "estoy en una reunión", "estoy en una reunion", "estoy en el trabajo",
    "estoy trabajando", "luego te marco", "despues te marco",
    "después te marco", "despues hablamos", "después hablamos",
    "le marco despues", "le marco después", "marco despues",
    "marco después", "al rato te marco", "mas tarde",
    "más tarde", "no puedo hablar", "estoy ocupado", "estoy ocupada",
    "en un momento", "ahorita no", "luego hablamos",
}

ALREADY_BOUGHT_PHRASES = {
    "ya compre", "ya compré", "ya lo compre", "ya lo compré",
    "ya tengo camion", "ya tengo camión", "ya tengo unidad",
    "ya resolvi", "ya resolví", "ya lo consegui", "ya lo conseguí",
    "compre con un particular", "compré con un particular",
    "compre por otro lado", "compré por otro lado",
    "compre en otro lado", "compré en otro lado",
    "ya adquiri", "ya adquirí", "ya tengo mi camion",
    "ya tengo mi camión", "ya me lo lleve", "ya me lo llevé",
    "ya hice la compra", "ya concrete", "ya concreté",
    "ya se concreto", "ya se concretó",
}

ATTENDED_BY_PATTERNS = [
    r"me atiende (?:la |el )?(?:sra?\.?|srta\.?|lic\.?|ing\.?|señor[ia]?|sr\.?)?\s*\w+",
    r"ya (?:me |)(?:atiende|atendio|atendió|atienden|esta atendiendo|está atendiendo)\s+\w+",
    r"(?:el |la )?(?:vendedor[a]?|asesor[a]?|ejecutiv[oa])\s+\w+\s+(?:me |ya me |)(?:atiende|atendio|atendió|esta ayudando|está ayudando)",
    r"ya hable con (?:el |la )?\w+",
    r"ya hablé con (?:el |la )?\w+",
    r"(?:el |la )\w+ me (?:esta|está) (?:ayudando|atendiendo)",
]


def detect_busy(text: str) -> bool:
    """Detect if client is busy and can't talk now."""
    t = text.lower().strip()
    for phrase in BUSY_PHRASES:
        if phrase in t:
            return True
    return False


def detect_already_bought(text: str) -> bool:
    """Detect if client already purchased a vehicle."""
    t = text.lower().strip()
    for phrase in ALREADY_BOUGHT_PHRASES:
        if phrase in t:
            return True
    return False


def detect_attended_by(text: str) -> Optional[str]:
    """
    Detect if client says they are being attended by someone else.
    Returns the matched text if found, None otherwise.
    """
    t = text.lower().strip()
    for pattern in ATTENDED_BY_PATTERNS:
        m = re.search(pattern, t)
        if m:
            return m.group(0)
    return None


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
    # Visit / availability (CRITICAL — client wants to go see the vehicle)
    "quiero verlo", "puedo ir", "dónde están", "donde estan",
    "tienen disponible", "aún lo tienen", "aun lo tienen",
    "tienes disponible", "hay disponible", "disponibles tienes",
    "qué unidad", "que unidad", "qué unidades", "que unidades",
    "qué tienen", "que tienen", "qué tienes", "que tienes",
    "quiero ir a verlo", "puedo visitarlos", "horarios",
    "pendiente de ir", "quiero ir", "se puede hoy",
    "puedo ir hoy", "voy para alla", "voy para allá",
    "ya llegue", "ya llegué", "ya estoy aqui", "ya estoy aquí",
    "cuando puedo ir", "cuándo puedo ir", "a que hora puedo ir",
    "hoy de una vez", "lo retomamos", "retomar",
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
    "quiero ir a verlos", "donde estan ubicados", "donde los encuentro",
    "quiero cerrar", "vamos a cerrar el trato", "listo para firmar",
    "donde firmo", "me estafaron", "me quiso estafar",
}


def detect_handoff(text: str) -> bool:
    """Detect if user wants to talk to a human."""
    t = text.lower().strip()
    for phrase in HANDOFF_PHRASES:
        if phrase in t:
            return True
    return False


# ============================================================
# BRANCH/LOCATION DETECTION
# ============================================================
BRANCH_LOCATIONS = {
    "tlalnepantla": "Tlalnepantla",
    "texcoco": "Texcoco",
    "cuautitlan": "Cuautitlán",
    "cuautitlán": "Cuautitlán",
    "queretaro": "Querétaro",
    "querétaro": "Querétaro",
    "celaya": "Celaya",
    "leon": "León",
    "león": "León",
    "guadalajara occidente": "Guadalajara (Occidente)",
    "occidente": "Guadalajara (Occidente)",
    "guadalajara mariano": "Guadalajara (Mariano Otero)",
    "mariano otero": "Guadalajara (Mariano Otero)",
    "tampico": "Tampico",
    "monterrey": "Monterrey",
}

# Short aliases for common abbreviations
BRANCH_ALIASES = {
    "tlalne": "Tlalnepantla",
    "texco": "Texcoco",
    "cuauti": "Cuautitlán",
    "qro": "Querétaro",
    "quere": "Querétaro",
    "mty": "Monterrey",
    "monte": "Monterrey",
}

# All valid canonical location labels (for post-processing detection)
VALID_LOCATIONS = set(BRANCH_LOCATIONS.values()) | set(BRANCH_ALIASES.values())


def detect_location(text: str) -> Optional[str]:
    """
    Detect if user message contains a branch/location name.
    Returns the canonical Monday label name, or None.
    Note: bare 'guadalajara' does NOT match — bot should ask Occidente or Mariano Otero.
    """
    t = text.lower().strip()
    t_clean = re.sub(r'[¿?¡!.,;:\-()"]', '', t).strip()

    # Check exact/substring matches (longer keys first to avoid partial matches)
    for key in sorted(BRANCH_LOCATIONS.keys(), key=len, reverse=True):
        if key in t_clean:
            return BRANCH_LOCATIONS[key]

    # Check short aliases
    for alias, canonical in BRANCH_ALIASES.items():
        # Use word boundary-ish check for short aliases to avoid false positives
        if re.search(r'\b' + re.escape(alias) + r'\b', t_clean):
            return canonical

    return None


# ============================================================
# MAIN HANDLER
# ============================================================
async def handle_reply(
    user_text: str,
    contact_data: Dict[str, Any],
    conversation_history: List[Dict[str, str]],
    campaign_type: str = "generic",
    pending_location: bool = False,
) -> Dict[str, Any]:
    """
    Handle a reply from a followup contact.

    Args:
        user_text: The message text from the user
        contact_data: Dict with name, vehicle, notes, resumen from Monday
        conversation_history: List of {"role": "user"|"assistant", "content": "..."} dicts
        campaign_type: "lost_lead" | "assigned_lead" | "attended_appointment" | "generic"
        pending_location: True if we previously asked for location and are waiting for response

    Returns:
        {
            "reply": "Bot response text",
            "action": "continue" | "stop" | "handoff" | "interested" | "pending_location",
            "summary": "Brief summary of the exchange",
            "location": "Canonical branch name" (optional, only when location detected)
        }
    """
    # 1. STOP detection — only on EXPLICIT rejection
    if detect_stop(user_text):
        client_name = contact_data.get("name", "").split("|")[0].strip()
        stop_name = f", {client_name}" if client_name else ""
        return {
            "reply": f"Entiendo perfectamente{stop_name}. Dejamos tu solicitud en pausa por ahora. Si en el futuro necesitas algo, ya sabes donde encontrarnos. Que tengas una excelente semana!",
            "action": "stop",
            "summary": "Cliente pidió no ser contactado",
        }

    # 1b. BUSY detection — respond ultra-short, don't push
    is_busy = detect_busy(user_text)
    has_bought = detect_already_bought(user_text)
    attended_by = detect_attended_by(user_text)

    if is_busy:
        client_name = contact_data.get("name", "").split("|")[0].strip()
        # Pick a natural short response
        busy_responses = [
            "Dale, con cuidado! Aqui estoy cuando puedas.",
            "Perfecto, sin prisa! Aqui andamos.",
            "Sale, cuando te desocupes con confianza.",
            "Claro que si, aqui te espero sin presion.",
        ]
        import random
        reply = random.choice(busy_responses)
        return {
            "reply": reply,
            "action": "continue",
            "summary": f"Cliente ocupado ({user_text[:60]}), bot respetó su tiempo",
        }

    if has_bought:
        # Don't try to sell — congratulate and show curiosity
        # Let the AI handle this with the context hint
        pass  # Handled via context_hint below

    if attended_by:
        # Client is being helped by someone else — acknowledge and back off
        client_name = contact_data.get("name", "").split("|")[0].strip()
        attended_responses = [
            f"Ah perfecto, estas en buenas manos! Si necesitas algo mas, aqui andamos.",
            f"Sale, que bueno que ya te estan atendiendo! Cualquier cosa aqui estamos.",
            f"Perfecto, te dejo con ellos! Si ocupas algo mas, con confianza.",
        ]
        import random
        reply = random.choice(attended_responses)
        return {
            "reply": reply,
            "action": "continue",
            "summary": f"Cliente atendido por alguien más ({attended_by}), bot se retiró",
        }

    # 2. Determine action hints + location detection
    action = "continue"
    detected_location = detect_location(user_text)
    _has_interest = detect_interest(user_text)
    _has_handoff = detect_handoff(user_text)

    logger.info(
        "🔎 Detection for '%s': interest=%s handoff=%s location=%s pending_location=%s",
        user_text[:60],
        _has_interest,
        _has_handoff,
        detected_location,
        pending_location,
    )

    if pending_location:
        # We were waiting for a location response
        if detected_location:
            action = "handoff"
        else:
            # No location detected — AI will re-ask naturally
            action = "pending_location"
    elif _has_handoff:
        if detected_location:
            action = "handoff"  # Direct handoff — location in same message
        else:
            action = "pending_location"  # Need to ask for location first
    elif _has_interest:
        if detected_location:
            action = "handoff"  # Interest + location → go straight to handoff
        else:
            action = "pending_location"  # Interest detected, ask for location

    logger.info("📊 Pre-LLM action: %s (campaign=%s)", action, campaign_type)

    # 3. Build system prompt with campaign-specific template
    _, time_str = get_mexico_time()

    prompt_template = CAMPAIGN_PROMPTS.get(campaign_type, CAMPAIGN_PROMPTS["generic"])

    # Detect if the first outbound message included the bot's name
    first_msg_included_name = False
    first_msg_was_long = False
    if conversation_history:
        first_msg = conversation_history[0].get("content", "")
        first_msg_included_name = BOT_NAME.lower().split()[0] in first_msg.lower()
        first_msg_was_long = len(first_msg) > 200

    # Build context hint for the AI
    presentation_hint = ""
    if not first_msg_included_name:
        presentation_hint = (
            "\n⚠️ IMPORTANTE: El primer mensaje NO incluyo tu nombre. "
            "Presentate naturalmente al inicio de tu respuesta "
            "(ej: 'Soy {bot_name}, ...'). Fusionalo con tu respuesta.\n"
        ).format(bot_name=BOT_NAME)
    if first_msg_was_long:
        presentation_hint += (
            "\n⚠️ El primer mensaje fue MUY LARGO. Compensa siendo ultra-directa "
            "y breve. Maximo 1 oracion.\n"
        )

    # Context-aware hints for specific situations
    context_hint = ""
    if has_bought:
        context_hint += (
            "\n⚠️ CONTEXTO CRITICO: El cliente ACABA DE DECIR que YA COMPRO una unidad. "
            "NO sigas vendiendo. NO preguntes si sigue buscando. "
            "Felicitalo genuinamente y preguntale que modelo se llevo o como le ha ido. "
            "Si ya lo felicitaste antes, cierra amablemente.\n"
        )

    vehicle = contact_data.get("vehicle", "").strip()
    notes = contact_data.get("notes", "").strip()
    resumen = contact_data.get("resumen", "").strip()

    system_prompt = prompt_template.format(
        bot_name=BOT_NAME,
        company_name=COMPANY_NAME,
        client_name=contact_data.get("name", "cliente").split("|")[0].strip(),
        vehicle=vehicle or "Sin dato",
        last_contact=contact_data.get("last_contact", "reciente"),
        notes=notes or "Sin notas",
        resumen=resumen or "Sin resumen previo",
        company_product=COMPANY_PRODUCT,
        company_location=COMPANY_LOCATION,
        company_url=COMPANY_URL,
        current_time=time_str,
        common_rules=_COMMON_RULES,
    )

    # Append dynamic hints after the system prompt
    if presentation_hint:
        system_prompt += presentation_hint

    if context_hint:
        system_prompt += context_hint

    # Warn AI when vehicle data is missing — prevent hallucinating models
    if not vehicle:
        system_prompt += (
            "\n⚠️ CRITICO: NO tienes dato de vehiculo para este cliente. "
            "NO inventes, NO menciones ningun modelo de camion. "
            "Si necesitas hablar del vehiculo, pregunta cual le interesa.\n"
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

    # 5. Call LLM (Gemini primary → OpenAI fallback)
    try:
        reply = await _llm_completion(messages, max_tokens=200, temperature=0.7)
    except Exception as e:
        logger.error(f"❌ LLM error (all providers failed): {e}")
        reply = "Gracias por tu respuesta. Te paso con un asesor para darte mejor atención."
        action = "handoff"

    # 6. Post-process: if GPT response hints at handoff
    reply_lower = reply.lower()
    handoff_hints = [
        "te paso con", "te comunico con", "un asesor te",
        "asesor especializado", "gerente de ventas",
        "que te contacte", "que se comunique contigo",
        "le pido a un asesor",
        # Common LLM-generated handoff phrases
        "te conecto con", "te transfiero", "conectarte con",
        "te pongo en contacto", "con un asesor", "con nuestro asesor",
        "te va a atender", "te atenderá", "un asesor para",
    ]
    if any(w in reply_lower for w in handoff_hints):
        if action == "continue":
            # AI wants to hand off but we haven't asked for location yet
            if detected_location:
                action = "handoff"
            else:
                action = "pending_location"

    # 6b. Post-process: if AI response lists branch options, it's asking for location
    # Count how many branch names appear in the reply
    branch_names_in_reply = sum(
        1 for loc in ["Tlalnepantla", "Texcoco", "Cuautitlan", "Queretaro",
                       "Celaya", "Leon", "Guadalajara", "Tampico", "Monterrey"]
        if loc.lower() in reply_lower
    )
    if branch_names_in_reply >= 3 and action != "handoff":
        action = "pending_location"

    # 6c. CRITICAL: Detect if bot is scheduling/confirming visits (FORBIDDEN)
    # If the bot confirms a time, says "te espero", gives an address, or says
    # "pasa a la oficina", replace the reply with a proper handoff message.
    scheduling_violations = [
        # Confirming presence/location
        "te espero", "te esperamos", "pasa a la oficina", "pasa a la sucursal",
        "aqui te espero", "aquí te espero", "ya te estoy esperando",
        "te veo aqui", "te veo aquí", "te recibo en",
        # Confirming times
        "te parece bien a las", "nos vemos a las",
        "ven a las", "te veo a las",
        # Creative scheduling the LLM might try
        "date una vuelta", "te esperamos hoy", "te esperamos mañana",
        "te esperamos manana", "pasate por", "pásate por",
        "caele", "te caigo", "nos vemos hoy", "nos vemos mañana",
        "nos vemos manana", "vienes hoy", "vienes mañana",
        "lo retomamos esta semana o",
        # Giving specific addresses or locations
        "en selectrucks", "en la agencia",
        "en nuestras instalaciones",
    ]
    # Also check for time patterns like "a las 5", "a las 10:30"
    _time_pattern = re.search(r'\ba las \d{1,2}(:\d{2})?\b', reply_lower)
    has_violation = any(v in reply_lower for v in scheduling_violations) or bool(_time_pattern)
    if has_violation:
        client_name = contact_data.get("name", "").split("|")[0].strip()
        name_part = f" {client_name}" if client_name else ""
        if detected_location:
            reply = (
                f"Perfecto{name_part}! En breve nos ponemos de acuerdo "
                f"para atenderte en {detected_location}."
            )
            action = "handoff"
        else:
            reply = (
                f"Que buena noticia{name_part}! Para apoyarte mejor, "
                f"en cual de nuestras sucursales te gustaria que te atendamos?\n\n"
                f"Tenemos puntos de venta en Tlalnepantla, Texcoco, Cuautitlan, "
                f"Queretaro, Celaya, Leon, en Guadalajara (Occidente y Mariano Otero), "
                f"Tampico y Monterrey.\n\nDime cual te queda mas comoda."
            )
            action = "pending_location"

    # 7. Log final action after all post-processing
    _matched_hints = [w for w in handoff_hints if w in reply_lower]
    if _matched_hints:
        logger.info("🔗 Handoff hints found in LLM reply: %s", _matched_hints)
    logger.info(
        "📋 Final action: %s | interest=%s handoff=%s location=%s pending=%s",
        action, _has_interest, _has_handoff, detected_location, pending_location,
    )

    # 8. Generate brief summary
    summary = _summarize_exchange(user_text, reply, action)

    result = {
        "reply": reply,
        "action": action,
        "summary": summary,
    }
    if detected_location:
        result["location"] = detected_location

    return result


def _summarize_exchange(user_text: str, bot_reply: str, action: str) -> str:
    """Generate a brief summary of the exchange for Monday notes."""
    t = user_text[:100]
    if action == "stop":
        return f"STOP: Cliente pidió no ser contactado. Dijo: '{t}'"
    if action == "handoff":
        return f"HANDOFF: Cliente pidió asesor. Dijo: '{t}'"
    if action == "pending_location":
        return f"UBICACION: Bot preguntó sucursal preferida. Dijo: '{t}'"
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
        result = await _llm_completion(
            [{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        return result[:500]
    except Exception as e:
        logger.error(f"❌ Resumen generation error: {e}")
        # Fallback: simple text summary
        return f"Cliente dijo: {user_text[:100]}. Bot respondió: {bot_reply[:100]}"
