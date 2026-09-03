"""Clone-bot administrator callback router."""

from handlers.common.clone_context import *
from handlers.clone.admin import dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users, business_automation, group_manager

_ADMIN_HANDLERS = (group_manager, business_automation, dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users)

class CloneAdminCallbacksMixin:
    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        owner = self.owner(context)
        staff_record = await self.staff_record(update, context)
        if not staff_record:
            await q.edit_message_text("❌ Not authorized")
            return
        action = q.data
        # Informational/status buttons intentionally perform no navigation.
        # They still need a registered callback path so Telegram's spinner closes.
        if action == "a_noop":
            return
        role = staff_record.get("role", "moderator")
        if role == "moderator":
            allowed_prefixes = ("a_home", "a_users", "a_user_", "a_pending", "a_pay_", "a_live_support", "a_help")
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
