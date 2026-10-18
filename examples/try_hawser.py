"""Hands-on test script for Hawser.

Run from the repo root:

    uv run python examples/try_hawser.py                      # offline: bundled invoice, recorded response
    uv run python examples/try_hawser.py --live               # same, but call the real model (needs a key)
    uv run python examples/try_hawser.py --file my.pdf        # your own document, bundled invoice schema
    uv run python examples/try_hawser.py --file my.pdf --schema my_schema.json --live
    uv run python examples/try_hawser.py --file notes.pdf --ask "Who signed this and when?" --live

Keys: export OPENROUTER_API_KEY (default model routes through OpenRouter) or ANTHROPIC_API_KEY
with --model anthropic/claude-opus-5-5. Nothing is sent anywhere unless --live is given.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from importlib import resources
from pathlib import Path

import hawser
from hawser import Client, HawserConfig, RecordMode
from hawser.runtime.local import LocalRuntime

DEFAULT_MODEL = "openrouter/anthropic/claude-opus-5.5"
INSTRUCTION = "Extract the stockstatement."


def bundled(name: str) -> bytes:
    return resources.files("hawser._quickstart").joinpath(name).read_bytes()


def make_runtime(work: Path, *, live: bool, model: str) -> LocalRuntime:
    """Project-local state under ``work/.hawser``; recordings under ``work/cassette``."""
    cassette = work / "cassette"
    cassette.mkdir(parents=True, exist_ok=True)
    if not any(cassette.glob("*.json")):
        # Seed with the bundled recordings so the offline path works out of the box.
        for entry in resources.files("hawser._quickstart").joinpath("cassette").iterdir():
            (cassette / entry.name).write_bytes(entry.read_bytes())
    config = HawserConfig(
        home=work / ".hawser",
        cassette_dir=cassette,
        # auto = replay when a recording matches, otherwise call live and record it.
        record_mode=RecordMode.auto if live else RecordMode.replay,
        default_model=model,
    )
    return LocalRuntime(config)


def report(result: hawser.RunResult) -> None:
    print(f"\n=== {result.status.value.upper()}  ({result.run_id}) ===")
    if result.error:
        print(f"error: {result.error.get('code')}: {result.error.get('message')}")
        return
    if isinstance(result.result, str):
        print(result.result)
    else:
        print(json.dumps(result.result, indent=2))

    if result.field_meta:
        print(
            "\nfield                         value                     status   page  bbox (pt)            conf"
        )
        by_id = {e.id: e for e in result.evidence}
        values = flatten(result.result)
        for ptr, meta in sorted(result.field_meta.items()):
            ev = next((by_id[i] for i in meta.evidence if i in by_id), None)
            page = getattr(ev, "page", "") if ev else ""
            bbox = getattr(ev, "bbox", None)
            box = f"{bbox.x0:.0f},{bbox.y0:.0f}-{bbox.x1:.0f},{bbox.y1:.0f}" if bbox else ""
            conf = f"{meta.anchor_confidence:.2f}" if meta.anchor_confidence is not None else ""
            print(
                f"{ptr:29s} {str(values.get(ptr))[:25]:25s} {meta.status.value:8s} {str(page):5s} {box:20s} {conf}"
            )

    for issue in result.validation.issues:
        print(
            f"{issue.severity.value:8s} {issue.rule_id:28s} {issue.field or '':28s} {issue.message}"
        )
    for w in result.warnings:
        print(f"warning  {w.code}: {w.message}")

    t = result.usage.totals
    replayed = any(c.replayed for c in result.usage.model_calls)
    print(
        f"\nusage: {t.model_calls} model call(s), {t.input_tokens} in / {t.output_tokens} out, "
        f"${t.cost_usd:.4f}, {t.wall_time_ms} ms{'  [replayed from cassette]' if replayed else ''}"
    )
    found = [p for p, m in result.field_meta.items() if m.status.value == "found"]
    if found:
        anchored = sum(1 for p in found if result.field_meta[p].evidence)
        print(f"evidence: {anchored}/{len(found)} found fields anchored to the page")


def flatten(
    data: object, path: str = "", out: dict[str, object] | None = None
) -> dict[str, object]:
    out = {} if out is None else out
    if isinstance(data, dict):
        for k, v in data.items():
            flatten(v, f"{path}/{k}", out)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            flatten(v, f"{path}/{i}", out)
    else:
        out[path] = data
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--file", action="append", help="input document (repeatable); default: bundled invoice"
    )
    ap.add_argument(
        "--schema",
        help="JSON Schema file; default: bundled invoice schema. Use --ask for no schema",
    )
    ap.add_argument("--ask", help="free-form instruction instead of schema extraction")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"registry model id (default {DEFAULT_MODEL})"
    )
    ap.add_argument(
        "--live", action="store_true", help="call the real provider (otherwise replay only)"
    )
    ap.add_argument(
        "--work", default=".hawser-try", help="directory for local state and recordings"
    )
    ap.add_argument("--json", action="store_true", help="dump the full RunResult as JSON")
    args = ap.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    if args.file:
        inputs = [Path(f) for f in args.file]
    else:
        tmp = Path(tempfile.mkdtemp(prefix="hawser-")) / "invoice.pdf"
        tmp.write_bytes(bundled("invoice.pdf"))
        inputs = [tmp]
        print("using the bundled synthetic invoice:", tmp)

    schema: dict[str, object] | None
    if args.ask:
        schema, instruction = None, args.ask
    elif args.schema:
        schema, instruction = json.loads(Path(args.schema).read_text()), INSTRUCTION
    else:
        schema, instruction = json.loads(bundled("invoice.schema.json")), INSTRUCTION

    client = Client(make_runtime(work, live=args.live, model=args.model))
    result = client.run(instruction, inputs=inputs, output_schema=schema, models=args.model)

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        report(result)
    if (
        result.status.value == "failed"
        and result.error
        and result.error.get("code") == "cassette_miss"
    ):
        print(
            "\nNo recording matches this request. Re-run with --live (and a provider key) to call the model."
        )
    return 0 if result.status.value in ("accepted", "needs_review") else 1


if __name__ == "__main__":
    sys.exit(main())
