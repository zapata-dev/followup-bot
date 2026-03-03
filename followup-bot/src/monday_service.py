"""
Monday.com service for Followup Bot.
Reads contacts from campaign groups, updates status, handles phone dedup.
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx
import pytz

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayFollowupService:
    def __init__(self):
        self.api_key = os.getenv("MONDAY_API_KEY", "")
        self.board_id = os.getenv("MONDAY_BOARD_ID", "")
        
        # Column IDs (configured via env vars, defaults match "Nuevo Tablero" board)
        self.phone_col_id = os.getenv("MONDAY_PHONE_COLUMN_ID", "phone_mm1298en")
        self.status_col_id = os.getenv("MONDAY_STATUS_COLUMN_ID", "color_mm12yy37")
        self.vehicle_col_id = os.getenv("MONDAY_VEHICLE_COLUMN_ID", "text_mm1272ft")
        self.template_col_id = os.getenv("MONDAY_TEMPLATE_COLUMN_ID", "long_text_mm126er5")
        self.send_date_col_id = os.getenv("MONDAY_SEND_DATE_COLUMN_ID", "date_mm129ayt")
        self.reply_date_col_id = os.getenv("MONDAY_REPLY_DATE_COLUMN_ID", "date_mm12nv11")
        self.last_contact_col_id = os.getenv("MONDAY_LAST_CONTACT_COLUMN_ID", "date_mm126t33")
        self.notes_col_id = os.getenv("MONDAY_NOTES_COLUMN_ID", "long_text_mm126q3t")
        self.reply_col_id = os.getenv("MONDAY_REPLY_COLUMN_ID", "long_text_mm1281tm")
        self.dedupe_col_id = os.getenv("MONDAY_DEDUPE_COLUMN_ID", "text_mm12nh13")
        self.error_col_id = os.getenv("MONDAY_ERROR_COLUMN_ID", "text_mm12dd4y")
        self.resumen_col_id = os.getenv("MONDAY_RESUMEN_COLUMN_ID", "long_text_mm12qhsy")

    def is_configured(self) -> bool:
        return bool(self.api_key and self.board_id)

    async def _graphql(self, query: str, variables: dict = None) -> dict:
        """Execute Monday GraphQL with retry."""
        if not self.is_configured():
            logger.warning("⚠️ Monday not configured, skipping API call")
            return {}

        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        }
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(MONDAY_API_URL, json=payload, headers=headers)

                if r.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"⏳ Monday rate limit, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                r.raise_for_status()
                data = r.json()

                if "errors" in data:
                    logger.error(f"❌ Monday GraphQL errors: {data['errors']}")
                    # If there's no usable data alongside the errors, return empty
                    if "data" not in data or data["data"] is None:
                        return {}

                return data

            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"❌ Monday API failed after 3 attempts: {e}")
                    raise

        return {}

    # ──────────────────────────────────────────────────────────
    # READ: Get pending contacts from a specific group
    # ──────────────────────────────────────────────────────────
    async def get_pending_contacts(self, group_id: str, limit: int = 50) -> List[Dict]:
        """
        Get contacts with status 'Pendiente' from a specific group.
        Returns list of dicts with: item_id, name, phone, vehicle, notes, resumen, template.
        """
        query = """
        query ($board_id: [ID!]!) {
            boards(ids: $board_id) {
                groups {
                    id
                    title
                }
                items_page(limit: 500, query_params: {
                    rules: [{
                        column_id: "STATUS_COL",
                        compare_value: ["Pendiente"]
                    }]
                }) {
                    items {
                        id
                        name
                        group { id title }
                        column_values {
                            id
                            text
                            value
                        }
                    }
                }
            }
        }
        """.replace("STATUS_COL", self.status_col_id)

        data = await self._graphql(query, {"board_id": [int(self.board_id)]})
        
        items = (data.get("data", {}).get("boards", [{}])[0]
                 .get("items_page", {}).get("items", []))

        contacts = []
        for item in items:
            # Filter by group
            if item.get("group", {}).get("id") != group_id:
                continue
            
            col_map = {cv["id"]: cv.get("text", "") for cv in item.get("column_values", [])}
            
            contacts.append({
                "item_id": item["id"],
                "name": item.get("name", ""),
                "phone": col_map.get(self.dedupe_col_id, ""),
                "vehicle": col_map.get(self.vehicle_col_id, ""),
                "notes": col_map.get(self.notes_col_id, ""),
                "resumen": col_map.get(self.resumen_col_id, ""),
                "template": col_map.get(self.template_col_id, ""),
                "group_id": group_id,
                "group_title": item.get("group", {}).get("title", ""),
            })

            if len(contacts) >= limit:
                break

        logger.info(f"📋 Found {len(contacts)} pending contacts in group {group_id}")
        return contacts

    # ──────────────────────────────────────────────────────────
    # READ: Get all groups (campaigns)
    # ──────────────────────────────────────────────────────────
    async def get_groups(self) -> List[Dict]:
        """Get all groups in the board (each group = a campaign)."""
        query = """
        query ($board_id: [ID!]!) {
            boards(ids: $board_id) {
                groups {
                    id
                    title
                }
            }
        }
        """
        data = await self._graphql(query, {"board_id": [int(self.board_id)]})
        groups = data.get("data", {}).get("boards", [{}])[0].get("groups", [])
        return [{"id": g["id"], "title": g["title"]} for g in groups]

    # ──────────────────────────────────────────────────────────
    # DEBUG: Show board structure and column IDs
    # ──────────────────────────────────────────────────────────
    async def get_board_structure(self) -> Dict:
        """Get full board structure: columns, groups, and sample items for debugging."""
        query = """
        query ($board_id: [ID!]!) {
            boards(ids: $board_id) {
                name
                columns {
                    id
                    title
                    type
                }
                groups {
                    id
                    title
                }
            }
        }
        """
        data = await self._graphql(query, {"board_id": [int(self.board_id)]})
        board = data.get("data", {}).get("boards", [{}])[0]

        # Show what the bot is currently configured to use
        configured = {
            "status_col": self.status_col_id,
            "dedupe_col": self.dedupe_col_id,
            "phone_col": self.phone_col_id,
            "vehicle_col": self.vehicle_col_id,
            "template_col": self.template_col_id,
            "send_date_col": self.send_date_col_id,
            "reply_date_col": self.reply_date_col_id,
            "notes_col": self.notes_col_id,
            "reply_col": self.reply_col_id,
            "error_col": self.error_col_id,
            "resumen_col": self.resumen_col_id,
        }

        return {
            "board_name": board.get("name", ""),
            "board_id": self.board_id,
            "columns": board.get("columns", []),
            "groups": board.get("groups", []),
            "configured_column_ids": configured,
        }

    # ──────────────────────────────────────────────────────────
    # SEARCH: Find contact by phone (for webhook replies)
    # ──────────────────────────────────────────────────────────
    async def find_by_phone(self, phone_clean: str) -> Optional[Dict]:
        """
        Find a contact item by phone (dedupe column).
        Returns dict with item_id, name, status, vehicle, notes, group info.
        """
        query = """
        query ($board_id: ID!, $col_id: String!, $value: String!) {
            items_page_by_column_values(
                board_id: $board_id,
                limit: 5,
                columns: [{ column_id: $col_id, column_values: [$value] }]
            ) {
                items {
                    id
                    name
                    group { id title }
                    column_values {
                        id
                        text
                        value
                    }
                }
            }
        }
        """
        data = await self._graphql(query, {
            "board_id": int(self.board_id),
            "col_id": self.dedupe_col_id,
            "value": phone_clean,
        })

        items = data.get("data", {}).get("items_page_by_column_values", {}).get("items", [])
        if not items:
            return None

        item = items[0]
        col_map = {cv["id"]: cv.get("text", "") for cv in item.get("column_values", [])}

        return {
            "item_id": item["id"],
            "name": item.get("name", ""),
            "status": col_map.get(self.status_col_id, ""),
            "phone": col_map.get(self.dedupe_col_id, ""),
            "vehicle": col_map.get(self.vehicle_col_id, ""),
            "notes": col_map.get(self.notes_col_id, ""),
            "resumen": col_map.get(self.resumen_col_id, ""),
            "group_id": item.get("group", {}).get("id", ""),
            "group_title": item.get("group", {}).get("title", ""),
        }

    # ──────────────────────────────────────────────────────────
    # UPDATE: Change status of a contact
    # ──────────────────────────────────────────────────────────
    async def update_status(self, item_id: str, new_status: str, extra_cols: Dict = None):
        """
        Update status label and optionally other columns.
        extra_cols: dict of {column_id: value} for additional updates.
        """
        col_vals = {
            self.status_col_id: {"label": new_status}
        }

        if extra_cols:
            col_vals.update(extra_cols)

        query = """
        mutation ($item_id: ID!, $board_id: ID!, $vals: JSON!) {
            change_multiple_column_values(
                item_id: $item_id,
                board_id: $board_id,
                column_values: $vals,
                create_labels_if_missing: true
            ) { id }
        }
        """
        await self._graphql(query, {
            "item_id": int(item_id),
            "board_id": int(self.board_id),
            "vals": json.dumps(col_vals),
        })
        logger.info(f"✅ Updated item {item_id} → {new_status}")

    async def update_send_date(self, item_id: str):
        """Mark send date as today."""
        try:
            tz = pytz.timezone("America/Mexico_City")
            today = datetime.now(tz).strftime("%Y-%m-%d")
        except Exception:
            today = datetime.now().strftime("%Y-%m-%d")

        await self.update_status(item_id, "Enviado", {
            self.send_date_col_id: {"date": today}
        })

    async def update_reply(self, item_id: str, new_status: str, reply_summary: str = ""):
        """Update status on reply and optionally set reply date + summary."""
        try:
            tz = pytz.timezone("America/Mexico_City")
            today = datetime.now(tz).strftime("%Y-%m-%d")
        except Exception:
            today = datetime.now().strftime("%Y-%m-%d")

        extra = {self.reply_date_col_id: {"date": today}}
        
        if reply_summary and self.reply_col_id:
            extra[self.reply_col_id] = {"text": reply_summary[:2000]}

        await self.update_status(item_id, new_status, extra)

    async def mark_error(self, item_id: str, error_msg: str):
        """Mark contact as Error with error message."""
        await self.update_status(item_id, "Error", {
            self.error_col_id: error_msg[:500]
        })

    # ──────────────────────────────────────────────────────────
    # NOTES: Add update/note to item
    # ──────────────────────────────────────────────────────────
    async def add_note(self, item_id: str, body: str):
        """Add a note/update to a Monday item."""
        query = """
        mutation ($item_id: ID!, $body: String!) {
            create_update(item_id: $item_id, body: $body) { id }
        }
        """
        await self._graphql(query, {"item_id": int(item_id), "body": body})


# Singleton
monday_followup = MondayFollowupService()
