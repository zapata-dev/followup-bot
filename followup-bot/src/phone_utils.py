"""
Utilidades de normalización de teléfonos MX.
Soporta formatos de Krino (whatsapp:+5213131073749_1234) y teléfonos crudos.
"""
import re
import logging

logger = logging.getLogger(__name__)


def normalize_phone(raw: str) -> str:
    """
    Normaliza un teléfono mexicano a formato: 521XXXXXXXXXX (13 dígitos).
    
    Soporta:
    - Krino format: "whatsapp:+5213131073749_1234"
    - Raw: "+52 1 313 107 3749"
    - Raw: "3131073749"
    - Raw: "13131073749"
    - Raw: "523131073749"
    - Raw: "5213131073749"
    
    Returns: "5213131073749" o "" si inválido.
    """
    if not raw:
        return ""
    
    s = str(raw).strip()
    
    # Krino format: "whatsapp:+5213131073749_1234"
    if "whatsapp:" in s:
        s = s.replace("whatsapp:", "")
    
    # Remove suffix like "_1234"
    if "_" in s:
        s = s.split("_")[0]
    
    # Strip everything except digits
    digits = re.sub(r"[^\d]", "", s)
    
    if not digits:
        return ""
    
    # Normalize to 521XXXXXXXXXX
    if len(digits) == 10:
        # 3131073749 → 5213131073749
        digits = "521" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        # 13131073749 → 5213131073749
        digits = "52" + digits
    elif len(digits) == 12 and digits.startswith("52"):
        # 523131073749 → 5213131073749
        digits = "521" + digits[2:]
    elif len(digits) == 13 and digits.startswith("521"):
        # Already correct
        pass
    else:
        logger.warning(f"⚠️ Teléfono no normalizable: {raw} → {digits}")
        return ""  # Invalid format, reject to avoid sending to wrong numbers
    
    return digits


def phone_for_evolution(phone: str) -> str:
    """
    Formatea teléfono para Evolution API sendText.
    Input: "5213131073749"
    Output: "5213131073749@s.whatsapp.net"
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return ""
    return f"{normalized}@s.whatsapp.net"


def phone_for_display(phone: str) -> str:
    """
    Formatea teléfono para display legible.
    Input: "5213131073749"
    Output: "+52 1 313 107 3749"
    """
    n = normalize_phone(phone)
    if len(n) == 13:
        return f"+{n[0:2]} {n[2]} {n[3:6]} {n[6:9]} {n[9:13]}"
    return n
