"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class _StartFeatureQuery:
    """Small callback-query adapter used by Clone Bot deep-link starts."""

    def __init__(self, message, user, data: str):
        self.message = message
        self.from_user = user
        self.data = data

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        return await self.message.reply_text(text, reply_markup=reply_markup, **kwargs)

    async def edit_message_caption(self, caption=None, reply_markup=None, **kwargs):
        return await self.message.reply_text(caption or "Choose an option below.", reply_markup=reply_markup)


class CloneStartMixin:
    @staticmethod
    def _consume_background_task(task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background /start task failed")

    def _start_background(self, coro):
        task=asyncio.create_task(coro)
        task.add_done_callback(self._consume_background_task)
        return task

    async def _post_start_tasks(self,context,owner,user,referrer_id=None):
        """Run non-critical work after /start has completed."""
        if referrer_id and referrer_id != user.id:
            try:
                await register_referral(owner,referrer_id,user.id)
            except Exception:
                logger.exception("Referral registration failed owner=%s user=%s",owner,user.id)

    async def _open_start_feature(self, update, context, owner: int, payload: str) -> bool:
        """Open a requested Clone Bot feature directly from a t.me start payload."""
        actions = {
            "plans": "c_plans",
            "buy": "c_buy",
            "renew": "c_renew",
            "profile": "c_profile",
            "referral": "c_referral",
            "referral_unlock": "c_referral_unlock",
            "support": "c_support",
        }
        action = actions.get(str(payload or "").strip().lower())
        if not action:
            return False

        from handlers.clone.user import navigation, profile, referral, support

        query = _StartFeatureQuery(update.effective_message, update.effective_user, action)
        for handler in (navigation, profile, referral, support):
            if await handler.handle(self, update, context, query, owner, action):
                return True
        return False

    async def child_start(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)

        # Detect seller locally. For non-sellers, staff lookup runs in parallel
        # with the user/settings reads so it never adds a sequential DB round-trip.
        staff = None
        if int(update.effective_user.id) == int(self.seller_account(context)):
            staff = {"role": "seller", "status": "active", "permissions": ["*"]}
            staff_task = None
        else:
            staff_task = asyncio.create_task(self.staff_record(update, context))

        try:
            # Only data needed to decide what the user sees is on the /start
            # critical path. Live Support settings are not used by send_welcome.
            user_task=asyncio.create_task(upsert_user(owner,update.effective_user))
            settings_task=asyncio.create_task(get_seller_settings(owner))
            tasks=[user_task, settings_task]
            if staff_task is not None:
                tasks.append(staff_task)
            results=await asyncio.gather(*tasks)
            user_record,settings=results[:2]
            if staff_task is not None:
                staff=results[2]

            if staff:
                context.user_data.clear()
                target = context.args[0] if context.args else "admin_panel"
                if target == "admin_payment":
                    qr_file_id = await get_bot_payment_qr(int(context.application.bot_data.get("seller_bot_id") or 0))
                    if not qr_file_id:
                        qr_file_id = str(settings.get("upi_qr_file_id") or "")
                    await update.effective_message.reply_text(
                        f"💳 Payment Settings\n\nUPI Name: {settings.get('upi_name') or 'Not Set'}\n"
                        f"UPI ID: {settings.get('upi_id') or 'Not Set'}\n"
                        f"QR: {'Added' if qr_file_id else 'Not Added'}",
                        reply_markup=self.payment_menu(),
                    )
                elif target == "admin_settings":
                    await update.effective_message.reply_text(
                        "⚙️ Bot Settings\n\n"
                        f"Bot Name: {settings.get('bot_name') or '-'}\n"
                        f"Support: {settings.get('support_username') or '-'}\n"
                        f"Currency: {currency_symbol(settings.get('currency'))} {normalize_currency(settings.get('currency')) or 'INR'} — {currency_name(settings.get('currency'))}\n"
                        f"Timezone: {settings.get('timezone') or 'Asia/Kolkata'}",
                        reply_markup=self.settings_menu(),
                    )
                elif target == "admin_channels":
                    await update.effective_message.reply_text("📢 Channels / Groups", reply_markup=self.channels_menu())
                elif target == "admin_stats":
                    data = await stats(owner)
                    await update.effective_message.reply_text(
                        "📊 Statistics\n\n"
                        f"Users: {data.get('users',0)}\nPlans: {data.get('plans',0)}\n"
                        f"Channels/Groups: {data.get('channels',0)}\n"
                        f"Pending Payments: {data.get('pending',0)}\nRevenue: {format_currency(settings.get('currency'), data.get('revenue',0))}",
                        reply_markup=self.admin_menu(staff.get('role', 'seller')),
                    )
                elif target == "admin_terms":
                    policy = await get_policy(owner)
                    parts=[]
                    for key in ("terms","privacy","refund","support"):
                        value=(policy or {}).get(key)
                        if value: parts.append(f"{key.title()}:\n{value}")
                    await update.effective_message.reply_text(
                        "📜 Terms & Policy\n\n" + ("\n\n".join(parts) if parts else "No policy configured."),
                        reply_markup=self.admin_menu(staff.get('role', 'seller')),
                    )
                else:
                    await update.effective_message.reply_text(
                        await self.admin_panel_text(owner, update.effective_user),
                        reply_markup=self.admin_menu(staff.get('role', 'seller')),
                        parse_mode="HTML",
                    )
                return

            if user_record and user_record.get("banned"):
                await update.effective_message.reply_text(
                    "🚫 You are banned from using this bot.\n"
                    f"Reason: {user_record.get('ban_reason') or 'Not specified'}"
                )
                return

            # Defaults are normally created when the clone bot is connected.
            if not settings:
                record=await get_bot_by_data_owner_id(owner)
                settings=await ensure_seller_defaults(
                    owner,
                    (record or {}).get("bot_name","Subscription Bot"),
                )

            # Clone Bot deep links such as ?start=plans open the requested page
            # directly instead of showing the normal welcome message first.
            start_payload = str(context.args[0] if context.args else "").strip().lower()
            if start_payload and await self._open_start_feature(update, context, owner, start_payload):
                return

            # User-visible response stays exactly the same.
            await self.send_welcome(
                update.effective_message,
                context,
                settings,
                update.effective_user,
            )

            referrer_id=None
            if context.args:
                arg=context.args[0]
                if arg.startswith("ref_"):
                    try:
                        referrer_id=int(arg.replace("ref_","",1))
                    except (TypeError,ValueError):
                        referrer_id=None

            self._start_background(
                self._post_start_tasks(
                    context, owner, update.effective_user, referrer_id,
                )
            )
        except Forbidden as exc:
            logger.info("Clone /start blocked for user=%s: %s", update.effective_user.id, exc)
        except Exception:
            logger.exception("Clone /start failed owner=%s user=%s", owner, update.effective_user.id)
            try:
                await update.effective_message.reply_text("⚠️ Temporary problem. Please try again.")
            except Exception:
                pass
