"""Clone-bot administrator callback router."""

import logging

from handlers.common.clone_context import *

logger = logging.getLogger(__name__)
from handlers.clone.admin import dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users, business_automation, group_manager

_ADMIN_HANDLERS = (group_manager, business_automation, dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users)

class CloneAdminCallbacksMixin:
    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        owner = self.owner(context)
        staff_record = await self.staff_record(update, context)
        if not staff_record:
            await q.edit_message_text("❌ Not authorized")
            return
        action = q.data
        # Resolve the staff role BEFORE any feature-specific dispatch.
        # The previous code passed `role` into the Plan handler before assigning
        # it, causing an UnboundLocalError and making ➕ Create New Plan appear
        # completely unresponsive.
        role = staff_record.get("role", "moderator")

        # Informational/status buttons intentionally perform no navigation.
        # They still need a registered callback path so Telegram's spinner closes.
        if action == "a_noop":
            return

        # Route every Plan Management callback directly.  In particular, the
        # Create New Plan selection screen uses a_plan_group_save for its Back
        # button; handling it here prevents the generic admin router from
        # swallowing the callback.  The handler itself performs the database
        # operation first, then we close the Telegram callback spinner once.
        if action == "a_plans" or action.startswith("a_plan_"):
            try:
                handled = await plans.handle(self, update, context, q, owner, staff_record, action, role)
                if not handled:
                    await q.answer("Button action not found", show_alert=True)
                else:
                    try:
                        await q.answer()
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception("Plan Management callback failed owner=%s action=%s", owner, action)
                detail = f"{type(exc).__name__}: {str(exc)[:180]}"
                try:
                    await q.answer(f"Plan Management error: {detail}", show_alert=True)
                except Exception:
                    try:
                        await q.message.reply_text(f"⚠️ Plan Management error\n\n{detail}")
                    except Exception:
                        pass
            return
        await q.answer()
        await q.answer()
        if role == "moderator":
            allowed_prefixes = (
                "a_home",
                "a_users",
                "a_user_",
                "a_pending",
                "a_history",
                "a_pay_",
                "a_seller_profile",
                "a_terms",
            )
            if not any(action == prefix or action.startswith(prefix) for prefix in allowed_prefixes):
                await q.answer("Moderator permission is not available for this section.", show_alert=True)
                return
        if role != "seller" and action.startswith("a_staff"):
            await q.answer("Only the seller can manage staff.", show_alert=True)
            return
        for handler in _ADMIN_HANDLERS:
            if await handler.handle(self, update, context, q, owner, staff_record, action, role):
                return
        await q.answer("Button action not found", show_alert=True)
