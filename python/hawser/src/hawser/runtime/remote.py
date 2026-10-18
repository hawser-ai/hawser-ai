"""The HTTP backend of the SDK: the same ``Runtime`` protocol against ``hawserd`` (ADR-0010)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from hawser.errors import HawserError
from hawser.models import (
    PAUSED_STATUSES,
    RESTING_STATUSES,
    TERMINAL_STATUSES,
    Approval,
    ApprovalStatus,
    Asset,
    AssetInput,
    Event,
    PathInput,
    Revision,
    RunRequest,
    RunResult,
    RunSummary,
    Step,
)
from hawser.models.review import Review, ReviewStatus
from hawser.runtime.base import RunHandle

RESTED = TERMINAL_STATUSES | RESTING_STATUSES | PAUSED_STATUSES


class RemoteError(HawserError):
    """An API error the server did not map to a known taxonomy class."""

    default_code = "remote_error"


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}
    code = str(body.get("code") or f"http_{resp.status_code}")
    message = str(body.get("detail") or resp.text or resp.reason_phrase)
    raw_details = body.get("details")
    details: dict[str, Any] = dict(raw_details) if isinstance(raw_details, dict) else {}
    error_type = str(body.get("error_type") or "")
    if error_type:
        raise HawserError.from_dict(
            {"type": error_type, "code": code, "message": message, "details": details}
        )
    raise RemoteError(message, code=code, details={**details, "status": resp.status_code})


def _parse_sse(lines: Iterator[str]) -> Iterator[tuple[str | None, str | None, str]]:
    """Yield ``(id, event, data)`` per SSE message."""
    ev_id: str | None = None
    ev_type: str | None = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield ev_id, ev_type, "\n".join(data)
            ev_id, ev_type, data = None, None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            ev_id = value
        elif field == "event":
            ev_type = value
        elif field == "data":
            data.append(value)
    if data:
        yield ev_id, ev_type, "\n".join(data)


class RemoteRuntime:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        poll_s: float = 0.5,
    ) -> None:
        from hawser import __version__  # noqa: PLC0415 - avoid import cycle at module load

        self.base_url = base_url.rstrip("/")
        self.poll_s = poll_s
        self.http = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"hawser-sdk/{__version__}",
            },
            timeout=timeout,
            transport=transport,
        )
        self.reviews = RemoteReviews(self)
        self.approvals = RemoteApprovals(self)

    # -- http ------------------------------------------------------------------------------
    def get_json(self, path: str, **params: Any) -> Any:
        resp = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        _raise_for(resp)
        return resp.json()

    def post_json(
        self, path: str, body: Any = None, *, headers: dict[str, str] | None = None
    ) -> Any:
        resp = self.http.post(path, json=body, headers=headers)
        _raise_for(resp)
        return resp.json() if resp.content else None

    # -- assets ----------------------------------------------------------------------------
    def upload(self, path: str | Path, *, name: str | None = None) -> Asset:
        p = Path(path).expanduser()
        with p.open("rb") as fh:
            resp = self.http.post(
                "/v1/assets",
                files={"file": (name or p.name, fh)},
                data={"name": name or p.name},
            )
        _raise_for(resp)
        return Asset.model_validate(resp.json())

    # -- Runtime protocol ------------------------------------------------------------------
    def submit(self, request: RunRequest) -> RunHandle:
        inputs: list[Any] = []
        for ref in request.inputs:
            if isinstance(ref, PathInput):
                asset = self.upload(ref.path, name=ref.name)
                inputs.append(AssetInput(asset_id=asset.id, name=ref.name or asset.name))
            else:
                inputs.append(ref)
        req = request.model_copy(update={"inputs": inputs})
        headers = {"Idempotency-Key": req.idempotency_key} if req.idempotency_key else None
        data = self.post_json("/v1/runs", json.loads(req.model_dump_json()), headers=headers)
        return RunHandle(self, str(data["id"]))

    def summary(self, run_id: str) -> RunSummary:
        return RunSummary.model_validate(self.get_json(f"/v1/runs/{run_id}"))

    def get(self, run_id: str) -> RunResult | None:
        resp = self.http.get(f"/v1/runs/{run_id}/result")
        if resp.status_code == 404:
            return None
        _raise_for(resp)
        return RunResult.model_validate(resp.json())

    def wait(self, run_id: str, *, timeout: float | None = None) -> RunResult:
        deadline = None if timeout is None else time.monotonic() + timeout
        for _ in self.stream_events(run_id):
            if deadline is not None and time.monotonic() > deadline:
                break
        while True:
            result = self.get(run_id)
            if result is not None and result.status in RESTED:
                return result
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"run {run_id} did not rest within {timeout}s")
            time.sleep(self.poll_s)

    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]:
        for item in self.get_json(f"/v1/runs/{run_id}/events", after_seq=after_seq):
            yield Event.model_validate(item)

    def stream_events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]:
        """Follow the run's events over SSE until the server closes the stream."""
        headers = {"Accept": "text/event-stream"}
        if after_seq:
            headers["Last-Event-ID"] = str(after_seq)
        with self.http.stream(
            "GET", f"/v1/runs/{run_id}/events", headers=headers, timeout=None
        ) as resp:
            _raise_for(resp)
            for _, ev_type, data in _parse_sse(resp.iter_lines()):
                if ev_type == "stream.end":
                    return
                yield Event.model_validate_json(data)

    def steps(self, run_id: str) -> list[Step]:
        return [Step.model_validate(s) for s in self.get_json(f"/v1/runs/{run_id}/steps")]

    def list(self, *, limit: int = 50) -> list[RunSummary]:
        page = self.get_json("/v1/runs", limit=limit)
        return [RunSummary.model_validate(r) for r in page["items"]]

    def cancel(self, run_id: str) -> bool:
        return bool(self.post_json(f"/v1/runs/{run_id}/cancel")["cancelled"])

    def resume(self, run_id: str) -> RunHandle:
        self.post_json(f"/v1/runs/{run_id}/resume")
        return RunHandle(self, run_id)

    def close(self) -> None:
        self.http.close()


