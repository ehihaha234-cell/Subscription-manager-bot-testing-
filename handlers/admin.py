import asyncio
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from database.admins import is_admin, add_admin, remove_admin
from database.channels import add_channel, remove_channel, get_all_channels, total_channels
from database.users import (
    total_users,
    get_user,
    get_user_by_username,
    ban_user,
    unban_user,
)
from database.payments import total_revenue
from database.mongo import get_database
from html import escape
from database.settings import get_setting, get_setting_value, set_setting
from database.subscriptions import (
    get_subscription,
    expire_subscription,
    activate_subscription,
    renew_subscription,
)
from database.seller_data import (
    c as seller_collection,
    USERS as SELLER_USERS,
    SUBS as SELLER_SUBS,
    get_user as get_seller_user,
    get_subscription as get_seller_subscription,
    activate_subscription as activate_seller_subscription,
)
from database.seller_bots import get_bot as get_seller_bot
from services.bot_manager import bot_manager
from utils.timezone_ui import timezone_guide, timezone_keyboard, timezone_from_key, normalize_timezone

from services.channel_service import (
    revoke_channel_access,
    grant_channel_access,
)


IST = ZoneInfo("Asia/Kolkata")


def format_time(dt):
    if not dt:
        return "-"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p IST")


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
        [InlineKeyboardButton("➕ Add Channel/Group", callback_data="admin_add_channel")],
        [InlineKeyboardButton("📋 Channel List", callback_data="admin_channels")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("💳 Payment Settings", callback_data="admin_payment_settings")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_bot_settings")],
        [InlineKeyboardButton("📨 Pending Payments", callback_data="admin_pending_payments")],
        [InlineKeyboardButton("📜 Payment History", callback_data="admin_payment_history")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👮 Admin Commands", callback_data="admin_commands")],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅ Owner Dashboard", callback_data="main_owner_dashboard")]
    ])


def user_action_keyboard(user_id: int, banned: bool):
    keyboard = [
        [
            InlineKeyboardButton(
                "🎁 Give / Extend Subscription",
                callback_data=f"user_manage_sub_{user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Remove Subscription",
                callback_data=f"user_remove_sub_{user_id}",
            )
        ],
    ]

    if banned:
        keyboard.append([
            InlineKeyboardButton(
                "✅ Unban User",
                callback_data=f"user_unban_{user_id}",
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                "🚫 Ban User",
                callback_data=f"user_ban_{user_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅ Back",
            callback_data="admin_users",
        )
    ])

    return InlineKeyboardMarkup(keyboard)


def parse_plan_time(time_text: str):
    time_text = time_text.strip().lower()

    if time_text.endswith("m"):
        return int(time_text[:-1]), "minutes"

    if time_text.endswith("h"):
        return int(time_text[:-1]) * 60, "hours"

    if time_text.endswith("mo"):
        return int(time_text[:-2]) * 30 * 1440, "months"

    if time_text.endswith("y"):
        return int(time_text[:-1]) * 365 * 1440, "years"

    if time_text.endswith("d"):
        return int(time_text[:-1]) * 1440, "days"

    raise ValueError("Invalid time format. Use m, h, d, mo or y")


