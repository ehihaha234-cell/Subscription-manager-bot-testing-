from __future__ import annotations

from telegram.ext import ContextTypes

from handlers.common.error_handler import notify_update_error, report_exception


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return

    await report_exception(error, update=update)
    await notify_update_error(update)
