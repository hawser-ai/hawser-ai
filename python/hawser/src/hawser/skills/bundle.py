"""Load a skill bundle directory into a :class:`Skill`.

Bundle layout::

    skill.yaml          manifest (SkillManifest)
    schemas.py          Pydantic models referenced as ``schemas.py:ClassName``   (or *.json)
    workflow.py         ``def run(ctx: WorkflowContext) -> dict`` for mode: workflow
    validators.py       ``VALIDATORS = [...]`` and/or functions decorated with @validator
    prompts/*.md        prompt files referenced from the manifest
    examples/           sample inputs + expected outputs
    evals/              dataset references
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from hawser.errors import PlanInvalid
from hawser.models import SkillManifest
from hawser.models.common import JsonSchema
from hawser.structured import inline_refs, to_json_schema
from hawser.validation.core import Validator

MANIFEST = "skill.yaml"
_IGNORED = {"__pycache__", ".DS_Store", "hawser.lock"}


@dataclass
class Skill:
    manifest: SkillManifest
    path: Path
    output_schema: JsonSchema
    bundle_hash: str
    run: Callable[..., Any] | None = None
    validators: dict[str, Validator] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    #: Pydantic classes exported by schemas.py (name -> class), when present.
    models: dict[str, type[BaseModel]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    def prompt(self, key: str) -> str | None:
        return self.prompts.get(key)


def bundle_hash(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_dir() or any(part in _IGNORED for part in p.parts):
            continue
        h.update(str(p.relative_to(path)).encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _import(path: Path, qualname: str, siblings: dict[str, ModuleType] | None = None) -> ModuleType:
    """Import a bundle file; ``siblings`` lets ``from schemas import X`` find bundle modules."""
    spec = importlib.util.spec_from_file_location(qualname, path)
    if spec is None or spec.loader is None:
        raise PlanInvalid(f"cannot import {path}", code="skill_module_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    saved = {name: sys.modules.get(name) for name in (siblings or {})}
    for name, mod in (siblings or {}).items():
        sys.modules[name] = mod
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PlanInvalid(
            f"error importing {path.name}: {exc}", code="skill_module_invalid"
        ) from exc
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return module


def _resolve_schema(
    ref: JsonSchema | str, path: Path, modules: dict[str, ModuleType], qual: str
) -> tuple[JsonSchema, type[BaseModel] | None]:
    if isinstance(ref, dict):
        return inline_refs(ref), None
    if ":" in ref:
        file, _, attr = ref.partition(":")
        module = modules.get(file)
        if module is None:
            module = _import(path / file, f"{qual}.{Path(file).stem}")
            modules[file] = module
        cls = getattr(module, attr, None)
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            raise PlanInvalid(f"{ref} is not a Pydantic model", code="skill_schema_invalid")
        return inline_refs(to_json_schema(cls)), cls
    target = path / ref
    if not target.exists():
        raise PlanInvalid(f"schema file not found: {target}", code="skill_schema_invalid")
    return inline_refs(json.loads(target.read_text(encoding="utf-8"))), None


def load_bundle(path: Path) -> Skill:  # noqa: PLR0912
    path = Path(path).resolve()
    manifest_path = path / MANIFEST
    if not manifest_path.exists():
        raise PlanInvalid(f"no {MANIFEST} in {path}", code="skill_not_found")
    try:
        manifest = SkillManifest.model_validate(
            yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        )
    except ValidationError as exc:
        raise PlanInvalid(f"invalid {MANIFEST}: {exc}", code="skill_manifest_invalid") from exc

    slug = manifest.name.replace("-", "_").replace(".", "_")
    qual = f"hawser_skill_{slug}_{manifest.version.replace('.', '_')}"
    modules: dict[str, ModuleType] = {}
    output_schema, output_model = _resolve_schema(manifest.output_schema, path, modules, qual)

    skill = Skill(
        manifest=manifest, path=path, output_schema=output_schema, bundle_hash=bundle_hash(path)
    )
    if output_model is not None:
        skill.models[output_model.__name__] = output_model
    schemas_py = path / "schemas.py"
    if schemas_py.exists():
        module = modules.get("schemas.py") or _import(schemas_py, f"{qual}.schemas")
        modules["schemas.py"] = module
        for name, obj in vars(module).items():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                skill.models[name] = obj

    workflow_py = path / "workflow.py"
    if manifest.mode.value == "workflow":
        if not workflow_py.exists():
            raise PlanInvalid("mode: workflow requires workflow.py", code="skill_workflow_missing")
        siblings = {Path(k).stem: v for k, v in modules.items()}
        module = _import(workflow_py, f"{qual}.workflow", siblings)
        run = getattr(module, "run", None)
        if not callable(run):
            raise PlanInvalid("workflow.py must define run(ctx)", code="skill_workflow_invalid")
        skill.run = run

    validators_py = path / "validators.py"
    if validators_py.exists():
        siblings = {Path(k).stem: v for k, v in modules.items()}
        module = _import(validators_py, f"{qual}.validators", siblings)
        for v in getattr(module, "VALIDATORS", []):
            skill.validators[v.id] = v
        for obj in vars(module).values():
            vid = getattr(obj, "id", None)
            if (
                isinstance(vid, str)
                and callable(getattr(obj, "validate", None))
                and not isinstance(obj, type)
            ):
                skill.validators[vid] = obj

    for key, rel in manifest.prompts.items():
        p = path / rel
        if not p.exists():
            raise PlanInvalid(f"prompt file not found: {p}", code="skill_prompt_missing")
        skill.prompts[key] = p.read_text(encoding="utf-8")
    return skill


__all__ = ["MANIFEST", "Skill", "bundle_hash", "load_bundle"]
