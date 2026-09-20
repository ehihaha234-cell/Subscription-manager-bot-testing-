"""Clone-bot end-user callback router."""

from handlers.common.clone_context import *
from handlers.clone.user import navigation, payments, profile, referral, support

_USER_HANDLERS = (navigation, payments, profile, referral, support)


def _callback_handler(action: str):
    """Select the owning feature directly instead of testing every handler."""
    if (
        action == "c_return_origin"
        or action in {
            "c_plans", "c_buy", "c_renew", "c_profile",
            "c_referral", "c_referral_unlock", "c_support",
            "seller_current_plan", "seller_upgrade_plan",
            "ba_user_home", "c_home",
        }
        or action.startswith("c_plans_target_")
        or action.startswith("c_plans_list_")
        or action.startswith("c_pg_renew_")
    ):
        return navigation

    if (
        action.startswith("c_select_")
        or action.startswith("c_star_")
        or action.startswith("c_pg_")
        or action == "c_upload"
    ):
        return payments

    if action == "c_profile":
        return profile
    if action in {"c_referral", "c_referral_unlock"}:
        return referral
    if action == "c_support":
        return support
    return None


class CloneUserCallbacksMixin:
    async def child_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        owner = self.owner(context)
        action = str(q.data or "")

        # Special buttons created by the shared message editor.
        if action == "w_rules":
            await q.answer(
                "Group rules are available in the connected group description.",
                show_alert=True,
            )
            return
        if action == "w_popup_long":
            await q.answer(
                "Popup text is too long for Telegram callback data.",
                show_alert=True,
            )
            return
        if action.startswith("w_popup:") or action.startswith("w_alert:"):
            from urllib.parse import unquote
            _, payload = action.split(":", 1)
            await q.answer(unquote(payload), show_alert=True)
            return

        # A callback must be acknowledged immediately. Run the Telegram
        # acknowledgement concurrently with the feature/database work so the
        # user does not wait for an extra network round-trip before navigation.
        answer_task = asyncio.create_task(q.answer())

        handler = _callback_handler(action)
        try:
            if handler is not None:
                await handler.handle(self, update, context, q, owner, action)
                return

            # Preserve legacy/unknown callback compatibility.
            for fallback in _USER_HANDLERS:
                if fallback is handler:
                    continue
                if await fallback.handle(self, update, context, q, owner, action):
                    return
        finally:
            try:
                await answer_task
            except Exception:
                pass
