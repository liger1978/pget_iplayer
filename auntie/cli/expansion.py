"""PID expansion helpers."""

from __future__ import annotations

from typing import Sequence

from .debug import debug_log
from .metadata import get_bbc_episode_pids
from .pids import normalise_pid


def expand_pids(pids: Sequence[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()

    for raw_pid in pids:
        pid = normalise_pid(raw_pid)
        try:
            episode_pids = [normalise_pid(ep) for ep in get_bbc_episode_pids(pid)]
            debug_log(f"Expanded {pid} into {episode_pids or '[no additional episodes]'}")
        except Exception as exc:
            debug_log(f"Failed to expand {pid}: {exc!r}")
            episode_pids = []

        candidates = episode_pids or [pid]
        for candidate in candidates:
            if candidate not in seen:
                expanded.append(candidate)
                seen.add(candidate)

    return expanded


__all__ = ["expand_pids"]
