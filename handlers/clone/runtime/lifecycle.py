"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneRuntimeLifecycleMixin:
    async def start_bot(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        async with self._lock_for(bot_id):
            if bot_id in self._running:
                running = self._running[bot_id]
                if running.application.running and (
                    not running.application.updater or running.application.updater.running
                ):
                    return True
                self._running.pop(bot_id, None)

            # Runtime lifecycle identity is always the unique Telegram clone bot_id.
            # Never fall back to seller/owner ID: one seller may own many clones.
            record = await get_bot_by_bot_id(bot_id)
            if not record or not record.get("active") or record.get("status") == "removed":
                return False

            bot_id = int(record["bot_id"])
            seller_account_id = int(record["owner_id"])
            allowed, quota = await bot_runtime_allowed(seller_account_id, bot_id)
            if not allowed:
                limit = quota.get("limit", 0)
                position = quota.get("position")
                reason = (
                    f"Clone bot position {position} exceeds seller plan limit {limit}"
                    if position is not None
                    else f"Seller plan allows {limit} clone bots"
                )
                await set_runtime_status(bot_id, "plan_limit_paused", reason)
                logger.warning(
                    "Clone bot blocked by seller plan bot_id=%s owner_id=%s position=%s limit=%s",
                    bot_id, seller_account_id, position, limit,
                )
                return False

            token = await get_decrypted_bot_token(bot_id)
            if not token:
                await set_runtime_status(bot_id, "token_missing", "Missing encrypted token")
                return False

            app: Optional[Application] = None
            data_owner_id = int(record.get("data_owner_id") or record["owner_id"])
            try:
                await asyncio.wait_for(
                    ensure_seller_defaults(data_owner_id, record.get("bot_name", "Subscription Bot")),
                    timeout=20,
                )
                app = self.build_app(token, data_owner_id, seller_account_id, bot_id=bot_id)
                await asyncio.wait_for(app.initialize(), timeout=25)

                # Clone bots use long-polling only. A stale Telegram webhook
                # on the same token makes getUpdates fail with a Conflict.
                # Remove it before starting polling so restarts/redeploys can
                # recover cleanly without requiring manual Bot API calls.
                try:
                    webhook_info = await asyncio.wait_for(app.bot.get_webhook_info(), timeout=10)
                    webhook_url = str(getattr(webhook_info, "url", "") or "")
                    if webhook_url:
                        logger.warning(
                            "Removing active webhook before clone polling bot_id=%s owner_id=%s url=%s",
                            bot_id, data_owner_id, webhook_url,
                        )
                    # Always call delete_webhook, even when Telegram reports no
                    # URL. This makes the polling startup deterministic after a
                    # previous webhook deployment and is harmless when none is set.
                    await asyncio.wait_for(
                        app.bot.delete_webhook(drop_pending_updates=False),
                        timeout=15,
                    )
                    # Verify Telegram no longer has an active webhook before
                    # polling starts. This prevents a stale webhook from
                    # producing getUpdates Conflict after a restart.
                    verify = await asyncio.wait_for(app.bot.get_webhook_info(), timeout=10)
                    if str(getattr(verify, "url", "") or ""):
                        raise RuntimeError(
                            f"Telegram webhook is still active after deletion: {getattr(verify, 'url', '')}"
                        )
                except Exception:
                    logger.exception(
                        "Could not clear Telegram webhook before polling bot_id=%s owner_id=%s",
                        bot_id, data_owner_id,
                    )
                    raise

                await asyncio.wait_for(app.start(), timeout=15)
                await asyncio.wait_for(
                    app.updater.start_polling(
                        drop_pending_updates=False,
                        allowed_updates=Update.ALL_TYPES,
                        bootstrap_retries=-1,
                    ),
                    timeout=35,
                )
                self._running[bot_id] = RunningSellerBot(
                    data_owner_id,
                    bot_id,
                    app,
                )
                await set_runtime_status(bot_id, "running", None)

                try:
                    await self.restore_scheduled_broadcasts(
                        app,
                        data_owner_id,
                    )
                except Exception:
                    logger.exception(
                        "Scheduled broadcast restoration failed "
                        "bot_id=%s owner_id=%s",
                        bot_id,
                        data_owner_id,
                    )
                try:
                    await self.restore_scheduled_campaigns(app, data_owner_id)
                except Exception:
                    logger.exception(
                        "Scheduled campaign restoration failed bot_id=%s owner_id=%s",
                        bot_id, data_owner_id,
                    )

                logger.info(
                    "Clone bot started bot_id=%s owner_id=%s",
                    bot_id,
                    data_owner_id,
                )
                return True
            except InvalidToken as exc:
                logger.warning(
                    "Clone bot token rejected; automatic retries disabled bot_id=%s owner_id=%s",
                    bot_id,
                    seller_account_id,
                )
                try:
                    await mark_invalid_token(bot_id, exc)
                except Exception:
                    logger.exception(
                        "Could not save invalid-token status bot_id=%s",
                        bot_id,
                    )
                if app:
                    await self._safe_shutdown(app)
                return False
            except Exception as exc:
                logger.exception("Seller bot start failed bot_id=%s", bot_id)
                try:
                    await set_runtime_status(bot_id, "error", str(exc)[:500])
                except Exception:
                    logger.exception("Could not save clone bot failure status bot_id=%s", bot_id)
                if app:
                    await self._safe_shutdown(app)
                return False

    async def _safe_shutdown(self, app):
        try:
            if app.updater and app.updater.running:
                await asyncio.wait_for(app.updater.stop(), timeout=15)
        except Exception:
            logger.debug("Clone updater stop failed", exc_info=True)
        try:
            if app.running:
                await asyncio.wait_for(app.stop(), timeout=15)
        except Exception:
            logger.debug("Clone application stop failed", exc_info=True)
        try:
            await asyncio.wait_for(app.shutdown(), timeout=15)
        except Exception:
            logger.debug("Clone application shutdown failed", exc_info=True)

    async def stop_bot(self, bot_id: int, runtime_status="paused"):
        bot_id = int(bot_id)
        async with self._lock_for(bot_id):
            # Runtime lifecycle is strictly keyed by the unique clone bot ID.
            # Never fall back to owner/seller IDs here: one seller can own multiple
            # clones, so such a fallback could stop the wrong clone.
            running = self._running.pop(bot_id, None)
            if running:
                await self._safe_shutdown(running.application)
            await set_runtime_status(bot_id, runtime_status, None)
            return True

    async def restart_bot(self, bot_id):
        await self.stop_bot(bot_id, "restarting")
        return await self.start_bot(bot_id)

