"""BBC metadata helpers, caching, and PID expansion."""

from __future__ import annotations

import re
import subprocess
from typing import Dict, Iterable

import requests

from .debug import debug_log
from importlib import import_module

from .iplayer import get_iplayer_invocation
from .pids import PID_PATTERN, normalise_pid
from .utils import (
    dedupe_preserve_order,
    extract_broadcast_date,
    format_command,
    sanitize_filename_component,
    safe_int_to_str,
    truncate_title,
    two_digit,
)

PID_METADATA: Dict[str, Dict[str, str]] = {}

PROGRAM_LABEL_WIDTH = 42


def bbc_metadata_from_pid(pid: str, timeout: int = 10) -> Dict[str, str]:
    """
    Returns:
    {
      "show_title": str,
      "season_number": str,
      "episode_number": str,
      "episode_title": str
    }
    """

    specials_regex = re.compile(r"\bspecials?\b", re.IGNORECASE)

    url = f"https://www.bbc.co.uk/programmes/{pid}.json"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    prog = data.get("programme") or {}

    episode_title = str(prog.get("title") or "")
    episode_pos = prog.get("position") if prog.get("type") == "episode" else None
    display_subtitle = ""
    if isinstance(prog.get("display_title"), dict):
        display_subtitle = str(prog["display_title"].get("subtitle") or "")

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

    season_str = safe_int_to_str(series_pos)
    if not season_str:
        specials_hints = []
        if series_title:
            specials_hints.append(series_title)
        if display_subtitle:
            specials_hints.append(display_subtitle.split(",", 1)[0].strip())
        specials_hints.extend(t for t in ancestor_titles if isinstance(t, str))
        if any(specials_regex.search(t or "") for t in specials_hints):
            season_str = "0"

    ep_str = safe_int_to_str(episode_pos)
    if not ep_str:
        ep_str = extract_broadcast_date(prog)

    return {
        "show_title": str(brand_title or ""),
        "season_number": season_str,
        "episode_number": ep_str,
        "episode_title": episode_title,
    }


def get_cached_metadata(pid: str) -> Dict[str, str]:
    metadata = PID_METADATA.get(pid)
    if metadata is None:
        try:
            metadata = bbc_metadata_from_pid(pid)
        except Exception:
            metadata = {}
        PID_METADATA[pid] = metadata
    return metadata


def build_program_label(pid: str) -> str:
    metadata = get_cached_metadata(pid)
    show = truncate_title(metadata.get("show_title", ""))
    episode = truncate_title(metadata.get("episode_title", ""))
    season_number = two_digit(metadata.get("season_number"))
    episode_number = two_digit(metadata.get("episode_number"))

    base = f"{pid}: {show} - s{season_number}e{episode_number} - {episode}"
    if len(base) < PROGRAM_LABEL_WIDTH:
        base = base.ljust(PROGRAM_LABEL_WIDTH)
    elif len(base) > PROGRAM_LABEL_WIDTH:
        base = base[:PROGRAM_LABEL_WIDTH]
    return base


def format_plex_filename(metadata: Dict[str, str], pid: str, extension: str) -> str:
    show_name = sanitize_filename_component(metadata.get("show_title")) or pid.upper()
    episode_name = sanitize_filename_component(metadata.get("episode_title")) or pid.upper()
    season_number = two_digit(metadata.get("season_number"))
    episode_number = two_digit(metadata.get("episode_number"))

    extension = extension if extension.startswith(".") else f".{extension}"
    extension = extension or ".mp4"

    base = f"{show_name} - s{season_number}e{episode_number} - {episode_name}".strip()
    if not base:
        base = pid.upper()

    max_stem_len = max(1, 255 - len(extension))
    if len(base) > max_stem_len:
        base = base[:max_stem_len].rstrip()

    return f"{base}{extension}"


def fetch_programme_json(pid: str, timeout: int) -> dict:
    url = f"https://www.bbc.co.uk/programmes/{pid}.json"
    debug_log(f"{pid}: fetching programme JSON via {url}")
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("programme payload is not an object")
    return payload


def fetch_children_programmes(pid: str, timeout: int) -> list[dict]:
    programmes: list[dict] = []
    page = 1
    base_url = f"https://www.bbc.co.uk/programmes/{pid}/children.json"
    while True:
        url = f"{base_url}?page={page}"
        debug_log(f"{pid}: fetching children page {page} via {url}")
        response = requests.get(url, timeout=timeout)
        if response.status_code == 404:
            debug_log(f"{pid}: children page {page} returned 404; stopping pagination")
            break
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("children payload is not an object")
        children = payload.get("children")
        if not isinstance(children, dict):
            debug_log(f"{pid}: children page {page} missing 'children' key")
            break
        page_programmes = [
            item for item in (children.get("programmes") or []) if isinstance(item, dict)
        ]
        debug_log(f"{pid}: children page {page} returned {len(page_programmes)} programmes")
        programmes.extend(page_programmes)
        total = children.get("total")
        try:
            total_int = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_int = None
        if total_int is not None and len(programmes) >= total_int:
            debug_log(f"{pid}: collected {len(programmes)} programmes across children pages")
            break
        if not page_programmes:
            debug_log(f"{pid}: no programmes found on children page {page}; stopping")
            break
        page += 1
        if page > 1000:
            debug_log(f"{pid}: reached pagination safety limit while fetching children")
            break
    return programmes


