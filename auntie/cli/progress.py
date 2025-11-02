"""Progress bar management for concurrent downloads."""

from __future__ import annotations

import re
import threading
import time
from typing import Dict

from tqdm import tqdm

from .colours import RESET, ColourStyle
from .metadata import PROGRAM_LABEL_WIDTH

PROGRESS_LINE = re.compile(
    r"^\s*(?P<percent>\d+(?:\.\d+)?)%.*?@\s*(?P<speed>.*?)\s+ETA:\s*(?P<eta>\S+).*?\[(?P<stream>[^\]]+)\]\s*$",
    re.IGNORECASE,
)
COMPLETED_LINE = re.compile(
    r"INFO:\s+Downloaded:.*?@\s*(?P<speed>.*?)\s*\([^)]*\)\s*\[(?P<stream>[^\]]+)\]",
    re.IGNORECASE,
)

STREAM_PRIORITY = ("waiting", "audio", "audio+video", "video", "converting")

DEFAULT_SPEED = "--.- Mb/s"
DEFAULT_ETA = "--:--:--"
ETA_FIELD_WIDTH = 8
SPEED_FIELD_WIDTH = 10
META_WIDTH = 5 + ETA_FIELD_WIDTH + 2 + SPEED_FIELD_WIDTH + 1
STREAM_FIELD_WIDTH = 12
PSEUDO_STREAMS = {"waiting", "converting"}
PERCENT_WIDTH = 8


