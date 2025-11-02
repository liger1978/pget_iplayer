"""Filesystem helpers for download management."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

from tqdm import tqdm

from .metadata import format_plex_filename, get_cached_metadata

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


def ensure_unique_path(directory: Path, filename: str) -> Path:
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


def locate_download_directory(token: str, pid: str) -> Path | None:
    expected = Path.cwd() / f".auntie-{pid}-{token}"
    if expected.exists():
        return expected
    suffix = f"-{pid}-{token}"
    for candidate in Path.cwd().iterdir():
        if (
            candidate.is_dir()
            and candidate.name.startswith(".auntie-")
            and candidate.name.endswith(suffix)
        ):
            return candidate
    return None


def find_downloaded_video(download_dir: Path) -> Path | None:
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


def move_video_to_root(
    video_path: Path,
    print_lock,
) -> Path | None:
    destination = ensure_unique_path(Path.cwd(), video_path.name)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        video_path.rename(destination)
    except OSError as exc:
        with print_lock:
            tqdm.write(f"{video_path.name}: failed to move video ({exc})")
        return None
    return destination


def rename_video_for_plex(pid: str, source_path: Path, print_lock) -> Path | None:
    metadata = get_cached_metadata(pid)

    target_name = format_plex_filename(metadata, pid, source_path.suffix)
    if source_path.name == target_name:
        return source_path

    destination = ensure_unique_path(source_path.parent, target_name)
    try:
        source_path.rename(destination)
    except OSError as exc:
        with print_lock:
            tqdm.write(f"{pid}: failed to rename {source_path.name} -> {destination.name} ({exc})")
        return None

    with print_lock:
        tqdm.write(f"{pid}: renamed to {destination.name}")
    return destination


def cleanup_download_directories(directories: Dict[Path, bool], pid: str, print_lock) -> None:
    """Remove temporary download directories when requested.

    The *directories* mapping indicates which paths should be removed.
    """
    seen: set[Path] = set()
    for target, should_remove in directories.items():
        if not should_remove or not target:
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


__all__ = [
    "cleanup_download_directories",
    "ensure_unique_path",
    "find_downloaded_video",
    "locate_download_directory",
    "move_video_to_root",
    "rename_video_for_plex",
    "VIDEO_EXTENSIONS",
]
