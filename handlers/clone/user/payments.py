"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from database.payment_gateways import (
    update_gateway_transaction,
    claim_razorpay_qr_pool_entry,
    cache_razorpay_qr_telegram_file_id,
)
from services.payment_gateways import cancel_previous_razorpay_qr_for_same_plan
from handlers.common.feature_navigation import feature_back_callback
import io
import time
import qrcode


def _razorpay_qr_photo(checkout: dict):
    """Build the QR locally from Razorpay's returned UPI URI; no image download."""
    content = str(checkout.get("qr_image_content") or "").strip()
    if not content:
        return None
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image()
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    stream.seek(0)
    stream.name = "razorpay_qr.png"
    return stream


async def _claim_precreated_razorpay_qr(tx: dict, plan: dict, owner: int, currency: str) -> dict | None:
    pool = await claim_razorpay_qr_pool_entry(
        owner, str(plan["plan_id"]), float(plan["price"]), currency, str(tx["transaction_id"]),
        bot_id=int((tx.get("metadata") or {}).get("bot_id") or 0),
    )
    if not pool:
        return None
    checkout = {
        "gateway_order_id": str(pool.get("qr_code_id") or ""),
        "checkout_url": str(pool.get("image_url") or ""),
        "qr_code_id": str(pool.get("qr_code_id") or ""),
        "qr_image_url": str(pool.get("image_url") or ""),
        "qr_image_content": str(pool.get("image_content") or ""),
        "telegram_file_id": str(pool.get("telegram_file_id") or ""),
        "qr_close_by": int(pool.get("qr_close_by") or 0),
        "checkout_mode": "upi_qr",
        "gateway_response": pool.get("gateway_response") or {},
        "status": "pending",
    }
    await update_gateway_transaction(tx["transaction_id"], **checkout)
    return checkout


