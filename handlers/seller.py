import asyncio
import logging
from html import escape

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken, TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
)
from telethon.sessions import StringSession
from handlers.clone.help_center import HELP_PAGES, HELP_LABELS

from database.seller_bots import (
    BotOwnershipError,
    count_owner_bots,
    delete_bot,
    get_bot,
    get_bot_by_bot_id,
    get_bots,
    save_bot,
    set_bot_active,
)
from database.seller_subscriptions import (
    create_plan_request,
    current_plan_text,
    effective_plan,
    seller_usage,
    get_config,
    plan_limit_warning,
    start_trial,
    subscription_history,
    choose_verified_plan_purchase,
    pending_plan_purchase,
)
from services.bot_manager import bot_manager
from services.invite_resend_lock import resend_invites_safely
from database.subscription_guard import get_active_invite, save_invite
from database.seller_data import (
    get_seller_settings, set_seller_setting, stats as seller_stats,
    get_channels, add_channel, remove_channel,
    get_business_accounts, count_business_accounts, business_automation_stats,
    disconnect_business_account, get_business_account,
    save_business_account_session,
)
from database.seller_referrals import seller_referral_stats
from database.platform_features import get_policy
from database.mongo import get_database
from database.sellers import get_or_create_seller, get_seller
from utils.timezone_ui import timezone_guide, timezone_keyboard, timezone_from_key, normalize_timezone
from config import ADMIN_IDS, TELEGRAM_API_ID, TELEGRAM_API_HASH
from database.users import get_user as get_platform_user
from database.payment_gateways import SUPPORTED_GATEWAYS, get_gateway_config, create_gateway_transaction
from services.payment_gateways import create_checkout, GatewayError
from utils.crypto import encrypt_secret, decrypt_secret


logger = logging.getLogger(__name__)


def _trial_datetime(value) -> str:
    if not value:
        return "Not available"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")


async def _activate_first_clone_trial(message, owner_id: int) -> bool:
    """Activate and announce the one-time trial after the first clone connects."""
    try:
        assignment = await start_trial(owner_id)
        plan, _ = await effective_plan(owner_id)
        config = await get_config()
    except ValueError as exc:
        logger.info("Free trial not activated owner_id=%s reason=%s", owner_id, exc)
        return False
    except Exception:
        logger.exception("Free trial activation failed owner_id=%s", owner_id)
        return False

    trial_days = int(config.get("trial_days", 7))
    plan_name = plan.get("name") or str(assignment.get("plan_id", "Starter")).replace("_", " ").title()
    text = (
        "🎉 Welcome to Subscription SaaS!\n\n"
        "Your Free Trial has been activated successfully.\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"🎁 Plan: {plan_name} (Free Trial)\n"
        f"📅 Duration: {trial_days} Days\n"
        f"📅 Activation Date: {_trial_datetime(assignment.get('created_at') or assignment.get('updated_at'))}\n"
        f"⏳ Expiry Date: {_trial_datetime(assignment.get('expiry_date'))}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "📊 Free Trial Limits\n"
        f"• 🤖 Clone Bots: {_display_plan_limit(plan.get('bot_limit'))}\n"
        f"• 👥 Active Subscribers: {_display_plan_limit(plan.get('active_subscriber_limit'))}\n"
        f"• 📢 Channels/Groups: {_display_plan_limit(plan.get('channel_limit'))}\n"
        f"• 📦 Subscription Plans: {_display_plan_limit(plan.get('plan_limit'))}\n"
        f"• 👨‍💼 Admins: {_display_plan_limit(plan.get('admin_limit'))}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🚀 Your Clone Bot is now ready to use.\n\n"
        "When the free trial expires, you can upgrade to a paid plan anytime "
        "from Profile → Buy/Upgrade Plan."
    )
    await message.reply_text(text)
    return True


async def send_seller_upgrade_plan(message, owner_id: int) -> None:
    """Send the seller plan selector from commands/deep links."""
    cfg = await get_config()
    plans = [p for p in cfg.get("paid_plans", []) if p.get("active", True)]
    rows = []
    lines = ["💎 Buy / Change Seller Plan", ""]
    current, _ = await effective_plan(owner_id)
    for plan in plans:
        lines.append(
            f"• {plan.get('name', 'Plan')} — ₹{plan.get('price', 0):g} / "
            f"{plan.get('duration_days', 30)} days"
        )
        request_type = (
            "upgrade"
            if float(plan.get("price", 0)) >= float(current.get("price", 0))
            else "downgrade"
        )
        rows.append([
            InlineKeyboardButton(
                f"Select {plan.get('name', 'Plan')}",
                callback_data=f"seller_buy_{request_type}_{plan.get('plan_id')}",
            )
        ])
    if not plans:
        lines.append("No paid seller plans are available right now.")
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="main_home")])
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


_channel_operation_locks: dict[int, asyncio.Lock] = {}


def _channel_lock(owner_id: int) -> asyncio.Lock:
    """Serialize channel add/remove/resend operations per seller."""
    lock = _channel_operation_locks.get(int(owner_id))
    if lock is None:
        lock = asyncio.Lock()
        _channel_operation_locks[int(owner_id)] = lock
    return lock




def _format_dt(value) -> str:
    value = _aware_utc(value)
    if not value:
        return "Not available"
    return value.strftime("%d %b %Y, %I:%M %p UTC")


def _limit_text(value) -> str:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return escape(str(value))
    return "Unlimited" if value < 0 else f"{value:,}"


async def _notify_owner_clone_bot_added(
    context: ContextTypes.DEFAULT_TYPE,
    seller_user,
    bot_user,
    bot_token: str,
):
    """Send a detailed clone-bot registration report to every platform owner.

    Telegram does not expose a user's real Telegram-account creation date.
    The stored platform join date (first interaction with the main bot) is used.
    Notification failures are logged and never break clone-bot registration.
    """
    seller_id = int(seller_user.id)
    try:
        seller, platform_user, plan_data, usage, channels = await asyncio.gather(
            get_seller(seller_id),
            get_platform_user(seller_id),
            effective_plan(seller_id),
            seller_usage(seller_id),
            get_channels(seller_id),
        )
        plan, assignment = plan_data
        seller = seller or {}
        platform_user = platform_user or {}

        full_name = " ".join(
            part for part in [seller_user.first_name, seller_user.last_name] if part
        ).strip() or "Unknown"
        username = f"@{seller_user.username}" if seller_user.username else "Not set"
        mention = (
            f'<a href="tg://user?id={seller_id}">{escape(full_name)}</a>'
        )

        expiry = (assignment or {}).get("expiry_date")
        plan_status = "Active"
        if expiry and _aware_utc(expiry) <= datetime.now(timezone.utc):
            plan_status = "Expired / Free fallback"

        bot_username = (bot_user.username or "").lstrip("@")
        bot_username_text = f"@{escape(bot_username)}" if bot_username else "Not set"
        bot_link = (
            f'<a href="https://t.me/{escape(bot_username)}">{bot_username_text}</a>'
            if bot_username else bot_username_text
        )

        lines = [
            "🆕 <b>New Clone Bot Registered</b>",
            "",
            "👤 <b>Seller Details</b>",
            f"• Name: {escape(full_name)}",
            f"• Mention: {mention}",
            f"• Username: {escape(username)}",
            f"• Seller ID: <code>{seller_id}</code>",
            f"• Platform Joining Date: {_format_dt(platform_user.get('joined_at') or seller.get('created_at'))}",
            "",
            "💎 <b>Seller Plan & Limits</b>",
            f"• Plan: {escape(str(plan.get('name') or 'Free'))}",
            f"• Status: {escape(plan_status)}",
            f"• Expiry: {_format_dt(expiry) if expiry else 'No expiry'}",
            f"• Clone Bots: {usage.get('bot_count', 0):,} / {_limit_text(plan.get('bot_limit', 1))}",
            f"• Active Subscribers: {usage.get('active_subscriber_count', 0):,} / {_limit_text(plan.get('active_subscriber_limit', 25))}",
            f"• Channels/Groups: {usage.get('channel_count', 0):,} / {_limit_text(plan.get('channel_limit', 1))}",
            f"• Subscription Plans: {usage.get('plan_count', 0):,} / {_limit_text(plan.get('plan_limit', 2))}",
            "",
            "🤖 <b>Clone Bot Details</b>",
            f"• Name: {escape(bot_user.first_name or 'Unknown')}",
            f"• Username: {bot_link}",
            f"• Bot ID: <code>{bot_user.id}</code>",
            "",
            "🔑 <b>Bot Token</b>",
            f"<code>{escape(bot_token)}</code>",
            "",
            "📢 <b>Connected Channels/Groups</b>",
        ]

        if channels:
            for index, channel in enumerate(channels, start=1):
                title = escape(str(channel.get("title") or "Unnamed"))
                chat_type = escape(str(channel.get("chat_type") or "unknown"))
                chat_id = int(channel.get("chat_id", 0))
                lines.append(
                    f"{index}. {title} ({chat_type}) — <code>{chat_id}</code>"
                )
        else:
            lines.append("• None connected yet")

        lines.extend([
            "",
            f"🕒 Registered: {_format_dt(datetime.now(timezone.utc))}",
        ])

        # Telegram messages are capped at 4096 characters. Keep the first report
        # complete and split unusually long channel lists into safe continuations.
        chunks = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 3900:
                chunks.append(current)
                current = "📢 <b>Connected Channels/Groups (continued)</b>\n" + line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for admin_id in {int(value) for value in ADMIN_IDS}:
            for chunk in chunks:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=chunk,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    logger.exception(
                        "Failed to notify owner about clone bot registration "
                        "admin_id=%s seller_id=%s bot_id=%s",
                        admin_id,
                        seller_id,
                        bot_user.id,
                    )
    except Exception:
        logger.exception(
            "Could not build clone bot registration notification "
            "seller_id=%s bot_id=%s",
            seller_id,
            bot_user.id,
        )


def main_seller_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Manage My Clone Bots", callback_data="seller_bots_list")],
        [InlineKeyboardButton("➕ Create New Clone Bot", callback_data="seller_connect")],
        [InlineKeyboardButton("💼 Business Automation", callback_data="seller_business")],
        [InlineKeyboardButton("💳 Buy / Change Plan", callback_data="seller_upgrade_plan")],
        [InlineKeyboardButton("📊 View Current Plan", callback_data="seller_current_plan")],
        [InlineKeyboardButton("📜 Plan History", callback_data="seller_plan_history")],
        [InlineKeyboardButton("🌐 Official Links", callback_data="official_links_open")],
    ])


def business_automation_keyboard(connected_count:int, enabled:bool):
    rows=[
        [InlineKeyboardButton("🔗 Connect Telegram Account", callback_data="seller_business_connect")],
        [InlineKeyboardButton(f"📱 Connected Accounts ({connected_count})", callback_data="seller_business_accounts")],
        [InlineKeyboardButton("👋 Welcome Message", callback_data="seller_business_welcome")],
        [InlineKeyboardButton("💬 Auto Reply & Reply Templates", callback_data="seller_business_replies")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="seller_business_settings")],
        [InlineKeyboardButton("📊 Statistics", callback_data="seller_business_statistics")],
    ]
    if connected_count:
        rows.append([InlineKeyboardButton("🔌 Disconnect Account", callback_data="seller_business_disconnect")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="main_home")])
    return InlineKeyboardMarkup(rows)


def business_settings_keyboard(settings:dict):
    enabled=bool(settings.get("business_automation_enabled"))
    once=bool(settings.get("business_welcome_once",True))
    ignore_outgoing=bool(settings.get("business_ignore_outgoing",True))
    anti_loop=bool(settings.get("business_anti_loop",True))
    flood=bool(settings.get("business_flood_protection",True))
    working=bool(settings.get("business_working_hours_enabled",False))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Disable Automation" if enabled else "Enable Automation",callback_data="seller_business_toggle")],
        [InlineKeyboardButton("Disable Welcome Once" if once else "Enable Welcome Once",callback_data="seller_business_once")],
        [InlineKeyboardButton("Allow Own Messages" if ignore_outgoing else "Ignore Own Messages",callback_data="seller_business_ignore_outgoing")],
        [InlineKeyboardButton("Disable Anti-loop" if anti_loop else "Enable Anti-loop",callback_data="seller_business_anti_loop")],
        [InlineKeyboardButton("Disable Flood Protection" if flood else "Enable Flood Protection",callback_data="seller_business_flood")],
        [InlineKeyboardButton("Disable Working Hours" if working else "Enable Working Hours",callback_data="seller_business_working_toggle")],
        [InlineKeyboardButton("🕒 Set Working Hours",callback_data="seller_business_working_hours")],
        [InlineKeyboardButton("⏱ Set Reply Delay",callback_data="seller_business_settings_delay")],
        [InlineKeyboardButton("🔘 Action Button Mode",callback_data="seller_business_action_mode")],
        [InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")],
    ])


