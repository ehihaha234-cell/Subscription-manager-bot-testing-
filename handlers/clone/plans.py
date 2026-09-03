"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import feature_back_callback


class ClonePlansMixin:
    async def show_plans(self, q, owner, select=False, context=None):
        plans=await get_plans(owner,True)
        settings=await get_seller_settings(owner)
        currency=normalize_currency(settings.get("currency")) or "INR"
        back_target = feature_back_callback(context) if context is not None else "c_home"
        back_keyboard=self.back(back_target)

        if not plans:
            await self.safe_query_message(
                q,
                "📋 No plans available.",
                back_keyboard,
            )
            return

        kb=[]
        lines=[f"📋 Available Plans\n\n💱 Currency: {currency_symbol(currency)} {currency}\n"]

        for p in plans:
            lines.append(
                f"• {p['name']} — {p['duration_text']} — "
                f"{format_currency(currency, p['price'])}"
            )

            if select:
                kb.append([
                    InlineKeyboardButton(
                        f"Buy {p['name']} - {format_currency(currency, p['price'])}",
                        callback_data=f"c_select_{p['plan_id']}",
                    )
                ])

        kb.append([
            InlineKeyboardButton("⬅ Back", callback_data=back_target)
        ])

        await self.safe_query_message(
            q,
            "\n".join(lines),
            InlineKeyboardMarkup(kb),
        )

