"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneSupportCommandsMixin:
    async def support_template_command_handler(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        message=update.effective_message; user=update.effective_user; chat=update.effective_chat
        if not message or not user or user.id!=self.seller_account(context) or not message.text:
            return
        owner=self.owner(context); support=await get_live_support_settings(owner)
        command=message.text.split()[0].split("@",1)[0].lstrip("/").lower()
        template=await get_support_template(owner,command)
        if not template or template.get("enabled", True) is False:
            return
        target_user_id=None
        if support.get("mode")=="topic" and support.get("support_group_id") and int(chat.id)==int(support["support_group_id"]) and message.message_thread_id:
            topic=await get_topic_by_thread(owner,chat.id,message.message_thread_id)
            if topic: target_user_id=int(topic["user_id"])
        elif support.get("mode")=="private" and chat.type=="private" and message.reply_to_message:
            link=await get_private_message_link(owner,chat.id,message.reply_to_message.message_id)
            if link: target_user_id=int(link["user_id"])
        if not target_user_id:
            await message.reply_text("❌ Is command ko user ke support topic me, ya private mode me user message ka reply karke bhejo.")
            raise ApplicationHandlerStop
        await self.send_support_template(context,owner,target_user_id,template)
        await message.reply_text(f"✅ /{command} sent to user")
        raise ApplicationHandlerStop

    async def connect_support_command(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)
        user=update.effective_user
        chat=update.effective_chat
        message=update.effective_message
        if not user or not await self.auth(update, context):
            # Non-bot-admin members (including Telegram group admins) get no reply.
            return
        if not chat or chat.type!="supergroup" or not getattr(chat,"is_forum",False):
            await message.reply_text("❌ /connectsupport ko Topics ON wale private supergroup ke andar bhejo.")
            return
        try:
            me=await context.bot.get_me()
            member=await context.bot.get_chat_member(chat.id,me.id)
            if getattr(member,"status","") not in {"administrator","creator"}:
                await message.reply_text("❌ Clone Bot ko group Admin banao.")
                return
            if getattr(member,"status","")!="creator" and not getattr(member,"can_manage_topics",False):
                await message.reply_text("❌ Bot ke liye Manage Topics permission ON karo.")
                return
            updated=await update_live_support_settings(
                owner,
                support_group_id=chat.id,
                support_group_title=chat.title or "Support Group",
                mode="topic",
                enabled=True,
            )
            await message.reply_text(
                "✅ Support group connected successfully.\n\n"
                f"Group: {updated.get('support_group_title')}\n"
                "Live Support: ON\n"
                "Mode: Topic Mode\n\n"
                "Ab kisi user ka pehla message aate hi uske naam aur ID se naya topic banega."
            )
        except TelegramError as exc:
            logger.exception("Support group connection failed owner=%s chat=%s",owner,getattr(chat,"id",None))
            await message.reply_text(f"❌ Support group connect failed: {str(exc)[:200]}")

