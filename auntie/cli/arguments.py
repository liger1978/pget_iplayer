"""Argument parsing for the auntie CLI."""

from __future__ import annotations

import argparse
import os

from .. import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="auntie",
        description=("download multiple BBC iPlayer programmes in parallel."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "pids",
        metavar="PID",
        nargs="+",
        help="one or more BBC programme, series (season) or brand (show) PIDs or URLs to download.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="enable verbose debug logging of get_iplayer interactions",
    )
    parser.add_argument(
        "-n",
        "--no-clean",
        action="store_true",
        help="preserve the temporary download subdirectory instead of deleting it",
    )
    parser.add_argument(
        "-p",
        "--plex",
        action="store_true",
        help="rename completed video files to Plex naming convention",
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=os.cpu_count() or 4,
        help="maximum number of parallel download workers",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="display the installed version and exit",
    )
    return parser


__all__ = ["build_parser"]
