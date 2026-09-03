"""Official Telegram Business automation for clone bots.

This handles Bot API ``business_connection`` and ``business_message`` updates.
Normal MTProto account automation remains in ``services.business_automation_runtime``.
"""

from __future__ import annotations

from handlers.common.feature_navigation import register_feature_origin

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, InputMediaPhoto,
    InputMediaVideo, Update,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from database.business_delivery import record_business_contact
from database.business_automation import (
    get_business_welcome, list_business_auto_replies, list_business_reply_templates,
    mark_business_recipient_inactive, upsert_business_recipient,
)
from database.business_official import (
    get_official_business_connection,
    increment_official_business_stat,
    save_official_business_connection,
)
from database.seller_bots import get_bot_by_data_owner_id
from utils.branding import append_branding
from database.seller_data import (
    claim_business_welcome,
    get_seller_settings,
    reset_business_welcome,
    reset_business_welcome_for_peer,
    set_business_welcome_message_ids,
)

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
        "{NAME}": first or name, "{FIRSTNAME}": first, "{SURNAME}": last,
        "{NAMESURNAME}": name, "{ID}": user_id, "{USERNAME}": username,
        "{MENTION}": mention, "{LANG}": str(getattr(user, "language_code", "") or ""),
        "{DATE}": now.strftime("%d %b %Y"),
        "{TIME}": now.strftime("%I:%M %p"), "{WEEKDAY}": now.strftime("%A"),
    }


def _render_variables(value: str, user) -> str:
    rendered = str(value or "")
    for token, replacement in _variable_values(user).items():
        rendered = rendered.replace(token, replacement)
    return rendered


def _render_button_rows(rows, user):
    result = []
    for row in rows or []:
        clean = []
        for item in row or []:
            copy = dict(item)
            copy["text"] = _render_variables(copy.get("text") or "", user)
            if "value" in copy:
                copy["value"] = _render_variables(copy.get("value") or "", user)
            if "url" in copy:
                copy["url"] = _render_variables(copy.get("url") or "", user)
            clean.append(copy)
        if clean:
            result.append(clean)
    return result

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
        logger.exception("Invalid official Business Automation working-hours settings")
        return True


async def _inline_markup(owner_id: int, rows) -> InlineKeyboardMarkup | None:
    result: list[list[InlineKeyboardButton]] = []
    bot_record = None
    for row in rows or []:
        clean: list[InlineKeyboardButton] = []
        for item in row or []:
            text = str(item.get("text") or "Open")[:64]
            value = str(item.get("value") or item.get("url") or "").strip()
            item_type = str(item.get("type") or ("url" if item.get("url") else ""))
            if item_type == "callback" and value:
                clean.append(InlineKeyboardButton(text, callback_data=value[:64]))
            elif item_type == "url" and value:
                clean.append(InlineKeyboardButton(text, url=value))
            elif item_type == "clone" and value:
                if bot_record is None:
                    bot_record = await get_bot_by_data_owner_id(int(owner_id))
                username = str((bot_record or {}).get("bot_username") or "").lstrip("@")
                if username:
                    clean.append(InlineKeyboardButton(text, url=f"https://t.me/{username}?start={value}"))
            elif value:
                # Older editor records may omit the type. Preserve clone feature
                # callbacks and treat web links as URL buttons.
                if value.startswith(("http://", "https://", "tg://")):
                    clean.append(InlineKeyboardButton(text, url=value))
                elif value.startswith("c_"):
                    clean.append(InlineKeyboardButton(text, callback_data=value[:64]))
                else:
                    if bot_record is None:
                        bot_record = await get_bot_by_data_owner_id(int(owner_id))
                    username = str((bot_record or {}).get("bot_username") or "").lstrip("@")
                    if username:
                        clean.append(InlineKeyboardButton(text, url=f"https://t.me/{username}?start={value}"))
        if clean:
            result.append(clean)
    return InlineKeyboardMarkup(result) if result else None


