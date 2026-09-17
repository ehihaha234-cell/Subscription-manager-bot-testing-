"""Clone-bot administrator callback router."""

from handlers.common.clone_context import *
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

        # Plan creation has its own explicit dispatch path. Keep it before the
        # generic admin routing so the Create New Plan button cannot be swallowed
        # by another callback route or role-prefix guard.
        if action == "a_plan_add":
            try:
                await q.answer()
                await plans.handle(self, update, context, q, owner, staff_record, action, role)
            except Exception as exc:
                logger.exception("Create New Plan callback failed owner=%s", owner)
                detail = f"{type(exc).__name__}: {str(exc)[:180]}"
                try:
                    await q.message.reply_text(f"⚠️ Create New Plan error\n\n{detail}")
                except Exception:
                    try:
                        await q.answer(f"Create New Plan error: {detail}", show_alert=True)
                    except Exception:
                        pass
            return

        # The Create New Plan selection screen uses its Back button as the
        # save/confirm action. Dispatch it explicitly so it cannot be blocked
        # by the generic role/prefix router. This keeps the existing button
        # layout unchanged and fixes the unresponsive Back button.
        if action == "a_plan_group_save" or action.startswith("a_plan_group_save_edit_"):
            try:
                await q.answer()
                handled = await plans.handle(self, update, context, q, owner, staff_record, action, role)
                if not handled:
                    await q.answer("Button action not found", show_alert=True)
            except Exception as exc:
                logger.exception("Plan target Back/save callback failed owner=%s", owner)
                detail = f"{type(exc).__name__}: {str(exc)[:180]}"
                try:
                    await q.answer(f"Plan Management error: {detail}", show_alert=True)
                except Exception:
                    pass
            return
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
