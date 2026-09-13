"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *


async def handle(self, update, context, q, owner, staff, a, role):
    if a == 'a_pg_home':
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        gateways = cfg.get('gateways') or {}
        rz = gateways.get('razorpay') or {}
        cf = gateways.get('cashfree') or {}
        lines = ['🌐 Automatic Payment Gateways', '', f"{('✅' if rz.get('enabled') else '❌')} Razorpay: {('Enabled' if rz.get('enabled') else 'Disabled')} | Credentials: {('Added' if rz.get('key_id') and rz.get('key_secret') else 'Not added')}", f"{('✅' if cf.get('enabled') else '❌')} Cashfree: {('Enabled' if cf.get('enabled') else 'Disabled')} | Credentials: {('Added' if cf.get('client_id') and cf.get('client_secret') else 'Not added')}"]
        rows = []
        for gateway in ('razorpay', 'cashfree'):
            g = gateways.get(gateway, {})
            rows.append([InlineKeyboardButton(f"{('✅' if g.get('enabled') else '❌')} {gateway.title()}", callback_data=f'a_pg_view_{gateway}')])
        rows += [[InlineKeyboardButton('📜 Gateway History', callback_data='a_pg_history')], [InlineKeyboardButton('⬅ Back', callback_data='a_payment')]]
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return True
    if a.startswith('a_pg_view_'):
        gateway = a.replace('a_pg_view_', '')
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        g = (cfg.get('gateways') or {}).get(gateway, {})
        if gateway == 'razorpay':
            await q.edit_message_text(_seller_razorpay_text(g), reply_markup=_seller_razorpay_keyboard(bool(g.get('enabled'))))
            return True
        details = f"Client ID: {('Added' if g.get('client_id') else 'Not added')}\nClient Secret: {('Added' if g.get('client_secret') else 'Not added')}"
        await q.edit_message_text(f"💳 Cashfree\n\nStatus: {('Enabled ✅' if g.get('enabled') else 'Disabled ❌')}\n{details}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⛔ Disable' if g.get('enabled') else '✅ Enable', callback_data='a_pg_toggle_cashfree')], [InlineKeyboardButton('🔑 Set / Replace Credentials', callback_data='a_pg_creds_cashfree')], [InlineKeyboardButton('✅ Test Connection', callback_data='a_pg_testconn_cashfree')], [InlineKeyboardButton('⬅ Back', callback_data='a_pg_home')]]))
        return True
    if a.startswith('a_pg_toggle_'):
        gateway = a.replace('a_pg_toggle_', '')
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        g = (cfg.get('gateways') or {}).get(gateway, {})
        try:
            await save_gateway_config('seller', owner, gateway, {'enabled': not bool(g.get('enabled')), 'mode': 'live'})
        except Exception as exc:
            await q.answer(str(exc), show_alert=True)
            return True
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        g = (cfg.get('gateways') or {}).get(gateway, {})
        if gateway == 'razorpay':
            await q.edit_message_text(_seller_razorpay_text(g), reply_markup=_seller_razorpay_keyboard(bool(g.get('enabled'))))
            return True
        details = f"Client ID: {('Added' if g.get('client_id') else 'Not added')}\nClient Secret: {('Added' if g.get('client_secret') else 'Not added')}"
        await q.edit_message_text(f"💳 Cashfree\n\nStatus: {('Enabled ✅' if g.get('enabled') else 'Disabled ❌')}\n{details}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⛔ Disable' if g.get('enabled') else '✅ Enable', callback_data='a_pg_toggle_cashfree')], [InlineKeyboardButton('🔑 Set / Replace Credentials', callback_data='a_pg_creds_cashfree')], [InlineKeyboardButton('✅ Test Connection', callback_data='a_pg_testconn_cashfree')], [InlineKeyboardButton('⬅ Back', callback_data='a_pg_home')]]))
        return True
    if a == 'a_pg_webhook_secret':
        context.user_data.clear()
        context.user_data['wait_pg_webhook_secret'] = True
        await q.edit_message_text('🔐 Set Webhook Secret\n\nSend the same Webhook Secret that you created in Razorpay Dashboard.\n\nRazorpay Key Secret and Webhook Secret are different.', reply_markup=self.back('a_pg_view_razorpay'))
        return True
    if a == 'a_pg_webhook_setup':
        try:
            cfg = await get_gateway_config('seller', owner, decrypt=True)
            g = (cfg.get('gateways') or {}).get('razorpay', {})
            await q.edit_message_text(_seller_webhook_setup_text(owner, g), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧪 Test Webhook', callback_data='a_pg_test_webhook')], [InlineKeyboardButton('📖 Setup Guide', callback_data='a_pg_webhook_guide')], [InlineKeyboardButton('⬅ Back', callback_data='a_pg_view_razorpay')]]))
        except Exception as exc:
            logger.exception('Seller Razorpay webhook setup page failed for owner=%s', owner)
            await q.answer('Webhook Setup could not open. Please try again.', show_alert=True)
        return True
    if a == 'a_pg_webhook_guide':
        links = await get_official_links()
        rows = []
        if links.get('support'):
            rows.append([InlineKeyboardButton('💬 Contact Support', url=links['support'])])
        rows.append([InlineKeyboardButton('⬅ Back', callback_data='a_pg_webhook_setup')])
        await q.edit_message_text(_seller_webhook_guide_text(), reply_markup=InlineKeyboardMarkup(rows))
        return True
    if a == 'a_pg_test_webhook':
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        g = (cfg.get('gateways') or {}).get('razorpay', {})
        received = g.get('last_webhook_received_at')
        if received:
            when = received.strftime('%Y-%m-%d %H:%M UTC') if isinstance(received, datetime) else str(received)
            text = f'✅ Test Webhook Received\n\nA valid Razorpay webhook signature was received successfully.\nLast received: {when}'
        else:
            text = '🧪 Razorpay Webhook Test\n\nNo valid webhook has been received yet.\n\nSend a test webhook from Razorpay Dashboard or complete a test payment, then tap Check Again.'
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Check Again', callback_data='a_pg_test_webhook')], [InlineKeyboardButton('📖 Setup Guide', callback_data='a_pg_webhook_guide')], [InlineKeyboardButton('⬅ Back', callback_data='a_pg_webhook_setup')]]))
        return True
    if a.startswith('a_pg_testconn_'):
        gateway = a.replace('a_pg_testconn_', '')
        try:
            await test_gateway_connection('seller', owner, gateway)
            await q.edit_message_text(f'✅ {gateway.title()} connection successful.\n\nAPI access verified.', reply_markup=self.back(f'a_pg_view_{gateway}'))
        except GatewayError as exc:
            await q.edit_message_text(f'❌ {gateway.title()} connection failed.\n\n{exc}', reply_markup=self.back(f'a_pg_view_{gateway}'))
        return True
    if a.startswith('a_pg_creds_'):
        gateway = a.replace('a_pg_creds_', '')
        context.user_data.clear()
        context.user_data['wait_pg_credentials'] = gateway
        help_text = {'razorpay': 'KEY_ID | KEY_SECRET', 'cashfree': 'CLIENT_ID | CLIENT_SECRET'}[gateway]
        await q.edit_message_text(f'Send credentials in one message:\n{help_text}', reply_markup=self.back(f'a_pg_view_{gateway}'))
        return True
    if a == 'a_pg_history':
        items = await gateway_history('seller', owner, 25)
        settings = await get_seller_settings(owner)
        currency = settings.get('currency')
        text = '📜 Gateway History\n\n' + '\n'.join((f"• {x.get('gateway', '-').title()} {format_currency(currency, x.get('amount', 0))} — {x.get('status')}" for x in items))
        await q.edit_message_text(text if items else '📜 No gateway payments yet.', reply_markup=self.back('a_pg_home'))
        return True
    return False
