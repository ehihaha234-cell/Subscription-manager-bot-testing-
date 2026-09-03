"""Business Automation UI and MTProto account connection inside clone-bot Admin Panel."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telethon import TelegramClient
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from config import TELEGRAM_API_HASH, TELEGRAM_API_ID
from database.seller_data import (
    business_automation_stats,
    count_business_accounts,
    disconnect_business_account,
    get_business_account,
    get_business_accounts,
    get_seller_settings,
    save_business_account_session,
    set_seller_setting,
)
from database.business_official import count_active_official_business_connections
from database.business_automation import (
    create_business_reply_template,
    delete_business_reply_template,
    get_business_auto_reply,
    list_business_auto_replies,
    get_business_auto_reply_item,
    create_business_auto_reply_item,
    update_business_auto_reply_item,
    delete_business_auto_reply_item,
    get_business_reply_template,
    get_business_welcome,
    list_business_reply_templates,
    update_business_auto_reply,
    update_business_reply_template,
    update_business_welcome,
)
from utils.crypto import decrypt_secret, encrypt_secret
from services.business_automation_runtime import business_automation_runtime

logger = logging.getLogger(__name__)



def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _buttons_count(rows):
    return sum(len(row) for row in (rows or []))


BA_VARIABLES = (
    "{ID} = user ID\n"
    "{NAME} = first name\n"
    "{SURNAME} = surname\n"
    "{NAMESURNAME} = full name\n"
    "{LANG} = user language\n"
    "{DATE} = current date\n"
    "{TIME} = current time\n"
    "{WEEKDAY} = week day\n"
    "{MENTION} = Link to the user profile\n"
    "{USERNAME} = username"
)


def _business_buttons_header() -> str:
    return """🔗 Buttons

Set the buttons to be placed under the message.

Send a message structured as follows:

• Add a Single button:
Button title - t.me/LinkExample

• Add multiple buttons on a single line:
Button 1 - t.me/LinkExample && Button 2 - t.me/LinkExample

• Add multiple rows of buttons:
Button 1 - t.me/LinkExample
Button 2 - t.me/LinkExample

⭐ Special Button:
• Add a share button:
Button title - share: Text

⚡ Feature Buttons:
• Add a feature button:
Button title - feature: feature_name
• Open a feature directly on clone bot:
Button title - clone: feature_name

