"""Module entry point for `python -m pget_iplayer` or direct execution."""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    # When executed as a script (e.g. `uv run -- pget_iplayer`), ensure the project
    # root is on sys.path so absolute imports resolve correctly.
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from pget_iplayer.cli import main  # noqa: E402  (import after potential sys.path tweak)


if __name__ == "__main__":
    raise SystemExit(main())
