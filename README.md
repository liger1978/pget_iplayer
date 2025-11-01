# auntie

Pretty parallel iPlayer downloads!

![Alt Text](auntie.gif)

`auntie` is a wrapper that launches multiple `get_iplayer` downloads in parallel. Each download runs in its own thread and reports progress via colour-coded bars (one per pid/stream) so you can monitor multiple jobs at once.

## Requirements

On Linux, first install [`get_iplayer`](https://github.com/get-iplayer/get_iplayer/wiki/installation), `ffmpeg` and `AtomicParsley`.

On MacOs, first install[ `get_player`](https://github.com/get-iplayer/get_iplayer_macos/releases/tag/latest).

On Windows, first install [`get_iplayer`](https://github.com/get-iplayer/get_iplayer_win32/releases/latest).

## Installation

The easiset way to install `auntie` is to download a zip file from releases, unzip it and run the compiled executable from a location of your choice. The following command line installation instructions are provided for convenience.

### Linux

```bash
a=$(uname -m)
case $a in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
esac
wget "https://github.com/liger1978/auntie/releases/latest/download/auntie_linux_${arch}.tar.gz"
tar xzf auntie_linux_${arch}.tar.gz
sudo install -m 0755 ./auntie /usr/local/bin
rm -f auntie_linux_${arch}.tar.gz auntie
```

### MacOS

```bash
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then
    ARCH=arm64
else
    ARCH=amd64
fi
wget "https://github.com/liger1978/auntie/releases/latest/download/auntie_macos_${arch}.tar.gz"
tar xzf auntie_macos_${arch}.tar.gz
sudo install -m 0755 ./auntie /usr/local/bin
rm -f auntie_macos_${arch}.tar.gz auntie
```

### Windows

(PowerShell)
```powershell
$os = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture
switch ($os) {
  'Arm64' { $arch = 'arm64'; break }
  'X64'   { $arch = 'amd64'; break }
  default {
    $a = if ($env:PROCESSOR_ARCHITECTURE -eq 'x86' -and $env:PROCESSOR_ARCHITEW6432) {
      $env:PROCESSOR_ARCHITEW6432
    } else { $env:PROCESSOR_ARCHITECTURE }
    $arch = $a.ToLower().Replace('amd64','amd64').Replace('arm64','arm64')
  }
}
iwr "https://github.com/liger1978/auntie/releases/latest/download/auntie_windows_${arch}.zip" -OutFile "auntie_windows_$arch.zip"
Expand-Archive "auntie_windows_$arch.zip" -DestinationPath . -Force
$dest = "$HOME\bin"
New-Item -Force -ItemType Directory $dest
Copy-Item "auntie.exe" "$dest\auntie.exe"
Remove-Item "auntie_windows_$arch.zip" -Force
Remove-Item "auntie.exe" -Force
$u = [Environment]::GetEnvironmentVariable('Path','User')
if ($u -notmatch [regex]::Escape($dest)) {
  [Environment]::SetEnvironmentVariable('Path', "$dest;$u",'User')
  Write-Host "Added $dest to your user PATH. Open a new shell to use it."
}
```
## Usage

```
usage: auntie [-h] [-d] [-n] [-p] [-t THREADS] [-v] PID [PID ...]

download multiple BBC iPlayer programmes in parallel.

positional arguments:
  PID                   one or more BBC programme, series (season) or brand (show) PIDs or URLs to download.

options:
  -h, --help            show this help message and exit
  -d, --debug           enable verbose debug logging of get_iplayer interactions (default: False)
  -n, --no-clean        preserve the temporary download subdirectory instead of deleting it (default: False)
  -p, --plex            rename completed video files to Plex naming convention (default: False)
  -t THREADS, --threads THREADS
                        maximum number of parallel download workers (default: 20)
  -v, --version         display the installed version and exit
```

## Development

### Git hooks

This project uses [pre-commit](https://pre-commit.com/) to keep formatting and linting consistent. After cloning the repository, install the hooks once:

```
make install-git-hooks
```

### Building a standalone binary

Run `make build` to produce a standalone `auntie` binary at `build/auntie`. The Makefile installs dependencies locally with `uv sync` and then invokes `uv run nuitka` to create a single-file executable without touching your global Python environment.

Related targets:

- `make docker-image` builds a local Docker image for reproducible builds.
- `make docker-build` runs the build inside that Docker image (this also runs `docker-image` first).
- `make clean` removes the `build/` directory.
- `make distclean` additionally deletes any `__pycache__` directories.
