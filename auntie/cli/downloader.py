"""Execution of get_iplayer downloads."""

from __future__ import annotations

import codecs
import errno
import os
import secrets
import select
import subprocess
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import BinaryIO

from tqdm import tqdm

from .colours import ColourStyle
from .debug import debug_log
from .filesystem import (
    cleanup_download_directories,
    find_downloaded_video,
    locate_download_directory,
    move_video_to_root,
    rename_video_for_plex,
)
from .iplayer import build_download_command
from .progress import ProgressTracker
from .utils import format_command, next_delimiter


class DownloadRunner:
    """Encapsulate the download lifecycle for a single PID."""

    def __init__(
        self,
        progress: ProgressTracker,
        *,
        plex_mode: bool,
        clean_temp: bool,
        print_lock: threading.Lock,
    ) -> None:
        self._progress = progress
        self._plex_mode = plex_mode
        self._clean_temp = clean_temp
        self._print_lock = print_lock

    def run(self, pid: str, colour: ColourStyle) -> int:
        token = ""
        expected_download_dir = Path()
        while True:
            token = secrets.token_hex(4)
            subdir_name = f".auntie-{pid}-{token}"
            expected_download_dir = Path.cwd() / subdir_name
            if not expected_download_dir.exists():
                break
            if self._clean_temp:
                try:
                    cleanup_download_directories(
                        {expected_download_dir: True}, pid, self._print_lock
                    )
                except Exception as exc:  # pragma: no cover - extremely unlikely
                    with self._print_lock:
                        tqdm.write(f"{pid}: unable to clear previous download directory ({exc})")
                    return 1
                break
        expected_download_dir.mkdir(parents=True, exist_ok=True)
        command = build_download_command(pid, expected_download_dir)
        debug_log(f"{pid}: using download subdir {expected_download_dir.name}")
        debug_log(f"{pid}: launching get_iplayer with command: {format_command(command)}")

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

        cleanup_map: dict[Path, bool] = {expected_download_dir: self._clean_temp}

        try:
            if using_pty:
                try:
                    master_fd, slave_fd = os.openpty()
                except OSError as exc:
                    with self._print_lock:
                        tqdm.write(f"{pid}: unable to allocate pty ({exc})")
                    debug_log(f"{pid}: unable to allocate PTY ({exc})")
                    return 1

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
                with self._print_lock:
                    tqdm.write(f"{pid}: get_iplayer command not found")
                debug_log(f"{pid}: get_iplayer command not found when launching")
                return 127
            except OSError as exc:
                if master_fd is not None:
                    os.close(master_fd)
                    master_fd = None
                if slave_fd is not None:
                    os.close(slave_fd)
                    slave_fd = None
                with self._print_lock:
                    tqdm.write(f"{pid}: failed to start get_iplayer ({exc})")
                debug_log(f"{pid}: failed to start get_iplayer ({exc})")
                return 1

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

            colour = self._progress.colour_for_pid(pid, colour)
            self._progress.start_pseudo_stream(pid, "waiting", colour)
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
                            self._progress.tick_pseudo_stream(pid, "waiting", colour)
                            self._progress.tick_pseudo_stream(pid, "converting", colour)
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
                            self._progress.tick_pseudo_stream(pid, "waiting", colour)
                            self._progress.tick_pseudo_stream(pid, "converting", colour)
                            continue
                    if not raw:
                        if process.poll() is not None:
                            break
                        continue
                    text = decoder.decode(raw) if raw else ""
                    if not text:
                        continue
                    buffer += text
                    self._progress.tick_pseudo_stream(pid, "waiting", colour)
                    self._progress.tick_pseudo_stream(pid, "converting", colour)
                    saw_carriage = False
                    while True:
                        delimiter_index = next_delimiter(buffer)
                        if delimiter_index is None:
                            break
                        delimiter_char = buffer[delimiter_index]
                        line = buffer[:delimiter_index]
                        remainder = buffer[delimiter_index + 1 :]
                        if delimiter_char == "\r" and remainder.startswith("\n"):
                            remainder = remainder[1:]
                        buffer = remainder
                        self._progress.emit_progress_line(pid, colour, line)
                        last_partial = ""
                        if delimiter_char == "\r":
                            saw_carriage = True
                    if saw_carriage and buffer and buffer != last_partial:
                        self._progress.emit_progress_line(pid, colour, buffer)
                        last_partial = buffer
            finally:
                if master_fd is not None and using_pty:
                    os.close(master_fd)
                    master_fd = None

            if decoder is not None:
                buffer += decoder.decode(b"", final=True)
            while True:
                delimiter_index = next_delimiter(buffer)
                if delimiter_index is None:
                    break
                delimiter_char = buffer[delimiter_index]
                line = buffer[:delimiter_index]
                remainder = buffer[delimiter_index + 1 :]
                if delimiter_char == "\r" and remainder.startswith("\n"):
                    remainder = remainder[1:]
                buffer = remainder
                self._progress.emit_progress_line(pid, colour, line)
                last_partial = ""
            if buffer:
                self._progress.emit_progress_line(pid, colour, buffer)

            return_code = process.wait()
            debug_log(f"{pid}: get_iplayer exited with code {return_code}")
            self._progress.mark_pid_complete(pid)
            self._progress.complete_pseudo_stream(pid, "waiting", colour)
            self._progress.complete_pseudo_stream(pid, "converting", colour)

            download_dir = locate_download_directory(token, pid)
            if download_dir is not None:
                cleanup_map[download_dir] = self._clean_temp

            debug_log(f"{pid}: located download directory {download_dir}")
            if download_dir and download_dir.exists():
                if return_code == 0:
                    video_path = find_downloaded_video(download_dir)
                    if video_path is None:
                        with self._print_lock:
                            tqdm.write(f"{pid}: no video file found in download directory")
                        return_code = max(return_code, 1)
                    else:
                        moved_video = move_video_to_root(video_path, self._print_lock)
                        if moved_video is None:
                            return_code = max(return_code, 1)
            elif return_code == 0:
                with self._print_lock:
                    tqdm.write(f"{pid}: download directory not found")
                return_code = 1

            if self._plex_mode and return_code == 0 and moved_video:
                renamed = rename_video_for_plex(pid, moved_video, self._print_lock)
                if renamed is None:
                    return_code = 1

            return return_code
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
            cleanup_download_directories(cleanup_map, pid, self._print_lock)


__all__ = ["DownloadRunner"]
