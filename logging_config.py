"""Backward-compatible logging imports.

New code should import from ``services.logger`` directly.
"""

from services.logger import get_logger, mask_sensitive, setup_logging

__all__ = ["get_logger", "mask_sensitive", "setup_logging"]
