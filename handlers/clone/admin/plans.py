"""Seller plan-target bundle management and per-bundle subscription plans."""

from handlers.common.clone_context import *


def _group_label(group):
    targets = group.get("targets") or []
    names = [str(x.get("title") or x.get("chat_id")) for x in targets]
    return ", ".join(names) if names else ", ".join(str(x) for x in group.get("chat_ids") or [])


def _group_plan_text(plans, currency):
    if not plans:
        return "📋 No plans added yet."
    lines = []
    for p in plans:
        status = "✅" if p.get("active") else "⏸"
        lines.append(f"{status} {p['name']} — {p['duration_text']} — {format_currency(currency, p['price'])} — ⭐{int(p.get('stars_price', 0) or 0)}")
    return "\n".join(lines)


async def _main(self, q, owner):
    # One-time compatibility migration: old global plans used to target every
    # connected chat. Convert them into one bundle so existing sellers keep the
    # exact same plan behaviour after the new UI is enabled.
    groups = await get_plan_groups(owner)
    if not groups:
        legacy = await get_plans(owner, group_id=None)
        legacy = [p for p in legacy if not p.get("group_id")]
        if legacy:
            chats = await get_channels(owner)
            if chats:
                try:
                    migrated = await create_plan_group(owner, [int(x["chat_id"]) for x in chats])
                    for plan in legacy:
                        await update_plan(owner, plan["plan_id"], group_id=migrated["group_id"], target_chat_ids=migrated["chat_ids"])
                    groups = await get_plan_groups(owner)
                except Exception:
                    pass
    settings = await get_seller_settings(owner)
    currency = normalize_currency(settings.get('currency')) or 'INR'

    # Show a compact summary in the main Plan Management header. Each target
    # bundle owns its own plan list, so the header is calculated from the same
    # bundle -> plans relationship used by View Plans. This keeps the summary
    # accurate when plans are added, deleted, enabled/disabled, or when a
    # multi-chat bundle is used.
    lines = ["📦 Plan Management", ""]
    if groups:
        lines.append("📊 Current Plan Summary")
        lines.append("")
        for index, group in enumerate(groups, 1):
            label = _group_label(group) or "Unnamed group/channel"
            plans = await get_plans(owner, group_id=str(group["group_id"]))
            lines.append(f"{index}. {label}:")
            lines.append(f"   Plans : {len(plans)}")
            if plans:
                for plan in plans:
                    price = format_currency(currency, plan.get("price", 0))
                    stars = int(plan.get("stars_price", 0) or 0)
                    lines.append(
                        f"      {plan.get('name', 'Unnamed')} / "
                        f"{plan.get('duration_text', '')} / {price} / ⭐{stars}"
                    )
            else:
                lines.append("      No plans added")
            lines.append("")
        lines.append("Select a connected group/channel bundle to manage its plans.")
    else:
        lines.append("No plan target created yet.")
        lines.append("Tap ➕ Create New Plan, select one or more connected groups/channels, then add plans.")
    kb = []
    for group in groups:
        label = _group_label(group)
        gid = str(group["group_id"])
        kb.append([
            InlineKeyboardButton(label[:28], callback_data=f"a_plan_group_info_{gid}"),
            InlineKeyboardButton("📋 View Plans", callback_data=f"a_plan_group_view_{gid}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"a_plan_group_del_{gid}"),
        ])
    kb.append([InlineKeyboardButton("➕ Create New Plan", callback_data="a_plan_add")])
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="a_home")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


