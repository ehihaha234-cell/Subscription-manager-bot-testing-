from __future__ import annotations

import logging

from database.payment_gateways import (
    claim_transaction_fulfillment,
    get_gateway_config,
    get_gateway_transaction,
    mark_transaction_fulfillment_retry,
    defer_cashfree_verification,
    expire_stale_cashfree_transactions,
    recoverable_gateway_transactions,
    recoverable_failed_notifications,
    reclaim_failed_transaction_notification,
    complete_transaction_notification,
    fail_transaction_notification,
    update_gateway_transaction,
)
from services.payment_gateways import (
    GatewayError,
    _verify_cashfree_payment,
    fulfill_transaction,
    expire_razorpay_qr_transactions_job,
    cleanup_razorpay_qr_pool_job,
    prewarm_razorpay_qr_pool_job,
)

logger = logging.getLogger(__name__)


async def recover_gateway_transactions_job() -> None:
    try:
        expired_qr = await expire_razorpay_qr_transactions_job()
        if expired_qr:
            logger.info("Expired Razorpay QR payment screens count=%s", expired_qr)
    except Exception:
        logger.exception("Razorpay QR expiry job failed")

    try:
        cleaned_qr = await cleanup_razorpay_qr_pool_job()
        if cleaned_qr:
            logger.info("Deleted expired Razorpay QR pool records count=%s", cleaned_qr)
    except Exception:
        logger.exception("Razorpay QR pool cleanup failed")

    try:
        warmed_qr = await prewarm_razorpay_qr_pool_job(target_per_plan=2)
        if warmed_qr:
            logger.info("Pre-created Razorpay QR codes count=%s", warmed_qr)
    except Exception:
        logger.exception("Razorpay QR prewarm failed")

    # Historical unpaid orders must not stay in the recovery queue forever.
    expired = await expire_stale_cashfree_transactions(max_age_minutes=30)
    if expired:
        logger.info("Expired stale Cashfree transactions count=%s", expired)

    items = await recoverable_gateway_transactions(limit=50)
    for tx in items:
        transaction_id = str(tx.get("transaction_id") or "")
        if not transaction_id:
            continue
        try:
            current = await get_gateway_transaction(transaction_id)
            if (
                not current
                or current.get("status") == "fulfilled"
                or current.get("fulfilled_at") is not None
            ):
                continue

            if current.get("gateway") == "cashfree" and current.get("status") in {"pending", "verification_pending"}:
                cfg = await get_gateway_config(current["scope"], current["owner_id"], decrypt=True)
                settings = (cfg.get("gateways") or {}).get("cashfree") or {}
                try:
                    payment_id, verified = await _verify_cashfree_payment(current, settings)
                except GatewayError as exc:
                    # Cashfree uses order_status=PAID only after a successful
                    # payment. Do not hammer the API for unpaid orders: persist
                    # a per-transaction backoff and retry later.
                    await defer_cashfree_verification(
                        transaction_id,
                        str(exc),
                        retry_after_seconds=120,
                    )
                    logger.debug(
                        "Cashfree payment deferred transaction_id=%s error=%s",
                        transaction_id,
                        exc,
                    )
                    continue
                await update_gateway_transaction(
                    transaction_id,
                    status="paid",
                    gateway_payment_id=payment_id,
                    server_verification=verified,
                    verification_error="",
                )

            work = await claim_transaction_fulfillment(transaction_id)
            if not work:
                continue
            try:
                await fulfill_transaction(work)
            except Exception as exc:
                await mark_transaction_fulfillment_retry(transaction_id, str(exc))
                raise
        except GatewayError as exc:
            logger.warning("Gateway recovery deferred transaction_id=%s error=%s", transaction_id, exc)
        except Exception:
            logger.exception("Gateway recovery failed transaction_id=%s", transaction_id)


async def recover_failed_invite_deliveries_job() -> None:
    """Retry only failed subscriber invite delivery for already-fulfilled payments."""
    items = await recoverable_failed_notifications(limit=50)
    for tx in items:
        transaction_id = str(tx.get("transaction_id") or "")
        if not transaction_id:
            continue
        try:
            claimed = await reclaim_failed_transaction_notification(
                transaction_id,
                "subscriber_access",
            )
            if not claimed:
                continue

            from database.seller_data import (
                fulfill_subscription_payment, fulfill_plan_group_subscription,
                get_plan, get_subscription, get_plan_group_subscription,
            )
            from services.bot_manager import bot_manager

            seller_id = int(tx.get("owner_id") or 0)
            user_id = int(tx.get("payer_user_id") or 0)
            plan_id = str((tx.get("metadata") or {}).get("plan_id") or "")
            plan = await get_plan(seller_id, plan_id)
            if not plan:
                raise GatewayError("Child subscription plan no longer exists")

            # Recover only targets captured at checkout. Never use the seller's
            # current connected-group list as a fallback: groups added later are
            # not part of an older purchase.
            metadata = tx.get("metadata") or {}
            group_id = str(metadata.get("group_id") or plan.get("group_id") or "").strip()
            target_chat_ids = [int(x) for x in (metadata.get("target_chat_ids") or [])]
            if group_id and not target_chat_ids:
                # Legacy transaction: use the user's saved Plan Group subscription
                # snapshot, not the current Plan Group configuration.
                saved_sub = await get_plan_group_subscription(seller_id, user_id, group_id)
                target_chat_ids = [int(x) for x in ((saved_sub or {}).get("target_chat_ids") or [])]

            # Re-apply idempotently in the correct subscription scope. This must
            # never extend a clone-wide subscription for a Plan Group purchase.
            if group_id:
                subscription_result = await fulfill_plan_group_subscription(
                    seller_id,
                    user_id,
                    f"gateway:{transaction_id}",
                    group_id,
                    plan.get("name", "Subscription"),
                    int(plan.get("duration_minutes", 0) or 0),
                    tx.get("amount", 0),
                    plan.get("duration_text"),
                    target_chat_ids=target_chat_ids,
                )
            else:
                subscription_result = await fulfill_subscription_payment(
                    seller_id,
                    user_id,
                    f"gateway:{transaction_id}",
                    plan.get("name", "Subscription"),
                    int(plan.get("duration_minutes", 0) or 0),
                    tx.get("amount", 0),
                    plan.get("duration_text"),
                )

            if group_id and not target_chat_ids:
                raise GatewayError(
                    "Saved Plan Group targets are missing; refusing to send unrelated group links"
                )

            delivery = await bot_manager.deliver_subscription_access(
                seller_id,
                user_id,
                success_details=None,
                bot_id=int((tx.get("metadata") or {}).get("bot_id") or 0),
                target_chat_ids=target_chat_ids,
            )
            if delivery.get("error") or (
                int(delivery.get("sent", 0) or 0) == 0
                and int(delivery.get("already_member", 0) or 0) == 0
            ):
                raise GatewayError(
                    delivery.get("error")
                    or "No invite link was delivered and the user is not a member"
                )

            await complete_transaction_notification(
                transaction_id,
                "subscriber_access",
                delivery,
            )
            logger.info(
                "Recovered invite delivery transaction_id=%s sent=%s already_member=%s",
                transaction_id,
                delivery.get("sent", 0),
                delivery.get("already_member", 0),
            )
        except Exception as exc:
            await fail_transaction_notification(
                transaction_id,
                "subscriber_access",
                str(exc),
            )
            logger.exception(
                "Invite delivery recovery failed transaction_id=%s",
                transaction_id,
            )
