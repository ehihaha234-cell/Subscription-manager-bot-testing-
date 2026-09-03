from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from config import ADMIN_IDS
from database.group_manager_protection import get_protection, increment_warn, clear_warn
from database.group_manager import get_moderation
from database.seller_data import get_channels
from database.staff import active_staff

logger = logging.getLogger(__name__)
FLOOD = defaultdict(lambda: deque(maxlen=50))
ADMIN_CACHE = {}
STAFF_CACHE = {}
URL_RE = re.compile(r"(?i)(?:https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/|@[A-Za-z0-9_]{5,})")

async def _is_chat_admin(bot, chat_id, user_id):
    key=(int(chat_id),int(user_id))
    now=time.monotonic()
    cached=ADMIN_CACHE.get(key)
    if cached and now-cached[0] < 20:
        return cached[1]
    try:
        member=await bot.get_chat_member(chat_id,user_id)
        value=member.status in {"administrator","creator"}
    except Exception:
        value=False
    ADMIN_CACHE[key]=(now,value)
    return value

async def _delete_ids(bot, chat_id, ids):
    if not ids:
        return
    await asyncio.gather(*(
        bot.delete_message(chat_id=chat_id,message_id=int(mid)) for mid in ids
    ), return_exceptions=True)

async def _punish(bot, owner, chat_id, user, action, *, warn_cfg, reason, reply_to=None, duration_seconds=None):
    action=str(action or "off").lower()
    if action=="off":
        return

    if action=="warn":
        try:
            count=await increment_warn(owner,chat_id,user.id,expires_seconds=duration_seconds)
        except TypeError:
            count=await increment_warn(owner,chat_id,user.id)
        except Exception:
            logger.exception("Anti-flood warning counter failed chat=%s user=%s",chat_id,user.id)
            count=1
        max_warns=int(warn_cfg.get("max_warns",3) or 3)
        warning_text=f"⚠️ {user.mention_html()} warned: {reason}\nWarns: {count}/{max_warns}"
        try:
            kwargs={"chat_id":chat_id,"text":warning_text,"parse_mode":"HTML"}
            if reply_to:
                kwargs["reply_to_message_id"]=reply_to
            await bot.send_message(**kwargs)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id,text=warning_text,parse_mode="HTML")
            except Exception:
                logger.exception("Anti-flood warning message failed chat=%s user=%s",chat_id,user.id)
        if count < max_warns:
            return
        action=str(warn_cfg.get("action") or "mute").lower()
        duration_seconds=int(warn_cfg.get("mute_minutes",30) or 30)*60
        try:
            await clear_warn(owner,chat_id,user.id)
        except Exception:
            pass

    if action not in {"kick","mute","ban"}:
        return

    seconds=int(duration_seconds or 0)
    until=datetime.now(timezone.utc)+timedelta(seconds=max(30,seconds or 1800))
    try:
        if action=="kick":
            await bot.ban_chat_member(chat_id,user.id)
            await bot.unban_chat_member(chat_id,user.id,only_if_banned=True)
        elif action=="ban":
            await bot.ban_chat_member(chat_id,user.id,until_date=until)
        elif action=="mute":
            await bot.restrict_chat_member(
                chat_id,user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
    except Exception:
        logger.exception("Anti-flood punishment failed action=%s chat=%s user=%s",action,chat_id,user.id)


def _forward_origin(m):
    return getattr(m, "forward_origin", None)

def _forward_type(m):
    """Return the configured forwarding category from Telegram's forward_origin.

    Telegram's modern API exposes the original sender as MessageOrigin* rather
    than through effective_user.  In particular, a bot-origin forward is a
    MessageOriginUser whose sender_user.is_bot is True, so it must be classified
    before the generic user case.
    """
    origin=_forward_origin(m)
    if not origin:
        return None
    name=origin.__class__.__name__.casefold()
    if "channel" in name:
        return "channels"
    if "chat" in name:
        return "groups"
    if "user" in name:
        sender=getattr(origin, "sender_user", None)
        return "bots" if bool(getattr(sender, "is_bot", False)) else "users"
    return None

async def _forward_is_connected_channel(owner, m):
    """Skip forwarding punishment for a configured subscription channel.

    A channel-origin forward can come from a channel connected with /connectgroup.
    Those connected channels are trusted content sources and are not treated as
    user/channel spam in the destination group.
    """
    origin=_forward_origin(m)
    if not origin or "channel" not in origin.__class__.__name__.casefold():
        return False
    source_chat=getattr(origin, "chat", None)
    source_id=getattr(source_chat, "id", None)
    if source_id is None:
        return False
    try:
        connected=await get_channels(int(owner))
    except Exception:
        logger.debug("Connected-channel lookup failed for forwarded message", exc_info=True)
        return False
    return any(int(item.get("chat_id", 0) or 0)==int(source_id) for item in (connected or []))

async def _forward_source_bot_is_destination_admin(bot, chat_id, m):
    """Return True when a forwarded bot is itself an admin of the destination.

    The person forwarding the message is not the original bot.  We therefore
    inspect MessageOriginUser.sender_user and exempt that bot when it is an
    administrator/creator of the protected group.
    """
    origin=_forward_origin(m)
    sender=getattr(origin, "sender_user", None) if origin else None
    if not sender or not bool(getattr(sender, "is_bot", False)):
        return False
    return await _is_chat_admin(bot, chat_id, int(sender.id))

async def anti_flood_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Early, isolated anti-flood pass. Must run before command/deletion guards."""
    m=update.effective_message
    chat=update.effective_chat
    user=update.effective_user
    if not m or not chat or not user or user.is_bot or chat.type not in {"group","supergroup"}:
        return

    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner:
        logger.error("Anti-flood skipped: seller_owner_id missing chat=%s", getattr(chat,"id",None))
        return

    # Anonymous admins send on behalf of the group itself.
    sender_chat=getattr(m,"sender_chat",None)
    if sender_chat is not None and int(getattr(sender_chat,"id",0) or 0)==int(chat.id):
        return

    # Only regular users are subject to anti-flood.
    if await _is_chat_admin(context.bot,chat.id,user.id):
        return
    if int(user.id)==int(context.application.bot_data.get("seller_account_id") or 0) or int(user.id) in {int(x) for x in (ADMIN_IDS or [])}:
        return
    cache_key=(owner,int(user.id)); now=time.monotonic(); cached=STAFF_CACHE.get(cache_key)
    if cached and now-cached[0] < 30:
        is_staff=cached[1]
    else:
        try:
            is_staff=bool(await active_staff(owner,user.id))
        except Exception:
            is_staff=False
        STAFF_CACHE[cache_key]=(now,is_staff)
    if is_staff:
        return

    p=await get_protection(owner,chat.id)
    flood=p.get("anti_flood") or {}
    action=str(flood.get("action","off") or "off").lower()
    if action=="off":
        return

    try:
        limit=max(2,int(flood.get("messages",5) or 5))
        seconds=max(1,int(flood.get("seconds",3) or 3))
    except Exception:
        limit,seconds=5,3

    key=(owner,int(chat.id),int(user.id))
    now=time.monotonic()
    dq=FLOOD[key]
    dq.append((now,int(m.message_id)))
    cutoff=now-seconds
    while dq and dq[0][0] < cutoff:
        dq.popleft()

    logger.info("Anti-flood check owner=%s chat=%s user=%s count=%s/%s window=%ss action=%s", owner,chat.id,user.id,len(dq),limit,seconds,action)
    if len(dq)<limit:
        return

    burst=[mid for _,mid in dq]
    dq.clear()
    if flood.get("delete",True):
        await _delete_ids(context.bot,chat.id,burst)

    warns=p.get("warns") or {}
    duration_key={"warn":"warn_duration_seconds","mute":"mute_duration_seconds","ban":"ban_duration_seconds"}.get(action)
    duration_seconds=int(flood.get(duration_key,0) or 0) if duration_key else 0
    await _punish(
        context.bot,owner,chat.id,user,action,
        warn_cfg=warns,reason="Anti-flood",
        reply_to=None if flood.get("delete",True) else m.message_id,
        duration_seconds=duration_seconds,
    )
    # Once a flood is detected, do not let the deleted burst reach other
    # handlers (auto-reply, live support, business automation, etc.).
    raise ApplicationHandlerStop


async def group_manager_protection_message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    m=update.effective_message
    chat=update.effective_chat
    user=update.effective_user
    if not m or not chat or chat.type not in {"group","supergroup"}:
        return

    # Service-message cleanup is independent of user/admin/anonymous checks.
    # This guarantees Join/Exit messages are removed when enabled, including
    # exits caused by an admin/bot, and it runs after Welcome (handler group -21
    # vs Welcome group -28).
    try:
        owner=int(context.application.bot_data.get("seller_owner_id") or 0)
        if owner:
            moderation=await get_moderation(owner, chat.id)
            service=moderation.get("service_messages") or {}
            service_enabled=bool(moderation.get("enabled", True))
            is_join=bool(getattr(m, "new_chat_members", None))
            is_exit=bool(getattr(m, "left_chat_member", None))
            if service_enabled and (
                (is_join and service.get("join")) or
                (is_exit and service.get("exit"))
            ):
                try:
                    await m.delete()
                except Exception:
                    logger.debug(
                        "Service message delete failed chat=%s message=%s",
                        chat.id, m.message_id, exc_info=True
                    )
                return
    except Exception:
        logger.debug("Service-message cleanup check failed", exc_info=True)

    if not m or not chat or not user or user.is_bot or chat.type not in {"group","supergroup"}:
        return

    owner=int(context.application.bot_data.get("seller_owner_id") or 0)
    if not owner:
        return

    # Anonymous group-admin messages have sender_chat set to the group itself.
    anonymous_admin=bool(getattr(m,"sender_chat",None) and int(getattr(m.sender_chat,"id",0) or 0)==int(chat.id))
    if anonymous_admin:
        return

    # Group admins, bot/global admins and seller staff are exempt from Anti-Flood.
    if await _is_chat_admin(context.bot,chat.id,user.id):
        return
    bot_admin=int(user.id)==int(context.application.bot_data.get("seller_account_id") or 0) or int(user.id) in {int(x) for x in (ADMIN_IDS or [])}
    if not bot_admin:
        cache_key=(owner,int(user.id)); now=time.monotonic(); cached=STAFF_CACHE.get(cache_key)
        if cached and now-cached[0] < 30:
            bot_admin=cached[1]
        else:
            try:
                bot_admin=bool(await active_staff(owner,user.id))
            except Exception:
                bot_admin=False
            STAFF_CACHE[cache_key]=(now,bot_admin)
    if bot_admin:
        return

    p=await get_protection(owner,chat.id)
    warns=p.get("warns") or {}

    # Existing protections remain user-only below this point.
    # Banned Words: run independently from the punishment selector.
    # If a word is configured and Delete Messages is ON, the offending
    # message is deleted even when the punishment is set to OFF.
    # This also handles punctuation, repeated spaces and mixed case.
    text=(m.text or m.caption or "")
    low=" ".join(str(text).casefold().split())
    bw=p.get("banned_words") or {}
    words=bw.get("words") or []
    if low and words:
        hit=None
        for raw_word in words:
            word=" ".join(str(raw_word or "").casefold().split()).strip()
            if not word:
                continue
            # Match a complete word/phrase, while allowing punctuation around it.
            # For multi-word phrases, whitespace in the configured phrase can
            # match any normalised whitespace in the incoming message.
            pattern=r"(?<!\\w)"+r"\\s+".join(re.escape(part) for part in word.split())+r"(?!\\w)"
            if re.search(pattern, low, re.IGNORECASE):
                hit=word
                break

        if hit:
            if bw.get("delete",True):
                await _delete_ids(context.bot,chat.id,[m.message_id])

            action=str(bw.get("action","off") or "off").lower()
            if action != "off":
                await _punish(
                    context.bot,owner,chat.id,user,action,
                    warn_cfg=warns,
                    reason=f"Banned word: {hit}",
                    reply_to=None if bw.get("delete",True) else m.message_id,
                )
            return

    spam=p.get("anti_spam") or {}
    fw=spam.get("forwarding") or {}
    ftype=_forward_type(m)
    if ftype:
        # Trusted/configured subscription channels are never punished by the
        # forwarding anti-spam rule.
        if ftype=="channels" and await _forward_is_connected_channel(owner, m):
            return

        # If the original sender is a bot and that bot is an administrator of
        # this destination group, treat it as an admin exemption rather than a
        # normal bot forward.
        if ftype=="bots" and await _forward_source_bot_is_destination_admin(context.bot, chat.id, m):
            return

        # Telegram represents anonymous/"send as chat" forwards as
        # MessageOriginChat.  Keep the category available for explicit Groups
        # protection, but never confuse the current anonymous admin with the
        # human user who performed the forwarding.  The normal destination admin
        # exemption above already handles admins forwarding regular user-origin
        # messages.
        if fw.get(ftype) and fw.get("action","off")!="off":
            if fw.get("delete",False):
                await _delete_ids(context.bot,chat.id,[m.message_id])
            await _punish(
                context.bot,owner,chat.id,user,fw.get("action"),
                warn_cfg=warns,reason="Forwarded message",reply_to=m.message_id
            )
            return

    # Telegram Links: deletion and punishment are intentionally independent.
    # "Penalty: Off" must disable only warn/mute/kick/ban; it must NOT disable
    # link detection or "Delete Messages".  The previous action!=off guard made
    # the UI show Deletion ON while Telegram deep-links (including ?startgroup=)
    # were silently allowed.
    tg=spam.get("telegram_links") or {}
    if text:
        is_tg=bool(re.search(
            r"(?i)(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/",
            text,
        ))
        if tg.get("username_antispam") and re.search(r"(?<!\w)@[A-Za-z0-9_]{5,}",text):
            is_tg=True
        if tg.get("bots_antispam") and re.search(r"(?i)(?:t\.me/|@)[A-Za-z0-9_]*bot\b",text):
            is_tg=True

        # Run when at least one Telegram protection action is configured.
        # This keeps the feature dormant when everything is OFF, while allowing
        # delete-only moderation with Penalty set to Off.
        tg_action=str(tg.get("action","off") or "off").lower()
        tg_delete=bool(tg.get("delete",False))
        tg_enabled=bool(tg.get("username_antispam") or tg.get("bots_antispam") or tg_delete or tg_action!="off")
        if tg_enabled and is_tg:
            if tg_delete:
                await _delete_ids(context.bot,chat.id,[m.message_id])
            if tg_action!="off":
                await _punish(
                    context.bot,owner,chat.id,user,tg_action,
                    warn_cfg=warns,reason="Telegram link",
                    reply_to=None if tg_delete else m.message_id,
                )
            return

    total=spam.get("total_links") or {}
    if total.get("action","off")!="off" and text and URL_RE.search(text):
        if total.get("delete",False): await _delete_ids(context.bot,chat.id,[m.message_id])
        await _punish(context.bot,owner,chat.id,user,total.get("action"),warn_cfg=warns,reason="Link",reply_to=m.message_id)
        return