def business_settings_text(settings:dict) -> str:
    start=str(settings.get("business_working_hours_start") or "09:00")
    end=str(settings.get("business_working_hours_end") or "21:00")
    tz=str(settings.get("business_working_hours_timezone") or settings.get("timezone") or "Asia/Kolkata")
    mode=str(settings.get("business_action_button_mode") or "clone_bot")
    return (
        "⚙️ Business Automation Settings\n\n"
        f"Automation: {'Enabled' if settings.get('business_automation_enabled') else 'Disabled'}\n"
        f"Welcome Once: {'Enabled' if settings.get('business_welcome_once',True) else 'Disabled'}\n"
        f"Ignore Own Messages: {'Enabled' if settings.get('business_ignore_outgoing',True) else 'Disabled'}\n"
        f"Anti-loop: {'Enabled' if settings.get('business_anti_loop',True) else 'Disabled'}\n"
        f"Flood Protection: {'Enabled' if settings.get('business_flood_protection',True) else 'Disabled'}\n"
        f"Working Hours: {'Enabled' if settings.get('business_working_hours_enabled',False) else 'Disabled'} ({start}-{end}, {tz})\n"
        f"Reply Delay: {int(settings.get('business_reply_delay_seconds',0) or 0)} seconds\n"
        f"Action Buttons: {'Open Clone Bot' if mode == 'clone_bot' else 'Stay in Account Chat'}"
    )


def business_welcome_keyboard(settings:dict):
    enabled=bool(settings.get("business_welcome_enabled",True))
    has_media=bool(settings.get("business_welcome_media_file_id"))
    button_count=sum(len(row) for row in (settings.get("business_welcome_buttons") or []))
    rows=[
        [InlineKeyboardButton("Disable Welcome" if enabled else "Enable Welcome",callback_data="seller_business_welcome_toggle")],
        [InlineKeyboardButton("✏️ Set Welcome Text",callback_data="seller_business_welcome_text")],
        [InlineKeyboardButton("🖼 Set Welcome Media",callback_data="seller_business_welcome_media")],
    ]
    if has_media:
        rows.append([InlineKeyboardButton("🗑 Remove Media",callback_data="seller_business_welcome_media_remove")])
    rows.extend([
        [InlineKeyboardButton("➕ Add URL Button",callback_data="seller_business_welcome_button_add")],
        [InlineKeyboardButton(f"🗑 Clear URL Buttons ({button_count})",callback_data="seller_business_welcome_buttons_clear")],
        [InlineKeyboardButton("👁 Preview",callback_data="seller_business_welcome_preview")],
        [InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")],
    ])
    return InlineKeyboardMarkup(rows)


def business_welcome_text(settings:dict) -> str:
    buttons=sum(len(row) for row in (settings.get("business_welcome_buttons") or []))
    return (
        "👋 Business Welcome Message\n\n"
        f"Status: {'Enabled' if settings.get('business_welcome_enabled',True) else 'Disabled'}\n"
        f"Text: {'Added' if settings.get('business_welcome_message') else 'Not added'}\n"
        f"Media: {'Added' if settings.get('business_welcome_media_file_id') else 'Not added'}\n"
        f"URL Buttons: {buttons}\n\n"
        "This shared welcome setup applies to every connected Telegram account."
    )


def business_replies_keyboard(settings:dict):
    auto_enabled=bool(settings.get("business_auto_reply_enabled",True))
    templates_enabled=bool(settings.get("business_templates_enabled",True))
    template_count=len(settings.get("business_reply_templates") or [])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💬 Auto Reply ({'On' if auto_enabled else 'Off'})",callback_data="seller_business_auto_reply")],
        [InlineKeyboardButton(f"📝 Reply Templates ({template_count})",callback_data="seller_business_templates")],
        [InlineKeyboardButton("Disable Templates" if templates_enabled else "Enable Templates",callback_data="seller_business_templates_toggle")],
        [InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")],
    ])


def business_auto_reply_keyboard(settings:dict):
    enabled=bool(settings.get("business_auto_reply_enabled",True))
    has_media=bool(settings.get("business_auto_reply_media_file_id"))
    button_count=sum(len(row) for row in (settings.get("business_auto_reply_buttons") or []))
    rows=[
        [InlineKeyboardButton("Disable Auto Reply" if enabled else "Enable Auto Reply",callback_data="seller_business_auto_reply_toggle")],
        [InlineKeyboardButton("✏️ Set Reply Text",callback_data="seller_business_auto_reply_text")],
        [InlineKeyboardButton("🖼 Set Reply Media",callback_data="seller_business_auto_reply_media")],
    ]
    if has_media:
        rows.append([InlineKeyboardButton("🗑 Remove Media",callback_data="seller_business_auto_reply_media_remove")])
    rows.extend([
        [InlineKeyboardButton("➕ Add URL Button",callback_data="seller_business_auto_reply_button_add")],
        [InlineKeyboardButton(f"🗑 Clear URL Buttons ({button_count})",callback_data="seller_business_auto_reply_buttons_clear")],
        [InlineKeyboardButton("⏱ Set Reply Delay",callback_data="seller_business_auto_reply_delay")],
        [InlineKeyboardButton("👁 Preview",callback_data="seller_business_auto_reply_preview")],
        [InlineKeyboardButton("⬅ Auto Reply & Templates",callback_data="seller_business_replies")],
    ])
    return InlineKeyboardMarkup(rows)


def business_auto_reply_text(settings:dict) -> str:
    buttons=sum(len(row) for row in (settings.get("business_auto_reply_buttons") or []))
    return (
        "💬 Business Auto Reply\n\n"
        f"Status: {'Enabled' if settings.get('business_auto_reply_enabled',True) else 'Disabled'}\n"
        f"Text: {'Added' if settings.get('business_auto_reply_message') else 'Not added'}\n"
        f"Media: {'Added' if settings.get('business_auto_reply_media_file_id') else 'Not added'}\n"
        f"URL Buttons: {buttons}\n"
        f"Reply Delay: {int(settings.get('business_reply_delay_seconds',0) or 0)} seconds\n\n"
        "This shared auto reply applies to every connected Telegram account."
    )


def _business_templates(settings:dict) -> list:
    return list(settings.get("business_reply_templates") or [])


def _business_find_template(settings:dict, template_id:str):
    for item in _business_templates(settings):
        if str(item.get("id"))==str(template_id):
            return item
    return None


def business_templates_keyboard(settings:dict):
    rows=[]
    for item in _business_templates(settings)[:40]:
        title=str(item.get("name") or item.get("shortcut") or "Template")[:35]
        rows.append([InlineKeyboardButton(f"📝 {title}",callback_data=f"seller_business_template_{item.get('id')}")])
    rows.append([InlineKeyboardButton("➕ Add Reply Template",callback_data="seller_business_template_add")])
    rows.append([InlineKeyboardButton("⬅ Auto Reply & Templates",callback_data="seller_business_replies")])
    return InlineKeyboardMarkup(rows)


def business_template_keyboard(template:dict):
    tid=str(template.get("id"))
    has_media=bool(template.get("media_file_id"))
    button_count=sum(len(row) for row in (template.get("buttons") or []))
    rows=[
        [InlineKeyboardButton("✏️ Edit Name & Shortcut",callback_data=f"seller_business_template_meta_{tid}")],
        [InlineKeyboardButton("✏️ Set Template Text",callback_data=f"seller_business_template_text_{tid}")],
        [InlineKeyboardButton("🖼 Set Template Media",callback_data=f"seller_business_template_media_{tid}")],
    ]
    if has_media:
        rows.append([InlineKeyboardButton("🗑 Remove Media",callback_data=f"seller_business_template_media_remove_{tid}")])
    rows.extend([
        [InlineKeyboardButton("➕ Add URL Button",callback_data=f"seller_business_template_button_{tid}")],
        [InlineKeyboardButton(f"🗑 Clear URL Buttons ({button_count})",callback_data=f"seller_business_template_buttons_clear_{tid}")],
        [InlineKeyboardButton("👁 Preview",callback_data=f"seller_business_template_preview_{tid}")],
        [InlineKeyboardButton("🗑 Delete Template",callback_data=f"seller_business_template_delete_{tid}")],
        [InlineKeyboardButton("⬅ Reply Templates",callback_data="seller_business_templates")],
    ])
    return InlineKeyboardMarkup(rows)


def business_template_text(template:dict) -> str:
    buttons=sum(len(row) for row in (template.get("buttons") or []))
    return (
        "📝 Reply Template\n\n"
        f"Name: {template.get('name') or '-'}\n"
        f"Shortcut: {template.get('shortcut') or '-'}\n"
        f"Text: {'Added' if template.get('text') else 'Not added'}\n"
        f"Media: {'Added' if template.get('media_file_id') else 'Not added'}\n"
        f"URL Buttons: {buttons}"
    )


async def business_automation_text(owner_id:int):
    settings=await get_seller_settings(owner_id)
    connected=await count_business_accounts(owner_id)
    enabled=bool(settings.get("business_automation_enabled"))
    return (
        "💼 Business Automation\n\n"
        f"Status: {'🟢 Enabled' if enabled else '🔴 Disabled'}\n"
        f"Connected Accounts: {connected}\n\n"
        "All connected Telegram accounts use one shared configuration:\n"
        "• Same welcome message and media\n"
        "• Same URL buttons\n"
        "• Same auto replies\n"
        "• Same reply templates\n"
        "• Same settings and statistics"
    ), connected, enabled


def limit_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy / Change Plan", callback_data="seller_upgrade_plan")],
        [InlineKeyboardButton("📊 View Current Plan", callback_data="seller_current_plan")],
        [InlineKeyboardButton("⬅ Back", callback_data="seller_bots_list")],
    ])


def seller_plan_page_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Pending Plan", callback_data="seller_pending_plan")],
        [InlineKeyboardButton("💳 Buy / Change Plan", callback_data="seller_upgrade_plan")],
        [InlineKeyboardButton("⬅ Back", callback_data="main_home")],
    ])



async def send_business_automation(message, owner_id: int):
    """Open the Business Automation home page from the clone-bot deep link."""
    text, connected, enabled = await business_automation_text(owner_id)
    await message.reply_text(
        text,
        reply_markup=business_automation_keyboard(connected, enabled),
    )

async def _clone_scope_for(owner_id: int, bot_id: int) -> int:
    """Resolve a clone-specific data scope and verify seller ownership."""
    record = await get_bot_by_bot_id(int(bot_id))
    if not record or int(record.get("owner_id", 0)) != int(owner_id):
        raise ValueError("Clone bot not found")
    return int(record.get("data_owner_id") or owner_id)


async def clone_list_markup(owner_id: int):
    bots = await get_bots(owner_id)
    rows = []
    for record in bots:
        status = "🟢" if record.get("runtime_status") == "running" else "🔴"
        username = record.get("bot_username") or str(record.get("bot_id"))
        rows.append([InlineKeyboardButton(
            f"{status} @{username}",
            callback_data=f"seller_select_{record['bot_id']}",
        )])
    # Keep this button visible even when the seller has reached the bot limit.
    # Clicking it then opens the plan-limit warning with upgrade options.
    rows.append([InlineKeyboardButton("➕ Create New Clone Bot", callback_data="seller_connect")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="main_home")])
    return InlineKeyboardMarkup(rows)


def selected_bot_markup(record):
    """Main-bot seller dashboard for one selected clone bot."""
    bot_id = int(record["bot_id"])
    active = bool(record.get("active"))
    username = str(record.get("bot_username") or "").lstrip("@")
    open_admin = (
        InlineKeyboardButton("🛠 Open Admin Panel", url=f"https://t.me/{username}?start=admin_panel")
        if username else InlineKeyboardButton("🛠 Open Admin Panel", callback_data=f"seller_open_admin_{bot_id}")
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Seller Profile", callback_data=f"seller_selected_profile_{bot_id}")],
        [open_admin],
        [
            InlineKeyboardButton("⏸ Pause Bot" if active else "▶️ Resume Bot", callback_data=f"seller_{'pause' if active else 'resume'}_{bot_id}"),
            InlineKeyboardButton("🔄 Replace Token", callback_data=f"seller_replace_{bot_id}"),
        ],
        [InlineKeyboardButton("🗑 Remove Bot", callback_data=f"seller_remove_{bot_id}")],
        [InlineKeyboardButton("📊 Statistics", callback_data=f"seller_selected_stats_{bot_id}")],
        [InlineKeyboardButton("🤝 Seller Referral", callback_data=f"seller_selected_referral_{bot_id}")],
        [InlineKeyboardButton("📜 Terms & Policy", callback_data=f"seller_selected_terms_{bot_id}")],
        [InlineKeyboardButton("🆘 Help & Commands", callback_data=f"seller_selected_help_{bot_id}")],
        [InlineKeyboardButton("⬅ Clone Bot List", callback_data="seller_bots_list")],
    ])


def selected_back(bot_id: int):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{int(bot_id)}")]])


def selected_profile_markup(bot_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Buy / Change Plan", callback_data=f"seller_upgrade_plan_selected_{int(bot_id)}")],
        [InlineKeyboardButton("📜 Plan History", callback_data="seller_plan_history")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{int(bot_id)}")],
    ])


