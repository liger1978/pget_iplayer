# pget_iplayer

pget_iplayer is a thin Python wrapper that launches multiple `get_iplayer` downloads in parallel. Each download runs in its own thread and reports progress via colour-coded bars (one per pid/stream) so you can monitor multiple jobs at once.

### Requirements

- Python 3.12+
- `get_iplayer` must be available on your `PATH`

### Usage

```bash
uv run -- pget-iplayer [-t THREADS] <PID> [PID ...]
```

- Supply one or more BBC programme, series (season) or brand (show) pids as positional arguments.
- Brand and series PIDs are expanded automatically so all child episodes are queued.
- Every pid is downloaded via `get_iplayer --get --subtitles --subs-embed --force --overwrite --tv-quality=fhd,hd,sd --pid=<PID>`.
- Use `-t/--threads` to limit concurrent downloads (defaults to the number of CPU cores).
- Output is summarised as per-stream progress bars (`audio`, `video`, etc.), colour-coded per pid, sorted by pid then stream name, and annotated with live speed + ETA.

If you prefer to avoid the hyphenated entry-point, the following equivalent invocation works too:

```bash
uv run -- pget_iplayer <PID> [PID ...]
```

You can also run the bundled wrapper script, which pins the correct `uv` invocation (and avoids having to install the project just to get the `pget-iplayer` console script on your `PATH`):

```bash
./bin/pget_iplayer <PID> [PID ...]
```

Example:

```bash
uv run -- pget-iplayer -t 6 m000xyz1 m000xyz2 m000xyz3
```

The command above will start three parallel downloads, each with audio/video progress bars rendered live.
