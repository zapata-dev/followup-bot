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
        self.location_col_id = os.getenv("MONDAY_LOCATION_COLUMN_ID", "dropdown_mm1a2hv5")

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
                    # Log full detail so we can debug column format issues
                    vars_summary = json.dumps(variables or {})[:500] if variables else "none"
                    logger.error(
                        f"❌ Monday GraphQL errors: {data['errors']}\n"
                        f"   Variables sent: {vars_summary}"
                    )
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
        Fetches all items from the group and filters by status in Python
        (more reliable than GraphQL status filtering).
        """
        items = await self._get_group_items(group_id)

        contacts = []
        for item in items:
            col_map = {cv["id"]: cv.get("text", "") for cv in item.get("column_values", [])}

            # Filter by status = "Pendiente" (case-insensitive)
            status = col_map.get(self.status_col_id, "").strip()
            if status.lower() != "pendiente":
                continue

            contacts.append({
                "item_id": item["id"],
                "name": item.get("name", ""),
                "phone": col_map.get(self.dedupe_col_id, "") or col_map.get(self.phone_col_id, ""),
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

    async def _get_group_items(self, group_id: str) -> List[Dict]:
        """Fetch all items from a specific group with pagination (supports 1000+)."""
        all_items = []
        cursor = None

        # First page
        first_query = """
        query ($board_id: [ID!]!, $group_id: [String!]!) {
            boards(ids: $board_id) {
                groups(ids: $group_id) {
                    id
                    title
                    items_page(limit: 500) {
                        cursor
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
        }
        """
        data = await self._graphql(first_query, {
            "board_id": [int(self.board_id)],
            "group_id": [group_id],
        })

        groups = data.get("data", {}).get("boards", [{}])[0].get("groups", [])
        if not groups:
            return []

        page = groups[0].get("items_page", {})
        all_items.extend(page.get("items", []))
        cursor = page.get("cursor")

        # Subsequent pages using next_items_page
        while cursor:
            next_query = """
            query ($cursor: String!) {
                next_items_page(limit: 500, cursor: $cursor) {
                    cursor
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
            data = await self._graphql(next_query, {"cursor": cursor})
            page = data.get("data", {}).get("next_items_page", {})
            all_items.extend(page.get("items", []))
            cursor = page.get("cursor")
            logger.info(f"📄 Fetched page, total items so far: {len(all_items)}")

        logger.info(f"📋 Total items fetched from group {group_id}: {len(all_items)}")
        return all_items

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
    def _phone_variants(self, phone_clean: str) -> list:
        """
        Generate all possible formats a phone might be stored as in Monday.
        Input: '5213131073749' (normalized 13 digits)
        Output: ['5213131073749', '3131073749', '523131073749', '13131073749',
                 '+5213131073749', '+52 1 313 107 3749']
        """
        variants = [phone_clean]
        if len(phone_clean) == 13 and phone_clean.startswith("521"):
            ten = phone_clean[3:]          # 3131073749
            twelve = "52" + ten            # 523131073749
            eleven = "1" + ten             # 13131073749
            plus_full = "+" + phone_clean  # +5213131073749
            variants.extend([ten, twelve, eleven, plus_full])
        return variants

    async def _search_phone_in_column(self, col_id: str, value: str) -> Optional[dict]:
        """Search a single phone value in a column. Returns first item or None."""
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
            "col_id": col_id,
            "value": value,
        })
        items = data.get("data", {}).get("items_page_by_column_values", {}).get("items", [])
        return items[0] if items else None

    async def find_by_phone(self, phone_clean: str) -> Optional[Dict]:
        """
        Find a contact item by phone. Tries multiple phone formats
        in both dedupe and phone columns to handle format mismatches.
        Returns dict with item_id, name, status, vehicle, notes, group info.
        """
        variants = self._phone_variants(phone_clean)

        # Try dedupe column first (primary), then phone column (fallback)
        columns_to_search = [self.dedupe_col_id]
        if self.phone_col_id and self.phone_col_id != self.dedupe_col_id:
            columns_to_search.append(self.phone_col_id)

        for col_id in columns_to_search:
            for variant in variants:
                item = await self._search_phone_in_column(col_id, variant)
                if item:
                    if variant != phone_clean:
                        logger.info(f"🔍 Found contact with variant format: '{variant}' (normalized: {phone_clean})")
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

        logger.warning(f"⚠️ Phone {phone_clean[:6]}*** not found in Monday (tried {len(variants)} variants in {len(columns_to_search)} columns)")
        return None

    # ──────────────────────────────────────────────────────────
    # UPDATE: Change status of a contact
    # ──────────────────────────────────────────────────────────
    async def update_status(self, item_id: str, new_status: str, extra_cols: Dict = None):
        """
        Update status label and optionally other columns.
        Strategy: try ALL columns together first (1 API call).
        If that fails, fall back to status-only + extra separately.
        """
        if not item_id:
            logger.warning("⚠️ update_status called with no item_id, skipping")
            return

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

        # Try all columns at once first (fewer API calls = less rate limiting)
        all_vals = {self.status_col_id: {"label": new_status}}
        if extra_cols:
            all_vals.update(extra_cols)

        logger.info(f"📝 Updating item {item_id}: status → {new_status}, cols: {list(all_vals.keys())}")
        result = await self._graphql(query, {
            "item_id": int(item_id),
            "board_id": int(self.board_id),
            "vals": json.dumps(all_vals),
        })

        if result.get("data", {}).get("change_multiple_column_values", {}).get("id"):
            logger.info(f"✅ Monday updated: item {item_id} → {new_status} + {len(all_vals) - 1} extra cols")
            return

        # Combined update failed — fall back to status only + extra separately
        logger.warning(f"⚠️ Combined update failed for item {item_id}, trying status-only fallback...")

        # STEP 1: Status only
        status_vals = {self.status_col_id: {"label": new_status}}
        result = await self._graphql(query, {
            "item_id": int(item_id),
            "board_id": int(self.board_id),
            "vals": json.dumps(status_vals),
        })
        if result.get("data", {}).get("change_multiple_column_values", {}).get("id"):
            logger.info(f"✅ Status-only updated: item {item_id} → {new_status}")
        else:
            logger.error(f"❌ Status update FAILED for item {item_id}. Response: {json.dumps(result)[:500]}")

        # STEP 2: Extra columns one by one (to identify which column is broken)
        if extra_cols:
            for col_id, col_val in extra_cols.items():
                try:
                    col_result = await self._graphql(query, {
                        "item_id": int(item_id),
                        "board_id": int(self.board_id),
                        "vals": json.dumps({col_id: col_val}),
                    })
                    if col_result.get("data", {}).get("change_multiple_column_values", {}).get("id"):
                        logger.info(f"✅ Column {col_id} updated for item {item_id}")
                    else:
                        logger.error(
                            f"❌ Column {col_id} FAILED for item {item_id}. "
                            f"Value: {json.dumps(col_val)[:200]}. "
                            f"Response: {json.dumps(col_result)[:300]}"
                        )
                except Exception as e:
                    logger.error(f"❌ Column {col_id} crashed for item {item_id}: {e}")

    async def update_send_date(self, item_id: str, normalized_phone: str = ""):
        """Mark send date as today and save normalized phone to dedupe column."""
        try:
            tz = pytz.timezone("America/Mexico_City")
            today = datetime.now(tz).strftime("%Y-%m-%d")
        except Exception:
            today = datetime.now().strftime("%Y-%m-%d")

        extra = {
            self.send_date_col_id: {"date": today},
        }
        # Save normalized phone so find_by_phone always matches on reply
        if normalized_phone and self.dedupe_col_id:
            extra[self.dedupe_col_id] = normalized_phone

        await self.update_status(item_id, "Enviado", extra)

    async def update_reply(
        self,
        item_id: str,
        new_status: str,
        reply_summary: str = "",
        resumen: str = "",
    ):
        """
        Update all relevant fields on reply:
        - Status label
        - Reply date (today)
        - Last contact date (today)
        - Reply summary (what the client said)
        - Resumen (AI-generated running conversation summary)
        """
        try:
            tz = pytz.timezone("America/Mexico_City")
            today = datetime.now(tz).strftime("%Y-%m-%d")
        except Exception:
            today = datetime.now().strftime("%Y-%m-%d")

        extra = {
            self.reply_date_col_id: {"date": today},
            self.last_contact_col_id: {"date": today},
        }

        if reply_summary and self.reply_col_id:
            extra[self.reply_col_id] = {"text": reply_summary[:2000]}

        if resumen and self.resumen_col_id:
            extra[self.resumen_col_id] = {"text": resumen[:2000]}

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

    # ──────────────────────────────────────────────────────────
    # CREATE: New item in a group (for orphan contacts)
    # ──────────────────────────────────────────────────────────
    async def create_item_in_group(
        self, group_id: str, name: str, column_values: Dict = None
    ) -> Optional[str]:
        """
        Create a new item in a specific Monday group.
        Used for the orphan contacts inbox (Propuesta 3).
        Returns the new item_id or None.
        """
        query = """
        mutation ($board_id: ID!, $group_id: String!, $name: String!, $vals: JSON!) {
            create_item(
                board_id: $board_id,
                group_id: $group_id,
                item_name: $name,
                column_values: $vals,
                create_labels_if_missing: true
            ) { id }
        }
        """
        vals = column_values or {}
        data = await self._graphql(query, {
            "board_id": int(self.board_id),
            "group_id": group_id,
            "name": name,
            "vals": json.dumps(vals),
        })

        new_id = data.get("data", {}).get("create_item", {}).get("id")
        if new_id:
            logger.info(f"✅ Created new item in group {group_id}: {name} → item {new_id}")
        else:
            logger.error(f"❌ Failed to create item in group {group_id}: {name}. Response: {json.dumps(data)[:500]}")
        return new_id

    # ──────────────────────────────────────────────────────────
    # BULK: Get all contacts for cache sync
    # ──────────────────────────────────────────────────────────
    async def get_all_contacts_for_cache(self, group_ids: List[str] = None) -> List[Dict]:
        """
        Fetch contacts from specified groups (or all groups) for local cache.
        Returns list of {phone, item_id, name, vehicle, notes, resumen, status, group_id, group_title}.
        """
        if not group_ids:
            groups = await self.get_groups()
            group_ids = [g["id"] for g in groups]

        all_contacts = []
        for gid in group_ids:
            try:
                items = await self._get_group_items(gid)
                for item in items:
                    col_map = {cv["id"]: cv.get("text", "") for cv in item.get("column_values", [])}
                    phone = col_map.get(self.dedupe_col_id, "") or col_map.get(self.phone_col_id, "")
                    if not phone:
                        continue
                    all_contacts.append({
                        "phone": phone,
                        "item_id": item["id"],
                        "name": item.get("name", ""),
                        "vehicle": col_map.get(self.vehicle_col_id, ""),
                        "notes": col_map.get(self.notes_col_id, ""),
                        "resumen": col_map.get(self.resumen_col_id, ""),
                        "status": col_map.get(self.status_col_id, ""),
                        "group_id": item.get("group", {}).get("id", gid),
                        "group_title": item.get("group", {}).get("title", ""),
                    })
            except Exception as e:
                logger.error(f"❌ Failed to fetch group {gid} for cache: {e}")

        logger.info(f"📦 Fetched {len(all_contacts)} contacts from {len(group_ids)} groups for cache")
        return all_contacts


# Singleton
monday_followup = MondayFollowupService()