def _aware_utc(value):
    if not value:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _limit_display(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return str(value or 0)
    return "Unlimited" if value < 0 else f"{value:,}"


def _money(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.2f}".rstrip("0").rstrip(".")


async def selected_seller_profile_text(owner_id: int, record: dict, user) -> str:
    seller = await get_or_create_seller(user)
    # Seller plan is seller-account scoped; clone statistics are clone-data scoped.
    seller_account_id = int(record.get("seller_account_id") or record.get("owner_id") or owner_id)
    scope_id = int(record.get("data_owner_id") or seller_account_id)
    plan, assignment = await effective_plan(seller_account_id)
    db = get_database()
    now = datetime.now(timezone.utc)

    expiry = _aware_utc((assignment or {}).get("expiry_date"))
    activated = _aware_utc((assignment or {}).get("created_at") or (assignment or {}).get("updated_at"))
    if expiry and expiry > now:
        remaining = expiry - now
        days = remaining.days
        hours = remaining.seconds // 3600
        minutes = (remaining.seconds % 3600) // 60
        remaining_text = f"{days}d {hours}h {minutes}m"
        expiry_text = expiry.strftime("%d-%m-%Y %I:%M %p UTC")
        plan_status = "✅ Active"
    elif str(plan.get("plan_id", "")).lower() == "free" or str(plan.get("name", "")).lower() == "free":
        remaining_text = "No expiry"
        expiry_text = "No expiry"
        plan_status = "🆓 Free Plan"
    else:
        remaining_text = "Expired"
        expiry_text = expiry.strftime("%d-%m-%Y %I:%M %p UTC") if expiry else "-"
        plan_status = "❌ Expired"

    activated_text = activated.strftime("%d-%m-%Y %I:%M %p UTC") if activated else "-"
    joined = _aware_utc((seller or {}).get("created_at"))
    joined_text = joined.strftime("%d-%m-%Y") if joined else "-"

    bots_used = await count_owner_bots(seller_account_id)
    active_subscribers = await db["seller_subscriptions"].count_documents({
        "owner_id": scope_id, "active": True, "expiry_date": {"$gt": now}
    })
    channels_used = await db["seller_channels"].count_documents({"owner_id": scope_id, "active": True})
    plans_used = await db["seller_plans"].count_documents({"owner_id": scope_id})
    total_users = await db["seller_users"].count_documents({"owner_id": scope_id})
    pending = await db["seller_payments"].count_documents({"owner_id": scope_id, "status": "pending"})

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_rows = await db["seller_payments"].aggregate([
        {"$match": {"owner_id": scope_id, "status": "approved", "created_at": {"$gte": today_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(length=1)
    total_rows = await db["seller_payments"].aggregate([
        {"$match": {"owner_id": scope_id, "status": "approved"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(length=1)
    today_revenue = today_rows[0].get("total", 0) if today_rows else 0
    total_revenue = total_rows[0].get("total", 0) if total_rows else 0

    username = f"@{user.username}" if getattr(user, "username", None) else "Not set"
    name = (seller or {}).get("first_name") or getattr(user, "full_name", None) or "Unknown"
    bot_username = record.get("bot_username") or str(record.get("bot_id"))
    runtime = str(record.get("runtime_status") or "stopped").lower()
    if runtime == "invalid_token":
        runtime_text = "🔴 Invalid Token — tap Replace Token"
    elif record.get("runtime_error"):
        runtime_text = "🔴 Error"
    elif runtime == "running":
        runtime_text = "🟢 Running"
    else:
        runtime_text = "🟡 Stopped"
    bot_status = "🟢 Active" if record.get("active") else "🟡 Paused"

    return (
        "👤 Seller Profile\n\n"
        f"🆔 Seller ID: {owner_id}\n"
        f"👤 Name: {name}\n"
        f"📛 Username: {username}\n"
        f"📅 Joined: {joined_text}\n\n"
        "💎 Plan Details\n"
        f"📦 Plan: {plan.get('name', 'Free')}\n"
        f"Status: {plan_status}\n"
        f"📅 Activated: {activated_text}\n"
        f"⏳ Expiry: {expiry_text}\n"
        f"⌛ Remaining: {remaining_text}\n\n"
        "📊 Seller Usage & Limits\n"
        f"🤖 Clone Bots: {bots_used:,} / {_limit_display(plan.get('bot_limit', 1))}\n"
        f"👥 Active Subscribers: {active_subscribers:,} / {_limit_display(plan.get('active_subscriber_limit', 25))}\n"
        f"📢 Channels / Groups: {channels_used:,} / {_limit_display(plan.get('channel_limit', 1))}\n"
        f"📦 Subscription Plans: {plans_used:,} / {_limit_display(plan.get('plan_limit', 2))}\n\n"
        "📈 Seller Statistics\n"
        f"👥 Total Users: {total_users:,}\n"
        f"💳 Pending Payments: {pending:,}\n"
        f"💰 Today Revenue: ₹{_money(today_revenue)}\n"
        f"💰 Total Revenue: ₹{_money(total_revenue)}\n\n"
        "🤖 Selected Clone Bot\n"
        f"Bot: @{bot_username}\n"
        f"Status: {bot_status}\n"
        f"Runtime: {runtime_text}"
    )


def payment_settings_markup(bot_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Automatic Gateways", callback_data="pgcfg_seller_home")],
        [InlineKeyboardButton("🏦 Set UPI ID", callback_data=f"seller_set_upi_id_{bot_id}")],
        [InlineKeyboardButton("👤 Set UPI Name", callback_data=f"seller_set_upi_name_{bot_id}")],
        [InlineKeyboardButton("🖼 Upload QR", callback_data=f"seller_set_qr_{bot_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")],
    ])


def bot_settings_markup(bot_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Bot Name", callback_data=f"seller_set_bot_name_{bot_id}")],
        [InlineKeyboardButton("💬 Welcome Message", callback_data=f"seller_set_welcome_{bot_id}")],
        [InlineKeyboardButton("📞 Support Username", callback_data=f"seller_set_support_{bot_id}")],
        [InlineKeyboardButton("💵 Currency", callback_data=f"seller_set_currency_{bot_id}"), InlineKeyboardButton("🕒 Timezone", callback_data=f"seller_set_timezone_{bot_id}")],
        [InlineKeyboardButton("🔔 Reminder Days", callback_data=f"seller_set_reminder_{bot_id}")],
        [InlineKeyboardButton("🎁 Referral Reward Days", callback_data=f"seller_set_referral_days_{bot_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")],
    ])


def channels_markup(bot_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel/Group", callback_data=f"seller_channel_add_{bot_id}")],
        [InlineKeyboardButton("📋 Channel List", callback_data=f"seller_channel_list_{bot_id}")],
        [InlineKeyboardButton("🔗 Resend Invite Links to Active Subscribers", callback_data=f"seller_channel_resend_{bot_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")],
    ])


async def selected_panel_text(owner_id: int, record, user) -> str:
    """Compact clone-specific dashboard header shown in the main bot."""
    scope_id = int(record.get("data_owner_id") or owner_id)
    plan, _ = await effective_plan(owner_id)
    data = await seller_stats(scope_id)
    seller = f"@{user.username}" if getattr(user, "username", None) else getattr(user, "full_name", str(owner_id))
    username = record.get("bot_username") or record.get("bot_id")
    runtime = str(record.get("runtime_status") or "stopped").lower()
    online = runtime == "running" and bool(record.get("active"))
    status = "🟢 Online" if online else ("⏸ Paused" if not record.get("active") else "🔴 Offline")
    return (
        f"👤 Seller: {seller}\n"
        f"🤖 Clone Bot: @{username}\n"
        f"📦 Plan: {plan.get('name', 'Free')}\n"
        f"{status}\n\n"
        f"Total Users: {int(data.get('total_users', data.get('users', 0))):,}\n"
        f"Active Today: {int(data.get('active_today', data.get('active', 0))):,}\n"
        f"Active Subscribers: {int(data.get('active_subscribers', 0)):,}\n"
        f"Plans: {int(data.get('plans', 0)):,}\n"
        f"Channels/Groups: {int(data.get('channels', 0)):,}\n"
        f"Pending Payments: {int(data.get('pending', 0)):,}\n"
        f"Today Revenue: ₹{_money(data.get('today_revenue', 0))}\n"
        f"Total Revenue: ₹{_money(data.get('total_revenue', data.get('revenue', 0)))}"
    )


def selected_help_keyboard(bot_id: int):
    order = list(HELP_LABELS.items())
    rows = []
    for index in range(0, len(order), 2):
        row = [InlineKeyboardButton(order[index][1], callback_data=f"seller_help_{bot_id}_{order[index][0]}")]
        if index + 1 < len(order):
            row.append(InlineKeyboardButton(order[index + 1][1], callback_data=f"seller_help_{bot_id}_{order[index + 1][0]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")])
    return InlineKeyboardMarkup(rows)


def selected_help_page_keyboard(bot_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Help Center", callback_data=f"seller_selected_help_{bot_id}")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")],
    ])


def _business_mtproto_ready() -> bool:
    return bool(TELEGRAM_API_ID and TELEGRAM_API_HASH)


def _normalize_phone(value: str) -> str:
    phone = "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch == "+")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    if not phone.startswith("+"):
        raise ValueError("Phone number must include the country code, for example +919876543210.")
    if len(phone) < 8 or len(phone) > 16 or not phone[1:].isdigit():
        raise ValueError("Invalid phone number format.")
    return phone


async def _business_send_code(context: ContextTypes.DEFAULT_TYPE, phone: str) -> None:
    client = TelegramClient(StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        context.user_data["business_auth"] = {
            "step": "code",
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "session": client.session.save(),
        }
    finally:
        await client.disconnect()


async def _business_complete_connection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    client: TelegramClient,
) -> None:
    me = await client.get_me()
    if not me:
        raise RuntimeError("Telegram account details could not be loaded.")
    encrypted_session = encrypt_secret(client.session.save())
    await save_business_account_session(
        int(update.effective_user.id),
        int(me.id),
        encrypted_session=encrypted_session,
        phone=str(getattr(me, "phone", "") or ""),
        username=str(getattr(me, "username", "") or ""),
        first_name=str(getattr(me, "first_name", "") or "Telegram Account"),
    )
    context.user_data.pop("business_auth", None)
    username_text = f"@{me.username}" if getattr(me, "username", None) else "Not set"
    await update.effective_message.reply_text(
        "✅ Telegram account connected successfully.\n\n"
        f"Account: {getattr(me, 'first_name', None) or 'Telegram Account'}\n"
        f"Username: {username_text}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💼 Business Automation", callback_data="seller_business")]
        ]),
    )


