"""Text and JSON probing."""

from __future__ import annotations

import json

from pydantic import BaseModel

from hawser_media.errors import MediaError


class TextInfo(BaseModel):
    chars: int
    valid_json: bool | None = None
    lines: int = 0


def probe(data: bytes, *, expect_json: bool = False) -> TextInfo:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MediaError("text_not_utf8", "text input must be UTF-8") from exc
    valid_json: bool | None = None
    if expect_json:
        try:
            json.loads(text)
            valid_json = True
        except ValueError as exc:
            raise MediaError("json_invalid", f"invalid JSON: {exc}") from exc
    return TextInfo(chars=len(text), valid_json=valid_json, lines=text.count("\n") + 1)


__all__ = ["TextInfo", "probe"]