async def _send_configured_message(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    business_connection_id: str,
    owner_id: int,
    text: str,
    media_type: str = "",
    media_file_id: str = "",
    media_items=None,
    button_rows=None,
    user=None,
) -> list[int]:
    """Send saved media as one Telegram album, followed by text/buttons.

    Telegram media groups do not support inline keyboards. Therefore, when
    multiple media items are configured, the complete album is sent first and
    the configured text/buttons are sent as one message immediately after it.
    """
    rendered_text = _render_variables(text, user) if user is not None else str(text or "")
    rendered_rows = _render_button_rows(button_rows or [], user) if user is not None else (button_rows or [])
    markup = await _inline_markup(owner_id, rendered_rows)
    items = list(media_items or [])
    if not items and media_file_id:
        items = [{"type": media_type or "document", "file_id": media_file_id}]
    items = [m for m in items if m.get("file_id")][:10]

    common = {
        "chat_id": int(chat_id),
        "business_connection_id": str(business_connection_id),
    }

    if len(items) > 1:
        album = []
        for item in items:
            kind = str(item.get("type") or "document").lower()
            file_id = str(item.get("file_id") or "")
            if kind == "photo":
                album.append(InputMediaPhoto(media=file_id))
            elif kind == "video":
                album.append(InputMediaVideo(media=file_id))
            else:
                # Telegram albums support photos, videos, audio, and documents.
                # GIF/animation is stored as a document inside mixed albums.
                album.append(InputMediaDocument(media=file_id))
        sent = await context.bot.send_media_group(media=album, **common)
        message_ids = [int(m.message_id) for m in sent if getattr(m, "message_id", None)]
        if rendered_text or markup:
            text_message = await context.bot.send_message(
                text=rendered_text or "Choose an option below.",
                reply_markup=markup,
                **common,
            )
            if getattr(text_message, "message_id", None):
                register_feature_origin(text_message, text=rendered_text or "Choose an option below.", markup=markup)
                message_ids.append(int(text_message.message_id))
        return message_ids

    if len(items) == 1:
        item = items[0]
        kind = str(item.get("type") or "document").lower()
        file_id = str(item.get("file_id") or "")
        single_common = {**common, "caption": rendered_text or None, "reply_markup": markup}
        if kind == "photo":
            sent = await context.bot.send_photo(photo=file_id, **single_common)
        elif kind == "video":
            sent = await context.bot.send_video(video=file_id, **single_common)
        elif kind == "animation":
            sent = await context.bot.send_animation(animation=file_id, **single_common)
        else:
            sent = await context.bot.send_document(document=file_id, **single_common)
        register_feature_origin(sent, text=rendered_text, markup=markup)
        return [int(sent.message_id)] if getattr(sent, "message_id", None) else []

    sent = await context.bot.send_message(
        text=rendered_text or "Welcome!",
        reply_markup=markup,
        **common,
    )
    register_feature_origin(sent, text=rendered_text or "Welcome!", markup=markup)
    return [int(sent.message_id)] if getattr(sent, "message_id", None) else []


