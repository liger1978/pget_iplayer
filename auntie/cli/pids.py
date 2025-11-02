"""PID parsing and normalisation helpers."""

from __future__ import annotations

import re

from .debug import debug_log

PID_PATTERN = re.compile(r"([a-z][b-df-hj-np-tv-z0-9]{7,10})", re.IGNORECASE)

BBC_IPLAYER_SINGLE_EPISODE_PREFIX = "https://www.bbc.co.uk/iplayer/episode/"
BBC_IPLAYER_SERIES_BRAND_PREFIX = "https://www.bbc.co.uk/iplayer/episodes/"


def normalise_pid(value: str) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        return ""

    lowered = trimmed.lower()

    if lowered.startswith(BBC_IPLAYER_SINGLE_EPISODE_PREFIX):
        candidate = trimmed[len(BBC_IPLAYER_SINGLE_EPISODE_PREFIX) :]
        candidate = candidate.split("/", 1)[0]
        candidate = candidate.split("?", 1)[0]
        candidate = candidate.split("#", 1)[0]
        candidate = candidate.strip()
        debug_log(
            f"Normalising PID from single episode URL '{trimmed}'; "
            f"extracted candidate '{candidate or '<empty>'}'"
        )
        if candidate and PID_PATTERN.fullmatch(candidate):
            result = candidate.lower()
            debug_log(f"Using PID '{result}' from single episode URL")
            return result
        debug_log(
            "No valid PID immediately after single episode URL prefix; "
            "falling back to generic parsing"
        )

    matches = PID_PATTERN.findall(trimmed)
    if matches:
        if lowered.startswith(BBC_IPLAYER_SERIES_BRAND_PREFIX):
            debug_log(f"Normalising PID from series/brand URL '{trimmed}'; candidates={matches}")
            for candidate in reversed(matches):
                if any(ch.isdigit() for ch in candidate):
                    result = candidate.lower()
                    debug_log(f"Using PID '{result}' from series/brand URL")
                    return result
            result = matches[-1].lower()
            debug_log(
                f"No candidate with digits in series/brand URL; defaulting to last PID '{result}'"
            )
            return result

        for candidate in reversed(matches):
            if any(ch.isdigit() for ch in candidate):
                result = candidate.lower()
                debug_log(f"Using PID '{result}' from matches {matches}")
                return result
        result = matches[-1].lower()
        debug_log(
            f"No candidate with digits found; defaulting to last PID '{result}' from matches {matches}"
        )
        return result

    result = trimmed.lower()
    debug_log(f"No PID match found; returning stripped value '{result}'")
    return result


__all__ = [
    "BBC_IPLAYER_SERIES_BRAND_PREFIX",
    "BBC_IPLAYER_SINGLE_EPISODE_PREFIX",
    "PID_PATTERN",
    "normalise_pid",
]
