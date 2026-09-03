"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.editor_engine import (
    editor_header, editor_menu_keyboard, editor_media_prompt, editor_text_prompt,
    url_buttons_header, build_editor_keyboard,
)
from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation
from database.broadcast import get_seller_broadcast_draft, update_seller_broadcast_draft


def _broadcast_text_editor_header(title):
    return (
        f"📝 {title}\n\n"
        "Seller, send now the message you want to set!\n\n"
        "You can use HTML and:\n"
        "• {ID} = user ID\n"
        "• {NAME} = first name\n"
        "• {SURNAME} = surname\n"
        "• {NAMESURNAME} = full name\n"
        "• {LANG} = user language\n"
        "• {DATE} = current date\n"
        "• {TIME} = current time\n"
        "• {WEEKDAY} = week day\n"
        "• {MENTION} = link to the user profile\n"
        "• {USERNAME} = username"
    )


def _broadcast_media_editor_header(title):
    return (
        f"🖼 {title}\n\n"
        "Send one photo/video/document, or select and send one Telegram album together.\n"
        "The complete media selection will replace the current media (maximum 10 files)."
    )


def _broadcast_buttons_editor_header():
    return (
        "🔗 Buttons\n\n"
        "Set the buttons to be placed under the message.\n\n"
        "Send a message structured as follows:\n\n"
        "• Add a Single button:\n"
        "Button title - t.me/LinkExample\n\n"
        "• Add multiple buttons on a single line:\n"
        "Button 1 - t.me/LinkExample && Button 2 - t.me/LinkExample\n\n"
        "• Add multiple rows of buttons:\n"
        "Button 1 - t.me/LinkExample\n"
        "Button 2 - t.me/LinkExample\n\n"
        "⭐ Special Button:\n"
        "• Add a share button:\n"
        "Button title - share: Text\n\n"
        "⚡ Feature Buttons:\n"
        "• Add a feature button:\n"
        "Button title - feature: feature_name\n\n"
        "Features:\n"
        "plans, buy, profile, renew, referral, referral_unlock, support, home"
    )


def _broadcast_text(item):
    return (
        "📣 Seller Broadcast\n\n"
        "Create one complete broadcast with text, up to 10 media files and URL/feature buttons.\n\n"
        + editor_header(
            "Current Setup",
            {**item, "enabled": True},
        )
    )


def _broadcast_keyboard(item):
    rows = [
        [InlineKeyboardButton("📝 Text", callback_data="a_bc_text"), InlineKeyboardButton("👀 See", callback_data="a_bc_see_text")],
        [InlineKeyboardButton("🖼 Media", callback_data="a_bc_media"), InlineKeyboardButton("👀 See", callback_data="a_bc_see_media")],
        [InlineKeyboardButton("🔗 Buttons", callback_data="a_bc_buttons"), InlineKeyboardButton("👀 See", callback_data="a_bc_see_buttons")],
        [InlineKeyboardButton("👀 Full Preview", callback_data="a_bc_preview")],
        [InlineKeyboardButton("📤 Send Broadcast", callback_data="a_bc_send")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_home")],
    ]
    return InlineKeyboardMarkup(rows)



def _scheduled_home_text(items):
    active = sum(1 for x in items if x.get("status") == "active")
    paused = sum(1 for x in items if x.get("status") != "active")
    return (
        "📅 Scheduled Broadcasts\n\n"
        f"Total: {len(items)}\n"
        f"🟢 Active: {active}\n"
        f"⏸ Paused: {paused}\n\n"
        "Create and manage reusable scheduled broadcast drafts."
    )


def _scheduled_home_keyboard(items):
    rows = [[InlineKeyboardButton("➕ Add Scheduled Broadcast", callback_data="a_sb_add")]]
    for item in items[:20]:
        icon = "🟢" if item.get("status") == "active" else "⏸"
        name = str(item.get("name") or "Scheduled Broadcast")[:35]
        rows.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"a_sb_open_{item['job_id']}")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="a_home")])
    return InlineKeyboardMarkup(rows)