async def _business_log_out_account(record: dict) -> None:
    encrypted = str(record.get("encrypted_session") or "")
    if not encrypted or not _business_mtproto_ready():
        return
    client = TelegramClient(
        StringSession(decrypt_secret(encrypted)),
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH,
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await client.disconnect()


async def seller_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    owner_id = int(q.from_user.id)
    action = q.data

    # Normalize legacy single-clone dashboard callbacks to the selected clone.
    # New multi-clone callbacks already carry the bot_id suffix.
    if action in {"seller_my_bot", "seller_pause", "seller_resume", "seller_replace", "seller_remove"}:
        selected_id = int(context.user_data.get("selected_clone_bot_id") or 0)
        if not selected_id:
            bots = await get_bots(owner_id)
            if len(bots) == 1:
                selected_id = int(bots[0].get("bot_id") or 0)
        if not selected_id:
            await q.edit_message_text(
                "🤖 Select a clone bot first.",
                reply_markup=await clone_list_markup(owner_id),
            )
            return
        action = f"{action}_{selected_id}"

    # Fallback for Open Admin Panel when a clone username is unavailable.
    # A Telegram deep-link cannot be constructed without a public bot username,
    # so handle this callback explicitly instead of leaving the button dead.
    if action.startswith("seller_open_admin_"):
        try:
            bot_id = int(action.rsplit("_", 1)[1])
        except (TypeError, ValueError):
            await q.answer("Invalid clone bot.", show_alert=True)
            return
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True)
            return
        username = str(record.get("bot_username") or "").lstrip("@")
        if not username:
            await q.answer(
                "This clone bot does not have a Telegram username, so its Admin Panel cannot be opened by deep link yet.",
                show_alert=True,
            )
            return
        await q.answer()
        await q.edit_message_text(
            "🛠 Open Admin Panel\n\nTap the button below to open this clone bot directly.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛠 Open Admin Panel", url=f"https://t.me/{username}?start=admin_panel")
            ], [InlineKeyboardButton("⬅ Back", callback_data=f"seller_select_{bot_id}")]]),
        )
        return

    # Selected clone-bot management pages stay inside the main SaaS bot.
    if action.startswith("seller_selected_help_"):
        bot_id = int(action.rsplit("_", 1)[1])
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True); return
        await q.edit_message_text(
            "🆘 Help & Commands\n\n"
            "Welcome to the Clone Bot Help Center.\n\n"
            "Select a section to learn what each feature does, where to find it and how to configure it.",
            reply_markup=selected_help_keyboard(bot_id),
        )
        return

    if action.startswith("seller_help_"):
        raw = action.replace("seller_help_", "", 1)
        bot_raw, key = raw.split("_", 1)
        bot_id = int(bot_raw)
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id or key not in HELP_PAGES:
            await q.answer("Help page not found.", show_alert=True); return
        await q.edit_message_text(HELP_PAGES[key], reply_markup=selected_help_page_keyboard(bot_id))
        return

    if action.startswith("seller_selected_profile_"):
        bot_id = int(action.rsplit("_", 1)[1])
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True)
            return
        await q.edit_message_text(
            await selected_seller_profile_text(owner_id, record, q.from_user),
            reply_markup=selected_profile_markup(bot_id),
        )
        return

    if action.startswith("seller_selected_payment_"):
        bot_id = int(action.rsplit("_", 1)[1]); scope_id = await _clone_scope_for(owner_id, bot_id); settings = await get_seller_settings(scope_id)
        await q.edit_message_text(
            f"💳 Payment Settings\n\nUPI Name: {settings.get('upi_name') or 'Not Set'}\n"
            f"UPI ID: {settings.get('upi_id') or 'Not Set'}\nQR: {'Added' if settings.get('upi_qr_file_id') else 'Not Added'}",
            reply_markup=payment_settings_markup(bot_id),
        ); return

    if action.startswith("seller_selected_settings_"):
        bot_id = int(action.rsplit("_", 1)[1]); scope_id = await _clone_scope_for(owner_id, bot_id); settings = await get_seller_settings(scope_id)
        await q.edit_message_text(
            "⚙️ Bot Settings\n\n"
            f"Bot Name: {settings.get('bot_name') or '-'}\nSupport: {settings.get('support_username') or '-'}\n"
            f"Currency: {settings.get('currency') or 'INR'}\nTimezone: {settings.get('timezone') or 'Asia/Kolkata'}",
            reply_markup=bot_settings_markup(bot_id),
        ); return

    if action.startswith("seller_selected_stats_"):
        bot_id = int(action.rsplit("_", 1)[1]); record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True); return
        scope_id = int(record.get("data_owner_id") or owner_id)
        data = await seller_stats(scope_id)
        plan, _ = await effective_plan(owner_id)
        usage = await seller_usage(owner_id)
        await q.edit_message_text(
            "📊 Statistics\n\n"
            f"👥 Total Users: {data.get('total_users', data.get('users',0))}\n"
            f"📅 Active Today: {data.get('active_today', data.get('active',0))}\n"
            f"👤 Active Subscribers: {data.get('active_subscribers',0)}\n"
            f"📦 Plans: {data.get('plans',0)}\n"
            f"📢 Channels/Groups: {data.get('channels',0)}\n"
            f"💳 Pending Payments: {data.get('pending',0)}\n"
            f"💰 Today Revenue: ₹{_money(data.get('today_revenue',0))}\n"
            f"💰 Total Revenue: ₹{_money(data.get('total_revenue', data.get('revenue',0)))}\n\n"
            "📦 Plan & Limitations\n"
            f"Plan: {plan.get('name','Free')}\n"
            f"Clone Bots: {usage.get('bot_count',0)} / {_limit_display(plan.get('bot_limit',1))}\n"
            f"Active Subscribers Limit: {_limit_display(plan.get('active_subscriber_limit', plan.get('subscriber_limit',25)))}\n"
            f"Channels/Groups Limit: {_limit_display(plan.get('channel_limit',1))}\n"
            f"Plans Limit: {_limit_display(plan.get('plan_limit',2))}",
            reply_markup=selected_back(bot_id),
        ); return

    if action.startswith("seller_selected_channels_"):
        bot_id = int(action.rsplit("_", 1)[1])
        await q.edit_message_text("📢 Channels / Groups", reply_markup=channels_markup(bot_id)); return

    if action.startswith("seller_selected_referral_"):
        bot_id = int(action.rsplit("_", 1)[1]); data = await seller_referral_stats(owner_id)
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=ref_{owner_id}"
        await q.edit_message_text(
            f"🤝 Seller Referral\n\nReferral Link:\n{link}\n\nTotal Referrals: {data.get('total',0)}\nRewarded: {data.get('rewarded',0)}",
            reply_markup=selected_back(bot_id), disable_web_page_preview=True,
        ); return

    if action.startswith("seller_selected_terms_"):
        bot_id = int(action.rsplit("_", 1)[1]); policy = await get_policy(owner_id)
        parts=[]
        for key in ("terms","privacy","refund","support"):
            value=(policy or {}).get(key)
            if value: parts.append(f"{key.title()}:\n{value}")
        await q.edit_message_text(
            "📜 Terms & Policy\n\n" + ("\n\n".join(parts) if parts else "No policy configured."),
            reply_markup=selected_back(bot_id),
        ); return

    if action.startswith("seller_tz_"):
        parts = action.split("_")
        if len(parts) < 4:
            await q.answer("Invalid timezone selection.", show_alert=True)
            return
        bot_id = int(parts[2])
        key = "_".join(parts[3:])
        if key == "manual":
            context.user_data.clear()
            context.user_data.update({"seller_edit_field": "timezone", "selected_clone_bot_id": bot_id})
            await q.edit_message_text(
                timezone_guide((await get_seller_settings(await _clone_scope_for(owner_id, bot_id))).get("timezone") or "Asia/Kolkata")
                + "\n\nSend the timezone name now.",
                reply_markup=selected_back(bot_id),
            )
            return
        timezone_name = timezone_from_key(key)
        if not timezone_name:
            await q.answer("Invalid timezone selection.", show_alert=True)
            return
        await set_seller_setting(await _clone_scope_for(owner_id, bot_id), "timezone", timezone_name)
        context.user_data.clear()
        await q.edit_message_text(
            f"✅ Timezone updated!\n\nTimezone: {timezone_name}",
            reply_markup=bot_settings_markup(bot_id),
        )
        return

    # Payment and bot-setting edit actions.
    setting_actions = {
        "seller_set_upi_id_": ("upi_id", "Send the UPI ID."),
        "seller_set_upi_name_": ("upi_name", "Send the UPI account/name."),
        "seller_set_bot_name_": ("bot_name", "Send the bot display name."),
        "seller_set_support_": ("support_username", "Send support @username or Telegram link."),
        "seller_set_currency_": ("currency", "Send currency code, for example INR."),
        "seller_set_timezone_": ("timezone", "__TIMEZONE_PICKER__"),
        "seller_set_welcome_": ("welcome_message", "Send the new welcome message text."),
        "seller_set_reminder_": ("reminder_days", "Send reminder days, for example 1."),
        "seller_set_referral_days_": ("referral_reward_days", "Send referral reward days, for example 7."),
    }
    for prefix, (field, prompt) in setting_actions.items():
        if action.startswith(prefix):
            bot_id = int(action.rsplit("_", 1)[1])
            context.user_data.clear(); context.user_data.update({"seller_edit_field": field, "selected_clone_bot_id": bot_id})
            if field == "timezone":
                settings = await get_seller_settings(await _clone_scope_for(owner_id, bot_id))
                await q.edit_message_text(
                    timezone_guide(settings.get("timezone") or "Asia/Kolkata"),
                    reply_markup=timezone_keyboard(f"seller_tz_{bot_id}_", f"seller_selected_settings_{bot_id}"),
                )
            else:
                await q.edit_message_text(prompt, reply_markup=selected_back(bot_id))
            return

    if action.startswith("seller_set_qr_"):
        bot_id=int(action.rsplit("_",1)[1]); context.user_data.clear(); context.user_data.update({"seller_waiting_qr":True,"selected_clone_bot_id":bot_id})
        await q.edit_message_text("🖼 Send the UPI QR image now.", reply_markup=selected_back(bot_id)); return

    if action.startswith("seller_channel_add_"):
        bot_id=int(action.rsplit("_",1)[1]); context.user_data.clear(); context.user_data.update({"seller_waiting_channel":True,"selected_clone_bot_id":bot_id})
        await q.edit_message_text("Send channel/group in this format:\n-1001234567890 | Group Name", reply_markup=selected_back(bot_id)); return

    if action.startswith("seller_channel_list_"):
        bot_id=int(action.rsplit("_",1)[1]); scope_id=await _clone_scope_for(owner_id, bot_id); items=await get_channels(scope_id); lines=["📋 Channel / Group List",""]
        rows=[]
        if not items: lines.append("No channel or group connected.")
        for item in items:
            lines.append(f"• {item.get('title','Chat')} ({item.get('chat_id')})")
            rows.append([InlineKeyboardButton(f"🗑 Remove {str(item.get('title','Chat'))[:24]}", callback_data=f"seller_channel_remove_{bot_id}_{item.get('chat_id')}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"seller_selected_channels_{bot_id}")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows)); return

    if action.startswith("seller_channel_remove_"):
        parts = action.split("_")
        bot_id = int(parts[3])
        chat_id = int(parts[4])
        scope_id = await _clone_scope_for(owner_id, bot_id)
        async with _channel_lock(scope_id):
            removed = await remove_channel(scope_id, chat_id)
        await q.edit_message_text(
            "✅ Channel/group removed." if removed else "ℹ️ Channel/group was already removed.",
            reply_markup=channels_markup(bot_id),
        )
        return

    if action.startswith("seller_channel_resend_"):
        bot_id = int(action.rsplit("_", 1)[1])
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True)
            return

        await q.edit_message_text("⏳ Preparing invite links...")

        async def _run_resend():
            scope_id = int(record.get("data_owner_id") or owner_id)
            async with _channel_lock(scope_id):
                running = bot_manager.get_running(bot_id)
                if not running:
                    return {"error": "not_running"}

                channels = tuple(await get_channels(scope_id))
                if not channels:
                    return {"error": "no_channels"}

                now = datetime.now(timezone.utc)
                subs = await get_database()["seller_subscriptions"].find(
                    {
                        "owner_id": scope_id,
                        "active": True,
                        "expiry_date": {"$gt": now},
                    },
                    {"user_id": 1},
                ).to_list(length=5000)

                sent = failed = skipped = reused = created = 0
                for sub in subs:
                    uid = int(sub.get("user_id") or 0)
                    if not uid:
                        skipped += 1
                        continue

                    links = []
                    for channel in channels:
                        chat_id = int(channel["chat_id"])
                        try:
                            invite_doc = await get_active_invite(owner_id, uid, chat_id)
                            invite_link = (invite_doc or {}).get("invite_link")

                            if invite_link:
                                reused += 1
                            else:
                                invite = await running.application.bot.create_chat_invite_link(
                                    chat_id,
                                    member_limit=1,
                                )
                                invite_link = invite.invite_link
                                await save_invite(owner_id, uid, chat_id, invite_link)
                                created += 1

                            links.append(
                                f"{channel.get('title', 'Channel/Group')}: {invite_link}"
                            )
                        except TelegramError as exc:
                            logger.warning(
                                "Invite creation failed owner_id=%s bot_id=%s "
                                "user_id=%s chat_id=%s error=%s",
                                owner_id,
                                bot_id,
                                uid,
                                chat_id,
                                exc,
                            )
                        except Exception:
                            logger.exception(
                                "Unexpected invite creation failure owner_id=%s "
                                "bot_id=%s user_id=%s chat_id=%s",
                                owner_id,
                                bot_id,
                                uid,
                                chat_id,
                            )

                    if not links:
                        failed += 1
                        continue

                    try:
                        await running.application.bot.send_message(
                            uid,
                            "🔗 Your invite links:\n\n" + "\n".join(links),
                            disable_web_page_preview=True,
                        )
                        sent += 1
                    except TelegramError as exc:
                        failed += 1
                        logger.warning(
                            "Invite resend delivery failed owner_id=%s bot_id=%s "
                            "user_id=%s error=%s",
                            owner_id,
                            bot_id,
                            uid,
                            exc,
                        )
                    except Exception:
                        failed += 1
                        logger.exception(
                            "Unexpected invite delivery failure owner_id=%s "
                            "bot_id=%s user_id=%s",
                            owner_id,
                            bot_id,
                            uid,
                        )

                    await asyncio.sleep(0.04)

                return {
                    "sent": sent,
                    "failed": failed,
                    "skipped": skipped,
                    "reused": reused,
                    "created": created,
                }

        started, result = await resend_invites_safely(
            owner_id,
            bot_id,
            _run_resend,
        )
        if not started:
            await q.edit_message_text(
                "⏳ Invite resend is already running. Please wait.",
                reply_markup=channels_markup(bot_id),
            )
            return

        result = result or {}
        if result.get("error") == "not_running":
            await q.edit_message_text(
                "❌ Clone bot is not running. Resume it first.",
                reply_markup=channels_markup(bot_id),
            )
            return
        if result.get("error") == "no_channels":
            await q.edit_message_text(
                "❌ No channel/group connected.",
                reply_markup=channels_markup(bot_id),
            )
            return

        await q.edit_message_text(
            "✅ Invite link resend completed.\n\n"
            f"Sent: {result.get('sent', 0)}\n"
            f"Failed: {result.get('failed', 0)}\n"
            f"Skipped: {result.get('skipped', 0)}\n"
            f"Reused links: {result.get('reused', 0)}\n"
            f"New links: {result.get('created', 0)}",
            reply_markup=channels_markup(bot_id),
        )
        return

    if action == "seller_bots_list":
        bots = await get_bots(owner_id)
        plan, _ = await effective_plan(owner_id)
        limit = int(plan.get("bot_limit", 1))
        limit_text = "Unlimited" if limit < 0 else str(limit)
        await q.edit_message_text(
            f"🤖 My Clone Bots — {len(bots)}/{limit_text}\n\nSelect a clone bot to manage.",
            reply_markup=await clone_list_markup(owner_id),
        )
        return

    if action.startswith("seller_select_"):
        bot_id = int(action.rsplit("_", 1)[1])
        record = await get_bot_by_bot_id(bot_id)
        if not record or int(record.get("owner_id", 0)) != owner_id:
            await q.answer("Clone bot not found.", show_alert=True)
            return
        context.user_data["selected_clone_bot_id"] = bot_id
        await q.edit_message_text(
            await selected_panel_text(owner_id, record, q.from_user),
            reply_markup=selected_bot_markup(record),
        )
        return

    if action.startswith("seller_plan_decide_"):
        raw = action.replace("seller_plan_decide_", "", 1)
        decision = next((d for d in ("replace_now", "after_expiry", "keep_pending") if raw.startswith(d + "_")), None)
        if not decision:
            await q.answer("Invalid action", show_alert=True)
            return
        payment_id = raw[len(decision) + 1:]
        purchase, changed = await choose_verified_plan_purchase(payment_id, owner_id, decision)
        if not purchase:
            await q.answer("Plan purchase not found", show_alert=True)
            return
        if not changed:
            await q.answer("This plan choice was already processed.", show_alert=True)
        await q.edit_message_text(_decision_result_text(purchase))
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    "📢 Seller Plan Decision\n\n"
                    f"Seller ID: {owner_id}\n"
                    f"Plan: {purchase.get('plan_name')}\n"
                    f"Selected: {str(purchase.get('decision')).replace('_',' ').title()}\n"
                    f"Payment ID: {purchase.get('payment_id')}",
                )
            except Exception:
                pass
        return

    if action == "seller_pending_plan":
        purchase = await pending_plan_purchase(owner_id)
        if not purchase:
            await q.edit_message_text("📦 Pending Plan\n\nNo pending or scheduled plan is available.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="seller_current_plan")]]))
            return
        text = (
            "📦 Pending Plan\n\n"
            f"Plan: {purchase.get('plan_name')}\n"
            f"Purchase Date: {_fmt_dt(purchase.get('created_at'))}\n"
            f"Payment Amount: ₹{float(purchase.get('amount',0)):g}\n"
            f"Duration: {purchase.get('duration_days')} Days\n"
            f"Scheduled Activation: {_fmt_dt(purchase.get('activation_date'))}\n"
            f"Payment Method: {str(purchase.get('source') or 'Payment').replace('gateway:','').title()}\n"
            f"Transaction ID: {purchase.get('verified_reference') or purchase.get('payment_id')}\n"
            f"Status: {str(purchase.get('status')).replace('_',' ').title()}"
        )
        rows=[]
        if purchase.get("status") in {"decision_required", "pending", "scheduled"}:
            rows.append([InlineKeyboardButton("⚡ Activate Now", callback_data=f"seller_plan_decide_replace_now_{purchase['payment_id']}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="seller_current_plan")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "seller_current_plan":
        await q.edit_message_text(await current_plan_text(owner_id), reply_markup=seller_plan_page_keyboard())
        return

    if action.startswith("seller_upgrade_plan"):
        cfg = await get_config()
        plans = [p for p in cfg.get("paid_plans", []) if p.get("active", True)]
        rows = []
        lines = ["💎 Buy / Change Seller Plan", ""]
        current, _ = await effective_plan(owner_id)
        for p in plans:
            lines.append(f"• {p.get('name','Plan')} — ₹{p.get('price',0):g} / {p.get('duration_days',30)} days")
            typ = "upgrade" if float(p.get("price", 0)) >= float(current.get("price", 0)) else "downgrade"
            rows.append([InlineKeyboardButton(f"Select {p.get('name')}", callback_data=f"seller_buy_{typ}_{p.get('plan_id')}")])
        if action == "seller_upgrade_plan_profile":
            back_target = "main_seller_profile"
        elif action.startswith("seller_upgrade_plan_selected_"):
            try:
                selected_bot_id = int(action.rsplit("_", 1)[1])
                back_target = f"seller_selected_profile_{selected_bot_id}"
            except (TypeError, ValueError):
                back_target = "seller_bots_list"
        else:
            back_target = "main_home"
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_target)])
        markup = InlineKeyboardMarkup(rows)
        text = "\n".join(lines)
        # A seller payment screen may be a photo (QR code). Telegram cannot use
        # edit_message_text on photo messages, so replace it with a normal text message.
        if q.message and (q.message.photo or q.message.document):
            try:
                await q.message.delete()
            except TelegramError:
                pass
            await context.bot.send_message(chat_id=q.message.chat_id, text=text, reply_markup=markup)
        else:
            await q.edit_message_text(text, reply_markup=markup)
        return

    if action.startswith("seller_buy_"):
        _, _, request_type, plan_id = action.split("_", 3)
        cfg = await get_config()
        plan = next((p for p in cfg.get("paid_plans", []) if p.get("plan_id") == plan_id), None)
        if not plan:
            await q.answer("Plan unavailable", show_alert=True)
            return
        await create_plan_request(owner_id, plan_id, request_type)
        context.user_data.clear()
        context.user_data["seller_payment_plan"] = plan_id
        context.user_data["seller_request_type"] = request_type
        gateway_cfg = await get_gateway_config("owner", 0, decrypt=True)
        gateways = gateway_cfg.get("gateways") or {}
        enabled_gateways = [g for g in SUPPORTED_GATEWAYS if bool((gateways.get(g) or {}).get("enabled"))]
        default_gateway = str(gateway_cfg.get("default_gateway") or "")
        if default_gateway in enabled_gateways:
            enabled_gateways.remove(default_gateway)
            enabled_gateways.insert(0, default_gateway)
        manual_enabled = bool(gateway_cfg.get("manual_enabled", True))

        rows = []
        text = ""
        if enabled_gateways:
            gateway = enabled_gateways[0]
            tx = await create_gateway_transaction(
                scope="owner", owner_id=0, payer_user_id=owner_id,
                gateway=gateway, amount=float(plan.get("price", 0)), currency="INR",
                purpose="seller_plan", reference_id=plan_id,
                metadata={"plan_id": plan_id, "request_type": request_type, "description": f"Seller {plan.get('name')} plan"},
            )
            try:
                checkout = await create_checkout(tx)
                text = (
                    f"💳 {gateway.title()} Payment\n\n"
                    f"Plan: {plan.get('name')}\nAmount: ₹{plan.get('price',0):g}\n"
                    f"Transaction: {tx['transaction_id']}\n\n"
                    "Payment successful hone ke baad plan automatically activate hoga."
                )
                rows.append([InlineKeyboardButton("💳 Pay Now", url=checkout.get("checkout_url"))])
            except GatewayError as exc:
                text = f"❌ Gateway error: {exc}"

        if manual_enabled:
            manual_text = (
                f"Plan: {plan.get('name')}\nAmount: ₹{plan.get('price',0):g}\n"
                f"UPI Name: {cfg.get('payment_upi_name') or 'Not Set'}\n"
                f"UPI ID: {cfg.get('payment_upi_id') or 'Not Set'}\n\n"
                "Pay and upload your payment screenshot."
            )
            text = f"{text}\n\n{manual_text}" if text else f"💳 Payment\n\n{manual_text}"
            rows.append([InlineKeyboardButton("📤 Upload Payment Screenshot", callback_data=f"seller_manual_{request_type}_{plan_id}")])

        if not enabled_gateways and not manual_enabled:
            text = "⚠️ No payment method is currently available. Please contact support."
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="seller_upgrade_plan")])
        kb = InlineKeyboardMarkup(rows)

        if cfg.get("payment_qr_file_id") and manual_enabled:
            try:
                await q.message.delete()
            except TelegramError:
                pass
            await context.bot.send_photo(q.message.chat_id, cfg["payment_qr_file_id"], caption=text, reply_markup=kb)
        else:
            await q.edit_message_text(text, reply_markup=kb)
        return

    if action.startswith("seller_manual_"):
        _, _, request_type, plan_id = action.split("_", 3)
        context.user_data.clear()
        context.user_data["seller_payment_plan"] = plan_id
        context.user_data["seller_request_type"] = request_type
        await q.message.reply_text("📷 Please upload your payment screenshot now.")
        return

    if action == "seller_plan_history":
        items = await subscription_history(owner_id, 15)
        lines = ["📜 Your Plan History", ""]
        if not items:
            lines.append("No plan history is available yet.")
        else:
            for item in items:
                created_at = item.get("created_at")
                date_text = created_at.strftime("%d-%m-%Y") if hasattr(created_at, "strftime") else "-"
                lines.append(
                    f"• {str(item.get('action') or 'Updated').replace('_',' ').title()}\n"
                    f"  Plan: {item.get('new_plan') or item.get('target_plan_id') or item.get('plan_name') or '-'}\n"
                    f"  Date: {date_text}"
                )
        await q.edit_message_text("\n\n".join(lines), reply_markup=seller_plan_page_keyboard())
        return

    if action == "seller_business":
        text,connected,enabled=await business_automation_text(owner_id)
        await q.edit_message_text(text,reply_markup=business_automation_keyboard(connected,enabled))
        return

    if action == "seller_business_accounts":
        accounts=await get_business_accounts(owner_id)
        lines=["📱 Connected Telegram Accounts",""]
        if not accounts:
            lines.append("No Telegram account is connected yet.")
        else:
            for index,item in enumerate(accounts,1):
                name=item.get("first_name") or "Telegram Account"
                username=f"@{item.get('username')}" if item.get("username") else "No username"
                phone=item.get("phone") or "Hidden"
                lines.append(
                    f"{index}. {name} — {username}\n"
                    f"   Phone: {phone}\n"
                    f"   Status: {item.get('connection_status','connected').title()}"
                )
        await q.edit_message_text("\n\n".join(lines),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")]]))
        return

    if action == "seller_business_connect":
        if not _business_mtproto_ready():
            await q.edit_message_text(
                "⚠️ Telegram Account Connection Is Not Configured\n\n"
                "Add TELEGRAM_API_ID and TELEGRAM_API_HASH to the Render environment, "
                "then redeploy the service. These credentials are created at Telegram's "
                "official developer portal and are shared by the SaaS platform; sellers "
                "do not need to create their own API credentials.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")
                ]]),
            )
            return
        context.user_data.clear()
        context.user_data["business_auth"]={"step":"phone"}
        await q.edit_message_text(
            "🔗 Connect Telegram Account\n\n"
            "Send the Telegram phone number with country code.\n\n"
            "Example: +919876543210\n\n"
            "Telegram will send a login code to that account. The authorized session is encrypted before it is stored.\n\n"
            "Send /cancel to stop this connection process.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_connect_cancel")]]),
        )
        return

    if action == "seller_business_connect_cancel":
        context.user_data.pop("business_auth",None)
        text,connected,enabled=await business_automation_text(owner_id)
        await q.edit_message_text(text,reply_markup=business_automation_keyboard(connected,enabled))
        return

    if action == "seller_business_welcome":
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
        return

    if action == "seller_business_welcome_toggle":
        settings=await get_seller_settings(owner_id)
        await set_seller_setting(owner_id,"business_welcome_enabled",not bool(settings.get("business_welcome_enabled",True)))
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
        return

    if action == "seller_business_welcome_text":
        context.user_data["business_editor"]={"field":"welcome_text"}
        await q.edit_message_text(
            "✏️ Send the new Business Automation welcome text.\n\nSend /cancel to stop.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_welcome")]]),
        )
        return

    if action == "seller_business_welcome_media":
        context.user_data["business_editor"]={"field":"welcome_media"}
        await q.edit_message_text(
            "🖼 Send one photo or video for the Business Automation welcome message.\n\nSend /cancel to stop.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_welcome")]]),
        )
        return

    if action == "seller_business_welcome_media_remove":
        await set_seller_setting(owner_id,"business_welcome_media_type","")
        await set_seller_setting(owner_id,"business_welcome_media_file_id","")
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
        return

    if action == "seller_business_welcome_button_add":
        context.user_data["business_editor"]={"field":"welcome_button"}
        await q.edit_message_text(
            "➕ Send the URL button in this format:\n\nButton Name | https://example.com\n\nSend /cancel to stop.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_welcome")]]),
        )
        return

    if action == "seller_business_welcome_buttons_clear":
        await set_seller_setting(owner_id,"business_welcome_buttons",[])
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
        return

    if action == "seller_business_welcome_preview":
        settings=await get_seller_settings(owner_id)
        preview_text=settings.get("business_welcome_message") or "👋 Welcome!"
        raw_buttons=settings.get("business_welcome_buttons") or []
        rows=[]
        for row in raw_buttons:
            buttons=[]
            for item in row:
                if isinstance(item,dict) and item.get("text") and item.get("url"):
                    buttons.append(InlineKeyboardButton(str(item["text"]),url=str(item["url"])))
            if buttons:
                rows.append(buttons)
        markup=InlineKeyboardMarkup(rows) if rows else None
        media_type=settings.get("business_welcome_media_type")
        file_id=settings.get("business_welcome_media_file_id")
        if media_type=="photo" and file_id:
            await q.message.reply_photo(file_id,caption=preview_text,reply_markup=markup)
        elif media_type=="video" and file_id:
            await q.message.reply_video(file_id,caption=preview_text,reply_markup=markup)
        else:
            await q.message.reply_text(preview_text,reply_markup=markup)
        await q.answer("Preview sent.")
        return

    if action == "seller_business_replies":
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(
            "💬 Auto Reply & Reply Templates\n\n"
            "Configure one shared auto reply and reusable seller shortcuts for every connected Telegram account.",
            reply_markup=business_replies_keyboard(settings),
        )
        return

    if action == "seller_business_auto_reply":
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
        return

    if action == "seller_business_auto_reply_toggle":
        settings=await get_seller_settings(owner_id)
        await set_seller_setting(owner_id,"business_auto_reply_enabled",not bool(settings.get("business_auto_reply_enabled",True)))
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
        return

    if action == "seller_business_auto_reply_text":
        context.user_data["business_editor"]={"field":"auto_reply_text"}
        await q.edit_message_text("✏️ Send the auto reply text.\n\nSend /cancel to stop.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_auto_reply")]]))
        return

    if action == "seller_business_auto_reply_media":
        context.user_data["business_editor"]={"field":"auto_reply_media"}
        await q.edit_message_text("🖼 Send one photo or video for the auto reply.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_auto_reply")]]))
        return

    if action == "seller_business_auto_reply_media_remove":
        await set_seller_setting(owner_id,"business_auto_reply_media_type","")
        await set_seller_setting(owner_id,"business_auto_reply_media_file_id","")
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
        return

    if action == "seller_business_auto_reply_button_add":
        context.user_data["business_editor"]={"field":"auto_reply_button"}
        await q.edit_message_text("➕ Send the URL button in this format:\nButton Name | https://example.com",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_auto_reply")]]))
        return

    if action == "seller_business_auto_reply_buttons_clear":
        await set_seller_setting(owner_id,"business_auto_reply_buttons",[])
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
        return

    if action == "seller_business_auto_reply_delay":
        context.user_data["business_editor"]={"field":"auto_reply_delay"}
        await q.edit_message_text("⏱ Send reply delay in seconds (0-300).",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_auto_reply")]]))
        return

    if action == "seller_business_auto_reply_preview":
        settings=await get_seller_settings(owner_id)
        preview_text=settings.get("business_auto_reply_message") or "Hello! Thanks for your message."
        raw_buttons=settings.get("business_auto_reply_buttons") or []
        rows=[[InlineKeyboardButton(str(btn.get("text") or "Open"),url=str(btn.get("url"))) for btn in row if btn.get("url")] for row in raw_buttons]
        markup=InlineKeyboardMarkup([row for row in rows if row]) if rows else None
        media_type=settings.get("business_auto_reply_media_type")
        file_id=settings.get("business_auto_reply_media_file_id")
        if media_type=="photo" and file_id:
            await q.message.reply_photo(file_id,caption=preview_text,reply_markup=markup)
        elif media_type=="video" and file_id:
            await q.message.reply_video(file_id,caption=preview_text,reply_markup=markup)
        else:
            await q.message.reply_text(preview_text,reply_markup=markup)
        await q.answer("Preview sent.")
        return

    if action == "seller_business_templates_toggle":
        settings=await get_seller_settings(owner_id)
        await set_seller_setting(owner_id,"business_templates_enabled",not bool(settings.get("business_templates_enabled",True)))
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text("💬 Auto Reply & Reply Templates",reply_markup=business_replies_keyboard(settings))
        return

    if action == "seller_business_templates":
        settings=await get_seller_settings(owner_id)
        templates=_business_templates(settings)
        await q.edit_message_text(f"📝 Reply Templates\n\nTemplates: {len(templates)}\n\nSend a shortcut from a connected account to replace it with the saved template.",reply_markup=business_templates_keyboard(settings))
        return

    if action == "seller_business_template_add":
        context.user_data["business_editor"]={"field":"template_add"}
        await q.edit_message_text("➕ Send template details in this format:\nShortcut | Template Name\n\nExample: /price | Price Details",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_templates")]]))
        return

    if action.startswith("seller_business_template_"):
        settings=await get_seller_settings(owner_id)
        prefixes=("meta_","text_","media_remove_","media_","button_","buttons_clear_","preview_","delete_")
        suffix=action[len("seller_business_template_"):]
        operation="open"
        tid=suffix
        for prefix in prefixes:
            if suffix.startswith(prefix):
                operation=prefix[:-1]
                tid=suffix[len(prefix):]
                break
        template=_business_find_template(settings,tid)
        if not template:
            await q.answer("Template not found.",show_alert=True)
            return
        if operation=="open":
            await q.edit_message_text(business_template_text(template),reply_markup=business_template_keyboard(template))
            return
        if operation in {"meta","text","media","button"}:
            context.user_data["business_editor"]={"field":f"template_{operation}","template_id":tid}
            prompts={
                "meta":"Send: Shortcut | Template Name",
                "text":"Send the new template text.",
                "media":"Send one photo or video.",
                "button":"Send: Button Name | https://example.com",
            }
            await q.edit_message_text(prompts[operation],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data=f"seller_business_template_{tid}")]]))
            return
        templates=_business_templates(settings)
        if operation=="media_remove":
            template["media_type"]=""; template["media_file_id"]=""
        elif operation=="buttons_clear":
            template["buttons"]=[]
        elif operation=="delete":
            templates=[item for item in templates if str(item.get("id"))!=str(tid)]
            await set_seller_setting(owner_id,"business_reply_templates",templates)
            settings=await get_seller_settings(owner_id)
            await q.edit_message_text("✅ Reply template deleted.",reply_markup=business_templates_keyboard(settings))
            return
        elif operation=="preview":
            preview_text=template.get("text") or template.get("name") or "Reply template"
            rows=[[InlineKeyboardButton(str(btn.get("text") or "Open"),url=str(btn.get("url"))) for btn in row if btn.get("url")] for row in (template.get("buttons") or [])]
            markup=InlineKeyboardMarkup([row for row in rows if row]) if rows else None
            if template.get("media_type")=="photo" and template.get("media_file_id"):
                await q.message.reply_photo(template["media_file_id"],caption=preview_text,reply_markup=markup)
            elif template.get("media_type")=="video" and template.get("media_file_id"):
                await q.message.reply_video(template["media_file_id"],caption=preview_text,reply_markup=markup)
            else:
                await q.message.reply_text(preview_text,reply_markup=markup)
            await q.answer("Preview sent.")
            return
        await set_seller_setting(owner_id,"business_reply_templates",templates)
        settings=await get_seller_settings(owner_id)
        template=_business_find_template(settings,tid)
        await q.edit_message_text(business_template_text(template),reply_markup=business_template_keyboard(template))
        return

    if action == "seller_business_settings":
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_settings_text(settings),reply_markup=business_settings_keyboard(settings))
        return

    if action in {"seller_business_toggle","seller_business_once","seller_business_ignore_outgoing","seller_business_anti_loop","seller_business_flood","seller_business_working_toggle"}:
        settings=await get_seller_settings(owner_id)
        key_map={
            "seller_business_toggle":("business_automation_enabled",False),
            "seller_business_once":("business_welcome_once",True),
            "seller_business_ignore_outgoing":("business_ignore_outgoing",True),
            "seller_business_anti_loop":("business_anti_loop",True),
            "seller_business_flood":("business_flood_protection",True),
            "seller_business_working_toggle":("business_working_hours_enabled",False),
        }
        key,default=key_map[action]
        await set_seller_setting(owner_id,key,not bool(settings.get(key,default)))
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_settings_text(settings),reply_markup=business_settings_keyboard(settings))
        return

    if action == "seller_business_working_hours":
        context.user_data["business_editor"]={"field":"working_hours"}
        await q.edit_message_text(
            "🕒 Send working hours in this format:\nHH:MM | HH:MM | Timezone\n\nExample: 09:00 | 21:00 | Asia/Kolkata",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_settings")]]),
        )
        return

    if action == "seller_business_settings_delay":
        context.user_data["business_editor"]={"field":"settings_delay"}
        await q.edit_message_text(
            "⏱ Send reply delay in seconds (0-300).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="seller_business_settings")]]),
        )
        return

    if action == "seller_business_action_mode":
        settings=await get_seller_settings(owner_id)
        mode=str(settings.get("business_action_button_mode") or "clone_bot")
        await q.edit_message_text(
            "🔘 Action Button Mode\n\nChoose what Plans, Renew, Profile and Referral buttons should do.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(("✅ " if mode=="clone_bot" else "")+"Open Clone Bot",callback_data="seller_business_action_clone_bot")],
                [InlineKeyboardButton(("✅ " if mode=="account_chat" else "")+"Stay in Account Chat",callback_data="seller_business_action_account_chat")],
                [InlineKeyboardButton("⬅ Settings",callback_data="seller_business_settings")],
            ]),
        )
        return

    if action in {"seller_business_action_clone_bot","seller_business_action_account_chat"}:
        mode="clone_bot" if action.endswith("clone_bot") else "account_chat"
        await set_seller_setting(owner_id,"business_action_button_mode",mode)
        settings=await get_seller_settings(owner_id)
        await q.edit_message_text(business_settings_text(settings),reply_markup=business_settings_keyboard(settings))
        return

    if action == "seller_business_statistics":
        stats=await business_automation_stats(owner_id)
        await q.edit_message_text(
            "📊 Business Automation Statistics\n\n"
            f"Connected Accounts: {int(stats.get('accounts',0))}\n"
            f"Conversations: {int(stats.get('conversations',0))}\n"
            f"Welcome Messages Sent: {int(stats.get('welcome_sent',0))}\n"
            f"Auto Replies Sent: {int(stats.get('auto_replies_sent',0))}\n"
            f"Reply Templates Used: {int(stats.get('templates_used',0))}\n\n"
            f"Plans Opened: {int(stats.get('plans_opened',0))}\n"
            f"Renew Opened: {int(stats.get('renew_opened',0))}\n"
            f"Profile Opened: {int(stats.get('profile_opened',0))}\n"
            f"Referral Opened: {int(stats.get('referral_opened',0))}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="seller_business_statistics")],[InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")]]),
        )
        return

    if action.startswith("seller_business_disconnect_"):
        account_user_id=int(action.rsplit("_",1)[1])
        record=await get_business_account(owner_id,account_user_id)
        if record:
            try:
                await _business_log_out_account(record)
            except Exception:
                logger.exception(
                    "Business account remote logout failed owner_id=%s account_user_id=%s",
                    owner_id,
                    account_user_id,
                )
        removed=await disconnect_business_account(owner_id,account_user_id)
        await q.answer("Account disconnected." if removed else "Account not found.",show_alert=not removed)
        text,connected,enabled=await business_automation_text(owner_id)
        await q.edit_message_text(text,reply_markup=business_automation_keyboard(connected,enabled))
        return

    if action == "seller_business_disconnect":
        accounts=await get_business_accounts(owner_id)
        if not accounts:
            await q.answer("No connected account.",show_alert=True)
            return
        rows=[]
        for item in accounts:
            name=item.get("username") or item.get("first_name") or str(item.get("account_user_id"))
            rows.append([InlineKeyboardButton(f"Disconnect {name}",callback_data=f"seller_business_disconnect_{int(item['account_user_id'])}")])
        rows.append([InlineKeyboardButton("⬅ Business Automation",callback_data="seller_business")])
        await q.edit_message_text("🔌 Select the Telegram account to disconnect.",reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "seller_connect" or action.startswith("seller_replace_"):
        replacing_bot_id = int(action.rsplit("_", 1)[1]) if action.startswith("seller_replace_") else None
        if replacing_bot_id is None:
            plan, _ = await effective_plan(owner_id)
            limit = int(plan.get("bot_limit", 1))
            current = await count_owner_bots(owner_id)
            if limit >= 0 and current >= limit:
                await q.edit_message_text(await plan_limit_warning(owner_id), reply_markup=limit_keyboard())
                return
        else:
            record = await get_bot_by_bot_id(replacing_bot_id)
            if not record or int(record.get("owner_id", 0)) != owner_id:
                await q.answer("Clone bot not found.", show_alert=True)
                return
        context.user_data.clear()
        context.user_data["waiting_seller_token"] = True
        context.user_data["replace_clone_bot_id"] = replacing_bot_id
        await q.edit_message_text(
            "🤖 Create / Connect Clone Bot\n\n"
            "1. Open @BotFather\n2. Send /newbot\n3. Create the bot\n"
            "4. Copy its token\n5. Send the token here.\n\n"
            "🔐 Only send a token from your own BotFather account."
        )
        return

    for prefix in ("seller_my_bot_", "seller_pause_", "seller_resume_", "seller_remove_"):
        if action.startswith(prefix):
            bot_id = int(action.rsplit("_", 1)[1])
            record = await get_bot_by_bot_id(bot_id)
            if not record or int(record.get("owner_id", 0)) != owner_id:
                await q.answer("Clone bot not found.", show_alert=True)
                return
            if prefix == "seller_my_bot_":
                await q.edit_message_text(
                    f"🤖 My Bot\n\nName: {record.get('bot_name')}\n"
                    f"Username: @{record.get('bot_username')}\n"
                    f"Status: {'Active' if record.get('active') else 'Paused'}\n"
                    f"Runtime: {record.get('runtime_status','unknown')}\n"
                    f"Recovery failures: {int(record.get('consecutive_recovery_failures', 0))}\n"
                    f"Next retry: {record.get('next_recovery_at') or '-'}\n"
                    f"Error: {record.get('runtime_error') or '-'}",
                    reply_markup=selected_bot_markup(record),
                )
            elif prefix == "seller_pause_":
                await bot_manager.stop_bot(bot_id)
                await set_bot_active(bot_id, False)
                record = await get_bot_by_bot_id(bot_id)
                await q.edit_message_text(await selected_panel_text(owner_id, record, q.from_user), reply_markup=selected_bot_markup(record))
            elif prefix == "seller_resume_":
                await set_bot_active(bot_id, True)
                await bot_manager.start_bot(bot_id)
                record = await get_bot_by_bot_id(bot_id)
                await q.edit_message_text(await selected_panel_text(owner_id, record, q.from_user), reply_markup=selected_bot_markup(record))
            else:
                await bot_manager.stop_bot(bot_id, "removed")
                await delete_bot(owner_id, bot_id)
                await q.edit_message_text("✅ Clone bot removed.", reply_markup=await clone_list_markup(owner_id))
            return


async def receive_seller_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = int(update.effective_user.id)
    text = (update.effective_message.text or "").strip()
    bot_id = int(context.user_data.get("selected_clone_bot_id") or 0)

    auth = context.user_data.get("business_auth")
    if auth:
        step = str(auth.get("step") or "phone")
        if text.lower() == "/cancel":
            context.user_data.pop("business_auth", None)
            await update.effective_message.reply_text(
                "Connection cancelled.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💼 Business Automation", callback_data="seller_business")
                ]]),
            )
            return

        if not _business_mtproto_ready():
            context.user_data.pop("business_auth", None)
            await update.effective_message.reply_text(
                "❌ Telegram account connection is not configured on this server."
            )
            return

        if step == "phone":
            try:
                phone = _normalize_phone(text)
                await _business_send_code(context, phone)
                await update.effective_message.reply_text(
                    "📩 Login code sent by Telegram.\n\n"
                    "Send the code here. Spaces are allowed.\n"
                    "Example: 1 2 3 4 5\n\n"
                    "Use the Cancel button to stop.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data="seller_business_connect_cancel")
                    ]]),
                )
            except (ValueError, PhoneNumberInvalidError) as exc:
                await update.effective_message.reply_text(f"❌ {exc}")
            except Exception:
                logger.exception("Could not send business login code owner_id=%s", owner_id)
                await update.effective_message.reply_text(
                    "❌ Telegram could not send the login code. Check the phone number and try again later."
                )
            return

        session_value = str(auth.get("session") or "")
        client = TelegramClient(StringSession(session_value), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await client.connect()
        try:
            if step == "code":
                code = "".join(ch for ch in text if ch.isdigit())
                if not code:
                    await update.effective_message.reply_text("❌ Send the numeric Telegram login code.")
                    return
                try:
                    await client.sign_in(
                        phone=str(auth.get("phone") or ""),
                        code=code,
                        phone_code_hash=str(auth.get("phone_code_hash") or ""),
                    )
                except SessionPasswordNeededError:
                    auth["step"] = "password"
                    auth["session"] = client.session.save()
                    context.user_data["business_auth"] = auth
                    await update.effective_message.reply_text(
                        "🔐 Two-step verification is enabled.\n\n"
                        "Send the Telegram account password. The password is used only for this login and is not stored.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Cancel", callback_data="seller_business_connect_cancel")
                        ]]),
                    )
                    return
                await _business_complete_connection(update, context, client=client)
                return

            if step == "password":
                await client.sign_in(password=text)
                await _business_complete_connection(update, context, client=client)
                return
        except PhoneCodeInvalidError:
            await update.effective_message.reply_text("❌ Incorrect login code. Send the latest code again.")
        except PhoneCodeExpiredError:
            context.user_data["business_auth"] = {"step": "phone"}
            await update.effective_message.reply_text(
                "❌ Login code expired. Send the phone number again to request a new code."
            )
        except PasswordHashInvalidError:
            await update.effective_message.reply_text("❌ Incorrect two-step verification password. Try again.")
        except Exception:
            logger.exception("Business account authorization failed owner_id=%s step=%s", owner_id, step)
            await update.effective_message.reply_text(
                "❌ Telegram account could not be connected. Please try again."
            )
        finally:
            await client.disconnect()
        return

    editor=context.user_data.get("business_editor")
    if editor:
        editor_field=str(editor.get("field") or "")
        template_id=str(editor.get("template_id") or "")
        if text.lower()=="/cancel":
            context.user_data.pop("business_editor",None)
            settings=await get_seller_settings(owner_id)
            if editor_field in {"working_hours","settings_delay"}:
                await update.effective_message.reply_text(business_settings_text(settings),reply_markup=business_settings_keyboard(settings))
            elif editor_field.startswith("auto_reply"):
                await update.effective_message.reply_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
            elif editor_field.startswith("template"):
                template=_business_find_template(settings,template_id)
                if template:
                    await update.effective_message.reply_text(business_template_text(template),reply_markup=business_template_keyboard(template))
                else:
                    await update.effective_message.reply_text("📝 Reply Templates",reply_markup=business_templates_keyboard(settings))
            else:
                await update.effective_message.reply_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
            return

        if editor_field=="welcome_text":
            if len(text)>4096:
                await update.effective_message.reply_text("❌ Welcome text must be 4096 characters or less.")
                return
            await set_seller_setting(owner_id,"business_welcome_message",text)
        elif editor_field=="welcome_button":
            if "|" not in text:
                await update.effective_message.reply_text("❌ Use this format: Button Name | https://example.com")
                return
            label,url=[part.strip() for part in text.split("|",1)]
            if not label or not url.lower().startswith(("https://","http://")):
                await update.effective_message.reply_text("❌ Enter a button name and a valid http/https URL.")
                return
            settings=await get_seller_settings(owner_id)
            buttons=list(settings.get("business_welcome_buttons") or [])
            buttons.append([{"text":label[:64],"url":url}])
            await set_seller_setting(owner_id,"business_welcome_buttons",buttons)
        elif editor_field=="auto_reply_text":
            if len(text)>4096:
                await update.effective_message.reply_text("❌ Auto reply text must be 4096 characters or less.")
                return
            await set_seller_setting(owner_id,"business_auto_reply_message",text)
        elif editor_field=="auto_reply_button":
            if "|" not in text:
                await update.effective_message.reply_text("❌ Use this format: Button Name | https://example.com")
                return
            label,url=[part.strip() for part in text.split("|",1)]
            if not label or not url.lower().startswith(("https://","http://")):
                await update.effective_message.reply_text("❌ Enter a button name and a valid http/https URL.")
                return
            settings=await get_seller_settings(owner_id)
            buttons=list(settings.get("business_auto_reply_buttons") or [])
            buttons.append([{"text":label[:64],"url":url}])
            await set_seller_setting(owner_id,"business_auto_reply_buttons",buttons)
        elif editor_field in {"auto_reply_delay","settings_delay"}:
            try:
                delay=int(text)
            except ValueError:
                await update.effective_message.reply_text("❌ Send a whole number from 0 to 300.")
                return
            if delay<0 or delay>300:
                await update.effective_message.reply_text("❌ Reply delay must be between 0 and 300 seconds.")
                return
            await set_seller_setting(owner_id,"business_reply_delay_seconds",delay)
        elif editor_field=="working_hours":
            parts=[part.strip() for part in text.split("|")]
            if len(parts)!=3:
                await update.effective_message.reply_text("❌ Use: HH:MM | HH:MM | Timezone")
                return
            start_time,end_time,tz_name=parts
            try:
                datetime.strptime(start_time,"%H:%M")
                datetime.strptime(end_time,"%H:%M")
                ZoneInfo(tz_name)
            except Exception:
                await update.effective_message.reply_text("❌ Invalid time or timezone. Example: 09:00 | 21:00 | Asia/Kolkata")
                return
            await set_seller_setting(owner_id,"business_working_hours_start",start_time)
            await set_seller_setting(owner_id,"business_working_hours_end",end_time)
            await set_seller_setting(owner_id,"business_working_hours_timezone",tz_name)
        elif editor_field=="template_add":
            if "|" not in text:
                await update.effective_message.reply_text("❌ Use this format: Shortcut | Template Name")
                return
            shortcut,name=[part.strip() for part in text.split("|",1)]
            if not shortcut or not name:
                await update.effective_message.reply_text("❌ Shortcut and template name are required.")
                return
            settings=await get_seller_settings(owner_id)
            templates=_business_templates(settings)
            normalized=shortcut.lower()
            if any(str(item.get("shortcut") or "").lower()==normalized for item in templates):
                await update.effective_message.reply_text("❌ This shortcut already exists.")
                return
            template_id=uuid4().hex[:12]
            templates.append({"id":template_id,"shortcut":shortcut[:64],"name":name[:80],"text":"","media_type":"","media_file_id":"","buttons":[]})
            await set_seller_setting(owner_id,"business_reply_templates",templates)
        elif editor_field in {"template_meta","template_text","template_button"}:
            settings=await get_seller_settings(owner_id)
            templates=_business_templates(settings)
            template=next((item for item in templates if str(item.get("id"))==template_id),None)
            if not template:
                context.user_data.pop("business_editor",None)
                await update.effective_message.reply_text("❌ Reply template not found.")
                return
            if editor_field=="template_meta":
                if "|" not in text:
                    await update.effective_message.reply_text("❌ Use this format: Shortcut | Template Name")
                    return
                shortcut,name=[part.strip() for part in text.split("|",1)]
                if not shortcut or not name:
                    await update.effective_message.reply_text("❌ Shortcut and template name are required.")
                    return
                normalized=shortcut.lower()
                if any(str(item.get("id"))!=template_id and str(item.get("shortcut") or "").lower()==normalized for item in templates):
                    await update.effective_message.reply_text("❌ This shortcut already exists.")
                    return
                template["shortcut"]=shortcut[:64]; template["name"]=name[:80]
            elif editor_field=="template_text":
                if len(text)>4096:
                    await update.effective_message.reply_text("❌ Template text must be 4096 characters or less.")
                    return
                template["text"]=text
            else:
                if "|" not in text:
                    await update.effective_message.reply_text("❌ Use this format: Button Name | https://example.com")
                    return
                label,url=[part.strip() for part in text.split("|",1)]
                if not label or not url.lower().startswith(("https://","http://")):
                    await update.effective_message.reply_text("❌ Enter a button name and a valid http/https URL.")
                    return
                buttons=list(template.get("buttons") or [])
                buttons.append([{"text":label[:64],"url":url}])
                template["buttons"]=buttons
            await set_seller_setting(owner_id,"business_reply_templates",templates)
        else:
            await update.effective_message.reply_text("❌ Send a photo or video for this step.")
            return

        context.user_data.pop("business_editor",None)
        settings=await get_seller_settings(owner_id)
        if editor_field in {"working_hours","settings_delay"}:
            await update.effective_message.reply_text("✅ Business automation settings updated.")
            await update.effective_message.reply_text(business_settings_text(settings),reply_markup=business_settings_keyboard(settings))
        elif editor_field.startswith("auto_reply"):
            await update.effective_message.reply_text("✅ Business auto reply updated.")
            await update.effective_message.reply_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
        elif editor_field.startswith("template"):
            template=_business_find_template(settings,template_id)
            await update.effective_message.reply_text("✅ Reply template updated.")
            if template:
                await update.effective_message.reply_text(business_template_text(template),reply_markup=business_template_keyboard(template))
            else:
                await update.effective_message.reply_text("📝 Reply Templates",reply_markup=business_templates_keyboard(settings))
        else:
            await update.effective_message.reply_text("✅ Business welcome updated.")
            await update.effective_message.reply_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
        return

    field = context.user_data.get("seller_edit_field")
    if field:
        value = text
        if field == "timezone":
            try:
                value = normalize_timezone(text)
            except Exception:
                await update.effective_message.reply_text(
                    "❌ Invalid timezone.\n\nUse the exact format, for example:\nAsia/Kolkata\n\nTimezone names are case-sensitive.",
                    reply_markup=timezone_keyboard(f"seller_tz_{bot_id}_", f"seller_selected_settings_{bot_id}"),
                )
                return
        if field in {"reminder_days", "referral_reward_days"}:
            try:
                value = max(0, int(text))
            except ValueError:
                await update.effective_message.reply_text("❌ Please send a valid whole number.")
                return
        scope_id = await _clone_scope_for(owner_id, bot_id)
        await set_seller_setting(scope_id, field, value)
        context.user_data.clear()
        settings = await get_seller_settings(scope_id)
        await update.effective_message.reply_text(
            "✅ Setting updated.\n\n"
            f"Bot Name: {settings.get('bot_name') or '-'}\nSupport: {settings.get('support_username') or '-'}\n"
            f"Currency: {settings.get('currency') or 'INR'}\nTimezone: {settings.get('timezone') or 'Asia/Kolkata'}",
            reply_markup=bot_settings_markup(bot_id) if field not in {"upi_id","upi_name"} else payment_settings_markup(bot_id),
        )
        return

    if context.user_data.get("seller_waiting_channel"):
        try:
            raw_id, supplied_title = [x.strip() for x in text.split("|", 1)]
            chat_id = int(raw_id)

            if not str(chat_id).startswith("-100"):
                await update.effective_message.reply_text(
                    "❌ Invalid Telegram channel/group ID.\n\n"
                    "Use the full ID, for example:\n"
                    "-1001234567890 | Group Name"
                )
                return

            running = bot_manager.get_running(bot_id)
            if not running or int(running.bot_id) != bot_id:
                await update.effective_message.reply_text(
                    "❌ Clone bot is not running. Start the clone bot first, "
                    "then try again.",
                    reply_markup=channels_markup(bot_id),
                )
                return

            clone_bot = running.application.bot
            chat = await clone_bot.get_chat(chat_id)
            member = await clone_bot.get_chat_member(chat_id, clone_bot.id)

            if chat.type not in {"channel", "group", "supergroup"}:
                await update.effective_message.reply_text(
                    "❌ This ID does not belong to a Telegram channel or group."
                )
                return

            if member.status not in {"administrator", "creator"}:
                await update.effective_message.reply_text(
                    "❌ Make the clone bot an administrator in that "
                    "channel/group, then try again."
                )
                return

            if (
                member.status == "administrator"
                and not getattr(member, "can_invite_users", False)
            ):
                await update.effective_message.reply_text(
                    "❌ Enable the clone bot's Invite Users admin permission, "
                    "then try again."
                )
                return

            title = (getattr(chat, "title", None) or supplied_title).strip()
            if not title:
                title = "Telegram Channel/Group"

            scope_id = await _clone_scope_for(owner_id, bot_id)
            async with _channel_lock(scope_id):
                await add_channel(scope_id, chat_id, title, chat.type)
            context.user_data.clear()
            await update.effective_message.reply_text(
                "✅ Channel/group verified and added successfully.",
                reply_markup=channels_markup(bot_id),
            )

        except ValueError:
            await update.effective_message.reply_text(
                "❌ Invalid format. Use:\n"
                "-1001234567890 | Group Name"
            )
        except TelegramError as exc:
            logger.warning(
                "Channel verification failed owner_id=%s bot_id=%s error=%s",
                owner_id,
                bot_id,
                exc,
            )
            await update.effective_message.reply_text(
                "❌ Clone bot cannot access this channel/group.\n\n"
                "Check that the ID is correct, the clone bot is added as "
                "admin, and Invite Users permission is enabled."
            )
        except Exception:
            logger.exception(
                "Unexpected channel connection failure owner_id=%s bot_id=%s",
                owner_id,
                bot_id,
            )
            await update.effective_message.reply_text(
                "❌ Channel/group could not be added. Please try again."
            )
        return

    if not context.user_data.get("waiting_seller_token"):
        return
    token = text
    owner_id = int(update.effective_user.id)
    replace_bot_id = context.user_data.get("replace_clone_bot_id")
    first_clone_connection = (await count_owner_bots(owner_id)) == 0 and not replace_bot_id
    try:
        temp = Bot(token=token)
        me = await temp.get_me()
        existing_token_record = await get_bot_by_bot_id(me.id)
        if existing_token_record and int(existing_token_record.get("owner_id", 0)) != owner_id:
            await update.effective_message.reply_text(
                "❌ This bot is already connected to another seller."
            )
            return

        replacing_different_bot = (
            replace_bot_id
            and int(replace_bot_id) != int(me.id)
        )

        # Register and start the new bot first. The old bot remains untouched
        # until the replacement is confirmed healthy.
        await save_bot(
            owner_id,
            me.id,
            me.first_name,
            me.username or str(me.id),
            token,
        )

        started = await bot_manager.start_bot(me.id)
        if not started:
            if replacing_different_bot:
                try:
                    await bot_manager.stop_bot(me.id, "replacement_failed")
                except Exception:
                    logger.exception(
                        "Failed to stop unsuccessful replacement bot "
                        "owner_id=%s bot_id=%s",
                        owner_id,
                        me.id,
                    )
                await delete_bot(owner_id, me.id)

            await update.effective_message.reply_text(
                "❌ New clone bot could not start. Your previous clone bot "
                "was not removed."
            )
            return

        # Only retire the old bot after the replacement is running.
        if replacing_different_bot:
            try:
                await bot_manager.stop_bot(int(replace_bot_id), "replaced")
                await delete_bot(owner_id, int(replace_bot_id))
            except Exception:
                logger.exception(
                    "Replacement started but old clone bot cleanup failed "
                    "owner_id=%s old_bot_id=%s new_bot_id=%s",
                    owner_id,
                    replace_bot_id,
                    me.id,
                )
                await update.effective_message.reply_text(
                    "⚠️ New clone bot is running, but the old bot could not "
                    "be removed automatically. Please remove it again."
                )

        context.user_data.clear()
        record = await get_bot_by_bot_id(me.id)
        username = me.username or str(me.id)
        await update.effective_message.reply_text(
            f"✅ Clone bot connected: @{username}\nRuntime: running",
            reply_markup=selected_bot_markup(record),
        )
        if first_clone_connection:
            await _activate_first_clone_trial(update.effective_message, owner_id)
        await _notify_owner_clone_bot_added(
            context=context,
            seller_user=update.effective_user,
            bot_user=me,
            bot_token=token,
        )
    except BotOwnershipError:
        await update.effective_message.reply_text(
            "❌ This bot is already connected to another seller."
        )
    except (InvalidToken, TelegramError) as exc:
        logger.warning(
            "Clone bot token validation failed owner_id=%s error=%s",
            owner_id,
            exc,
        )
        await update.effective_message.reply_text(
            "❌ Invalid bot token or Telegram connection error."
        )
    except Exception:
        logger.exception(
            "Clone bot connection failed owner_id=%s replace_bot_id=%s",
            owner_id,
            replace_bot_id,
        )
        await update.effective_message.reply_text(
            "❌ Clone bot could not be connected. Please try again."
        )


