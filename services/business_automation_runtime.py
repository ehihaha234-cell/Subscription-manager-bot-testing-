"""MTProto runtime for connected seller Telegram accounts.

Each active account gets one Telethon client and an incoming private-message
listener. The listener reads the seller's shared Business Automation settings
and sends the configured welcome/auto-reply messages.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot
from telethon import Button, TelegramClient, events, utils
from telethon.tl import types
from telethon.sessions import StringSession

from config import TELEGRAM_API_HASH, TELEGRAM_API_ID
from database.seller_bots import get_bot_by_data_owner_id
from database.business_delivery import record_business_contact
from handlers.common.clone_context import MAIN_BOT_USERNAME
from utils.branding import append_branding
from database.business_automation import (
    get_business_auto_reply,
    list_business_auto_replies,
    get_business_welcome,
    list_business_reply_templates,
)
from database.seller_data import (
    claim_business_welcome,
    get_business_accounts,
    get_seller_settings,
    increment_business_account_stat,
    get_business_contact,
    reset_business_welcome,
    set_business_welcome_message_ids,
)
from utils.crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _keyword_in_message(keyword: str, message: str) -> bool:
    keyword = " ".join(str(keyword or "").casefold().split())
    message = " ".join(str(message or "").casefold().split())
    if not keyword or not message:
        return False
    if re.fullmatch(r"[\w]+", keyword, flags=re.UNICODE):
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", message, flags=re.UNICODE) is not None
    return keyword in message



def _variable_values(user) -> dict[str, str]:
    zone = ZoneInfo("Asia/Kolkata")
    now = datetime.now(zone)
    first = str(getattr(user, "first_name", "") or "")
    last = str(getattr(user, "last_name", "") or "")
    name = " ".join(x for x in (first, last) if x).strip() or str(getattr(user, "username", "") or "User")
    username_raw = str(getattr(user, "username", "") or "").lstrip("@")
    username = f"@{username_raw}" if username_raw else ""
    user_id = str(getattr(user, "id", "") or "")
    mention = f"tg://user?id={user_id}" if user_id else ""
    return {
        "{NAME}": name,
        "{FIRSTNAME}": first,
        "{SURNAME}": last,
        "{NAMESURNAME}": name,
        "{ID}": user_id,
        "{USERNAME}": username,
        "{MENTION}": mention,
        "{DATE}": now.strftime("%d %b %Y"),
        "{TIME}": now.strftime("%I:%M %p"),
        "{WEEKDAY}": now.strftime("%A"),
    }


def _render_variables(value: str, user) -> str:
    rendered = str(value or "")
    for token, replacement in _variable_values(user).items():
        rendered = rendered.replace(token, replacement)
    return rendered


def _render_button_rows(rows, user):
    rendered_rows = []
    for row in rows or []:
        rendered_row = []
        for item in row or []:
            copy = dict(item)
            copy["text"] = _render_variables(str(copy.get("text") or ""), user)
            if "value" in copy:
                copy["value"] = _render_variables(str(copy.get("value") or ""), user)
            if "url" in copy:
                copy["url"] = _render_variables(str(copy.get("url") or ""), user)
            rendered_row.append(copy)
        if rendered_row:
            rendered_rows.append(rendered_row)
    return rendered_rows


def _with_powered_by(text: str) -> str:
    username = str(MAIN_BOT_USERNAME or "").lstrip("@").strip()
    base = str(text or "").rstrip()
    if not username:
        return base
    marker = f"Powered by @{username}"
    if marker.casefold() in base.casefold():
        return base
    return f"{base}\n\n━━━━━━━━━━━━━━\n🤖 {marker}" if base else f"🤖 {marker}"


class BusinessAutomationRuntime:
    def __init__(self) -> None:
        self._clients: dict[tuple[int, int], TelegramClient] = {}
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._message_peers: dict[tuple[int, int], dict[int, int]] = {}
        self._history_checked: set[tuple[int, int, int]] = set()

    def _lock(self, key: tuple[int, int]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _ready() -> bool:
        return bool(TELEGRAM_API_ID and TELEGRAM_API_HASH)

    async def start_all(self) -> int:
        if not self._ready():
            logger.warning("Business Automation runtime disabled: Telegram API credentials missing")
            return 0
        # Query all active accounts across all sellers.
        from database.seller_data import get_all_active_business_accounts

        records = await get_all_active_business_accounts()
        started = 0
        for record in records:
            try:
                if await self.start_account(int(record["owner_id"]), int(record["account_user_id"]), record=record):
                    started += 1
            except Exception:
                logger.exception(
                    "Business Automation account restore failed owner=%s account=%s",
                    record.get("owner_id"),
                    record.get("account_user_id"),
                )
        logger.info("Business Automation runtimes started=%s/%s", started, len(records))
        return started

    async def start_account(self, owner_id: int, account_user_id: int, *, record: dict | None = None) -> bool:
        key = (int(owner_id), int(account_user_id))
        async with self._lock(key):
            existing = self._clients.get(key)
            if existing and existing.is_connected():
                return True

            if record is None:
                records = await get_business_accounts(owner_id)
                record = next((r for r in records if int(r.get("account_user_id", 0)) == int(account_user_id)), None)
            if not record or not record.get("active") or not record.get("encrypted_session"):
                return False

            session = decrypt_secret(record["encrypted_session"])
            client = TelegramClient(StringSession(session), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                logger.warning("Business Automation session unauthorized owner=%s account=%s", owner_id, account_user_id)
                return False

            async def incoming_handler(event):
                await self._handle_incoming(owner_id, account_user_id, client, event)

            async def outgoing_handler(event):
                await self._handle_outgoing(owner_id, account_user_id, client, event)

            async def deleted_handler(event):
                await self._handle_deleted(owner_id, account_user_id, event)

            async def raw_handler(update):
                await self._handle_raw_update(owner_id, account_user_id, update)

            client.add_event_handler(incoming_handler, events.NewMessage(incoming=True))
            client.add_event_handler(outgoing_handler, events.NewMessage(outgoing=True))
            client.add_event_handler(deleted_handler, events.MessageDeleted())
            client.add_event_handler(raw_handler, events.Raw())
            self._clients[key] = client
            logger.info("Business Automation listener active owner=%s account=%s", owner_id, account_user_id)
            return True

    async def stop_account(self, owner_id: int, account_user_id: int) -> None:
        key = (int(owner_id), int(account_user_id))
        async with self._lock(key):
            client = self._clients.pop(key, None)
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception("Business Automation client disconnect failed owner=%s account=%s", *key)

    async def shutdown(self) -> None:
        keys = list(self._clients)
        for owner_id, account_user_id in keys:
            await self.stop_account(owner_id, account_user_id)

    @staticmethod
    def _inside_working_hours(settings: dict) -> bool:
        if not settings.get("business_working_hours_enabled"):
            return True
        try:
            zone = ZoneInfo(settings.get("business_working_hours_timezone") or "Asia/Kolkata")
            now = datetime.now(zone).strftime("%H:%M")
            start = str(settings.get("business_working_hours_start") or "00:00")
            end = str(settings.get("business_working_hours_end") or "23:59")
            if start <= end:
                return start <= now <= end
            return now >= start or now <= end
        except Exception:
            logger.exception("Invalid Business Automation working-hours settings")
            return True

    async def _telethon_buttons(self, owner_id: int, rows) -> list[list[Button]] | None:
        result = []
        bot_record = None
        for row in rows or []:
            clean = []
            for item in row or []:
                value = str(item.get("value") or item.get("url") or "").strip()
                item_type = str(item.get("type") or ("url" if item.get("url") else ""))
                if item_type == "callback":
                    # User accounts cannot send bot callback buttons. Convert shared
                    # feature buttons into a Clone Bot deep link.
                    if bot_record is None:
                        bot_record = await get_bot_by_data_owner_id(int(owner_id))
                    username = str((bot_record or {}).get("bot_username") or "").lstrip("@")
                    feature = value.removeprefix("c_")
                    if username:
                        value = f"https://t.me/{username}?start={feature}"
                        item_type = "url"
                elif item_type == "clone":
                    if bot_record is None:
                        bot_record = await get_bot_by_data_owner_id(int(owner_id))
                    username = str((bot_record or {}).get("bot_username") or "").lstrip("@")
                    if username:
                        value = f"https://t.me/{username}?start={value}"
                        item_type = "url"
                if item_type == "url" and value:
                    clean.append(Button.url(str(item.get("text") or "Open")[:64], value))
            if clean:
                result.append(clean)
        return result or None

    async def _download_clone_media(self, owner_id: int, file_id: str, media_type: str = "") -> io.BytesIO | None:
        try:
            bot_record = await get_bot_by_data_owner_id(int(owner_id))
            if not bot_record:
                return None
            encrypted_token = bot_record.get("bot_token_encrypted")
            if not encrypted_token:
                return None
            token = decrypt_secret(encrypted_token)
            async with Bot(token=token) as bot:
                tg_file = await bot.get_file(file_id)
                data = await tg_file.download_as_bytearray()
            stream = io.BytesIO(bytes(data))
            extension = {
                "photo": ".jpg",
                "video": ".mp4",
                "animation": ".gif",
                "document": ".bin",
            }.get(str(media_type or "").lower(), ".bin")
            stream.name = f"business_media{extension}"
            return stream
        except Exception:
            logger.exception("Business Automation media download failed owner=%s", owner_id)
            return None

    async def _send_configured_message(
        self, client: TelegramClient, peer_id: int, owner_id: int, *,
        text: str, media_type: str, media_file_id: str, button_rows, media_items=None, user=None,
    ) -> list[int]:
        rendered_text = _render_variables(text, user) if user is not None else str(text or "")
        rendered_rows = _render_button_rows(button_rows, user) if user is not None else button_rows
        buttons = await self._telethon_buttons(owner_id, rendered_rows)
        items = list(media_items or [])
        if not items and media_file_id:
            items = [{"type": media_type or "document", "file_id": media_file_id}]
        items = [x for x in items if x.get("file_id")][:10]
        streams = []
        for item in items:
            stream = await self._download_clone_media(owner_id, str(item.get("file_id") or ""), str(item.get("type") or ""))
            if stream is not None:
                streams.append(stream)
        sent_ids: list[int] = []
        if len(streams) > 1:
            sent = await client.send_file(peer_id, streams, album=True)
            if isinstance(sent, (list, tuple)):
                sent_ids.extend(int(getattr(item, "id", 0) or 0) for item in sent)
            elif sent is not None:
                sent_ids.append(int(getattr(sent, "id", 0) or 0))
            if rendered_text or buttons:
                message = await client.send_message(peer_id, rendered_text or "Choose an option below.", buttons=buttons)
                sent_ids.append(int(getattr(message, "id", 0) or 0))
            return [message_id for message_id in sent_ids if message_id > 0]
        if len(streams) == 1:
            message = await client.send_file(peer_id, streams[0], caption=rendered_text or "", buttons=buttons, force_document=str(items[0].get("type") or "").lower() == "document")
            return [int(getattr(message, "id", 0) or 0)] if int(getattr(message, "id", 0) or 0) > 0 else []
        message = await client.send_message(peer_id, rendered_text or "Welcome!", buttons=buttons)
        return [int(getattr(message, "id", 0) or 0)] if int(getattr(message, "id", 0) or 0) > 0 else []

    async def send_text_to_contact(
        self, owner_id: int, account_user_id: int, peer_id: int, text: str
    ) -> bool:
        """Send a plain message through one connected Normal account."""
        key = (int(owner_id), int(account_user_id))
        client = self._clients.get(key)
        if not client or not client.is_connected():
            started = await self.start_account(int(owner_id), int(account_user_id))
            client = self._clients.get(key) if started else None
        if not client or not client.is_connected():
            return False
        await client.send_message(int(peer_id), str(text), link_preview=False)
        return True


    def _remember_message_peer(self, owner_id: int, account_user_id: int, message_id: int, peer_id: int) -> None:
        if not message_id or not peer_id:
            return
        key = (int(owner_id), int(account_user_id))
        mapping = self._message_peers.setdefault(key, {})
        mapping[int(message_id)] = int(peer_id)
        # Keep memory bounded while retaining enough recent IDs for delete events.
        if len(mapping) > 2000:
            for old_id in sorted(mapping)[:500]:
                mapping.pop(old_id, None)

    async def _reset_peer_welcome(self, owner_id: int, account_user_id: int, peer_id: int) -> None:
        if int(peer_id or 0) <= 0:
            return
        await reset_business_welcome(int(owner_id), int(account_user_id), int(peer_id))
        self._history_checked.discard((int(owner_id), int(account_user_id), int(peer_id)))
        logger.info(
            "Business welcome reset after chat deletion owner=%s account=%s peer=%s",
            owner_id, account_user_id, peer_id,
        )

    async def _handle_deleted(self, owner_id: int, account_user_id: int, event) -> None:
        """Handle Telethon's high-level delete event when peer information is available."""
        try:
            peer_id = int(getattr(event, "chat_id", 0) or 0)
            if peer_id > 0:
                await self._reset_peer_welcome(owner_id, account_user_id, peer_id)
                return
            mapping = self._message_peers.get((int(owner_id), int(account_user_id)), {})
            peers = {mapping.get(int(mid)) for mid in (getattr(event, "deleted_ids", None) or [])}
            for mapped_peer in peers:
                if mapped_peer:
                    await self._reset_peer_welcome(owner_id, account_user_id, int(mapped_peer))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Business Automation deleted-message handler failed owner=%s account=%s", owner_id, account_user_id)

    async def _handle_raw_update(self, owner_id: int, account_user_id: int, update) -> None:
        """Catch private-chat clear-history updates that MessageDeleted may not identify."""
        try:
            if isinstance(update, types.UpdateDeleteHistory):
                peer_id = int(utils.get_peer_id(update.peer) or 0)
                if peer_id > 0:
                    await self._reset_peer_welcome(owner_id, account_user_id, peer_id)
                return
            if isinstance(update, types.UpdateDeleteMessages):
                mapping = self._message_peers.get((int(owner_id), int(account_user_id)), {})
                peers = {mapping.get(int(mid)) for mid in (update.messages or [])}
                for peer_id in peers:
                    if peer_id:
                        await self._reset_peer_welcome(owner_id, account_user_id, int(peer_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Business Automation raw deletion handler failed owner=%s account=%s", owner_id, account_user_id)

    async def _history_was_cleared(
        self, owner_id: int, account_user_id: int, peer_id: int,
        client: TelegramClient, current_message_id: int,
    ) -> bool:
        """Detect a cleared private chat before claiming the next welcome.

        Telegram does not always emit a usable delete-history event.  We therefore
        persist the last processed message id and verify that it is still present.
        A second recent-history check covers old database records that do not yet
        contain ``last_message_id``.
        """
        existing = await get_business_contact(owner_id, account_user_id, peer_id)
        if not existing:
            return False
        try:
            welcome_message_ids = [
                int(value) for value in (existing.get("welcome_message_ids") or [])
                if int(value or 0) > 0
            ]
            if welcome_message_ids:
                delivered = await client.get_messages(int(peer_id), ids=welcome_message_ids[-5:])
                if not isinstance(delivered, (list, tuple)):
                    delivered = [delivered]
                if not any(item is not None for item in delivered):
                    logger.info(
                        "Business welcome messages no longer exist; treating chat as cleared owner=%s account=%s peer=%s",
                        owner_id, account_user_id, peer_id,
                    )
                    return True

            previous_message_id = int(existing.get("last_message_id") or 0)
            previous_missing = False
            if previous_message_id > 0 and previous_message_id != int(current_message_id or 0):
                previous = await client.get_messages(int(peer_id), ids=previous_message_id)
                previous_missing = previous is None

            # Let Telegram finish applying the incoming update before inspecting
            # the visible history. This avoids stale results directly inside the
            # NewMessage callback.
            await asyncio.sleep(0.15)
            messages = await client.get_messages(int(peer_id), limit=5)
            visible_ids = {
                int(getattr(message, "id", 0) or 0)
                for message in messages
                if int(getattr(message, "id", 0) or 0) > 0
            }
            older_visible = {
                message_id for message_id in visible_ids
                if message_id != int(current_message_id or 0)
            }

            # A cleared chat contains only the newly arrived message.  For newer
            # records, the disappearance of the stored previous id confirms it.
            if not older_visible:
                return True
            if previous_message_id > 0 and previous_missing and previous_message_id not in visible_ids:
                # Avoid treating one individually deleted old message as a full
                # clear when other prior history is still visible.
                return len(older_visible) == 0
            return False
        except Exception:
            logger.exception(
                "Business Automation history check failed owner=%s account=%s peer=%s",
                owner_id, account_user_id, peer_id,
            )
            return False

    async def _handle_incoming(self, owner_id: int, account_user_id: int, client: TelegramClient, event) -> None:
        try:
            if not event.is_private or event.out:
                return
            sender = await event.get_sender()
            peer_id = int(event.sender_id or 0)
            if not peer_id or peer_id == int(account_user_id) or getattr(sender, "bot", False):
                return

            current_message_id = int(getattr(event, "id", 0) or 0)
            self._remember_message_peer(owner_id, account_user_id, current_message_id, peer_id)
            history_was_cleared = await self._history_was_cleared(
                owner_id, account_user_id, peer_id, client, current_message_id
            )

            await record_business_contact(
                owner_id, peer_id, mode="normal",
                account_user_id=account_user_id, chat_id=peer_id,
            )

            settings = await get_seller_settings(owner_id)
            welcome = await get_business_welcome(owner_id)
            auto_replies = await list_business_auto_replies(owner_id)
            if not settings.get("business_automation_enabled"):
                return
            if not self._inside_working_hours(settings):
                return

            first_contact = await claim_business_welcome(
                owner_id,
                account_user_id,
                peer_id,
                welcome_once=True,
                force_new_conversation=history_was_cleared,
                current_message_id=current_message_id,
            )

            delay = max(0, min(int(settings.get("business_reply_delay_seconds", 0) or 0), 300))
            if delay:
                await asyncio.sleep(delay)

            welcome_sent = False
            if welcome.get("enabled", True) and first_contact:
                text = await append_branding(str(welcome.get("text") or "").strip())
                media_file_id = str(welcome.get("media_file_id") or "")
                media_items = list(welcome.get("media") or [])
                if text or media_file_id or media_items:
                    sent_message_ids = await self._send_configured_message(
                        client,
                        peer_id,
                        owner_id,
                        text=text,
                        media_type=str(welcome.get("media_type") or ""),
                        media_file_id=media_file_id,
                        button_rows=welcome.get("buttons") or [],
                        media_items=welcome.get("media") or [],
                        user=sender,
                    )
                    await set_business_welcome_message_ids(
                        owner_id, account_user_id, peer_id, sent_message_ids
                    )
                    await increment_business_account_stat(owner_id, account_user_id, "welcome_sent")
                    welcome_sent = True

            # Keyword replies run after the first welcome. Exact matching avoids
            # accidental replies when a keyword appears inside an unrelated sentence.
            if not welcome_sent:
                incoming_text = " ".join(str(getattr(event, "raw_text", "") or "").strip().lower().split())
                match = next((x for x in auto_replies if x.get("enabled", True) and _keyword_in_message(str(x.get("keyword") or ""), incoming_text)), None)
                if match:
                    text = str(match.get("text") or "").strip()
                    media_file_id = str(match.get("media_file_id") or "")
                    media_items = list(match.get("media") or [])
                    if text or media_file_id or media_items:
                        await self._send_configured_message(
                            client, peer_id, owner_id,
                            text=text,
                            media_type=str(match.get("media_type") or ""),
                            media_file_id=media_file_id,
                            button_rows=match.get("buttons") or [],
                            media_items=match.get("media") or [],
                            user=sender,
                        )
                        await increment_business_account_stat(owner_id, account_user_id, "auto_replies_sent")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Business Automation incoming message failed owner=%s account=%s peer=%s",
                owner_id,
                account_user_id,
                getattr(event, "sender_id", None),
            )

    async def _handle_outgoing(self, owner_id: int, account_user_id: int, client: TelegramClient, event) -> None:
        """Replace seller shortcuts such as /payment with the saved template."""
        try:
            if not event.is_private:
                return
            raw = str(getattr(event, "raw_text", "") or "").strip()
            if not raw or any(ch.isspace() for ch in raw):
                return
            templates = await list_business_reply_templates(owner_id)
            item = next(
                (
                    x for x in templates
                    if x.get("enabled", True)
                    and raw.casefold() == str(x.get("shortcut") or "").strip().casefold()
                ),
                None,
            )
            if not item:
                return
            peer_id = int(event.chat_id or 0)
            if not peer_id:
                return
            self._remember_message_peer(owner_id, account_user_id, int(getattr(event, "id", 0) or 0), peer_id)
            recipient = await event.get_chat()
            try:
                await event.delete()
            except Exception:
                logger.exception("Business template shortcut delete failed owner=%s", owner_id)
            await self._send_configured_message(
                client, peer_id, owner_id,
                text=str(item.get("text") or item.get("name") or ""),
                media_type=str(item.get("media_type") or ""),
                media_file_id=str(item.get("media_file_id") or ""),
                button_rows=item.get("buttons") or [],
                media_items=item.get("media") or [],
                user=recipient,
            )
            await increment_business_account_stat(owner_id, account_user_id, "templates_used")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Business Automation outgoing template failed owner=%s account=%s", owner_id, account_user_id)



business_automation_runtime = BusinessAutomationRuntime()
