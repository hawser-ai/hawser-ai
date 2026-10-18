from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.errors import ApprovalRequired, PolicyViolation, ToolError
from hawser.gateway import Gateway, load_registry
from hawser.models import ApprovalStatus, EventType, Policy, RunStatus, SideEffect
from hawser.models.policy import ApprovalPolicy
from hawser.models.tool import ToolLimits
from hawser.runtime.local import LocalRuntime
from hawser.testing import StaticAdapter
from hawser.tools import Broker, registry, tool
from hawser.tools.external import set_http_client
from hawser.tools.search import BM25, search_indexes, tokenize

# -- test tools (registered at import) ---------------------------------------------------------


@tool(
    "test.echo",
    description="echo",
    input_schema={
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    },
)
def echo(x: int) -> dict[str, Any]:
    return {"x": x, "leak": "stripped"}


@tool("test.slow", description="sleeps")
def slow(seconds: float) -> dict[str, Any]:
    time.sleep(seconds)
    return {"slept": seconds}


registry.get("test.slow").spec.limits = ToolLimits(wall_s=0.2)


@tool("test.ledger_post", description="external write", side_effect=SideEffect.external_write)
def ledger_post(amount: float, action_id: str = "") -> dict[str, Any]:
    return {"posted": amount, "action_id": action_id}


# -- a skill that posts to the ledger ----------------------------------------------------------

SKILL_YAML = """name: ledger-post
version: 0.1.0
description: Extract a title then post an amount.
mode: workflow
inputs:
  document:
    type: document
output_schema: schemas.py:Out
tools: [document.extract, test.ledger_post, test.echo]
"""
SCHEMAS = (
    "from pydantic import BaseModel\n\n"
    "class Out(BaseModel):\n    title: str\n    posted: float | None = None\n"
)
WORKFLOW = """from hawser.workflow import WorkflowContext
from schemas import Out

def run(ctx: WorkflowContext) -> dict:
    doc = ctx.extract("document", Out)
    ctx.attach("", doc)
    posted = ctx.tool("test.ledger_post", amount=42.0, action_id="ledger-1")
    echoed = ctx.tool("test.echo", x=1)
    return {"title": (doc.result or {}).get("title"), "posted": posted["posted"] + echoed["x"] - 1}
"""


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills" / "ledger-post"
    d.mkdir(parents=True)
    (d / "skill.yaml").write_text(SKILL_YAML)
    (d / "schemas.py").write_text(SCHEMAS)
    (d / "workflow.py").write_text(WORKFLOW)
    return tmp_path / "skills"


def _rt(tmp_path: Path, skill_dir: Path) -> LocalRuntime:
    env = {
        "title": {"value": "Doc", "source_text": "Doc", "found": True},
        "posted": {"value": 0.0, "source_text": "", "found": False},
    }
    cfg = HawserConfig(home=tmp_path / "home", record_mode=RecordMode.live, skill_paths=[skill_dir])
    return LocalRuntime(cfg, gateway=Gateway(load_registry(), {"anthropic": StaticAdapter(env)}))


def _doc(tmp_path: Path) -> Path:
    p = tmp_path / "document.txt"
    p.write_text("Doc\n")
    return p


def test_approval_pauses_and_resume_completes(tmp_path: Path, skill_dir: Path) -> None:
    rt = _rt(tmp_path, skill_dir)
    client = Client(rt)
    paused = client.run(skill="ledger-post", inputs=[_doc(tmp_path)])
    assert paused.status == RunStatus.awaiting_approval
    assert any(w.code == "approval_pending" for w in paused.warnings)
    pending = rt.approvals.list(run_id=paused.run_id, status=ApprovalStatus.pending)
    assert len(pending) == 1
    assert pending[0].tool == "test.ledger_post"
    assert pending[0].action_id == "ledger-1"
    assert pending[0].payload == {"amount": 42.0}  # the action id is its own field
    types = [e.type for e in rt.events(paused.run_id)]
    assert EventType.approval_requested in types
    calls_before = rt.get(paused.run_id).usage.totals.model_calls  # type: ignore[union-attr]

    rt.approvals.decide(pending[0].id, approve=True, by="chetan", comment="ok")
    result = rt.resume(paused.run_id).result()
    assert result.status == RunStatus.accepted
    assert result.result == {"title": "Doc", "posted": 42.0}
    # the model call was not repeated: extraction replayed from its checkpoint
    assert result.usage.totals.model_calls == calls_before == 1
    types = [e.type for e in rt.events(paused.run_id)]
    assert EventType.approval_decided in types
    assert types.count(EventType.model_called) == 1
    assert types.count(EventType.step_skipped) >= 1
    tool_events = [e for e in rt.events(paused.run_id) if e.type == EventType.tool_called]
    assert {e.payload["tool"] for e in tool_events} >= {"test.ledger_post", "test.echo"}
    assert result.usage.totals.tool_calls == 2


