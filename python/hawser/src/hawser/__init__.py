"""Hawser — an open-source runtime for verifiable multimodal AI workflows.

import hawser
result = hawser.run("Extract the invoice", inputs=["invoice.pdf"], output_schema=Invoice)
"""

from hawser import errors, ids
from hawser.client import Client, run
from hawser.config import HawserConfig, RecordMode
from hawser.models import Policy, RunRequest, RunResult, RunStatus

__version__ = "0.1.0a0"

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
