"""Command-line interface for the pget_iplayer project."""

from __future__ import annotations

import argparse
import codecs
import errno
import itertools
import os
import re
import select
import subprocess
import threading
import platform
import shutil
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple

from tqdm import tqdm

from . import __version__

PROGRESS_LINE = re.compile(
    r"^\s*(?P<percent>\d+(?:\.\d+)?)%.*?@\s*(?P<speed>.*?)\s+ETA:\s*(?P<eta>\S+).*?\[(?P<stream>[^\]]+)\]\s*$",
    re.IGNORECASE,
)
COMPLETED_LINE = re.compile(
    r"INFO:\s+Downloaded:.*?@\s*(?P<speed>.*?)\s*\([^)]*\)\s*\[(?P<stream>[^\]]+)\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ColourStyle:
    tqdm_name: str


COLOUR_STYLES: Tuple[ColourStyle, ...] = (
    ColourStyle("red"),
    ColourStyle("green"),
    ColourStyle("yellow"),
    ColourStyle("blue"),
    ColourStyle("magenta"),
    ColourStyle("cyan"),
)

PROGRESS_LOCK = threading.Lock()
PROGRESS_BARS: Dict[tuple[str, str], tqdm] = {}
PID_COLOUR: Dict[str, ColourStyle] = {}
COMPLETED_BARS: set[tuple[str, str]] = set()
STREAM_STATE: Dict[tuple[str, str], tuple[str | None, str | None, bool]] = {}

DEFAULT_SPEED = "--.- Mb/s"
DEFAULT_ETA = "--:--:--"
ETA_FIELD_WIDTH = 8
SPEED_FIELD_WIDTH = 10
META_WIDTH = 5 + ETA_FIELD_WIDTH + 2 + SPEED_FIELD_WIDTH + 2  # "(ETA " + eta + ", " + speed + ") "


REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    "get_iplayer": ("get_iplayer",),
    "AtomicParsley": ("AtomicParsley", "atomicparsley"),
    "ffmpeg": ("ffmpeg",),
}

INSTALL_HINTS: dict[str, dict[str, str]] = {
    "Linux": {
        "get_iplayer": "sudo apt install get-iplayer",
        "AtomicParsley": "sudo apt install atomicparsley",
        "ffmpeg": "sudo apt install ffmpeg",
    },
    "Darwin": {
        "get_iplayer": "brew install get_iplayer",
        "AtomicParsley": "brew install atomicparsley",
        "ffmpeg": "brew install ffmpeg",
    },
    "Windows": {
        "get_iplayer": "choco install get-iplayer",
        "AtomicParsley": "choco install atomicparsley",
        "ffmpeg": "choco install ffmpeg",
    },
}


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
    return [
        "get_iplayer",
        "--get",
        "--subtitles",
        "--subs-embed",
        "--force",
        "--overwrite",
        "--tv-quality=fhd,hd,sd",
        f"--pid={pid}",
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


def _sorted_keys() -> list[tuple[str, str]]:
    return sorted(
        PROGRESS_BARS.keys(),
        key=lambda item: (item[0], item[1]),
    )


def _format_label(pid: str, stream: str, width: int = 14) -> str:
    if len(stream) <= width:
        stream_part = stream.ljust(width)
    else:
        stream_part = stream[: width - 1] + "…"
    return f"{pid} {stream_part}: "


def _format_percent(value: float) -> str:
    return f"{value:6.1f}% "


def _format_meta(speed: str | None, eta: str | None, completed: bool = False) -> str:
    if completed:
        return "(completed)".ljust(META_WIDTH)
    eta_val = (eta or DEFAULT_ETA)[:ETA_FIELD_WIDTH].ljust(ETA_FIELD_WIDTH)
    speed_val = (speed or DEFAULT_SPEED)[:SPEED_FIELD_WIDTH].rjust(SPEED_FIELD_WIDTH)
    return f"(ETA {eta_val}, {speed_val}) ".ljust(META_WIDTH)


def _compose_desc(
    pid: str,
    stream: str,
    percent: float,
    speed: str | None,
    eta: str | None,
    completed: bool = False,
) -> str:
    percent_display = 100.0 if completed else percent
    return (
        f"{_format_label(pid, stream)}"
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


def _missing_tools() -> list[str]:
    missing = []
    for tool, candidates in REQUIRED_TOOLS.items():
        if not any(shutil.which(candidate) for candidate in candidates):
            missing.append(tool)
    return missing


def _print_tool_help(missing: Iterable[str]) -> None:
    system = platform.system()
    hints = INSTALL_HINTS.get(system, {})
    print("The following required tools were not found in PATH:")
    for tool in missing:
        hint = hints.get(tool)
        if hint:
            print(f"  - {tool}: {hint}")
        else:
            print(f"  - {tool}: install {tool} and ensure it is available in PATH")
    if system not in INSTALL_HINTS:
        print(f"Detected platform '{system}' has no specific guidance; please install the tools manually.")


def main(argv: Sequence[str] | None = None) -> int:
    _reset_progress_state()
    parser = build_parser()
    args = parser.parse_args(argv)

    missing = _missing_tools()
    if missing:
        _print_tool_help(missing)
        return 1

    threads = []
    results: Dict[str, int] = {}
    print_lock = threading.Lock()
    results_lock = threading.Lock()
    for pid, color in zip(args.pids, _cycle_colors()):
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

    with PROGRESS_LOCK:
        _reassign_positions_locked()

    failures = {pid: code for pid, code in results.items() if code != 0}
    if failures:
        for pid, code in failures.items():
            with print_lock:
                tqdm.write(f"{pid}: download failed with exit code {code}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
