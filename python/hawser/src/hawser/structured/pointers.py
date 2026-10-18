"""JSON Pointer (RFC 6901) helpers. ``field_meta`` keys are built only through these."""

from __future__ import annotations


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def pointer(path: list[str | int]) -> str:
    return "".join(f"/{escape_token(str(p))}" for p in path)


__all__ = ["escape_token", "pointer"]
