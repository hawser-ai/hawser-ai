"""Conformance suite: the same scenarios against any ``Runtime`` (local or a server).

Every scenario uses the bundled quickstart (invoice, schema, policy, recorded model response),
so a target only needs ``HAWSER_RECORD_MODE=replay`` with the quickstart cassette to pass
without a provider key. ``python -m hawser.conformance --server URL`` runs it from the shell.
"""

from hawser.conformance.scenarios import (
    SCENARIOS,
    ScenarioResult,
    Target,
    local_target,
    quickstart_cassette_dir,
    quickstart_request,
    run_all,
)

__all__ = [
    "SCENARIOS",
    "ScenarioResult",
    "Target",
    "local_target",
    "quickstart_cassette_dir",
    "quickstart_request",
    "run_all",
]