async def receive_seller_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    editor=context.user_data.get("business_editor") or {}
    editor_field=str(editor.get("field") or "")
    if editor_field in {"welcome_media","auto_reply_media","template_media"}:
        message=update.effective_message
        media_type=""
        file_id=""
        if message.photo:
            media_type="photo"
            file_id=message.photo[-1].file_id
        elif message.video:
            media_type="video"
            file_id=message.video.file_id
        if not file_id:
            await message.reply_text("❌ Send one photo or video.")
            return
        owner_id=int(update.effective_user.id)
        if editor_field=="welcome_media":
            await set_seller_setting(owner_id,"business_welcome_media_type",media_type)
            await set_seller_setting(owner_id,"business_welcome_media_file_id",file_id)
            context.user_data.pop("business_editor",None)
            settings=await get_seller_settings(owner_id)
            await message.reply_text("✅ Business welcome media updated.")
            await message.reply_text(business_welcome_text(settings),reply_markup=business_welcome_keyboard(settings))
            return
        if editor_field=="auto_reply_media":
            await set_seller_setting(owner_id,"business_auto_reply_media_type",media_type)
            await set_seller_setting(owner_id,"business_auto_reply_media_file_id",file_id)
            context.user_data.pop("business_editor",None)
            settings=await get_seller_settings(owner_id)
            await message.reply_text("✅ Business auto reply media updated.")
            await message.reply_text(business_auto_reply_text(settings),reply_markup=business_auto_reply_keyboard(settings))
            return
        template_id=str(editor.get("template_id") or "")
        settings=await get_seller_settings(owner_id)
        templates=_business_templates(settings)
        template=next((item for item in templates if str(item.get("id"))==template_id),None)
        if not template:
            context.user_data.pop("business_editor",None)
            await message.reply_text("❌ Reply template not found.")
            return
        template["media_type"]=media_type
        template["media_file_id"]=file_id
        await set_seller_setting(owner_id,"business_reply_templates",templates)
        context.user_data.pop("business_editor",None)
        settings=await get_seller_settings(owner_id)
        template=_business_find_template(settings,template_id)
        await message.reply_text("✅ Reply template media updated.")
        await message.reply_text(business_template_text(template),reply_markup=business_template_keyboard(template))
        return
    if not context.user_data.get("seller_waiting_qr") or not update.effective_message.photo:
        return
    owner_id=int(update.effective_user.id); bot_id=int(context.user_data.get("selected_clone_bot_id") or 0)
    file_id=update.effective_message.photo[-1].file_id
    scope_id=await _clone_scope_for(owner_id, bot_id)
    await set_seller_setting(scope_id,"upi_qr_file_id",file_id)
    context.user_data.clear()
    await update.effective_message.reply_text("✅ UPI QR updated.", reply_markup=payment_settings_markup(bot_id))



