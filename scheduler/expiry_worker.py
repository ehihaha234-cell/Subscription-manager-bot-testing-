from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database.subscriptions import (
    claim_expired_subscription,
    claim_expiry_notification,
    complete_expiry_claim,
    complete_expiry_notification,
    get_expired_subscriptions,
    release_expiry_claim,
    release_expiry_notification,
)
from services.channel_service import (
    revoke_channel_access,
    send_expiry_notification,
)
from database.seller_data import (
    active_plan_group_subscriptions_for_chat,
    expire_plan_group_subscription,
    expired_plan_group_subscriptions,
    get_subscription as get_seller_subscription,
    active_expiry_reminder_plan_group_subscriptions,
    claim_plan_group_expiry_reminder,
    complete_plan_group_expiry_reminder,
    release_plan_group_expiry_reminder,
    get_channels as get_seller_channels,
    get_plan_group,
    get_seller_settings,
)
from database.subscription_guard import is_whitelisted
from logging_config import get_logger

logger = get_logger(__name__)


async def check_expired_users():
    now = datetime.now(timezone.utc)
    try:
        await check_expired_plan_group_subscriptions()
    except Exception:
        logger.exception("Plan Group subscription expiry worker failed")
    subscriptions = await get_expired_subscriptions(now)

    for snapshot in subscriptions:
        user_id = snapshot.get("user_id")
        if user_id is None:
            continue

        claimed = await claim_expired_subscription(
            user_id,
            now=datetime.now(timezone.utc),
            stale_after_seconds=900,
        )
        if not claimed:
            continue

        claim_token = claimed["_claim_token"]
        expected_expiry = claimed.get("expiry_date")

        try:
            result = await revoke_channel_access(
                user_id,
                send_notification=False,
            )

            # Do not mark the subscription expired while any configured
            # channel removal failed. The claim is released for a later retry.
            if result["failed_chat_ids"]:
                await release_expiry_claim(
                    user_id,
                    claim_token,
                    error=(
                        "Failed channel removals: "
                        + ",".join(
                            str(chat_id)
                            for chat_id in result["failed_chat_ids"]
                        )
                    ),
                )
                logger.warning(
                    "Expiry retry scheduled user_id=%s failed_channels=%s",
                    user_id,
                    result["failed_chat_ids"],
                )
                continue

            completed = await complete_expiry_claim(
                user_id,
                claim_token,
                expected_expiry,
            )
            if not completed:
                logger.info(
                    "Expiry finalization skipped because subscription "
                    "changed user_id=%s",
                    user_id,
                )
                continue

            notification_token = await claim_expiry_notification(user_id)
            if notification_token:
                sent = await send_expiry_notification(
                    user_id,
                    removed=result["removed"],
                )
                if sent:
                    await complete_expiry_notification(
                        user_id,
                        notification_token,
                    )
                else:
                    await release_expiry_notification(
                        user_id,
                        notification_token,
                        error="Telegram notification failed",
                    )

            logger.info(
                "Expired subscription processed user_id=%s expiry=%s "
                "removed=%s",
                user_id,
                expected_expiry,
                result["removed"],
            )

        except Exception as exc:
            logger.exception(
                "Failed processing expired subscription user_id=%s",
                user_id,
            )
            try:
                await release_expiry_claim(
                    user_id,
                    claim_token,
                    error=str(exc),
                )
            except Exception:
                logger.exception(
                    "Failed releasing expiry claim user_id=%s",
                    user_id,
                )


