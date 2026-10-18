"""Build the bundled quickstart: ``python -m hawser_synth.quickstart``.

Writes ``python/hawser/src/hawser/_quickstart/`` with a synthetic invoice, its schema, a policy
and a cassette. The cassette is produced by running the *real* pipeline with a static adapter
that returns the generator's ground truth, so the recorded request fingerprint matches what
``hawser quickstart`` sends. The entry is marked ``source: synthetic``; the nightly job
re-records it live when a key is available.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway, load_registry
from hawser.recording import Cassette
from hawser.runtime.local import LocalRuntime
from hawser.structured import inline_refs
from hawser.testing import StaticAdapter, build_envelope
from hawser_synth.invoice import generate_invoice, invoice_schema

SEED = 42
INSTRUCTION = "Extract the invoice."

README = """# Hawser quickstart

- `invoice.pdf` — a synthetic invoice (generated; no real company data).
- `invoice.schema.json` — the output schema (JSON Schema, from a Pydantic model).
- `policy.yaml` — budget, repair and review policy for the run.
- `cassette/` — recorded model responses so `hawser quickstart` works offline. Entries labelled
  `source: synthetic` were produced from the generator's ground truth (used for the default
  model); entries labelled `source: live` are real recordings (e.g. Claude Opus 5.5 via
  OpenRouter: `hawser quickstart --model openrouter/anthropic/claude-opus-5.5`).
  `hawser quickstart --live` runs the request against the real model and records it.

Equivalent explicit command:

    hawser run -i "Extract the invoice." -f quickstart/invoice.pdf \\
      --schema quickstart/invoice.schema.json --policy quickstart/policy.yaml \\
      --cassette quickstart/cassette
"""

POLICY = """# Quickstart policy. See docs/concepts/policy.md for every field.
limits:
  max_cost_usd: 0.25
repair:
  attempts: 1
review_on_failure: true
"""


def build(target: Path) -> Path:
    # Live recordings (made with `hawser quickstart --live` and copied in) survive rebuilds.
    keep: dict[str, str] = {}
    for entry in (target / "cassette").glob("*.json") if target.exists() else []:
        data = json.loads(entry.read_text(encoding="utf-8"))
        if data.get("source") == "live":
            keep[entry.name] = entry.read_text(encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    gen = generate_invoice(SEED, layout=0)
    (target / "invoice.pdf").write_bytes(gen.pdf)
    schema = invoice_schema()
    (target / "invoice.schema.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )
    (target / "policy.yaml").write_text(POLICY, encoding="utf-8")
    (target / "truth.json").write_text(gen.truth.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (target / "README.md").write_text(README, encoding="utf-8")
    (target / "__init__.py").write_text("", encoding="utf-8")

    envelope = build_envelope(
        inline_refs(schema), gen.truth.model_dump(mode="json"), source_texts=gen.source_texts
    )
    cassette_dir = target / "cassette"
    with tempfile.TemporaryDirectory() as tmp:
        cfg = HawserConfig(
            home=Path(tmp) / "home", record_mode=RecordMode.record, cassette_dir=cassette_dir
        )
        cassette = Cassette(cassette_dir, RecordMode.record)
        gateway = Gateway(
            load_registry(), {"anthropic": StaticAdapter(envelope)}, cassette=cassette
        )
        runtime = LocalRuntime(cfg, gateway=gateway)
        result = Client(runtime).run(
            INSTRUCTION,
            inputs=[target / "invoice.pdf"],
            output_schema=schema,
            policy=yaml_policy(),
        )
        runtime.close()
    if result.status.value != "accepted":
        raise SystemExit(f"quickstart build produced status {result.status}: {result.error}")
    for entry in cassette_dir.glob("*.json"):
        data = json.loads(entry.read_text(encoding="utf-8"))
        data["source"] = "synthetic"
        entry.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    for name, text in keep.items():
        (cassette_dir / name).write_text(text, encoding="utf-8")
    return target


def yaml_policy() -> dict[str, object]:
    import yaml  # noqa: PLC0415

    return yaml.safe_load(POLICY)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    default = (
        Path(__file__).resolve().parents[4] / "python" / "hawser" / "src" / "hawser" / "_quickstart"
    )
    target = Path(args[0]) if args else default
    out = build(target)
    print(f"quickstart written to {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