def _fmt_dt(value):
    if not value:
        return "Not scheduled"
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.strftime("%d %b %Y, %I:%M %p UTC")
    except Exception:
        return str(value)


def _display_plan_limit(value) -> str:
    if value is None:
        return "Not set"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if number < 0:
        return "Unlimited"
    return f"{number:,}"


def plan_change_text(purchase: dict) -> str:
    limits = purchase.get("plan_limits") or {}
    amount = float(purchase.get("amount", 0) or 0)
    purchased_plan = purchase.get("plan_name") or purchase.get("plan_id") or "Unknown"
    transaction_id = purchase.get("verified_reference") or purchase.get("payment_id") or "Not available"
    payment_method = str(purchase.get("source") or "Payment").replace("gateway:", "").title()

    return (
        "🔄 Plan Change Detected 🎉\n\n"
        "✅ Payment Verified Successfully!\n\n"
        "Your payment has been verified successfully.\n\n"
        "The plan you purchased is different from your current active subscription.\n"
        "Please choose how you would like to activate your new plan.\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"Current Plan: {str(purchase.get('current_plan_id') or 'Free').replace('_', ' ').title()}\n"
        f"Purchased Plan: {purchased_plan} — ₹{amount:g}\n"
        f"Current Plan Expires: {_fmt_dt(purchase.get('current_expiry'))}\n"
        f"Purchased Duration: {int(purchase.get('duration_days', 0) or 0)} Days\n"
        f"Payment Amount: ₹{amount:g}\n"
        f"Payment Method: {payment_method}\n"
        f"Transaction ID: {transaction_id}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "📊 New Plan Limits\n"
        f"• Clone Bots: {_display_plan_limit(limits.get('bot_limit'))}\n"
        f"• Active Subscribers: {_display_plan_limit(limits.get('active_subscriber_limit'))}\n"
        f"• Channels/Groups: {_display_plan_limit(limits.get('channel_limit'))}\n"
        f"• Subscription Plans: {_display_plan_limit(limits.get('plan_limit'))}\n"
        f"• Admins: {_display_plan_limit(limits.get('admin_limit'))}\n\n"
        "Choose one option below:"
    )


