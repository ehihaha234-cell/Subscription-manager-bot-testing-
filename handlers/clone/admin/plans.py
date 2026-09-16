"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_plans':
        await q.edit_message_text('📦 Plan Management', reply_markup=self.plans_admin_menu())
        return True
    if a == 'a_plan_channels':
        plans = await get_plans(owner)
        channels = await get_channels(owner)
        if not plans:
            await q.edit_message_text('🔗 Plan - Channel Settings\n\n❌ No plans found. Add a plan first.', reply_markup=self.plans_admin_menu())
            return True
        if not channels:
            await q.edit_message_text('🔗 Plan - Channel Settings\n\n❌ No connected group/channel found. Connect a chat first.', reply_markup=self.plans_admin_menu())
            return True
        lines=['🔗 Plan - Channel Settings','', 'Select a plan to manage which connected groups/channels it belongs to.']
        kb=[]
        for plan in plans:
            assigned={int(x) for x in (plan.get('target_chat_ids') or [])}
            if not assigned:
                access='🌐 All connected chats (legacy/global)'
            else:
                access=f'🎯 {len(assigned)} chat(s) assigned'
            lines.append(f"\n📦 {plan.get('name','Plan')}\n{access}")
            kb.append([InlineKeyboardButton(f"⚙ {str(plan.get('name','Plan'))[:24]}", callback_data=f"a_plan_chat_{plan['plan_id']}")])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_plans')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_plan_chat_') and not a.startswith('a_plan_chat_toggle_'):
        plan_id=a.replace('a_plan_chat_','',1)
        plan=await get_plan(owner,plan_id)
        if not plan:
            await q.answer('Plan not found.', show_alert=True)
            return True
        channels=await get_channels(owner)
        if not channels:
            await q.edit_message_text('❌ No connected group/channel found.', reply_markup=self.back('a_plan_channels'))
            return True
        assigned={int(x) for x in (plan.get('target_chat_ids') or [])}
        lines=[f"🔗 Plan - Channel Settings\n\n📦 Plan: {plan.get('name','Plan')}", '', 'Tap a chat to enable/disable this plan for that chat.', '']
        kb=[]
        for ch in channels:
            cid=int(ch.get('chat_id'))
            enabled=cid in assigned
            title=str(ch.get('title') or 'Chat')
            lines.append(f"{('✅' if enabled else '❌')} {title}\n  {cid}")
            kb.append([InlineKeyboardButton(f"{('✅' if enabled else '❌')} {title[:28]}", callback_data=f"a_plan_chat_toggle_{plan_id}_{cid}")])
        if not assigned:
            lines.append('\n🌐 No specific chat is selected. This keeps the plan available as a global/legacy plan.')
        else:
            lines.append(f"\n🎯 Assigned chats: {len(assigned)}")
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_plan_channels')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_plan_chat_toggle_'):
        payload=a.replace('a_plan_chat_toggle_','',1)
        try:
            plan_id,chat_text=payload.rsplit('_',1)
            chat_id=int(chat_text)
        except (TypeError,ValueError):
            await q.answer('Invalid plan/chat.', show_alert=True)
            return True
        plan=await get_plan(owner,plan_id)
        channels=await get_channels(owner)
        connected={int(x.get('chat_id')) for x in channels if x.get('chat_id') is not None}
        if not plan or chat_id not in connected:
            await q.answer('Plan or connected chat not found.', show_alert=True)
            return True
        current=await toggle_plan_target_chat(owner,plan_id,chat_id)
        if current is None:
            await q.answer('Plan not found.', show_alert=True)
            return True
        # Re-render the same settings page without changing the rest of the UI.
        assigned=set(int(x) for x in current)
        lines=[f"🔗 Plan - Channel Settings\n\n📦 Plan: {plan.get('name','Plan')}", '', 'Tap a chat to enable/disable this plan for that chat.', '']
        kb=[]
        for ch in channels:
            cid=int(ch.get('chat_id'))
            enabled=cid in assigned
            title=str(ch.get('title') or 'Chat')
            lines.append(f"{('✅' if enabled else '❌')} {title}\n  {cid}")
            kb.append([InlineKeyboardButton(f"{('✅' if enabled else '❌')} {title[:28]}", callback_data=f"a_plan_chat_toggle_{plan_id}_{cid}")])
        if not assigned:
            lines.append('\n🌐 No specific chat is selected. This keeps the plan available as a global/legacy plan.')
        else:
            lines.append(f"\n🎯 Assigned chats: {len(assigned)}")
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_plan_channels')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a == 'a_plan_add':
        plan_cfg, _ = await effective_plan(self.seller_account(context))
        existing = len(await get_plans(owner))
        limit = int(plan_cfg.get('plan_limit', 2))
        if limit >= 0 and existing >= limit:
            await q.edit_message_text(await plan_limit_warning(self.seller_account(context)), reply_markup=self.limit_keyboard('a_plans'))
            return True
        context.user_data.clear()
        context.user_data['wait_plan_add'] = True
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        await q.edit_message_text(f'➕ Add Subscription Plan\n\nCurrency: {currency_symbol(code)} {code} — {currency_name(code)}\n\nSend: Plan Name | Duration | Price | Stars\nExample: Premium | 30d | 199 | 99\n\nDuration: m = minutes, h = hours, d = days, mo = months, y = years\n\nPrice uses the current bot currency. Changing currency later changes the label, not the numeric price.', reply_markup=self.back('a_plans'))
        return True
    if a == 'a_plan_list':
        plans = await get_plans(owner)
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        lines = [f'📋 Plans\n\n💱 Currency: {currency_symbol(code)} {code} — {currency_name(code)}\n']
        kb = []
        for p in plans:
            assigned = [int(x) for x in (p.get('target_chat_ids') or []) if str(x).lstrip('-').isdigit()]
            access = '🌐 All connected chats' if not assigned else f'🎯 {len(assigned)} chat(s)'
            lines.append(f"{('✅' if p.get('active') else '⏸')} {p['name']} — {p['duration_text']} — {format_currency(code, p['price'])} — ⭐{int(p.get('stars_price',0) or 0)}\n   {access}")
            kb.append([InlineKeyboardButton(f"✏ {p['name'][:16]}", callback_data=f"a_plan_edit_{p['plan_id']}"), InlineKeyboardButton('🗑', callback_data=f"a_plan_del_{p['plan_id']}")])
            kb.append([InlineKeyboardButton('⏸ Disable' if p.get('active') else '▶ Enable', callback_data=f"a_plan_toggle_{p['plan_id']}")])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_plans')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_plan_edit_'):
        context.user_data.clear()
        context.user_data['wait_plan_edit'] = a.replace('a_plan_edit_', '')
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        await q.edit_message_text(f'✏️ Edit Subscription Plan\n\nCurrency: {currency_symbol(code)} {code}\n\nSend new: Plan Name | Duration | Price | Stars\nExample: Premium | 30d | 199 | 99\n\nDuration: m = minutes, h = hours, d = days, mo = months, y = years', reply_markup=self.back('a_plan_list'))
        return True
    if a.startswith('a_plan_del_'):
        await delete_plan(owner, a.replace('a_plan_del_', ''))
        await q.edit_message_text('✅ Plan deleted', reply_markup=self.plans_admin_menu())
        return True
    if a.startswith('a_plan_toggle_'):
        pid = a.replace('a_plan_toggle_', '')
        p = await get_plan(owner, pid)
        await update_plan(owner, pid, active=not bool(p.get('active')))
        await q.edit_message_text('✅ Plan status updated', reply_markup=self.plans_admin_menu())
        return True
    return False