async def handle(self, update, context, q, owner, action):
    back_keyboard = self.back(feature_back_callback(context))
    if action.startswith('c_select_'):
        plan = await get_plan(owner, action.replace('c_select_', ''))
        if not plan:
            await q.answer('Plan not found', show_alert=True)
            return True
        context.user_data['selected_child_plan'] = plan
        bot_id = int(context.application.bot_data.get('seller_bot_id') or 0)
        gateway_cfg = await get_gateway_config('seller', owner, decrypt=True)
        gateways = gateway_cfg.get('gateways') or {}
        razorpay_settings = gateways.get('razorpay') or {}
        if (razorpay_settings.get('enabled') and
                str(razorpay_settings.get('checkout_mode') or 'upi_qr').lower() == 'upi_qr' and
                bot_id):
            # Same plan: replace the user's previous QR. Other plans remain visible
            # and usable until their own QR expires.
            try:
                await cancel_previous_razorpay_qr_for_same_plan(
                    context.bot, owner, q.from_user.id, bot_id, str(plan['plan_id'])
                )
            except Exception:
                logger.exception('Could not replace previous Razorpay QR for same plan')
        s = await get_seller_settings(owner)
        qr_file_id = await get_bot_payment_qr(int(context.application.bot_data.get('seller_bot_id') or 0))
        if not qr_file_id:
            qr_file_id = str(s.get('upi_qr_file_id') or '')
        currency = normalize_currency(s.get('currency')) or 'INR'
        enabled = [g for g in SUPPORTED_GATEWAYS if (gateways.get(g) or {}).get('enabled')]
        if currency != 'INR':
            enabled = []
        default_gateway = str(gateway_cfg.get('default_gateway') or '')
        if default_gateway in enabled:
            enabled.remove(default_gateway)
            enabled.insert(0, default_gateway)
        manual_enabled = bool(gateway_cfg.get('manual_enabled', True))
        stars_enabled = bool(gateway_cfg.get('stars_enabled', False))
        rows = []
        text = ''
        if enabled:
            gateway = enabled[0]
            tx = await create_gateway_transaction(
                scope='seller', owner_id=owner, payer_user_id=q.from_user.id,
                gateway=gateway, amount=float(plan['price']), currency=currency,
                purpose='child_subscription', reference_id=plan['plan_id'],
                metadata={
                    'plan_id': plan['plan_id'],
                    'plan_name': plan['name'],
                    'description': f"{plan['name']} subscription",
                    'bot_id': int(context.application.bot_data.get('seller_bot_id') or 0),
                },
            )
            try:
                checkout = None
                if gateway == 'razorpay':
                    checkout = await _claim_precreated_razorpay_qr(tx, plan, owner, currency)
                if checkout is None:
                    checkout = await create_checkout(tx)
                if gateway == 'razorpay' and checkout.get('checkout_mode') == 'upi_qr':
                    image = None
                    image_url = str(checkout.get('qr_image_url') or checkout.get('checkout_url') or '')
                    cached_file_id = str(checkout.get('telegram_file_id') or '').strip()
                    if not cached_file_id:
                        image = _razorpay_qr_photo(checkout)
                        if image is None and not image_url:
                            raise GatewayError('Razorpay QR image was not returned')
                    close_by = int(checkout.get('qr_close_by') or 0)
                    remaining = max(1, int((close_by - time.time() + 59) // 60)) if close_by else 30
                    text = (
                        f"💳 Razorpay UPI Payment\n\n"
                        f"Plan: {plan['name']}\n"
                        f"Amount: {format_currency(currency, plan['price'])}\n"
                        f"Transaction: {tx['transaction_id']}\n\n"
                        f"📱 Scan this QR with any UPI app.\n"
                        f"⏳ QR is valid for {remaining} minutes.\n"
                        f"✅ Payment will be verified automatically.\n"
                        f"You do not need to send a payment screenshot."
                    )
                    try:
                        await q.message.delete()
                    except TelegramError:
                        pass
                    sent = await context.bot.send_photo(
                        chat_id=q.message.chat_id,
                        photo=cached_file_id if cached_file_id else (image if image is not None else image_url),
                        caption=text,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data='c_buy')]]),
                    )
                    if not cached_file_id and getattr(sent, 'photo', None):
                        try:
                            await cache_razorpay_qr_telegram_file_id(
                                str(checkout.get('qr_code_id') or ''),
                                str(sent.photo[-1].file_id),
                            )
                        except Exception:
                            pass
                    await update_gateway_transaction(
                        tx['transaction_id'],
                        payment_message_chat_id=int(sent.chat_id),
                        payment_message_id=int(sent.message_id),
                        payment_message_type='photo',
                    )
                    return True

                text = (
                    f"💳 {gateway.title()} Payment\n\n"
                    f"Plan: {plan['name']}\n"
                    f"Amount: {format_currency(currency, plan['price'])}\n"
                    f"Transaction: {tx['transaction_id']}\n\n"
                    f"Payment successful hone ke baad plan automatically activate hoga."
                )
                rows.append([InlineKeyboardButton('💳 Pay Now', url=checkout.get('checkout_url'))])
            except GatewayError as exc:
                text = f'❌ Gateway error: {exc}'
        stars_price = int(plan.get('stars_price', 0) or 0)
        if stars_enabled and stars_price > 0:
            rows.append([InlineKeyboardButton(
                f'⭐ Pay {stars_price} Stars',
                callback_data=f"c_star_{plan['plan_id']}",
            )])
            stars_line = f"⭐ Telegram Stars: {stars_price}"
            text = f"{text}\n\n{stars_line}" if text else (
                f"💳 Payment\n\nPlan: {plan['name']}\n{stars_line}"
            )
        if manual_enabled:
            context.user_data['waiting_child_screenshot'] = True
            manual_text = f"Plan: {plan['name']}\nAmount: {format_currency(currency, plan['price'])}\nDuration: {plan['duration_text']}\n\nUPI Name: {s.get('upi_name') or 'Not Set'}\nUPI ID: {s.get('upi_id') or 'Not Set'}\n\nPay the amount and send your payment screenshot here."
            text = f'{text}\n\n{manual_text}' if text else f'💳 Payment\n\n{manual_text}'
        if not enabled and currency != 'INR':
            notice = f'⚠️ Automatic checkout is currently unavailable for {currency} in this bot. Use Manual Payment or Telegram Stars.'
            text = f'{text}\n\n{notice}' if text else notice
        if not enabled and (not manual_enabled) and not (stars_enabled and stars_price > 0):
            text = '⚠️ No payment method is currently available. Please contact support.'
        rows.append([InlineKeyboardButton('⬅ Back', callback_data='c_buy')])
        kb = InlineKeyboardMarkup(rows)
        if qr_file_id and manual_enabled:
            try:
                await q.message.delete()
            except TelegramError:
                pass
            await context.bot.send_photo(q.message.chat_id, qr_file_id, caption=text, reply_markup=kb)
        else:
            await self.safe_query_message(q, text, kb)
        return True
    if action.startswith('c_star_'):
        plan_id = action.replace('c_star_', '')
        plan = await get_plan(owner, plan_id)
        cfg = await get_gateway_config('seller', owner, decrypt=True)
        stars = int((plan or {}).get('stars_price', 0) or 0)
        if not cfg.get('stars_enabled') or not plan or stars <= 0:
            await q.answer('Telegram Stars is unavailable for this plan.', show_alert=True)
            return True
        await context.bot.send_invoice(
            chat_id=q.from_user.id,
            title=f"{plan['name']} Subscription",
            description=f"{plan['duration_text']} access subscription",
            payload=f"stars:clone:{owner}:{q.from_user.id}:{plan_id}",
            provider_token='',
            currency='XTR',
            prices=[LabeledPrice(plan['name'], stars)],
        )
        return True
    if action.startswith('c_pg_'):
        try:
            _, _, gateway, plan_id = action.split('_', 3)
        except ValueError:
            await q.answer('Invalid payment option', show_alert=True)
            return True
        plan = await get_plan(owner, plan_id)
        if not plan:
            await q.answer('Plan not found', show_alert=True)
            return True
        s = await get_seller_settings(owner)
        currency = normalize_currency(s.get('currency')) or 'INR'
        if currency != 'INR':
            await self.safe_query_message(q, f'⚠️ {gateway.title()} automatic checkout is currently configured for INR only. Current bot currency is {currency}. Use Manual Payment or change the currency to INR.', back_keyboard)
            return True
        if gateway == 'razorpay':
            bot_id = int(context.application.bot_data.get('seller_bot_id') or 0)
            try:
                await cancel_previous_razorpay_qr_for_same_plan(
                    context.bot, owner, q.from_user.id, bot_id, str(plan_id)
                )
            except Exception:
                logger.exception('Could not replace previous Razorpay QR for same plan')
        tx = await create_gateway_transaction(
            scope='seller', owner_id=owner, payer_user_id=q.from_user.id, gateway=gateway,
            amount=float(plan['price']), currency=currency, purpose='child_subscription',
            reference_id=plan_id, metadata={
                'plan_id': plan_id, 'plan_name': plan['name'],
                'description': f"{plan['name']} subscription",
                'bot_id': int(context.application.bot_data.get('seller_bot_id') or 0),
            },
        )
        try:
            checkout = None
            if gateway == 'razorpay':
                checkout = await _claim_precreated_razorpay_qr(tx, plan, owner, currency)
            if checkout is None:
                checkout = await create_checkout(tx)
            if gateway == 'razorpay' and checkout.get('checkout_mode') == 'upi_qr':
                image = None
                image_url = str(checkout.get('qr_image_url') or checkout.get('checkout_url') or '')
                cached_file_id = str(checkout.get('telegram_file_id') or '').strip()
                if not cached_file_id:
                    image = _razorpay_qr_photo(checkout)
                    if image is None and not image_url:
                        raise GatewayError('Razorpay QR image was not returned')
                close_by = int(checkout.get('qr_close_by') or 0)
                remaining = max(1, int((close_by - __import__('time').time() + 59) // 60)) if close_by else 30
                text = (
                    f"💳 Razorpay UPI Payment\n\nPlan: {plan['name']}\n"
                    f"Amount: {format_currency(currency, plan['price'])}\n"
                    f"Transaction: {tx['transaction_id']}\n\n"
                    f"📱 Scan this QR with any UPI app.\n"
                    f"⏳ QR is valid for {remaining} minutes.\n"
                    f"✅ Payment will be verified automatically.\n"
                    f"You do not need to send a payment screenshot."
                )
                try:
                    await q.message.delete()
                except TelegramError:
                    pass
                sent = await context.bot.send_photo(
                    chat_id=q.message.chat_id,
                    photo=cached_file_id if cached_file_id else (image if image is not None else image_url),
                    caption=text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅ Back', callback_data='c_buy')]]),
                )
                if not cached_file_id and getattr(sent, 'photo', None):
                    try:
                        await cache_razorpay_qr_telegram_file_id(
                            str(checkout.get('qr_code_id') or ''),
                            str(sent.photo[-1].file_id),
                        )
                    except Exception:
                        pass
                await update_gateway_transaction(
                    tx['transaction_id'], payment_message_chat_id=int(sent.chat_id),
                    payment_message_id=int(sent.message_id), payment_message_type='photo',
                )
                return True
            await self.safe_query_message(q, f"💳 {gateway.title()} Secure Payment\n\nPlan: {plan['name']}\nAmount: {format_currency(currency, plan['price'])}\nTransaction: {tx['transaction_id']}\n\nPayment verify hote hi subscription automatically activate hogi.", InlineKeyboardMarkup([[InlineKeyboardButton('💳 Pay Now', url=checkout.get('checkout_url'))], [InlineKeyboardButton('⬅ Back', callback_data='c_buy')]]))
            await update_gateway_transaction(
                tx['transaction_id'], payment_message_chat_id=int(q.message.chat_id),
                payment_message_id=int(q.message.message_id), payment_message_type='text',
            )
        except GatewayError as exc:
            await self.safe_query_message(q, f'❌ Gateway error: {exc}', back_keyboard)
        return True
        await self.safe_query_message(q, f"💳 {gateway.title()} Secure Payment\n\nPlan: {plan['name']}\nAmount: {format_currency(currency, plan['price'])}\nTransaction: {tx['transaction_id']}\n\nPayment verify hote hi subscription automatically activate hogi.", InlineKeyboardMarkup([[InlineKeyboardButton('💳 Pay Now', url=checkout.get('checkout_url'))], [InlineKeyboardButton('⬅ Back', callback_data='c_buy')]]))
        return True
    if action == 'c_upload':
        context.user_data['waiting_child_screenshot'] = True
        await q.message.reply_text('📷 Upload your payment screenshot.', reply_markup=back_keyboard)
        return True
    return False
