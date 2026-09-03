"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.editor_engine import FEATURE_CALLBACKS


LIVE_SUPPORT_BUTTONS_HEADER = """🔗 Buttons

Set the buttons to be placed under the message.

Send a message structured as follows:

• Add a Single button:
Button title - t.me/LinkExample

• Add multiple buttons on a single line:
Button 1 - t.me/LinkExample && Button 2 - t.me/LinkExample

• Add multiple rows of buttons:
Button 1 - t.me/LinkExample
Button 2 - t.me/LinkExample

⭐ Special Button:
• Add a share button:
Button title - share: Text

⚡ Feature Buttons:
• Add a feature button:
Button title - feature: feature_name

Features:
plans, buy, profile, renew, referral, referral_unlock, support, home"""


def _live_support_parse_buttons(text: str):
    rows = []
    for line_no, raw_line in enumerate((text or '').splitlines(), 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        row = []
        for button_no, item in enumerate(raw_line.split('&&'), 1):
            item = item.strip()
            if ' - ' not in item:
                raise ValueError(f"Line {line_no}, button {button_no}: missing ' - '.")
            title, target = [x.strip() for x in item.split(' - ', 1)]
            if not title or not target:
                raise ValueError(f"Line {line_no}, button {button_no}: button title and target are required.")
            if target.startswith(('http://','https://','tg://')) or target.startswith('t.me/'):
                value = 'https://' + target if target.startswith('t.me/') else target
                row.append({'text': title, 'type': 'url', 'value': value})
            elif target.startswith('@'):
                username = target[1:].strip()
                if not username:
                    raise ValueError('Invalid Telegram username.')
                row.append({'text': title, 'type': 'url', 'value': f"https://t.me/{username}"})
            elif target.startswith('share:'):
                value = target.split(':',1)[1].strip()
                if not value:
                    raise ValueError('Share text is required.')
                row.append({'text': title, 'type': 'share', 'value': value})
            elif target.startswith('feature:'):
                feature = target.split(':',1)[1].strip().lower()
                callback = FEATURE_CALLBACKS.get(feature)
                if not callback:
                    raise ValueError(f"Unknown feature '{feature}'. Available: {', '.join(FEATURE_CALLBACKS)}")
                row.append({'text': title, 'type': 'callback', 'value': callback})
            else:
                raise ValueError("Only t.me URL, @username, share:, or feature: are supported.")
        rows.append(row)
    if not rows:
        raise ValueError('No buttons found.')
    return rows


def _live_support_buttons_input(rows):
    lines=[]
    reverse={v:k for k,v in FEATURE_CALLBACKS.items()}
    for row in rows or []:
        parts=[]
        for item in row or []:
            title=str(item.get('text') or 'Button')
            kind=str(item.get('type') or '')
            value=str(item.get('value') or '')
            if kind == 'url':
                value=value.replace('https://','',1) if value.startswith('https://') else value
            elif kind == 'share':
                value=f'share: {value}'
            elif kind == 'callback':
                value=f"feature: {reverse.get(value, value)}"
            parts.append(f'{title} - {value}')
        if parts: lines.append(' && '.join(parts))
    return '\n'.join(lines)


def _live_support_editor_header(title, item, extra=''):
    buttons=sum(len(r) for r in (item.get('buttons') or []))
    media='🖼 Media: ✅ Added' if item.get('media_file_id') else '🖼 Media: ❌ Not added'
    return (f"{title}\n\n"
            f"Status: {'🟢 Enabled' if item.get('enabled', True) else '🔴 Disabled'}\n"
            f"📝 Text: {'✅ Added' if item.get('text') else '❌ Not added'}\n"
            f"{media}\n"
            f"🔗 Buttons: {buttons}\n\n"
            "Use the options below to add, replace, preview, or remove each part." + (f"\n\n{extra}" if extra else ''))


def _live_support_media_prompt(title):
    return f"🖼 {title}\n\nSend one photo, video, GIF or document."


def _live_support_text_prompt(title):
    return (f"📝 {title}\n\nSeller, send now the message you want to set!\n\n"
            "You can use HTML and:\n"
            "• {ID} = user ID\n• {NAME} = first name\n• {SURNAME} = surname\n• {NAMESURNAME} = full name\n"
            "• {LANG} = user language\n• {DATE} = current date\n• {TIME} = current time\n• {WEEKDAY} = week day\n"
            "• {MENTION} = link to the user profile\n• {USERNAME} = username\n• {PLAN} = current plan\n• {EXPIRY} = subscription expiry")


def _live_support_component_keyboard(back, *, remove_callback=None, remove_label='Remove'):
    rows=[]
    if remove_callback:
        rows.append([InlineKeyboardButton(f'🗑 {remove_label}', callback_data=remove_callback)])
    rows.append([InlineKeyboardButton('⬅ Back', callback_data=back)])
    return InlineKeyboardMarkup(rows)


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_live_support':
        support = await get_live_support_settings(owner)
        blocked = await count_support_blocks(owner)
        await q.edit_message_text(self.live_support_text(support, blocked), reply_markup=self.live_support_menu(support))
        return True
    if a == 'a_live_support_toggle':
        support = await get_live_support_settings(owner)
        updated = await update_live_support_settings(owner, enabled=not bool(support.get('enabled')))
        blocked = await count_support_blocks(owner)
        await q.edit_message_text(self.live_support_text(updated, blocked), reply_markup=self.live_support_menu(updated))
        return True
    if a in {'a_live_support_mode_private', 'a_live_support_mode_topic'}:
        mode = 'private' if a.endswith('private') else 'topic'
        updated = await update_live_support_settings(owner, mode=mode)
        blocked = await count_support_blocks(owner)
        await q.edit_message_text(self.live_support_text(updated, blocked), reply_markup=self.live_support_menu(updated))
        return True
    if a == 'a_live_support_group_info':
        support = await get_live_support_settings(owner)
        await q.edit_message_text(f"📌 Support Group\n\nName: {support.get('support_group_title') or 'Not connected'}\nChat ID: {support.get('support_group_id') or '-'}\n\nGroup badalne ke liye naye forum group me /connectsupport bhejo.", reply_markup=self.back('a_live_support'))
        return True
    if a == 'a_live_support_blocks':
        blocked = await count_support_blocks(owner)
        await q.edit_message_text(f'🚫 Support-blocked users: {blocked}\n\nUser ke support topic ke first details message se Block/Unblock kiya ja sakta hai.', reply_markup=self.back('a_live_support'))
        return True
    if a == 'a_support_auto_replies':
        items = await list_support_auto_replies(owner)
        await q.edit_message_text(
            '💬 Auto Reply Keywords\n\nCreate a keyword first. After saving it, the editor opens so you can add text, media and buttons. When a customer sends that keyword, its saved reply is sent automatically.',
            reply_markup=self.support_auto_replies_menu(items),
        )
        return True
    if a == 'a_support_ar_add':
        context.user_data.clear(); context.user_data['wait_support_ar_keyword'] = True
        await q.edit_message_text('➕ Add Auto Reply Keyword\n\nSend one keyword or phrase.\nExample: payment', reply_markup=self.back('a_support_auto_replies')); return True
    if a.startswith('a_support_ar_view_'):
        keyword = a.replace('a_support_ar_view_', '')
        item = await get_support_auto_reply(owner, keyword)
        if not item:
            await q.edit_message_text('❌ Auto reply not found', reply_markup=self.back('a_support_auto_replies')); return True
        await q.edit_message_text(
            f"💬 Keyword Auto Reply\n\nKeyword: {keyword}\n\nWhen this keyword appears anywhere in a customer message or sentence, the configured reply is sent automatically.\n\n" + _live_support_editor_header('Current Setup', item),
            reply_markup=self.support_auto_reply_edit_menu(keyword, item),
        )
        return True
    if a.startswith('a_support_ar_keyword_'):
        keyword = a.replace('a_support_ar_keyword_', '')
        context.user_data.clear(); context.user_data['wait_support_ar_keyword_edit'] = keyword
        await q.edit_message_text('✏️ Change Keyword\n\nSend the new keyword or phrase.', reply_markup=self.back(f'a_support_ar_view_{keyword}')); return True
    if a.startswith('a_support_ar_toggle_'):
        keyword = a.replace('a_support_ar_toggle_', '')
        item = await get_support_auto_reply(owner, keyword)
        if not item:
            await q.edit_message_text('❌ Auto reply not found', reply_markup=self.back('a_support_auto_replies')); return True
        await save_support_auto_reply(owner, keyword, enabled=not bool(item.get('enabled', True)))
        item = await get_support_auto_reply(owner, keyword)
        await q.edit_message_text(f"💬 Keyword Auto Reply\n\nKeyword: {keyword}\n\n" + _live_support_editor_header('Current Setup', item), reply_markup=self.support_auto_reply_edit_menu(keyword, item)); return True
    if a.startswith('a_support_ar_text_'):
        keyword = a.replace('a_support_ar_text_', ''); item = await get_support_auto_reply(owner, keyword) or {}
        context.user_data.clear(); context.user_data['wait_support_ar_text'] = keyword
        await q.edit_message_text(_live_support_text_prompt('Auto Reply Text'), reply_markup=_live_support_component_keyboard(f'a_support_ar_view_{keyword}', remove_callback=f'a_support_ar_rmtext_{keyword}' if item.get('text') else None, remove_label='Remove Text')); return True
    if a.startswith('a_support_ar_media_'):
        keyword = a.replace('a_support_ar_media_', ''); item = await get_support_auto_reply(owner, keyword) or {}
        context.user_data.clear(); context.user_data['wait_support_ar_media'] = keyword
        await q.edit_message_text(_live_support_media_prompt('Auto Reply Media'), reply_markup=_live_support_component_keyboard(f'a_support_ar_view_{keyword}', remove_callback=f'a_support_ar_rmmedia_{keyword}' if item.get('media_file_id') else None, remove_label='Remove Media')); return True
    if a.startswith('a_support_ar_buttons_'):
        keyword = a.replace('a_support_ar_buttons_', ''); item = await get_support_auto_reply(owner, keyword) or {}
        context.user_data.clear(); context.user_data['wait_support_ar_buttons'] = keyword
        await q.edit_message_text(LIVE_SUPPORT_BUTTONS_HEADER, reply_markup=_live_support_component_keyboard(f'a_support_ar_view_{keyword}', remove_callback=f'a_support_ar_rmbuttons_{keyword}' if item.get('buttons') else None, remove_label='Remove Buttons')); return True
    if a.startswith('a_support_ar_see_text_'):
        keyword=a.replace('a_support_ar_see_text_',''); item=await get_support_auto_reply(owner,keyword)
        if item: await q.message.reply_text(item.get('text') or '❌ No text has been saved.')
        await q.answer('Saved text shown.'); return True
    if a.startswith('a_support_ar_see_media_'):
        keyword=a.replace('a_support_ar_see_media_',''); item=await get_support_auto_reply(owner,keyword)
        if item and item.get('media_file_id'):
            kind=item.get('media_type'); fid=item.get('media_file_id')
            if kind=='photo': await q.message.reply_photo(fid)
            elif kind=='video': await q.message.reply_video(fid)
            elif kind=='animation': await q.message.reply_animation(fid)
            else: await q.message.reply_document(fid)
        else: await q.message.reply_text('❌ No media has been saved.')
        await q.answer('Saved media shown.'); return True
    if a.startswith('a_support_ar_see_buttons_'):
        keyword=a.replace('a_support_ar_see_buttons_',''); item=await get_support_auto_reply(owner,keyword)
        if item:
            buttons=item.get('buttons') or []; raw=str(item.get('buttons_input') or '').strip() or _live_support_buttons_input(buttons)
            await q.message.reply_text(f'🔗 Current Buttons\n\n{raw}', reply_markup=self.build_welcome_keyboard(buttons))
        await q.answer('Saved buttons shown.'); return True
    if a.startswith('a_support_ar_preview_'):
        keyword=a.replace('a_support_ar_preview_',''); item=await get_support_auto_reply(owner,keyword)
        if item: await self.send_support_template(context,owner,q.from_user.id,item,q.from_user)
        await q.answer('Preview sent', show_alert=True); return True
    if a.startswith('a_support_ar_rmtext_'):
        keyword = a.replace('a_support_ar_rmtext_', '')
        await save_support_auto_reply(owner, keyword, text='')
        await q.edit_message_text('✅ Text removed', reply_markup=self.support_auto_reply_text_menu(keyword, False))
        return True
    if a.startswith('a_support_ar_rmmedia_'):
        keyword = a.replace('a_support_ar_rmmedia_', '')
        await save_support_auto_reply(owner, keyword, media_type='', media_file_id='')
        await q.edit_message_text('✅ Media removed', reply_markup=self.support_auto_reply_media_menu(keyword, False))
        return True
    if a.startswith('a_support_ar_rmbuttons_'):
        keyword = a.replace('a_support_ar_rmbuttons_', '')
        await save_support_auto_reply(owner, keyword, buttons=[])
        await q.edit_message_text('✅ Keyboard removed', reply_markup=self.support_auto_reply_buttons_menu(keyword, False))
        return True
    if a.startswith('a_support_ar_delete_'):
        keyword = a.replace('a_support_ar_delete_', '')
        await delete_support_auto_reply(owner, keyword)
        await q.edit_message_text('✅ Auto reply deleted', reply_markup=self.support_auto_replies_menu(await list_support_auto_replies(owner)))
        return True
    if a.startswith('a_support_ar_preview_'):
        keyword = a.replace('a_support_ar_preview_', '')
        item = await get_support_auto_reply(owner, keyword)
        if item:
            await self.send_support_template(context, owner, q.from_user.id, item, q.from_user)
        await q.answer('Preview sent', show_alert=True)
        return True
    if a == 'a_support_templates':
        templates = await list_support_templates(owner)
        await q.edit_message_text('📝 Reply Templates\n\nCreate a replacement keyword, then configure its text, media and buttons. When the seller sends that keyword alone in a customer support chat, the saved template is sent automatically.', reply_markup=self.support_templates_menu(templates)); return True
    if a == 'a_support_tpl_add':
        context.user_data.clear(); context.user_data['wait_support_tpl_command'] = True
        await q.edit_message_text('➕ Add Reply Template\n\nSend one unique keyword only.\nExample: payment', reply_markup=self.back('a_support_templates')); return True
    if a.startswith('a_support_tpl_view_'):
        command=a.replace('a_support_tpl_view_',''); tpl=await get_support_template(owner,command)
        if not tpl: await q.edit_message_text('❌ Template not found', reply_markup=self.back('a_support_templates')); return True
        auto_delete=_format_auto_delete(_template_auto_delete_seconds(tpl))
        await q.edit_message_text(f"📝 Business Reply Template\n\nTemplate Name: /{command}\nShortcut: {command}\n\n" + _live_support_editor_header('Current Setup',tpl,f'⏱ Auto Remove: {auto_delete}'), reply_markup=self.support_template_edit_menu(command,tpl)); return True
    if a.startswith('a_support_tpl_keyword_'):
        command=a.replace('a_support_tpl_keyword_',''); context.user_data.clear(); context.user_data['wait_support_tpl_command_edit']=command
        await q.edit_message_text('✏️ Change Keyword\n\nSend the new keyword.', reply_markup=self.back(f'a_support_tpl_view_{command}')); return True
    if a.startswith('a_support_tpl_toggle_'):
        command=a.replace('a_support_tpl_toggle_',''); tpl=await get_support_template(owner,command)
        if not tpl: await q.edit_message_text('❌ Template not found', reply_markup=self.back('a_support_templates')); return True
        await save_support_template(owner,command,enabled=not bool(tpl.get('enabled',True))); tpl=await get_support_template(owner,command)
        await q.edit_message_text(f"📝 Business Reply Template\n\nTemplate Name: /{command}\nShortcut: {command}\n\n" + _live_support_editor_header('Current Setup',tpl,f"⏱ Auto Remove: {_format_auto_delete(_template_auto_delete_seconds(tpl))}"), reply_markup=self.support_template_edit_menu(command,tpl)); return True
    if a.startswith('a_support_tpl_text_'):
        command=a.replace('a_support_tpl_text_',''); tpl=await get_support_template(owner,command) or {}
        context.user_data.clear(); context.user_data['wait_support_tpl_text']=command
        await q.edit_message_text(_live_support_text_prompt('Reply Template Text'), reply_markup=_live_support_component_keyboard(f'a_support_tpl_view_{command}',remove_callback=f'a_support_tpl_rmtext_{command}' if tpl.get('text') else None,remove_label='Remove Text')); return True
    if a.startswith('a_support_tpl_media_'):
        command=a.replace('a_support_tpl_media_',''); tpl=await get_support_template(owner,command) or {}
        context.user_data.clear(); context.user_data['wait_support_tpl_media']=command
        await q.edit_message_text(_live_support_media_prompt('Reply Template Media'), reply_markup=_live_support_component_keyboard(f'a_support_tpl_view_{command}',remove_callback=f'a_support_tpl_rmmedia_{command}' if tpl.get('media_file_id') else None,remove_label='Remove Media')); return True
    if a.startswith('a_support_tpl_buttons_'):
        command=a.replace('a_support_tpl_buttons_',''); tpl=await get_support_template(owner,command) or {}
        context.user_data.clear(); context.user_data['wait_support_tpl_buttons']=command
        await q.edit_message_text(LIVE_SUPPORT_BUTTONS_HEADER, reply_markup=_live_support_component_keyboard(f'a_support_tpl_view_{command}',remove_callback=f'a_support_tpl_rmbuttons_{command}' if tpl.get('buttons') else None,remove_label='Remove Buttons')); return True
    if a.startswith('a_support_tpl_see_text_'):
        command=a.replace('a_support_tpl_see_text_',''); tpl=await get_support_template(owner,command)
        if tpl: await q.message.reply_text(tpl.get('text') or '❌ No text has been saved.')
        await q.answer('Saved text shown.'); return True
    if a.startswith('a_support_tpl_see_media_'):
        command=a.replace('a_support_tpl_see_media_',''); tpl=await get_support_template(owner,command)
        if tpl and tpl.get('media_file_id'):
            kind=tpl.get('media_type'); fid=tpl.get('media_file_id')
            if kind=='photo': await q.message.reply_photo(fid)
            elif kind=='video': await q.message.reply_video(fid)
            elif kind=='animation': await q.message.reply_animation(fid)
            else: await q.message.reply_document(fid)
        else: await q.message.reply_text('❌ No media has been saved.')
        await q.answer('Saved media shown.'); return True
    if a.startswith('a_support_tpl_see_buttons_'):
        command=a.replace('a_support_tpl_see_buttons_',''); tpl=await get_support_template(owner,command)
        if tpl:
            buttons=tpl.get('buttons') or []; raw=str(tpl.get('buttons_input') or '').strip() or _live_support_buttons_input(buttons)
            await q.message.reply_text(f'🔗 Current Buttons\n\n{raw}',reply_markup=self.build_welcome_keyboard(buttons))
        await q.answer('Saved buttons shown.'); return True
    if a.startswith('a_support_tpl_preview_'):
        command=a.replace('a_support_tpl_preview_',''); tpl=await get_support_template(owner,command)
        if tpl: await self.send_support_template(context,owner,q.from_user.id,tpl,q.from_user)
        await q.answer('Preview sent',show_alert=True); return True
    if a.startswith('a_support_tpl_autodel_'):
        command = a.replace('a_support_tpl_autodel_', '')
        tpl = await get_support_template(owner, command)
        if not tpl:
            await q.edit_message_text('❌ Template not found', reply_markup=self.back('a_support_templates'))
            return True
        current = _template_auto_delete_seconds(tpl)
        await q.edit_message_text(f'⏱ Template Auto Remove — /{command}\n\nCurrent: {_format_auto_delete(current)}\n\nBot ka template reply selected time ke baad automatically remove hoga.', reply_markup=self.support_template_auto_delete_menu(command, current))
        return True
    if a.startswith('a_tpl_ad_custom_'):
        command = a.replace('a_tpl_ad_custom_', '')
        context.user_data.clear()
        context.user_data['wait_support_tpl_auto_delete'] = command
        await q.edit_message_text('⌨️ Custom auto-remove duration bhejo.\n\nExamples:\n30s = 30 seconds\n2m = 2 minutes\n1h = 1 hour\n6h = 6 hours\n1d = 1 day\noff = disable\n\nMaximum: 7 days', reply_markup=self.back(f'a_support_tpl_autodel_{command}'))
        return True
    if a.startswith('a_tpl_ad_'):
        payload = a.replace('a_tpl_ad_', '', 1)
        seconds_text, command = payload.split('_', 1)
        seconds = int(seconds_text)
        await save_support_template(owner, command, auto_delete_seconds=seconds)
        await q.edit_message_text(f'✅ Template Auto Remove updated\n\n/{command}: {_format_auto_delete(seconds)}', reply_markup=self.support_template_auto_delete_menu(command, seconds))
        return True
    if a.startswith('a_support_tpl_rmtext_'):
        command = a.replace('a_support_tpl_rmtext_', '')
        await save_support_template(owner, command, text='')
        await q.edit_message_text('✅ Text removed', reply_markup=self.support_template_text_menu(command, False))
        return True
    if a.startswith('a_support_tpl_rmmedia_'):
        command = a.replace('a_support_tpl_rmmedia_', '')
        await save_support_template(owner, command, media_type='', media_file_id='')
        await q.edit_message_text('✅ Media removed', reply_markup=self.support_template_media_menu(command, False))
        return True
    if a.startswith('a_support_tpl_rmbuttons_'):
        command = a.replace('a_support_tpl_rmbuttons_', '')
        await save_support_template(owner, command, buttons=[])
        await q.edit_message_text('✅ Keyboard removed', reply_markup=self.support_template_buttons_menu(command, False))
        return True
    if a.startswith('a_support_tpl_delete_'):
        command = a.replace('a_support_tpl_delete_', '')
        await delete_support_template(owner, command)
        await q.edit_message_text(f'✅ /{command} deleted', reply_markup=self.support_templates_menu(await list_support_templates(owner)))
        return True
    if a.startswith('a_support_tpl_preview_'):
        command = a.replace('a_support_tpl_preview_', '')
        tpl = await get_support_template(owner, command)
        await self.send_support_template(context, owner, q.from_user.id, tpl, q.from_user)
        await q.answer('Preview sent', show_alert=True)
        return True
    return False
