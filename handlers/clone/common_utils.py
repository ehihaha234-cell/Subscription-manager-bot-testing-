"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneCommonUtilsMixin:
    @staticmethod
    def parse_duration(value:str)->int:
        value=value.strip().lower(); n=int(value[:-1]); unit=value[-1]
        if n<=0: raise ValueError("Duration must be positive")
        if unit=="m": return n
        if unit=="h": return n*60
        if unit=="d": return n*1440
        raise ValueError("Use m, h or d")

    @classmethod
    def parse_plan(cls,text:str):
        p=[x.strip() for x in text.split("|")]
        if len(p)!=4: raise ValueError("Use: Plan Name | Duration | Price | Stars")
        stars=int(p[3])
        if stars < 0 or stars > 2500: raise ValueError("Stars must be between 0 and 2500")
        return p[0],p[1].lower(),cls.parse_duration(p[1]),float(p[2]),stars

    def owner(self,context):
        # owner() is the clone-specific persistent data scope. Seller identity
        # is always available separately through seller_account().
        return int(context.application.bot_data.get("data_owner_id") or context.application.bot_data["seller_owner_id"])

    def seller_account(self,context): return int(context.application.bot_data.get("seller_account_id", self.owner(context)))

    async def staff_record(self, update, context):
        uid = int(update.effective_user.id)
        if uid == self.seller_account(context):
            return {"role": "seller", "status": "active", "permissions": ["*"]}
        return await active_staff(self.owner(context), uid)

    async def auth(self,update,context):
        return bool(await self.staff_record(update, context))

    async def safe_query_message(self,q,text,reply_markup=None):
        """Edit the callback's existing message, including media captions.

        Feature navigation must stay on the same Business Automation welcome
        message.  A media welcome has no text body, so editing its caption is
        required instead of replying with another message.
        """
        message = q.message
        has_media = bool(
            getattr(message, "photo", None)
            or getattr(message, "video", None)
            or getattr(message, "animation", None)
            or getattr(message, "document", None)
            or getattr(message, "audio", None)
        )
        try:
            if has_media:
                return await q.edit_message_caption(
                    caption=text,
                    reply_markup=reply_markup,
                )
            return await q.edit_message_text(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            error=str(exc).lower()
            if "message is not modified" in error:
                return None
            raise

