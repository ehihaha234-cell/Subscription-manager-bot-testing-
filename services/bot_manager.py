"""Seller clone-bot manager facade.

The implementation is split into focused feature mixins while preserving the
public import path: ``from services.bot_manager import bot_manager``.
"""

from handlers.common.clone_context import *
from handlers.clone.menus import CloneMenusMixin
from handlers.clone.live_support_ui import CloneLiveSupportUIMixin
from handlers.clone.referral_unlock_ui import CloneReferralUnlockUIMixin
from handlers.clone.welcome_editor import CloneWelcomeEditorMixin
from handlers.clone.user_ui import CloneUserUIMixin
from handlers.clone.common_utils import CloneCommonUtilsMixin
from handlers.clone.start import CloneStartMixin
from handlers.clone.help import CloneHelpMixin
from handlers.clone.admin_entry import CloneAdminEntryMixin
from handlers.clone.plans import ClonePlansMixin
from handlers.clone.user_callbacks import CloneUserCallbacksMixin
from handlers.clone.admin_callbacks import CloneAdminCallbacksMixin
from handlers.clone.support_core import CloneSupportCoreMixin
from handlers.clone.support_commands import CloneSupportCommandsMixin
from handlers.clone.live_support import CloneLiveSupportMixin
from handlers.clone.broadcasting import CloneBroadcastMixin
from handlers.clone.media_handlers import CloneMediaHandlersMixin
from handlers.clone.channels import CloneChannelsMixin
from handlers.clone.payment import ClonePaymentDeliveryMixin
from handlers.clone.expiry import CloneExpiryMixin
from handlers.clone.runtime import CloneRuntimeAppMixin
from handlers.clone.runtime import CloneRuntimeLifecycleMixin
from handlers.clone.runtime import CloneRuntimeRecoveryMixin
from handlers.clone.runtime import CloneRuntimeHealthMixin


class SellerBotManager(
    CloneMenusMixin,
    CloneLiveSupportUIMixin,
    CloneReferralUnlockUIMixin,
    CloneWelcomeEditorMixin,
    CloneUserUIMixin,
    CloneCommonUtilsMixin,
    CloneStartMixin,
    CloneHelpMixin,
    CloneAdminEntryMixin,
    ClonePlansMixin,
    CloneUserCallbacksMixin,
    CloneAdminCallbacksMixin,
    CloneSupportCoreMixin,
    CloneSupportCommandsMixin,
    CloneLiveSupportMixin,
    CloneBroadcastMixin,
    CloneMediaHandlersMixin,
    CloneChannelsMixin,
    ClonePaymentDeliveryMixin,
    CloneExpiryMixin,
    CloneRuntimeAppMixin,
    CloneRuntimeLifecycleMixin,
    CloneRuntimeRecoveryMixin,
    CloneRuntimeHealthMixin,
):
    def __init__(self):
        self._running: Dict[int, RunningSellerBot] = {}
        self._bot_locks: Dict[int, asyncio.Lock] = {}
        self._restore_semaphore = asyncio.Semaphore(3)
        self._watchdog_lock = asyncio.Lock()
        self._recovery_attempts: Dict[int, int] = {}
        self._recovery_totals: Dict[int, int] = {}
        self._last_recovery_at: Dict[int, datetime] = {}
        self._last_failure_at: Dict[int, datetime] = {}
        self._last_recovery_error: Dict[int, str] = {}

    def _lock_for(self, bot_id: int) -> asyncio.Lock:
        bot_id = int(bot_id)
        lock = self._bot_locks.get(bot_id)
        if lock is None:
            lock = asyncio.Lock()
            self._bot_locks[bot_id] = lock
        return lock

    def is_running(self, owner_id: int) -> bool:
        return owner_id in self._running

    def get_running(self, owner_id: int):
        owner_id = int(owner_id)
        direct = self._running.get(owner_id)
        if direct:
            return direct
        return next((r for r in self._running.values() if int(r.owner_id) == owner_id), None)


bot_manager = SellerBotManager()
