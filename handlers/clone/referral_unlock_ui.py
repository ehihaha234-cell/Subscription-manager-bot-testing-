"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneReferralUnlockUIMixin:
    @staticmethod
    def referral_unlock_text(settings):
        enabled=bool(settings.get("referral_unlock_enabled",False))
        required=int(settings.get("referral_unlock_required",3) or 3)
        target=settings.get("referral_unlock_target_title") or "Not selected"
        duration_days=max(1,int(settings.get("referral_unlock_duration_days",30) or 30))
        count_mode=settings.get("referral_unlock_count_mode","subscription")
        count_mode_label=(
            "New user starts the bot"
            if count_mode == "start"
            else "Referred user completes a subscription"
        )
        return (
            "🔓 Referral Unlock Setup\n\n"
            "This button lets users unlock a selected private group or channel after completing the required referrals.\n\n"
            f"Status: {'Enabled ✅' if enabled else 'Disabled ❌'}\n"
            f"Required referrals: {required}\n"
            f"Count condition: {count_mode_label}\n"
            f"Access duration: {duration_days} day(s)\n"
            f"Destination: {target}\n\n"
            "Setup steps:\n"
            "1. Select the required referral count.\n"
            "2. Choose when a referral should count.\n"
            "3. Set how many days access will remain active.\n"
            "4. Select a connected group or channel.\n"
            "5. Enable Referral Unlock.\n"
            "6. Add feature:referral_unlock from any supported button editor.\n\n"
            "Count options:\n"
            "• New User Starts: counts after a new referred user starts the bot.\n"
            "• User Subscribes: counts only after the referred user completes a subscription.\n\n"
            "Supported editors:\n"
            "• Welcome Message buttons\n"
            "• Live Support Template buttons\n"
            "• Auto Reply buttons\n\n"
            "Button format:\n"
            "Unlock Access - feature:referral_unlock"
        )

    @staticmethod
    def referral_unlock_menu(settings, channels):
        enabled=bool(settings.get("referral_unlock_enabled",False))
        required=int(settings.get("referral_unlock_required",3) or 3)
        target_title=settings.get("referral_unlock_target_title") or "Not selected"
        rows=[
            [InlineKeyboardButton("⛔ Disable" if enabled else "✅ Enable",callback_data="a_referral_unlock_toggle")],
            [InlineKeyboardButton(f"👥 Required Referrals: {required}",callback_data="a_referral_unlock_required")],
            [InlineKeyboardButton(
                "👤 Count When: New User Starts"
                if settings.get("referral_unlock_count_mode", "subscription") == "start"
                else "💳 Count When: User Subscribes",
                callback_data="a_referral_unlock_count_mode",
            )],
            [InlineKeyboardButton(f"📅 Access Duration: {int(settings.get('referral_unlock_duration_days',30) or 30)} Days",callback_data="a_referral_unlock_duration")],
            [InlineKeyboardButton(f"📢 Destination: {target_title}",callback_data="a_referral_unlock_destination")],
        ]
        rows.append([InlineKeyboardButton("⬅ Back",callback_data="a_settings")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def referral_unlock_channels_menu(channels):
        rows=[]
        for item in channels:
            title=str(item.get("title") or item.get("chat_id"))[:40]
            rows.append([InlineKeyboardButton(title,callback_data=f"a_referral_unlock_chat_{item.get('chat_id')}")])
        rows.append([InlineKeyboardButton("⬅ Back",callback_data="a_referral_unlock")])
        return InlineKeyboardMarkup(rows)

