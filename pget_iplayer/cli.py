"""Command-line interface for the pget_iplayer project."""

from __future__ import annotations

import argparse
import codecs
import errno
import itertools
import os
import re
import requests
import secrets
import select
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import BinaryIO, Dict, Iterable, Optional, Sequence, Tuple

from tqdm import tqdm

from . import __version__

PID_PATTERN = re.compile(r"([a-z][b-df-hj-np-tv-z0-9]{7,10})", re.IGNORECASE)


PROGRESS_LINE = re.compile(
    r"^\s*(?P<percent>\d+(?:\.\d+)?)%.*?@\s*(?P<speed>.*?)\s+ETA:\s*(?P<eta>\S+).*?\[(?P<stream>[^\]]+)\]\s*$",
    re.IGNORECASE,
)
COMPLETED_LINE = re.compile(
    r"INFO:\s+Downloaded:.*?@\s*(?P<speed>.*?)\s*\([^)]*\)\s*\[(?P<stream>[^\]]+)\]",
    re.IGNORECASE,
)


RESET = "\033[0m"

DEBUG_ENABLED = False


def _debug_log(message: str) -> None:
    if not DEBUG_ENABLED:
        return
    try:
        tqdm.write(f"[debug] {message}")
    except Exception:
        pass


def _format_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


@dataclass(frozen=True)
class ColourStyle:
    tqdm_name: str
    ansi_code: str

COLOUR_STYLES: Tuple[ColourStyle, ...] = (
    # Muted emerald – for active/highlight state
    ColourStyle("#1a4d41", "\033[38;2;26;77;65m"),
    # Soft olive – for success/ok state
    ColourStyle("#657253", "\033[38;2;101;114;83m"),
    # Dusty teal – alternative accent
    ColourStyle("#4e7f7b", "\033[38;2;78;127;123m"),
    # Cool slate-grey blue
    ColourStyle("#5a6b7d", "\033[38;2;90;107;125m"),
    # Slate blue
    ColourStyle("#475d7b", "\033[38;2;71;93;123m"),
    # Soft off-white / chalk – for backgrounds or highlighting text on dark
    ColourStyle("#f5f5f5", "\033[38;2;245;245;245m"),
    # Pale champagne – for light accent or highlighting selection
    ColourStyle("#e8ddcf", "\033[38;2;232;221;207m"),
    # Warm mid-neutral – for secondary text
    ColourStyle("#606060", "\033[38;2;96;96;96m"),
    # Deep charcoal – for primary text on light background or background on dark console
    ColourStyle("#2e2e2e", "\033[38;2;46;46;46m"),
    # Warm taupe – for subtle backgrounds or blocks
    ColourStyle("#8f8173", "\033[38;2;143;129;115m"),
    # Cognac amber – for warnings or emphasis
    ColourStyle("#b37537", "\033[38;2;179;117;55m"),
    # Muted burgundy – for errors or critical/high state
    ColourStyle("#7a2f3b", "\033[38;2;122;47;59m")
)

STREAM_PRIORITY = ("waiting", "audio", "audio+video", "video", "converting")

PROGRESS_LOCK = threading.Lock()
PROGRESS_BARS: Dict[tuple[str, str], tqdm] = {}
PID_COLOUR: Dict[str, ColourStyle] = {}
COMPLETED_BARS: set[tuple[str, str]] = set()
STREAM_STATE: Dict[tuple[str, str], tuple[str | None, str | None, bool]] = {}
PID_LABELS: Dict[str, str] = {}
PSEUDO_TIMERS: Dict[tuple[str, str], Dict[str, float]] = {}
PID_METADATA: Dict[str, Dict[str, str]] = {}

