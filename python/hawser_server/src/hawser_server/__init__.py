"""Hawser control plane: ``hawserd`` (ADR-0010)."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("hawser-server")
except PackageNotFoundError:  # running from a source tree without installed metadata
    __version__ = "0.1.0"

__all__ = ["__version__"]
