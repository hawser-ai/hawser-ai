"""Choose the execution path for a run: direct, a skill's workflow, or agent planning."""

from __future__ import annotations

from hawser.models import ExecutionMode, RunResult
from hawser.runtime.agent import run_agent
from hawser.runtime.context import RunContext
from hawser.runtime.direct import run_direct
from hawser.runtime.workflow import run_workflow
from hawser.skills.registry import SkillRegistry


def execute(ctx: RunContext, skills: SkillRegistry) -> RunResult:
    req = ctx.request
    if req.skill is None:
        if req.mode == ExecutionMode.agent:
            return run_agent(ctx, skills)
        return run_direct(ctx)
    skill = skills.resolve(req.skill)
    ctx.skill = skill
    mode = req.mode or skill.manifest.mode
    if mode == ExecutionMode.agent:
        # The skill supplies goal, inputs, output schema, tool allow-list and validators;
        # the planner decides the steps instead of workflow.py (the three-mode comparison).
        return run_agent(ctx, skills)
    if mode == ExecutionMode.workflow:
        return run_workflow(ctx, skill)
    # Direct mode with a skill: one extraction straight into the skill's output schema.
    if req.output_schema is None:
        ctx.request = req.model_copy(
            update={"output_schema": skill.output_schema, "mode": ExecutionMode.direct}
        )
    if not ctx.request.instruction:
        ctx.request = ctx.request.model_copy(
            update={"instruction": skill.manifest.instruction or skill.manifest.description}
        )
    return run_direct(ctx)


__all__ = ["execute"]
