"""Find installed skill bundles."""

from __future__ import annotations

from pathlib import Path

from hawser.errors import PlanInvalid
from hawser.models import SkillRef
from hawser.skills.bundle import MANIFEST, Skill, load_bundle


class SkillRegistry:
    def __init__(self, search_paths: list[Path] | None = None, *, cwd: Path | None = None) -> None:
        cwd = cwd or Path.cwd()
        default = [cwd / "skills", Path.home() / ".hawser" / "skills"]
        self.search_paths = [Path(p) for p in (search_paths or [])] + default
        self._cache: dict[tuple[str, str], Skill] = {}

    def discover(self) -> list[Path]:
        found: list[Path] = []
        for base in self.search_paths:
            if not base.is_dir():
                continue
            for p in sorted(base.iterdir()):
                if (p / MANIFEST).exists():
                    found.append(p)
        return found

    def list(self) -> list[Skill]:
        out: list[Skill] = []
        for p in self.discover():
            try:
                out.append(load_bundle(p))
            except PlanInvalid:
                continue
        return out

    def resolve(self, ref: SkillRef | str) -> Skill:
        name, version = (ref, None) if isinstance(ref, str) else (ref.name, ref.version)
        candidates = [
            s for s in self.list() if s.name == name and (version is None or s.version == version)
        ]
        if not candidates:
            paths = ", ".join(str(p) for p in self.search_paths)
            raise PlanInvalid(
                f"skill {name!r}{'@' + version if version else ''} not found (searched {paths})",
                code="skill_not_found",
            )
        candidates.sort(
            key=lambda s: tuple(int(x) for x in s.version.split("-")[0].split(".")), reverse=True
        )
        return candidates[0]


__all__ = ["SkillRegistry"]