class RemoteReviews:
    """The ``ReviewService`` surface over HTTP (what the CLI uses)."""

    def __init__(self, rt: RemoteRuntime) -> None:
        self.rt = rt

    def get(self, run_id: str) -> Review | None:
        resp = self.rt.http.get(f"/v1/reviews/{run_id}")
        if resp.status_code == 404:
            return None
        _raise_for(resp)
        return Review.model_validate(resp.json())

    def list(self, *, status: ReviewStatus | None = None) -> list[Review]:
        items = self.rt.get_json("/v1/reviews", status=status.value if status else None)
        return [Review.model_validate(r) for r in items]

    def revisions(self, run_id: str) -> list[Revision]:
        items = self.rt.get_json(f"/v1/runs/{run_id}/revisions")
        return [Revision.model_validate(r) for r in items]

    def latest_revision(self, run_id: str) -> Revision | None:
        revs = self.revisions(run_id)
        return revs[-1] if revs else None

    def current_result(self, run_id: str) -> Any:
        return self.rt.get_json(f"/v1/reviews/{run_id}/current")["result"]

    def open(self, run_id: str, *, assigned_to: str | None = None) -> Review:
        data = self.rt.post_json(f"/v1/reviews/{run_id}/open", {"assigned_to": assigned_to})
        return Review.model_validate(data)

    def edit(
        self,
        run_id: str,
        changes: dict[str, Any],
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Review:
        headers = {"If-Match": f'"{expected_version}"'} if expected_version is not None else None
        data = self.rt.post_json(
            f"/v1/reviews/{run_id}/edit",
            {"changes": changes, "reviewer": reviewer, "comment": comment},
            headers=headers,
        )
        return Review.model_validate(data)

    def _decide(
        self,
        run_id: str,
        verb: str,
        *,
        reviewer: str,
        comment: str | None,
        expected_version: int | None,
    ) -> Revision:
        headers = {"If-Match": f'"{expected_version}"'} if expected_version is not None else None
        data = self.rt.post_json(
            f"/v1/reviews/{run_id}/{verb}",
            {"reviewer": reviewer, "comment": comment},
            headers=headers,
        )
        return Revision.model_validate(data)

    def approve(
        self,
        run_id: str,
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Revision:
        return self._decide(
            run_id, "approve", reviewer=reviewer, comment=comment, expected_version=expected_version
        )

    def reject(
        self,
        run_id: str,
        *,
        reviewer: str = "local",
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Revision:
        return self._decide(
            run_id, "reject", reviewer=reviewer, comment=comment, expected_version=expected_version
        )


class RemoteApprovals:
    def __init__(self, rt: RemoteRuntime) -> None:
        self.rt = rt

    def list(
        self, *, run_id: str | None = None, status: ApprovalStatus | None = None
    ) -> list[Approval]:
        items = self.rt.get_json(
            "/v1/approvals", run_id=run_id, status=status.value if status else "all"
        )
        return [Approval.model_validate(a) for a in items]

    def decide(
        self,
        approval_id: str,
        *,
        approve: bool,
        by: str = "local",
        comment: str | None = None,
        resume: bool = True,
    ) -> Approval:
        data = self.rt.post_json(
            f"/v1/approvals/{approval_id}/decide",
            {"approve": approve, "by": by, "comment": comment, "resume": resume},
        )
        return Approval.model_validate(data["approval"])


__all__ = ["RemoteApprovals", "RemoteError", "RemoteReviews", "RemoteRuntime"]
