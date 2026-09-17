"""Build the model and write answers plus an issues report per tenant.

    python -m ctxlayer                                   # every tenant; env/app from manifest query_defaults
    python -m ctxlayer --tenant acme                     # one tenant
    python -m ctxlayer --environment staging --application payments-api
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from .build import build
from .queries import issues_report, question_a, question_b


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ctxlayer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root containing manifest.json")
    parser.add_argument("--out", type=Path, default=Path("output"), help="output directory (default: output/)")
    parser.add_argument("--tenant", help="tenant to answer for (default: every tenant in the manifest)")
    parser.add_argument("--environment", help="environment (default: manifest query_defaults)")
    parser.add_argument("--application", help="catalog application name (default: manifest query_defaults)")
    args = parser.parse_args(argv)

    store = build(args.root.resolve())
    defaults = store.manifest.query_defaults
    environment = args.environment or defaults["environment"]
    application = args.application or defaults["application_name"]
    tenants = [args.tenant] if args.tenant else list(store.manifest.tenants)

    print(f"model: {len(store.entities())} entities, {len(store.links())} links; evaluation time {store.manifest.as_of.isoformat()}")
    for tenant in tenants:
        folder = args.out / tenant
        dump(folder / "question_a.json", question_a(store, tenant, environment))
        dump(folder / "question_b.json", question_b(store, tenant, environment, application))
        dump(folder / "issues.json", issues_report(store, tenant))
        print(f"{tenant}: wrote {folder.as_posix()}/{{question_a,question_b,issues}}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