def _scheduled_editor_text(item):
    media_count = len(item.get("media") or [])
    button_count = sum(len(row) for row in (item.get("buttons") or []))
    status = "🟢 Active" if item.get("status") == "active" else "⏸ Paused"
    media_line = f"🖼 Media: {media_count}/10" if media_count else "🖼 Media: ❌ Not added"
    return (
        "📅 Scheduled Broadcast\n\n"
        "Automatically sends this saved broadcast to your selected audience at the scheduled time. "
        "You can send it once or repeat it automatically.\n\n"
        f"Name: {item.get('name') or 'Scheduled Broadcast'}\n"
        f"Status: {status}\n\n"
        f"📝 Text: {'✅ Added' if item.get('text') else '❌ Not added'}\n"
        f"{media_line}\n"
        f"🔗 Buttons: {button_count}\n\n"
        f"📆 Schedule: {item.get('schedule_at') or 'Not Set'}\n"
        f"🔁 Repeat: {item.get('repeat_interval') or 'Not Set'}"
    )


def _scheduled_editor_keyboard(item):
    job_id = item["job_id"]
    pause_label = "⏸ Pause" if item.get("status") == "active" else "▶️ Resume"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(pause_label, callback_data=f"a_sb_toggle_{job_id}"), InlineKeyboardButton("🗑 Remove", callback_data=f"a_sb_delete_{job_id}")],
        [InlineKeyboardButton("📝 Text", callback_data=f"a_sb_text_{job_id}"), InlineKeyboardButton("👀 See", callback_data=f"a_sb_see_text_{job_id}")],
        [InlineKeyboardButton("🖼 Media", callback_data=f"a_sb_media_{job_id}"), InlineKeyboardButton("👀 See", callback_data=f"a_sb_see_media_{job_id}")],
        [InlineKeyboardButton("🔗 Buttons", callback_data=f"a_sb_buttons_{job_id}"), InlineKeyboardButton("👀 See", callback_data=f"a_sb_see_buttons_{job_id}")],
        [InlineKeyboardButton("👀 Full Preview", callback_data=f"a_sb_preview_{job_id}")],
        [InlineKeyboardButton("📅 Schedule Settings", callback_data=f"a_sb_settings_{job_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_broadcast_schedule")],
    ])


def _scheduled_settings_text(item):
    return (
        "📅 Schedule Settings\n\n"
        "Configure when this broadcast should be sent automatically.\n\n"
        "Examples\n\n"
        "One-Time\n"
        "02 Sep 2026 • 06:50 PM\n\n"
        "Recurring\n"
        "6s = Every 6 Seconds\n"
        "7m = Every 7 Minutes\n"
        "8h = Every 8 Hours\n"
        "9d = Every 9 Days\n\n"
        f"Current Schedule: {item.get('schedule_at') or 'Not Set'}\n"
        f"Repeat Broadcast: {item.get('repeat_interval') or 'Not Set'}"
    )


def _scheduled_settings_keyboard(item):
    job_id = item["job_id"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Schedule", callback_data=f"a_sb_schedule_{job_id}")],
        [InlineKeyboardButton("🔁 Repeat Broadcast", callback_data=f"a_sb_repeat_{job_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"a_sb_open_{job_id}")],
    ])


def _scheduled_input_keyboard(job_id, remove_action=None):
    rows = []
    if remove_action:
        rows.append([InlineKeyboardButton("🗑 Remove", callback_data=f"a_sb_{remove_action}_{job_id}")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"a_sb_open_{job_id}")])
    return InlineKeyboardMarkup(rows)

def _input_keyboard(back_callback, remove_callback=None, remove_label="Remove"):
    rows = []
    if remove_callback:
        rows.append([InlineKeyboardButton(f"🗑 {remove_label}", callback_data=remove_callback)])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(rows)


async def _send_editor_text_see(bot, chat_id, text, title):
    if not text:
        await bot.send_message(chat_id, f"{title}\n\n❌ Not added")
        return
    await bot.send_message(chat_id, text)


def _media_items(item):
    media = list(item.get("media") or [])
    if not media and item.get("media_file_id"):
        media = [{"type": item.get("media_type"), "file_id": item.get("media_file_id")}]
    return media[:10]


def _build_media(item, caption=None):
    media_type = str(item.get("type") or item.get("media_type") or "")
    file_id = str(item.get("file_id") or item.get("media_file_id") or "")
    if media_type == "photo":
        return InputMediaPhoto(file_id, caption=caption)
    if media_type == "video":
        return InputMediaVideo(file_id, caption=caption)
    if media_type == "document":
        return InputMediaDocument(file_id, caption=caption)
    if media_type == "animation":
        return InputMediaAnimation(file_id, caption=caption)
    return None


