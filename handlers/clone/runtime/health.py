"""Focused clone-bot feature mixin; behavior preserved from services.bot_manager."""

from handlers.common.clone_context import *


class CloneRuntimeHealthMixin:
    async def runtime_health(self):
        """Return clone-bot runtime information for health endpoints."""
        records = await get_all_active_bots()
        active_ids = {
            int(record["bot_id"])
            for record in records
            if record.get("bot_id") is not None
        }
        running_ids = set()
        unhealthy_ids = []

        for bot_id, running in list(self._running.items()):
            app = running.application
            healthy = bool(
                app.running
                and app.updater
                and app.updater.running
            )
            if healthy:
                running_ids.add(int(bot_id))
            else:
                unhealthy_ids.append(int(bot_id))

        offline_ids = sorted(active_ids - running_ids)
        all_metric_ids = active_ids | set(self._recovery_totals)
        recovery_total = sum(
            self._recovery_totals.get(bot_id, 0)
            for bot_id in all_metric_ids
        )

        def iso(value):
            return value.isoformat() if value else None

        return {
            "active": len(active_ids),
            "running": len(running_ids),
            "offline": len(offline_ids),
            "unhealthy": len(unhealthy_ids),
            "offline_bot_ids": offline_ids,
            "unhealthy_bot_ids": sorted(unhealthy_ids),
            "recovery_attempts_total": recovery_total,
            "currently_recovering": sorted(
                bot_id
                for bot_id, attempt in self._recovery_attempts.items()
                if attempt > 0
            ),
            "last_recovery_at": {
                str(bot_id): iso(value)
                for bot_id, value in self._last_recovery_at.items()
            },
            "last_failure_at": {
                str(bot_id): iso(value)
                for bot_id, value in self._last_failure_at.items()
            },
            "last_errors": {
                str(bot_id): error
                for bot_id, error in self._last_recovery_error.items()
                if error
            },
        }

    async def shutdown_all(self):
        bot_ids = list(self._running)
        if bot_ids:
            await asyncio.gather(
                *(self.stop_bot(bot_id, "service_stopped") for bot_id in bot_ids),
                return_exceptions=True,
            )