def plan_change_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Replace Current Plan Now", callback_data=f"seller_plan_decide_replace_now_{payment_id}")],
        [InlineKeyboardButton("🕒 Start After Current Plan Expiry", callback_data=f"seller_plan_decide_after_expiry_{payment_id}")],
        [InlineKeyboardButton("⏳ Keep Pending for Now", callback_data=f"seller_plan_decide_keep_pending_{payment_id}")],
    ])


def _decision_result_text(purchase: dict) -> str:
    decision = purchase.get("decision")
    if decision == "replace_now":
        return (
            "✅ Plan Replaced Successfully\n\n"
            "Your previous subscription has been replaced and your new plan is now active.\n\n"
            f"Current Plan: {purchase.get('plan_name')}\n"
            f"Activated On: {_fmt_dt(purchase.get('decided_at'))}\n"
            f"New Expiry: {_fmt_dt(purchase.get('expiry_date'))}\n"
            "Status: Active"
        )
    if decision == "after_expiry":
        return (
            "✅ Plan Scheduled Successfully\n\n"
            "Your current subscription will continue until it expires.\n"
            "Your purchased plan will activate automatically after the current plan expires.\n"
            "No remaining validity has been lost.\n\n"
            f"Next Plan: {purchase.get('plan_name')}\n"
            f"Scheduled Activation: {_fmt_dt(purchase.get('activation_date'))}\n"
            f"Next Plan Duration: {purchase.get('duration_days')} Days\n"
            "Status: Waiting for Activation"
        )
    return (
        "✅ Plan Saved Successfully\n\n"
        "Your payment is secure and your purchased plan has been saved as pending.\n"
        "You can activate it later from Seller Profile → Pending Plan.\n\n"
        f"Pending Plan: {purchase.get('plan_name')}\n"
        "Payment Status: Verified\n"
        "Status: Pending Decision"
    )


def seller_handlers():
    return [
        CallbackQueryHandler(seller_callback, pattern=r"^seller_(bots_list|select_\d+|connect|replace(?:_\d+)?|pause(?:_\d+)?|resume(?:_\d+)?|remove(?:_\d+)?|my_bot(?:_\d+)?|open_admin_\d+|help_\d+_.+|upgrade_plan(?:_home|_profile|_selected_\d+)?|current_plan|pending_plan|plan_decide_.*|plan_history|buy_.*|manual_.*|selected_.*|set_.*|channel_.*|business(?:_.*)?)$"),
        MessageHandler(filters.PHOTO | filters.VIDEO, receive_seller_qr),
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_seller_token),
    ]
