import asyncio
import html
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from utils.timezone_ui import timezone_guide, timezone_keyboard, timezone_from_key, normalize_timezone
from config import PUBLIC_BASE_URL

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.error import BadRequest, Conflict, Forbidden, InvalidToken, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

from database.seller_subscriptions import (
    effective_plan, plan_limit_warning, current_plan_text, get_config,
    seller_access_state, usage_warning, bot_runtime_allowed,
)
from database.payment_gateways import (
    SUPPORTED_GATEWAYS, create_gateway_transaction, get_gateway_config,
    save_gateway_config, set_gateway_preferences, gateway_history,
)
from services.payment_gateways import create_checkout, test_gateway_connection, GatewayError
from database.official_links import get_official_links
from database.seller_bots import (
    claim_runtime_recovery, finish_runtime_recovery, get_all_active_bots, get_bot, get_bot_payment_qr, set_bot_payment_qr,
    get_bot_by_bot_id, get_bot_by_data_owner_id, get_decrypted_bot_token,
    mark_invalid_token, recovery_allowed, set_runtime_status,
)
from database.mongo import get_database
from database.seller_referrals import seller_referral_stats
from database.referral_unlock import (
    expired_referral_unlocks,
    get_referral_unlock,
    mark_referral_unlock_expired,
    save_referral_unlock,
)
from database.live_support import (
    count_support_blocks, delete_support_topic, reset_support_topic_mapping, get_live_support_settings,
    get_private_message_link, get_support_topic, get_topic_by_thread,
    is_support_blocked, save_private_message_link, save_support_topic,
    claim_support_topic_creation, complete_support_topic_creation,
    fail_support_topic_creation, claim_support_topic_header, mark_support_topic_header, release_support_topic_header_claim,
    set_support_block, update_live_support_settings,
    list_support_templates, get_support_template, save_support_template,
    delete_support_template, list_support_auto_replies, get_support_auto_reply,
    save_support_auto_reply, delete_support_auto_reply, match_support_auto_reply,
    claim_support_delivery, complete_support_delivery, fail_support_delivery,
)
from database.platform_features import (
    audit,
    broadcast_cancel_requested,
    claim_failed_delivery,
    claim_scheduled_broadcast,
    create_coupon,
    create_invoice,
    get_failed_deliveries,
    get_policy,
    list_coupons,
    pending_scheduled_broadcasts,
    release_failed_delivery_claim,
    release_scheduled_broadcast,
    reserve_payment_fingerprint,
    resolve_failed_delivery,
    save_failed_delivery,
    save_scheduled_broadcast,
    set_scheduled_status,
    create_scheduled_campaign, list_scheduled_campaigns, get_scheduled_campaign,
    update_scheduled_campaign, delete_scheduled_campaign,
)
from database.seller_data import (
    activate_subscription, fulfill_subscription_payment, active_subscriptions, add_channel, create_payment, create_automatic_payment, create_plan, delete_plan,
    ensure_seller_defaults, expired_subscriptions, get_channels, get_payment,
    set_channel_auto_invite,
    get_plan, get_plans, get_seller_settings, get_subscription, get_user, mark_expired,
    payment_history, pending_payments, remove_channel, set_payment_status,
    claim_payment_for_processing, finalize_processed_payment,
    release_processing_payment,
    set_seller_setting, stats, update_plan, upsert_user,
    register_referral, count_all_referrals, count_successful_referrals,
    mark_referral_rewarded, finalize_referral_reward,
    release_referral_reward, get_user_by_username, set_user_ban,
    remove_subscription,
)

logger=logging.getLogger(__name__)
WELCOME_RUNTIME_VERSION="2026-07-13-main-role-dashboard-fix-13"

