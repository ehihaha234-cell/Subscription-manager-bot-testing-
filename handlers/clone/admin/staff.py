"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_staff':
        await q.edit_message_text('👮 Staff Management\n\nPromote trusted people as Admin or Moderator for this clone bot.\n\nAdmin: broad management access\nModerator: users, pending payments and live support', reply_markup=self.staff_menu())
        return True
    if a in {'a_staff_add_admin', 'a_staff_add_moderator'}:
        context.user_data.clear()
        context.user_data['wait_staff_promote'] = 'admin' if a.endswith('admin') else 'moderator'
        await q.edit_message_text('Send the Telegram User ID of the person you want to promote.\n\nThe person must start this clone bot once before using staff access.', reply_markup=self.back('a_staff'))
        return True
    if a == 'a_staff_list':
        rows = await list_staff(owner)
        if not rows:
            await q.edit_message_text('📋 Staff List\n\nNo staff members added.', reply_markup=self.back('a_staff'))
            return True
        kb = []
        lines = ['📋 Staff List\n']
        for row in rows:
            uid = int(row['user_id'])
            role_name = str(row.get('role', 'moderator')).title()
            status = row.get('status', 'active')
            label = '@' + row.get('username') if row.get('username') else row.get('full_name') or str(uid)
            lines.append(f'• {label} — {role_name} — {status.title()}')
            kb.append([InlineKeyboardButton(f'{role_name}: {label}', callback_data=f'a_staff_view_{uid}')])
        kb.append([InlineKeyboardButton('⬅ Back', callback_data='a_staff')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return True
    if a.startswith('a_staff_view_'):
        uid = int(a.replace('a_staff_view_', ''))
        row = await active_staff(owner, uid)
        if not row:
            all_rows = await list_staff(owner)
            row = next((x for x in all_rows if int(x.get('user_id', 0)) == uid), None)
        if not row:
            await q.edit_message_text('❌ Staff member not found.', reply_markup=self.back('a_staff_list'))
            return True
        label = '@' + row.get('username') if row.get('username') else row.get('full_name') or 'Not available'
        text = f"👮 Staff Details\n\nName: {label}\nUser ID: {uid}\nRole: {str(row.get('role', '')).title()}\nStatus: {str(row.get('status', 'active')).title()}\nTotal Actions: {int(row.get('total_actions', 0))}\nLast Action: {row.get('last_action') or 'No activity yet'}"
        await q.edit_message_text(text, reply_markup=self.staff_item_menu(uid, row.get('status') == 'suspended'))
        return True
    if a.startswith('a_staff_status_'):
        _, _, _, uid, status = a.split('_', 4)
        await set_staff_status(owner, int(uid), status)
        await q.edit_message_text(f'✅ Staff status updated: {status.title()}', reply_markup=self.back('a_staff_list'))
        return True
    if a.startswith('a_staff_remove_'):
        uid = int(a.replace('a_staff_remove_', ''))
        await remove_staff(owner, uid)
        await q.edit_message_text('✅ Staff member removed.', reply_markup=self.back('a_staff_list'))
        return True
    return False
