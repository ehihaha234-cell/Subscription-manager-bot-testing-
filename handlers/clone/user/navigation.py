"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.editor_engine import build_editor_keyboard
from handlers.common.feature_navigation import capture_feature_origin, restore_feature_origin, feature_back_callback
from database.business_automation import get_business_welcome
from handlers.common.clone_context import MAIN_BOT_USERNAME
from utils.branding import append_branding
from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo
from datetime import datetime
from zoneinfo import ZoneInfo



def _render_business_variables(value: str, user) -> str:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    first = str(getattr(user, "first_name", "") or "")
    last = str(getattr(user, "last_name", "") or "")
    name = " ".join(x for x in (first, last) if x).strip() or str(getattr(user, "username", "") or "User")
    username_raw = str(getattr(user, "username", "") or "").lstrip("@")
    user_id = str(getattr(user, "id", "") or "")
    values = {
        "{NAME}": first or name, "{FIRSTNAME}": first, "{SURNAME}": last, "{NAMESURNAME}": name,
        "{ID}": user_id, "{USERNAME}": f"@{username_raw}" if username_raw else "",
        "{MENTION}": f"tg://user?id={user_id}" if user_id else "",
        "{LANG}": str(getattr(user, "language_code", "") or ""),
        "{DATE}": now.strftime("%d %b %Y"), "{TIME}": now.strftime("%I:%M %p"),
        "{WEEKDAY}": now.strftime("%A"),
    }
    rendered = str(value or "")
    for token, replacement in values.items():
        rendered = rendered.replace(token, replacement)
    return rendered


def _render_business_buttons(rows, user):
    result = []
    for row in rows or []:
        clean = []
        for item in row or []:
            copy = dict(item)
            copy["text"] = _render_business_variables(copy.get("text") or "", user)
            if "value" in copy:
                copy["value"] = _render_business_variables(copy.get("value") or "", user)
            if "url" in copy:
                copy["url"] = _render_business_variables(copy.get("url") or "", user)
            clean.append(copy)
        if clean:
            result.append(clean)
    return result


def _business_connection_id(message):
    return getattr(message, "business_connection_id", None)


def _business_media(item: dict) -> list[dict]:
    media = list(item.get("media") or [])
    if not media and item.get("media_file_id"):
        media = [{"type": item.get("media_type") or "document", "file_id": item.get("media_file_id")}]
    return [m for m in media if m.get("file_id")][:10]


async def _send_business_welcome(update, context, owner: int, business_connection_id: str):
    """Return feature navigation to the configured Business welcome, not /start."""
    item = await get_business_welcome(owner)
    user = update.effective_user
    text = _render_business_variables(str(item.get("text") or "Welcome!"), user)
    text = await append_branding(text)
    bot_record = await get_bot_by_data_owner_id(owner) or {}
    markup = build_editor_keyboard(
        _render_business_buttons(item.get("buttons") or [], user),
        clone_username=str(bot_record.get("bot_username") or ""),
    )
    chat_id = update.effective_chat.id
    media = _business_media(item)
    common = {
        "chat_id": chat_id,
        "business_connection_id": business_connection_id,
    }

    if not media:
        await context.bot.send_message(text=text, reply_markup=markup, **common)
        return

    if len(media) > 1:
        album = []
        for entry in media:
            kind = str(entry.get("type") or "document").lower()
            file_id = str(entry.get("file_id") or "")
            if kind == "photo":
                album.append(InputMediaPhoto(media=file_id))
            elif kind == "video":
                album.append(InputMediaVideo(media=file_id))
            else:
                album.append(InputMediaDocument(media=file_id))
        await context.bot.send_media_group(media=album, **common)
        await context.bot.send_message(text=text, reply_markup=markup, **common)
        return

    entry = media[0]
    kind = str(entry.get("type") or "document").lower()
    file_id = str(entry.get("file_id") or "")
    kwargs = {**common, "caption": text, "reply_markup": markup}
    if kind == "photo":
        await context.bot.send_photo(photo=file_id, **kwargs)
    elif kind == "video":
        await context.bot.send_video(video=file_id, **kwargs)
    elif kind == "animation":
        await context.bot.send_animation(animation=file_id, **kwargs)
    else:
        await context.bot.send_document(document=file_id, **kwargs)


async def handle(self, update, context, q, owner, action):
    if action == 'c_return_origin':
        if await restore_feature_origin(q, context):
            return True
        # Never replace a Business Automation broadcast with the normal Clone
        # Bot welcome when origin restoration is temporarily unavailable.
        try:
            await q.answer("Previous broadcast could not be restored. Please try again.", show_alert=True)
        except Exception:
            pass
        return True
    if action in {'c_plans','c_buy','c_renew','c_profile','c_referral','c_referral_unlock','c_support'}:
        try:
            capture_feature_origin(q, context)
        except Exception:
            # Back-navigation tracking is optional and must not block the feature itself.
            pass
    back_keyboard = self.back(feature_back_callback(context))
    if action == 'seller_current_plan':
        seller_account_id = self.seller_account(context)
        await q.edit_message_text(
            await current_plan_text(seller_account_id),
            reply_markup=self.limit_keyboard('a_home'),
        )
        return True
    if action == 'seller_upgrade_plan':
        cfg = await get_config()
        plans = [p for p in cfg.get('paid_plans', []) if p.get('active', True)]
        if not plans:
            await q.edit_message_text('No paid seller plans are available right now.', reply_markup=self.back('a_home'))
            return True
        lines = ['💎 Upgrade Seller Plan', '']
        for p in plans:
            lines.append(f"• {p.get('name', 'Plan')} — ₹{p.get('price', 0)} / {p.get('duration_days', 30)} days")
        lines += ['', 'Contact the SaaS owner to activate a plan.']
        await q.edit_message_text('\n'.join(lines), reply_markup=self.back('a_home'))
        return True
    if action in {'ba_user_home', 'c_home'}:
        business_connection_id = _business_connection_id(q.message)
        if business_connection_id:
            await _send_business_welcome(update, context, owner, business_connection_id)
            return True
        record = await get_bot_by_data_owner_id(owner)
        settings = await ensure_seller_defaults(owner, (record or {}).get('bot_name', 'Subscription Bot'))
        await self.send_welcome(q.message, context, settings, q.from_user)
        return True
    if action == 'c_plans':
        await self.show_plans(q, owner, True, context)
        return True
    if action in {'c_buy', 'c_renew'}:
        await self.show_plans(q, owner, True, context)
        return True
    return False
