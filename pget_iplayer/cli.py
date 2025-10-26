"""Command-line interface for the pget_iplayer project."""

from __future__ import annotations

import argparse
import codecs
import errno
import itertools
import os
import re
import requests
import select
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional, Sequence, Tuple

from tqdm import tqdm

from . import __version__

PID_PATTERN = re.compile(r"([a-z0-9]{8})", re.IGNORECASE)


PROGRESS_LINE = re.compile(
    r"^\s*(?P<percent>\d+(?:\.\d+)?)%.*?@\s*(?P<speed>.*?)\s+ETA:\s*(?P<eta>\S+).*?\[(?P<stream>[^\]]+)\]\s*$",
    re.IGNORECASE,
)
COMPLETED_LINE = re.compile(
    r"INFO:\s+Downloaded:.*?@\s*(?P<speed>.*?)\s*\([^)]*\)\s*\[(?P<stream>[^\]]+)\]",
    re.IGNORECASE,
)


RESET = "\033[0m"


@dataclass(frozen=True)
class ColourStyle:
    tqdm_name: str
    ansi_code: str


COLOUR_STYLES: Tuple[ColourStyle, ...] = (
    ColourStyle("red", "\033[91m"),
    ColourStyle("green", "\033[92m"),
    ColourStyle("yellow", "\033[93m"),
    ColourStyle("blue", "\033[94m"),
    ColourStyle("magenta", "\033[95m"),
    ColourStyle("cyan", "\033[96m"),
)

STREAM_PRIORITY = ("audio", "audio+video", "video")

PROGRESS_LOCK = threading.Lock()
PROGRESS_BARS: Dict[tuple[str, str], tqdm] = {}
PID_COLOUR: Dict[str, ColourStyle] = {}
COMPLETED_BARS: set[tuple[str, str]] = set()
STREAM_STATE: Dict[tuple[str, str], tuple[str | None, str | None, bool]] = {}
PID_LABELS: Dict[str, str] = {}

DEFAULT_SPEED = "--.- Mb/s"
DEFAULT_ETA = "--:--:--"
ETA_FIELD_WIDTH = 8
SPEED_FIELD_WIDTH = 10
META_WIDTH = 5 + ETA_FIELD_WIDTH + 2 + SPEED_FIELD_WIDTH + 1  # "(ETA " + eta + ", " + speed + ")"
PROGRAM_LABEL_WIDTH = 42
STREAM_FIELD_WIDTH = 12


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
        PID_LABELS.clear()


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="pget-iplayer",
        description=(
            "Parallel wrapper around get_iplayer for downloading multiple pids concurrently."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Display the installed version and exit.",
    )
    parser.add_argument(
        "pids",
        metavar="PID",
        nargs="+",
        help="One or more BBC programme pids or URLs to download.",
    )
    return parser


def _command_for_pid(pid: str) -> Sequence[str]:
    normalised = _normalise_pid(pid)
    return [
        "get_iplayer",
        "--get",
        "--subtitles",
        "--subs-embed",
        "--force",
        "--overwrite",
        "--tv-quality=fhd,hd,sd",
        f"--pid={normalised}",
    ]


def _emit_line(pid: str, colour: ColourStyle, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return
    match = PROGRESS_LINE.match(stripped)
    if not match:
        complete_match = COMPLETED_LINE.search(stripped)
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
    match = PID_PATTERN.search(value)
    if match:
        return match.group(1).lower()
    return value.strip().lower()


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


def _build_program_label(pid: str) -> str:
    try:
        metadata = bbc_metadata_from_pid(pid)
    except Exception:
        metadata = {}

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
    return (
        f"{_program_label(pid)} {stream_display}"
        f"{_format_percent(percent_display)}"
        f"{_format_meta(speed, eta, completed)}"
    )


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
    results: Dict[str, int],
    print_lock: threading.Lock,
    results_lock: threading.Lock,
) -> None:
    command = _command_for_pid(pid)
    try:
        master_fd, slave_fd = os.openpty()
    except OSError as exc:
        with print_lock:
            tqdm.write(f"{pid}: unable to allocate pty ({exc})")
        with results_lock:
            results[pid] = 1
        return

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            text=False,
            bufsize=0,
            close_fds=True,
        )
    except FileNotFoundError:
        os.close(master_fd)
        os.close(slave_fd)
        with print_lock:
            tqdm.write(f"{pid}: get_iplayer command not found")
        with results_lock:
            results[pid] = 127
        return
    except OSError as exc:
        os.close(master_fd)
        os.close(slave_fd)
        with print_lock:
            tqdm.write(f"{pid}: failed to start get_iplayer ({exc})")
        with results_lock:
            results[pid] = 1
        return

    os.close(slave_fd)
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    last_partial = ""
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            try:
                raw = os.read(master_fd, 1024)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not raw:
                if process.poll() is not None:
                    break
                continue
            text = decoder.decode(raw) if raw else ""
            if not text:
                continue
            buffer += text
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
        os.close(master_fd)

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

def main(argv: Sequence[str] | None = None) -> int:
    _reset_progress_state()
    parser = build_parser()
    args = parser.parse_args(argv)

    normalised_pids = [_normalise_pid(pid) for pid in args.pids]

    for pid in normalised_pids:
        if pid not in PID_LABELS:
            PID_LABELS[pid] = _build_program_label(pid)

    threads = []
    results: Dict[str, int] = {}
    print_lock = threading.Lock()
    results_lock = threading.Lock()
    cursor_hidden = False
    try:
        if sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
            cursor_hidden = True

        for pid, color in zip(normalised_pids, _cycle_colors()):
            thread = threading.Thread(
                target=_run_get_iplayer,
                name=f"get-iplayer-{pid}",
                args=(pid, color, results, print_lock, results_lock),
                daemon=True,
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        summary_lines = _finalize_bars()
        failures = {pid: code for pid, code in results.items() if code != 0}

        if summary_lines:
            sys.stdout.write("\r")
            sys.stdout.flush()
        for line in summary_lines:
            print(line)

        if failures:
            for pid, code in failures.items():
                tqdm.write(f"{pid}: download failed with exit code {code}")
            return 1
        return 0
    finally:
        if cursor_hidden and sys.stdout.isatty():
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
