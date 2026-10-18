"""The Plan IR (blueprint §10.1).

A plan is a list of nodes executed in order at the top level; container nodes (``branch``,
``loop``, ``parallel``) reference other nodes by id and execute them themselves. Values flow
through **bindings**: ``"$input.invoice"`` (an input asset), ``"$node.extract"`` (a node's whole
output) or ``"$node.extract.total"`` (a path into it; list indexes as ``[0]``).

The IR is deliberately flat (one ``Node`` model with optional fields per kind) so it can be
produced by a model as plain JSON and validated here, without relying on provider-enforced
schemas.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from hawser.models.base import HawserModel
from hawser.models.common import JsonSchema, Slug
from hawser.models.skill import ValidatorConfig


class NodeKind(StrEnum):
    call_tool = "call_tool"
    call_model = "call_model"
    compute = "compute"
    branch = "branch"
    loop = "loop"
    parallel = "parallel"
    validate = "validate"
    review = "review"
    repair = "repair"
    exit = "exit"


class ContextSpec(HawserModel):
    """What a ``call_model`` node may see (blueprint §13)."""

    #: Input names to draw pages from; empty = all inputs.
    inputs: list[str] = Field(default_factory=list)
    #: ``all`` pages, ``matching`` a query (BM25), or an explicit list of 1-based pages.
    pages: Literal["all", "matching"] | list[int] = "all"
    query: str | None = None
    #: Prior node outputs to include as compact JSON summaries.
    include_nodes: list[str] = Field(default_factory=list)
    max_tokens: int | None = Field(default=None, ge=500)


class Node(HawserModel):
    id: Slug
    kind: NodeKind
    description: str = ""
    # call_tool
    tool: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    action_id: str | None = None
    # call_model
    instruction: str | None = None
    output_schema: JsonSchema | None = None
    context: ContextSpec = ContextSpec()
    # compute / branch / loop
    expression: str | None = None
    condition: str | None = None
    then: list[str] = Field(default_factory=list)
    otherwise: list[str] = Field(default_factory=list, alias="else")
    body: list[str] = Field(default_factory=list)
    max_iterations: int | None = Field(default=None, ge=1)
    until: str | None = None
    # parallel
    branches: list[list[str]] = Field(default_factory=list)
    max_parallel: int | None = Field(default=None, ge=1)
    # validate / repair
    target: str | None = None
    validators: list[str | ValidatorConfig] = Field(default_factory=list)
    max_attempts: int | None = Field(default=None, ge=0)
    # review
    reason: str | None = None
    # exit
    status: Literal["success", "failure"] | None = None
    result: Any = None

    @model_validator(mode="after")
    def _shape(self) -> Node:
        k = self.kind
        need = {
            NodeKind.call_tool: ["tool"],
            NodeKind.call_model: ["instruction"],
            NodeKind.compute: ["expression"],
            NodeKind.branch: ["condition"],
            NodeKind.loop: ["body", "max_iterations"],
            NodeKind.parallel: ["branches"],
            NodeKind.validate: ["target"],
            NodeKind.review: ["reason"],
            NodeKind.repair: ["target"],
            NodeKind.exit: ["status"],
        }[k]
        for field in need:
            if not getattr(self, field):
                raise ValueError(f"node {self.id!r} of kind {k.value} needs {field!r}")
        return self


class Plan(HawserModel):
    goal: str
    inputs: list[str] = Field(default_factory=list)
    nodes: list[Node]
    #: Free-text rationale from the planner; never executed.
    notes: str = ""

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def contained_ids(self) -> set[str]:
        """Ids executed by a container rather than at the top level."""
        out: set[str] = set()
        for n in self.nodes:
            out.update(n.then, n.otherwise, n.body)
            for br in n.branches:
                out.update(br)
        return out

    def top_level(self) -> list[Node]:
        contained = self.contained_ids()
        return [n for n in self.nodes if n.id not in contained]


__all__ = ["ContextSpec", "Node", "NodeKind", "Plan"]
