"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from handlers.clone.help_center import help_home_keyboard, help_home_text


class CloneHelpMixin:
    async def help_command(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)
        is_owner=update.effective_user.id==self.seller_account(context)

        user_text=(
            "📚 Clone Bot Help Center\n\n"
            "👤 User Commands\n"
            "/start — Open the welcome menu\n"
            "/help — Open this help guide\n"
            "/version — Check deployed runtime version\n\n"
            "📋 Plans & Purchase\n"
            "Open Plans or Buy Plan, select a plan, complete payment and upload the payment screenshot when manual payment is enabled.\n\n"
            "🔄 Renew Plan\n"
            "Renew before or after expiry using the available renewal options.\n\n"
            "👤 My Profile\n"
            "View your Telegram ID, active plan, start date, expiry, remaining time and referral details.\n\n"
            "🎁 Referral\n"
            "Share your referral link. Reward days are added according to the seller's referral settings after a valid approved payment.\n\n"
            "📞 Live Support\n"
            "Send your message or supported media through the Support button. The seller's reply will return inside this bot.\n\n"
            "⏰ Expiry\n"
            "Expired access is removed automatically. Use Renew Plan to continue."
        )

        if not is_owner:
            await update.effective_message.reply_text(user_text)
            return

        await update.effective_message.reply_text(
            help_home_text(),
            reply_markup=help_home_keyboard(),
        )

