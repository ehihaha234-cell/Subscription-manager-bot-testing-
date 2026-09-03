"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from database.group_manager import add_forced_join_chat


class CloneChannelsMixin:
    async def connect_group_command(self, update:Update, context:ContextTypes.DEFAULT_TYPE):
        """Connect the current private/super group without asking for a numeric chat id."""
        owner=self.owner(context)
        user=update.effective_user
        chat=update.effective_chat
        message=update.effective_message

        if not user or not await self.auth(update, context):
            # Non-bot-admin members (including Telegram group admins) get no reply.
            return
        if not chat or chat.type not in {"group", "supergroup"}:
            await message.reply_text(
                "❌ Ye command target group ke andar bhejo.\n\n"
                "Child bot ko group me add karke Admin banao, phir /connectgroup send karo."
            )
            return

        try:
            me=await context.bot.get_me()
            member=await context.bot.get_chat_member(chat.id, me.id)
            status=getattr(member, "status", "")
            can_invite=getattr(member, "can_invite_users", False)
            if status not in {"administrator", "creator"}:
                await message.reply_text(
                    "❌ Pehle child bot ko is group ka Admin banao.\n"
                    "Invite Users permission bhi ON rakho."
                )
                return
            if status != "creator" and not can_invite:
                await message.reply_text(
                    "❌ Bot ke paas Invite Users permission nahi hai.\n"
                    "Group Admin settings me Invite Users permission ON karo, phir /connectgroup dobara bhejo."
                )
                return

            await add_channel(owner, chat.id, chat.title or "Premium Group", chat.type)

            # Confirm that Telegram can actually generate an invite for this chat.
            invite=await context.bot.create_chat_invite_link(
                chat_id=chat.id,
                member_limit=1,
                name="Connection test",
            )
            try:
                await context.bot.revoke_chat_invite_link(chat.id, invite.invite_link)
            except Exception:
                pass

            await message.reply_text(
                "✅ Group connected successfully.\n\n"
                f"Group: {chat.title or 'Premium Group'}\n"
                "Invite-link permission: Working ✅\n\n"
                "Ab payment approve hone par active user ko fresh invite link milega."
            )
            context.user_data.clear()
        except BadRequest as exc:
            logger.warning("Group connect failed owner=%s chat=%s: %s", owner, getattr(chat,'id',None), exc)
            await message.reply_text(
                "❌ Group save nahi hua ya invite link create nahi ho saka.\n\n"
                "Check karo:\n"
                "• Bot group me Admin ho\n"
                "• Invite Users permission ON ho\n"
                "• Group supergroup/private group ho\n\n"
                f"Telegram error: {exc}"
            )
        except Exception as exc:
            logger.exception("Unexpected group connect error owner=%s", owner)
            await message.reply_text(f"❌ Group connect failed: {exc}")

    async def connect_forced_join_command(self, update:Update, context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context); user=update.effective_user; chat=update.effective_chat; message=update.effective_message
        if not user or not await self.auth(update, context):
            return
        if not chat or chat.type not in {"group","supergroup","channel"}:
            return
        try:
            me=await context.bot.get_me()
            member=await context.bot.get_chat_member(chat.id, me.id)
            status=getattr(member,"status","")
            if status not in {"administrator","creator"}:
                await message.reply_text("❌ Make the bot an administrator in this group/channel first.")
                return
            # Explicitly reject normal subscription connections.
            from database.seller_data import get_channels
            connected=await get_channels(owner)
            if any(int(x.get("chat_id",0) or 0)==int(chat.id) for x in connected or []):
                await message.reply_text("❌ This group/channel is already connected with /connectgroup. Use a separate group/channel for Forced Join.")
                return
            await add_forced_join_chat(owner,chat.id,chat.title or "Group/Channel",chat.type)
            await message.reply_text("✅ Forced Join group/channel connected.\n\nIt will now appear in Group Manager → Forced Join.")
        except Exception as exc:
            logger.exception("Forced join connection failed")
            await message.reply_text(f"❌ Could not connect this group/channel.\n\n{exc}")

