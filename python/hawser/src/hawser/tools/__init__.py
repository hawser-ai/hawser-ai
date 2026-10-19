"""Tools (blueprint §11): typed, versioned capabilities behind a permission-enforcing broker."""

from hawser.tools import compute, convert, document, external, image, ocr, search
from hawser.tools.broker import Broker, ToolResult
from hawser.tools.registry import ToolRegistry, registry, tool

#: Importing the families registers their tools; listing them keeps the imports "used".
FAMILIES = (compute, convert, document, external, image, ocr, search)

__all__ = ["FAMILIES", "Broker", "ToolRegistry", "ToolResult", "registry", "tool"]