DEFAULT_SPEED = "--.- Mb/s"
DEFAULT_ETA = "--:--:--"
ETA_FIELD_WIDTH = 8
SPEED_FIELD_WIDTH = 10
META_WIDTH = 5 + ETA_FIELD_WIDTH + 2 + SPEED_FIELD_WIDTH + 1  # "(ETA " + eta + ", " + speed + ")"
PROGRAM_LABEL_WIDTH = 42
STREAM_FIELD_WIDTH = 12
PSEUDO_STREAMS = {"waiting", "converting"}
PERCENT_WIDTH = 8
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mkv",
    ".mov",
    ".ts",
    ".avi",
    ".flv",
    ".wmv",
    ".webm",
    ".mpg",
    ".mpeg",
}
INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _reset_progress_state() -> None:
    with PROGRESS_LOCK:
        for bar in PROGRESS_BARS.values():
            try:
                bar.close()
            except Exception:
                pass
        PROGRESS_BARS.clear()
        PID_COLOUR.clear()
        COMPLETED_BARS.clear()
        STREAM_STATE.clear()
        PSEUDO_TIMERS.clear()
        PID_LABELS.clear()
        PID_METADATA.clear()


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="pget-iplayer",
        description=(
            "Parallel wrapper around get_iplayer for downloading multiple pids concurrently."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "pids",
        metavar="PID",
        nargs="+",
        help="One or more BBC programme, series (season) or brand (show) PIDs or URLs to download.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable verbose debug logging of get_iplayer interactions",
    )
    parser.add_argument(
        "-n",
        "--no-clean",
        action="store_true",
        help="Preserve the temporary download subdirectory instead of deleting it",
    )
    parser.add_argument(
        "-p",
        "--plex",
        action="store_true",
        help="Rename completed video files to Plex naming convention",
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=os.cpu_count() or 4,
        help="Maximum number of parallel download workers",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Display the installed version and exit.",
    )
    return parser


def _command_for_pid(pid: str, output_dir: Path) -> Sequence[str]:
    normalised = _normalise_pid(pid)
    base_command = list(_get_iplayer_invocation())
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


