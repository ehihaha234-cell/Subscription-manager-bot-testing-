import re
import html
import logging
from datetime import datetime
import time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from database.group_manager import get_group, update_welcome, get_auto_reply, get_template, get_moderation
from handlers.clone.group_manager_buttons import build_group_keyboard, find_button
from database.seller_bots import get_bot_by_data_owner_id

logger = logging.getLogger(__name__)


async def vars_text(text, user, chat, bot):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    first = html.escape(str(getattr(user, "first_name", "") or "User"))
    surname = html.escape(str(getattr(user, "last_name", "") or ""))
    full_name = html.escape(str(getattr(user, "full_name", "") or (first + (" " + surname if surname else ""))))
    username_raw = str(getattr(user, "username", "") or "")
    username = f"@{html.escape(username_raw)}" if username_raw else ""
    lang = html.escape(str(getattr(user, "language_code", "") or ""))
    group_name = html.escape(str(getattr(chat, "title", "") or "Group"))
    mention = f'<a href="tg://user?id={user.id}">{first}</a>'

    # get_chat() is a Telegram API call. Do not make it for every Welcome;
    # only fetch the group description when the Welcome actually uses {RULES}.
    rules = ""
    if "{RULES}" in text:
        try:
            full_chat = await bot.get_chat(chat.id)
            rules = html.escape(str(getattr(full_chat, "description", "") or ""))
        except Exception:
            rules = html.escape(str(getattr(chat, "description", "") or ""))

    vals = {
        "{ID}": str(user.id),
        "{NAME}": first,
        "{SURNAME}": surname,
        "{NAMESURNAME}": full_name,
        "{LANG}": lang,
        "{DATE}": now.strftime("%d-%m-%Y"),
        "{TIME}": now.strftime("%I:%M %p"),
        "{WEEKDAY}": now.strftime("%A"),
        "{MENTION}": mention,
        "{USERNAME}": username,
        "{GROUPNAME}": group_name,
        "{GROUP}": group_name,  # legacy alias
        "{RULES}": rules,
    }
    for key, value in vals.items():
        text = text.replace(key, value)
    return text

async def _send(bot,chat_id,item,text,markup,reply_to=None):
    """Send a configured group-manager item without dropping button-only setups."""
    media = item.get('media') or []
    common = {'chat_id': chat_id, 'parse_mode': 'HTML', 'reply_markup': markup}
    if reply_to:
        common['reply_to_message_id'] = reply_to

    if not media:
        # Telegram cannot send an empty text message. A Welcome configured with
        # only inline buttons is still a valid setup, so keep the buttons and
        # provide a minimal fallback message instead of silently doing nothing.
        return await bot.send_message(text=text or '👋 Welcome!', **common)

    entry = media[0]
    typ = entry.get('type')
    fid = entry.get('file_id')
    if text:
        common['caption'] = text
    if typ == 'photo':
        return await bot.send_photo(photo=fid, **common)
    if typ == 'video':
        return await bot.send_video(video=fid, **common)
    return await bot.send_document(document=fid, **common)

async def _markup(owner,item,item_key):
    return build_group_keyboard(item.get('buttons'),item_key=item_key)

async def _subscription_guard_allows_welcome(context, chat_id: int, user_id: int) -> bool:
    """Use the Subscription Guard result from the same join update.

    The guard handler runs immediately before this Welcome handler. Reading its
    result avoids a Telegram get_chat_member race immediately after a join.
    """
    results = context.application.bot_data.get("welcome_guard_results", {})
    key = (int(chat_id), int(user_id))
    if key in results:
        allowed = bool(results.pop(key))
        return allowed

    # No guard result means the guard was not active for this chat/update.
    # Do not block Welcome just because Telegram membership propagation is slow.
    return True


