"""Clone-bot seller broadcast editor and delivery engine."""

from handlers.common.clone_context import *
from datetime import timedelta
from handlers.common.editor_engine import build_editor_keyboard, parse_editor_buttons
from handlers.common.feature_navigation import register_feature_origin
from database.broadcast import get_seller_broadcast_draft, update_seller_broadcast_draft
from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.error import RetryAfter


class CloneBroadcastMixin:
    @staticmethod
    def _broadcast_variables(text, user):
        now = datetime.now(timezone.utc)
        username = str(user.get("username") or "")
        mention = f"@{username}" if username else str(user.get("name") or user.get("first_name") or "User")
        values = {
            "{NAME}": str(user.get("name") or user.get("first_name") or "User"),
            "{ID}": str(user.get("user_id") or ""),
            "{USERNAME}": f"@{username}" if username else "Not Set",
            "{MENTION}": mention,
            "{DATE}": now.strftime("%d %b %Y"),
            "{TIME}": now.strftime("%I:%M %p UTC"),
        }
        result = str(text or "")
        for key, value in values.items():
            result = result.replace(key, value)
        return result

    @staticmethod
    def _input_media(item, caption=None):
        media_type = str(item.get("type") or item.get("media_type") or "")
        file_id = str(item.get("file_id") or item.get("media_file_id") or "")
        if media_type == "photo":
            return InputMediaPhoto(file_id, caption=caption)
        if media_type == "video":
            return InputMediaVideo(file_id, caption=caption)
        if media_type == "document":
            return InputMediaDocument(file_id, caption=caption)
        return None

    async def _send_seller_broadcast_item(self, bot, chat_id, item, user):
        text = self._broadcast_variables(item.get("text"), user)
        buttons = build_editor_keyboard(item.get("buttons"))
        media = list(item.get("media") or [])
        if not media and item.get("media_file_id"):
            media = [{"type": item.get("media_type"), "file_id": item.get("media_file_id")}]

        if not media:
            sent = await bot.send_message(chat_id, text or "Broadcast", reply_markup=buttons)
            register_feature_origin(sent, text=text or "Broadcast", markup=buttons)
            return

        if len(media) == 1:
            media_type = str(media[0].get("type") or "")
            file_id = str(media[0].get("file_id") or "")
            kwargs = {"chat_id": chat_id, "caption": text or None, "reply_markup": buttons}
            if media_type == "photo":
                sent = await bot.send_photo(photo=file_id, **kwargs)
            elif media_type == "video":
                sent = await bot.send_video(video=file_id, **kwargs)
            elif media_type == "document":
                sent = await bot.send_document(document=file_id, **kwargs)
            elif media_type == "animation":
                sent = await bot.send_animation(animation=file_id, **kwargs)
            else:
                raise ValueError("Unsupported broadcast media")
            register_feature_origin(sent, text=text, markup=buttons)
            return

        album = []
        for index, media_item in enumerate(media[:10]):
            built = self._input_media(media_item, text if index == 0 and text else None)
            if built:
                album.append(built)
        if not album:
            raise ValueError("No valid album media")
        await bot.send_media_group(chat_id=chat_id, media=album)
        if buttons:
            sent = await bot.send_message(chat_id, "Choose an option:", reply_markup=buttons)
            register_feature_origin(sent, text="Choose an option:", markup=buttons)

    async def send_seller_broadcast_preview(self, message, item):
        owner = int(message.chat_id)
        user = {"user_id": owner, "name": "Preview User", "username": "preview_user"}
        await self._send_seller_broadcast_item(message.get_bot(), owner, item, user)

    async def send_seller_broadcast(self, owner, context, item, progress_callback=None):
        from database.seller_data import c, USERS

        users = []
        cursor = c(USERS).find(
            {"owner_id": int(owner)},
            {"user_id": 1, "name": 1, "first_name": 1, "username": 1},
        )
        async for user in cursor:
            user_id = user.get("user_id")
            if not user_id or int(user_id) == int(owner):
                continue
            users.append(user)

        total = len(users)
        success = failed = 0
        if progress_callback:
            await progress_callback(total=total, success=0, failed=0, processed=0)

        for index, user in enumerate(users, start=1):
            user_id = int(user.get("user_id"))
            try:
                while True:
                    try:
                        await self._send_seller_broadcast_item(context.bot, user_id, item, user)
                        success += 1
                        break
                    except RetryAfter as exc:
                        wait_for = max(1, int(getattr(exc, "retry_after", 1) or 1))
                        logger.warning("Seller broadcast flood wait owner=%s user=%s retry_after=%s", owner, user_id, wait_for)
                        await asyncio.sleep(wait_for + 1)
            except Exception as exc:
                failed += 1
                logger.warning("Seller broadcast failed owner=%s user=%s error=%s", owner, user_id, exc)

            if progress_callback:
                await progress_callback(total=total, success=success, failed=failed, processed=index)
            await asyncio.sleep(0.08)

        return {"total": total, "success": success, "failed": failed}

    @staticmethod
    def _seller_broadcast_progress_text(total, success, failed, processed, completed=False):
        remaining = max(0, int(total) - int(processed))
        percent = int((processed / total) * 100) if total else 100
        title = "✅ Broadcast Completed" if completed else "📢 Broadcast Processing..."
        status = "✅ Completed" if completed else "🟢 Sending"
        return (
            f"{title}\n\n"
            f"Status: {status}\n\n"
            f"👥 Total Users: {total}\n"
            f"✅ Delivered: {success}\n"
            f"❌ Failed: {failed}\n"
            f"⏳ Remaining: {remaining}\n\n"
            f"📈 Progress: {processed} / {total} ({percent}%)"
        )

    async def run_seller_broadcast_background(self, owner, context, item, progress_message):
        async def update_progress(*, total, success, failed, processed):
            if processed not in (0, total) and processed % 2:
                return
            try:
                await progress_message.edit_text(self._seller_broadcast_progress_text(total, success, failed, processed, completed=False))
            except Exception as exc:
                if "Message is not modified" not in str(exc):
                    logger.warning("Seller broadcast progress edit failed owner=%s error=%s", owner, exc)
        try:
            result = await self.send_seller_broadcast(owner, context, item, progress_callback=update_progress)
            try:
                await progress_message.edit_text(self._seller_broadcast_progress_text(result["total"], result["success"], result["failed"], result["total"], completed=True))
            except Exception:
                logger.exception("Seller broadcast final progress edit failed owner=%s", owner)
        finally:
            context.application.bot_data.pop(f"seller_broadcast_running:{int(owner)}", None)

    async def seller_broadcast_confirm_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner = self.owner(context)
        if int(update.effective_user.id) != int(self.seller_account(context)):
            return
        pending = context.user_data.get("seller_broadcast_confirmation") or {}
        if int(pending.get("owner_id") or 0) != int(owner):
            await update.effective_message.reply_text("❌ No broadcast is waiting for confirmation.")
            return
        running_key = f"seller_broadcast_running:{int(owner)}"
        if context.application.bot_data.get(running_key):
            await update.effective_message.reply_text("⚠️ A broadcast is already running.")
            return
        item = pending.get("draft") or {}
        if not (item.get("text") or item.get("media") or item.get("media_file_id")):
            context.user_data.pop("seller_broadcast_confirmation", None)
            await update.effective_message.reply_text("❌ Broadcast content is empty.")
            return
        context.user_data.pop("seller_broadcast_confirmation", None)
        context.application.bot_data[running_key] = True
        progress_message = await update.effective_message.reply_text(self._seller_broadcast_progress_text(0, 0, 0, 0, completed=False))
        context.application.create_task(
            self.run_seller_broadcast_background(owner, context, item, progress_message),
            name=f"seller_broadcast_{owner}",
        )

    async def seller_broadcast_cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner = self.owner(context)
        if int(update.effective_user.id) != int(self.seller_account(context)):
            return
        pending = context.user_data.pop("seller_broadcast_confirmation", None)
        if pending:
            await update.effective_message.reply_text("❌ Broadcast cancelled.")
        else:
            await update.effective_message.reply_text("ℹ️ No broadcast is waiting for confirmation.")

    @staticmethod
    def _campaign_interval_seconds(value: str) -> int | None:
        import re
        match = re.fullmatch(r"([1-9]\d*)([smhdSMHD])", str(value or "").strip())
        if not match:
            return None
        return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]

    async def _campaign_job_remove(self, application: Application, job_id: str):
        if not application.job_queue:
            return
        for job in application.job_queue.get_jobs_by_name(f"campaign_{job_id}"):
            job.schedule_removal()

    async def _campaign_next_run_from_item(self, owner: int, item: dict, *, prefer_now_for_repeat: bool = False):
        now = datetime.now(timezone.utc)
        interval = self._campaign_interval_seconds(item.get("repeat_interval"))
        if prefer_now_for_repeat and interval:
            return now + timedelta(seconds=interval)
        next_run = item.get("next_run_at")
        if next_run:
            return next_run if next_run.tzinfo else next_run.replace(tzinfo=timezone.utc)
        schedule_at = str(item.get("schedule_at") or "").strip()
        if schedule_at:
            try:
                parsed = datetime.strptime(schedule_at, "%d %b %Y • %I:%M %p")
                settings = await get_seller_settings(int(owner))
                zone = ZoneInfo(settings.get("timezone", "Asia/Kolkata"))
                return parsed.replace(tzinfo=zone).astimezone(timezone.utc)
            except Exception:
                logger.exception("Could not parse campaign schedule owner=%s job=%s", owner, item.get("job_id"))
        return now + timedelta(seconds=interval) if interval else None

    async def _campaign_schedule_job(self, application: Application, item: dict, when=None):
        if not application.job_queue or item.get("status") != "active":
            return
        job_id = str(item["job_id"])
        await self._campaign_job_remove(application, job_id)
        run_at = when or item.get("next_run_at")
        if not run_at:
            return
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        application.job_queue.run_once(
            self.scheduled_campaign_job,
            when=max(run_at, datetime.now(timezone.utc) + timedelta(seconds=1)),
            data={"owner_id": int(item["owner_id"]), "job_id": job_id},
            name=f"campaign_{job_id}",
        )

    async def schedule_campaign_from_item(self, application: Application, owner: int, item: dict, *, prefer_now_for_repeat: bool = False):
        if not item or item.get("status") != "active":
            return item
        next_run = await self._campaign_next_run_from_item(owner, item, prefer_now_for_repeat=prefer_now_for_repeat)
        if not next_run:
            return item
        updated = await update_scheduled_campaign(owner, item["job_id"], next_run_at=next_run)
        if updated:
            await self._campaign_schedule_job(application, updated)
            return updated
        return item

    async def scheduled_campaign_job(self, context: ContextTypes.DEFAULT_TYPE):
        data = context.job.data or {}
        owner = int(data.get("owner_id") or 0)
        job_id = str(data.get("job_id") or "")
        item = await get_scheduled_campaign(owner, job_id)
        if not item or item.get("status") != "active":
            return
        try:
            await self.send_seller_broadcast(owner, context, item)
            interval = self._campaign_interval_seconds(item.get("repeat_interval"))
            if interval:
                next_run = datetime.now(timezone.utc) + timedelta(seconds=interval)
                updated = await update_scheduled_campaign(
                    owner, job_id, next_run_at=next_run, last_run_at=datetime.now(timezone.utc), status="active"
                )
                if updated:
                    await self._campaign_schedule_job(context.application, updated)
            else:
                await update_scheduled_campaign(
                    owner, job_id, next_run_at=None, last_run_at=datetime.now(timezone.utc), status="paused"
                )
                await self._campaign_job_remove(context.application, job_id)
        except Exception:
            logger.exception("Scheduled campaign execution failed owner=%s job=%s", owner, job_id)
            interval = self._campaign_interval_seconds(item.get("repeat_interval"))
            if interval:
                updated = await update_scheduled_campaign(
                    owner, job_id, next_run_at=datetime.now(timezone.utc) + timedelta(seconds=interval)
                )
                if updated:
                    await self._campaign_schedule_job(context.application, updated)
            else:
                await update_scheduled_campaign(owner, job_id, status="paused")

    async def restore_scheduled_campaigns(self, application: Application, owner_id: int):
        items = await list_scheduled_campaigns(owner_id)
        now = datetime.now(timezone.utc)
        for item in items:
            if item.get("status") != "active":
                continue
            next_run = await self._campaign_next_run_from_item(owner_id, item)
            if not next_run:
                continue
            item = await update_scheduled_campaign(owner_id, item["job_id"], next_run_at=next_run) or item
            await self._campaign_schedule_job(application, item, when=max(next_run, now + timedelta(seconds=1)))

    async def broadcast_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner = self.owner(context)
        if update.effective_user.id != self.seller_account(context):
            return

        scheduled_editor = context.user_data.get("scheduled_broadcast_editor") or {}
        scheduled_field = str(scheduled_editor.get("field") or "")
        if scheduled_field:
            from handlers.clone.admin.broadcast_coupons import (
                _scheduled_editor_text, _scheduled_editor_keyboard,
                _scheduled_settings_text, _scheduled_settings_keyboard,
            )

            async def edit_scheduled_menu(text, markup):
                chat_id = scheduled_editor.get("menu_chat_id")
                message_id = scheduled_editor.get("menu_message_id")
                if chat_id and message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=int(chat_id), message_id=int(message_id),
                            text=text, reply_markup=markup,
                        )
                        try:
                            await update.effective_message.delete()
                        except Exception:
                            pass
                        return
                    except Exception:
                        logger.exception("Scheduled broadcast menu edit failed owner=%s", owner)
                await update.effective_message.reply_text(text, reply_markup=markup)

            raw = (update.effective_message.text or update.effective_message.caption or "").strip()
            if scheduled_field == "name":
                if not raw:
                    await update.effective_message.reply_text("❌ Send a broadcast name.")
                    raise ApplicationHandlerStop
                item = await create_scheduled_campaign(owner, raw)
                context.user_data.pop("scheduled_broadcast_editor", None)
                await edit_scheduled_menu(_scheduled_editor_text(item), _scheduled_editor_keyboard(item))
                raise ApplicationHandlerStop

            job_id = str(scheduled_editor.get("job_id") or "")
            item = await get_scheduled_campaign(owner, job_id)
            if not item:
                context.user_data.pop("scheduled_broadcast_editor", None)
                await update.effective_message.reply_text("❌ Scheduled broadcast not found.")
                raise ApplicationHandlerStop

            if scheduled_field == "text":
                if not raw:
                    await update.effective_message.reply_text("❌ Send text.")
                    raise ApplicationHandlerStop
                item = await update_scheduled_campaign(owner, job_id, text=raw)
                context.user_data.pop("scheduled_broadcast_editor", None)
                await edit_scheduled_menu(_scheduled_editor_text(item), _scheduled_editor_keyboard(item))
                raise ApplicationHandlerStop

            if scheduled_field == "buttons":
                try:
                    buttons = parse_editor_buttons(raw)
                except ValueError as exc:
                    await update.effective_message.reply_text(f"❌ {exc}")
                    raise ApplicationHandlerStop
                item = await update_scheduled_campaign(owner, job_id, buttons=buttons)
                context.user_data.pop("scheduled_broadcast_editor", None)
                await edit_scheduled_menu(_scheduled_editor_text(item), _scheduled_editor_keyboard(item))
                raise ApplicationHandlerStop

            if scheduled_field == "schedule":
                try:
                    parsed = datetime.strptime(raw, "%d %b %Y %I:%M %p")
                    if parsed <= datetime.now():
                        raise ValueError("past")
                    display = parsed.strftime("%d %b %Y • %I:%M %p")
                except Exception:
                    await update.effective_message.reply_text(
                        "❌ Use: 02 Sep 2026 06:50 PM\nThe date/time must be in the future."
                    )
                    raise ApplicationHandlerStop
                zone = ZoneInfo((await get_seller_settings(owner)).get("timezone", "Asia/Kolkata"))
                run_at = parsed.replace(tzinfo=zone).astimezone(timezone.utc)
                item = await update_scheduled_campaign(owner, job_id, schedule_at=display, next_run_at=run_at, status="active")
                item = await self.schedule_campaign_from_item(context.application, owner, item)
                context.user_data.pop("scheduled_broadcast_editor", None)
                await edit_scheduled_menu(_scheduled_settings_text(item), _scheduled_settings_keyboard(item))
                raise ApplicationHandlerStop

            if scheduled_field == "repeat":
                import re
                match = re.fullmatch(r"([1-9]\d*)([smhdSMHD])", raw)
                if not match:
                    await update.effective_message.reply_text("❌ Use formats like 6s, 7m, 8h or 9d.")
                    raise ApplicationHandlerStop
                interval = f"{match.group(1)}{match.group(2).lower()}"
                item = await update_scheduled_campaign(owner, job_id, repeat_interval=interval, status="active")
                item = await self.schedule_campaign_from_item(context.application, owner, item, prefer_now_for_repeat=not bool(item.get("schedule_at")))
                context.user_data.pop("scheduled_broadcast_editor", None)
                await edit_scheduled_menu(_scheduled_settings_text(item), _scheduled_settings_keyboard(item))
                raise ApplicationHandlerStop

            if scheduled_field == "media":
                msg = update.effective_message
                media_type = ""
                file_id = ""
                if msg.photo:
                    media_type, file_id = "photo", msg.photo[-1].file_id
                elif msg.video:
                    media_type, file_id = "video", msg.video.file_id
                elif msg.document:
                    media_type, file_id = "document", msg.document.file_id
                elif msg.animation:
                    media_type, file_id = "animation", msg.animation.file_id
                if not file_id:
                    await msg.reply_text("❌ Send a photo, video, GIF or document.")
                    raise ApplicationHandlerStop

                async def save_scheduled_media(items):
                    ordered = sorted(items[:10], key=lambda x: int(x.get("message_id") or 0))
                    clean = [{"type": x["type"], "file_id": x["file_id"]} for x in ordered]
                    first = clean[0]
                    saved = await update_scheduled_campaign(
                        owner, job_id, media=clean, media_type=first["type"], media_file_id=first["file_id"]
                    )
                    context.user_data.pop("scheduled_broadcast_editor", None)
                    context.user_data.pop("scheduled_media_batch", None)
                    await edit_scheduled_menu(_scheduled_editor_text(saved), _scheduled_editor_keyboard(saved))

                entry = {"type": media_type, "file_id": file_id, "message_id": msg.message_id}
                group_id = str(msg.media_group_id or "")
                if not group_id:
                    await save_scheduled_media([entry])
                    raise ApplicationHandlerStop
                batch = context.user_data.get("scheduled_media_batch")
                if not batch or batch.get("group_id") != group_id:
                    batch = {"group_id": group_id, "items": [], "generation": 0}
                    context.user_data["scheduled_media_batch"] = batch
                if len(batch["items"]) < 10:
                    batch["items"].append(entry)
                batch["generation"] += 1
                generation = batch["generation"]

                async def finalize_scheduled_album():
                    await asyncio.sleep(1.2)
                    current = context.user_data.get("scheduled_media_batch") or {}
                    if current.get("group_id") != group_id or current.get("generation") != generation:
                        return
                    await save_scheduled_media(list(current.get("items") or []))

                context.application.create_task(finalize_scheduled_album())
                raise ApplicationHandlerStop

        editor = context.user_data.get("seller_broadcast_editor") or {}
        field = str(editor.get("field") or "")
        if field in {"text", "buttons"}:
            raw = (update.effective_message.text or update.effective_message.caption or "").strip()
            if not raw:
                await update.effective_message.reply_text("❌ Send text.")
                raise ApplicationHandlerStop
            try:
                if field == "text":
                    item = await update_seller_broadcast_draft(owner, text=raw)
                else:
                    item = await update_seller_broadcast_draft(owner, buttons=parse_editor_buttons(raw))
            except ValueError as exc:
                await update.effective_message.reply_text(f"❌ {exc}")
                raise ApplicationHandlerStop
            context.user_data.pop("seller_broadcast_editor", None)
            from handlers.clone.admin.broadcast_coupons import _broadcast_text, _broadcast_keyboard
            await update.effective_message.reply_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))
            raise ApplicationHandlerStop

        if field == "media":
            msg = update.effective_message
            media_type = ""
            file_id = ""
            if msg.photo:
                media_type, file_id = "photo", msg.photo[-1].file_id
            elif msg.video:
                media_type, file_id = "video", msg.video.file_id
            elif msg.document:
                media_type, file_id = "document", msg.document.file_id
            elif msg.animation:
                media_type, file_id = "animation", msg.animation.file_id
            if not file_id:
                await msg.reply_text("❌ Send a photo, video, GIF or document.")
                raise ApplicationHandlerStop

            async def save_items(items):
                ordered = sorted(items[:10], key=lambda x: int(x.get("message_id") or 0))
                clean = [{"type": x["type"], "file_id": x["file_id"]} for x in ordered]
                first = clean[0]
                item = await update_seller_broadcast_draft(
                    owner,
                    media=clean,
                    media_type=first["type"],
                    media_file_id=first["file_id"],
                )
                context.user_data.pop("seller_broadcast_editor", None)
                context.user_data.pop("seller_broadcast_media_batch", None)
                from handlers.clone.admin.broadcast_coupons import _broadcast_text, _broadcast_keyboard
                await msg.reply_text(_broadcast_text(item), reply_markup=_broadcast_keyboard(item))

            entry = {"type": media_type, "file_id": file_id, "message_id": msg.message_id}
            group_id = str(msg.media_group_id or "")
            if not group_id:
                await save_items([entry])
                raise ApplicationHandlerStop

            batch = context.user_data.get("seller_broadcast_media_batch")
            if not batch or batch.get("group_id") != group_id:
                batch = {"group_id": group_id, "items": [], "generation": 0}
                context.user_data["seller_broadcast_media_batch"] = batch
            if len(batch["items"]) < 10:
                batch["items"].append(entry)
            batch["generation"] += 1
            generation = batch["generation"]

            async def finalize_album():
                await asyncio.sleep(1.2)
                current = context.user_data.get("seller_broadcast_media_batch") or {}
                if current.get("group_id") != group_id or current.get("generation") != generation:
                    return
                await save_items(list(current.get("items") or []))

            context.application.create_task(finalize_album())
            raise ApplicationHandlerStop

        if context.user_data.get("wait_scheduled_broadcast"):
            raw = (update.effective_message.text or update.effective_message.caption or "").strip()
            lines = raw.splitlines()
            try:
                run_local = datetime.strptime(lines[0].strip(), "%Y-%m-%d %H:%M")
                settings = await get_seller_settings(owner)
                zone = ZoneInfo(settings.get("timezone", "Asia/Kolkata"))
                run_at = run_local.replace(tzinfo=zone).astimezone(timezone.utc)
                if run_at <= datetime.now(timezone.utc):
                    raise ValueError("past")
            except Exception:
                await update.effective_message.reply_text("❌ First line must be a future time: YYYY-MM-DD HH:MM")
                return
            job = await save_scheduled_broadcast(owner, run_at, update.effective_chat.id, update.effective_message.message_id)
            context.application.job_queue.run_once(self.scheduled_broadcast_job, when=run_at, data=job, name=f"scheduled_{job['job_id']}")
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Broadcast scheduled for {run_local:%d-%m-%Y %I:%M %p}", reply_markup=self.admin_menu())
            raise ApplicationHandlerStop

    async def restore_scheduled_broadcasts(self, application: Application, owner_id: int):
        jobs = await pending_scheduled_broadcasts(owner_id)
        now = datetime.now(timezone.utc)
        for job in jobs:
            run_at = job.get("run_at") or now
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            existing = application.job_queue.get_jobs_by_name(f"scheduled_{job['job_id']}")
            if existing:
                continue
            application.job_queue.run_once(self.scheduled_broadcast_job, when=max(run_at, now), data=job, name=f"scheduled_{job['job_id']}")
        if jobs:
            logger.info("Restored scheduled broadcasts owner_id=%s count=%s", owner_id, len(jobs))

    async def scheduled_broadcast_job(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job.data
        job_id = job["job_id"]
        owner = int(job["owner_id"])
        claimed = await claim_scheduled_broadcast(job_id)
        if not claimed:
            return
        try:
            from database.seller_data import c, USERS
            users = c(USERS).find({"owner_id": owner}, {"user_id": 1})
            success = failed = 0
            async for user in users:
                if await broadcast_cancel_requested(job_id):
                    break
                uid = user.get("user_id")
                if not uid or uid == owner:
                    continue
                try:
                    await context.bot.copy_message(uid, job["from_chat_id"], job["message_id"])
                    success += 1
                except Exception as exc:
                    failed += 1
                    await save_failed_delivery(owner, uid, "scheduled_broadcast", {"job_id": job_id}, str(exc))
                await asyncio.sleep(0.05)
            await set_scheduled_status(job_id, "completed", {"success": success, "failed": failed})
            try:
                # Scheduled jobs are stored under the clone-specific data scope.
                # Resolve the real seller Telegram account before sending a DM.
                from database.seller_bots import get_bot_by_data_owner_id
                bot_record = await get_bot_by_data_owner_id(owner)
                seller_chat_id = int((bot_record or {}).get("seller_account_id") or (bot_record or {}).get("owner_id") or owner)
                await context.bot.send_message(seller_chat_id, f"✅ Scheduled broadcast completed\nSuccess: {success}\nFailed: {failed}")
            except Exception:
                logger.exception("Scheduled broadcast completion notice failed job_id=%s owner_id=%s", job_id, owner)
        except Exception as exc:
            logger.exception("Scheduled broadcast execution failed job_id=%s owner_id=%s", job_id, owner)
            await release_scheduled_broadcast(job_id, str(exc))
