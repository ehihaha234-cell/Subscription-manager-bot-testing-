"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import feature_back_callback


class ClonePlansMixin:
    async def show_plans(self, q, owner, select=False, context=None, force_new_message=False, target_chat_ids=None, group_id=None):
        plans=await get_plans(owner,True, group_id=group_id) if group_id else await get_plans(owner,True)
        target_chat_ids=[int(x) for x in (target_chat_ids or [])]
        if target_chat_ids:
            wanted=set(target_chat_ids)
            filtered=[]
            for plan in plans:
                assigned={int(x) for x in (plan.get("target_chat_ids") or [])}
                # Legacy/unassigned plans remain available everywhere. A
                # targeted plan appears only when its assignment exactly covers
                # the destination set requested by this welcome button.
                if not assigned or assigned == wanted:
                    filtered.append(plan)
            plans=filtered
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

        if context is not None:
            context.user_data["selected_child_target_chat_ids"] = target_chat_ids
        kb=[]
        lines=[f"📋 Available Plans\n\n💱 Currency: {currency_symbol(currency)} {currency}\n"]
        if target_chat_ids:
            lines.append(f"🎯 Access: {len(target_chat_ids)} connected chat(s)\n")

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

        markup = InlineKeyboardMarkup(kb)
        if force_new_message:
            try:
                await q.message.delete()
            except Exception:
                pass
            await q.message.chat.send_message(
                "\n".join(lines),
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return

        await self.safe_query_message(
            q,
            "\n".join(lines),
            markup,
        )