Features:
plans, buy, profile, renew, referral, referral_unlock, support, home"""


def _parse_business_buttons(text: str) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    features = {
        "plans": "c_plans",
        "buy": "c_buy",
        "profile": "c_profile",
        "renew": "c_renew",
        "referral": "c_referral",
        "referral_unlock": "c_referral_unlock",
        "support": "c_support",
        "home": "c_home",
    }
    for line_no, raw_line in enumerate((text or "").splitlines(), 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        row = []
        for button_no, item in enumerate(raw_line.split("&&"), 1):
            item = item.strip()
            if " - " not in item:
                raise ValueError(f"Line {line_no}, button {button_no}: missing ' - '.")
            title, target = [part.strip() for part in item.split(" - ", 1)]
            if not title or not target:
                raise ValueError(f"Line {line_no}, button {button_no}: button title and target are required.")

            if target.startswith(("http://", "https://", "tg://")) or target.startswith("t.me/"):
                value = "https://" + target if target.startswith("t.me/") else target
                row.append({"text": title, "type": "url", "value": value})
            elif target.startswith("@"):
                username = target[1:].strip()
                if not username or len(username) > 32 or not all(ch.isalnum() or ch == "_" for ch in username):
                    raise ValueError(f"Line {line_no}, button {button_no}: invalid Telegram username.")
                row.append({"text": title, "type": "url", "value": f"https://t.me/{username}"})
            elif target.startswith("share:"):
                value = target.split(":", 1)[1].strip()
                if not value:
                    raise ValueError(f"Line {line_no}, button {button_no}: share text is required.")
                row.append({"text": title, "type": "share", "value": value})
            elif target.startswith("feature:"):
                feature = target.split(":", 1)[1].strip().lower()
                callback = features.get(feature)
                if not callback:
                    raise ValueError(
                        f"Line {line_no}, button {button_no}: unknown feature '{feature}'. "
                        "Available: " + ", ".join(features)
                    )
                row.append({"text": title, "type": "callback", "value": callback})
            elif target.startswith("clone:"):
                feature = target.split(":", 1)[1].strip().lower()
                if feature not in features:
                    raise ValueError(
                        f"Line {line_no}, button {button_no}: unknown clone feature '{feature}'. "
                        "Available: " + ", ".join(features)
                    )
                row.append({"text": title, "type": "clone", "value": feature})
            else:
                raise ValueError(
                    f"Line {line_no}, button {button_no}: only t.me URL, @username, share:, feature:, or clone: are supported."
                )
        rows.append(row)
    if not rows:
        raise ValueError("No buttons found.")
    return rows


def _build_business_buttons(rows, clone_username: str = ""):
    if not rows:
        return None
    keyboard = []
    clone_username = str(clone_username or "").lstrip("@")
    for row in rows:
        out = []
        for item in row:
            title = str(item.get("text") or "Button")
            kind = str(item.get("type") or "")
            value = str(item.get("value") or "")
            if kind == "url" and value:
                out.append(InlineKeyboardButton(title, url=value))
            elif kind == "callback":
                out.append(InlineKeyboardButton(title, callback_data=value or "c_home"))
            elif kind == "share":
                out.append(
                    InlineKeyboardButton(
                        title,
                        url=f"https://t.me/share/url?text={quote(value)}"
                    )
                )
            elif kind == "clone" and clone_username:
                out.append(
                    InlineKeyboardButton(
                        title,
                        url=f"https://t.me/{clone_username}?start={quote(value or 'home')}"
                    )
                )
        if out:
            keyboard.append(out)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def _editor_header(title: str, item: dict) -> str:
    buttons = sum(len(row) for row in (item.get("buttons") or []))
    media = len(item.get("media") or [])
    if not media and item.get("media_file_id"):
        media = 1
    media_line = f"🖼 Media: {media}/10" if media else "🖼 Media: ❌ Not added"
    return (
        f"{title}\n\n"
        f"Status: {'🟢 Enabled' if item.get('enabled', True) else '🔴 Disabled'}\n"
        f"📝 Text: {'✅ Added' if item.get('text') else '❌ Not added'}\n"
        f"{media_line}\n"
        f"🔗 Buttons: {buttons}\n\n"
        "Use the options below to add, replace, preview, or remove each part."
    )


def _business_media_prompt(title: str) -> str:
    return (
        f"🖼 {title}\n\n"
        "Send one photo/video/document, or send one Telegram album together.\n"
        "The complete media selection will replace the current media (maximum 10 files)."
    )


def _business_editor_keyboard(prefix: str, item: dict, *, back_callback: str, allow_toggle: bool = True):
    rows = []
    if allow_toggle:
        rows.append([InlineKeyboardButton(
            "🔴 Disable" if item.get("enabled", True) else "🟢 Enable",
            callback_data=f"{prefix}_toggle"
        )])
    rows.extend([
        [
            InlineKeyboardButton("📝 Text", callback_data=f"{prefix}_text"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_see_text"),
        ],
        [
            InlineKeyboardButton("🖼 Media", callback_data=f"{prefix}_media"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_see_media"),
        ],
        [
            InlineKeyboardButton("🔗 Buttons", callback_data=f"{prefix}_buttons"),
            InlineKeyboardButton("👀 See", callback_data=f"{prefix}_see_buttons"),
        ],
        [InlineKeyboardButton("👀 Full Preview", callback_data=f"{prefix}_preview")],
        [InlineKeyboardButton("⬅ Back", callback_data=back_callback)],
    ])
    return _kb(rows)



def _business_text_prompt(title: str) -> str:
    return (
        f"📝 {title}\n\n"
        "Seller, send now the message you want to set!\n\n"
        "You can use HTML and:\n"
        f"• {BA_VARIABLES.replace(chr(10), chr(10) + '• ')}"
    )



def _buttons_input_text(rows) -> str:
    """Rebuild the exact-style button input text for the See Buttons view."""
    lines = []
    for row in rows or []:
        parts = []
        for item in row or []:
            title = str(item.get("text") or "Button")
            kind = str(item.get("type") or "")
            value = str(item.get("value") or "")
            if kind == "url":
                if value.startswith("https://t.me/"):
                    target = value[len("https://"):]
                else:
                    target = value
            elif kind == "share":
                target = f"share: {value}"
            elif kind == "callback":
                reverse = {
                    "c_plans": "plans", "c_buy": "buy", "c_profile": "profile",
                    "c_renew": "renew", "c_referral": "referral",
                    "c_referral_unlock": "referral_unlock",
                    "c_support": "support", "c_home": "home",
                }
                target = f"feature: {reverse.get(value, value)}"
            elif kind == "clone":
                target = f"clone: {value}"
            else:
                target = value
            parts.append(f"{title} - {target}")
        if parts:
            lines.append(" && ".join(parts))
    return "\n".join(lines)


async def _send_business_component_preview(message, item: dict, component: str):
    bot = message.get_bot()
    clone_username = str(getattr(bot, "username", "") or "").lstrip("@")
    if component == "text":
        await message.reply_text(item.get("text") or "❌ No text has been saved.")
        return
    if component == "media":
        items = list(item.get("media") or [])
        if not items and item.get("media_file_id"):
            items = [{"type": item.get("media_type") or "document", "file_id": item.get("media_file_id")}]
        items = [x for x in items if x.get("file_id")][:10]
        if not items:
            await message.reply_text("❌ No media has been saved.")
            return

        # See Media must show the saved Telegram album as one media group,
        # not as a sequence of unrelated one-by-one messages. Telegram media
        # groups support photos, videos and documents. If the saved set contains
        # an animation, show that item separately because Telegram does not
        # support GIF/animation objects inside sendMediaGroup.
        album_items = [x for x in items if str(x.get("type") or "").lower() in {"photo", "video", "document"}]
        other_items = [x for x in items if str(x.get("type") or "").lower() not in {"photo", "video", "document"}]
        if len(album_items) >= 2:
            album = []
            for x in album_items:
                kind = str(x.get("type") or "document").lower()
                fid = x.get("file_id")
                if kind == "photo":
                    album.append(InputMediaPhoto(fid))
                elif kind == "video":
                    album.append(InputMediaVideo(fid))
                else:
                    album.append(InputMediaDocument(fid))
            await message.reply_media_group(album)
        elif len(album_items) == 1:
            x = album_items[0]
            kind = str(x.get("type") or "document").lower()
            fid = x.get("file_id")
            if kind == "photo":
                await message.reply_photo(fid)
            elif kind == "video":
                await message.reply_video(fid)
            else:
                await message.reply_document(fid)
        for x in other_items:
            kind = str(x.get("type") or "document").lower()
            fid = x.get("file_id")
            if kind == "animation":
                await message.reply_animation(fid)
        return
    if component == "buttons":
        buttons = item.get("buttons") or []
        markup = _build_business_buttons(buttons, clone_username=clone_username)
        if not markup:
            await message.reply_text("❌ No buttons have been saved.")
            return
        input_text = str(item.get("buttons_input") or "").strip() or _buttons_input_text(buttons)
        await message.reply_text(
            f"🔗 Current Buttons\n\n{input_text}",
            reply_markup=markup,
        )


def _input_keyboard(back_callback: str, *, remove_callback: str | None = None, remove_label: str = "Remove"):
    rows = []
    if remove_callback:
        rows.append([InlineKeyboardButton(f"🗑 {remove_label}", callback_data=remove_callback)])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_callback)])
    return _kb(rows)


def _home_keyboard(enabled: bool):
    return _kb([
        [InlineKeyboardButton("👋 Welcome Message", callback_data="ba_welcome")],
        [
            InlineKeyboardButton("💬 Auto Reply", callback_data="ba_auto"),
            InlineKeyboardButton("📝 Reply Templates", callback_data="ba_templates"),
        ],
        [InlineKeyboardButton("⚙️ Settings", callback_data="ba_settings")],
        [InlineKeyboardButton("📊 Statistics", callback_data="ba_stats")],
        [InlineKeyboardButton("⬅ Admin Panel", callback_data="a_home")],
    ])


async def _home(owner: int):
    settings = await get_seller_settings(owner)
    enabled = bool(settings.get("business_automation_enabled"))
    connected = await count_active_official_business_connections(owner)
    connection_status = "🟢 Connected" if connected else "🔴 Not Connected"
    text = (
        "💼 Business Automation\n\n"
        "Connect your Telegram Business Account and automate customer conversations.\n\n"
        "📌 Setup Guide\n"
        "1. Log in to your account using the official Telegram app.\n"
        "2. Open Settings → Account → Chat Automation.\n"
        "3. Use the bot username/search option.\n"
        "4. Enter your Clone Bot username and select it.\n"
        "5. Grant all permissions except Manage Gifts and Manage Stars.\n"
        "6. Return here after Telegram confirms the connection.\n\n"
        f"Automation: {'🟢 Enabled' if enabled else '🔴 Disabled'}\n"
        f"Business Account: {connection_status}\n\n"
        "After connecting, you can configure and control Welcome Message, Auto Reply, and Reply Templates from here. You can also manage Settings and view Statistics."
    )
    return text, _home_keyboard(enabled)


async def _editor_state(owner: int) -> tuple[dict, dict, list[dict]]:
    welcome = await get_business_welcome(owner)
    auto_reply = await get_business_auto_reply(owner)
    templates = await list_business_reply_templates(owner)
    return welcome, auto_reply, templates


def _welcome_text(item):
    return (
        "👋 Welcome Message\n\n"
        "This message is sent automatically when a customer messages a connected account for the first time. "
        "Add text, media, URL buttons, or Clone Bot feature buttons, then use Preview before enabling it.\n\n"
        + _editor_header("Current Setup", item)
    )


def _welcome_keyboard(item):
    return _business_editor_keyboard("ba_welcome", item, back_callback="ba_home", allow_toggle=True)


def _auto_replies_keyboard(items):
    rows = [[InlineKeyboardButton(
        f"{'🟢' if item.get('enabled', True) else '🔴'} {item.get('keyword') or 'Keyword'}",
        callback_data=f"ba_ar_open_{item.get('reply_id')}",
    )] for item in items]
    rows.extend([
        [InlineKeyboardButton("➕ Add Keyword", callback_data="ba_ar_add")],
        [InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")],
    ])
    return _kb(rows)


def _auto_item_text(item):
    return (
        "💬 Keyword Auto Reply\n\n"
        f"Keyword: {item.get('keyword') or '-'}\n\n"
        "When this keyword appears anywhere in a customer message or sentence, the configured reply is sent automatically.\n\n"
        + _editor_header("Current Setup", item)
    )

def _auto_item_keyboard(item):
    rid = str(item.get("reply_id"))
    enabled = bool(item.get("enabled", True))
    rows = [
        [InlineKeyboardButton("✏️ Change Keyword", callback_data=f"ba_ar_keyword_{rid}")],
        [
            InlineKeyboardButton(
                "🔴 Disable" if enabled else "🟢 Enable",
                callback_data=f"ba_ar_{rid}_toggle",
            ),
            InlineKeyboardButton(
                "🗑 Remove Keyword",
                callback_data=f"ba_ar_{rid}_delete",
            ),
        ],
    ]
    common = _business_editor_keyboard(
        f"ba_ar_{rid}", item, back_callback="ba_auto", allow_toggle=False,
    )
    rows.extend(common.inline_keyboard)
    return _kb(rows)

def _templates_keyboard(templates):
    rows = [
        [InlineKeyboardButton(
            f"{'🟢' if item.get('enabled', True) else '🔴'} {item.get('name') or item.get('shortcut') or 'Template'}",
            callback_data=f"ba_tpl_open_{item.get('template_id')}",
        )]
        for item in templates
    ]
    rows.extend([
        [InlineKeyboardButton("➕ Add Reply Template", callback_data="ba_tpl_add")],
        [InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")],
    ])
    return _kb(rows)


def _template_text(item):
    summary = _editor_header(
        "📝 Business Reply Template",
        item,
    )
    return (
        f"{summary}\n\n"
        f"Template Name: {item.get('name') or '-'}\n"
        f"Shortcut: {item.get('shortcut') or '-'}"
    )


def _template_keyboard(item):
    tid = str(item.get("template_id"))
    enabled = bool(item.get("enabled", True))
    rows = [
        [InlineKeyboardButton("✏️ Change Keyword", callback_data=f"ba_tpl_meta_{tid}")],
        [
            InlineKeyboardButton(
                "🔴 Disable" if enabled else "🟢 Enable",
                callback_data=f"ba_tpl_{tid}_toggle",
            ),
            InlineKeyboardButton(
                "🗑 Remove Keyword",
                callback_data=f"ba_tpl_{tid}_delete",
            ),
        ],
    ]
    common = _business_editor_keyboard(
        f"ba_tpl_{tid}",
        item,
        back_callback="ba_templates",
        allow_toggle=False,
    )
    rows.extend(common.inline_keyboard)
    return _kb(rows)




def _settings_text(s):
    return (
        "⚙️ Business Automation Settings\n\nControl how automation works for every connected account. These settings are shared across all accounts.\n\n"
        f"Automation: {'Enabled' if s.get('business_automation_enabled') else 'Disabled'}\n"
        f"Welcome Once: {'Enabled' if s.get('business_welcome_once', True) else 'Disabled'}\n"
        f"Ignore Own Messages: {'Enabled' if s.get('business_ignore_outgoing', True) else 'Disabled'}\n"
        f"Anti-loop: {'Enabled' if s.get('business_anti_loop', True) else 'Disabled'}\n"
        f"Flood Protection: {'Enabled' if s.get('business_flood_protection', True) else 'Disabled'}\n"
        f"Working Hours: {'Enabled' if s.get('business_working_hours_enabled') else 'Disabled'}\n"
        f"Reply Delay: {int(s.get('business_reply_delay_seconds', 0) or 0)} seconds"
    )


def _settings_keyboard(s):
    return _kb([
        [InlineKeyboardButton("Disable Automation" if s.get("business_automation_enabled") else "Enable Automation", callback_data="ba_setting_automation")],
        [InlineKeyboardButton("Disable Welcome Once" if s.get("business_welcome_once", True) else "Enable Welcome Once", callback_data="ba_setting_once")],
        [InlineKeyboardButton("Allow Own Messages" if s.get("business_ignore_outgoing", True) else "Ignore Own Messages", callback_data="ba_setting_outgoing")],
        [InlineKeyboardButton("Disable Anti-loop" if s.get("business_anti_loop", True) else "Enable Anti-loop", callback_data="ba_setting_loop")],
        [InlineKeyboardButton("Disable Flood Protection" if s.get("business_flood_protection", True) else "Enable Flood Protection", callback_data="ba_setting_flood")],
        [InlineKeyboardButton("Disable Working Hours" if s.get("business_working_hours_enabled") else "Enable Working Hours", callback_data="ba_setting_hours_toggle")],
        [InlineKeyboardButton("🕒 Set Working Hours", callback_data="ba_setting_hours")],
        [InlineKeyboardButton("⏱ Set Reply Delay", callback_data="ba_setting_delay")],
        [InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")],
    ])


def _preview_markup(rows, clone_username: str = ""):
    return _build_business_buttons(rows, clone_username=clone_username)


async def _send_preview(message, text, media_type, file_id, buttons, media_items=None):
    bot = message.get_bot()
    clone_username = str(getattr(bot, "username", "") or "").lstrip("@")
    markup = _preview_markup(buttons, clone_username)
    text = text or "Preview message"
    items = list(media_items or [])
    if not items and file_id:
        items = [{"type": media_type or "document", "file_id": file_id}]
    items = [x for x in items if x.get("file_id")][:10]
    if len(items) > 1:
        album = []
        for item in items:
            kind = str(item.get("type") or "document").lower()
            fid = str(item.get("file_id") or "")
            if kind == "photo":
                album.append(InputMediaPhoto(fid))
            elif kind == "video":
                album.append(InputMediaVideo(fid))
            else:
                album.append(InputMediaDocument(fid))
        await message.reply_media_group(album)
        await message.reply_text(text, reply_markup=markup)
    elif len(items) == 1:
        item = items[0]; kind = str(item.get("type") or "document"); fid = item.get("file_id")
        if kind == "photo": await message.reply_photo(fid, caption=text, reply_markup=markup)
        elif kind == "video": await message.reply_video(fid, caption=text, reply_markup=markup)
        elif kind == "animation": await message.reply_animation(fid, caption=text, reply_markup=markup)
        else: await message.reply_document(fid, caption=text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


def _mtproto_ready():
    return bool(TELEGRAM_API_ID and TELEGRAM_API_HASH)


async def _send_code(context, phone):
    client = TelegramClient(StringSession(), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    await client.connect()
    sent = await client.send_code_request(phone)
    context.user_data["ba_auth"] = {"step": "code", "phone": phone, "phone_code_hash": sent.phone_code_hash, "client": client}


async def _finish_auth(context, owner, code=None, password=None):
    auth = context.user_data.get("ba_auth") or {}
    client = auth.get("client")
    if not client:
        raise RuntimeError("Login session expired")
    if password is not None:
        await client.sign_in(password=password)
    else:
        await client.sign_in(phone=auth["phone"], code=code, phone_code_hash=auth["phone_code_hash"])
    me = await client.get_me()
    encrypted = encrypt_secret(StringSession.save(client.session))
    record = await save_business_account_session(owner, int(me.id), encrypted_session=encrypted, phone=auth.get("phone", ""), username=getattr(me, "username", "") or "", first_name=getattr(me, "first_name", "") or "")
    await client.disconnect()
    await business_automation_runtime.start_account(owner, int(me.id), record=record)
    context.user_data.pop("ba_auth", None)


async def _logout(record):
    token = record.get("encrypted_session")
    if not token or not _mtproto_ready():
        return
    client = TelegramClient(StringSession(decrypt_secret(token)), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await client.disconnect()


async def handle(self, update, context, q, owner, staff_record, action, role):
    if not action.startswith("ba_"):
        return False
    if role != "seller":
        await q.answer("Only the seller can manage Business Automation.", show_alert=True)
        return True

    # Inline Back/menu buttons also cancel any pending editor input.
    context.user_data.pop("ba_editor", None)
    context.user_data.pop("ba_media_batch", None)

    if action == "ba_home":
        text, markup = await _home(owner); await q.edit_message_text(text, reply_markup=markup); return True
    if action == "ba_accounts":
        accounts = await get_business_accounts(owner)
        lines = [
            "📱 Connected Accounts",
            "",
            "View all connected Telegram accounts here.",
            "Tap Disconnect only when you want to remove an account and stop its automation.",
            "",
        ]
        rows = []
        if not accounts:
            lines.append("No account is connected yet.")
        for i, x in enumerate(accounts, 1):
            account_id = int(x["account_user_id"])
            name = x.get("first_name") or x.get("username") or account_id
            username = f"@{x.get('username')}" if x.get("username") else "No username"
            lines.append(
                f"{i}. {name}\n{username}\n"
                f"Status: {x.get('connection_status', 'connected').title()}"
            )
            rows.append([
                InlineKeyboardButton(
                    f"🔌 Disconnect {x.get('username') or x.get('first_name') or account_id}",
                    callback_data=f"ba_disconnect_{account_id}",
                )
            ])
        rows.append([InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")])
        await q.edit_message_text("\n\n".join(lines), reply_markup=_kb(rows)); return True
    if action == "ba_connect":
        text = (
            "🔗 Connect Telegram Account\n\n"
            "Choose which type of Telegram account you want to connect.\n\n"
            "👤 Normal Telegram Account\n"
            "• Free to use\n"
            "• Connect with phone number, Telegram login code, and 2-step password when enabled\n"
            "• Supports Welcome Message, media, Auto Reply, and Reply Templates\n"
            "• Real inline callback buttons are not supported\n\n"
            "💼 Telegram Business Account\n"
            "• Telegram Business/Premium is required\n"
            "• Supports official Business integration and real inline buttons"
        )
        await q.edit_message_text(text, reply_markup=_kb([
            [InlineKeyboardButton("👤 Normal Account", callback_data="ba_connect_normal")],
            [InlineKeyboardButton("💼 Business Account", callback_data="ba_connect_official")],
            [InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")],
        ])); return True
    if action == "ba_connect_normal":
        if not _mtproto_ready():
            await q.edit_message_text("⚠️ Telegram API credentials are not configured by the platform owner.", reply_markup=_kb([[InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")]])); return True
        context.user_data["ba_auth"] = {"step": "phone"}
        await q.edit_message_text(
            "👤 Connect Normal Telegram Account\n\n"
            "Send the phone number with country code.\n"
            "Example: +919876543210\n\n"
            "Telegram will send a login code. Send /cancel to stop."
        ); return True
    if action == "ba_connect_official":
        await q.edit_message_text(
            "💼 Connect Telegram Business Account\n\n"
            "Telegram Business/Premium must be active on the account.\n\n"
            "Open Telegram Settings → Telegram Business → Chatbots, then connect this Clone Bot. "
            "After Telegram confirms the connection, return here.\n\n"
            "Normal accounts can still use Welcome Message, Auto Reply, and Reply Templates without Premium.",
            reply_markup=_kb([[InlineKeyboardButton("⬅ Connect Account", callback_data="ba_connect")]])
        ); return True
    if action == "ba_disconnect":
        accounts = await get_business_accounts(owner)
        if not accounts:
            await q.answer("No connected account.", show_alert=True); return True
        rows = [[InlineKeyboardButton(f"Disconnect {x.get('username') or x.get('first_name') or x.get('account_user_id')}", callback_data=f"ba_disconnect_{int(x['account_user_id'])}")] for x in accounts]
        rows.append([InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")])
        await q.edit_message_text("🔌 Select the Telegram account to disconnect.", reply_markup=_kb(rows)); return True
    if action.startswith("ba_disconnect_"):
        account_id = int(action.rsplit("_", 1)[1]); record = await get_business_account(owner, account_id)
        if record:
            try: await _logout(record)
            except Exception: logger.exception("Remote business logout failed owner=%s account=%s", owner, account_id)
        await business_automation_runtime.stop_account(owner, account_id)
        removed = await disconnect_business_account(owner, account_id)
        await q.answer("Account disconnected." if removed else "Account not found.", show_alert=not removed)
        text, markup = await _home(owner); await q.edit_message_text(text, reply_markup=markup); return True

    # Load only the data needed by the selected section.  Previously all three
    # editor collections were loaded for Settings and Statistics too, so one
    # editor-storage error made every Business Automation button appear dead.
    s = await get_seller_settings(owner)

    if action.startswith("ba_welcome"):
        welcome = await get_business_welcome(owner)
    else:
        welcome = None
    if action == "ba_auto" or action.startswith("ba_ar_"):
        auto_replies = await list_business_auto_replies(owner)
    else:
        auto_replies = []
    if action == "ba_templates" or action.startswith("ba_tpl_"):
        templates = await list_business_reply_templates(owner)
    else:
        templates = []

    if action == "ba_welcome":
        await q.edit_message_text(_welcome_text(welcome), reply_markup=_welcome_keyboard(welcome)); return True
    if action == "ba_welcome_toggle":
        welcome = await update_business_welcome(owner, enabled=not welcome.get("enabled", True))
        await q.edit_message_text(_welcome_text(welcome), reply_markup=_welcome_keyboard(welcome)); return True
    if action == "ba_welcome_text":
        context.user_data["ba_editor"] = {"field": "welcome_text"}
        await q.edit_message_text(_business_text_prompt("Business Welcome Text"), reply_markup=_input_keyboard("ba_welcome", remove_callback="ba_welcome_rmtext" if welcome.get("text") else None, remove_label="Remove Text")); return True
    if action == "ba_welcome_media":
        context.user_data["ba_editor"] = {"field": "welcome_media"}
        await q.edit_message_text(_business_media_prompt("Business Welcome Media"), reply_markup=_input_keyboard("ba_welcome", remove_callback="ba_welcome_rmmedia" if (welcome.get("media") or welcome.get("media_file_id")) else None, remove_label="Remove Media")); return True
    if action == "ba_welcome_buttons":
        context.user_data["ba_editor"] = {"field": "welcome_buttons"}
        await q.edit_message_text(_business_buttons_header(), reply_markup=_input_keyboard("ba_welcome", remove_callback="ba_welcome_rmbuttons" if welcome.get("buttons") else None, remove_label="Remove Buttons")); return True
    if action == "ba_welcome_rmtext":
        welcome = await update_business_welcome(owner, text="")
        await q.edit_message_text(_welcome_text(welcome), reply_markup=_welcome_keyboard(welcome)); return True
    if action == "ba_welcome_rmmedia":
        welcome = await update_business_welcome(owner, media_type="", media_file_id="", media=[])
        await q.edit_message_text(_welcome_text(welcome), reply_markup=_welcome_keyboard(welcome)); return True
    if action == "ba_welcome_rmbuttons":
        welcome = await update_business_welcome(owner, buttons=[])
        await q.edit_message_text(_welcome_text(welcome), reply_markup=_welcome_keyboard(welcome)); return True
    if action == "ba_welcome_see_text":
        await _send_business_component_preview(q.message, welcome, "text"); await q.answer("Saved text shown."); return True
    if action == "ba_welcome_see_media":
        await _send_business_component_preview(q.message, welcome, "media"); await q.answer("Saved media shown."); return True
    if action == "ba_welcome_see_buttons":
        await _send_business_component_preview(q.message, welcome, "buttons"); await q.answer("Saved buttons shown."); return True
    if action == "ba_welcome_preview":
        await _send_preview(q.message, welcome.get("text"), welcome.get("media_type"), welcome.get("media_file_id"), welcome.get("buttons"), welcome.get("media")); await q.answer("Preview sent."); return True

    if action == "ba_auto":
        await q.edit_message_text(
            "💬 Auto Reply Keywords\n\nCreate a keyword first. After saving it, the common editor opens so you can add text, media and buttons. When a customer sends that keyword, its saved reply is sent automatically.",
            reply_markup=_auto_replies_keyboard(auto_replies),
        ); return True
    if action == "ba_ar_add":
        context.user_data["ba_editor"] = {"field": "auto_keyword_add"}
        await q.edit_message_text("➕ Add Auto Reply Keyword\n\nSend one keyword or phrase.\nExamples: price, plan, payment", reply_markup=_kb([[InlineKeyboardButton("⬅ Back", callback_data="ba_auto")]])); return True
    if action.startswith("ba_ar_"):
        suffix = action[len("ba_ar_"):]
        op = ""; rid = ""
        for candidate in ("delete", "open", "keyword"):
            prefix = candidate + "_"
            if suffix.startswith(prefix): op = candidate; rid = suffix[len(prefix):]; break
        if not op:
            for candidate in ("toggle", "preview", "see_text", "see_media", "see_buttons", "rmtext", "rmmedia", "rmbuttons", "text", "media", "buttons"):
                marker = "_" + candidate
                if suffix.endswith(marker): rid = suffix[:-len(marker)]; op = candidate; break
        item = await get_business_auto_reply_item(owner, rid) if rid else None
        if not item:
            await q.answer("Auto reply not found.", show_alert=True); return True
        if op == "open":
            await q.edit_message_text(_auto_item_text(item), reply_markup=_auto_item_keyboard(item)); return True
        if op == "keyword":
            context.user_data["ba_editor"] = {"field": "auto_keyword_edit", "reply_id": rid}
            await q.edit_message_text("✏️ Change Keyword\n\nSend the new word or phrase.", reply_markup=_input_keyboard(f"ba_ar_open_{rid}")); return True
        if op == "text":
            context.user_data["ba_editor"] = {"field": "auto_item_text", "reply_id": rid}
            await q.edit_message_text(_business_text_prompt("Auto Reply Text"), reply_markup=_input_keyboard(f"ba_ar_open_{rid}", remove_callback=f"ba_ar_{rid}_rmtext" if item.get("text") else None, remove_label="Remove Text")); return True
        if op == "media":
            context.user_data["ba_editor"] = {"field": "auto_item_media", "reply_id": rid}
            await q.edit_message_text(_business_media_prompt("Auto Reply Media"), reply_markup=_input_keyboard(f"ba_ar_open_{rid}", remove_callback=f"ba_ar_{rid}_rmmedia" if (item.get("media") or item.get("media_file_id")) else None, remove_label="Remove Media")); return True
        if op == "buttons":
            context.user_data["ba_editor"] = {"field": "auto_item_buttons", "reply_id": rid}
            await q.edit_message_text(_business_buttons_header(), reply_markup=_input_keyboard(f"ba_ar_open_{rid}", remove_callback=f"ba_ar_{rid}_rmbuttons" if item.get("buttons") else None, remove_label="Remove Buttons")); return True
        if op == "toggle": item = await update_business_auto_reply_item(owner, rid, enabled=not item.get("enabled", True))
        elif op == "rmtext": item = await update_business_auto_reply_item(owner, rid, text="")
        elif op == "rmmedia": item = await update_business_auto_reply_item(owner, rid, media_type="", media_file_id="", media=[])
        elif op == "rmbuttons": item = await update_business_auto_reply_item(owner, rid, buttons=[])
        elif op == "delete":
            await delete_business_auto_reply_item(owner, rid)
            auto_replies = await list_business_auto_replies(owner)
            await q.edit_message_text("✅ Auto reply deleted.", reply_markup=_auto_replies_keyboard(auto_replies)); return True
        elif op == "see_text":
            await _send_business_component_preview(q.message, item, "text"); await q.answer("Saved text shown."); return True
        elif op == "see_media":
            await _send_business_component_preview(q.message, item, "media"); await q.answer("Saved media shown."); return True
        elif op == "see_buttons":
            await _send_business_component_preview(q.message, item, "buttons"); await q.answer("Saved buttons shown."); return True
        elif op == "preview":
            await _send_preview(q.message, item.get("text") or item.get("keyword"), item.get("media_type"), item.get("media_file_id"), item.get("buttons"), item.get("media")); await q.answer("Preview sent."); return True
        if item:
            await q.edit_message_text(_auto_item_text(item), reply_markup=_auto_item_keyboard(item)); return True

    if action == "ba_templates":
        await q.edit_message_text(
            "📝 Reply Templates\n\nCreate a replacement keyword, then configure its text, media and buttons. When the seller sends that keyword alone in a customer chat, Telegram replaces it with the saved template.\n\nExample keyword: payment",
            reply_markup=_templates_keyboard(templates),
        ); return True
    if action == "ba_tpl_add":
        context.user_data["ba_editor"] = {"field": "template_add"}
        await q.edit_message_text("➕ New Reply Template\n\nSend one unique keyword only. It may include special characters, but it cannot contain spaces.\n\nExample: payment", reply_markup=_kb([[InlineKeyboardButton("⬅ Back", callback_data="ba_templates")]])); return True

    if action.startswith("ba_tpl_"):
        suffix = action[len("ba_tpl_"):]
        op = ""; tid = ""
        for candidate in ("delete", "open", "meta"):
            prefix = candidate + "_"
            if suffix.startswith(prefix): op = candidate; tid = suffix[len(prefix):]; break
        if not op:
            # Common editor callbacks are ba_tpl_<id>_<operation>.
            for candidate in ("toggle", "delete", "preview", "see_text", "see_media", "see_buttons", "rmtext", "rmmedia", "rmbuttons", "text", "media", "buttons"):
                marker = "_" + candidate
                if suffix.endswith(marker): tid = suffix[:-len(marker)]; op = candidate; break
        item = await get_business_reply_template(owner, tid) if tid else None
        if not item:
            await q.answer("Template not found.", show_alert=True); return True
        if op == "open":
            await q.edit_message_text(_template_text(item), reply_markup=_template_keyboard(item)); return True
        if op == "meta":
            context.user_data["ba_editor"] = {"field": "template_meta", "template_id": tid}
            await q.edit_message_text("✏️ Change Template Keyword\n\nSend one unique keyword only. It cannot contain spaces.", reply_markup=_input_keyboard(f"ba_tpl_open_{tid}")); return True
        if op == "toggle":
            item = await update_business_reply_template(owner, tid, enabled=not item.get("enabled", True))
        if op == "text":
            context.user_data["ba_editor"] = {"field": "template_text", "template_id": tid}
            await q.edit_message_text(_business_text_prompt("Reply Template Text"), reply_markup=_input_keyboard(f"ba_tpl_open_{tid}", remove_callback=f"ba_tpl_{tid}_rmtext" if item.get("text") else None, remove_label="Remove Text")); return True
        if op == "media":
            context.user_data["ba_editor"] = {"field": "template_media", "template_id": tid}
            await q.edit_message_text(_business_media_prompt("Reply Template Media"), reply_markup=_input_keyboard(f"ba_tpl_open_{tid}", remove_callback=f"ba_tpl_{tid}_rmmedia" if (item.get("media") or item.get("media_file_id")) else None, remove_label="Remove Media")); return True
        if op == "buttons":
            context.user_data["ba_editor"] = {"field": "template_buttons", "template_id": tid}
            await q.edit_message_text(_business_buttons_header(), reply_markup=_input_keyboard(f"ba_tpl_open_{tid}", remove_callback=f"ba_tpl_{tid}_rmbuttons" if item.get("buttons") else None, remove_label="Remove Buttons")); return True
        if op == "rmtext": item = await update_business_reply_template(owner, tid, text="")
        elif op == "rmmedia": item = await update_business_reply_template(owner, tid, media_type="", media_file_id="", media=[])
        elif op == "rmbuttons": item = await update_business_reply_template(owner, tid, buttons=[])
        elif op == "delete":
            await delete_business_reply_template(owner, tid)
            templates = await list_business_reply_templates(owner)
            await q.edit_message_text("✅ Reply template deleted.", reply_markup=_templates_keyboard(templates)); return True
        elif op == "see_text":
            await _send_business_component_preview(q.message, item, "text"); await q.answer("Saved text shown."); return True
        elif op == "see_media":
            await _send_business_component_preview(q.message, item, "media"); await q.answer("Saved media shown."); return True
        elif op == "see_buttons":
            await _send_business_component_preview(q.message, item, "buttons"); await q.answer("Saved buttons shown."); return True
        elif op == "preview":
            await _send_preview(q.message, item.get("text") or item.get("name"), item.get("media_type"), item.get("media_file_id"), item.get("buttons"), item.get("media")); await q.answer("Preview sent."); return True
        if item:
            await q.edit_message_text(_template_text(item), reply_markup=_template_keyboard(item)); return True

    if action == "ba_settings": await q.edit_message_text(_settings_text(s),reply_markup=_settings_keyboard(s)); return True
    toggle_map={"ba_setting_automation":("business_automation_enabled",False),"ba_setting_once":("business_welcome_once",True),"ba_setting_outgoing":("business_ignore_outgoing",True),"ba_setting_loop":("business_anti_loop",True),"ba_setting_flood":("business_flood_protection",True),"ba_setting_hours_toggle":("business_working_hours_enabled",False)}
    if action in toggle_map:
        key,default=toggle_map[action]; await set_seller_setting(owner,key,not s.get(key,default)); s=await get_seller_settings(owner); await q.edit_message_text(_settings_text(s),reply_markup=_settings_keyboard(s)); return True
    if action == "ba_stats":
        st = await business_automation_stats(owner)
        text = (
            "📊 Business Automation Statistics\n\n"
            "💼 Business Accounts\n"
            f"• Connected: {int(st.get('accounts', 0))}\n"
            f"• Total Connection Records: {int(st.get('accounts_total', 0))}\n\n"
            "👥 Business Customers\n"
            f"• Active Customers: {int(st.get('connected_users', 0))}\n"
            f"• Active Today: {int(st.get('active_today', 0))}\n"
            f"• Total Customers: {int(st.get('customers_total', 0))}\n"
            f"• Conversations: {int(st.get('conversations', 0))}\n\n"
            "⚡ Automation Activity\n"
            f"• Welcome Messages Sent: {int(st.get('welcome_sent', 0))}\n"
            f"• Auto Replies Sent: {int(st.get('auto_replies_sent', 0))}\n"
            f"• Reply Templates Used: {int(st.get('templates_used', 0))}\n\n"
            "🔘 Feature Opens\n"
            f"• Plans: {int(st.get('plans_opened', 0))}\n"
            f"• Renew: {int(st.get('renew_opened', 0))}\n"
            f"• Profile: {int(st.get('profile_opened', 0))}\n"
            f"• Referral: {int(st.get('referral_opened', 0))}\n\n"
        )
        await q.edit_message_text(
            text,
            reply_markup=_kb([
                [InlineKeyboardButton("🔄 Refresh", callback_data="ba_stats")],
                [InlineKeyboardButton("⬅ Business Automation", callback_data="ba_home")],
            ]),
        )
        return True
    return True


async def handle_text(self, update, context):
    owner = self.owner(context)
    if int(update.effective_user.id) != int(self.seller_account(context)):
        return False
    text = (update.effective_message.text or "").strip()
    auth = context.user_data.get("ba_auth")
    if auth:
        if text.lower() == "/cancel":
            client=auth.get("client")
            if client:
                try: await client.disconnect()
                except Exception: pass
            context.user_data.pop("ba_auth",None); await update.effective_message.reply_text("Connection cancelled.",reply_markup=_kb([[InlineKeyboardButton("💼 Business Automation",callback_data="ba_home")]])); return True
        try:
            step=auth.get("step")
            if step == "phone":
                await _send_code(context,text)
                await update.effective_message.reply_text(
                    "✅ Login code sent by Telegram.\n\n"
                    "⚠️ Do not send the code as 5 digits together because Telegram may invalidate a login code shared directly in a chat.\n\n"
                    "Send it with spaces, for example: 1 2 3 4 5\n"
                    "or with a hyphen: 12-345"
                )
                return True
            if step == "code":
                # Telegram can invalidate a login code when the exact digits are shared in a Telegram chat.
                # Require separators, then remove them locally before MTProto verification.
                digits = "".join(ch for ch in text if ch.isdigit())
                has_separator = any(not ch.isdigit() for ch in text)
                if not has_separator or len(digits) < 5:
                    await update.effective_message.reply_text(
                        "⚠️ Do not send the code as plain digits.\n\n"
                        "Send it with spaces, for example: 1 2 3 4 5\n"
                        "or with a hyphen: 12-345\n\n"
                        "If you already sent the plain code, request a new code first because Telegram may have invalidated it."
                    )
                    return True
                try:
                    await _finish_auth(context,owner,code=digits)
                except SessionPasswordNeededError:
                    auth["step"]="password"
                    context.user_data["ba_auth"]=auth
                    await update.effective_message.reply_text("🔐 Two-step verification is enabled. Send your Telegram password.")
                    return True
                await update.effective_message.reply_text("✅ Telegram account connected successfully.",reply_markup=_kb([[InlineKeyboardButton("💼 Business Automation",callback_data="ba_home")]]))
                return True
            if step == "password":
                await _finish_auth(context,owner,password=text); await update.effective_message.reply_text("✅ Telegram account connected successfully.",reply_markup=_kb([[InlineKeyboardButton("💼 Business Automation",callback_data="ba_home")]])); return True
        except PhoneCodeExpiredError:
            context.user_data["ba_auth"] = {"step": "phone"}
            await update.effective_message.reply_text(
                "❌ Telegram invalidated or expired this code.\n\n"
                "Send your phone number again, then enter the new code with spaces, for example: 1 2 3 4 5."
            )
            return True
        except (PhoneNumberInvalidError,PhoneCodeInvalidError,PasswordHashInvalidError) as exc:
            await update.effective_message.reply_text(f"❌ Telegram login failed: {type(exc).__name__}. Please try again.")
            return True
        except Exception:
            logger.exception("Business account login failed owner=%s",owner); await update.effective_message.reply_text("❌ Telegram account could not be connected. Please try again."); return True

    editor = context.user_data.get("ba_editor")
    if not editor:
        return False
    field = str(editor.get("field") or "")
    template_id = str(editor.get("template_id") or "")
    try:
        if field == "welcome_text":
            await update_business_welcome(owner, text=text)
        elif field == "auto_keyword_add":
            item = await create_business_auto_reply_item(owner, text)
            context.user_data.pop("ba_editor", None)
            await update.effective_message.reply_text(
                "✅ Keyword created. Now add its text, media and buttons.",
                reply_markup=_auto_item_keyboard(item),
            )
            return True
        elif field == "auto_keyword_edit":
            await update_business_auto_reply_item(owner, str(editor.get("reply_id") or ""), keyword=text)
        elif field == "auto_item_text":
            await update_business_auto_reply_item(owner, str(editor.get("reply_id") or ""), text=text)
        elif field == "welcome_buttons":
            await update_business_welcome(owner, buttons=_parse_business_buttons(text), buttons_input=text)
        elif field == "auto_item_buttons":
            await update_business_auto_reply_item(owner, str(editor.get("reply_id") or ""), buttons=_parse_business_buttons(text), buttons_input=text)
        elif field == "template_buttons":
            await update_business_reply_template(owner, template_id, buttons=_parse_business_buttons(text), buttons_input=text)
        elif field == "template_add":
            keyword = text.strip()
            if not keyword or any(ch.isspace() for ch in keyword):
                raise ValueError("Send one keyword only, without spaces")
            existing = await list_business_reply_templates(owner)
            if any(str(x.get("shortcut") or "").casefold() == keyword.casefold() for x in existing):
                raise ValueError("This keyword already exists")
            item = await create_business_reply_template(owner, keyword[:64], keyword[:80])
            context.user_data.pop("ba_editor", None)
            await update.effective_message.reply_text(
                "✅ Reply template created. Now add its text, media and buttons.",
                reply_markup=_template_keyboard(item),
            )
            return True
        elif field == "template_meta":
            keyword = text.strip()
            if not keyword or any(ch.isspace() for ch in keyword):
                raise ValueError("Send one keyword only, without spaces")
            existing = await list_business_reply_templates(owner)
            if any(str(x.get("template_id")) != template_id and str(x.get("shortcut") or "").casefold() == keyword.casefold() for x in existing):
                raise ValueError("This keyword already exists")
            await update_business_reply_template(owner, template_id, shortcut=keyword[:64], name=keyword[:80])
        elif field == "template_text":
            await update_business_reply_template(owner, template_id, text=text)
        elif field == "delay":
            value = int(text)
            if not 0 <= value <= 300:
                raise ValueError("Reply delay must be 0-300 seconds")
            await set_seller_setting(owner, "business_reply_delay_seconds", value)
        elif field == "working_hours":
            parts = [part.strip() for part in text.split("|")]
            if len(parts) != 3:
                raise ValueError("Use: HH:MM | HH:MM | Timezone")
            datetime.strptime(parts[0], "%H:%M")
            datetime.strptime(parts[1], "%H:%M")
            ZoneInfo(parts[2])
            await set_seller_setting(owner, "business_working_hours_start", parts[0])
            await set_seller_setting(owner, "business_working_hours_end", parts[1])
            await set_seller_setting(owner, "business_working_hours_timezone", parts[2])
        else:
            return False
    except (ValueError, TypeError) as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
        return True

    context.user_data.pop("ba_editor", None)
    if field.startswith("welcome_"):
        item = await get_business_welcome(owner)
        await update.effective_message.reply_text(_welcome_text(item), reply_markup=_welcome_keyboard(item))
    elif field.startswith("auto_item_") or field == "auto_keyword_edit":
        item = await get_business_auto_reply_item(owner, str(editor.get("reply_id") or ""))
        await update.effective_message.reply_text(_auto_item_text(item), reply_markup=_auto_item_keyboard(item))
    elif field.startswith("template_"):
        item = await get_business_reply_template(owner, template_id)
        await update.effective_message.reply_text(_template_text(item), reply_markup=_template_keyboard(item))
    else:
        home_text, home_markup = await _home(owner)
        await update.effective_message.reply_text(home_text, reply_markup=home_markup)
    return True


async def handle_media(self, update, context):
    owner = self.owner(context)
    if int(update.effective_user.id) != int(self.seller_account(context)):
        return False
    editor = context.user_data.get("ba_editor") or {}
    field = str(editor.get("field") or "")
    if field not in {"welcome_media", "auto_item_media", "template_media"}:
        return False

    msg = update.effective_message
    media_type = ""
    file_id = ""
    if msg.photo:
        media_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        media_type, file_id = "video", msg.video.file_id
    elif msg.animation:
        media_type, file_id = "animation", msg.animation.file_id
    elif msg.document:
        media_type, file_id = "document", msg.document.file_id
    if not file_id:
        return False

    async def save_items(items):
        items = items[:10]
        first = items[0] if items else {"type": "", "file_id": ""}
        if field == "welcome_media":
            await update_business_welcome(owner, media=items, media_type=first["type"], media_file_id=first["file_id"])
            back = "ba_welcome"
        elif field == "auto_item_media":
            rid = str(editor.get("reply_id") or "")
            await update_business_auto_reply_item(owner, rid, media=items, media_type=first["type"], media_file_id=first["file_id"])
            back = f"ba_ar_open_{rid}"
        else:
            tid = str(editor.get("template_id") or "")
            item = await update_business_reply_template(owner, tid, media=items, media_type=first["type"], media_file_id=first["file_id"])
            if not item:
                await msg.reply_text("❌ Reply template not found.")
                return
            back = f"ba_tpl_open_{tid}"
        context.user_data.pop("ba_editor", None)
        context.user_data.pop("ba_media_batch", None)
        if field == "welcome_media":
            current = await get_business_welcome(owner)
            await msg.reply_text(_welcome_text(current), reply_markup=_welcome_keyboard(current))
        elif field == "auto_item_media":
            current = await get_business_auto_reply_item(owner, str(editor.get("reply_id") or ""))
            await msg.reply_text(_auto_item_text(current), reply_markup=_auto_item_keyboard(current))
        else:
            current = await get_business_reply_template(owner, str(editor.get("template_id") or ""))
            await msg.reply_text(_template_text(current), reply_markup=_template_keyboard(current))

    item = {"type": media_type, "file_id": file_id}
    group_id = str(msg.media_group_id or "")
    if not group_id:
        await save_items([item])
        return True

    batch = context.user_data.get("ba_media_batch")
    if not batch or batch.get("group_id") != group_id:
        batch = {"group_id": group_id, "items": [], "generation": 0}
        context.user_data["ba_media_batch"] = batch
    if len(batch["items"]) < 10:
        batch["items"].append(item)
    batch["generation"] += 1
    generation = batch["generation"]

    async def finalize_album():
        await asyncio.sleep(1.2)
        current = context.user_data.get("ba_media_batch") or {}
        if current.get("group_id") != group_id or current.get("generation") != generation:
            return
        await save_items(list(current.get("items") or []))

    context.application.create_task(finalize_album())
    return True

