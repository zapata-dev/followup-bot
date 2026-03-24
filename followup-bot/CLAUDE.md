# CLAUDE.md — Followup Bot

## Overview

Bot de seguimiento outbound por WhatsApp. Lee contactos de Monday.com, envía mensajes personalizados con rate limiting anti-baneo, y maneja respuestas con IA + handoff humano.

**Separado de Tono-Bot** — otro número, otro board, otro deploy.

## Architecture

```
Monday.com Board ←→ Followup Bot API ←→ Evolution API (WhatsApp)
(contactos, status)   (FastAPI/Render)     (envío/recepción)
                            ↕
                     SQLite (conversaciones)
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, webhook, admin endpoints, lifecycle |
| `conversation_logic.py` | GPT handler for replies (re-engagement prompt) |
| `sender_service.py` | Outbound sender con rate limiting, spintax, circuit breaker |
| `monday_service.py` | Monday.com read/update (campaign groups, fechas) |
| `memory_store.py` | SQLite para historial de conversaciones |
| `phone_utils.py` | Normalización de teléfonos mexicanos |
| `dashboard.py` | Admin UI — Dashboard V3 con template builder |

## Env Vars

### Required
```
EVOLUTION_API_URL, EVOLUTION_API_KEY, OPENAI_API_KEY
MONDAY_API_KEY, MONDAY_BOARD_ID
```

### Bot Identity (mismo agente "Estefania Fernandez" para ambas empresas)
```
# Go-On Zapata
BOT_NAME="Estefania Fernandez"
COMPANY_NAME="Go-On Zapata"
COMPANY_LOCATION="todo el país"
COMPANY_PRODUCT="camiones seminuevos"

# Selectruck Zapata
BOT_NAME="Estefania Fernandez"
COMPANY_NAME="Selectruck Zapata"
COMPANY_LOCATION="todo el país"
COMPANY_PRODUCT="camiones seminuevos"
```

### Monday Columns
```
MONDAY_STATUS_COLUMN_ID, MONDAY_PHONE_COLUMN_ID, MONDAY_DEDUPE_COLUMN_ID
MONDAY_VEHICLE_COLUMN_ID, MONDAY_SEND_DATE_COLUMN_ID
MONDAY_REPLY_DATE_COLUMN_ID, MONDAY_LAST_CONTACT_COLUMN_ID
```

### Sender Config
```
SEND_DELAY_MIN=10, SEND_DELAY_MAX=20
MAX_SENDS_PER_HOUR=60
SEND_WINDOW_START=09:00, SEND_WINDOW_END=14:00
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health + metrics |
| `/webhook` | POST | Evolution webhook (replies) |
| `/admin/groups` | GET | List Monday groups (campaigns) |
| `/admin/start/{group_id}` | POST | Start campaign sending |
| `/admin/pause/{group_id}` | POST | Pause campaign |
| `/admin/status` | GET | Sender status |

## Monday Board Status Labels

`Pendiente` → `En Cola` → `Enviado` → `Respondió` / `Interesado` / `Handoff` / `STOP` / `Error`

## Tiempos de Último Mensaje

El bot mantiene **3 columnas de fecha** en Monday para rastrear el historial de contacto:

| Columna | Env Var | Cuándo se actualiza |
|---------|---------|---------------------|
| Fecha de envío | `MONDAY_SEND_DATE_COLUMN_ID` | Cuando el bot envía el mensaje outbound |
| Fecha de respuesta | `MONDAY_REPLY_DATE_COLUMN_ID` | Cuando el contacto responde por primera vez |
| Último contacto | `MONDAY_LAST_CONTACT_COLUMN_ID` | Cada vez que hay interacción (send **o** reply) |

**Notas importantes:**
- `MONDAY_LAST_CONTACT_COLUMN_ID` es la columna más útil: se actualiza en **ambas** direcciones — cuando el bot manda un mensaje Y cuando el contacto responde.
- Sirve para filtrar en Monday quién lleva más de X días sin contacto.
- Todas las fechas usan timezone **Mexico City** (pytz).
- Código relevante: `monday_service.py` → `update_send_date()` y `update_reply()`.

## Features Anti-Ban

- **Spintax**: los mensajes outbound tienen variantes `{opción1|opción2}` que se eligen al azar para evitar mensajes idénticos.
- **Typing presence**: el bot simula estar escribiendo antes de enviar.
- **Rate limiting**: delays aleatorios entre mensajes, límites por hora y por día.
- **Circuit breaker**: si hay N errores consecutivos, el envío se pausa automáticamente.
- **Global lock**: solo una campaña puede enviar a la vez.

## Auto-Resume

Si una campaña queda interrumpida al cerrar el horario de oficina, el scheduler la reanuda automáticamente al inicio del siguiente horario hábil (`sender_service.py` → `start_auto_resume_scheduler()`).

## Dashboard V3

Panel admin en `/admin/dashboard` con:
- Selector de campaña (grupo de Monday)
- Constructor de templates con spintax, semáforo de longitud y preview de burbujas WhatsApp
- Snippets y ejemplos de mensajes
- Copy preview antes de enviar

## Handoff & Alertas

Cuando un contacto se marca como `Handoff` o `Interesado`, el bot envía una alerta enriquecida con:
- Resumen del lead
- Historial de conversación
- Fecha y vehículo de interés
- Link directo al item en Monday

## Campaign Workflow

1. Create group in Monday + import contacts CSV
2. All contacts start as "Pendiente"
3. `POST /admin/start/{group_id}` → bot reads + sends with delay
4. Monday updates to "Enviado" + `send_date` registrada
5. Replies come through webhook → AI responds → Monday updates
6. STOP → no more contact. Handoff → alert owner. Interested → flag lead.
