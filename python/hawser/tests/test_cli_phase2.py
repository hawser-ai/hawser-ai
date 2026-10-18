from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hawser.cli import app

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "invoice-po-reconciliation"
runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / "hawser.toml").write_text("[hawser]\n", encoding="utf-8")
    shutil.copytree(
        SKILL, tmp_path / "skills" / SKILL.name, ignore=shutil.ignore_patterns("__pycache__")
    )
    return tmp_path


def test_skill_commands(project: Path) -> None:
    res = runner.invoke(app, ["skill", "list"])
    assert res.exit_code == 0, res.output
    assert "invoice-po-reconciliation" in res.output

    res = runner.invoke(app, ["skill", "validate", "skills/invoice-po-reconciliation"])
    assert res.exit_code == 0, res.output
    assert "document.extract" in res.output

    res = runner.invoke(app, ["skill", "install", "skills/invoice-po-reconciliation"])
    assert res.exit_code == 0, res.output
    lock = json.loads((project / "hawser.lock").read_text())
    assert lock["skills"]["invoice-po-reconciliation"]["version"] == "1.0.0"

    res = runner.invoke(app, ["skill", "new", "receipt-basics"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["skill", "validate", "skills/receipt-basics"])
    assert res.exit_code == 0, res.output

    res = runner.invoke(
        app, ["skill", "export", "skills/invoice-po-reconciliation", "--out", "bundle.tar.gz"]
    )
    assert res.exit_code == 0, res.output
    assert (project / "bundle.tar.gz").stat().st_size > 1000


def test_skill_test_replays_bundled_example(project: Path) -> None:
    res = runner.invoke(
        app,
        [
            "skill",
            "test",
            "skills/invoice-po-reconciliation",
            "--model",
            "openrouter/anthropic/claude-haiku-5.5",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "ok pair-01000: accepted" in res.output


def test_eval_run_and_gate_from_recordings(project: Path) -> None:
    from hawser_synth.dataset import build

    ds = build(project / "ds", n=1, seed=1000, scanned_fraction=0.0)
    cas = ds / "cassettes" / "openrouter_anthropic_claude-haiku-5.5"
    cas.mkdir(parents=True)
    for f in (SKILL / "examples" / "cassette").glob("*.json"):
        shutil.copy(f, cas / f.name)
    res = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--dataset",
            str(ds),
            "--model",
            "openrouter/anthropic/claude-haiku-5.5",
            "--out",
            "out",
        ],
    )
    assert res.exit_code == 0, res.output
    report = json.loads((project / "out" / "report.json").read_text())
    assert report["metrics"]["field_accuracy"] == 1.0
    assert report["metrics"]["cases"] == 1
    assert report["cases"][0]["status"] == "accepted"
    (project / "t.yaml").write_text(
        "field_accuracy: {min: 0.9}\nfailed_rate: {max: 0}\n", encoding="utf-8"
    )
    assert runner.invoke(app, ["eval", "gate", "out", "--thresholds", "t.yaml"]).exit_code == 0
    (project / "t2.yaml").write_text("review_rate: {max: -1}\n", encoding="utf-8")
    assert runner.invoke(app, ["eval", "gate", "out", "--thresholds", "t2.yaml"]).exit_code == 1
    res = runner.invoke(app, ["compare", "out", "out"])
    assert res.exit_code == 0, res.output
    assert "regressions: 0" in res.output
