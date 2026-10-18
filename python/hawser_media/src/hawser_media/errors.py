"""Errors raised by media primitives. The runtime maps them to ``AdmissionError`` codes."""

from __future__ import annotations


class MediaError(Exception):
    """A media file could not be probed or decoded. ``code`` is a stable, lowercase identifier."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["MediaError"]