# Clone-bot currency is a display/plan currency setting. Keep all UI formatting
# centralized so changing the setting never leaves hard-coded INR symbols behind.
CURRENCY_INFO = {
    "INR": ("₹", "Indian Rupee"),
    "USD": ("$", "US Dollar"),
    "EUR": ("€", "Euro"),
    "GBP": ("£", "British Pound"),
    "AED": ("د.إ", "UAE Dirham"),
    "CAD": ("CA$", "Canadian Dollar"),
    "AUD": ("A$", "Australian Dollar"),
    "SGD": ("S$", "Singapore Dollar"),
    "JPY": ("¥", "Japanese Yen"),
    "BDT": ("৳", "Bangladeshi Taka"),
    "NPR": ("रु", "Nepalese Rupee"),
    "MYR": ("RM", "Malaysian Ringgit"),
    "THB": ("฿", "Thai Baht"),
    "IDR": ("Rp", "Indonesian Rupiah"),
}

def normalize_currency(value) -> str:
    code = str(value or "INR").strip().upper()
    return code if code in CURRENCY_INFO else ""

def currency_symbol(value) -> str:
    code = normalize_currency(value) or "INR"
    return CURRENCY_INFO[code][0]

def currency_name(value) -> str:
    code = normalize_currency(value) or "INR"
    return CURRENCY_INFO[code][1]

def format_currency(value, amount, *, spaced=False) -> str:
    code = normalize_currency(value) or "INR"
    symbol = currency_symbol(code)
    try:
        number = f"{float(amount):g}"
    except (TypeError, ValueError):
        number = str(amount if amount is not None else 0)
    # Prefix symbols are conventional for the supported UI currencies.
    return f"{symbol}{' ' if spaced else ''}{number}"

def currency_settings_text(current) -> str:
    code = normalize_currency(current) or "INR"
    symbol = currency_symbol(code)
    name = currency_name(code)
    supported = ", ".join(CURRENCY_INFO.keys())
    return (
        "💱 Currency Settings\n\n"
        f"Current Currency: {symbol} {code} — {name}\n\n"
        "📌 What this setting changes:\n"
        "• Plan price display\n"
        "• Plan management and edit screens\n"
        "• Manual payment amount display\n"
        "• Payment history, receipts and profile amounts\n"
        "• Clone dashboard revenue display\n\n"
        "⚠️ Important:\n"
        "• Changing currency does NOT convert existing prices. Example: 199 stays 199; only its currency label changes.\n"
        "• Telegram Stars are always ⭐ and are not converted.\n"
        "• In this bot, Indian payment gateways are treated as INR-only. For a non-INR currency, use Manual Payment or configure a gateway that supports that currency before enabling automatic checkout.\n\n"
        f"Supported codes: {supported}\n\n"
        "Send one 3-letter currency code, for example: USD"
    )

MAIN_BOT_USERNAME=os.getenv("MAIN_BOT_USERNAME","Local_supplier3_bot").lstrip("@")


def clone_feature_back_target(context) -> str:
    """Return the message origin for end-user feature Back buttons."""
    return str(context.user_data.get("clone_feature_back_target") or "c_home")


def _seller_razorpay_webhook_url(owner_id: int) -> str:
    if not PUBLIC_BASE_URL:
        return "PUBLIC_BASE_URL is not configured"
    return f"{PUBLIC_BASE_URL}/webhooks/razorpay/seller/{int(owner_id)}"


def _seller_razorpay_text(g: dict) -> str:
    return (
        "💳 Razorpay\n\n"
        f"Status: {'Enabled ✅' if g.get('enabled') else 'Disabled ❌'}\n"
        f"Key ID: {'Added' if g.get('key_id') else 'Not added'}\n"
        f"Key Secret: {'Added' if g.get('key_secret') else 'Not added'}\n"
        f"Webhook URL: {'Generated ✅' if PUBLIC_BASE_URL else 'Not available ❌'}\n"
        f"Webhook Secret: {'Added ✅' if g.get('webhook_secret') else 'Not added ❌'}"
    )


