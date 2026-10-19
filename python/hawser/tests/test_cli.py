from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hawser.cli import app

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def test_init_and_quickstart_offline(project: Path) -> None:
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0, res.output
    assert (project / "hawser.toml").exists()
    assert (project / "quickstart" / "invoice.pdf").exists()
    assert list((project / "quickstart" / "cassette").glob("*.json"))
    assert not (project / "quickstart" / "__init__.py").exists()

    res = runner.invoke(app, ["quickstart", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["status"] == "accepted"
    assert data["result"]["invoice_number"].startswith("INV-")
    assert data["usage"]["model_calls"][0]["replayed"] is True
    assert data["usage"]["totals"]["cost_usd"] == 0
    found = [p for p, m in data["field_meta"].items() if m["status"] == "found"]
    anchored = [p for p in found if data["field_meta"][p]["evidence"]]
    assert len(anchored) / len(found) >= 0.9

    # the run is stored under the project-local .hawser/ and inspectable
    run_id = data["run_id"]
    res = runner.invoke(app, ["inspect", run_id, "--events"])
    assert res.exit_code == 0, res.output
    assert "ACCEPTED" in res.output
    assert "extract" in res.output
    assert "model.called" in res.output

    res = runner.invoke(app, ["inspect", run_id, "--field", "/total"])
    assert res.exit_code == 0, res.output
    assert "document_region" in res.output

    res = runner.invoke(app, ["runs", "list"])
    assert res.exit_code == 0
    assert run_id in res.output

    res = runner.invoke(app, ["models", "list"])
    assert res.exit_code == 0
    assert "anthropic/claude-opus-5-5" in res.output


def test_run_explicit_with_cassette(project: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    res = runner.invoke(
        app,
        [
            "run",
            "-i",
            "Extract the invoice.",
            "-f",
            "quickstart/invoice.pdf",
            "--schema",
            "quickstart/invoice.schema.json",
            "--policy",
            "quickstart/policy.yaml",
            "--cassette",
            "quickstart/cassette",
            "--record-mode",
            "replay",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "ACCEPTED" in res.output
    assert "replayed from cassette" in res.output


def test_quickstart_live_without_credentials_fails_cleanly(project: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    res = runner.invoke(app, ["quickstart", "--live", "--json"], env=env)
    assert res.exit_code == 1, res.output
    data = json.loads(res.output)
    assert data["status"] == "failed"
    assert data["error"]["code"] in {"missing_credentials", "connection", "http_401"}


def test_quickstart_replays_live_openrouter_recording(project: Path) -> None:
    """The bundle ships a real recording (Claude Opus 5.5 via OpenRouter); it replays offline."""
    assert runner.invoke(app, ["init"]).exit_code == 0
    entries = [
        json.loads(p.read_text()) for p in (project / "quickstart" / "cassette").glob("*.json")
    ]
    live = [e for e in entries if e["source"] == "live"]
    assert live, "no live recording in the bundle"
    assert live[0]["provider"] == "openrouter"
    res = runner.invoke(
        app, ["quickstart", "--model", "openrouter/anthropic/claude-opus-5.5", "--json"]
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["status"] == "accepted"
    assert data["usage"]["model_calls"][0]["replayed"] is True
    assert data["usage"]["model_calls"][0]["provider"] == "openrouter"
    truth = json.loads((project / "quickstart" / "truth.json").read_text())
    assert data["result"]["total"] == truth["total"]
    assert len(data["result"]["line_items"]) == len(truth["line_items"])
    found = [p for p, m in data["field_meta"].items() if m["status"] == "found"]
    assert all(data["field_meta"][p]["evidence"] for p in found)
