"""The Python SDK surface (blueprint §2): ``hawser.run(...)`` and ``Client``."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hawser.config import HawserConfig
from hawser.models import (
    Event,
    ExecutionMode,
    InputRef,
    ModelPreference,
    PathInput,
    Policy,
    RunRequest,
    RunResult,
    RunSummary,
    SkillRef,
)
from hawser.runtime.base import Runtime
from hawser.structured import to_json_schema

InputLike = str | Path | InputRef


def _input(value: InputLike) -> InputRef:
    if isinstance(value, str | Path):
        p = Path(value)
        return PathInput(path=str(p), name=p.name)
    return value


class Client:
    def __init__(
        self, runtime: Runtime | None = None, *, config: HawserConfig | None = None
    ) -> None:
        if runtime is None:
            from hawser.runtime.local import LocalRuntime  # noqa: PLC0415 - avoid import cycle at module load

            runtime = LocalRuntime(config)
        self.runtime = runtime

    def run(
        self,
        instruction: str | None = None,
        *,
        inputs: Sequence[InputLike] = (),
        output_schema: type[BaseModel] | dict[str, Any] | None = None,
        policy: Policy | dict[str, Any] | None = None,
        mode: ExecutionMode | str | None = None,
        skill: SkillRef | str | None = None,
        models: ModelPreference | str | None = None,
        tools: Sequence[str] | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> RunResult:
        request = self.build_request(
            instruction,
            inputs=inputs,
            output_schema=output_schema,
            policy=policy,
            mode=mode,
            skill=skill,
            models=models,
            tools=tools,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
        return self.runtime.submit(request).result()

    @staticmethod
    def build_request(
        instruction: str | None = None,
        *,
        inputs: Sequence[InputLike] = (),
        output_schema: type[BaseModel] | dict[str, Any] | None = None,
        policy: Policy | dict[str, Any] | None = None,
        mode: ExecutionMode | str | None = None,
        skill: SkillRef | str | None = None,
        models: ModelPreference | str | None = None,
        tools: Sequence[str] | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> RunRequest:
        return RunRequest(
            instruction=instruction,
            inputs=[_input(i) for i in inputs],
            output_schema=None if output_schema is None else to_json_schema(output_schema),
            skill=SkillRef(name=skill) if isinstance(skill, str) else skill,
            mode=ExecutionMode(mode) if isinstance(mode, str) else mode,
            policy=Policy.model_validate(policy)
            if isinstance(policy, dict)
            else (policy or Policy()),
            tools=list(tools) if tools is not None else None,
            models=ModelPreference(pinned=models) if isinstance(models, str) else models,
            metadata=dict(metadata or {}),
            idempotency_key=idempotency_key,
        )

    def get(self, run_id: str) -> RunResult | None:
        return self.runtime.get(run_id)

    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]:
        return self.runtime.events(run_id, after_seq=after_seq)

    def list(self, *, limit: int = 50) -> list[RunSummary]:
        return self.runtime.list(limit=limit)


_default: Client | None = None


def default_client() -> Client:
    global _default  # noqa: PLW0603 - module-level singleton by design
    if _default is None:
        _default = Client()
    return _default


def run(instruction: str | None = None, **kwargs: Any) -> RunResult:
    """Convenience: ``hawser.run("...", inputs=[...], output_schema=Model)``."""
    return default_client().run(instruction, **kwargs)


__all__ = ["Client", "default_client", "run"]
