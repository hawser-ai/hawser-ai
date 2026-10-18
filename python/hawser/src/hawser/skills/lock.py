"""``hawser.lock``: pinned skill versions and bundle hashes, plus the permissions they held."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from hawser.clock import now
from hawser.skills.bundle import Skill

LOCK_FILENAME = "hawser.lock"


class LockedSkill(BaseModel):
    version: str
    hash: str
    tools: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    installed_at: str


class Lockfile(BaseModel):
    skills: dict[str, LockedSkill] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Lockfile:
        path = path or Path(LOCK_FILENAME)
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path | None = None) -> Path:
        path = path or Path(LOCK_FILENAME)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def diff(self, skill: Skill) -> dict[str, Any]:
        """What installing ``skill`` changes: version, hash, widened or narrowed permissions."""
        prev = self.skills.get(skill.name)
        tools = sorted(skill.manifest.tools)
        models = sorted(skill.manifest.models.allowed or [])
        if prev is None:
            return {
                "new": True,
                "tools_added": tools,
                "tools_removed": [],
                "models_added": models,
                "models_removed": [],
            }
        return {
            "new": False,
            "version_from": prev.version,
            "version_to": skill.version,
            "hash_changed": prev.hash != skill.bundle_hash,
            "tools_added": sorted(set(tools) - set(prev.tools)),
            "tools_removed": sorted(set(prev.tools) - set(tools)),
            "models_added": sorted(set(models) - set(prev.models)),
            "models_removed": sorted(set(prev.models) - set(models)),
        }

    def widens(self, skill: Skill) -> bool:
        d = self.diff(skill)
        return bool(d["tools_added"] or d["models_added"]) and not d.get("new", False)

    def record(self, skill: Skill) -> None:
        self.skills[skill.name] = LockedSkill(
            version=skill.version,
            hash=skill.bundle_hash,
            tools=sorted(skill.manifest.tools),
            models=sorted(skill.manifest.models.allowed or []),
            installed_at=now().isoformat(),
        )

    def verify(self, skill: Skill) -> str | None:
        """Return a problem description when the installed bundle differs from the lock."""
        prev = self.skills.get(skill.name)
        if prev is None:
            return None
        if prev.version != skill.version:
            return f"locked version {prev.version} but found {skill.version}"
        if prev.hash != skill.bundle_hash:
            return "bundle contents changed since it was locked"
        return None


__all__ = ["LOCK_FILENAME", "LockedSkill", "Lockfile"]
