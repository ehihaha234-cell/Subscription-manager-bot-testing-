"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneCommonUtilsMixin:
    @staticmethod
    def parse_duration(value:str)->int:
        value = value.strip().lower()
        if value.endswith("mo"):
            unit = "mo"
            number = value[:-2]
            multiplier = 30 * 1440
        elif value.endswith("m"):
            unit = "m"
            number = value[:-1]
            multiplier = 1
        elif value.endswith("h"):
            unit = "h"
            number = value[:-1]
            multiplier = 60
        elif value.endswith("d"):
            unit = "d"
            number = value[:-1]
            multiplier = 1440
        elif value.endswith("y"):
            unit = "y"
            number = value[:-1]
            multiplier = 365 * 1440
        else:
            raise ValueError("Use m, h, d, mo or y")

        try:
            n = int(number)
        except (TypeError, ValueError):
            raise ValueError("Use m, h, d, mo or y")
        if n <= 0:
            raise ValueError("Duration must be positive")
        return n * multiplier

    @classmethod
    def parse_plan(cls,text:str):
        p=[x.strip() for x in text.split("|")]
        if len(p)!=4: raise ValueError("Use: Plan Name | Duration | Price | Stars")
        try:
            price = float(p[2])
            stars = int(p[3])
        except (TypeError, ValueError):
            raise ValueError("Price and Stars must be valid numbers")
        if price < 0: raise ValueError("Price cannot be negative")
        if stars < 0: raise ValueError("Stars cannot be negative")
        return p[0],p[1].lower(),cls.parse_duration(p[1]),price,stars

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

    async def management_role(self, update, context):
        """Return the active clone staff role for management features."""
        record = await self.staff_record(update, context)
        if not record or record.get("status", "active") != "active":
            return None
        return str(record.get("role") or "").lower()

    async def seller_or_admin(self, update, context):
        """True for the clone seller and promoted Admin staff only."""
        return (await self.management_role(update, context)) in {"seller", "admin"}

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

