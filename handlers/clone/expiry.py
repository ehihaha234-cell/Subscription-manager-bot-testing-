"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneExpiryMixin:
    async def expiry_job(self,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)

        for unlock in await expired_referral_unlocks(owner):
            uid=int(unlock.get("user_id"))
            chat_id=int(unlock.get("chat_id"))
            invite_link=unlock.get("invite_link")
            if invite_link:
                try:
                    await context.bot.revoke_chat_invite_link(chat_id,invite_link)
                except Exception:
                    pass
            try:
                await context.bot.ban_chat_member(chat_id,uid)
                await context.bot.unban_chat_member(chat_id,uid,only_if_banned=True)
            except Exception:
                logger.exception("Referral unlock expiry removal failed owner=%s user=%s chat=%s",owner,uid,chat_id)
            await mark_referral_unlock_expired(owner,uid)
            try:
                await context.bot.send_message(
                    uid,
                    "⏳ Your referral-unlocked access has expired.\n\nUse the Referral Unlock button again to view the current requirements.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔓 Referral Unlock",callback_data="c_referral_unlock")]]),
                )
            except Exception:
                pass

        for sub in await expired_subscriptions(owner):
            uid=sub["user_id"]

            for invite_doc in await active_invites_for_user(owner, uid):
                try:
                    await context.bot.revoke_chat_invite_link(
                        int(invite_doc["chat_id"]), invite_doc["invite_link"]
                    )
                except Exception:
                    pass
                await deactivate_invite(owner, invite_doc["invite_link"])

            for ch in await get_channels(owner):
                try:
                    await context.bot.ban_chat_member(
                        ch["chat_id"],
                        uid,
                    )
                    await context.bot.unban_chat_member(
                        ch["chat_id"],
                        uid,
                        only_if_banned=True,
                    )
                except Exception:
                    pass

            await mark_expired(owner,uid)

            keyboard=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Renew Plan",
                        callback_data="c_renew",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👤 My Profile",
                        callback_data="c_profile",
                    )
                ],
            ])

            try:
                await context.bot.send_message(
                    uid,
                    "⏰ Your subscription has expired.\n\n"
                    "Access to premium channel/group has been removed.\n\n"
                    "Use 🔄 Renew Plan to continue.",
                    reply_markup=keyboard,
                )
            except Exception:
                pass