async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connection = update.business_connection
    if connection is None:
        return
    owner_id = int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner_id:
        return
    await save_official_business_connection(owner_id, connection)
    logger.info(
        "Official business connection updated owner=%s connection=%s enabled=%s user=%s",
        owner_id,
        connection.id,
        connection.is_enabled,
        getattr(connection.user, "id", None),
    )


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.business_message
    if message is None or not message.business_connection_id:
        return
    if message.chat.type != "private":
        return

    owner_id = int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner_id:
        return
    connection_id = str(message.business_connection_id)

    try:
        connection_doc = await get_official_business_connection(owner_id, connection_id)
        if not connection_doc:
            connection = await context.bot.get_business_connection(connection_id)
            connection_doc = await save_official_business_connection(owner_id, connection)
        if not connection_doc.get("enabled", True) or not connection_doc.get("can_reply", True):
            return

        business_user_id = int(connection_doc.get("business_user_id") or 0)
        sender = message.from_user
        sender_id = int(getattr(sender, "id", 0) or 0)
        if not sender_id or bool(getattr(sender, "is_bot", False)):
            return

        # A seller sends a template keyword alone; replace that message with the
        # configured reply template. These updates must never enter Live Support.
        if sender_id == business_user_id:
            raw = str(message.text or message.caption or "").strip()
            if raw and not any(ch.isspace() for ch in raw):
                templates = await list_business_reply_templates(owner_id)
                template = next((x for x in templates if raw.casefold() == str(x.get("shortcut") or "").strip().casefold()), None)
                if template:
                    try:
                        await context.bot.delete_business_messages(connection_id, [message.message_id])
                    except Exception:
                        logger.debug("Could not delete official business template keyword", exc_info=True)
                    await _send_configured_message(
                        context, chat_id=message.chat_id, business_connection_id=connection_id,
                        owner_id=owner_id, text=str(template.get("text") or template.get("name") or ""),
                        media_type=str(template.get("media_type") or ""), media_file_id=str(template.get("media_file_id") or ""),
                        media_items=template.get("media") or [], button_rows=template.get("buttons") or [],
                        user=message.chat,
                    )
                    await increment_official_business_stat(owner_id, connection_id, "templates_used")
            return

        await upsert_business_recipient(owner_id, connection_id, message.chat_id, sender)
        # Keep a direct numeric-ID route for payment-success mirroring. This is
        # independent of usernames and survives username changes.
        await record_business_contact(
            owner_id,
            sender_id,
            mode="official",
            account_user_id=business_user_id,
            connection_id=connection_id,
            chat_id=message.chat_id,
        )

        settings = await get_seller_settings(owner_id)
        if not settings.get("business_automation_enabled"):
            return
        if not _inside_working_hours(settings):
            return

        welcome = await get_business_welcome(owner_id)
        auto_replies = await list_business_auto_replies(owner_id)
        first_contact = await claim_business_welcome(
            owner_id,
            business_user_id or owner_id,
            sender_id,
            welcome_once=True,
        )

        delay = max(0, min(int(settings.get("business_reply_delay_seconds", 0) or 0), 300))
        if delay:
            await asyncio.sleep(delay)

        welcome_sent = False
        if welcome.get("enabled", True) and first_contact:
            text = str(welcome.get("text") or "").strip()
            media_file_id = str(welcome.get("media_file_id") or "")
            media_items = list(welcome.get("media") or [])
            if text or media_file_id or media_items:
                text = await append_branding(text)
                welcome_message_ids = await _send_configured_message(
                    context,
                    chat_id=message.chat_id,
                    business_connection_id=connection_id,
                    owner_id=owner_id,
                    text=text,
                    media_type=str(welcome.get("media_type") or ""),
                    media_file_id=media_file_id,
                    media_items=media_items,
                    button_rows=welcome.get("buttons") or [],
                    user=sender,
                )
                await set_business_welcome_message_ids(
                    owner_id,
                    business_user_id or owner_id,
                    sender_id,
                    welcome_message_ids,
                )
                await increment_official_business_stat(owner_id, connection_id, "welcome_sent")
                welcome_sent = True

        if not welcome_sent:
            incoming_text = str(message.text or message.caption or "").strip()
            match = next((x for x in auto_replies if x.get("enabled", True) and _keyword_in_message(str(x.get("keyword") or ""), incoming_text)), None)
            if match:
                text = str(match.get("text") or "").strip()
                media_file_id = str(match.get("media_file_id") or "")
                media_items = list(match.get("media") or [])
                if text or media_file_id or media_items:
                    await _send_configured_message(
                        context, chat_id=message.chat_id, business_connection_id=connection_id,
                        owner_id=owner_id, text=text,
                        media_type=str(match.get("media_type") or ""), media_file_id=media_file_id,
                        media_items=media_items, button_rows=match.get("buttons") or [],
                        user=sender,
                    )
                    await increment_official_business_stat(owner_id, connection_id, "auto_replies_sent")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Official Business Automation message failed owner=%s connection=%s chat=%s",
            owner_id,
            connection_id,
            getattr(message, "chat_id", None),
        )


