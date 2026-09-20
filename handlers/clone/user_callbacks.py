"""Clone-bot end-user callback router."""

from handlers.common.clone_context import *
from handlers.clone.user import navigation, payments, profile, referral, support

_USER_HANDLERS = (navigation, payments, profile, referral, support)

class CloneUserCallbacksMixin:
    async def child_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        owner = self.owner(context)
        action = q.data

        # Welcome Message editor feature buttons use a dedicated namespace.
        # Normalize them to the existing user-feature handlers so profile,
        # referral, referral-unlock, support and home use the same proven
        # feature implementations as the built-in clone buttons.
        welcome_feature_actions = {
            "wf_profile": "c_profile",
            "wf_referral": "c_referral",
            "wf_referral_unlock": "c_referral_unlock",
            "wf_support": "c_support",
            "wf_home": "c_home",
        }
        action = welcome_feature_actions.get(action, action)

        # Special buttons created by the shared message editor.
        if action == "w_rules":
            await q.answer("Group rules are available in the connected group description.", show_alert=True)
            return
        if action == "w_popup_long":
            await q.answer("Popup text is too long for Telegram callback data.", show_alert=True)
            return
        if action.startswith("w_popup:") or action.startswith("w_alert:"):
            from urllib.parse import unquote
            kind, payload = action.split(":", 1)
            await q.answer(unquote(payload), show_alert=True)
            return
        await q.answer()
        for handler in _USER_HANDLERS:
            try:
                handled = await handler.handle(self, update, context, q, owner, action)
            except Exception as exc:
                # A custom Welcome feature button must never become a silent
                # dead callback. Log the real exception and give the user a
                # short retry message instead of leaving Telegram spinning.
                logger.exception("Clone feature callback failed action=%s owner=%s", action, owner)
                try:
                    await q.message.reply_text(
                        "⚠️ This feature could not be opened. Please try again."
                    )
                except Exception:
                    pass
                return
            if handled:
                return
        return
