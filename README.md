# pget_iplayer

pget_iplayer is a thin Python wrapper that launches multiple `get_iplayer` downloads in parallel. Each download runs in its own thread and reports progress via colour-coded bars (one per pid/stream) so you can monitor multiple jobs at once.

### Requirements

- Python 3.12+
- `get_iplayer` must be available on your `PATH`

### Usage

```bash
uv run -- pget_iplayer [-t THREADS] <PID> [PID ...]
```

- Supply one or more BBC iPlayer PIDs or URLs (episode/series/brand) as positional arguments. URLs are normalised to the correct episode PID automatically.
- Brand and series PIDs are expanded automatically so all child episodes are queued.
- Every pid is downloaded via `get_iplayer --get --subtitles --subs-embed --force --overwrite --tv-quality=fhd,hd,sd --pid=<PID>`.
- Use `-t/--threads` to limit concurrent downloads; defaults to your CPU core count (fallback to 4 if it cannot be detected).
- Pass `-p/--plex` to rename completed video files to Plex's `Show - sXXeYY - Episode.ext` format.
- Each download runs inside a hidden temporary directory named `.pget_iplayer-<PID>-<RANDOM>`; on success the finished video is moved back to the working directory and the temp folder is removed (including subtitle sidecars). Use `-n/--no-clean` if you want to inspect the download artefacts and keep the directory.
- Output is summarised as per-stream progress bars (`audio`, `video`, etc.), colour-coded per pid, sorted by pid then stream name, and annotated with live speed + ETA.

Example:

```bash
uv run -- pget_iplayer -t 6 m000xyz1 m000xyz2 m000xyz3
```

The command above will start three parallel downloads, each with audio/video progress bars rendered live.

### Building a standalone binary

Run `make build` to produce a standalone `pget_iplayer` binary at `build/pget_iplayer`. The Makefile installs dependencies locally with `uv sync` and then invokes `uv run nuitka` to create a single-file executable without touching your global Python environment.

Related targets:

- `make docker-image` builds a local Docker image for reproducible builds.
- `make docker-build` runs the build inside that Docker image (this also runs `docker-image` first).
- `make clean` removes the `build/` directory.
- `make distclean` additionally deletes any `__pycache__` directories.
