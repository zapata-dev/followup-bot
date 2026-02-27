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
| `sender_service.py` | Outbound sender with rate limiting |
| `monday_service.py` | Monday.com read/update (campaign groups) |
| `memory_store.py` | SQLite for conversation history |
| `phone_utils.py` | Mexican phone normalization |

## Env Vars

### Required
```
EVOLUTION_API_URL, EVOLUTION_API_KEY, OPENAI_API_KEY
MONDAY_API_KEY, MONDAY_BOARD_ID
```

### Bot Identity
```
BOT_NAME="Toño Ramirez"
COMPANY_NAME="Go-On Zapata"
COMPANY_LOCATION="Querétaro"
COMPANY_PRODUCT="camiones seminuevos"
```

### Monday Columns
```
MONDAY_STATUS_COLUMN_ID, MONDAY_PHONE_COLUMN_ID, MONDAY_DEDUPE_COLUMN_ID
MONDAY_VEHICLE_COLUMN_ID, MONDAY_SEND_DATE_COLUMN_ID, etc.
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

## Campaign Workflow

1. Create group in Monday + import contacts CSV
2. All contacts start as "Pendiente"
3. `POST /admin/start/{group_id}` → bot reads + sends with delay
4. Monday updates to "Enviado"
5. Replies come through webhook → AI responds → Monday updates
6. STOP → no more contact. Handoff → alert owner. Interested → flag lead.
