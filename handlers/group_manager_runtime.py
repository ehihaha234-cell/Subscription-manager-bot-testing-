import re
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from database.group_manager import get_group, update_welcome, get_auto_reply, get_template, get_moderation
from handlers.clone.group_manager_buttons import build_group_keyboard, find_button
from database.seller_bots import get_bot_by_data_owner_id


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

    rules = ""
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
    media=item.get('media') or []; common={'chat_id':chat_id,'parse_mode':'HTML','reply_markup':markup}
    if reply_to: common['reply_to_message_id']=reply_to
    if not media: return await bot.send_message(text=text,**common)
    e=media[0]; typ=e.get('type'); fid=e.get('file_id'); common['caption']=text
    if typ=='photo': return await bot.send_photo(photo=fid,**common)
    if typ=='video': return await bot.send_video(video=fid,**common)
    return await bot.send_document(document=fid,**common)

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
    """Delete the join service message after Welcome is sent."""
    try:
        settings = await get_moderation(owner, chat_id)
        service = settings.get("service_messages") or {}
        if not service.get("join"):
            return
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # Cleanup failure must never delay/break the Welcome Message.
        return


async def group_manager_new_members(update:Update,context:ContextTypes.DEFAULT_TYPE):
    m=update.effective_message
    if not m or m.chat.type not in {'group','supergroup'}: return
    owner=int(context.application.bot_data.get('seller_owner_id') or 0); doc=await get_group(owner,m.chat.id,m.chat.title or 'Group'); item=doc.get('welcome') or {}
    if not item.get('enabled') or not (item.get('text') or item.get('media')): return
    markup=await _markup(owner,item,'w')
    for user in m.new_chat_members or []:
        if user.is_bot: continue

        # Subscription Guard is intentionally executed before this handler.
        # Send the welcome only if the user survived that guard and is still
        # a member of the group.
        if not await _subscription_guard_allows_welcome(
            context, m.chat.id, user.id
        ):
            continue

        if item.get('delete_last_welcome') and item.get('last_message_id'):
            try:
                await context.bot.delete_message(chat_id=m.chat.id,message_id=int(item['last_message_id']))
            except Exception:
                # The old welcome may already be gone or no longer deletable; never block the new welcome.
                pass
        sent=await _send(context.bot,m.chat.id,item,await vars_text(item.get('text') or '', user, m.chat, context.bot),markup)
        if sent:
            item['last_message_id']=sent.message_id
            await update_welcome(owner,m.chat.id,last_message_id=sent.message_id)
            # Welcome is sent first. Service-message deletion runs in the
            # background so it cannot add latency to the Welcome response.
            context.application.create_task(
                _delete_join_service_after_welcome(
                    context, m.chat.id, m.message_id, owner
                )
            )

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