class ProgressTracker:
    """Thread-safe progress bar management."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: Dict[tuple[str, str], tqdm] = {}
        self._pid_colours: Dict[str, ColourStyle] = {}
        self._completed_bars: set[tuple[str, str]] = set()
        self._stream_state: Dict[tuple[str, str], tuple[str | None, str | None, bool]] = {}
        self._pid_labels: Dict[str, str] = {}
        self._pseudo_timers: Dict[tuple[str, str], Dict[str, float]] = {}

    def reset(self) -> None:
        with self._lock:
            for bar in self._bars.values():
                try:
                    bar.close()
                except Exception:
                    pass
            self._bars.clear()
            self._pid_colours.clear()
            self._completed_bars.clear()
            self._stream_state.clear()
            self._pseudo_timers.clear()
            self._pid_labels.clear()

    def register_label(self, pid: str, label: str) -> None:
        self._pid_labels[pid] = label

    def colour_for_pid(self, pid: str, default: ColourStyle) -> ColourStyle:
        with self._lock:
            return self._pid_colours.setdefault(pid, default)

    def emit_progress_line(self, pid: str, default_colour: ColourStyle, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return

        lower = stripped.lower()
        colour = self.colour_for_pid(pid, default_colour)
        if "converting" in lower or "tagging" in lower:
            self.start_pseudo_stream(pid, "converting", colour)

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
            if stream not in PSEUDO_STREAMS:
                self.complete_pseudo_stream(pid, "waiting", colour)
        self.update_stream(pid, stream, percent, colour, speed, eta)

    def start_pseudo_stream(self, pid: str, stream: str, colour: ColourStyle) -> None:
        key = (pid, stream)
        now = time.perf_counter()
        if key in self._pseudo_timers:
            return
        self._pseudo_timers[key] = {"start": now, "last": 0.0}
        self.update_stream(pid, stream, 1.0, colour, None, None)

    def tick_pseudo_stream(self, pid: str, stream: str, colour: ColourStyle) -> None:
        key = (pid, stream)
        timer = self._pseudo_timers.get(key)
        if not timer:
            return
        now = time.perf_counter()
        elapsed = now - timer["start"]
        percent = min(99.0, (elapsed / 300.0) * 100.0)
        self.update_stream(pid, stream, percent, colour, None, None)

    def complete_pseudo_stream(self, pid: str, stream: str, colour: ColourStyle) -> None:
        key = (pid, stream)
        if key not in self._pseudo_timers:
            return
        self._pseudo_timers.pop(key, None)
        self.update_stream(pid, stream, 100.0, colour, None, "00:00:00")

    def mark_pid_complete(self, pid: str) -> None:
        with self._lock:
            for key, bar in list(self._bars.items()):
                if key[0] != pid:
                    continue
                if bar.n < bar.total:
                    bar.n = bar.total
                self._completed_bars.add(key)
                self._stream_state[key] = (None, None, True)
                bar.set_description_str(
                    self._compose_desc(pid, key[1], bar.n, None, None, completed=True),
                    refresh=False,
                )
                bar.refresh()
            self._reassign_positions_locked()

    def update_stream(
        self,
        pid: str,
        stream: str,
        percent: float,
        colour: ColourStyle,
        speed: str | None,
        eta: str | None,
    ) -> None:
        key = (pid, stream)
        if key in self._completed_bars:
            return
        colour = self.colour_for_pid(pid, colour)
        bar = self._get_progress_bar(pid, stream, colour)
        if bar is None:
            return
        with self._lock:
            clamped_percent = max(0.0, min(100.0, percent))
            increment = clamped_percent - bar.n
            if increment < 0:
                bar.reset(total=100.0)
                bar.n = 0.0
                increment = clamped_percent
            bar.update(increment)
            is_complete_marker = clamped_percent >= 100.0 or eta == "00:00:00"
            bar.set_description_str(
                self._compose_desc(
                    pid,
                    stream,
                    clamped_percent,
                    speed if speed else None,
                    eta if eta else None,
                    completed=is_complete_marker,
                ),
                refresh=False,
            )
            self._stream_state[key] = (
                speed if speed else None,
                eta if eta else None,
                is_complete_marker,
            )
            if is_complete_marker:
                self._completed_bars.add(key)
                bar.n = bar.total
                self._reassign_positions_locked()
            bar.refresh()

    def finalise(self) -> list[str]:
        with self._lock:
            self._reassign_positions_locked()
            keys = self._sorted_keys()
            bars = [self._bars[key] for key in keys]
            lines: list[str] = []
            for key, bar in zip(keys, bars):
                pid, stream = key
                colour_style = self._pid_colours.get(pid)
                stream_state = self._stream_state.get(key, (None, None, False))
                speed, eta, completed = stream_state
                percent = 100.0 if completed else bar.n
                desc = self._compose_desc(pid, stream, percent, speed, eta, completed)
                if completed:
                    desc = desc.rstrip() + " "
                format_dict = bar.format_dict
                bar_segment = format_dict.get("bar")
                if not bar_segment:
                    try:
                        bar_segment = tqdm.format_meter(
                            format_dict["n"],
                            format_dict["total"],
                            format_dict["elapsed"],
                            ncols=format_dict["ncols"],
                            prefix="",
                            ascii=format_dict["ascii"],
                            unit=format_dict["unit"],
                            unit_scale=format_dict["unit_scale"],
                            rate=format_dict["rate"],
                            bar_format="{bar}",
                            postfix=format_dict["postfix"],
                            unit_divisor=format_dict["unit_divisor"],
                            initial=format_dict["initial"],
                            colour=None,
                        )
                    except Exception:
                        bar_segment = ""
                if bar_segment is None:
                    bar_segment = ""
                if colour_style:
                    bar_segment = f"{colour_style.ansi_code}{bar_segment}{RESET}"
                lines.append(f"{desc}|{bar_segment}|")
            for bar in bars:
                bar.leave = False
            self._bars.clear()
            self._pid_colours.clear()
            self._completed_bars.clear()
            self._stream_state.clear()
        for bar in bars:
            bar.close()
        return lines

    # Internal helpers -------------------------------------------------

    def _get_progress_bar(self, pid: str, stream: str, colour: ColourStyle) -> tqdm:
        key = (pid, stream)
        with self._lock:
            bar = self._bars.get(key)
            if bar is None:
                desc = self._compose_desc(pid, stream, 0.0, None, None)
                bar = tqdm(
                    total=100.0,
                    desc=desc,
                    position=len(self._bars),
                    leave=True,
                    dynamic_ncols=True,
                    colour=colour.tqdm_name,
                    smoothing=0.0,
                )
                bar.bar_format = "{desc}|{bar}|"
                self._bars[key] = bar
                self._stream_state[key] = (None, None, False)
                self._reassign_positions_locked()
            return bar

    def _sorted_keys(self) -> list[tuple[str, str]]:
        return sorted(
            self._bars.keys(),
            key=lambda item: (item[0], self._stream_sort_key(item[1])),
        )

    def _stream_sort_key(self, stream: str) -> tuple[int, int, str]:
        for index, name in enumerate(STREAM_PRIORITY):
            if stream.startswith(name):
                return (0, index, stream)
        return (1, 0, stream)

    def _program_label(self, pid: str) -> str:
        label = self._pid_labels.get(pid)
        if label:
            return label
        fallback = f"{pid}: "
        if len(fallback) < PROGRAM_LABEL_WIDTH:
            fallback = fallback.ljust(PROGRAM_LABEL_WIDTH)
        else:
            fallback = fallback[:PROGRAM_LABEL_WIDTH]
        return fallback

    def _format_percent(self, value: float) -> str:
        return f"{value:6.1f}% "

    def _format_meta(self, speed: str | None, eta: str | None, completed: bool = False) -> str:
        if completed:
            return "(completed)".ljust(META_WIDTH)
        eta_val = (eta or DEFAULT_ETA)[:ETA_FIELD_WIDTH].ljust(ETA_FIELD_WIDTH)
        speed_val = (speed or DEFAULT_SPEED)[:SPEED_FIELD_WIDTH].rjust(SPEED_FIELD_WIDTH)
        return f"(ETA {eta_val}, {speed_val})".ljust(META_WIDTH)

    def _compose_desc(
        self,
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
            percent_part = self._format_percent(percent_display)
            meta_part = self._format_meta(speed, eta, completed)
            if not completed:
                percent_part = " " * PERCENT_WIDTH
                meta_part = " " * META_WIDTH
        else:
            percent_part = self._format_percent(percent_display)
            meta_part = self._format_meta(speed, eta, completed)
        return f"{self._program_label(pid)} {stream_display}{percent_part}{meta_part}"

    def _reassign_positions_locked(self) -> None:
        for position, key in enumerate(self._sorted_keys()):
            bar = self._bars[key]
            pid, stream = key
            speed, eta, completed = self._stream_state.get(
                key, (None, None, key in self._completed_bars)
            )
            bar.set_description_str(
                self._compose_desc(pid, stream, bar.n, speed, eta, completed),
                refresh=False,
            )
            if bar.pos != position:
                bar.pos = position
                bar.refresh()


__all__ = ["ProgressTracker"]
