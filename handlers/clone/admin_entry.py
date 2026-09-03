"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneAdminEntryMixin:
    async def admin(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        if not await self.auth(update,context): await update.effective_message.reply_text("❌ Not authorized"); return
        context.user_data.clear()
        await update.effective_message.reply_text(
            await self.admin_panel_text(self.owner(context), update.effective_user),
            reply_markup=self.admin_menu(),
            parse_mode="HTML",
        )

