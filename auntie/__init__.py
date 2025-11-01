"""auntie package."""

from importlib import metadata

try:
    from .version import __version__
except ImportError:
    try:
        __version__ = metadata.version("auntie")
    except metadata.PackageNotFoundError:
        # Fallback during local development when the package is not installed.
        __version__ = "1.0.0"

__all__ = ["__version__"]
