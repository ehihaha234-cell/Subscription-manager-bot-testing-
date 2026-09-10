"""Focused clone-bot expiry worker; existing expiry/removal flow stays unchanged."""

from handlers.common.clone_context import *


class CloneExpiryMixin:
    async def _send_expiry_reminders(self, context, owner):
        """Send each configured pre-expiry reminder once for the current expiry."""
        settings = await get_seller_settings(owner)
        try:
            reminder_days = max(0, int(settings.get("reminder_days", 1) or 0))
        except (TypeError, ValueError):
            reminder_days = 1
        if reminder_days <= 0:
            return

        timezone_name = str(settings.get("timezone") or "Asia/Kolkata")
        try:
            display_tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            display_tz = ZoneInfo("Asia/Kolkata")

        for sub in await active_expiry_reminder_subscriptions(owner, reminder_days):
            uid = sub.get("user_id")
            expiry = sub.get("expiry_date")
            if uid is None or not expiry:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            token = await claim_expiry_reminder(owner, int(uid), expiry)
            if not token:
                continue

            expiry_local = expiry.astimezone(display_tz)
            day_label = "day" if reminder_days == 1 else "days"
            plan_name = str(sub.get("plan") or "Unknown")
            text = (
                "⏰ <b>Subscription Expiry Reminder</b>\n\n"
                f"Your subscription will expire in <b>{reminder_days} {day_label}</b>.\n\n"
                f"📦 <b>Plan:</b> {html.escape(plan_name)}\n"
                f"📅 <b>Expiry Date:</b> {expiry_local.strftime('%d-%m-%Y %I:%M:%S %p')} {html.escape(timezone_name)}\n\n"
                "🔄 Please renew your plan before it expires to continue "
                "accessing the premium channel/group."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Renew Plan", callback_data="c_renew")],
            ])

            try:
                await context.bot.send_message(
                    int(uid),
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                try:
                    await release_expiry_reminder(owner, int(uid), token)
                except Exception:
                    pass
                logger.exception(
                    "Expiry reminder delivery failed owner=%s user=%s",
                    owner,
                    uid,
                )
                continue

            try:
                await complete_expiry_reminder(owner, int(uid), token, expiry)
            except Exception:
                logger.exception(
                    "Expiry reminder finalization failed owner=%s user=%s",
                    owner,
                    uid,
                )

    async def expiry_job(self,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)

        # Reminder delivery is independent from the existing expiry/removal
        # flow below. Do not alter the expiry behavior when reminder delivery fails.
        try:
            await self._send_expiry_reminders(context, owner)
        except Exception:
            logger.exception("Expiry reminder worker failed owner=%s", owner)

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
