"""Media type detection by content, never by extension alone (blueprint §4.2)."""

from __future__ import annotations

import json
from enum import StrEnum

import filetype
from pydantic import BaseModel


class MediaKind(StrEnum):
    text = "text"
    json = "json"
    pdf = "pdf"
    office = "office"
    image = "image"
    audio = "audio"
    video = "video"


class Sniffed(BaseModel):
    media_type: str
    kind: MediaKind
    extension: str | None = None


_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/tiff", "image/bmp"}
_OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}


def sniff(data: bytes, *, filename: str | None = None) -> Sniffed | None:  # noqa: PLR0911
    """Return the detected media type, or ``None`` when the content is not a supported type.

    Binary formats are detected from magic bytes. Text is accepted only when it decodes as
    UTF-8; JSON is recognised by parsing. The filename is used only to prefer ``text/markdown``
    over ``text/plain`` for ``.md`` files — never to decide the family.
    """
    kind = filetype.guess(data)
    if kind is not None:
        mime: str = kind.mime
        ext: str = kind.extension
        if mime == "application/pdf":
            return Sniffed(media_type=mime, kind=MediaKind.pdf, extension=ext)
        if mime in _IMAGE_TYPES:
            return Sniffed(media_type=mime, kind=MediaKind.image, extension=ext)
        if mime.startswith("audio/"):
            return Sniffed(media_type=mime, kind=MediaKind.audio, extension=ext)
        if mime.startswith("video/"):
            return Sniffed(media_type=mime, kind=MediaKind.video, extension=ext)
        if mime in _OFFICE_TYPES:
            return Sniffed(media_type=mime, kind=MediaKind.office, extension=ext)
        return None

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            return Sniffed(media_type="application/json", kind=MediaKind.json, extension="json")
    if filename and filename.lower().endswith((".md", ".markdown")):
        return Sniffed(media_type="text/markdown", kind=MediaKind.text, extension="md")
    return Sniffed(media_type="text/plain", kind=MediaKind.text, extension="txt")


__all__ = ["MediaKind", "Sniffed", "sniff"]