def parse_plans(text: str):
    plans = []

    for part in text.split(","):
        duration_text, price_text = part.strip().split(":")
        duration_minutes, unit = parse_plan_time(duration_text)

        plans.append({
            "duration_text": duration_text.strip(),
            "duration_minutes": duration_minutes,
            "duration_days": max(1, duration_minutes // 1440),
            "price": int(price_text.strip()),
            "unit": unit,
        })

    return plans


async def build_user_details_text(user):
    subscription = await get_subscription(user["user_id"])

    if subscription:
        plan = subscription.get("plan", "No Plan")
        expiry = format_time(subscription.get("expiry_date"))
        sub_status = "✅ Active" if subscription.get("active") else "❌ Expired"
    else:
        plan = "No Plan"
        expiry = "-"
        sub_status = "No subscription"

    banned = bool(user.get("banned"))

    return (
        "👤 User Details\n\n"
        f"🆔 ID: {user.get('user_id')}\n"
        f"👤 Name: {user.get('first_name') or '-'}\n"
        f"📛 Username: @{user.get('username') if user.get('username') else 'None'}\n"
        f"🚫 Banned: {'Yes' if banned else 'No'}\n"
        f"📝 Reason: {user.get('ban_reason') or '-'}\n"
        f"📅 Joined: {format_time(user.get('joined_at'))}\n\n"
        f"💎 Plan: {plan}\n"
        f"📅 Expiry: {expiry}\n"
        f"📌 Status: {sub_status}"
    )


async def show_user_details(query, user):
    text = await build_user_details_text(user)
    banned = bool(user.get("banned"))

    await query.edit_message_text(
        text,
        reply_markup=user_action_keyboard(user["user_id"], banned),
    )


def seller_user_action_keyboard(owner_id:int,user_id:int,banned:bool):
    prefix=f"owner_su_{int(owner_id)}_{int(user_id)}"
    rows=[
        [InlineKeyboardButton("🎁 Give / Extend Subscription",callback_data=prefix+"_manage")],
        [InlineKeyboardButton("❌ Remove Subscription",callback_data=prefix+"_remove")],
    ]
    rows.append([InlineKeyboardButton("✅ Unban User" if banned else "🚫 Ban User",callback_data=prefix+("_unban" if banned else "_ban"))])
    rows.append([InlineKeyboardButton("⬅ Back",callback_data="admin_users")])
    return InlineKeyboardMarkup(rows)


async def build_seller_user_details_text(owner_id:int,user:dict):
    sub=await get_seller_subscription(int(owner_id),int(user["user_id"])) or {}
    bot_record=await get_seller_bot(int(owner_id)) or {}
    return (
        "👤 Clone Bot User Details\n\n"
        f"🏪 Seller ID: {owner_id}\n"
        f"🤖 Clone Bot: @{bot_record.get('bot_username','Not connected')}\n"
        f"🆔 User ID: {user.get('user_id')}\n"
        f"👤 Name: {' '.join(x for x in [user.get('first_name'),user.get('last_name')] if x) or '-'}\n"
        f"📛 Username: @{user.get('username') if user.get('username') else 'None'}\n"
        f"🚫 Banned: {'Yes' if user.get('banned') else 'No'}\n"
        f"📅 Joined: {format_time(user.get('joined_at'))}\n\n"
        f"💎 Plan: {sub.get('plan') or 'No Plan'}\n"
        f"📅 Expiry: {format_time(sub.get('expiry_date'))}\n"
        f"📌 Status: {'✅ Active' if sub.get('active') else '❌ Inactive'}"
    )


async def show_seller_user_details(query,owner_id:int,user:dict):
    await query.edit_message_text(
        await build_seller_user_details_text(owner_id,user),
        reply_markup=seller_user_action_keyboard(owner_id,user["user_id"],bool(user.get("banned"))),
    )


async def find_seller_users(search:str):
    value=search.strip()
    if value.startswith("@"):
        query={"username_normalized":value[1:].lower()}
    else:
        try:
            query={"user_id":int(value)}
        except ValueError:
            query={"username_normalized":value.lower()}
    return await seller_collection(SELLER_USERS).find(query).limit(20).to_list(length=20)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keep /admin backward-compatible, but open the Owner Dashboard."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    from handlers.main_dashboard import owner_dashboard_keyboard, owner_dashboard_text

    await update.message.reply_text(
        await owner_dashboard_text(),
        reply_markup=owner_dashboard_keyboard(),
    )

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await is_admin(query.from_user.id):
        await query.edit_message_text("❌ You are not authorized.")
        return

    if query.data == "admin_users":
        context.user_data.clear()
        context.user_data["waiting_user_search"] = True

        await query.edit_message_text(
            "👥 User Management\n\nSend User ID or @username to search.",
            reply_markup=back_keyboard(),
        )

    elif query.data.startswith("user_ban_"):
        user_id = int(query.data.replace("user_ban_", ""))

        await ban_user(user_id, "Banned by admin")
        await revoke_channel_access(user_id)

        user = await get_user(user_id)
        await show_user_details(query, user)

    elif query.data.startswith("user_unban_"):
        user_id = int(query.data.replace("user_unban_", ""))

        await unban_user(user_id)

        user = await get_user(user_id)
        await show_user_details(query, user)

    elif query.data.startswith("user_manage_sub_"):
        user_id = int(query.data.replace("user_manage_sub_", ""))
        context.user_data.clear()
        context.user_data["manage_sub_user"] = user_id

        channels = await get_all_channels()
        plan_map = {}
        rows = []
        counter = 0
        for channel in channels:
            for plan in channel.get("plans", []):
                counter += 1
                key = f"p{counter}"
                plan_map[key] = {
                    "name": f"{channel.get('title','Plan')} - {plan.get('duration_text','')}",
                    "duration_minutes": int(plan.get("duration_minutes", 0)),
                    "duration_text": plan.get("duration_text", ""),
                }
                rows.append([InlineKeyboardButton(
                    plan_map[key]["name"],
                    callback_data=f"user_apply_plan_{user_id}_{key}",
                )])
        context.user_data["manage_sub_plans"] = plan_map
        rows.append([InlineKeyboardButton("⌨️ Custom Duration", callback_data=f"user_custom_sub_{user_id}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin_users")])
        await query.edit_message_text(
            "🎁 Give / Extend Subscription\n\n"
            "Select an existing plan or choose Custom Duration.\n\n"
            "If the user already has an active subscription, the new duration is added to the remaining validity.",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif query.data.startswith("user_apply_plan_"):
        _, _, _, user_id_text, key = query.data.split("_", 4)
        user_id = int(user_id_text)
        plan = (context.user_data.get("manage_sub_plans") or {}).get(key)
        if not plan:
            await query.answer("Plan selection expired. Open User Management again.", show_alert=True)
            return
        current = await get_subscription(user_id)
        if current and current.get("active"):
            expiry = await renew_subscription(user_id=user_id, duration_minutes=plan["duration_minutes"])
        else:
            expiry = await activate_subscription(
                user_id=user_id,
                plan_name=plan["name"],
                duration_minutes=plan["duration_minutes"],
            )
        await grant_channel_access(user_id)
        context.user_data.clear()
        user = await get_user(user_id)
        await show_user_details(query, user)

    elif query.data.startswith("user_custom_sub_"):
        user_id = int(query.data.replace("user_custom_sub_", ""))
        context.user_data.clear()
        context.user_data["manage_sub_custom_user"] = user_id
        await query.edit_message_text(
            "⌨️ Custom Subscription Duration\n\n"
            "Send duration in one of these formats:\n"
            "• Minutes: 30m\n"
            "• Hours: 12h\n"
            "• Days: 7d\n"
            "• Months: 3mo\n"
            "• Years: 1y\n\n"
            "Note: 1 month = 30 days and 1 year = 365 days.",
            reply_markup=back_keyboard(),
        )

    elif query.data.startswith("user_remove_sub_"):
        user_id = int(query.data.replace("user_remove_sub_", ""))

        await expire_subscription(user_id)
        await revoke_channel_access(user_id)

        user = await get_user(user_id)
        await show_user_details(query, user)

    elif query.data.startswith("owner_su_"):
        parts=query.data.split("_")
        if len(parts)!=5:
            await query.answer("Invalid user action",show_alert=True)
            return
        seller_owner_id=int(parts[2]); user_id=int(parts[3]); action=parts[4]
        user=await get_seller_user(seller_owner_id,user_id)
        if not user:
            await query.edit_message_text("❌ Clone bot user not found.",reply_markup=back_keyboard())
            return
        if action=="view":
            await show_seller_user_details(query,seller_owner_id,user)
            return
        if action=="manage":
            context.user_data.clear()
            context.user_data["owner_seller_sub_action"]={"owner_id":seller_owner_id,"user_id":user_id,"action":"manage"}
            await query.edit_message_text(
                "🎁 Give / Extend Clone Bot Subscription\n\n"
                "Send a custom duration:\n"
                "30m, 12h, 7d, 3mo or 1y.\n\n"
                "Existing active validity will be preserved and the new duration will be added.",
                reply_markup=back_keyboard(),
            )
            return
        if action=="remove":
            await seller_collection(SELLER_SUBS).update_one({"owner_id":seller_owner_id,"user_id":user_id},{"$set":{"active":False}})
        elif action=="ban":
            await seller_collection(SELLER_USERS).update_one({"owner_id":seller_owner_id,"user_id":user_id},{"$set":{"banned":True,"ban_reason":"Banned by platform owner"}})
        elif action=="unban":
            await seller_collection(SELLER_USERS).update_one({"owner_id":seller_owner_id,"user_id":user_id},{"$set":{"banned":False,"ban_reason":""}})
        user=await get_seller_user(seller_owner_id,user_id)
        await show_seller_user_details(query,seller_owner_id,user)
        return

    elif query.data == "admin_add_channel":
        context.user_data.clear()
        context.user_data["waiting_channel"] = True

        await query.edit_message_text(
            "📢 Forward any message from your channel/group.\n\n"
            "⚠ Bot must be admin there."
        )

    elif query.data == "admin_channels":
        channels = await get_all_channels()

        if not channels:
            await query.edit_message_text(
                "📋 No channel/group added yet.",
                reply_markup=back_keyboard(),
            )
            return

        text = "📋 Added Channels/Groups:\n\n"
        keyboard = []

        for channel in channels:
            chat_id = channel.get("chat_id")
            title = channel.get("title", "Unknown")
            plans = channel.get("plans", [])

            text += f"• {title}\nID: {chat_id}\n"

            if plans:
                for plan in plans:
                    text += f"  - {plan.get('duration_text')} = ₹{plan.get('price')}\n"
            else:
                text += "  - No plans set\n"

            text += "\n"

            keyboard.append([
                InlineKeyboardButton(
                    f"❌ Remove {title}",
                    callback_data=f"admin_remove_{chat_id}",
                )
            ])

        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="main_owner_dashboard")])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data.startswith("admin_remove_"):
        chat_id = int(query.data.replace("admin_remove_", ""))

        await remove_channel(chat_id)

        await query.edit_message_text(
            "✅ Channel/Group removed successfully.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "admin_payment_settings":
        upi = await get_setting("upi_id")
        name = await get_setting("upi_name")
        qr = await get_setting("upi_qr_file_id")

        text = (
            "💳 Payment Settings\n\n"
            f"👤 UPI Name: {name['value'] if name else 'Not Set'}\n"
            f"🏦 UPI ID: {upi['value'] if upi else 'Not Set'}\n"
            f"🖼 QR Code: {'✅ Added' if qr else '❌ Not Added'}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏ Set UPI ID", callback_data="set_upi_id")],
            [InlineKeyboardButton("👤 Set UPI Name", callback_data="set_upi_name")],
            [InlineKeyboardButton("🖼 Upload QR", callback_data="set_upi_qr")],
            [InlineKeyboardButton("⬅ Back", callback_data="main_owner_dashboard")],
        ])

        await query.edit_message_text(text, reply_markup=keyboard)

    elif query.data == "set_upi_id":
        context.user_data.clear()
        context.user_data["waiting_upi_id"] = True

        await query.edit_message_text(
            "🏦 Send the new UPI ID.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_upi_name":
        context.user_data.clear()
        context.user_data["waiting_upi_name"] = True

        await query.edit_message_text(
            "👤 Send the new UPI Name.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_upi_qr":
        context.user_data.clear()
        context.user_data["waiting_upi_qr"] = True

        await query.edit_message_text(
            "🖼 Send the QR Code image.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "admin_bot_settings":
        bot_name = await get_setting_value(
            "bot_name",
            "Subscription Bot",
        )
        support_username = await get_setting_value(
            "support_username",
            "Not Set",
        )
        currency = await get_setting_value(
            "currency",
            "INR",
        )
        timezone_name = await get_setting_value(
            "timezone",
            "Asia/Kolkata",
        )
        reminder_days = await get_setting_value(
            "reminder_days",
            1,
        )

        text = (
            "⚙️ Bot Settings\n\n"
            f"🤖 Bot Name: {bot_name}\n"
            f"📞 Support: {support_username or 'Not Set'}\n"
            f"💵 Currency: {currency}\n"
            f"🕒 Timezone: {timezone_name}\n"
            f"🔔 Reminder: {reminder_days} day(s)"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🤖 Bot Name",
                    callback_data="set_bot_name",
                )
            ],
            [
                InlineKeyboardButton(
                    "💬 Welcome Message",
                    callback_data="set_welcome_message",
                )
            ],
            [
                InlineKeyboardButton(
                    "📞 Support Username",
                    callback_data="set_support_username",
                )
            ],
            [
                InlineKeyboardButton(
                    "💵 Currency",
                    callback_data="set_currency",
                ),
                InlineKeyboardButton(
                    "🕒 Timezone",
                    callback_data="set_timezone",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔔 Reminder Days",
                    callback_data="set_reminder_days",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅ Back",
                    callback_data="main_owner_dashboard",
                )
            ],
        ])

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )

    elif query.data == "set_bot_name":
        context.user_data.clear()
        context.user_data["waiting_bot_name"] = True

        await query.edit_message_text(
            "🤖 Send the new Bot Name.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_support_username":
        context.user_data.clear()
        context.user_data["waiting_support_username"] = True

        await query.edit_message_text(
            "📞 Send the new Support Username.\n\nExample:\n@YourSupport",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_currency":
        context.user_data.clear()
        context.user_data["waiting_currency"] = True

        await query.edit_message_text(
            "💵 Send currency.\n\nExample:\nINR",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_timezone":
        context.user_data.clear()
        context.user_data["waiting_timezone"] = True

        await query.edit_message_text(
            "🕒 Send timezone.\n\nExample:\nAsia/Kolkata",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_reminder_days":
        context.user_data.clear()
        context.user_data["waiting_reminder_days"] = True

        await query.edit_message_text(
            "🔔 Send reminder days.\n\nExample:\n1",
            reply_markup=back_keyboard(),
        )

    elif query.data == "set_welcome_message":
        context.user_data.clear()
        context.user_data["waiting_welcome_message"] = True

        await query.edit_message_text(
            "💬 Send the new Welcome Message.",
            reply_markup=back_keyboard(),
        )
    elif query.data == "admin_stats":
        await _render_main_statistics(query)

    elif query.data == "admin_stats_refresh":
        await _render_main_statistics(query, force=True)

    elif query.data in {"admin_stats_lifetime", "admin_stats_today", "admin_stats_users"}:
        kind = query.data.removeprefix("admin_stats_")
        await _render_ranking(query, kind)

    elif query.data in {
        "admin_stats_lifetime_refresh",
        "admin_stats_today_refresh",
        "admin_stats_users_refresh",
    }:
        kind = query.data.removeprefix("admin_stats_").removesuffix("_refresh")
        await _render_ranking(query, kind, force=True)

    elif query.data == "admin_broadcast":
        await query.edit_message_text(
            "📢 Broadcast\n\n"
            "Use command:\n"
            "/broadcast\n\n"
            "Then send text, photo, video, document, or forwarded message.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "admin_commands":
        await query.edit_message_text(
            "👮 Admin Commands\n\n"
            "/admin\n"
            "/addadmin USER_ID\n"
            "/removeadmin USER_ID\n"
            "/addchannel\n"
            "/removechannel CHAT_ID\n"
            "/stats\n"
            "/broadcast",
            reply_markup=back_keyboard(),
        )

    elif query.data in ["admin_back", "admin_home", "admin_panel"]:
        context.user_data.clear()
        from handlers.main_dashboard import owner_dashboard_keyboard, owner_dashboard_text

        await query.edit_message_text(
            await owner_dashboard_text(),
            reply_markup=owner_dashboard_keyboard(),
        )


# ---------------------------------------------------------------------------
# Owner Main Statistics
# ---------------------------------------------------------------------------

_MAIN_STATS_CACHE = {"expires": 0.0, "data": None}
_MAIN_STATS_CACHE_TTL = 15.0


def _stats_dt(value):
    if not value:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _owner_main_statistics(*, force=False):
    """Build platform-wide clone/seller statistics with batched Mongo queries."""
    now_ts = time.monotonic()
    if not force and _MAIN_STATS_CACHE["data"] is not None and now_ts < _MAIN_STATS_CACHE["expires"]:
        return _MAIN_STATS_CACHE["data"]

    db = get_database()
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(IST)
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)

    # Read the relatively small control collections once. Heavy statistics
    # are aggregated by MongoDB rather than doing one query per bot/seller.
    bots_task = db["seller_bots"].find(
        {"status": {"$ne": "removed"}},
        {
            "owner_id": 1, "seller_account_id": 1, "data_owner_id": 1,
            "bot_id": 1, "bot_name": 1, "bot_username": 1,
            "active": 1, "runtime_status": 1, "status": 1,
        },
    ).to_list(length=None)
    sellers_task = db["sellers"].find(
        {},
        {"owner_id": 1, "first_name": 1, "username": 1, "active": 1, "updated_at": 1},
    ).to_list(length=None)

    user_task = db["seller_users"].aggregate([
        {"$group": {"_id": "$owner_id", "count": {"$sum": 1}}}
    ]).to_list(length=None)
    active_task = db["seller_subscriptions"].aggregate([
        {"$match": {"active": True, "expiry_date": {"$gt": now}}},
        {"$group": {"_id": "$owner_id", "count": {"$sum": 1}}}
    ]).to_list(length=None)
    payment_task = db["seller_payments"].aggregate([
        {"$match": {"status": {"$in": ["approved", "paid", "success"]}}},
        {"$group": {
            "_id": "$owner_id",
            "successful": {"$sum": 1},
            "revenue": {"$sum": {"$ifNull": ["$amount", 0]}},
            "today_revenue": {"$sum": {
                "$cond": [
                    {"$or": [
                        {"$gte": ["$processed_at", start_utc]},
                        {"$gte": ["$created_at", start_utc]},
                    ]},
                    {"$ifNull": ["$amount", 0]},
                    0,
                ]
            }},
            "today_successful": {"$sum": {
                "$cond": [
                    {"$or": [
                        {"$gte": ["$processed_at", start_utc]},
                        {"$gte": ["$created_at", start_utc]},
                    ]},
                    1,
                    0,
                ]
            }},
        }}
    ]).to_list(length=None)
    pending_task = db["seller_payments"].aggregate([
        {"$match": {"status": "pending"}},
        {"$group": {"_id": "$owner_id", "count": {"$sum": 1}}}
    ]).to_list(length=None)
    channel_task = db["seller_channels"].aggregate([
        {"$match": {"active": True}},
        {"$group": {
            "_id": {"owner_id": "$owner_id", "chat_type": "$chat_type"},
            "count": {"$sum": 1},
        }}
    ]).to_list(length=None)

    bots, sellers, user_rows, active_rows, payment_rows, pending_rows, channel_rows = await asyncio.gather(
        bots_task, sellers_task, user_task, active_task,
        payment_task, pending_task, channel_task,
    )

    def group_map(rows):
        out = {}
        for row in rows:
            key = row.get("_id")
            if key is None:
                continue
            out[key] = row
        return out

    users_by_scope = {r["_id"]: int(r.get("count", 0)) for r in user_rows if r.get("_id") is not None}
    active_by_scope = {r["_id"]: int(r.get("count", 0)) for r in active_rows if r.get("_id") is not None}
    pending_by_scope = {r["_id"]: int(r.get("count", 0)) for r in pending_rows if r.get("_id") is not None}
    payments_by_scope = {}
    for r in payment_rows:
        if r.get("_id") is not None:
            payments_by_scope[r["_id"]] = {
                "successful": int(r.get("successful", 0)),
                "revenue": float(r.get("revenue", 0) or 0),
                "today_revenue": float(r.get("today_revenue", 0) or 0),
                "today_successful": int(r.get("today_successful", 0)),
            }

    channels_by_scope = {}
    total_groups = total_channels = 0
    for r in channel_rows:
        key = r.get("_id") or {}
        scope = key.get("owner_id")
        ctype = str(key.get("chat_type") or "").lower()
        count = int(r.get("count", 0))
        if scope is None:
            continue
        entry = channels_by_scope.setdefault(scope, {"groups": 0, "channels": 0})
        if ctype == "channel":
            entry["channels"] += count
            total_channels += count
        else:
            entry["groups"] += count
            total_groups += count

    # Every clone maps its data scope back to the seller account.
    seller_by_id = {int(x.get("owner_id")): x for x in sellers if x.get("owner_id") is not None}
    scope_to_seller = {}
    clone_rows = []
    for bot in bots:
        try:
            seller_id = int(bot.get("owner_id") or bot.get("seller_account_id") or 0)
        except (TypeError, ValueError):
            seller_id = 0
        try:
            scope = int(bot.get("data_owner_id") or seller_id)
        except (TypeError, ValueError):
            scope = seller_id
        if seller_id:
            scope_to_seller[scope] = seller_id

        runtime = str(bot.get("runtime_status") or "").lower()
        is_running = bool(bot.get("active")) and runtime == "running"
        bot_id = int(bot.get("bot_id") or 0)
        clone_rows.append({
            "bot_id": bot_id,
            "bot_name": str(bot.get("bot_name") or bot.get("bot_username") or f"Bot {bot_id}"),
            "bot_username": str(bot.get("bot_username") or "").lstrip("@"),
            "seller_id": seller_id,
            "scope": scope,
            "running": is_running,
            "users": users_by_scope.get(scope, 0),
        })

    # Combine all clone scopes for each seller.
    seller_stats = {}
    for bot in clone_rows:
        sid = bot["seller_id"]
        if not sid:
            continue
        st = seller_stats.setdefault(sid, {
            "users": 0, "active": 0, "pending": 0, "successful": 0,
            "today_revenue": 0.0, "revenue": 0.0, "today_successful": 0,
            "running": 0, "stopped": 0, "bots": [],
        })
        scope = bot["scope"]
        st["users"] += users_by_scope.get(scope, 0)
        st["active"] += active_by_scope.get(scope, 0)
        st["pending"] += pending_by_scope.get(scope, 0)
        pay = payments_by_scope.get(scope, {})
        st["successful"] += int(pay.get("successful", 0))
        st["today_successful"] += int(pay.get("today_successful", 0))
        st["today_revenue"] += float(pay.get("today_revenue", 0))
        st["revenue"] += float(pay.get("revenue", 0))
        if bot["running"]:
            st["running"] += 1
        else:
            st["stopped"] += 1
        st["bots"].append(bot)

    # Seller activity: "today active/total" uses today's updated seller records.
    today_active_sellers = sum(
        1 for seller in sellers
        if bool(seller.get("active")) and (_stats_dt(seller.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= start_utc
    )

    total_users = sum(users_by_scope.values())
    total_active = sum(active_by_scope.values())
    total_pending = sum(pending_by_scope.values())
    total_successful = sum(v["successful"] for v in payments_by_scope.values())
    total_today_successful = sum(v["today_successful"] for v in payments_by_scope.values())
    total_revenue = sum(v["revenue"] for v in payments_by_scope.values())
    total_today_revenue = sum(v["today_revenue"] for v in payments_by_scope.values())

    configured = len(clone_rows)
    running = sum(1 for b in clone_rows if b["running"])
    offline_error = max(0, configured - running)

    data = {
        "configured": configured,
        "running": running,
        "offline_error": offline_error,
        "seller_today": today_active_sellers,
        "seller_total": len(sellers),
        "total_users": total_users,
        "active_subscribers": total_active,
        "groups": total_groups,
        "channels": total_channels,
        "today_revenue": total_today_revenue,
        "today_successful": total_today_successful,
        "successful": total_successful,
        "revenue": total_revenue,
        "sellers": seller_stats,
        "clones": clone_rows,
        "seller_by_id": seller_by_id,
    }
    _MAIN_STATS_CACHE["data"] = data
    _MAIN_STATS_CACHE["expires"] = time.monotonic() + _MAIN_STATS_CACHE_TTL
    return data


def _seller_display(stats, seller_id, seller_by_id):
    seller = seller_by_id.get(int(seller_id), {})
    name = str(seller.get("first_name") or seller.get("username") or "Unknown")
    username = str(seller.get("username") or "").lstrip("@")
    mention = f'<a href="tg://user?id={int(seller_id)}">{escape(name)}</a>'
    return name, (f"@{escape(username)}" if username else "-"), mention


def _ranking_keyboard(kind):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_stats_{kind}_refresh"),
            InlineKeyboardButton("⬅ Back", callback_data="admin_stats"),
        ]
    ])


async def _render_main_statistics(query, *, force=False):
    data = await _owner_main_statistics(force=force)
    text = (
        "📊 <b>Main Statistics</b>\n\n"
        "🤖 <b>Clone Bots</b>\n"
        f"• Configured: {data['configured']}\n"
        f"• Running: 🟢 {data['running']}\n"
        f"• Offline/Error: 🔴 {data['offline_error']}\n\n"
        "📈 <b>Platform Usage</b>\n"
        f"Seller: {data['seller_today']}/{data['seller_total']}\n"
        f"Total users: {data['total_users']}\n"
        f"Active subscriber: {data['active_subscribers']}\n"
        f"Connected group: {data['groups']}\n"
        f"Connected Channel: {data['channels']}\n\n"
        "💳 <b>Payments</b>\n"
        f"• Today: ₹{data['today_revenue']:.2f} ({data['today_successful']} payments)\n"
        f"• Total Successful: {data['successful']}\n"
        f"• Total Revenue: ₹{data['revenue']:.2f}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Lifetime Seller Ranking", callback_data="admin_stats_lifetime")],
        [InlineKeyboardButton("📅 Today Seller Ranking", callback_data="admin_stats_today")],
        [InlineKeyboardButton("👥 Top 10 Clone Bot Highest User", callback_data="admin_stats_users")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats_refresh")],
        [InlineKeyboardButton("⬅ Owner Dashboard", callback_data="main_owner_dashboard")],
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def _render_ranking(query, kind, *, force=False):
    data = await _owner_main_statistics(force=force)
    sellers = data["sellers"]
    seller_by_id = data["seller_by_id"]

    if kind in {"lifetime", "today"}:
        key = "revenue" if kind == "lifetime" else "today_revenue"
        title = "🏆 <b>Lifetime Seller Ranking</b>" if kind == "lifetime" else "📅 <b>Today Seller Ranking</b>"
        ranked = sorted(
            ((sid, st) for sid, st in sellers.items()),
            key=lambda item: (-float(item[1].get(key, 0)), -int(item[1].get("users", 0)), int(item[0])),
        )
    else:
        title = "👥 <b>Top 10 Clone Bot Highest User</b>"
        ranked = sorted(
            data["clones"],
            key=lambda b: (-int(b.get("users", 0)), -float(sellers.get(b.get("seller_id"), {}).get("revenue", 0)), int(b.get("bot_id", 0))),
        )[:10]

    if not ranked:
        text = f"{title}\n\nNo statistics available yet."
    else:
        blocks = []
        for rank, item in enumerate(ranked[:10], 1):
            if kind in {"lifetime", "today"}:
                sid, st = item
                bot_list = st.get("bots", [])
                first_bot = bot_list[0] if bot_list else {}
                bot_name = first_bot.get("bot_name") or "-"
                bot_username = first_bot.get("bot_username") or "-"
                bot_id = first_bot.get("bot_id") or "-"
                name, username, mention = _seller_display(st, sid, seller_by_id)
                block = (
                    f"<b>{rank}. {escape(str(bot_name))}</b>\n"
                    f"Bot username : @{escape(str(bot_username).lstrip('@')) if bot_username != '-' else '-'}\n"
                    f"Clone bot id: {bot_id}\n"
                    f"Seller name: {escape(name)}\n"
                    f"Seller mention: {mention}\n"
                    f"Seller id: {sid}\n\n"
                    "📊 <b>Seller Statistics — Combined</b>\n"
                    f"🤖 Running Bots: {st['running']} | Stopped: {st['stopped']}\n"
                    f"👥 Total Users: {st['users']}\n"
                    f"💳 Pending Payments: {st['pending']}\n"
                    f"✅ Successful Payments: {st['successful']}\n"
                    f"💰 Today Revenue: ₹{st['today_revenue']:.2f}\n"
                    f"💰 Total Revenue: ₹{st['revenue']:.2f}"
                )
            else:
                bot = item
                sid = int(bot.get("seller_id") or 0)
                st = sellers.get(sid, {
                    "running": 1 if bot.get("running") else 0, "stopped": 0 if bot.get("running") else 1,
                    "users": bot.get("users", 0), "pending": 0, "successful": 0,
                    "today_revenue": 0, "revenue": 0,
                })
                name, username, mention = _seller_display(st, sid, seller_by_id) if sid else ("Unknown", "-", "-")
                block = (
                    f"<b>{rank}. {escape(str(bot.get('bot_name') or '-'))}</b>\n"
                    f"Bot username : @{escape(str(bot.get('bot_username') or '-').lstrip('@')) if bot.get('bot_username') else '-'}\n"
                    f"Clone bot id: {int(bot.get('bot_id') or 0)}\n"
                    f"Seller name: {escape(name)}\n"
                    f"Seller mention: {mention}\n"
                    f"Seller id: {sid or '-'}\n\n"
                    "📊 <b>Seller Statistics — Combined</b>\n"
                    f"🤖 Running Bots: {st.get('running',0)} | Stopped: {st.get('stopped',0)}\n"
                    f"👥 Total Users: {st.get('users',0)}\n"
                    f"💳 Pending Payments: {st.get('pending',0)}\n"
                    f"✅ Successful Payments: {st.get('successful',0)}\n"
                    f"💰 Today Revenue: ₹{st.get('today_revenue',0):.2f}\n"
                    f"💰 Total Revenue: ₹{st.get('revenue',0):.2f}"
                )
            blocks.append(block)

        text = title + "\n\n" + "\n\n".join(blocks)
        # Telegram text limit is 4096. Keep all ranking entries compact and
        # trim only if an unusual seller name makes the message too long.
        if len(text) > 4000:
            text = text[:3990] + "\n…"

    await query.edit_message_text(
        text,
        reply_markup=_ranking_keyboard(kind),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def receive_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    text = update.message.text.strip()

    if context.user_data.get("waiting_bot_name"):
        await set_setting("bot_name", text)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Bot Name updated successfully!\n\nNew Name: {text}",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_welcome_message"):
        await set_setting("welcome_message", text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Welcome Message updated successfully!",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_support_username"):
        value = text if text.startswith("@") else f"@{text}"
        await set_setting("support_username", value)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Support Username updated!\n\n{value}",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_currency"):
        value = text.upper()
        await set_setting("currency", value)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Currency updated!\n\n{value}",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_timezone"):
        try:
            timezone_name = normalize_timezone(text)
        except Exception:
            await update.message.reply_text(
                "❌ Invalid timezone.\n\nUse the exact format, for example:\nAsia/Kolkata\n\nTimezone names are case-sensitive.",
                reply_markup=timezone_keyboard("admin_tz_", "admin_settings"),
            )
            return
        await set_setting("timezone", timezone_name)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Timezone updated!\n\n{timezone_name}",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_reminder_days"):
        try:
            days = int(text)
            if not 0 <= days <= 365:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Send a number from 0 to 365."
            )
            return
        await set_setting("reminder_days", days)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Reminder Days updated!\n\n{days} day(s)",
            reply_markup=admin_keyboard(),
        )
        return

    if context.user_data.get("waiting_upi_id"):
        await set_setting("upi_id", text)
        context.user_data.clear()
        await update.message.reply_text("✅ UPI ID updated successfully.")
        return

    if context.user_data.get("waiting_upi_name"):
        await set_setting("upi_name", text)
        context.user_data.clear()
        await update.message.reply_text("✅ UPI Name updated successfully.")
        return

    if context.user_data.get("owner_seller_sub_action"):
        data=context.user_data["owner_seller_sub_action"]
        try:
            duration_minutes,_=parse_plan_time(text.lower())
            owner_id=int(data["owner_id"]); user_id=int(data["user_id"])
            current=await get_seller_subscription(owner_id,user_id) or {}
            plan_name=current.get("plan") or "Owner Assigned"
            expiry=await activate_seller_subscription(owner_id,user_id,plan_name,duration_minutes,amount=0,duration_text=text.lower())
            delivery=await bot_manager.deliver_subscription_access(owner_id,user_id)
            context.user_data.clear()
            await update.message.reply_text(
                "✅ Clone bot user subscription updated successfully.\n\n"
                f"🏪 Seller ID: {owner_id}\n"
                f"👤 User ID: {user_id}\n"
                f"⏳ Duration added: {text.lower()}\n"
                f"📅 New expiry: {format_time(expiry)}\n"
                f"🔗 New invite links sent: {delivery.get('sent',0)}\n"
                f"✅ Already joined chats: {delivery.get('already_member',0)}\n"
                f"⚠️ Failed links: {delivery.get('failed',0)}",
                reply_markup=back_keyboard(),
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Could not update subscription.\n\nError: {exc}")
        return

    if context.user_data.get("manage_sub_custom_user"):
        duration_text = text.lower().strip()
        user_id = int(context.user_data["manage_sub_custom_user"])
        try:
            duration_minutes, _ = parse_plan_time(duration_text)
            current = await get_subscription(user_id)
            if current and current.get("active"):
                expiry = await renew_subscription(user_id=user_id, duration_minutes=duration_minutes)
                action_text = "extended"
            else:
                expiry = await activate_subscription(
                    user_id=user_id,
                    plan_name="Custom Subscription",
                    duration_minutes=duration_minutes,
                )
                action_text = "given"

            await grant_channel_access(user_id)
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Subscription {action_text} successfully!\n\n"
                f"👤 User ID: {user_id}\n"
                f"⏳ Duration added: {duration_text}\n"
                f"📅 New expiry: {format_time(expiry)}",
                reply_markup=back_keyboard(),
            )
        except Exception as e:
            await update.message.reply_text(
                "❌ Invalid duration.\n\n"
                "Use: 30m, 12h, 7d, 3mo or 1y.\n"
                "m = minutes, h = hours, d = days, mo = months, y = years.\n\n"
                f"Error: {e}"
            )
        return

    if context.user_data.get("waiting_user_search"):
        search=text.strip()
        main_user=None
        if search.startswith("@"):
            main_user=await get_user_by_username(search)
        else:
            try:
                main_user=await get_user(int(search))
            except Exception:
                main_user=None
        seller_users=await find_seller_users(search)
        context.user_data["waiting_user_search"]=False

        if seller_users:
            if len(seller_users)==1:
                item=seller_users[0]
                class FakeQuery:
                    async def edit_message_text(self,text,reply_markup=None,**kwargs):
                        return await update.message.reply_text(text,reply_markup=reply_markup)
                await show_seller_user_details(FakeQuery(),int(item["owner_id"]),item)
                return
            rows=[]
            for item in seller_users:
                bot_record=await get_seller_bot(int(item["owner_id"])) or {}
                label=f"@{bot_record.get('bot_username','clone_bot')} — {item.get('user_id')}"
                rows.append([InlineKeyboardButton(label[:60],callback_data=f"owner_su_{item['owner_id']}_{item['user_id']}_view")])
            rows.append([InlineKeyboardButton("⬅ Back",callback_data="admin_users")])
            await update.message.reply_text("Multiple clone-bot user records found. Choose one:",reply_markup=InlineKeyboardMarkup(rows))
            return

        if main_user:
            details=await build_user_details_text(main_user)
            await update.message.reply_text(details,reply_markup=user_action_keyboard(main_user["user_id"],bool(main_user.get("banned"))))
            return

        await update.message.reply_text("❌ User not found in the main bot or any seller clone bot.",reply_markup=back_keyboard())
        return