def _emit_line(pid: str, colour: ColourStyle, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return

    lower = stripped.lower()
    if "converting" in lower or "tagging" in lower:
        colour = _get_colour(pid, colour)
        _start_pseudo_stream(pid, "converting", colour)
    match = PROGRESS_LINE.match(stripped)
    complete_match = None
    if not match:
        complete_match = COMPLETED_LINE.search(stripped)
        if DEBUG_ENABLED and complete_match is None:
            _debug_log(f"{pid}: output: {stripped}")
        if not complete_match:
            return
        percent = 100.0
        stream = complete_match.group("stream").strip().lower()
        speed = complete_match.group("speed").strip()
        eta = "00:00:00"
    else:
        percent = float(match.group("percent"))
        stream = match.group("stream").strip().lower()
        speed = match.group("speed").strip()
        eta = match.group("eta").strip()
        if stream not in {"waiting", "converting"}:
            colour = _get_colour(pid, colour)
            _complete_pseudo_stream(pid, "waiting", colour)
    _update_progress(pid, stream, percent, colour, speed, eta)


def _next_delimiter(buffer: str) -> int | None:
    newline = buffer.find("\n")
    carriage = buffer.find("\r")
    indices = [idx for idx in (newline, carriage) if idx != -1]
    if not indices:
        return None
    return min(indices)


def _get_colour(pid: str, default: ColourStyle) -> ColourStyle:
    with PROGRESS_LOCK:
        return PID_COLOUR.setdefault(pid, default)


def _normalise_pid(value: str) -> str:
    matches = PID_PATTERN.findall(value)
    if matches:
        for candidate in reversed(matches):
            if any(ch.isdigit() for ch in candidate):
                return candidate.lower()
    return value.strip().lower()


@lru_cache(maxsize=1)
def _resolve_get_iplayer_entrypoint() -> str:
    override = os.environ.get("PGET_IPLAYER_COMMAND")
    if override:
        _debug_log(f"Using get_iplayer entrypoint from PGET_IPLAYER_COMMAND: {override}")
        return override
    if os.name == "nt":
        candidates = ("get_iplayer.cmd", "get_iplayer.bat", "get_iplayer.exe", "get_iplayer")
    else:
        candidates = ("get_iplayer",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            _debug_log(f"Found get_iplayer on PATH: {resolved}")
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
                _debug_log(f"Using get_iplayer entrypoint from installer path: {resolved}")
                return resolved
    resolved = candidates[0]
    _debug_log(f"Fallback get_iplayer entrypoint: {resolved}")
    return resolved


@lru_cache(maxsize=1)
def _get_iplayer_invocation() -> Tuple[str, ...]:
    entrypoint = _resolve_get_iplayer_entrypoint()
    if os.name == "nt":
        lowered = entrypoint.lower()
        if lowered.endswith((".cmd", ".bat")):
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            invocation = (comspec, "/c", entrypoint)
            _debug_log(f"get_iplayer invocation via COMSPEC: {_format_command(invocation)}")
            return invocation
    invocation = (entrypoint,)
    _debug_log(f"get_iplayer invocation: {_format_command(invocation)}")
    return invocation


def _truncate_title(title: str, max_len: int = 10) -> str:
    clean = (title or "").strip()
    if len(clean) <= max_len:
        return clean.ljust(max_len)
    return clean[: max_len - 1] + "…"


def _two_digit(value: str | int | None) -> str:
    if value is None:
        return "00"
    if isinstance(value, int):
        return f"{value % 100:02d}"
    if isinstance(value, str) and value.isdigit():
        return f"{int(value) % 100:02d}"
    return "00"


def _sanitize_filename_component(value: str | None) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", (value or "").strip())
    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned


def _format_plex_filename(metadata: Dict[str, str], pid: str, extension: str) -> str:
    show_name = _sanitize_filename_component(metadata.get("show_title")) or pid.upper()
    episode_name = _sanitize_filename_component(metadata.get("episode_title")) or pid.upper()
    season_number = _two_digit(metadata.get("season_number"))
    episode_number = _two_digit(metadata.get("episode_number"))

    extension = extension if extension.startswith(".") else f".{extension}"
    extension = extension or ".mp4"

    base = f"{show_name} - s{season_number}e{episode_number} - {episode_name}".strip()
    if not base:
        base = pid.upper()

    max_stem_len = max(1, 255 - len(extension))
    if len(base) > max_stem_len:
        base = base[:max_stem_len].rstrip()

    return f"{base}{extension}"


def _ensure_unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        new_candidate = directory / f"{stem} ({index}){suffix}"
        if not new_candidate.exists():
            return new_candidate
        index += 1


def _locate_download_directory(token: str, pid: str) -> Path | None:
    expected = Path.cwd() / f".pget_iplayer-{pid}-{token}"
    if expected.exists():
        return expected
    suffix = f"-{pid}-{token}"
    for candidate in Path.cwd().iterdir():
        if candidate.is_dir() and candidate.name.startswith(".pget_iplayer-") and candidate.name.endswith(suffix):
            return candidate
    return None


def _find_downloaded_video(download_dir: Path) -> Path | None:
    best_candidate: tuple[float, int, Path] | None = None
    for path in download_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        candidate_key = (stat.st_mtime, stat.st_size)
        if best_candidate is None or candidate_key > best_candidate[:2]:
            best_candidate = (stat.st_mtime, stat.st_size, path)
    if best_candidate is None:
        return None
    return best_candidate[2]


def _move_video_to_root(
    video_path: Path,
    print_lock: threading.Lock,
) -> Path | None:
    destination = _ensure_unique_path(Path.cwd(), video_path.name)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        video_path.rename(destination)
    except OSError as exc:
        with print_lock:
            tqdm.write(f"{video_path.name}: failed to move video ({exc})")
        return None
    return destination


def _rename_video_for_plex(
    pid: str,
    source_path: Path,
    print_lock: threading.Lock,
) -> Path | None:
    metadata = PID_METADATA.get(pid)
    if metadata is None:
        try:
            metadata = bbc_metadata_from_pid(pid)
        except Exception:
            metadata = {}
        PID_METADATA[pid] = metadata

    target_name = _format_plex_filename(metadata, pid, source_path.suffix)
    if source_path.name == target_name:
        return source_path

    destination = _ensure_unique_path(source_path.parent, target_name)
    try:
        source_path.rename(destination)
    except OSError as exc:
        with print_lock:
            tqdm.write(
                f"{pid}: failed to rename {source_path.name} -> {destination.name} ({exc})"
            )
        return None

    with print_lock:
        tqdm.write(f"{pid}: renamed to {destination.name}")
    return destination


def _build_program_label(pid: str) -> str:
    try:
        metadata = bbc_metadata_from_pid(pid)
    except Exception:
        metadata = {}
    PID_METADATA[pid] = metadata

    show = _truncate_title(metadata.get("show_title", ""))
    episode = _truncate_title(metadata.get("episode_title", ""))
    season_number = _two_digit(metadata.get("season_number"))
    episode_number = _two_digit(metadata.get("episode_number"))

    base = (
        f"{pid}: {show} - s{season_number}e{episode_number} - {episode}"
    )
    if len(base) < PROGRAM_LABEL_WIDTH:
        base = base.ljust(PROGRAM_LABEL_WIDTH)
    elif len(base) > PROGRAM_LABEL_WIDTH:
        base = base[:PROGRAM_LABEL_WIDTH]
    return base


def _expand_pids(pids: Sequence[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()

    for raw_pid in pids:
        pid = _normalise_pid(raw_pid)
        try:
            episode_pids = [
                _normalise_pid(ep) for ep in get_bbc_episode_pids(pid)
            ]
            if DEBUG_ENABLED:
                _debug_log(f"Expanded {pid} into {episode_pids or '[no additional episodes]'}")
        except Exception as exc:
            if DEBUG_ENABLED:
                _debug_log(f"Failed to expand {pid}: {exc!r}")
            episode_pids = []

        candidates = episode_pids or [pid]
        for candidate in candidates:
            if candidate not in seen:
                expanded.append(candidate)
                seen.add(candidate)

    return expanded


def _stream_sort_key(stream: str) -> tuple[int, int, str]:
    for index, name in enumerate(STREAM_PRIORITY):
        if stream.startswith(name):
            return (0, index, stream)
    return (1, 0, stream)


def _sorted_keys() -> list[tuple[str, str]]:
    return sorted(
        PROGRESS_BARS.keys(),
        key=lambda item: (item[0], _stream_sort_key(item[1])),
    )


def _program_label(pid: str) -> str:
    label = PID_LABELS.get(pid)
    if label:
        return label
    fallback = f"{pid}: "
    if len(fallback) < PROGRAM_LABEL_WIDTH:
        fallback = fallback.ljust(PROGRAM_LABEL_WIDTH)
    else:
        fallback = fallback[: PROGRAM_LABEL_WIDTH]
    return fallback


def _format_percent(value: float) -> str:
    return f"{value:6.1f}% "


def _format_meta(speed: str | None, eta: str | None, completed: bool = False) -> str:
    if completed:
        return "(completed)".ljust(META_WIDTH)
    eta_val = (eta or DEFAULT_ETA)[:ETA_FIELD_WIDTH].ljust(ETA_FIELD_WIDTH)
    speed_val = (speed or DEFAULT_SPEED)[:SPEED_FIELD_WIDTH].rjust(SPEED_FIELD_WIDTH)
    return f"(ETA {eta_val}, {speed_val})".ljust(META_WIDTH)


def _compose_desc(
    pid: str,
    stream: str,
    percent: float,
    speed: str | None,
    eta: str | None,
    completed: bool = False,
) -> str:
    percent_display = 100.0 if completed else percent
    stream_display = f"{stream}"[:STREAM_FIELD_WIDTH].ljust(STREAM_FIELD_WIDTH)
    if stream in PSEUDO_STREAMS:
        percent_part = _format_percent(percent_display)
        meta_part = _format_meta(speed, eta, completed)
        if not completed:
            percent_part = " " * PERCENT_WIDTH
            meta_part = " " * META_WIDTH
    else:
        percent_part = _format_percent(percent_display)
        meta_part = _format_meta(speed, eta, completed)
    return f"{_program_label(pid)} {stream_display}{percent_part}{meta_part}"


def _start_pseudo_stream(pid: str, stream: str, colour: ColourStyle) -> None:
    key = (pid, stream)
    now = time.perf_counter()
    if key in PSEUDO_TIMERS:
        return
    PSEUDO_TIMERS[key] = {"start": now, "last": 0.0}
    _update_progress(pid, stream, 1.0, colour, None, None)


def _tick_pseudo_stream(pid: str, stream: str, colour: ColourStyle) -> None:
    key = (pid, stream)
    timer = PSEUDO_TIMERS.get(key)
    if not timer:
        return
    now = time.perf_counter()
    elapsed = now - timer["start"]
    percent = min(99.0, (elapsed / 300.0) * 100.0)
    _update_progress(pid, stream, percent, colour, None, None)


def _complete_pseudo_stream(pid: str, stream: str, colour: ColourStyle) -> None:
    key = (pid, stream)
    if key not in PSEUDO_TIMERS:
        return
    PSEUDO_TIMERS.pop(key, None)
    _update_progress(pid, stream, 100.0, colour, None, "00:00:00")


def _reassign_positions_locked() -> None:
    for position, key in enumerate(_sorted_keys()):
        bar = PROGRESS_BARS[key]
        pid, stream = key
        speed, eta, completed = STREAM_STATE.get(
            key, (None, None, key in COMPLETED_BARS)
        )
        bar.set_description_str(
            _compose_desc(pid, stream, bar.n, speed, eta, completed),
            refresh=False,
        )
        if bar.pos != position:
            bar.pos = position
            bar.refresh()


def _finalize_bars() -> list[str]:
    with PROGRESS_LOCK:
        _reassign_positions_locked()
        keys = _sorted_keys()
        bars = [PROGRESS_BARS[key] for key in keys]
        lines: list[str] = []
        for key, bar in zip(keys, bars):
            pid, stream = key
            speed, eta, completed = STREAM_STATE.get(
                key, (None, None, key in COMPLETED_BARS)
            )
            percent = 0.0
            if bar.total:
                percent = (bar.n / bar.total) * 100
            completed = completed or percent >= 100.0
            percent = min(100.0, max(0.0, percent))
            bar_blocks = 10
            filled_blocks = int(round(percent / 100.0 * bar_blocks))
            filled_blocks = max(0, min(bar_blocks, filled_blocks))
            bar_segment = "█" * filled_blocks + " " * (bar_blocks - filled_blocks)
            colour_style = PID_COLOUR.get(pid)
            bar_segment_coloured = bar_segment
            if colour_style:
                bar_segment_coloured = f"{colour_style.ansi_code}{bar_segment}{RESET}"
            desc = _compose_desc(pid, stream, percent, speed, eta, completed)
            if completed:
                desc = desc.rstrip() + " "
            lines.append(f"{desc}|{bar_segment_coloured}|")
        for bar in bars:
            bar.leave = False
        PROGRESS_BARS.clear()
        PID_COLOUR.clear()
        COMPLETED_BARS.clear()
        STREAM_STATE.clear()
    for bar in bars:
        bar.close()
    return lines


def _get_progress_bar(pid: str, stream: str, colour: ColourStyle) -> tqdm:
    key = (pid, stream)
    with PROGRESS_LOCK:
        bar = PROGRESS_BARS.get(key)
        if bar is None:
            desc = _compose_desc(pid, stream, 0.0, None, None)
            bar = tqdm(
                total=100.0,
                desc=desc,
                position=len(PROGRESS_BARS),
                leave=True,
                dynamic_ncols=True,
                colour=colour.tqdm_name,
                smoothing=0.0,
            )
            bar.bar_format = "{desc}|{bar}|"
            PROGRESS_BARS[key] = bar
            STREAM_STATE[key] = (None, None, False)
            _reassign_positions_locked()
        return bar


def _update_progress(
    pid: str,
    stream: str,
    percent: float,
    colour: ColourStyle,
    speed: str,
    eta: str,
) -> None:
    key = (pid, stream)
    if key in COMPLETED_BARS:
        return
    colour = _get_colour(pid, colour)
    bar = _get_progress_bar(pid, stream, colour)
    if bar is None:
        return
    with PROGRESS_LOCK:
        clamped_percent = max(0.0, min(100.0, percent))
        increment = clamped_percent - bar.n
        if increment < 0:
            bar.reset(total=100.0)
            bar.n = 0.0
            increment = clamped_percent
        bar.update(increment)
        is_complete_marker = clamped_percent >= 100.0 or eta == "00:00:00"
        bar.set_description_str(
            _compose_desc(
                pid,
                stream,
                clamped_percent,
                speed if speed else None,
                eta if eta else None,
                completed=is_complete_marker,
            ),
            refresh=False,
        )
        STREAM_STATE[key] = (
            speed if speed else None,
            eta if eta else None,
            is_complete_marker,
        )
        if is_complete_marker:
            COMPLETED_BARS.add(key)
            bar.n = bar.total
            _reassign_positions_locked()
        bar.refresh()


def _run_get_iplayer(
    pid: str,
    colour: ColourStyle,
    plex_mode: bool,
    clean_temp: bool,
    results: Dict[str, int],
    print_lock: threading.Lock,
    results_lock: threading.Lock,
) -> None:
    token = ""
    expected_download_dir = Path()
    while True:
        token = secrets.token_hex(4)
        subdir_name = f".pget_iplayer-{pid}-{token}"
        expected_download_dir = Path.cwd() / subdir_name
        if not expected_download_dir.exists():
            break
        if clean_temp:
            try:
                shutil.rmtree(expected_download_dir)
            except OSError as exc:
                with print_lock:
                    tqdm.write(
                        f"{pid}: unable to clear previous download directory ({exc})"
                    )
                with results_lock:
                    results[pid] = 1
                return
            break
    expected_download_dir.mkdir(parents=True, exist_ok=True)
    command = _command_for_pid(pid, expected_download_dir)
    if DEBUG_ENABLED:
        _debug_log(f"{pid}: using download subdir {subdir_name}")
    _debug_log(f"{pid}: launching get_iplayer with command: {_format_command(command)}")
    download_dir: Path | None = None
    moved_video: Path | None = None
    master_fd: int | None = None
    slave_fd: int | None = None
    process: subprocess.Popen | None = None
    output_queue: Queue[bytes] | None = None
    reader_thread: threading.Thread | None = None
    using_pty = os.name != "nt"
    decoder = None
    buffer = ""
    last_partial = ""

    def _cleanup_download_dir() -> None:
        if not clean_temp:
            return
        candidates: list[Path] = []
        if download_dir:
            candidates.append(download_dir)
        candidates.append(expected_download_dir)
        seen: set[Path] = set()
        for target in candidates:
            if not target:
                continue
            try:
                canonical = target.resolve(strict=False)
            except (OSError, RuntimeError):
                canonical = target
            if canonical in seen:
                continue
            seen.add(canonical)
            if not canonical.exists():
                continue
            try:
                shutil.rmtree(canonical)
            except FileNotFoundError:
                continue
            except OSError as exc:
                with print_lock:
                    tqdm.write(f"{pid}: failed to remove download directory ({exc})")

    try:
        if using_pty:
            try:
                master_fd, slave_fd = os.openpty()
            except OSError as exc:
                with print_lock:
                    tqdm.write(f"{pid}: unable to allocate pty ({exc})")
                _debug_log(f"{pid}: unable to allocate PTY ({exc})")
                with results_lock:
                    results[pid] = 1
                return

        try:
            if using_pty:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    text=False,
                    bufsize=0,
                    close_fds=True,
                )
            else:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=False,
                    bufsize=0,
                    close_fds=False,
                )
        except FileNotFoundError:
            if master_fd is not None:
                os.close(master_fd)
                master_fd = None
            if slave_fd is not None:
                os.close(slave_fd)
                slave_fd = None
            with print_lock:
                tqdm.write(f"{pid}: get_iplayer command not found")
            _debug_log(f"{pid}: get_iplayer command not found when launching")
            with results_lock:
                results[pid] = 127
            return
        except OSError as exc:
            if master_fd is not None:
                os.close(master_fd)
                master_fd = None
            if slave_fd is not None:
                os.close(slave_fd)
                slave_fd = None
            with print_lock:
                tqdm.write(f"{pid}: failed to start get_iplayer ({exc})")
            _debug_log(f"{pid}: failed to start get_iplayer ({exc})")
            with results_lock:
                results[pid] = 1
            return

        if using_pty and slave_fd is not None:
            os.close(slave_fd)
            slave_fd = None
        elif not using_pty:
            assert process is not None
            stdout_pipe = process.stdout
            if stdout_pipe is None:
                raise RuntimeError("process stdout pipe not available on Windows")
            output_queue = Queue()

            def _drain_stdout(pipe: BinaryIO, queue: Queue[bytes]) -> None:
                try:
                    while True:
                        chunk = pipe.read(1024)
                        if not chunk:
                            break
                        queue.put(chunk)
                except Exception:
                    pass
                finally:
                    queue.put(b"")

            reader_thread = threading.Thread(
                target=_drain_stdout, args=(stdout_pipe, output_queue), daemon=True
            )
            reader_thread.start()

        colour = _get_colour(pid, colour)
        _start_pseudo_stream(pid, "waiting", colour)
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        last_partial = ""
        try:
            while True:
                if using_pty:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if not ready:
                        if process.poll() is not None:
                            break
                        _tick_pseudo_stream(pid, "waiting", colour)
                        _tick_pseudo_stream(pid, "converting", colour)
                        continue
                    try:
                        raw = os.read(master_fd, 1024)
                    except BlockingIOError:
                        continue
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                else:
                    assert output_queue is not None
                    try:
                        raw = output_queue.get(timeout=0.1)
                    except Empty:
                        if process.poll() is not None:
                            break
                        _tick_pseudo_stream(pid, "waiting", colour)
                        _tick_pseudo_stream(pid, "converting", colour)
                        continue
                if not raw:
                    if process.poll() is not None:
                        break
                    continue
                text = decoder.decode(raw) if raw else ""
                if not text:
                    continue
                buffer += text
                _tick_pseudo_stream(pid, "waiting", colour)
                _tick_pseudo_stream(pid, "converting", colour)
                saw_carriage = False
                while True:
                    delimiter_index = _next_delimiter(buffer)
                    if delimiter_index is None:
                        break
                    delimiter_char = buffer[delimiter_index]
                    line = buffer[:delimiter_index]
                    remainder = buffer[delimiter_index + 1 :]
                    if delimiter_char == "\r" and remainder.startswith("\n"):
                        remainder = remainder[1:]
                    buffer = remainder
                    _emit_line(pid, colour, line)
                    last_partial = ""
                    if delimiter_char == "\r":
                        saw_carriage = True
                if saw_carriage and buffer and buffer != last_partial:
                    _emit_line(pid, colour, buffer)
                    last_partial = buffer
        finally:
            if master_fd is not None and using_pty:
                os.close(master_fd)
                master_fd = None

        if decoder is not None:
            buffer += decoder.decode(b"", final=True)
        while True:
            delimiter_index = _next_delimiter(buffer)
            if delimiter_index is None:
                break
            delimiter_char = buffer[delimiter_index]
            line = buffer[:delimiter_index]
            remainder = buffer[delimiter_index + 1 :]
            if delimiter_char == "\r" and remainder.startswith("\n"):
                remainder = remainder[1:]
            buffer = remainder
            _emit_line(pid, colour, line)
            last_partial = ""
        if buffer:
            _emit_line(pid, colour, buffer)

        return_code = process.wait()
        _debug_log(f"{pid}: get_iplayer exited with code {return_code}")
        with results_lock:
            results[pid] = return_code
        with PROGRESS_LOCK:
            for key in [item for item in PROGRESS_BARS if item[0] == pid]:
                bar = PROGRESS_BARS[key]
                if bar.n < bar.total:
                    bar.n = bar.total
                COMPLETED_BARS.add(key)
                STREAM_STATE[key] = (None, None, True)
                bar.set_description_str(
                    _compose_desc(key[0], key[1], bar.n, None, None, completed=True),
                    refresh=False,
                )
                bar.refresh()
            _reassign_positions_locked()
        _complete_pseudo_stream(pid, "waiting", colour)
        _complete_pseudo_stream(pid, "converting", colour)

        download_dir = _locate_download_directory(token, pid)
        _debug_log(f"{pid}: located download directory {download_dir}")
        if download_dir and download_dir.exists():
            if return_code == 0:
                video_path = _find_downloaded_video(download_dir)
                if video_path is None:
                    with print_lock:
                        tqdm.write(f"{pid}: no video file found in download directory")
                    with results_lock:
                        results[pid] = 1
                else:
                    moved_video = _move_video_to_root(video_path, print_lock)
                    if moved_video is None:
                        with results_lock:
                            results[pid] = 1
        elif return_code == 0:
            with print_lock:
                tqdm.write(f"{pid}: download directory not found")
            with results_lock:
                results[pid] = 1

        if plex_mode and return_code == 0 and moved_video:
            renamed = _rename_video_for_plex(pid, moved_video, print_lock)
            if renamed is None:
                with results_lock:
                    results[pid] = 1
    finally:
        if reader_thread is not None:
            reader_thread.join()
        if process is not None and process.stdout:
            try:
                process.stdout.close()
            except Exception:
                pass
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        _cleanup_download_dir()


def _cycle_colors() -> Iterable[ColourStyle]:
    return itertools.cycle(COLOUR_STYLES if COLOUR_STYLES else (ColourStyle("white"),))

def _safe_int_to_str(n: Optional[int]) -> str:
    if isinstance(n, int):
        return str(n)
    if isinstance(n, str) and n.isdigit():
        return n
    return ""

def _extract_broadcast_date(node: dict) -> str:
    """Return YYYYMMDD from first_broadcast_date if available."""
    date_str = node.get("first_broadcast_date")
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y%m%d")
    except Exception:
        return ""

def bbc_metadata_from_pid(pid: str, timeout: int = 10) -> Dict[str, str]:
    """
    Returns:
    {
      "show_title": str,        # brand title
      "season_number": str,     # series number or "0" for specials
      "episode_number": str,    # episode position or broadcast date YYYYMMDD
      "episode_title": str      # episode title
    }
    """
    SPECIALS_REGEX = re.compile(r"\bspecials?\b", re.IGNORECASE)

    url = f"https://www.bbc.co.uk/programmes/{pid}.json"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    prog = data.get("programme") or {}

    episode_title = str(prog.get("title") or "")
    episode_pos = prog.get("position") if prog.get("type") == "episode" else None
    display_subtitle = ""
    if isinstance(prog.get("display_title"), dict):
        display_subtitle = str(prog["display_title"].get("subtitle") or "")

    # Walk parents to find series + brand
    node = prog
    brand_title = ""
    series_title = ""
    series_pos = None
    ancestor_titles = []

    while isinstance(node, dict) and isinstance(node.get("parent"), dict):
        parent_prog = node["parent"].get("programme")
        if not isinstance(parent_prog, dict):
            break

        ptype = parent_prog.get("type")
        ptitle = parent_prog.get("title")
        if isinstance(ptitle, str) and ptitle:
            ancestor_titles.append(ptitle)

        if ptype == "series":
            if series_pos is None:
                series_pos = parent_prog.get("position")
            if not series_title:
                series_title = str(parent_prog.get("title") or "")
        elif ptype == "brand":
            if not brand_title:
                brand_title = str(parent_prog.get("title") or "")
            break

        node = parent_prog

    if not brand_title and prog.get("type") == "brand":
        brand_title = str(prog.get("title") or "")

    # Determine season number
    season_str = _safe_int_to_str(series_pos)
    if not season_str:
        specials_hints = []
        if series_title:
            specials_hints.append(series_title)
        if display_subtitle:
            specials_hints.append(display_subtitle.split(",", 1)[0].strip())
        specials_hints.extend(t for t in ancestor_titles if isinstance(t, str))
        if any(SPECIALS_REGEX.search(t or "") for t in specials_hints):
            season_str = "0"

    # Episode number or fallback date
    ep_str = _safe_int_to_str(episode_pos)
    if not ep_str:
        ep_str = _extract_broadcast_date(prog)

    return {
        "show_title": str(brand_title or ""),
        "season_number": season_str,
        "episode_number": ep_str,
        "episode_title": episode_title,
    }

def get_bbc_episode_pids(pid: str, timeout: int = 120) -> list[str]:
    """
    Return a clean list of BBC episode PIDs for a brand, series, or episode PID,
    using get_iplayer's recursive listing feature.
    """
    base_command = list(_get_iplayer_invocation())
    cmd = [
        *base_command,
        f"--pid={pid}",
        "--pid-recursive-list",
    ]

    _debug_log(f"Expanding PID {pid} with command: {_format_command(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    _debug_log(f"PID expansion command exited with code {result.returncode}")
    if result.stdout:
        _debug_log(f"PID expansion stdout:\n{result.stdout.strip() or '<empty>'}")
    if result.stderr:
        _debug_log(f"PID expansion stderr:\n{result.stderr.strip() or '<empty>'}")

    pid_pattern = re.compile(r"\b[a-z][a-z0-9]{7,10}\b")

    pids = []
    collecting = False
    for line in result.stdout.splitlines():
        line = line.strip()

        if not collecting:
            if line.startswith("Episodes:"):
                collecting = True
            continue

        if not line or line.startswith("INFO:"):
            continue

        match = pid_pattern.search(line)
        if match:
            pids.append(match.group(0))

    _debug_log(f"PID expansion result for {pid}: {pids or '[no matches]'}")

    return pids

def main(argv: Sequence[str] | None = None) -> int:
    _reset_progress_state()
    parser = build_parser()
    args = parser.parse_args(argv)
    global DEBUG_ENABLED
    DEBUG_ENABLED = bool(getattr(args, "debug", False))
    if DEBUG_ENABLED:
        tqdm.write("[debug] Debug logging enabled")

    normalised_pids = [_normalise_pid(pid) for pid in args.pids]
    expanded_pids = _expand_pids(normalised_pids)

    for pid in expanded_pids:
        if pid not in PID_LABELS:
            PID_LABELS[pid] = _build_program_label(pid)

    max_threads = max(1, args.threads)
    color_iter = _cycle_colors()

    results: Dict[str, int] = {}
    print_lock = threading.Lock()
    results_lock = threading.Lock()
    cursor_hidden = False
    interrupted = False
    executor: ThreadPoolExecutor | None = None
    shutdown_called = False
    summary_lines: list[str] = []
    failures: Dict[str, int] = {}
    clean_temp = not args.no_clean
    try:
        if sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
            cursor_hidden = True

        executor = ThreadPoolExecutor(max_workers=max_threads)
        futures = [
            executor.submit(
                _run_get_iplayer,
                pid,
                next(color_iter),
                args.plex,
                clean_temp,
                results,
                print_lock,
                results_lock,
            )
            for pid in expanded_pids
        ]

        try:
            for future in futures:
                future.result()
        except KeyboardInterrupt:
            interrupted = True
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            shutdown_called = True
        else:
            executor.shutdown(wait=True)
            shutdown_called = True
    except KeyboardInterrupt:
        interrupted = True
        if executor and not shutdown_called:
            executor.shutdown(wait=True, cancel_futures=True)
            shutdown_called = True
    finally:
        if executor and not shutdown_called:
            executor.shutdown(wait=True, cancel_futures=True)
            shutdown_called = True
        summary_lines = _finalize_bars()
        failures = {pid: code for pid, code in results.items() if code != 0}

        if summary_lines:
            sys.stdout.write("\r")
            sys.stdout.flush()
        for line in summary_lines:
            print(line)

        if cursor_hidden and sys.stdout.isatty():
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

    if failures and not interrupted:
        for pid, code in failures.items():
            tqdm.write(f"{pid}: download failed with exit code {code}")
        return 1
    if interrupted:
        tqdm.write("Downloads interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
