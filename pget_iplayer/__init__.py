"""pget_iplayer package."""

from importlib import metadata


def __getattr__(name: str) -> str:
    if name == "__version__":
        try:
            return metadata.version("pget-iplayer")
        except metadata.PackageNotFoundError:
            # Fallback during local development when the package is not installed.
            return "0.0.0"
    raise AttributeError(name)


__all__ = ["__version__"]
