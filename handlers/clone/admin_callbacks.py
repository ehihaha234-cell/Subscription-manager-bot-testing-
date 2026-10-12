"""Clone-bot administrator callback router."""

import logging

from handlers.common.clone_context import *

logger = logging.getLogger(__name__)
from handlers.clone.admin import dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users, business_automation, group_manager

_ADMIN_HANDLERS = (group_manager, business_automation, dashboard, plans, channels, welcome, gateways, live_support, payments, broadcast_coupons, referrals, help_terms, staff, users)

class CloneAdminCallbacksMixin:
    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        owner = self.owner(context)
        staff_record = await self.staff_record(update, context)
        if not staff_record:
            await q.edit_message_text("❌ Not authorized")
            return
        action = q.data
        # Resolve the staff role BEFORE any feature-specific dispatch.
        # The previous code passed `role` into the Plan handler before assigning
        # it, causing an UnboundLocalError and making ➕ Create New Plan appear
        # completely unresponsive.
        role = staff_record.get("role", "moderator")

        if action == "a_backup_restore":
            if role != "seller":
                await q.answer("Only the seller can restore a clone backup.", show_alert=True)
                return
            context.user_data.pop("clone_backup_bytes", None)
            context.user_data["awaiting_clone_backup_upload"] = True
            await q.edit_message_text(
                "💾 <b>Clone Bot Backup & Restore</b>\n\n"
                "Send the .json.gz backup file created from Owner Dashboard → Clone Bot Backup.\n\n"
                "The backup will be restored only into this clone bot.\n"
                "Maximum file size: 20 MB.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="a_home")]]),
                parse_mode="HTML",
            )
            return
        if action in {"a_backup_mode_merge", "a_backup_mode_replace"}:
            if role != "seller":
                await q.answer("Only the seller can restore a clone backup.", show_alert=True)
                return
            raw = context.user_data.get("clone_backup_bytes")
            if not raw:
                await q.answer("Please upload the backup file again.", show_alert=True)
                return
            mode = "merge" if action.endswith("merge") else "replace"
            await q.answer()
            status = await q.message.reply_text("⏳ Preparing restore…")
            progress_state = {"done": -1}
            async def progress(done, total, current):
                if done == progress_state["done"] and done != total:
                    return
                progress_state["done"] = done
                percent = 100 if total <= 0 else min(100, int(done * 100 / total))
                filled = min(10, int(percent / 10))
                bar = "█" * filled + "░" * (10 - filled)
                try:
                    await status.edit_text(
                        f"♻️ <b>Restore Progress</b>\n\n[{bar}] {percent}%\n"
                        f"Processed: {done:,} / {total:,}\nCurrent: {html.escape(str(current))}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            try:
                from services.clone_backup import restore_clone_backup
                result = await restore_clone_backup(raw, int(owner), mode, progress_callback=progress)
                context.user_data.pop("clone_backup_bytes", None)
                context.user_data.pop("awaiting_clone_backup_upload", None)
                await status.edit_text(
                    "✅ <b>Clone backup restored.</b>\n\n"
                    f"Mode: <b>{mode.title()}</b>\n"
                    f"Restored: {result['restored']:,}\n"
                    f"Skipped existing records: {result['skipped']:,}\n"
                    f"Backup records: {result['records']:,}",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.exception("Clone backup restore failed owner=%s mode=%s", owner, mode)
                await status.edit_text(f"❌ Restore failed: {html.escape(str(exc)[:400])}")
            return

        # Resolve the staff role BEFORE any feature-specific dispatch.
        # to a user-details screen. This prevents a duration/ban-reason message
        # sent after Back from being processed as the previous pending action.
        if action.startswith("a_user_view_"):
            context.user_data.pop("wait_user_group_duration", None)
            context.user_data.pop("wait_user_group_duration_gid", None)
            context.user_data.pop("user_custom_duration_text", None)
            context.user_data.pop("wait_user_ban_reason", None)

        # Informational/status buttons intentionally perform no navigation.
        # They still need a registered callback path so Telegram's spinner closes.
        if action == "a_noop":
            return

        # Route every Plan Management callback directly.  In particular, the
        # Create New Plan selection screen uses a_plan_group_save for its Back
        # button; handling it here prevents the generic admin router from
        # swallowing the callback.  The handler itself performs the database
        # operation first, then we close the Telegram callback spinner once.
        if action == "a_plans" or action.startswith("a_plan_"):
            try:
                handled = await plans.handle(self, update, context, q, owner, staff_record, action, role)
                if not handled:
                    await q.answer("Button action not found", show_alert=True)
                else:
                    try:
                        await q.answer()
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception("Plan Management callback failed owner=%s action=%s", owner, action)
                detail = f"{type(exc).__name__}: {str(exc)[:180]}"
                try:
                    await q.answer(f"Plan Management error: {detail}", show_alert=True)
                except Exception:
                    try:
                        await q.message.reply_text(f"⚠️ Plan Management error\n\n{detail}")
                    except Exception:
                        pass
            return
        # Answer the callback once; answering twice raises BadRequest and
        # prevents all feature handlers (including Remove Subscription) from running.
        try:
            await q.answer()
        except Exception:
            pass
        if role == "moderator":
            allowed_prefixes = (
                "a_home",
                "a_users",
                "a_user_",
                "a_pending",
                "a_history",
                "a_pay_",
                "a_seller_profile",
                "a_terms",
            )
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
