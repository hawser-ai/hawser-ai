"""Provider-neutral request and response shapes."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hawser.models import ModelCallUsage, ModelSpec
from hawser.models.common import JsonSchema


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    kind: Literal["image"] = "image"
    media_type: str
    data: bytes
    #: Free-form label used in traces (e.g. ``invoice.pdf#page=2``).
    label: str | None = None


class DocumentPart(BaseModel):
    """A native document part (PDF) for providers that accept them directly."""

    kind: Literal["document"] = "document"
    media_type: str = "application/pdf"
    data: bytes
    label: str | None = None


Part = Annotated[TextPart | ImagePart | DocumentPart, Field(discriminator="kind")]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    parts: list[Part]


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Registry id, ``<provider>/<model>``.
    model: str
    system: str | None = None
    messages: list[Message]
    output_schema: JsonSchema | None = None
    max_output_tokens: int = 16_000
    #: Reasoning effort (``low`` … ``max``) where the provider supports it.
    effort: str | None = None
    #: Caller tags carried into traces; never sent to the provider.
    metadata: dict[str, str] = Field(default_factory=dict)

    def fingerprint_payload(self) -> dict[str, Any]:
        """The request with binary parts replaced by their hashes, for cassette keys."""

        def part(p: TextPart | ImagePart | DocumentPart) -> dict[str, Any]:
            if isinstance(p, TextPart):
                return {"kind": "text", "text": p.text}
            return {
                "kind": p.kind,
                "media_type": p.media_type,
                "sha256": hashlib.sha256(p.data).hexdigest(),
                "label": p.label,
            }

        return {
            "model": self.model,
            "system": self.system,
            "messages": [
                {"role": m.role, "parts": [part(p) for p in m.parts]} for m in self.messages
            ],
            "output_schema": self.output_schema,
            "max_output_tokens": self.max_output_tokens,
            "effort": self.effort,
        }


class ModelResponse(BaseModel):
    text: str | None = None
    #: Parsed JSON when ``output_schema`` was given and the output parsed.
    parsed: Any | None = None
    parse_error: str | None = None
    usage: ModelCallUsage
    stop_reason: str | None = None
    truncated: bool = False
    #: True when the provider rejected the schema grammar and the gateway fell back to
    #: prompt-guided JSON (parsed from text, not enforced by the provider).
    structured_fallback: bool = False
    #: Candidate model ids that failed before this response's model answered.
    fallback_from: list[str] = Field(default_factory=list)
    #: Raw provider payload, kept only when ``config.store_raw`` is set.
    raw: dict[str, Any] | None = None

    def to_cassette(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"raw"})

    @classmethod
    def from_cassette(cls, data: dict[str, Any]) -> ModelResponse:
        resp = cls.model_validate(data)
        resp.usage.replayed = True
        resp.usage.cost_usd = 0.0
        return resp


class ModelAdapter(Protocol):
    provider: str

    def complete(self, spec: ModelSpec, request: ModelRequest) -> ModelResponse: ...


__all__ = [
    "DocumentPart",
    "ImagePart",
    "Message",
    "ModelAdapter",
    "ModelRequest",
    "ModelResponse",
    "Part",
    "TextPart",
]
