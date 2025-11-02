"""General utility helpers for the auntie CLI."""

from __future__ import annotations

import re
import shlex
from datetime import datetime
from typing import Iterable, Optional, Sequence

INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]")
WHITESPACE_PATTERN = re.compile(r"\s+")


def format_command(command: Sequence[str]) -> str:
    """Return a shell-escaped representation of *command*."""
    return " ".join(shlex.quote(part) for part in command)


def next_delimiter(buffer: str) -> int | None:
    """Return the index of the next newline or carriage-return in *buffer*."""
    newline = buffer.find("\n")
    carriage = buffer.find("\r")
    indices = [idx for idx in (newline, carriage) if idx != -1]
    if not indices:
        return None
    return min(indices)


def truncate_title(title: str, max_len: int = 10) -> str:
    """Trim and padding helper for fixed-width title fields."""
    clean = (title or "").strip()
    if len(clean) <= max_len:
        return clean.ljust(max_len)
    return clean[: max_len - 1] + "…"


def two_digit(value: str | int | None) -> str:
    """Return a two digit string representation, defaulting to '00'."""
    if value is None:
        return "00"
    if isinstance(value, int):
        return f"{value % 100:02d}"
    if isinstance(value, str) and value.isdigit():
        return f"{int(value) % 100:02d}"
    return "00"


def sanitize_filename_component(value: str | None) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", (value or "").strip())
    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """Deduplicate while preserving iteration order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def safe_int_to_str(value: Optional[int | str]) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdigit():
        return value
    return ""


def extract_broadcast_date(node: dict) -> str:
    """Return YYYYMMDD from first_broadcast_date if available."""
    date_str = node.get("first_broadcast_date")
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y%m%d")
    except Exception:
        return ""


__all__ = [
    "dedupe_preserve_order",
    "extract_broadcast_date",
    "format_command",
    "next_delimiter",
    "sanitize_filename_component",
    "safe_int_to_str",
    "truncate_title",
    "two_digit",
]
