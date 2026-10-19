"""Document, image, audio and video primitives for Hawser.

This package knows nothing about runs, tenants or policies. It turns bytes into typed facts
(probes, text indexes, rendered pages) that the ``hawser`` runtime wraps into contract objects.
"""

from hawser_media.errors import MediaError
from hawser_media.sniff import MediaKind, Sniffed, sniff

__all__ = ["MediaError", "MediaKind", "Sniffed", "sniff"]
