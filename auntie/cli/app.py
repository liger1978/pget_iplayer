"""CLI application entry point orchestration."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from tqdm import tqdm

from .arguments import build_parser
from .colours import cycle_colours
from .debug import DEBUG_ENABLED, set_debug
from .downloader import DownloadRunner
from .expansion import expand_pids
from .metadata import build_program_label
from .pids import normalise_pid
from .progress import ProgressTracker


def main(argv: Sequence[str] | None = None) -> int:
    progress = ProgressTracker()
    progress.reset()

    parser = build_parser()
    args = parser.parse_args(argv)

    set_debug(bool(getattr(args, "debug", False)))
    if DEBUG_ENABLED:
        tqdm.write("[debug] Debug logging enabled")

    normalised_pids = [normalise_pid(pid) for pid in args.pids]
    expanded_pids = expand_pids(normalised_pids)

    for pid in expanded_pids:
        progress.register_label(pid, build_program_label(pid))

    max_threads = max(1, args.threads)
    colour_iter = cycle_colours()

    results: dict[str, int] = {}
    print_lock = threading.Lock()
    cursor_hidden = False
    interrupted = False
    summary_lines: list[str] = []
    failures: dict[str, int] = {}
    clean_temp = not args.no_clean
    executor: ThreadPoolExecutor | None = None

    runner = DownloadRunner(
        progress,
        plex_mode=bool(args.plex),
        clean_temp=clean_temp,
        print_lock=print_lock,
    )

    try:
        if sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
            cursor_hidden = True

        executor = ThreadPoolExecutor(max_workers=max_threads)
        futures = {
            executor.submit(runner.run, pid, next(colour_iter)): pid for pid in expanded_pids
        }

        try:
            for future, pid in futures.items():
                results[pid] = future.result()
        except KeyboardInterrupt:
            interrupted = True
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            executor = None
            raise
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
                executor = None
    except KeyboardInterrupt:
        interrupted = True
    finally:
        summary_lines = progress.finalise()
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


__all__ = ["main"]
