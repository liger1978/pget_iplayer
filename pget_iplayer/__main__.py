"""Module entry point for `python -m pget_iplayer` or direct execution."""

from __future__ import annotations

import sys
from pathlib import Path


try:
    from pget_iplayer.cli import main
except ImportError:  # pragma: no cover
    # When executed as a script (e.g. `uv run -- pget_iplayer`), ensure the project
    # root is on sys.path so absolute imports resolve correctly.
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from pget_iplayer.cli import main  # type: ignore[import]


if __name__ == "__main__":
    raise SystemExit(main())
