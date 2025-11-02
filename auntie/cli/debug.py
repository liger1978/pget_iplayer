"""Lightweight debug helpers for the auntie CLI."""

from __future__ import annotations

from typing import Final

from tqdm import tqdm

DEBUG_PREFIX: Final[str] = "[debug] "
DEBUG_ENABLED: bool = False


def set_debug(enabled: bool) -> None:
    """Enable or disable verbose debug logging."""
    global DEBUG_ENABLED
    DEBUG_ENABLED = bool(enabled)


def debug_log(message: str) -> None:
    """Emit a debug log line if debugging is enabled."""
    if not DEBUG_ENABLED:
        return
    try:
        tqdm.write(f"{DEBUG_PREFIX}{message}")
    except Exception:
        # tqdm can raise exceptions while finalising progress bars; ignore them quietly.
        pass


__all__ = ["DEBUG_ENABLED", "debug_log", "set_debug"]
