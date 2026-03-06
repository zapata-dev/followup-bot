"""
Media Processor — Procesa audio, imágenes y video con Gemini multimodal.

Descarga medios de Evolution API y los envía a Gemini para:
- Audio → transcripción en texto
- Imagen → descripción del contenido
- Video → descripción del contenido

Usa google-genai SDK con Gemini 2.5 Flash (mejor precio/calidad multimodal).
"""
import os
import base64
import logging
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# ============================================================
# GEMINI CONFIG (native SDK for multimodal)
# ============================================================
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_MEDIA_MODEL = os.getenv("GEMINI_MEDIA_MODEL", "gemini-2.5-flash-lite")
_MEDIA_TIMEOUT = 30.0

# Lazy-init the client to avoid import errors if google-genai not installed
_genai_client = None


def _get_genai_client():
    """Lazy-init Google GenAI client."""
    global _genai_client
    if _genai_client is None:
        if not _GEMINI_API_KEY:
            logger.warning("⚠️ GEMINI_API_KEY not set — media processing disabled")
            return None
        try:
            from google import genai
            _genai_client = genai.Client(api_key=_GEMINI_API_KEY)
        except ImportError:
            logger.error("❌ google-genai not installed — media processing disabled")
            return None
    return _genai_client


# ============================================================
# MIME TYPE MAPPING
# ============================================================
_AUDIO_MIMETYPES = {
    "audio/ogg; codecs=opus": "audio/ogg",
    "audio/ogg": "audio/ogg",
    "audio/mpeg": "audio/mpeg",
    "audio/mp4": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/wav": "audio/wav",
}

_IMAGE_MIMETYPES = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}

_VIDEO_MIMETYPES = {
    "video/mp4": "video/mp4",
    "video/3gpp": "video/3gpp",
    "video/quicktime": "video/quicktime",
}


# ============================================================
# DOWNLOAD MEDIA FROM EVOLUTION API
# ============================================================
async def _download_media_base64(
    evolution_url: str,
    api_key: str,
    instance: str,
    message: Dict[str, Any],
) -> Optional[str]:
    """
    Download media from Evolution API as base64.
    Uses the getBase64FromMediaMessage endpoint.
    """
    url = f"{evolution_url.rstrip('/')}/chat/getBase64FromMediaMessage/{instance}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {"message": message, "convertToMp4": False}

    try:
        async with httpx.AsyncClient(timeout=_MEDIA_TIMEOUT) as client:
            r = await client.post(url, json=body, headers=headers)
            if r.status_code >= 400:
                logger.error(f"❌ Evolution media download error: {r.status_code} {r.text[:200]}")
                return None
            data = r.json()
            return data.get("base64", None)
    except Exception as e:
        logger.error(f"❌ Evolution media download failed: {e}")
        return None


def _get_mime_type(msg_content: Dict[str, Any], media_key: str) -> str:
    """Extract MIME type from message content."""
    media_obj = msg_content.get(media_key, {})
    mime = media_obj.get("mimetype", "") or media_obj.get("mimeType", "")
    return mime


# ============================================================
# PROCESS MEDIA WITH GEMINI
# ============================================================
async def _process_with_gemini(
    media_base64: str,
    mime_type: str,
    prompt: str,
) -> Optional[str]:
    """Send media + prompt to Gemini and return the text response."""
    client = _get_genai_client()
    if not client:
        return None

    try:
        from google import genai
        from google.genai import types

        media_bytes = base64.b64decode(media_base64)

        response = client.models.generate_content(
            model=_MEDIA_MODEL,
            contents=[
                types.Content(
                    parts=[
                        types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
                        types.Part.from_text(text=prompt),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.2,
            ),
        )
        result = response.text.strip() if response.text else None
        if result:
            logger.info(f"✅ Gemini media processing OK: {result[:80]}...")
        return result
    except Exception as e:
        logger.error(f"❌ Gemini media processing failed: {e}")
        return None


# ============================================================
# PUBLIC API
# ============================================================
async def process_media_message(
    msg_content: Dict[str, Any],
    message_obj: Dict[str, Any],
    evolution_url: str,
    api_key: str,
    instance: str,
) -> Optional[Dict[str, str]]:
    """
    Process a media message: download from Evolution + analyze with Gemini.

    Returns:
        {"type": "audio|imagen|video", "text": "transcription or description"}
        or None if processing fails.
    """
    # Determine media type and key
    media_key = None
    media_type = None
    prompt = ""

    if "audioMessage" in msg_content:
        media_key = "audioMessage"
        media_type = "audio"
        prompt = (
            "Transcribe este mensaje de voz en español. "
            "Solo devuelve la transcripción exacta de lo que dice la persona, "
            "sin agregar nada más. Si no se entiende algo, escribe [inaudible]."
        )
    elif "imageMessage" in msg_content:
        media_key = "imageMessage"
        media_type = "imagen"
        prompt = (
            "Describe brevemente qué se ve en esta imagen en español, en 1-2 oraciones. "
            "Si hay texto visible en la imagen, inclúyelo. "
            "Si es una captura de pantalla, describe su contenido. "
            "Si es un documento o cotización, extrae los datos clave."
        )
    elif "videoMessage" in msg_content:
        media_key = "videoMessage"
        media_type = "video"
        prompt = (
            "Describe brevemente qué se ve en este video en español, en 1-2 oraciones. "
            "Enfócate en el contenido principal."
        )
    else:
        return None

    # Get MIME type
    raw_mime = _get_mime_type(msg_content, media_key)
    # Normalize MIME type
    all_mimes = {**_AUDIO_MIMETYPES, **_IMAGE_MIMETYPES, **_VIDEO_MIMETYPES}
    mime_type = all_mimes.get(raw_mime, raw_mime.split(";")[0].strip())

    if not mime_type:
        logger.warning(f"⚠️ Unknown MIME type for {media_key}: {raw_mime}")
        return None

    # Download media base64 from Evolution API
    logger.info(f"📥 Downloading {media_type} from Evolution API (mime: {mime_type})...")
    media_b64 = await _download_media_base64(
        evolution_url, api_key, instance, message_obj
    )

    if not media_b64:
        logger.warning(f"⚠️ Could not download {media_type} — falling back to placeholder")
        return None

    # Process with Gemini
    logger.info(f"🧠 Processing {media_type} with Gemini ({_MEDIA_MODEL})...")
    result_text = await _process_with_gemini(media_b64, mime_type, prompt)

    if not result_text:
        return None

    return {"type": media_type, "text": result_text}
