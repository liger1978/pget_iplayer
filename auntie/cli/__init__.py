"""CLI package for the auntie application."""

from __future__ import annotations

import requests

from .app import main
from .arguments import build_parser
from .colours import ColourStyle, RESET, COLOUR_STYLES, cycle_colours
from .debug import DEBUG_ENABLED, debug_log, set_debug
from .downloader import DownloadRunner
from .expansion import expand_pids
from .filesystem import (
    cleanup_download_directories,
    ensure_unique_path,
    find_downloaded_video,
    locate_download_directory,
    move_video_to_root,
    rename_video_for_plex,
)
from .iplayer import build_download_command, get_iplayer_invocation, resolve_get_iplayer_entrypoint
from .metadata import (
    PROGRAM_LABEL_WIDTH,
    PID_METADATA,
    bbc_metadata_from_pid,
    build_program_label,
    format_plex_filename,
    get_bbc_episode_pids,
    get_cached_metadata,
    _get_bbc_episode_pids_via_get_iplayer,
)
from .pids import (
    BBC_IPLAYER_SERIES_BRAND_PREFIX,
    BBC_IPLAYER_SINGLE_EPISODE_PREFIX,
    PID_PATTERN,
    normalise_pid,
)
from .progress import ProgressTracker
from .utils import (
    dedupe_preserve_order,
    extract_broadcast_date,
    format_command,
    next_delimiter,
    sanitize_filename_component,
    safe_int_to_str,
    truncate_title,
    two_digit,
)

# Backwards compatibility aliases -----------------------------------------------------------
_normalise_pid = normalise_pid
_two_digit = two_digit
_sanitize_filename_component = sanitize_filename_component
_format_plex_filename = format_plex_filename
_ensure_unique_path = ensure_unique_path
_locate_download_directory = locate_download_directory
_find_downloaded_video = find_downloaded_video
_extract_broadcast_date = extract_broadcast_date
_safe_int_to_str = safe_int_to_str

# re-export requests so tests and external callers can patch/inspect it as before
requests = requests

__all__ = [
    "BBC_IPLAYER_SERIES_BRAND_PREFIX",
    "BBC_IPLAYER_SINGLE_EPISODE_PREFIX",
    "COLOUR_STYLES",
    "ColourStyle",
    "DEBUG_ENABLED",
    "DownloadRunner",
    "PID_METADATA",
    "PID_PATTERN",
    "PROGRAM_LABEL_WIDTH",
    "ProgressTracker",
    "RESET",
    "bbc_metadata_from_pid",
    "build_download_command",
    "build_parser",
    "build_program_label",
    "cleanup_download_directories",
    "cycle_colours",
    "debug_log",
    "dedupe_preserve_order",
    "ensure_unique_path",
    "expand_pids",
    "extract_broadcast_date",
    "find_downloaded_video",
    "format_command",
    "format_plex_filename",
    "get_bbc_episode_pids",
    "get_cached_metadata",
    "get_iplayer_invocation",
    "locate_download_directory",
    "main",
    "move_video_to_root",
    "normalise_pid",
    "next_delimiter",
    "rename_video_for_plex",
    "resolve_get_iplayer_entrypoint",
    "requests",
    "sanitize_filename_component",
    "safe_int_to_str",
    "set_debug",
    "truncate_title",
    "two_digit",
    "_extract_broadcast_date",
    "_find_downloaded_video",
    "_format_plex_filename",
    "_get_bbc_episode_pids_via_get_iplayer",
    "_locate_download_directory",
    "_normalise_pid",
    "_safe_int_to_str",
    "_sanitize_filename_component",
    "_two_digit",
]
