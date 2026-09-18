from datetime import datetime, timezone

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
    """Expire Plan Group subscriptions without touching clone-wide access."""
    now = datetime.now(timezone.utc)
    rows = await expired_plan_group_subscriptions(limit=5000)
    from services.bot_manager import bot_manager

    for row in rows:
        owner_id = int(row.get("owner_id") or 0)
        user_id = int(row.get("user_id") or 0)
        group_id = str(row.get("group_id") or "").strip()
        if not owner_id or not user_id or not group_id:
            continue

        running = bot_manager.get_running(owner_id)
        if not running:
            # Leave the record active-but-expired so a later worker can retry
            # removal when the clone bot is available again.
            continue
        bot = running.application.bot
        target_ids = []
        for value in row.get("target_chat_ids") or []:
            try:
                target_ids.append(int(value))
            except (TypeError, ValueError):
                pass
        target_ids = list(dict.fromkeys(target_ids))

        failed = False
        for chat_id in target_ids:
            # Another active Plan Group or the normal clone subscription keeps
            # this particular chat accessible; never remove that user's access.
            generic = await get_seller_subscription(owner_id, user_id)
            generic_expiry = (generic or {}).get("expiry_date")
            if generic_expiry and generic_expiry.tzinfo is None:
                generic_expiry = generic_expiry.replace(tzinfo=timezone.utc)
            generic_active = bool(generic and generic.get("active") and generic_expiry and generic_expiry > now)
            if generic_active or await active_plan_group_subscriptions_for_chat(owner_id, user_id, chat_id):
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if getattr(member, "status", "") in {"creator", "administrator"} or await is_whitelisted(owner_id, chat_id, user_id):
                    continue
                await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            except Exception as exc:
                failed = True
                logger.warning(
                    "Plan Group expiry removal failed owner=%s user=%s group=%s chat=%s: %s",
                    owner_id, user_id, group_id, chat_id, exc,
                )

        if failed:
            continue
        await expire_plan_group_subscription(owner_id, user_id, group_id, row.get("expiry_date"))
        logger.info(
            "Plan Group subscription expired owner=%s user=%s group=%s expiry=%s",
            owner_id, user_id, group_id, row.get("expiry_date"),
        )