def _episode_pids_from_programmes(programmes: Iterable[dict]) -> list[str]:
    episode_pids: list[str] = []
    for item in programmes:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "episode":
            continue
        candidate = item.get("pid")
        if isinstance(candidate, str) and PID_PATTERN.fullmatch(candidate):
            episode_pids.append(candidate.lower())
    return episode_pids


def _series_pids_from_programmes(programmes: Iterable[dict]) -> list[str]:
    series_pids: list[str] = []
    for item in programmes:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "series":
            continue
        candidate = item.get("pid")
        if isinstance(candidate, str) and PID_PATTERN.fullmatch(candidate):
            series_pids.append(candidate.lower())
    return dedupe_preserve_order(series_pids)


def _api_expand_series(pid: str, timeout: int) -> list[str]:
    programmes = fetch_children_programmes(pid, timeout)
    episode_pids = _episode_pids_from_programmes(programmes)
    debug_log(f"{pid}: API series expansion found {len(episode_pids)} episode(s)")
    return dedupe_preserve_order(episode_pids)


def _api_expand_brand(pid: str, timeout: int) -> list[str]:
    programmes = fetch_children_programmes(pid, timeout)
    episode_pids = _episode_pids_from_programmes(programmes)
    series_pids = _series_pids_from_programmes(programmes)
    debug_log(
        f"{pid}: API brand expansion has {len(series_pids)} series child(ren) "
        f"and {len(episode_pids)} direct episode(s)"
    )
    for series_pid in series_pids:
        series_episodes = _api_expand_series(series_pid, timeout)
        debug_log(
            f"{pid}: series {series_pid} contributed {len(series_episodes)} episode(s) via API"
        )
        episode_pids.extend(series_episodes)
    return dedupe_preserve_order(episode_pids)


def get_bbc_episode_pids(pid: str, timeout: int = 120) -> list[str]:
    pid = normalise_pid(pid)
    try:
        programme_payload = fetch_programme_json(pid, timeout)
    except requests.RequestException as exc:
        debug_log(f"{pid}: failed to fetch programme metadata via API ({exc})")
    except ValueError as exc:
        debug_log(f"{pid}: invalid programme payload via API ({exc})")
    else:
        programme = programme_payload.get("programme")
        if isinstance(programme, dict):
            programme_type = programme.get("type")
            debug_log(f"{pid}: programme.type from API is '{programme_type}'")
            if programme_type == "episode":
                debug_log(f"{pid}: programme is an episode; returning PID directly")
                return [pid]
            if programme_type == "series":
                try:
                    series_pids = _api_expand_series(pid, timeout)
                except requests.RequestException as exc:
                    debug_log(f"{pid}: failed to expand series via API ({exc})")
                except ValueError as exc:
                    debug_log(f"{pid}: invalid series children payload via API ({exc})")
                else:
                    if series_pids:
                        debug_log(
                            f"{pid}: API series expansion succeeded with {len(series_pids)} PID(s)"
                        )
                        return series_pids
                    debug_log(f"{pid}: API series expansion returned no PIDs")
            elif programme_type == "brand":
                try:
                    brand_pids = _api_expand_brand(pid, timeout)
                except requests.RequestException as exc:
                    debug_log(f"{pid}: failed to expand brand via API ({exc})")
                except ValueError as exc:
                    debug_log(f"{pid}: invalid brand children payload via API ({exc})")
                else:
                    if brand_pids:
                        debug_log(
                            f"{pid}: API brand expansion succeeded with {len(brand_pids)} PID(s)"
                        )
                        return brand_pids
                    debug_log(f"{pid}: API brand expansion returned no PIDs")
            else:
                debug_log(f"{pid}: API payload missing 'programme' object")
    debug_log(f"{pid}: falling back to get_iplayer PID expansion")
    try:
        cli_module = import_module("auntie.cli")
    except Exception:
        fallback = _get_bbc_episode_pids_via_get_iplayer_impl
    else:
        fallback = getattr(
            cli_module,
            "_get_bbc_episode_pids_via_get_iplayer",
            _get_bbc_episode_pids_via_get_iplayer_impl,
        )
    return fallback(pid, timeout)


def _get_bbc_episode_pids_via_get_iplayer_impl(pid: str, timeout: int = 120) -> list[str]:
    """
    Return a clean list of BBC episode PIDs for a brand, series, or episode PID,
    using get_iplayer's recursive listing feature.
    """
    base_command = list(get_iplayer_invocation())
    cmd = [
        *base_command,
        f"--pid={pid}",
        "--pid-recursive-list",
    ]

    debug_log(f"Expanding PID {pid} with command: {format_command(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    debug_log(f"PID expansion command exited with code {result.returncode}")
    if result.stdout:
        debug_log(f"PID expansion stdout:\n{result.stdout.strip() or '<empty>'}")
    if result.stderr:
        debug_log(f"PID expansion stderr:\n{result.stderr.strip() or '<empty>'}")

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

    debug_log(f"PID expansion result for {pid}: {pids or '[no matches]'}")

    return pids


_get_bbc_episode_pids_via_get_iplayer = _get_bbc_episode_pids_via_get_iplayer_impl


__all__ = [
    "PID_METADATA",
    "PROGRAM_LABEL_WIDTH",
    "bbc_metadata_from_pid",
    "build_program_label",
    "format_plex_filename",
    "get_bbc_episode_pids",
    "get_cached_metadata",
]