async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the owner channel/group connection flow."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    context.user_data.clear()
    context.user_data["waiting_channel"] = True
    await update.message.reply_text(
        "📢 Add Channel / Group\n\n"
        "1. Add the main bot as an administrator.\n"
        "2. Forward any message from the target channel/group here.\n\n"
        "The bot will detect the chat automatically.",
        reply_markup=back_keyboard(),
    )


async def receive_upi_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the owner UPI QR image when Payment Settings requests it."""
    if not update.effective_user or not await is_admin(update.effective_user.id):
        return
    if not context.user_data.get("waiting_upi_qr"):
        return
    if not update.message or not update.message.photo:
        await update.effective_message.reply_text(
            "❌ Please send the QR code as a photo.",
            reply_markup=back_keyboard(),
        )
        return

    file_id = update.message.photo[-1].file_id
    await set_setting("upi_qr_file_id", file_id)
    context.user_data.clear()
    await update.message.reply_text(
        "✅ UPI QR code updated successfully.",
        reply_markup=admin_keyboard(),
    )


async def receive_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    if not context.user_data.get("waiting_channel"):
        return

    message = update.message
    chat = getattr(message, "forward_from_chat", None)

    if chat is None:
        origin = getattr(message, "forward_origin", None)
        chat = getattr(origin, "chat", None)

    if chat is None:
        await message.reply_text(
            "❌ Channel/group detect nahi hua.\n\n"
            "Please channel/group se message forward karo."
        )
        return

    context.user_data["pending_channel"] = {
        "chat_id": chat.id,
        "title": chat.title or "Unknown",
    }

    context.user_data["waiting_channel"] = False
    context.user_data["waiting_plans"] = True

    await message.reply_text(
        f"✅ Channel detected!\n\n"
        f"Title: {chat.title}\n"
        f"ID: {chat.id}\n\n"
        "Now send plans:\n\n"
        "Example:\n"
        "5m:10, 1h:20, 1d:99\n\n"
        "m = minutes\n"
        "h = hours\n"
        "d = days"
    )


async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage:\n/removechannel CHAT_ID")
        return

    await remove_channel(int(context.args[0]))
    await update.message.reply_text("✅ Channel removed successfully.")


async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage:\n/addadmin USER_ID")
        return

    await add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin added successfully.")


async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage:\n/removeadmin USER_ID")
        return

    await remove_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin removed successfully.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    users = await total_users()
    channels = await total_channels()
    revenue = await total_revenue()

    await update.message.reply_text(
        f"📊 Bot Statistics\n\n"
        f"👤 Users: {users}\n"
        f"📢 Channels: {channels}\n"
        f"💰 Revenue: ₹{revenue}"
    )


def admin_handlers():
    return [
        CommandHandler("admin", admin_panel),
        CommandHandler("stats", stats_command),
        CommandHandler("addadmin", add_admin_command),
        CommandHandler("removeadmin", remove_admin_command),
        CommandHandler("addchannel", add_channel_start),
        CommandHandler("removechannel", remove_channel_command),
        CallbackQueryHandler(admin_buttons, pattern=r"^(admin_|user_|owner_su_|set_upi_|set_timezone$|set_bot_name$|set_welcome_message$|set_support_username$|set_currency$|set_reminder_days$)"),
        MessageHandler(filters.FORWARDED, receive_channel_forward),
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_text),
    ]
