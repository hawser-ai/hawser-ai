from __future__ import annotations

from pathlib import Path

from hawser.conformance import SCENARIOS, local_target, run_all


def test_conformance_against_local_runtime(tmp_path: Path) -> None:
    results = run_all(local_target(tmp_path / "home"))
    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(f"{r.name}: {r.detail}" for r in failed)
    assert {r.name for r in results} == set(SCENARIOS)


def test_conformance_cli(tmp_path: Path) -> None:
    from hawser.conformance.__main__ import main

    assert main(["--home", str(tmp_path / "home"), "--only", "accepted_extraction"]) == 0