async def _delete_join_service_after_welcome(context, chat_id: int, message_id: int, owner: int):
    try:
        settings = await get_moderation(owner, chat_id)
        service = settings.get("service_messages") or {}
        if service.get("join"):
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def _welcome_join_seen(context, chat_id: int, user_id: int, *, ttl_seconds: int = 30) -> bool:
    """Return True when this join was already welcomed recently.

    Telegram can deliver the same join as both a CHAT_MEMBER update and a
    NEW_CHAT_MEMBERS service message. Keep a short per-clone dedupe window so
    the fallback handler never creates duplicate welcomes.
    """
    now_ts = time.monotonic()
    store = context.application.bot_data.setdefault("group_welcome_recent_joins", {})
    # Cheap opportunistic cleanup; this dict stays small in normal groups.
    stale = [key for key, ts in store.items() if now_ts - float(ts or 0) > ttl_seconds]
    for key in stale:
        store.pop(key, None)
    key = (int(chat_id), int(user_id))
    if key in store:
        return True
    store[key] = now_ts
    return False


async def _send_group_welcome_for_user(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat,
    user,
    service_message=None,
) -> bool:
    """Shared Welcome sender for NEW_CHAT_MEMBERS and CHAT_MEMBER updates."""
    owner = int(
        context.application.bot_data.get('data_owner_id')
        or context.application.bot_data.get('seller_owner_id')
        or 0
    )
    if not owner or not chat or not user or user.is_bot:
        return False

    doc = await get_group(owner, chat.id, getattr(chat, 'title', None) or 'Group')
    item = doc.get('welcome') or {}
    configured = bool(item.get('text') or item.get('media') or item.get('buttons'))
    if not item.get('enabled') or not configured:
        return False

    if _welcome_join_seen(context, chat.id, user.id):
        logger.debug('Group Welcome duplicate join ignored owner=%s chat_id=%s user_id=%s', owner, chat.id, user.id)
        return False

    if not await _subscription_guard_allows_welcome(context, chat.id, user.id):
        return False

    try:
        markup = await _markup(owner, item, 'w')
    except Exception:
        logger.exception('Group Welcome keyboard build failed owner=%s chat_id=%s', owner, chat.id)
        markup = None

    old_welcome_id = int(item.get('last_message_id') or 0) if item.get('delete_last_welcome') else 0
    try:
        rendered = await vars_text(item.get('text') or '', user, chat, context.bot)
        sent = await _send(context.bot, chat.id, item, rendered, markup)
    except Exception:
        logger.exception(
            'Group Welcome send failed owner=%s chat_id=%s user_id=%s',
            owner, chat.id, getattr(user, 'id', None),
        )
        return False

    if not sent:
        return False

    item['last_message_id'] = sent.message_id
    await update_welcome(owner, chat.id, last_message_id=sent.message_id)

    if service_message is not None:
        await _delete_join_service_after_welcome(
            context,
            chat.id,
            service_message.message_id,
            owner,
        )

    if old_welcome_id:
        async def _delete_previous(message_id=old_welcome_id, target_chat_id=chat.id):
            try:
                await context.bot.delete_message(chat_id=target_chat_id, message_id=message_id)
            except Exception:
                pass
        context.application.create_task(_delete_previous())
    return True


async def group_manager_new_members(update:Update,context:ContextTypes.DEFAULT_TYPE):
    """Primary Welcome path for NEW_CHAT_MEMBERS service messages."""
    m = update.effective_message
    if not m or m.chat.type not in {'group', 'supergroup'}:
        return

    for user in m.new_chat_members or []:
        await _send_group_welcome_for_user(
            context,
            chat=m.chat,
            user=user,
            service_message=m,
        )