async def _selection(self, q, owner, context):
    channels = await get_channels(owner)
    selected = set(int(x) for x in (context.user_data.get("plan_group_selected_chats") or []))
    lines = ["➕ Create New Plan", "", "Select one or more connected group/channel:", ""]
    kb = []
    for ch in channels:
        cid = int(ch["chat_id"])
        mark = "✅" if cid in selected else "☐"
        title = str(ch.get("title") or cid)
        lines.append(f"{mark} {title}")
        kb.append([InlineKeyboardButton(f"{mark} {title[:35]}", callback_data=f"a_plan_group_toggle_{cid}")])
    if not channels:
        lines.append("No connected groups/channels found.")
    # No Save button: Back confirms the current selection and creates the bundle.
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="a_plan_group_save")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_plans':
        await _main(self, q, owner)
        return True

    if a == 'a_plan_add':
        # Do not clear the complete user_data here. Other admin flows may keep
        # state there, and clearing it can make the callback appear to do
        # nothing when PTB persistence/other handlers are active. Only reset
        # the temporary selection state used by this screen.
        context.user_data.pop('plan_group_selected_chats', None)
        try:
            await _selection(self, q, owner, context)
        except Exception as exc:
            logger.exception('Failed to open Create New Plan screen owner=%s', owner)
            try:
                await q.answer('Unable to open Create New Plan. Please try again.', show_alert=True)
            except Exception:
                pass
        return True

    if a.startswith('a_plan_group_toggle_'):
        try:
            cid = int(a.replace('a_plan_group_toggle_', ''))
        except (TypeError, ValueError):
            await q.answer('Invalid group/channel selection.', show_alert=True)
            return True
        selected = set(int(x) for x in (context.user_data.get('plan_group_selected_chats') or []))
        if cid in selected:
            selected.remove(cid)
        else:
            selected.add(cid)
        context.user_data['plan_group_selected_chats'] = list(selected)
        await _selection(self, q, owner, context)
        return True

    if a == 'a_plan_group_save':
        selected = []
        for value in (context.user_data.get('plan_group_selected_chats') or []):
            try:
                cid = int(value)
            except (TypeError, ValueError):
                continue
            if cid not in selected:
                selected.append(cid)
        if not selected:
            await q.answer('Select at least one group/channel.', show_alert=True)
            return True
        try:
            group = await create_plan_group(owner, selected)
        except Exception as exc:
            await q.answer(str(exc), show_alert=True)
            return True
        context.user_data.clear()
        await _main(self, q, owner)
        return True

    if a.startswith('a_plan_group_info_'):
        gid = a.replace('a_plan_group_info_', '')
        group = await get_plan_group(owner, gid)
        if not group:
            await q.answer('Plan group not found.', show_alert=True)
            return True
        settings = await get_seller_settings(owner)
        currency = normalize_currency(settings.get('currency')) or 'INR'
        plans = await get_plans(owner, True, gid)
        text = f"📋 {_group_label(group)}\n\n{_group_plan_text(plans, currency)}"
        await q.answer(text[:190], show_alert=True)
        return True

    if a.startswith('a_plan_group_del_'):
        gid = a.replace('a_plan_group_del_', '')
        await delete_plan_group(owner, gid)
        await _main(self, q, owner)
        return True

    if a.startswith('a_plan_group_view_'):
        gid = a.replace('a_plan_group_view_', '')
        group = await get_plan_group(owner, gid)
        if not group:
            await q.answer('Plan group not found.', show_alert=True)
            return True
        plans = await get_plans(owner, group_id=gid)
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        lines = [f"📋 Plans — {_group_label(group)}", "", f"💱 Currency: {currency_symbol(code)} {code} — {currency_name(code)}", ""]
        kb = []
        for p in plans:
            lines.append(f"{('✅' if p.get('active') else '⏸')} {p['name']} — {p['duration_text']} — {format_currency(code, p['price'])} — ⭐{int(p.get('stars_price',0) or 0)}")
            kb.append([InlineKeyboardButton(f"✏️ {p['name'][:16]}", callback_data=f"a_plan_edit_{p['plan_id']}"), InlineKeyboardButton("🗑️", callback_data=f"a_plan_del_{p['plan_id']}")])
            kb.append([InlineKeyboardButton('⏸️ Disable' if p.get('active') else '▶️ Enable', callback_data=f"a_plan_toggle_{p['plan_id']}")])
        kb.append([InlineKeyboardButton("➕ Add Plan", callback_data=f"a_plan_group_add_{gid}")])
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="a_plans")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True

    if a.startswith('a_plan_group_add_'):
        gid = a.replace('a_plan_group_add_', '')
        if not await get_plan_group(owner, gid):
            await q.answer('Plan group not found.', show_alert=True)
            return True
        plan_cfg, _ = await effective_plan(self.seller_account(context))
        existing = len(await get_plans(owner, group_id=gid))
        limit = int(plan_cfg.get('plan_limit', 2))
        if limit >= 0 and existing >= limit:
            await q.edit_message_text(await plan_limit_warning(self.seller_account(context)), reply_markup=self.limit_keyboard(f'a_plan_group_view_{gid}'))
            return True
        context.user_data.clear()
        context.user_data['wait_plan_add'] = {'group_id': gid}
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        await q.edit_message_text(f"➕ Add Plan — {_group_label(await get_plan_group(owner, gid))}\n\nCurrency: {currency_symbol(code)} {code} — {currency_name(code)}\n\nSend: Plan Name | Duration | Price | Stars\nExample: Premium | 30d | 199 | 99\n\nDuration: m = minutes, h = hours, d = days, mo = months, y = years", reply_markup=self.back(f'a_plan_group_view_{gid}'))
        return True

    if a.startswith('a_plan_edit_'):
        pid = a.replace('a_plan_edit_', '')
        plan = await get_plan(owner, pid)
        if not plan:
            await q.answer('Plan not found.', show_alert=True)
            return True
        context.user_data.clear()
        context.user_data['wait_plan_edit'] = pid
        settings = await get_seller_settings(owner)
        code = normalize_currency(settings.get('currency')) or 'INR'
        gid = str(plan.get('group_id') or '')
        back = f'a_plan_group_view_{gid}' if gid else 'a_plans'
        await q.edit_message_text(f'✏️ Edit Subscription Plan\n\nCurrency: {currency_symbol(code)} {code} — {currency_name(code)}\n\nSend new: Plan Name | Duration | Price | Stars\nExample: Premium | 30d | 199 | 99\n\nDuration: m = minutes, h = hours, d = days, mo = months, y = years', reply_markup=self.back(back))
        return True

    if a.startswith('a_plan_del_'):
        pid = a.replace('a_plan_del_', '')
        plan = await get_plan(owner, pid)
        gid = str((plan or {}).get('group_id') or '')
        await delete_plan(owner, pid)
        await q.edit_message_text('✅ Plan deleted', reply_markup=self.back(f'a_plan_group_view_{gid}' if gid else 'a_plans'))
        return True

    if a.startswith('a_plan_toggle_'):
        pid = a.replace('a_plan_toggle_', '')
        p = await get_plan(owner, pid)
        if not p:
            await q.answer('Plan not found.', show_alert=True)
            return True
        await update_plan(owner, pid, active=not bool(p.get('active')))
        gid = str(p.get('group_id') or '')
        if gid:
            # Re-render the scoped list after the toggle.
            group = await get_plan_group(owner, gid)
            plans = await get_plans(owner, group_id=gid)
            settings = await get_seller_settings(owner)
            code = normalize_currency(settings.get('currency')) or 'INR'
            lines = [f"📋 Plans — {_group_label(group)}", "", f"💱 Currency: {currency_symbol(code)} {code} — {currency_name(code)}", ""]
            kb=[]
            for plan in plans:
                lines.append(f"{('✅' if plan.get('active') else '⏸')} {plan['name']} — {plan['duration_text']} — {format_currency(code, plan['price'])} — ⭐{int(plan.get('stars_price',0) or 0)}")
                kb.append([InlineKeyboardButton(f"✏️ {plan['name'][:16]}", callback_data=f"a_plan_edit_{plan['plan_id']}"), InlineKeyboardButton("🗑️", callback_data=f"a_plan_del_{plan['plan_id']}")])
                kb.append([InlineKeyboardButton('⏸️ Disable' if plan.get('active') else '▶️ Enable', callback_data=f"a_plan_toggle_{plan['plan_id']}")])
            kb.append([InlineKeyboardButton("➕ Add Plan", callback_data=f"a_plan_group_add_{gid}")])
            kb.append([InlineKeyboardButton("⬅ Back", callback_data="a_plans")])
            await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
        else:
            await _main(self, q, owner)
        return True
    return False