async def _send_editor_media_see(bot, chat_id, item, title):
    media = _media_items(item)
    if not media:
        await bot.send_message(chat_id, f"{title}\n\n❌ Not added")
        return
    if len(media) == 1:
        built = _build_media(media[0], item.get("text") or None)
        if built:
            if isinstance(built, InputMediaPhoto):
                await bot.send_photo(chat_id, media[0]["file_id"], caption=item.get("text") or None)
            elif isinstance(built, InputMediaVideo):
                await bot.send_video(chat_id, media[0]["file_id"], caption=item.get("text") or None)
            elif isinstance(built, InputMediaDocument):
                await bot.send_document(chat_id, media[0]["file_id"], caption=item.get("text") or None)
            elif isinstance(built, InputMediaAnimation):
                await bot.send_animation(chat_id, media[0]["file_id"], caption=item.get("text") or None)
            return
    album = []
    for index, media_item in enumerate(media):
        built = _build_media(media_item, item.get("text") if index == 0 else None)
        if built:
            album.append(built)
    if album:
        await bot.send_media_group(chat_id=chat_id, media=album)


async def _send_editor_buttons_see(bot, chat_id, item, title):
    buttons = item.get("buttons") or []
    if not buttons:
        await bot.send_message(chat_id, f"{title}\n\n❌ Not added")
        return
    lines = [title, "", "Current Buttons"]
    for row in buttons:
        if isinstance(row, list):
            for button in row:
                if isinstance(button, dict):
                    lines.append(f"• {button.get('text') or button.get('label') or 'Button'}")
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=build_editor_keyboard(buttons))


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_broadcast_schedule':
        context.user_data.pop('scheduled_broadcast_editor', None)
        context.user_data.pop('scheduled_media_batch', None)
        items = await list_scheduled_campaigns(owner)
        await q.edit_message_text(_scheduled_home_text(items), reply_markup=_scheduled_home_keyboard(items))
        return True
    if a == 'a_sb_add':
        context.user_data['scheduled_broadcast_editor'] = {
            'field': 'name', 'menu_chat_id': q.message.chat_id, 'menu_message_id': q.message.message_id
        }
        await q.edit_message_text(
            '➕ Add Scheduled Broadcast\n\nSend a name for this broadcast.\n\nExample: Morning Promotion',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data='a_broadcast_schedule')]]),
        )
        return True
    if a.startswith('a_sb_open_'):
        job_id = a.removeprefix('a_sb_open_')
        item = await get_scheduled_campaign(owner, job_id)
        if not item:
            await q.answer('Scheduled broadcast not found.', show_alert=True)
            return True
        await q.edit_message_text(_scheduled_editor_text(item), reply_markup=_scheduled_editor_keyboard(item))
        return True
    for prefix, field in (('a_sb_text_', 'text'), ('a_sb_media_', 'media'), ('a_sb_buttons_', 'buttons')):
        if a.startswith(prefix):
            job_id = a.removeprefix(prefix)
            item = await get_scheduled_campaign(owner, job_id)
            if not item:
                await q.answer('Scheduled broadcast not found.', show_alert=True)
                return True
            context.user_data['scheduled_broadcast_editor'] = {
                'field': field, 'job_id': job_id,
                'menu_chat_id': q.message.chat_id, 'menu_message_id': q.message.message_id,
            }
            if field == 'text':
                prompt = _broadcast_text_editor_header('Scheduled Broadcast Text')
                remove = 'rmtext' if item.get('text') else None
            elif field == 'media':
                prompt = _broadcast_media_editor_header('Scheduled Broadcast Media')
                remove = 'rmmedia' if item.get('media') else None
            else:
                prompt = _broadcast_buttons_editor_header()
                remove = 'rmbuttons' if item.get('buttons') else None
            await q.edit_message_text(prompt, reply_markup=_scheduled_input_keyboard(job_id, remove))
            return True
    if a.startswith('a_sb_see_text_') or a.startswith('a_sb_see_media_') or a.startswith('a_sb_see_buttons_'):
        if a.startswith('a_sb_see_text_'):
            kind, job_id = 'text', a.removeprefix('a_sb_see_text_')
        elif a.startswith('a_sb_see_media_'):
            kind, job_id = 'media', a.removeprefix('a_sb_see_media_')
        else:
            kind, job_id = 'buttons', a.removeprefix('a_sb_see_buttons_')
        item = await get_scheduled_campaign(owner, job_id)
        if not item:
            await q.answer('Scheduled broadcast not found.', show_alert=True)
            return True
        if kind == 'text':
            await _send_editor_text_see(q.message.get_bot(), q.message.chat_id, item.get('text'), '📝 Scheduled Broadcast Text')
        elif kind == 'media':
            await _send_editor_media_see(q.message.get_bot(), q.message.chat_id, item, '🖼 Scheduled Broadcast Media')
        else:
            await _send_editor_buttons_see(q.message.get_bot(), q.message.chat_id, item, '🔗 Scheduled Broadcast Buttons')
        await q.answer('Shown.')
        return True
    if a.startswith('a_sb_settings_'):
        job_id = a.removeprefix('a_sb_settings_')
        item = await get_scheduled_campaign(owner, job_id)
        if item:
            await q.edit_message_text(_scheduled_settings_text(item), reply_markup=_scheduled_settings_keyboard(item))
        return True
    if a.startswith('a_sb_schedule_'):
        job_id = a.removeprefix('a_sb_schedule_')
        context.user_data['scheduled_broadcast_editor'] = {
            'field': 'schedule', 'job_id': job_id, 'menu_chat_id': q.message.chat_id, 'menu_message_id': q.message.message_id
        }
        await q.edit_message_text(
            '📅 Schedule\n\nSend the date and time for this broadcast.\n\nExample:\n02 Sep 2026 06:50 PM\n\nAccepted format: DD Mon YYYY HH:MM AM/PM',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data=f'a_sb_settings_{job_id}')]]),
        )
        return True
    if a.startswith('a_sb_repeat_'):
        job_id = a.removeprefix('a_sb_repeat_')
        context.user_data['scheduled_broadcast_editor'] = {
            'field': 'repeat', 'job_id': job_id, 'menu_chat_id': q.message.chat_id, 'menu_message_id': q.message.message_id
        }
        await q.edit_message_text(
            '🔁 Repeat Broadcast\n\nSend how often this broadcast should repeat.\n\nExamples:\n6s = Every 6 Seconds\n7m = Every 7 Minutes\n8h = Every 8 Hours\n9d = Every 9 Days',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data=f'a_sb_settings_{job_id}')]]),
        )
        return True
    if a.startswith('a_sb_toggle_'):
        job_id = a.removeprefix('a_sb_toggle_')
        item = await get_scheduled_campaign(owner, job_id)
        if item:
            if item.get('status') == 'active':
                await self._campaign_job_remove(context.application, job_id)
                item = await update_scheduled_campaign(owner, job_id, status='paused')
            else:
                item = await update_scheduled_campaign(owner, job_id, status='active')
                item = await self.schedule_campaign_from_item(
                    context.application, owner, item, prefer_now_for_repeat=not bool(item.get("schedule_at"))
                )
            await q.edit_message_text(_scheduled_editor_text(item), reply_markup=_scheduled_editor_keyboard(item))
        return True
    if a.startswith('a_sb_delete_'):
        job_id = a.removeprefix('a_sb_delete_')
        await self._campaign_job_remove(context.application, job_id)
        await delete_scheduled_campaign(owner, job_id)
        items = await list_scheduled_campaigns(owner)
        await q.edit_message_text(_scheduled_home_text(items), reply_markup=_scheduled_home_keyboard(items))
        return True
    for prefix, field, value in (('a_sb_rmtext_', 'text', ''), ('a_sb_rmmedia_', 'media', []), ('a_sb_rmbuttons_', 'buttons', [])):
        if a.startswith(prefix):
            job_id = a.removeprefix(prefix)
            item = await update_scheduled_campaign(owner, job_id, **{field: value})
            await q.edit_message_text(_scheduled_editor_text(item), reply_markup=_scheduled_editor_keyboard(item))
            return True
    if a.startswith('a_sb_preview_'):
        job_id = a.removeprefix('a_sb_preview_')
        item = await get_scheduled_campaign(owner, job_id)
        if not item or not (item.get('text') or item.get('media')):
            await q.answer('Add text or media first.', show_alert=True)
            return True
        await self.send_seller_broadcast_preview(q.message, item)
        await q.answer('Preview sent.')
        return True
    if a == 'a_coupons':
        coupons = await list_coupons(owner)
        lines = ['🎟 Coupon System\n', 'Create: CODE | percent/fixed | VALUE | USAGE_LIMIT']
        for cpn in coupons[:20]:
            lines.append(f"• {cpn['code']} — {cpn['value']:g} {cpn['discount_type']} — {cpn['used_count']}/{cpn['usage_limit']}")
        context.user_data.clear()
        context.user_data['wait_coupon_create'] = True
        await q.edit_message_text('\n'.join(lines), reply_markup=self.back())
        return True

    if a == 'a_broadcast':
        context.user_data.pop('seller_broadcast_editor', None)
        context.user_data.pop('seller_broadcast_media_batch', None)
        item = await get_seller_broadcast_draft(owner)
        await q.edit_message_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))
        return True
    if a == 'a_bc_text':
        item = await get_seller_broadcast_draft(owner)
        context.user_data['seller_broadcast_editor'] = {'field': 'text'}
        await q.edit_message_text(
            _broadcast_text_editor_header('Seller Broadcast Text'),
            reply_markup=_input_keyboard('a_broadcast', 'a_bc_rmtext' if item.get('text') else None, 'Remove Text'),
        )
        return True
    if a == 'a_bc_media':
        item = await get_seller_broadcast_draft(owner)
        context.user_data['seller_broadcast_editor'] = {'field': 'media'}
        await q.edit_message_text(
            editor_media_prompt('Seller Broadcast Media'),
            reply_markup=_input_keyboard('a_broadcast', 'a_bc_rmmedia' if (item.get('media') or item.get('media_file_id')) else None, 'Remove Media'),
        )
        return True
    if a == 'a_bc_buttons':
        item = await get_seller_broadcast_draft(owner)
        context.user_data['seller_broadcast_editor'] = {'field': 'buttons'}
        await q.edit_message_text(
            _broadcast_buttons_editor_header(),
            reply_markup=_input_keyboard('a_broadcast', 'a_bc_rmbuttons' if item.get('buttons') else None, 'Remove Buttons'),
        )
        return True
    if a in ('a_bc_see_text', 'a_bc_see_media', 'a_bc_see_buttons'):
        item = await get_seller_broadcast_draft(owner)
        bot = q.message.get_bot()
        if a == 'a_bc_see_text':
            await _send_editor_text_see(bot, q.message.chat_id, item.get('text'), '📝 Seller Broadcast Text')
        elif a == 'a_bc_see_media':
            await _send_editor_media_see(bot, q.message.chat_id, item, '🖼 Seller Broadcast Media')
        else:
            await _send_editor_buttons_see(bot, q.message.chat_id, item, '🔗 Seller Broadcast Buttons')
        await q.answer('Shown.')
        return True
    if a == 'a_bc_rmtext':
        item = await update_seller_broadcast_draft(owner, text='')
        await q.edit_message_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))
        return True
    if a == 'a_bc_rmmedia':
        item = await update_seller_broadcast_draft(owner, media=[], media_type='', media_file_id='')
        await q.edit_message_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))
        return True
    if a == 'a_bc_rmbuttons':
        item = await update_seller_broadcast_draft(owner, buttons=[])
        await q.edit_message_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))
        return True
    if a == 'a_bc_preview':
        item = await get_seller_broadcast_draft(owner)
        await self.send_seller_broadcast_preview(q.message, item)
        await q.answer('Preview sent.')
        return True
    if a == 'a_bc_send':
        item = await get_seller_broadcast_draft(owner)
        if not (item.get('text') or item.get('media') or item.get('media_file_id')):
            await q.answer('Add text or media first.', show_alert=True)
            return True
        context.user_data['seller_broadcast_confirmation'] = {
            'owner_id': int(owner),
            'draft': item,
        }
        await q.answer()
        await q.message.reply_text(
            "📢 Broadcast Confirmation\n\n"
            "Send /confirm to start this broadcast.\n"
            "Send /cancel to cancel it."
        )
        return True
    return False
