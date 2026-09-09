"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from database.forced_join import upsert_required


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
        """Connect a Forced Join group/channel using its own storage.

        The target may be the current group, or a chat explicitly supplied as
        /connectforcedjoin <chat_id|@username> from the seller/admin private chat.
        This is intentionally independent of /connectgroup storage.
        """
        owner=self.owner(context)
        user=update.effective_user
        message=update.effective_message
        chat=update.effective_chat

        if not user or not await self.auth(update, context):
            return

        target_id=0
        target_title=None
        target_type=None

        # In a group/supergroup, no argument means "connect this group".
        # In private/admin chat, an explicit @username or numeric ID can target
        # either a group or a channel. This is the reliable way to connect a
        # channel because Telegram does not deliver ordinary channel commands
        # to bots as normal user messages.
        if context.args:
            raw_target=str(context.args[0]).strip()
            try:
                target_id=int(raw_target)
                info=await context.bot.get_chat(target_id)
            except ValueError:
                try:
                    info=await context.bot.get_chat(raw_target)
                    target_id=int(info.id)
                except Exception:
                    await message.reply_text("❌ Send a valid chat ID or @username.")
                    return
            except Exception:
                await message.reply_text("❌ Could not find this group/channel. Check the ID or @username.")
                return
            target_title=info.title or "Group/Channel"
            target_type=info.type
        else:
            if not chat or chat.type not in {"group", "supergroup"}:
                await message.reply_text(
                    "❌ For a group, send /connectforcedjoin inside that group.\n\n"
                    "For a channel, send this from the bot admin chat:\n"
                    "/connectforcedjoin @channelusername\n"
                    "or /connectforcedjoin <channel_id>"
                )
                return
            target_id=int(chat.id)
            target_title=chat.title or "Group/Channel"
            target_type=chat.type

        try:
            # IMPORTANT: Do not check /connectgroup storage here. A chat may be
            # connected to both systems; their databases and runtime purposes
            # remain completely separate.
            me=await context.bot.get_me()
            member=await context.bot.get_chat_member(target_id, me.id)
            status=getattr(member, "status", "")
            if status not in {"administrator", "creator"}:
                await message.reply_text(
                    "❌ Make the bot an administrator in this group/channel first."
                )
                return
            if status != "creator" and not getattr(member, "can_invite_users", False):
                await message.reply_text(
                    "❌ Bot needs the Invite Users permission in this group/channel."
                )
                return

            # Test the exact Telegram permission before saving anything.
            invite=await context.bot.create_chat_invite_link(
                chat_id=target_id,
                name="Forced Join",
                member_limit=0,
            )

            # Legacy manual command: keep the target independent for every
            # connected Group Manager access group/channel. It never creates
            # one owner-wide Forced Join toggle.
            connected = await get_channels(owner)
            if not connected:
                await message.reply_text(
                    "❌ Connect at least one group/channel in Group Manager first."
                )
                return
            for access in connected:
                access_chat_id=int(access.get("chat_id", 0) or 0)
                if access_chat_id:
                    await upsert_required(
                        owner,
                        access_chat_id,
                        target_id,
                        target_title,
                        target_type,
                        invite.invite_link,
                    )

            await message.reply_text(
                "✅ Forced Join group/channel connected successfully.\n\n"
                f"Name: {target_title}\n"
                f"Type: {target_type}\n"
                f"ID: {target_id}\n"
                "Invite link: Working ✅\n\n"
                "This is stored separately from /connectgroup."
            )
        except Exception as exc:
            logger.exception("Forced Join connection failed owner=%s chat=%s", owner, target_id)
            await message.reply_text(
                "❌ Could not connect this group/channel.\n\n"
                f"Telegram error: {exc}"
            )