async def group_manager_chat_member_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback Welcome path for join events delivered only as CHAT_MEMBER.

    Some groups/runtime configurations receive membership joins as chat_member
    updates without a usable NEW_CHAT_MEMBERS service message. The old runtime
    listened only to the latter, so the editor looked configured but nothing was
    sent. This handler covers that update type while short-term dedupe prevents
    double welcomes when Telegram sends both forms.
    """
    event = update.chat_member
    if not event or getattr(event.chat, 'type', None) not in {'group', 'supergroup'}:
        return

    old_status = getattr(event.old_chat_member, 'status', '')
    new_status = getattr(event.new_chat_member, 'status', '')
    joined = old_status in {'left', 'kicked'} and new_status in {
        'member', 'restricted', 'administrator', 'creator', 'owner'
    }
    if not joined:
        return

    user = event.new_chat_member.user
    await _send_group_welcome_for_user(context, chat=event.chat, user=user)

async def group_manager_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    m=update.effective_message
    if not m or m.chat.type not in {'group','supergroup'} or not m.from_user or m.from_user.is_bot: return
    text=(m.text or m.caption or '').strip()
    if not text: return
    owner=int(context.application.bot_data.get('seller_owner_id') or 0); doc=await get_group(owner,m.chat.id,m.chat.title or 'Group')
    low=' '.join(text.casefold().split())
    for item in doc.get('auto_replies') or []:
        keyword=' '.join(str(item.get('keyword') or '').casefold().split())
        if item.get('enabled',True) and keyword and low == keyword and (item.get('text') or item.get('media')):
            await _send(context.bot,m.chat.id,item,await vars_text(item.get('text') or '', m.from_user, m.chat, context.bot),await _markup(owner,item,'a'+str(item.get('id') or '')),m.message_id); return
    for item in doc.get('templates') or []:
        if item.get('enabled',True) and low==(item.get('keyword') or '').casefold() and (item.get('text') or item.get('media')):
            await _send(context.bot,m.chat.id,item,await vars_text(item.get('text') or '', m.from_user, m.chat, context.bot),await _markup(owner,item,'t'+str(item.get('id') or '')),m.message_id); return


async def group_manager_special_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return

    data = q.data
    owner = int(context.application.bot_data.get("seller_owner_id") or 0)
    group_id = None
    item_key = ""
    row_index = col_index = -1

    try:
        if data.startswith("gmspv_"):
            # Preview callback: gmspv_<groupid>_<itemkey>_<row>_<col>
            rest = data[len("gmspv_"):]
            gid_s, item_key, row_s, col_s = rest.split("_", 3)
            group_id = int(gid_s)
            row_index, col_index = int(row_s), int(col_s)
        elif data.startswith("gmsp_"):
            # Live group callback: gmsp_<itemkey>_<row>_<col>
            rest = data[len("gmsp_"):]
            item_key, row_s, col_s = rest.rsplit("_", 2)
            group_id = int(q.message.chat.id)
            row_index, col_index = int(row_s), int(col_s)
        else:
            return
    except Exception:
        await q.answer("Button data is invalid.", show_alert=True)
        return

    doc = await get_group(owner, group_id)
    item = None
    if item_key == "w":
        item = doc.get("welcome") or {}
    elif item_key.startswith("a"):
        item = await get_auto_reply(owner, group_id, item_key[1:])
    elif item_key.startswith("t"):
        item = await get_template(owner, group_id, item_key[1:])

    button = find_button(item or {}, row_index, col_index)
    if not button:
        await q.answer("This button is no longer available.", show_alert=True)
        return

    typ = str(button.get("type") or "")
    value = str(button.get("value") or "")

    if typ == "popup":
        await q.answer(value[:200], show_alert=False)
        return
    if typ == "alert":
        await q.answer(value[:200], show_alert=True)
        return
    if typ == "rules":
        try:
            chat = await context.bot.get_chat(group_id)
            rules = str(getattr(chat, "description", "") or "No group rules have been set.")
        except Exception:
            rules = "No group rules have been set."
        await q.answer(rules[:200], show_alert=True)
        return
    if typ == "copy":
        # Used only as fallback when Telegram/PTB CopyTextButton is unavailable.
        await q.answer(("Copy: " + value)[:200], show_alert=True)
        return

    await q.answer()
