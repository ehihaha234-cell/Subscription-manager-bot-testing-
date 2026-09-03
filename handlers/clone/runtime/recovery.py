"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneRuntimeRecoveryMixin:
    async def _restore_one(self, bot_id: int) -> bool:
        async with self._restore_semaphore:
            return await self.start_bot(bot_id)

    async def restore_active_bots(self):
        records = await get_all_active_bots()
        if not records:
            return {"started": 0, "failed": 0}
        results = await asyncio.gather(
            *(self._restore_one(int(record["bot_id"])) for record in records),
            return_exceptions=True,
        )
        started = sum(result is True for result in results)
        failed = len(results) - started
        for result in results:
            if isinstance(result, Exception):
                logger.error("Clone bot restore task failed", exc_info=(type(result), result, result.__traceback__))
        return {"started": started, "failed": failed}

    async def _recover_bot_with_retry(
        self,
        bot_id: int,
        *,
        max_attempts: int = 3,
        delays=(2, 5, 10),
    ) -> bool:
        """Recover one clone bot with bounded retries and recovery metrics."""
        bot_id = int(bot_id)
        last_error = ""
        record = await get_bot_by_bot_id(bot_id)
        if not record:
            return False
        if str(record.get("runtime_status") or "").lower() in {
            "invalid_token",
            "token_missing",
            "plan_limit_paused",
        }:
            return False
        if not await recovery_allowed(record):
            return False
        claim = await claim_runtime_recovery(bot_id, cooldown_seconds=300)
        if not claim:
            return False

        for attempt in range(1, max_attempts + 1):
            self._recovery_attempts[bot_id] = attempt
            self._recovery_totals[bot_id] = self._recovery_totals.get(bot_id, 0) + 1
            logger.warning(
                "[RECOVERY] clone bot restart bot_id=%s attempt=%s/%s",
                bot_id,
                attempt,
                max_attempts,
            )

            try:
                recovered = await self.restart_bot(bot_id)
                if recovered:
                    now = datetime.now(timezone.utc)
                    self._last_recovery_at[bot_id] = now
                    self._last_recovery_error.pop(bot_id, None)
                    self._recovery_attempts[bot_id] = 0
                    logger.info(
                        "[RECOVERY] clone bot restored bot_id=%s attempt=%s time=%s",
                        bot_id,
                        attempt,
                        now.isoformat(),
                    )
                    await finish_runtime_recovery(bot_id, True)
                    return True
                last_error = "restart_bot returned False"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)[:500]
                logger.exception(
                    "[RECOVERY] clone bot restart failed bot_id=%s attempt=%s",
                    bot_id,
                    attempt,
                )

            self._last_failure_at[bot_id] = datetime.now(timezone.utc)
            self._last_recovery_error[bot_id] = last_error
            if attempt < max_attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)]
                await asyncio.sleep(max(0, delay))

        self._recovery_attempts[bot_id] = 0
        try:
            failures = int((claim or {}).get("consecutive_recovery_failures", 0)) + 1
            retry_after = min(3600, 300 * (2 ** min(failures - 1, 3)))
            await finish_runtime_recovery(
                bot_id, False, last_error[:500], retry_after_seconds=retry_after
            )
        except Exception:
            logger.exception(
                "Could not save recovery failure status bot_id=%s",
                bot_id,
            )
        return False

    async def recover_dead_bots(self):
        """Recover stopped and unexpectedly missing active clone-bot runtimes."""
        if self._watchdog_lock.locked():
            return {
                "checked": len(self._running),
                "candidates": 0,
                "restarted": 0,
                "failed": 0,
                "skipped": True,
            }

        async with self._watchdog_lock:
            records = await get_all_active_bots()
            active_ids = {
                int(record["bot_id"])
                for record in records
                if record.get("bot_id") is not None
            }

            # Remove stale in-memory entries for bots that are no longer active.
            stale_ids = [
                int(bot_id)
                for bot_id in list(self._running)
                if int(bot_id) not in active_ids
            ]
            for bot_id in stale_ids:
                running = self._running.pop(bot_id, None)
                if running:
                    await self._safe_shutdown(running.application)

            candidates = []
            for bot_id in active_ids:
                running = self._running.get(bot_id)
                if running is None:
                    candidates.append(bot_id)
                    continue

                app = running.application
                updater_running = bool(app.updater and app.updater.running)
                if not app.running or not updater_running:
                    candidates.append(bot_id)

            if not candidates:
                return {
                    "checked": len(active_ids),
                    "candidates": 0,
                    "restarted": 0,
                    "failed": 0,
                    "stale_removed": len(stale_ids),
                    "skipped": False,
                }

            logger.warning(
                "Clone bot watchdog recovery candidates: %s",
                candidates,
            )
            results = await asyncio.gather(
                *(self._recover_bot_with_retry(bot_id) for bot_id in candidates),
                return_exceptions=True,
            )

            restarted = sum(result is True for result in results)
            failed = len(results) - restarted
            for bot_id, result in zip(candidates, results):
                if isinstance(result, Exception):
                    self._last_failure_at[bot_id] = datetime.now(timezone.utc)
                    self._last_recovery_error[bot_id] = str(result)[:500]
                    logger.error(
                        "Clone bot watchdog task failed bot_id=%s",
                        bot_id,
                        exc_info=(type(result), result, result.__traceback__),
                    )

            return {
                "checked": len(active_ids),
                "candidates": len(candidates),
                "restarted": restarted,
                "failed": failed,
                "stale_removed": len(stale_ids),
                "skipped": False,
            }

