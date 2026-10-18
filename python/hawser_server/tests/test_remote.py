"""The SDK's HTTP backend and the CLI against a real ``hawserd`` process (uvicorn in a thread)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hawser.cli import app
from hawser.client import Client
from hawser.conformance import run_all
from hawser.errors import HawserError
from hawser.models import ApprovalStatus, EventType, RunStatus
from hawser.models.common import JsonSchema
from hawser.runtime.remote import RemoteRuntime
from hawser_server.testing import LiveServer, ServicesFactory, wait_for
from hawser_server.worker import Worker
from hawser_synth import Invoice

runner = CliRunner(env={"COLUMNS": "200"})


def test_sdk_against_server(
    live_server: LiveServer, api_key: str, worker: Worker, invoice_pdf: Path
) -> None:
    client = Client(base_url=live_server.url, api_key=api_key)
    result = client.run("Extract the invoice.", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.accepted, result.error
    assert result.parse_as(Invoice).invoice_number.startswith("INV-")
    assert result.evidence_for("/total")

    rt = client.runtime
    assert isinstance(rt, RemoteRuntime)
    events = list(rt.events(result.run_id))
    assert events[0].type == EventType.run_created
    streamed = list(rt.stream_events(result.run_id))
    assert [e.seq for e in streamed] == [e.seq for e in events]
    assert {s.key for s in rt.steps(result.run_id)} >= {"admit"}
    assert result.run_id in [s.id for s in rt.list()]
    assert rt.cancel(result.run_id) is False
    with pytest.raises(HawserError) as exc:
        rt.summary("run_01J9X3K4N7Q2M8R5T6V7W8X9Y0")
    assert exc.value.code == "run_unknown"


def test_cli_against_server(
    live_server: LiveServer,
    api_key: str,
    worker: Worker,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(invoice_schema))
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(
        app,
        [
            "--server",
            live_server.url,
            "--api-key",
            api_key,
            "run",
            "-i",
            "Extract the invoice.",
            "-f",
            str(invoice_pdf),
            "--schema",
            str(schema_path),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["status"] == "accepted"
    run_id = data["run_id"]

    monkeypatch.setenv("HAWSER_SERVER_URL", live_server.url)
    monkeypatch.setenv("HAWSER_API_KEY", api_key)
    res = runner.invoke(app, ["inspect", run_id, "--events"])
    assert res.exit_code == 0, res.output
    assert "ACCEPTED" in res.output and "model.called" in res.output
    res = runner.invoke(app, ["runs", "list"])
    assert res.exit_code == 0 and run_id in res.output
    res = runner.invoke(app, ["init", "proj", "--server", live_server.url])
    assert res.exit_code == 0
    assert f'url = "{live_server.url}"' in (tmp_path / "proj" / "hawser.toml").read_text()


def test_review_and_conformance_against_server(
    live_server: LiveServer, api_key: str, worker: Worker, services: Any
) -> None:
    # the server replays the bundled quickstart recording for the conformance suite
    from hawser.config import RecordMode
    from hawser.conformance import quickstart_cassette_dir
    from hawser.gateway.defaults import build_gateway

    cfg = services.config.runtime.model_copy(
        update={"record_mode": RecordMode.replay, "cassette_dir": quickstart_cassette_dir()}
    )
    services.engine.gateway = build_gateway(cfg)
    rt = RemoteRuntime(live_server.url, api_key)
    results = run_all(rt)
    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(f"{r.name}: {r.detail}" for r in failed)


def test_approvals_against_server(make_services: ServicesFactory, tmp_path: Path) -> None:
    from hawser.models import SideEffect
    from hawser.tools import tool

    @tool("test.remote_post", description="external write", side_effect=SideEffect.external_write)
    def remote_post(amount: float, action_id: str = "") -> dict[str, Any]:
        return {"posted": amount}

    skill = tmp_path / "skills" / "remote-post"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: remote-post\nversion: 0.1.0\ndescription: post\nmode: workflow\n"
        "inputs:\n  document:\n    type: document\noutput_schema: schemas.py:Out\n"
        "tools: [document.extract, test.remote_post]\n"
    )
    (skill / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class Out(BaseModel):\n    title: str\n    posted: float | None = None\n"
    )
    (skill / "workflow.py").write_text(
        "from hawser.workflow import WorkflowContext\nfrom schemas import Out\n\n"
        "def run(ctx):\n    doc = ctx.extract('document', Out)\n    ctx.attach('', doc)\n"
        "    posted = ctx.tool('test.remote_post', amount=42.0, action_id='post-1')\n"
        "    return {'title': (doc.result or {}).get('title'), 'posted': posted['posted']}\n"
    )
    env = {
        "title": {"value": "Doc", "source_text": "Doc", "found": True},
        "posted": {"value": 0.0, "source_text": "", "found": False},
    }
    svc = make_services(output=env, skill_paths=[tmp_path / "skills"])
    tenant = svc.auth.create_tenant("acme")
    _, token = svc.auth.create_key(tenant.id, "t")
    srv = LiveServer(svc).start()
    w = Worker(svc, lease_seconds=5, poll_s=0.02)
    w.start()
    try:
        doc = tmp_path / "document.txt"
        doc.write_text("Doc\n")
        rt = RemoteRuntime(srv.url, token)
        paused = Client(rt).run(inputs=[doc], skill="remote-post")
        assert paused.status == RunStatus.awaiting_approval
        pending = rt.approvals.list(run_id=paused.run_id, status=ApprovalStatus.pending)
        assert len(pending) == 1 and pending[0].tool == "test.remote_post"

        decided = rt.approvals.decide(pending[0].id, approve=True, by="chetan", comment="ok")
        assert decided.status == ApprovalStatus.approved
        wait_for(lambda: rt.summary(paused.run_id).status == RunStatus.accepted)
        final = rt.get(paused.run_id)
        assert final is not None and final.result == {"title": "Doc", "posted": 42.0}
        types = [e.type for e in rt.events(paused.run_id)]
        assert EventType.approval_requested in types and EventType.approval_decided in types
        assert rt.approvals.list(run_id=paused.run_id, status=ApprovalStatus.pending) == []

        # the CLI's `hawser approve` works the same way against the server
        paused2 = Client(rt).run(inputs=[doc], skill="remote-post", idempotency_key="second")
        assert paused2.status == RunStatus.awaiting_approval
        res = runner.invoke(
            app, ["--server", srv.url, "--api-key", token, "approve", paused2.run_id, "--by", "cli"]
        )
        assert res.exit_code == 0, res.output
        assert "accepted" in res.output
    finally:
        w.stop()
        srv.stop()