async def handle_deleted_business_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deleted = update.deleted_business_messages
    if deleted is None:
        return
    owner_id = int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner_id:
        return
    connection_id = str(deleted.business_connection_id or "")
    if not connection_id:
        return
    try:
        connection_doc = await get_official_business_connection(owner_id, connection_id)
        if not connection_doc:
            connection = await context.bot.get_business_connection(connection_id)
            connection_doc = await save_official_business_connection(owner_id, connection)
        business_user_id = int(connection_doc.get("business_user_id") or owner_id)
        chat_id = int(deleted.chat.id)
        # A Telegram chat-history clear or message deletion must not make the
        # customer a first-time contact again.  The welcome claim is permanent
        # per owner/account/customer, so keep BUSINESS_CONTACTS untouched.
        logger.info(
            "Business messages deleted; permanent welcome claim preserved "
            "owner=%s account=%s chat=%s ids=%s",
            owner_id,
            business_user_id,
            chat_id,
            list(deleted.message_ids or []),
        )
    except Exception:
        logger.exception("Could not reset Business welcome after deleted messages owner=%s", owner_id)


async def _broadcast_api_call(call, *, attempts: int = 12):
    """Run one Business API request with Telegram-safe retry handling.

    Telegram's ``RetryAfter`` value is mandatory.  The old implementation
    capped every wait at 30 seconds, so a request such as ``retry in 449
    seconds`` was retried too early and the remaining recipients were counted
    as failed.  Wait for the complete server-requested duration and retry the
    same request instead.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            return await call()
        except RetryAfter as exc:
            last_error = exc
            wait = getattr(exc, "retry_after", 1)
            try:
                wait = wait.total_seconds()
            except AttributeError:
                pass
            wait_seconds = max(float(wait), 1.0)
            logger.warning(
                "Business broadcast rate limited; waiting %.1f seconds before retry %s/%s",
                wait_seconds,
                attempt + 1,
                attempts,
            )
            # Add a small safety margin because retrying at the exact boundary
            # can immediately trigger another flood wait.
            await asyncio.sleep(wait_seconds + 1.0)
        except (TimedOut, NetworkError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
            raise
    if last_error:
        raise last_error



def _telegram_error_details(exc: Exception) -> str:
    """Return a safe, useful Telegram exception description for Render logs."""
    parts = [f"type={type(exc).__name__}"]
    message = str(exc or "").strip()
    if message:
        parts.append(f"message={message}")

    # python-telegram-bot exceptions may expose extra response information on
    # different versions. Read it defensively so diagnostics never break the
    # broadcast itself.
    for attr in ("retry_after", "new_chat_id", "migrate_to_chat_id"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={value}")

    return " | ".join(parts)


def _log_broadcast_send_error(
    stage: str,
    exc: Exception,
    *,
    owner_id: int | None = None,
    chat_id: int | None = None,
    connection_id: str = "",
    media_type: str = "",
    file_id: str = "",
) -> None:
    # Log only a short file-id prefix; the complete identifier is unnecessary
    # and makes Render logs hard to read.
    file_hint = f"{file_id[:18]}..." if file_id else ""
    logger.warning(
        "Business broadcast Telegram error stage=%s owner=%s chat=%s "
        "connection=%s media_type=%s file_id=%s reason=%s",
        stage, owner_id, chat_id, connection_id, media_type, file_hint,
        _telegram_error_details(exc),
    )


def _is_permanently_unreachable(exc: Exception) -> bool:
    """Return True when retrying the same recipient cannot fix delivery.

    ``BUSINESS_PEER_USAGE_MISSING`` is returned by Telegram when the current
    Business connection is not allowed to use that peer. Retrying the same
    request does not change that permission, so the recipient must be skipped
    and the broadcast must continue.
    """
    text = str(exc or "").casefold().replace("-", "_")
    if isinstance(exc, Forbidden):
        # Forbidden responses are permanent for this broadcast attempt.
        return True
    if isinstance(exc, BadRequest):
        return any(token in text for token in (
            "chat not found", "user not found", "user is deactivated",
            "peer_id_invalid", "peer id invalid", "account deleted",
            "business_peer_usage_missing", "business peer usage missing",
            "business connection not found", "business connection disabled",
        ))
    return False


def _is_retryable_broadcast_error(exc: Exception) -> bool:
    """Retry only errors that can realistically recover without changing data."""
    return isinstance(exc, (RetryAfter, TimedOut, NetworkError))

def _broadcast_failure_reason(exc: Exception) -> str:
    text = str(exc or "").casefold()
    if isinstance(exc, Forbidden):
        if "blocked" in text:
            return "blocked_or_forbidden"
        return "no_permission"
    if isinstance(exc, BadRequest):
        normalized = text.replace("-", "_")
        if "business_peer_usage_missing" in normalized or "business peer usage missing" in text:
            return "business_peer_unavailable"
        if "business connection" in text and ("not found" in text or "disabled" in text):
            return "connection_disabled"
        if "chat not found" in text:
            return "chat_unavailable"
        if "button" in text or "url" in text or "reply markup" in text:
            return "invalid_buttons"
        if "file" in text or "media" in text:
            return "invalid_media"
        return "bad_request"
    if isinstance(exc, RetryAfter):
        return "rate_limited"
    if isinstance(exc, (TimedOut, NetworkError)):
        return "temporary_network_error"
    return "unknown_error"


async def _send_broadcast_media(
    context,
    common: dict,
    items: list[dict],
    *,
    text: str = "",
    markup=None,
) -> dict:
    """Send broadcast media with the fewest possible Telegram API calls.

    A single media item carries its caption and buttons in the same request,
    which prevents the old "media only" partial-delivery problem. Albums carry
    the text as the first caption; buttons are sent separately because Telegram
    does not support reply markup on ``send_media_group``.
    """
    clean = [m for m in (items or []) if str(m.get("file_id") or "")][:10]
    if not clean:
        return {"media": True, "text": not bool(text), "buttons": markup is None, "errors": []}

    errors = []
    visual = [m for m in clean if str(m.get("type") or "").lower() in {"photo", "video"}]
    other = [m for m in clean if m not in visual]
    media_ok = True
    text_ok = not bool(text)
    buttons_ok = markup is None

    # Best case: one media item. Caption and buttons travel in the same call.
    if len(clean) == 1:
        item = clean[0]
        kind = str(item.get("type") or "document").lower()
        fid = str(item.get("file_id") or "")
        kwargs = dict(common)
        if text:
            kwargs["caption"] = text
        if markup is not None:
            kwargs["reply_markup"] = markup
        try:
            if kind == "photo":
                sent = await _broadcast_api_call(lambda: context.bot.send_photo(photo=fid, **kwargs))
            elif kind == "video":
                sent = await _broadcast_api_call(lambda: context.bot.send_video(video=fid, **kwargs))
            elif kind == "animation":
                sent = await _broadcast_api_call(lambda: context.bot.send_animation(animation=fid, **kwargs))
            else:
                sent = await _broadcast_api_call(lambda: context.bot.send_document(document=fid, **kwargs))
            register_feature_origin(sent, text=text, markup=markup)
            return {"media": True, "text": True, "buttons": True, "errors": []}
        except Exception as exc:
            _log_broadcast_send_error(
                "combined_media", exc,
                chat_id=common.get("chat_id"),
                connection_id=str(common.get("business_connection_id") or ""),
                media_type=kind, file_id=fid,
            )
            errors.append(("combined_media", exc))
            return {"media": False, "text": not bool(text), "buttons": markup is None, "errors": errors}

    caption_used = False
    if visual:
        try:
            if len(visual) > 1:
                album = []
                for index, entry in enumerate(visual[:10]):
                    fid = str(entry.get("file_id") or "")
                    caption = text if index == 0 and text else None
                    if str(entry.get("type") or "").lower() == "photo":
                        album.append(InputMediaPhoto(media=fid, caption=caption))
                    else:
                        album.append(InputMediaVideo(media=fid, caption=caption))
                await _broadcast_api_call(lambda: context.bot.send_media_group(media=album, **common))
                caption_used = bool(text)
            else:
                entry = visual[0]
                fid = str(entry.get("file_id") or "")
                kwargs = dict(common)
                if text:
                    kwargs["caption"] = text
                    caption_used = True
                if str(entry.get("type") or "").lower() == "photo":
                    await _broadcast_api_call(lambda: context.bot.send_photo(photo=fid, **kwargs))
                else:
                    await _broadcast_api_call(lambda: context.bot.send_video(video=fid, **kwargs))
        except Exception as exc:
            media_ok = False
            _log_broadcast_send_error(
                "visual_media", exc,
                chat_id=common.get("chat_id"),
                connection_id=str(common.get("business_connection_id") or ""),
                media_type="album" if len(visual) > 1 else str(visual[0].get("type") or ""),
                file_id=str(visual[0].get("file_id") or "") if visual else "",
            )
            errors.append(("visual_media", exc))

    remaining_slots = max(0, 10 - len(visual))
    for index, entry in enumerate(other[:remaining_slots]):
        kind = str(entry.get("type") or "document").lower()
        fid = str(entry.get("file_id") or "")
        kwargs = dict(common)
        if text and not caption_used and index == 0:
            kwargs["caption"] = text
            caption_used = True
        try:
            if kind == "animation":
                await _broadcast_api_call(lambda fid=fid, kwargs=kwargs: context.bot.send_animation(animation=fid, **kwargs))
            else:
                await _broadcast_api_call(lambda fid=fid, kwargs=kwargs: context.bot.send_document(document=fid, **kwargs))
        except Exception as exc:
            media_ok = False
            _log_broadcast_send_error(
                "other_media", exc,
                chat_id=common.get("chat_id"),
                connection_id=str(common.get("business_connection_id") or ""),
                media_type=kind, file_id=fid,
            )
            errors.append(("other_media", exc))

    text_ok = (not bool(text)) or caption_used

    # Telegram albums cannot carry inline buttons. Send a compact button-only
    # message after successful media. If the caption could not be attached,
    # include the text here as a fallback.
    if markup is not None or (text and not caption_used):
        fallback_text = text if not caption_used else "Choose an option below."
        try:
            sent = await _broadcast_api_call(lambda: context.bot.send_message(
                text=fallback_text or "Broadcast message", reply_markup=markup, **common
            ))
            register_feature_origin(sent, text=fallback_text or "Broadcast message", markup=markup)
            text_ok = True
            buttons_ok = True
        except Exception as exc:
            _log_broadcast_send_error(
                "buttons_or_text", exc,
                chat_id=common.get("chat_id"),
                connection_id=str(common.get("business_connection_id") or ""),
            )
            errors.append(("buttons_or_text", exc))
            # Invalid markup must not block plain text delivery.
            if markup is not None and text and not caption_used:
                try:
                    await _broadcast_api_call(lambda: context.bot.send_message(text=text, **common))
                    text_ok = True
                except Exception as fallback_exc:
                    _log_broadcast_send_error(
                        "text_fallback", fallback_exc,
                        chat_id=common.get("chat_id"),
                        connection_id=str(common.get("business_connection_id") or ""),
                    )
                    errors.append(("text_fallback", fallback_exc))

    return {"media": media_ok, "text": text_ok, "buttons": buttons_ok, "errors": errors}


async def _send_one_official_business_broadcast(context, owner_id: int, item: dict, recipient: dict) -> dict:
    class Recipient:
        pass
    user = Recipient()
    user.id = int(recipient.get("chat_id") or 0)
    user.first_name = str(recipient.get("first_name") or "")
    user.last_name = str(recipient.get("last_name") or "")
    user.username = str(recipient.get("username") or "")

    chat_id = int(recipient.get("chat_id") or 0)
    connection_id = str(recipient.get("connection_id") or "")
    if not chat_id or not connection_id:
        return {"status": "pending", "reason": "invalid_recipient", "components": {}}

    common = {"chat_id": chat_id, "business_connection_id": connection_id}
    rendered_text = _render_variables(str(item.get("text") or ""), user)
    rendered_rows = _render_button_rows(item.get("buttons") or [], user)
    markup = await _inline_markup(owner_id, rendered_rows)
    media_items = list(item.get("media") or [])
    if not media_items and item.get("media_file_id"):
        media_items = [{"type": item.get("media_type") or "document", "file_id": item.get("media_file_id")}]
    media_items = [m for m in media_items if m.get("file_id")][:10]

    errors = []
    components = {
        "media": not bool(media_items),
        "text": not bool(rendered_text),
        "buttons": markup is None,
    }

    if media_items:
        result = await _send_broadcast_media(
            context, common, media_items, text=rendered_text, markup=markup
        )
        components.update({k: bool(result.get(k)) for k in ("media", "text", "buttons")})
        errors.extend(result.get("errors") or [])
    elif rendered_text or markup:
        try:
            sent = await _broadcast_api_call(lambda: context.bot.send_message(
                text=rendered_text or "Choose an option below.", reply_markup=markup, **common
            ))
            register_feature_origin(sent, text=rendered_text or "Choose an option below.", markup=markup)
            components["text"] = True
            components["buttons"] = True
        except Exception as exc:
            _log_broadcast_send_error(
                "message", exc, owner_id=owner_id, chat_id=chat_id,
                connection_id=connection_id,
            )
            errors.append(("message", exc))
            if markup is not None and rendered_text:
                try:
                    await _broadcast_api_call(lambda: context.bot.send_message(text=rendered_text, **common))
                    components["text"] = True
                except Exception as retry_exc:
                    _log_broadcast_send_error(
                        "message_text_fallback", retry_exc, owner_id=owner_id,
                        chat_id=chat_id, connection_id=connection_id,
                    )
                    errors.append(("text_fallback", retry_exc))

    required = [
        components["media"] if media_items else True,
        components["text"] if rendered_text else True,
        components["buttons"] if markup is not None else True,
    ]
    delivered = sum(1 for value in required if value)
    total_required = len(required)

    if all(required):
        return {"status": "full", "reason": "", "components": components}

    last_exc = errors[-1][1] if errors else None
    reason = _broadcast_failure_reason(last_exc) if last_exc else "partial_delivery"
    if delivered > 0:
        status = "partial"
    elif last_exc is not None:
        # Never stop the first delivery pass for a Telegram rejection.
        # The caller moves this recipient to the end of the retry queue and
        # immediately continues with the next recipient. Final failure is
        # decided only after all configured retry rounds are exhausted.
        status = "retry"
    else:
        status = "retry"
    logger.warning(
        "Business broadcast %s owner=%s chat=%s connection=%s components=%s "
        "reason=%s telegram_error=%s",
        status, owner_id, chat_id, connection_id, components, reason,
        _telegram_error_details(last_exc) if last_exc is not None else "none",
    )
    return {"status": status, "reason": reason, "components": components}


async def send_official_business_broadcast(context, owner_id: int, item: dict, recipients: list[dict], progress_callback=None) -> dict:
    """Deliver sequentially with an end-of-queue retry strategy.

    A Telegram rejection never blocks the current pass and is not marked as
    failed immediately. The recipient is moved to the end of a retry queue,
    while delivery continues with the next recipient. After three retry
    rounds, permanent Telegram rejections become failed; temporary/system
    errors remain pending for a later run.
    """
    final_results = {}
    retry_queue = list(recipients)
    # Initial pass + three retry rounds.
    retry_delays = (3.0, 10.0, 30.0)
    total_rounds = len(retry_delays) + 1

    for round_index in range(total_rounds):
        next_retry_queue = []
        for index, recipient in enumerate(retry_queue, 1):
            key = (str(recipient.get("connection_id") or ""), int(recipient.get("chat_id") or 0))
            last_exception = None
            try:
                result = await _send_one_official_business_broadcast(
                    context, owner_id, item, recipient
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exception = exc
                logger.exception(
                    "Business broadcast recipient queued for retry owner=%s round=%s index=%s/%s "
                    "chat=%s connection=%s error=%s",
                    owner_id, round_index + 1, index, len(retry_queue),
                    recipient.get("chat_id"), recipient.get("connection_id"),
                    _telegram_error_details(exc),
                )
                result = {
                    "status": "retry",
                    "reason": _broadcast_failure_reason(exc),
                    "components": {},
                    "retryable": _is_retryable_broadcast_error(exc),
                    "permanent": _is_permanently_unreachable(exc),
                }

            status = str(result.get("status") or "retry")
            if status == "retry":
                # Save enough classification data for finalization after the
                # last retry round. _send_one already converted API errors to
                # a reason, so infer permanence from the known reason when an
                # exception object is unavailable here.
                reason = str(result.get("reason") or "telegram_rejected")
                permanent_reason = reason in {
                    "blocked", "blocked_or_forbidden", "deleted_account",
                    "chat_not_found", "chat_unavailable", "no_permission",
                    "business_peer_unavailable", "business_connection_invalid",
                    "connection_disabled", "peer_invalid", "forbidden",
                    "invalid_buttons", "invalid_media", "bad_request",
                }
                is_last_round = round_index == total_rounds - 1
                if not is_last_round:
                    result["status"] = "retry"
                    next_retry_queue.append(recipient)
                elif bool(result.get("permanent")) or permanent_reason:
                    result["status"] = "failed"
                    try:
                        await mark_business_recipient_inactive(
                            owner_id, str(recipient.get("connection_id") or ""),
                            int(recipient.get("chat_id") or 0), reason
                        )
                    except Exception:
                        logger.debug("Could not mark business recipient inactive", exc_info=True)
                else:
                    # Temporary/system errors are not reported as permanent
                    # failures after retries; keep them pending for a future run.
                    result["status"] = "pending"

            final_results[key] = result

            if progress_callback is not None:
                counts_now = Counter(str(x.get("status") or "retry") for x in final_results.values())
                completed_now = counts_now["full"] + counts_now["partial"] + counts_now["failed"]
                retry_count = counts_now["retry"] + counts_now["pending"] + len(next_retry_queue)
                # Count each recipient only once even while it moves through
                # retry rounds. Remaining means not yet finally resolved.
                unresolved = sum(1 for x in final_results.values() if str(x.get("status")) in {"retry", "pending"})
                untouched = max(len(recipients) - len(final_results), 0)
                await progress_callback({
                    "total": len(recipients),
                    "full": counts_now["full"],
                    "partial": counts_now["partial"],
                    "failed": counts_now["failed"],
                    "pending": counts_now["pending"],
                    "retry_queue": unresolved,
                    "processed": completed_now,
                    "remaining": unresolved + untouched,
                    "round": round_index + 1,
                    "current": recipient,
                    "state": "sending",
                })

            # Move immediately to the next recipient; rejected recipients are
            # retried only after the current pass finishes.
            if index < len(retry_queue):
                await asyncio.sleep(0.75)

        retry_queue = next_retry_queue
        if not retry_queue:
            break
        if round_index < len(retry_delays):
            delay = retry_delays[round_index]
            logger.warning(
                "Business broadcast retry queue size=%s round=%s wait=%.1fs owner=%s",
                len(retry_queue), round_index + 2, delay, owner_id,
            )
            if progress_callback is not None:
                counts_now = Counter(str(x.get("status") or "retry") for x in final_results.values())
                resolved = counts_now["full"] + counts_now["partial"] + counts_now["failed"]
                await progress_callback({
                    "total": len(recipients),
                    "full": counts_now["full"],
                    "partial": counts_now["partial"],
                    "failed": counts_now["failed"],
                    "pending": counts_now["pending"],
                    "retry_queue": len(retry_queue),
                    "processed": resolved,
                    "remaining": len(retry_queue),
                    "round": round_index + 2,
                    "state": "retry_wait",
                    "wait_seconds": delay,
                })
            await asyncio.sleep(delay)

    counts = Counter()
    reasons = Counter()
    component_failures = Counter()
    for result in final_results.values():
        status = str(result.get("status") or "pending")
        if status == "retry":
            status = "pending"
        counts[status] += 1
        reason = str(result.get("reason") or "")
        if reason:
            reasons[reason] += 1
        for component, delivered in (result.get("components") or {}).items():
            if not delivered:
                component_failures[str(component)] += 1

    return {
        "total": len(recipients),
        "full": counts["full"],
        "partial": counts["partial"],
        "failed": counts["failed"],
        "pending": counts["pending"],
        "retry_queue": 0,
        "reasons": dict(reasons),
        "component_failures": dict(component_failures),
    }