def test_rejected_action_fails_the_run(tmp_path: Path, skill_dir: Path) -> None:
    rt = _rt(tmp_path, skill_dir)
    paused = Client(rt).run(skill="ledger-post", inputs=[_doc(tmp_path)])
    pending = rt.approvals.list(run_id=paused.run_id)[0]
    rt.approvals.decide(pending.id, approve=False, by="chetan")
    result = rt.resume(paused.run_id).result()
    assert result.status == RunStatus.failed
    assert result.error is not None and result.error["code"] == "action_rejected"
    with pytest.raises(PolicyViolation):
        rt.resume(paused.run_id)  # not awaiting_approval any more


def test_pre_approved_pattern_skips_the_pause(tmp_path: Path, skill_dir: Path) -> None:
    rt = _rt(tmp_path, skill_dir)
    result = Client(rt).run(
        skill="ledger-post",
        inputs=[_doc(tmp_path)],
        policy=Policy(approvals=ApprovalPolicy(pre_approved=["test.ledger_post:ledger-*"])),
    )
    assert result.status == RunStatus.accepted
    assert rt.approvals.list(run_id=result.run_id) == []


def test_broker_permissions_validation_limits(tmp_path: Path, skill_dir: Path) -> None:
    rt = _rt(tmp_path, skill_dir)
    from hawser.clock import now
    from hawser.ids import new_id
    from hawser.models import RunRequest, Usage
    from hawser.runtime.context import RunContext
    from hawser.runtime.events import EventEmitter
    from hawser.runtime.steps import StepExecutor
    from hawser.storage import RunRecord

    run = RunRecord(
        id=new_id("run"),
        tenant_id=rt.config.tenant_id,
        status=RunStatus.running,
        request=RunRequest(instruction="x", tools=["test.*"]),
        trace_id="t",
        created_at=now(),
        updated_at=now(),
    )
    rt.meta.put_run(run)
    emitter = EventEmitter(rt.meta, run_id=run.id, tenant_id=run.tenant_id)
    usage = Usage()
    ctx = RunContext(
        config=rt.config,
        run=run,
        request=run.request,
        meta=rt.meta,
        artifacts=rt.artifacts,
        gateway=rt.gateway,
        emitter=emitter,
        steps=StepExecutor(rt.meta, rt.artifacts, emitter, run_id=run.id, usage=usage),
        usage=usage,
    )
    broker = Broker(ctx)

    out = broker.call("test.echo", {"x": 3})
    assert out.output == {"x": 3}  # extra key stripped
    assert out.usage.tool == "test.echo"
    with pytest.raises(ToolError) as exc:
        broker.call("test.echo", {"x": "nope"})
    assert exc.value.code == "tool_input_invalid"
    with pytest.raises(PolicyViolation) as exc2:
        broker.call(
            "compute.align_line_items", {"left": [], "right": []}
        )  # request allows test.* only
    assert exc2.value.code == "tool_not_allowed"
    with pytest.raises(ToolError) as exc3:
        broker.call("test.slow", {"seconds": 2})
    assert exc3.value.code == "tool_timeout"
    with pytest.raises(ApprovalRequired):
        broker.call("test.ledger_post", {"amount": 1.0})
    assert len(usage.tool_calls) == 1  # only the successful call settled


def test_http_post_tool_sends_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 7})

    set_http_client(httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        from hawser.tools.external import http_post

        out = http_post("https://erp.example.com/invoices", {"total": 1}, action_id="act-1")
        assert out == {"status": 201, "action_id": "act-1", "body": {"id": 7}}
        assert seen[0].headers["Idempotency-Key"] == "act-1"
        assert json.loads(seen[0].content) == {"total": 1}
        set_http_client(
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")))
        )
        with pytest.raises(ToolError) as exc:
            http_post("https://erp.example.com/x", {}, action_id="a")
        assert exc.value.code == "http_503" and exc.value.retryable
    finally:
        set_http_client(None)


def test_bm25_search(invoice: Any) -> None:
    from hawser.evidence import build_document_index
    from hawser_media import pdf

    idx = build_document_index(
        "asset_01J9X3K4N7Q2M8R5T6V7W8X9Y0", pdf.extract_text(invoice.pdf), name="invoice.pdf"
    )
    hits = search_indexes([idx], f"invoice {invoice.truth.invoice_number}")
    assert hits and hits[0]["page"] == 1
    assert invoice.truth.invoice_number.lower() in hits[0]["snippet"].lower()
    bm = BM25([tokenize("red apple pie"), tokenize("green apple"), tokenize("blue sky")])
    assert bm.top(tokenize("apple pie"), 2)[0][0] == 0
    assert search_indexes([idx], "zzzz qqqq") == []
