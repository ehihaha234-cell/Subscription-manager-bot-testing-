"""Clone-bot end-user callback router."""

from handlers.common.clone_context import *
from handlers.clone.user import navigation, payments, profile, referral, support

_USER_HANDLERS = (navigation, payments, profile, referral, support)

class CloneUserCallbacksMixin:
    async def child_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        owner = self.owner(context)
        action = q.data
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
                if await handler.handle(self, update, context, q, owner, action):
                    return
            except Exception:
                logger.exception(
                    "Clone user callback failed owner=%s user=%s action=%s",
                    owner, getattr(q.from_user, "id", None), action,
                )
                try:
                    await q.message.reply_text(
                        "⚠️ Temporary problem. Please try again."
                    )
                except Exception:
                    pass
                return
        return
