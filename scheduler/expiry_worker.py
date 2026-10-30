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


async def running_bot_send_warning(chat_id: int, text: str):
    from services.channel_service import bot as main_bot
    await main_bot.send_message(chat_id=chat_id, text=text)
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
from database.channels import get_all_channels
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

            # Expiry and subscriber notification must not be blocked by a
            # failed chat removal. Report each failure to the platform admins;
            # access-removal retry can be handled separately by operations.
            if result["failed_chat_ids"]:
                from config import ADMIN_IDS
                names = {}
                try:
                    names = {int(x.get("chat_id")): str(x.get("title") or x.get("chat_id")) for x in await get_all_channels()}
                except Exception:
                    pass
                details = getattr(result, "get", lambda *_: [])("failed_details", [])
                detail_by_id = {int(x.get("chat_id")): str(x.get("error") or "Unknown Telegram error") for x in details if x.get("chat_id") is not None}
                warning = ["⚠️ Subscription Access Removal Failed", "", f"Subscriber ID: {user_id}"]
                for chat_id in result["failed_chat_ids"]:
                    warning.extend([f"• {names.get(int(chat_id), str(chat_id))}", f"  Chat ID: {chat_id}", f"  Problem: {detail_by_id.get(int(chat_id), 'Bot could not remove the member. Check bot membership and admin permissions.')}"])
                warning.extend(["", "Please add the bot to the group/channel and grant the required Ban Users permission."])
                for admin_id in ADMIN_IDS:
                    try:
                        await running_bot_send_warning(int(admin_id), "\n".join(warning))
                    except Exception:
                        logger.exception("Could not send expiry removal warning admin=%s user=%s", admin_id, user_id)
                logger.warning("Expiry access removal failed; expiry notification will still be sent user_id=%s failed_channels=%s", user_id, result["failed_chat_ids"])

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

        # Revoke links belonging to THIS expired Plan Group first. Do not gate
        # this on another active Plan Group for the same chat: links are created
        # per subscription and old links must not remain reusable after expiry.
        try:
            from database.subscription_invites import (
                active_invites_for_user,
                revoke_subscription_invites,
                mark_invite_revoked,
            )
            invite_rows = await active_invites_for_user(owner_id, user_id)
            group_rows = [
                r for r in invite_rows
                if str(r.get("group_id") or "") == group_id
            ]
            # Revoke links for this user's expired Plan Group using target chat IDs too.
            # Some older/current subscription records can carry a different internal
            # group_id from the invite record; group_id-only matching misses those links.
            # Never revoke a chat's links if another active Plan Group still covers it.
            revoked_count = 0
            candidate_rows = [
                r for r in invite_rows
                if str(r.get("group_id") or "") == group_id
                or int(r.get("chat_id") or 0) in target_ids
            ]
            for invite_row in candidate_rows:
                try:
                    chat_id = int(invite_row.get("chat_id") or 0)
                    invite_link = str(invite_row.get("invite_link") or "")
                    if not chat_id or not invite_link:
                        continue
                    # CRITICAL: the Plan Group subscription being processed may be
                    # expired while this particular invite belongs to a newer payment.
                    # Never revoke a link before its own recorded Telegram expiry.
                    invite_expiry = invite_row.get("expiry_date")
                    if invite_expiry is None:
                        logger.warning(
                            "Skipping Plan Group invite revoke: expiry missing owner=%s user=%s chat=%s link_suffix=%s group=%s",
                            owner_id, user_id, chat_id, invite_link[-12:], invite_row.get("group_id"),
                        )
                        continue
                    if isinstance(invite_expiry, str):
                        try:
                            invite_expiry = datetime.fromisoformat(invite_expiry.replace("Z", "+00:00"))
                        except ValueError:
                            logger.warning("Skipping Plan Group invite revoke: invalid expiry owner=%s user=%s chat=%s link_suffix=%s", owner_id, user_id, chat_id, invite_link[-12:])
                            continue
                    if not isinstance(invite_expiry, datetime):
                        logger.warning("Skipping Plan Group invite revoke: unsupported expiry type owner=%s user=%s chat=%s link_suffix=%s", owner_id, user_id, chat_id, invite_link[-12:])
                        continue
                    if invite_expiry.tzinfo is None:
                        # Mongo legacy naive timestamps in this worker are UTC.
                        invite_expiry = invite_expiry.replace(tzinfo=timezone.utc)
                    else:
                        invite_expiry = invite_expiry.astimezone(timezone.utc)
                    if invite_expiry > datetime.now(timezone.utc):
                        logger.info(
                            "Skipping premature Plan Group invite revoke owner=%s user=%s chat=%s link_suffix=%s invite_expiry=%s",
                            owner_id, user_id, chat_id, invite_link[-12:], invite_expiry.isoformat(),
                        )
                        continue
                    # For links tagged to this group, revoke directly. For links tagged
                    # to another group or legacy untagged links, preserve them if another
                    # active Plan Group covers the same target chat.
                    row_group = str(invite_row.get("group_id") or "")
                    if row_group != group_id and await active_plan_group_subscriptions_for_chat(owner_id, user_id, chat_id):
                        continue
                    try:
                        await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link)
                    except Exception as exc:
                        message = str(exc).lower()
                        already_invalid = any(term in message for term in (
                            "invite link is already revoked", "invite link is invalid",
                            "invite link has expired", "invite link not found",
                        ))
                        if not already_invalid:
                            logger.warning(
                                "Plan Group invite revoke failed owner=%s user=%s chat=%s link_suffix=%s error=%s",
                                owner_id, user_id, chat_id, invite_link[-12:], exc,
                            )
                            continue
                    if await mark_invite_revoked(owner_id, invite_link):
                        revoked_count += 1
                except Exception:
                    logger.exception(
                        "Plan Group invite revoke candidate failed owner=%s user=%s group=%s",
                        owner_id, user_id, group_id,
                    )

            # Compatibility for old records created before group_id was stored.
            # Only use untagged records on this group's targets, and preserve a
            # chat if another still-active Plan Group covers it.
            legacy_rows = [
                r for r in invite_rows
                if not r.get("group_id") and int(r.get("chat_id") or 0) in target_ids
            ]
            legacy_checked = 0
            legacy_revoked = 0
            for invite_row in legacy_rows:
                try:
                    chat_id = int(invite_row.get("chat_id"))
                    invite_link = str(invite_row.get("invite_link") or "")
                    invite_expiry = invite_row.get("expiry_date")
                    if invite_expiry is None:
                        logger.warning(
                            "Skipping legacy invite revoke: expiry missing owner=%s user=%s chat=%s link_suffix=%s",
                            owner_id, user_id, chat_id, invite_link[-12:],
                        )
                        continue
                    if isinstance(invite_expiry, str):
                        try:
                            invite_expiry = datetime.fromisoformat(invite_expiry.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                    if not isinstance(invite_expiry, datetime):
                        continue
                    if invite_expiry.tzinfo is None:
                        invite_expiry = invite_expiry.replace(tzinfo=timezone.utc)
                    else:
                        invite_expiry = invite_expiry.astimezone(timezone.utc)
                    if invite_expiry > datetime.now(timezone.utc):
                        logger.info(
                            "Skipping premature legacy invite revoke owner=%s user=%s chat=%s link_suffix=%s invite_expiry=%s",
                            owner_id, user_id, chat_id, invite_link[-12:], invite_expiry.isoformat(),
                        )
                        continue
                    if await active_plan_group_subscriptions_for_chat(owner_id, user_id, chat_id):
                        continue
                    legacy_checked += 1
                    try:
                        await bot.revoke_chat_invite_link(
                            chat_id=chat_id,
                            invite_link=str(invite_row.get("invite_link") or ""),
                        )
                    except Exception as exc:
                        message = str(exc).lower()
                        already_invalid = any(term in message for term in (
                            "invite link is already revoked", "invite link is invalid",
                            "invite link has expired", "invite link not found",
                        ))
                        if not already_invalid:
                            logger.warning(
                                "Legacy invite revoke failed owner=%s user=%s chat=%s link_suffix=%s error=%s",
                                owner_id, user_id, chat_id,
                                str(invite_row.get("invite_link", ""))[-12:], exc,
                            )
                            continue
                    if await mark_invite_revoked(owner_id, invite_row.get("invite_link")):
                        legacy_revoked += 1
                except Exception:
                    logger.exception(
                        "Legacy invite revoke processing failed owner=%s user=%s group=%s",
                        owner_id, user_id, group_id,
                    )

            logger.info(
                "Plan Group expiry invite revocation checked owner=%s user=%s group=%s target_chats=%s group_records=%s group_revoked=%s legacy_checked=%s legacy_revoked=%s total_revoked=%s",
                owner_id, user_id, group_id, target_ids, len(group_rows),
                revoked_count, legacy_checked, legacy_revoked,
                revoked_count + legacy_revoked,
            )
        except Exception:
            logger.exception(
                "Plan Group invite revocation failed owner=%s user=%s group=%s",
                owner_id, user_id, group_id,
            )

        channels = {int(x.get("chat_id")): str(x.get("title") or "Group/Channel") for x in await get_seller_channels(owner_id)}
        removed_names = []
        failed = []
        for chat_id in target_ids:
            # Keep access when the same target is covered by another active Plan Group.
            if await active_plan_group_subscriptions_for_chat(owner_id, user_id, chat_id):
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                member_status = getattr(member, "status", "")
                # A previous attempt may have banned the user but missed the unban.
                # Recover that state now; do not leave a renewed subscriber blocked.
                if member_status == "kicked":
                    await bot.unban_chat_member(
                        chat_id=chat_id,
                        user_id=user_id,
                        only_if_banned=True,
                    )
                    continue
                # Telegram reports `left` after a completed kick (ban + unban).
                # Do not repeat removal for a user who is already outside the chat.
                if member_status in {"left", "creator", "administrator"} or await is_whitelisted(owner_id, chat_id, user_id):
                    continue
                await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                removed_names.append(channels.get(chat_id, str(chat_id)))
            except Exception as exc:
                failed.append((chat_id, str(exc)))
                logger.warning("Plan Group expiry removal failed owner=%s user=%s group=%s chat=%s: %s", owner_id, user_id, group_id, chat_id, exc)
        if failed:
            # Notify the seller with the exact chat and Telegram error. Do not
            # expose technical removal failures to the subscriber.
            failed_lines = ["⚠️ Subscription Access Removal Failed", "", f"Subscriber ID: {user_id}", f"Plan Group: {group_id}", ""]
            for chat_id, error_text in failed:
                failed_lines.extend([
                    f"• {channels.get(chat_id, str(chat_id))}",
                    f"  Chat ID: {chat_id}",
                    f"  Problem: {error_text}",
                ])
            failed_lines.extend(["", "Please ensure the bot is a member/admin and has Ban Users permission in the listed group/channel."])
            try:
                await bot.send_message(owner_id, "\n".join(failed_lines))
            except Exception:
                logger.exception("Could not send Plan Group removal warning owner=%s user=%s group=%s", owner_id, user_id, group_id)
        expired = await expire_plan_group_subscription(owner_id, user_id, group_id, row.get("expiry_date"))
        if not expired:
            # Another expiry path may have already marked this row inactive after
            # access was removed. Do not suppress the user's expiry notice solely
            # because this worker lost the state-update race.
            logger.info(
                "Plan Group expiry state already updated; continuing notification check owner=%s user=%s group=%s expiry=%s",
                owner_id, user_id, group_id, row.get("expiry_date"),
            )
        try:
            group = await get_plan_group(owner_id, group_id) or {}
            # Always show every configured group/channel in this Plan Group,
            # regardless of whether access removal succeeded or failed.
            display_names = [
                channels.get(int(chat_id), str(chat_id))
                for chat_id in target_ids
            ]
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
            lines.extend(["", f"📅 Expired On: {expiry_text}", "", "Use 🔄 Renew Plan to restore your access."])
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

