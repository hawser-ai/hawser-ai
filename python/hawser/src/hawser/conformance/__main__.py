"""``python -m hawser.conformance [--server URL --api-key KEY] [--home DIR]``."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from hawser.conformance.scenarios import Target, local_target, run_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hawser.conformance")
    parser.add_argument("--server", default=os.environ.get("HAWSER_SERVER_URL"))
    parser.add_argument("--api-key", default=os.environ.get("HAWSER_API_KEY"))
    parser.add_argument(
        "--home", type=Path, default=None, help="local runtime home (default: temp)"
    )
    parser.add_argument("--only", action="append", default=None)
    args = parser.parse_args(argv)

    target: Target
    if args.server:
        if not args.api_key:
            print("--api-key (or HAWSER_API_KEY) is required with --server", file=sys.stderr)
            return 2
        from hawser.runtime.remote import RemoteRuntime  # noqa: PLC0415

        target = RemoteRuntime(args.server, args.api_key)
        print(f"target: {args.server}")
    else:
        home = args.home or Path(tempfile.mkdtemp(prefix="hawser-conformance-"))
        target = local_target(home)
        print(f"target: LocalRuntime ({home})")

    results = run_all(target, args.only)
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.name}")
        if not r.passed:
            print("      " + r.detail.replace("\n", "\n      "))
    failed = [r for r in results if not r.passed]
    print(f"{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
