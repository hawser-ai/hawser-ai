"""Contract drift check: committed api/schemas must match what the models export."""

import json
from pathlib import Path

from hawser.models import CONTRACT_VERSION
from hawser.models.export import EXPORTED, check, export_all, write

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "api" / "schemas"


def test_committed_schemas_are_current() -> None:
    problems = check(SCHEMA_DIR)
    assert problems == [], "run `make schemas` and commit the result:\n" + "\n".join(problems)


def test_export_is_deterministic(tmp_path: Path) -> None:
    first = export_all()
    write(tmp_path)
    second = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.schema.json")}
    assert first == second


def test_every_schema_carries_contract_version() -> None:
    for name in EXPORTED:
        doc = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert doc["x-hawser-contract-version"] == CONTRACT_VERSION
        assert doc["$schema"].endswith("2020-12/schema")
