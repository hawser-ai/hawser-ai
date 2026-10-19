"""Hawser — an open-source runtime for verifiable multimodal AI workflows.

import hawser
result = hawser.run("Extract the invoice", inputs=["invoice.pdf"], output_schema=Invoice)
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from hawser import errors, ids
from hawser.client import Client, run
from hawser.config import HawserConfig, RecordMode
from hawser.models import Policy, RunRequest, RunResult, RunStatus

try:
    __version__ = _pkg_version("hawser-ai")
except PackageNotFoundError:  # running from a source tree without installed metadata
    __version__ = "0.1.0"

__all__ = [
    "Client",
    "HawserConfig",
    "Policy",
    "RecordMode",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "__version__",
    "errors",
    "ids",
    "run",
]
