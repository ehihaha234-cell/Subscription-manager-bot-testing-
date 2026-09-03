"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneMediaHandlersMixin:
    async def welcome_media_handler(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)
        if update.effective_user.id!=self.seller_account(context):
            return
        if context.user_data.get("wait_support_ar_media"):
            keyword=context.user_data["wait_support_ar_media"]
            msg=update.effective_message; media_type=""; file_id=""
            if msg.photo: media_type="photo"; file_id=msg.photo[-1].file_id
            elif msg.video: media_type="video"; file_id=msg.video.file_id
            elif msg.animation: media_type="animation"; file_id=msg.animation.file_id
            elif msg.document: media_type="document"; file_id=msg.document.file_id
            if not file_id: await msg.reply_text("❌ Send a photo, video, GIF or document."); return
            await save_support_auto_reply(owner,keyword,media_type=media_type,media_file_id=file_id)
            context.user_data.clear(); await msg.reply_text("✅ Media saved",reply_markup=self.support_auto_reply_edit_menu(keyword, await get_support_auto_reply(owner, keyword)))
            raise ApplicationHandlerStop
        if context.user_data.get("wait_support_tpl_media"):
            command=context.user_data["wait_support_tpl_media"]
            msg=update.effective_message; media_type=""; file_id=""
            if msg.photo: media_type="photo"; file_id=msg.photo[-1].file_id
            elif msg.video: media_type="video"; file_id=msg.video.file_id
            elif msg.animation: media_type="animation"; file_id=msg.animation.file_id
            elif msg.document: media_type="document"; file_id=msg.document.file_id
            if not file_id: await msg.reply_text("❌ Photo, video, GIF ya document bhejo."); return
            await save_support_template(owner,command,media_type=media_type,media_file_id=file_id)
            context.user_data.clear(); await msg.reply_text("✅ Template media saved",reply_markup=self.support_template_edit_menu(command, await get_support_template(owner, command)))
            raise ApplicationHandlerStop
        if not context.user_data.get("wait_welcome_media"): return
        msg=update.effective_message; media_type=""; file_id=""
        if msg.photo: media_type="photo"; file_id=msg.photo[-1].file_id
        elif msg.video: media_type="video"; file_id=msg.video.file_id
        elif msg.animation: media_type="animation"; file_id=msg.animation.file_id
        elif msg.document: media_type="document"; file_id=msg.document.file_id
        if not file_id: await msg.reply_text("❌ Send photo, video, GIF or document."); return
        await set_seller_setting(owner,"welcome_media_type",media_type)
        await set_seller_setting(owner,"welcome_media_file_id",file_id)
        context.user_data.clear(); await msg.reply_text("✅ Welcome media saved. Use 👀 Full Preview to check it.",reply_markup=self.welcome_media_menu(True))
        raise ApplicationHandlerStop

    async def seller_qr_upload_handler(self, update:Update, context:ContextTypes.DEFAULT_TYPE):
        """Handle seller Manual Payment QR uploads in a dedicated high-priority handler.

        This state is intentionally isolated from the normal media/business/live-support
        handlers so a QR upload cannot be consumed by another editor or router first.
        Both Telegram photo messages and image documents are accepted.
        """
        if not context.user_data.get("wait_qr"):
            return

        owner = self.owner(context)
        user = update.effective_user
        if not user:
            return

        # Payment settings belong to the clone owner/seller. Keep the original
        # owner-only behavior rather than allowing ordinary clone users to write it.
        if int(user.id) != int(self.seller_account(context)):
            return

        msg = update.effective_message
        file_id = ""
        if msg and msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg and msg.document and str(msg.document.mime_type or "").lower().startswith("image/"):
            file_id = msg.document.file_id

        if not file_id:
            if msg:
                await msg.reply_text("❌ Send the QR code as a photo or image file.")
            return

        try:
            bot_id = int(context.application.bot_data.get("seller_bot_id") or 0)
            if not bot_id:
                raise RuntimeError("Clone bot id is missing")

            # QR codes are clone-bot specific. This prevents a newly created
            # clone from accidentally reading/writing another clone's QR.
            saved = await set_bot_payment_qr(bot_id, file_id)
            if not saved:
                raise RuntimeError(f"Clone bot record not found: {bot_id}")

            # Only the original legacy-scope clone may mirror its QR into the
            # old seller_settings location. Newer clones must never overwrite
            # that shared legacy value.
            clone_record = await get_bot_by_bot_id(bot_id)
            if clone_record and int(clone_record.get("data_owner_id") or 0) == int(owner):
                await set_seller_setting(owner, "upi_qr_file_id", file_id)

            # Verify the exact clone record before confirming success.
            if await get_bot_payment_qr(bot_id) != file_id:
                raise RuntimeError(f"QR verification failed for bot_id={bot_id}")

            context.user_data.pop("wait_qr", None)
            gateway_cfg = await get_gateway_config("seller", owner, decrypt=True)
            await msg.reply_text(
                "✅ QR code saved successfully.",
                reply_markup=self.manual_payment_menu(bool(gateway_cfg.get("manual_enabled", True))),
            )
            logger.info("Seller payment QR saved bot_id=%s owner_id=%s", bot_id, owner)
        except Exception:
            logger.exception("Failed to save seller payment QR owner_id=%s", owner)
            await msg.reply_text("❌ QR code could not be saved. Please try again.")
        raise ApplicationHandlerStop

    async def photo_handler(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)

        if context.user_data.get("waiting_child_screenshot"):
            plan=context.user_data.get("selected_child_plan")
            if not plan: await update.effective_message.reply_text("Select a plan first"); return
            photo=update.effective_message.photo[-1]
            unique=getattr(photo,"file_unique_id","")
            if not await reserve_payment_fingerprint("child",owner,unique,update.effective_user.id):
                context.user_data.clear(); await update.effective_message.reply_text("⚠️ This payment screenshot was already submitted. Send a new genuine payment proof."); return
            p=await create_payment(owner,update.effective_user.id,plan,photo.file_id); context.user_data.clear()
            await audit("child_payment_submitted",update.effective_user.id,owner,{"payment_id":p.get("payment_id")})
            kb=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Approve",
                        callback_data=f"a_pay_ok_{p['payment_id']}",
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"a_pay_no_{p['payment_id']}",
                    ),
                ]
            ])

            caption=await self.payment_details_caption(
                owner,
                p,
                status="pending",
            )

            # `owner` is the clone-specific data scope. For a seller with multiple
            # clone bots it can be the clone bot ID, which is NOT the seller's
            # Telegram chat ID. Always deliver the approval notification to the
            # actual seller account while keeping the payment itself stored under
            # the clone-specific owner/data scope.
            seller_account_id = self.seller_account(context)
            try:
                await context.bot.send_photo(
                    seller_account_id,
                    p["screenshot_file_id"],
                    caption=caption,
                    reply_markup=kb,
                )
            except TelegramError:
                # The payment is already safely stored as pending. Do not leave
                # the user without confirmation if the live notification fails.
                logger.exception(
                    "Manual payment saved but seller notification failed: "
                    "seller_account_id=%s data_owner_id=%s payment_id=%s",
                    seller_account_id, owner, p.get("payment_id"),
                )
                await update.effective_message.reply_text(
                    "✅ Payment screenshot submitted successfully. It is pending "
                    "approval and the admin can review it from Pending Payments."
                )
                raise ApplicationHandlerStop

            await update.effective_message.reply_text(
                "✅ Payment screenshot submitted successfully. Waiting for approval."
            )
            raise ApplicationHandlerStop

    async def forward_handler(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        owner=self.owner(context)
        if update.effective_user.id!=self.seller_account(context) or not context.user_data.get("wait_channel"): return
        m=update.effective_message; chat=getattr(m,"forward_from_chat",None)
        if chat is None:
            origin=getattr(m,"forward_origin",None); chat=getattr(origin,"chat",None)
        if chat is None:
            await m.reply_text(
                "❌ Forward se group detect nahi hua.\n\n"
                "Easy method: child bot ko group me Admin banao, phir group ke andar /connectgroup bhejo."
            )
            return
        await add_channel(owner,chat.id,chat.title or "Unknown",getattr(chat,"type","unknown")); context.user_data.clear(); await m.reply_text("✅ Channel/group added",reply_markup=self.channels_menu())

