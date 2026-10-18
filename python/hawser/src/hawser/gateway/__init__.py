"""Model gateway (blueprint §9): one contract across providers, recording and cost accounting."""

from hawser.gateway.base import (
    DocumentPart,
    ImagePart,
    Message,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    Part,
    TextPart,
)
from hawser.gateway.gateway import Gateway
from hawser.gateway.registry import Registry, load_registry

__all__ = [
    "DocumentPart",
    "Gateway",
    "ImagePart",
    "Message",
    "ModelAdapter",
    "ModelRequest",
    "ModelResponse",
    "Part",
    "Registry",
    "TextPart",
    "load_registry",
]
