"""Export contract models to JSON Schema (ADR-0004).

Usage::

    python -m hawser.models.export --out api/schemas          # write
    python -m hawser.models.export --out api/schemas --check  # fail if committed files differ

Each file is ``<Name>.schema.json`` with ``x-hawser-contract-version`` injected at the top
level. Output is deterministic (sorted keys, 2-space indent, trailing newline).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from hawser.models import (
    CONTRACT_VERSION,
    Approval,
    Artifact,
    Asset,
    AssetManifest,
    Event,
    Evidence,
    InputRef,
    Issue,
    ModelSpec,
    PlanEnvelope,
    Policy,
    Review,
    Revision,
    RunRequest,
    RunResult,
    RunSummary,
    SkillManifest,
    Step,
    ToolSpec,
    Transform,
    Usage,
    ValidationReport,
)
from hawser.plan.ir import Plan

#: Name -> type. Order is irrelevant; the file set is what matters.
EXPORTED: dict[str, Any] = {
    "Approval": Approval,
    "Artifact": Artifact,
    "Asset": Asset,
    "AssetManifest": AssetManifest,
    "Event": Event,
    "Evidence": Evidence,
    "InputRef": InputRef,
    "Issue": Issue,
    "ModelSpec": ModelSpec,
    "Plan": Plan,
    "PlanEnvelope": PlanEnvelope,
    "Policy": Policy,
    "Review": Review,
    "Revision": Revision,
    "RunRequest": RunRequest,
    "RunResult": RunResult,
    "RunSummary": RunSummary,
    "SkillManifest": SkillManifest,
    "Step": Step,
    "ToolSpec": ToolSpec,
    "Transform": Transform,
    "Usage": Usage,
    "ValidationReport": ValidationReport,
}


def schema_for(name: str) -> dict[str, Any]:
    schema = TypeAdapter(EXPORTED[name]).json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://hawser.dev/schemas/{CONTRACT_VERSION}/{name}.schema.json"
    schema.setdefault("title", name)
    schema["x-hawser-contract-version"] = CONTRACT_VERSION
    return schema


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_all() -> dict[str, str]:
    """Return ``{filename: rendered json}`` for every exported model."""
    return {f"{name}.schema.json": render(schema_for(name)) for name in EXPORTED}


def write(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in export_all().items():
        path = out / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check(out: Path) -> list[str]:
    """Return a list of problems (empty when the committed schemas are current)."""
    problems: list[str] = []
    expected = export_all()
    for filename, text in expected.items():
        path = out / filename
        if not path.exists():
            problems.append(f"missing: {path}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"stale: {path}")
    for path in sorted(out.glob("*.schema.json")):
        if path.name not in expected:
            problems.append(f"unexpected: {path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hawser.models.export")
    parser.add_argument("--out", type=Path, default=Path("api/schemas"))
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.out)
        for p in problems:
            print(p, file=sys.stderr)
        if problems:
            print("schemas are out of date; run `python -m hawser.models.export`", file=sys.stderr)
            return 1
        print(f"{len(EXPORTED)} schemas up to date in {args.out}")
        return 0
    written = write(args.out)
    print(f"wrote {len(written)} schemas to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
