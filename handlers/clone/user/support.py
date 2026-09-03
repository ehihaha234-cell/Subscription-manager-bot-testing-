"""Feature callback handler extracted from the legacy clone callback router."""

from handlers.common.clone_context import *
from handlers.common.feature_navigation import feature_back_callback


async def handle(self, update, context, q, owner, action):
    back_keyboard = self.back(feature_back_callback(context))
    if action == 'c_support':
        support = await get_live_support_settings(owner)
        if not support.get('enabled'):
            await self.safe_query_message(q, '🔴 Live support is currently unavailable. Please try again later.', back_keyboard)
            return True
        await self.safe_query_message(q, '💬 Live Support is ON.\n\nSend any text, photo, video, voice, audio, document or sticker here. Your message will stay in the support conversation and will not be auto-deleted.', back_keyboard)
        return True
    return False
