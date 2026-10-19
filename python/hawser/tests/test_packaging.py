from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ["hawser", "hawser_server"]  # the published distributions


@pytest.mark.parametrize("package", PACKAGES)
def test_each_package_ships_the_licence(package: str) -> None:
    """Hatchling bundles a package's own LICENSE; each copy must match the repository's."""
    copy = ROOT / "python" / package / "LICENSE"
    assert copy.read_bytes() == (ROOT / "LICENSE").read_bytes()