async def check_expired_plan_group_subscriptions():
    """Handle Plan Group reminders and expiry without touching clone-wide access."""
    now = datetime.now(timezone.utc)
    from services.bot_manager import bot_manager

    # Pre-expiry reminders are Plan Group specific.
    owner_ids = set()
    try:
        from database.seller_data import get_database
        owner_rows = await get_database()["seller_plan_group_subscriptions"].distinct("owner_id", {"active": True})
        owner_ids.update(int(x) for x in owner_rows if x is not None)
    except Exception:
        logger.exception("Unable to load Plan Group reminder owners")

    for owner_id in owner_ids:
        try:
            settings = await get_seller_settings(owner_id)
            try:
                reminder_days = max(0, int(settings.get("reminder_days", 1) or 0))
            except (TypeError, ValueError):
                reminder_days = 1
            if reminder_days > 0:
                timezone_name = str(settings.get("timezone") or "Asia/Kolkata")
                try:
                    display_tz = ZoneInfo(timezone_name)
                except (ZoneInfoNotFoundError, ValueError):
                    display_tz = ZoneInfo("Asia/Kolkata")
                channels = {int(x.get("chat_id")): str(x.get("title") or "Group/Channel") for x in await get_seller_channels(owner_id)}
                running = bot_manager.get_running(owner_id)
                if running:
                    for row in await active_expiry_reminder_plan_group_subscriptions(owner_id, reminder_days):
                        uid = int(row.get("user_id") or 0)
                        gid = str(row.get("group_id") or "")
                        expiry = row.get("expiry_date")
                        if not uid or not gid or not expiry:
                            continue
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=timezone.utc)
                        token = await claim_plan_group_expiry_reminder(owner_id, uid, gid, expiry)
                        if not token:
                            continue
                        group = await get_plan_group(owner_id, gid) or {}
                        target_ids = [int(x) for x in (row.get("target_chat_ids") or group.get("chat_ids") or [])]
                        names = [channels.get(cid, str(cid)) for cid in dict.fromkeys(target_ids)]
                        plan_name = str(row.get("plan") or "Unknown")
                        day_label = "day" if reminder_days == 1 else "days"
                        expiry_local = expiry.astimezone(display_tz)
                        lines = [
                            "⏰ Subscription Expiry Reminder",
                            "",
                            f"Your subscription will expire in {reminder_days} {day_label}.",
                            "",
                            f"📦 Plan: {plan_name}",
                            "🔊 GROUP/CHANNEL:",
                        ]
                        lines.extend(f"• {name}" for name in names)
                        lines.extend([
                            "",
                            f"📅 Expiry Date: {expiry_local.strftime('%d-%m-%Y %I:%M:%S %p')} {timezone_name}",
                            "",
                            "🔄 Please renew your plan before it expires to continue accessing the premium channel/group.",
                        ])
                        try:
                            await running.application.bot.send_message(
                                uid,
                                "\n".join(lines),
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Renew Plan", callback_data=f"c_pg_renew_{gid}")]]),
                            )
                            await complete_plan_group_expiry_reminder(owner_id, uid, gid, token, expiry)
                        except Exception as exc:
                            await release_plan_group_expiry_reminder(owner_id, uid, gid, token, str(exc))
                            logger.exception("Plan Group expiry reminder delivery failed owner=%s user=%s group=%s", owner_id, uid, gid)
        except Exception:
            logger.exception("Plan Group reminder worker failed owner=%s", owner_id)

    rows = await expired_plan_group_subscriptions(limit=5000)
    for row in rows:
        owner_id = int(row.get("owner_id") or 0)
        user_id = int(row.get("user_id") or 0)
        group_id = str(row.get("group_id") or "").strip()
        if not owner_id or not user_id or not group_id:
            continue
        running = bot_manager.get_running(owner_id)
        if not running:
            continue
        bot = running.application.bot
        target_ids = []
        for value in row.get("target_chat_ids") or []:
            try:
                target_ids.append(int(value))
            except (TypeError, ValueError):
                pass
        target_ids = list(dict.fromkeys(target_ids))
        channels = {int(x.get("chat_id")): str(x.get("title") or "Group/Channel") for x in await get_seller_channels(owner_id)}
        removed_names = []
        failed = False
        for chat_id in target_ids:
            # Keep access when the same target is covered by another active Plan Group.
            if await active_plan_group_subscriptions_for_chat(owner_id, user_id, chat_id):
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if getattr(member, "status", "") in {"creator", "administrator"} or await is_whitelisted(owner_id, chat_id, user_id):
                    continue
                await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                removed_names.append(channels.get(chat_id, str(chat_id)))
            except Exception as exc:
                failed = True
                logger.warning("Plan Group expiry removal failed owner=%s user=%s group=%s chat=%s: %s", owner_id, user_id, group_id, chat_id, exc)
        if failed:
            continue
        expired = await expire_plan_group_subscription(owner_id, user_id, group_id, row.get("expiry_date"))
        if not expired:
            continue
        try:
            group = await get_plan_group(owner_id, group_id) or {}
            # Only show chats from this expired Plan Group that were actually removed.
            if removed_names:
                display_names = removed_names
            else:
                display_names = [channels.get(int(x), str(x)) for x in target_ids if not await active_plan_group_subscriptions_for_chat(owner_id, user_id, int(x))]
            settings = await get_seller_settings(owner_id)
            timezone_name = str(settings.get("timezone") or "Asia/Kolkata")
            try:
                display_tz = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError):
                display_tz = ZoneInfo("Asia/Kolkata")
            expiry = row.get("expiry_date")
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            expiry_text = expiry.astimezone(display_tz).strftime('%d-%m-%Y %I:%M:%S %p') + f" {timezone_name}" if expiry else "-"
            lines = ["⏰ Your subscription has expired.", "", "🔊 Group/Channel:"]
            lines.extend(f"• {name}" for name in display_names)
            lines.extend(["", f"📅 Expired On: {expiry_text}", "", "❌ Your access to the applicable group/channel(s) has been removed.", "", "Use 🔄 Renew Plan to continue."])
            await bot.send_message(
                user_id,
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Renew Plan", callback_data=f"c_pg_renew_{group_id}"), InlineKeyboardButton("👤 Profile", callback_data="c_profile")],
                ]),
            )
        except Exception:
            logger.exception("Plan Group expiry notification failed owner=%s user=%s group=%s", owner_id, user_id, group_id)
        logger.info("Plan Group subscription expired owner=%s user=%s group=%s expiry=%s removed=%s", owner_id, user_id, group_id, row.get("expiry_date"), removed_names)

