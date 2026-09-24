"""Telegram Stars payment flow for the main SaaS seller-plan checkout."""

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import MessageHandler, PreCheckoutQueryHandler, ContextTypes, filters

from database.payment_gateways import get_gateway_config
from database.seller_subscriptions import (
    get_paid_plan,
    process_verified_plan_purchase,
)
from handlers.seller import plan_change_keyboard

logger = logging.getLogger(__name__)


def seller_stars_handlers():
    return [
        PreCheckoutQueryHandler(seller_stars_precheckout),
        MessageHandler(filters.SUCCESSFUL_PAYMENT, seller_stars_success),
    ]


async def seller_stars_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        kind, owner_text, user_text, plan_id = str(query.invoice_payload or "").split(":", 3)
        owner_id = int(owner_text)
        user_id = int(user_text)
        if kind != "stars" or owner_id != int(query.from_user.id) or user_id != int(query.from_user.id):
            raise ValueError("invalid invoice")

        cfg = await get_gateway_config("owner", 0, decrypt=True)
        plan = await get_paid_plan(plan_id)
        expected = int((plan or {}).get("stars_price", 0) or 0)
        if not cfg.get("stars_enabled") or not plan or expected <= 0:
            raise ValueError("stars disabled or plan unavailable")
        if query.currency != "XTR" or int(query.total_amount or 0) != expected:
            raise ValueError("invalid amount")

        await query.answer(ok=True)
    except Exception:
        logger.exception("Seller Stars pre-checkout validation failed")
        await query.answer(
            ok=False,
            error_message="This Stars invoice is no longer valid. Please reopen the payment page.",
        )


async def seller_stars_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.effective_message.successful_payment
    if not payment or payment.currency != "XTR":
        return

    try:
        kind, owner_text, user_text, plan_id = str(payment.invoice_payload or "").split(":", 3)
        owner_id = int(owner_text)
        user_id = int(user_text)
        if kind != "stars" or owner_id != int(update.effective_user.id) or user_id != int(update.effective_user.id):
            raise ValueError("invalid Stars payment payload")

        cfg = await get_gateway_config("owner", 0, decrypt=True)
        plan = await get_paid_plan(plan_id)
        expected = int((plan or {}).get("stars_price", 0) or 0)
        if not cfg.get("stars_enabled") or not plan or expected <= 0:
            raise ValueError("Stars payment is no longer available for this plan")
        if int(payment.total_amount or 0) != expected:
            raise ValueError("Stars amount mismatch")

        reference = str(payment.telegram_payment_charge_id or "").strip()
        if not reference:
            raise ValueError("Missing Telegram Stars payment reference")

        purchase = await process_verified_plan_purchase(
            owner_id,
            plan_id,
            int(plan.get("duration_days", 30) or 30),
            source="telegram_stars",
            amount=0,
            payment_reference=reference,
            approved_by=0,
        )

        # Keep the Stars amount with the verified payment record for history/UI.
        from database.mongo import get_database
        await get_database()["seller_plan_payments"].update_one(
            {"payment_id": purchase.get("payment_id")},
            {"$set": {
                "stars_amount": int(payment.total_amount),
                "telegram_payment_charge_id": reference,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        if purchase.get("status") == "activated" and purchase.get("decision") == "same_plan_extended":
            expiry = purchase.get("expiry_date")
            expiry_text = expiry.strftime("%d %b %Y, %I:%M %p UTC") if hasattr(expiry, "strftime") else "-"
            await update.effective_message.reply_text(
                "✅ Telegram Stars Payment Successful\n\n"
                f"📦 Plan: {plan.get('name', plan_id)}\n"
                f"⭐ Stars Paid: {int(payment.total_amount)}\n"
                f"⌛ Added Duration: {int(plan.get('duration_days', 30))} Days\n"
                f"📅 New Expiry: {expiry_text}\n\n"
                "Your seller subscription has been extended successfully."
            )
            return

        if purchase.get("status") == "decision_required":
            await update.effective_message.reply_text(
                "✅ Telegram Stars Payment Successful\n\n"
                f"📦 Purchased Plan: {plan.get('name', plan_id)}\n"
                f"⭐ Stars Paid: {int(payment.total_amount)}\n"
                f"⌛ Duration: {int(plan.get('duration_days', 30))} Days\n\n"
                "Your purchased plan is different from your current plan. Choose how you want to activate it below.",
                reply_markup=plan_change_keyboard(purchase["payment_id"]),
            )
            return

        await update.effective_message.reply_text(
            "✅ Telegram Stars Payment Successful\n\n"
            f"📦 Plan: {plan.get('name', plan_id)}\n"
            f"⭐ Stars Paid: {int(payment.total_amount)}\n"
            f"⌛ Duration: {int(plan.get('duration_days', 30))} Days\n\n"
            "Your payment has been verified successfully."
        )
    except Exception:
        logger.exception("Seller Stars fulfillment failed user=%s", update.effective_user.id if update.effective_user else None)
        await update.effective_message.reply_text(
            "⚠️ Your Telegram Stars payment was received, but activation needs support review. "
            "Please keep your Telegram Stars receipt and contact support."
        )


# Backwards-compatible singular export for simple registration.
handlers = seller_stars_handlers