def _seller_razorpay_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ Disable" if enabled else "✅ Enable", callback_data="a_pg_toggle_razorpay")],
        [InlineKeyboardButton("🔑 Set / Replace Credentials", callback_data="a_pg_creds_razorpay")],
        [InlineKeyboardButton("🔐 Set Webhook Secret", callback_data="a_pg_webhook_secret")],
        [InlineKeyboardButton("🔗 Webhook Setup", callback_data="a_pg_webhook_setup")],
        [InlineKeyboardButton("✅ Test Connection", callback_data="a_pg_testconn_razorpay")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_pg_home")],
    ])


def _seller_webhook_setup_text(owner_id: int, g: dict) -> str:
    return (
        "🔗 Razorpay Webhook Setup\n\n"
        "Your unique webhook URL has been generated automatically.\n\n"
        f"Webhook URL:\n{_seller_razorpay_webhook_url(owner_id)}\n\n"
        "Required Events:\n"
        "• payment.captured\n"
        "• order.paid\n"
        "• payment_link.paid\n\n"
        f"Webhook Secret: {'Added ✅' if g.get('webhook_secret') else 'Not added ❌'}\n"
        f"Last valid webhook: {'Received ✅' if g.get('last_webhook_received_at') else 'Not received yet ⚪'}"
    )


def _seller_webhook_guide_text() -> str:
    return (
        "📖 Razorpay Webhook Setup Guide\n\n"
        "1. Log in to your Razorpay Dashboard.\n"
        "2. Open Settings → Webhooks.\n"
        "3. Tap Add New Webhook.\n"
        "4. Copy the URL shown on the Webhook Setup page and paste it in Razorpay.\n"
        "5. Create a strong Webhook Secret.\n"
        "6. Select payment.captured, order.paid and payment_link.paid.\n"
        "7. Save the webhook.\n"
        "8. Return to the Razorpay page in this bot.\n"
        "9. Tap Set Webhook Secret and paste the same secret.\n"
        "10. Open Webhook Setup and tap Test Webhook.\n\n"
        "Important: Razorpay Key Secret and Webhook Secret are different."
    )


class _MessageQueryAdapter:
    """Adapt a normal Message to the small CallbackQuery interface used by detail views."""

    def __init__(self, message):
        self.message = message

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        return await self.message.reply_text(text, reply_markup=reply_markup, **kwargs)


def _format_auto_delete(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds == 0:
        return "Off"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _template_auto_delete_seconds(template: dict) -> int:
    if not template:
        return 0
    if template.get("auto_delete_seconds") is not None:
        return max(0, int(template.get("auto_delete_seconds") or 0))
    return max(0, int(template.get("auto_delete_minutes") or 0) * 60)


def _parse_auto_delete_duration(value: str) -> int:
    raw = str(value or "").strip().lower().replace(" ", "")
    if raw in {"0", "off", "none", "disable", "disabled"}:
        return 0
    units = (("mo", 30 * 86400), ("min", 60), ("s", 1), ("m", 60), ("h", 3600), ("d", 86400))
    for suffix, multiplier in units:
        if raw.endswith(suffix):
            number = raw[:-len(suffix)]
            if not number or not number.isdigit():
                break
            seconds = int(number) * multiplier
            if seconds < 0 or seconds > 7 * 86400:
                raise ValueError("Duration 0 seconds se 7 days ke beech rakho")
            return seconds
    raise ValueError("Use: 30s, 2m, 1h, 6h, 1d ya off")

from services.message_moderation import moderate_seller_message
from handlers.deleting_messages import deleting_messages_handlers
from services.protected_bot import ProtectedExtBot
from handlers.content_protection import content_protection_handlers
from database.content_protection import get_content_protection_settings

from database.subscription_guard import save_invite, active_invites_for_user, deactivate_invite
from database.staff import active_staff, list_staff, promote_staff, remove_staff, set_staff_status, log_staff_action
from services.subscription_guard import subscription_guard_chat_member, subscription_guard_new_members
from handlers.subscription_guard import subscription_guard_handlers

@dataclass
class RunningSellerBot:
    owner_id:int; bot_id:int; application:Application


__all__ = [name for name in globals() if not name.startswith("__")]
