"""Helpers for locating and invoking get_iplayer."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Sequence, Tuple

from .debug import debug_log
from .pids import normalise_pid
from .utils import format_command


@lru_cache(maxsize=1)
def resolve_get_iplayer_entrypoint() -> str:
    override = os.environ.get("GET_IPLAYER_COMMAND")
    if override:
        debug_log(f"Using get_iplayer entrypoint from GET_IPLAYER_COMMAND: {override}")
        return override
    if os.name == "nt":
        candidates = (
            "get_iplayer.cmd",
            "get_iplayer.bat",
            "get_iplayer.exe",
            "get_iplayer",
        )
    else:
        candidates = ("get_iplayer",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            debug_log(f"Found get_iplayer on PATH: {resolved}")
            return resolved
    if os.name == "nt":
        probable_locations = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if not base:
                continue
            probable_locations.append(Path(base) / "get_iplayer" / "get_iplayer.cmd")
        for candidate in probable_locations:
            if candidate.exists():
                resolved = str(candidate)
                debug_log(f"Using get_iplayer entrypoint from installer path: {resolved}")
                return resolved
    resolved = candidates[0]
    debug_log(f"Fallback get_iplayer entrypoint: {resolved}")
    return resolved


@lru_cache(maxsize=1)
def get_iplayer_invocation() -> Tuple[str, ...]:
    entrypoint = resolve_get_iplayer_entrypoint()
    if os.name == "nt":
        lowered = entrypoint.lower()
        if lowered.endswith((".cmd", ".bat")):
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            invocation = (comspec, "/c", entrypoint)
            debug_log(f"get_iplayer invocation via COMSPEC: {format_command(invocation)}")
            return invocation
    invocation = (entrypoint,)
    debug_log(f"get_iplayer invocation: {format_command(invocation)}")
    return invocation


def build_download_command(pid: str, output_dir: Path) -> Sequence[str]:
    normalised = normalise_pid(pid)
    base_command = list(get_iplayer_invocation())
    return [
        *base_command,
        "--get",
        "--subtitles",
        "--subs-embed",
        "--force",
        "--overwrite",
        "--tv-quality=fhd,hd,sd",
        "--log-progress",
        "--output",
        str(output_dir),
        f"--pid={normalised}",
    ]


__all__ = ["build_download_command", "get_iplayer_invocation", "resolve_get_iplayer_entrypoint"]
