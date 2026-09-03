"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.clone.help_center import HELP_PAGES, help_home_keyboard, help_home_text, help_page_keyboard


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_help':
        await q.edit_message_text(help_home_text(), reply_markup=help_home_keyboard())
        return True
    if a.startswith('a_help_'):
        page_key = a[len('a_help_'):]
        page_text = HELP_PAGES.get(page_key)
        if page_text is None:
            await q.answer('Help section not found.', show_alert=True)
            return True
        await q.edit_message_text(page_text, reply_markup=help_page_keyboard())
        return True
    if a == 'a_terms':
        parts = []
        for key in ('terms', 'privacy', 'refund', 'support'):
            policy = await get_policy(key)
            parts.append(f"{key.title()}:\n{policy.get('text')}")
        await q.edit_message_text('📜 Terms & Policy\n\n' + '\n\n'.join(parts), reply_markup=self.admin_menu())
        return True
    return False
