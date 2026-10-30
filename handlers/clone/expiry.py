"""Focused clone-bot expiry worker; existing expiry/removal flow stays unchanged."""

from handlers.common.clone_context import *
from services.membership_access import kick_then_unban


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

        # Revoke due invite links through the normal subscription lifecycle.
        # This collection is independent of Subscription Guard settings/state.
        try:
            from database.subscription_invites import revoke_subscription_invites
            await revoke_subscription_invites(context.bot, owner, due_only=True)
        except Exception:
            logger.exception("Automatic invite-link expiry revocation failed owner=%s", owner)

        for sub in await expired_subscriptions(owner):
            uid=sub["user_id"]

            # A legacy/global subscription can expire while a Plan Group subscription
            # is still active. Never revoke that Plan Group's fresh timed invite just
            # because the user's legacy subscription row appears in expired_subscriptions.
            # Timed invites are already revoked by the due-only lifecycle pass above.
            now_utc = datetime.now(timezone.utc)
            for invite_doc in await active_invites_for_user(owner, uid):
                chat_id = int(invite_doc.get("chat_id") or 0)
                if not chat_id:
                    continue

                invite_expiry = invite_doc.get("expiry_date")
                if invite_expiry is not None:
                    if getattr(invite_expiry, "tzinfo", None) is None:
                        invite_expiry = invite_expiry.replace(tzinfo=timezone.utc)
                    else:
                        invite_expiry = invite_expiry.astimezone(timezone.utc)
                    # Future-dated links must remain valid until their own expiry.
                    if invite_expiry > now_utc:
                        continue
                else:
                    # Fail closed: an invite without its own saved expiry cannot be
                    # safely revoked by this legacy/global subscription-expiry loop.
                    # Normal payment and Plan Group timed links must remain valid until
                    # their own Telegram expiry. The due-only invite lifecycle above
                    # handles records that have a confirmed expiry.
                    logger.warning(
                        "Skipping legacy invite revoke: invite expiry missing owner=%s user=%s chat=%s link_suffix=%s",
                        owner, uid, chat_id, str(invite_doc.get("invite_link") or "")[-12:],
                    )
                    continue

                try:
                    await context.bot.revoke_chat_invite_link(
                        chat_id=chat_id, invite_link=invite_doc["invite_link"]
                    )
                except Exception:
                    pass
                await deactivate_invite(owner, invite_doc["invite_link"])

            for ch in await get_channels(owner):
                try:
                    removal_state = await kick_then_unban(
                        context.bot, ch["chat_id"], uid
                    )
                    if removal_state == "unbanned_existing":
                        logger.info(
                            "Recovered previously banned clone subscriber owner=%s user=%s chat=%s",
                            owner, uid, ch["chat_id"],
                        )
                except Exception as exc:
                    logger.warning(
                        "Clone subscription expiry kick/unban failed owner=%s user=%s chat=%s: %s",
                        owner, uid, ch.get("chat_id"), exc,
                    )

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

            expiry_notice = (
                "⏰ Your subscription has expired.\n\n"
                "Access to premium channel/group has been removed.\n\n"
                "Use 🔄 Renew Plan to continue."
            )
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=expiry_notice,
                    reply_markup=keyboard,
                )
                logger.info(
                    "Subscription expiry notification sent owner=%s user=%s",
                    owner, uid,
                )
            except Exception as exc:
                # Retry once without the keyboard so a callback-markup issue does
                # not silently suppress the expiry notification.
                logger.exception(
                    "Subscription expiry notification failed owner=%s user=%s; retrying plain text: %s",
                    owner, uid, exc,
                )
                try:
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=expiry_notice,
                    )
                    logger.info(
                        "Subscription expiry plain-text fallback sent owner=%s user=%s",
                        owner, uid,
                    )
                except Exception:
                    logger.exception(
                        "Subscription expiry plain-text fallback failed owner=%s user=%s",
                        owner, uid,
                    )

        # Expired subscriptions can free capacity without an admin action.
        # Recheck pending selections on each existing expiry cycle (60 seconds);
        # the resume routine only opens payment pages for users whose clone now
        # has capacity and clears each selection after it is handled.
        try:
            from services.bot_manager import bot_manager
            seller_account_id = int(
                context.application.bot_data.get('seller_account_id') or owner
            )
            await bot_manager.resume_pending_limited_purchases(seller_account_id)
        except Exception:
            logger.exception(
                'Could not resume limited plan selections after expiry scan owner=%s',
                owner,
            )

