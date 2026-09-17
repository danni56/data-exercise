#!/usr/bin/env python3
"""Read-only checks of the supplied input envelope. No solution is evaluated."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class InputError(ValueError):
    """A supplied file does not satisfy its documented envelope contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InputError(message)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def timestamp(value: Any, label: str) -> datetime:
    require(isinstance(value, str), f"{label}: requires an offset-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{label}: invalid timestamp {value!r}") from exc
    require(parsed.utcoffset() is not None, f"{label}: timezone offset is absent")
    return parsed


def input_path(root: Path, relative: Any) -> Path:
    require(isinstance(relative, str) and bool(relative), "payload_path must be a nonempty string")
    require(not Path(relative).is_absolute(), f"Input path must be relative: {relative}")
    path = (root / relative).resolve()
    require(path.is_relative_to(root), f"Input path escapes repository root: {relative}")
    require(path.is_file(), f"Missing input file: {relative}")
    return path


def object_list(value: Any, label: str) -> list[dict[str, Any]]:
    require(isinstance(value, list), f"{label}: requires an array")
    require(all(isinstance(item, dict) for item in value), f"{label}: entries must be objects")
    return value


def check(root: Path) -> tuple[int, int, int]:
    manifest = read_json(root / "manifest.json")
    require(isinstance(manifest, dict), "manifest.json: requires an object")
    require(manifest.get("schema_version") == 1, "Unsupported manifest schema_version")
    as_of = timestamp(manifest.get("as_of"), "as_of")
    tenants = manifest.get("tenants")
    require(isinstance(tenants, list) and bool(tenants), "tenants must be a nonempty array")
    require(all(isinstance(t, str) and t for t in tenants), "tenant identifiers must be strings")
    require(len(tenants) == len(set(tenants)), "tenants contains duplicate entries")
    defaults = manifest.get("query_defaults")
    require(isinstance(defaults, dict), "query_defaults must be an object")
    require(defaults.get("tenant_id") in tenants, "query_defaults has an undeclared tenant")
    input_path(root, manifest.get("extract_notes"))
    entries = object_list(manifest.get("snapshots"), "snapshots")
    ids: set[str] = set()
    declared: dict[Path, dict[str, dict[str, Any]]] = {}
    unavailable = 0
    required = {
        "snapshot_id", "source_family", "source_instance_id", "tenant_id", "payload_path",
        "format", "observed_at", "exported_at", "freshness_budget_seconds", "status", "coverage", "scope",
    }
    for entry in entries:
        require(required <= entry.keys(), "Manifest collection is missing required fields")
        sid = entry["snapshot_id"]
        require(isinstance(sid, str) and bool(sid), "snapshot_id must be a nonempty string")
        require(sid not in ids, f"Duplicate snapshot_id: {sid}")
        ids.add(sid)
        family = entry["source_family"]
        require(isinstance(family, str) and family in {"aws", "terraform", "kubernetes", "catalog"}, f"{sid}: unsupported source_family")
        require(entry["tenant_id"] in tenants, f"{sid}: undeclared tenant")
        require(isinstance(entry["source_instance_id"], str) and bool(entry["source_instance_id"]), f"{sid}: missing source_instance_id")
        require(isinstance(entry["scope"], dict) and bool(entry["scope"]), f"{sid}: missing scope")
        require(type(entry["freshness_budget_seconds"]) is int and entry["freshness_budget_seconds"] >= 0, f"{sid}: invalid freshness budget")
        require(entry["format"] == ("csv" if family == "catalog" else "json"), f"{sid}: format does not match source family")
        if entry["status"] == "not_provided":
            require(entry["coverage"] == "unknown", f"{sid}: unavailable collection must have unknown coverage")
            require(all(entry[k] is None for k in ("payload_path", "observed_at", "exported_at")), f"{sid}: unavailable collection contains payload or timestamps")
            unavailable += 1
            continue
        require(entry["status"] == "success" and entry["coverage"] == "complete", f"{sid}: unsupported collection status/coverage")
        observed = timestamp(entry["observed_at"], sid + ".observed_at")
        exported = timestamp(entry["exported_at"], sid + ".exported_at")
        require(observed <= exported <= as_of, f"{sid}: inconsistent observation/export/evaluation times")
        path = input_path(root, entry["payload_path"])
        declared.setdefault(path, {})[sid] = entry

    record_count = 0
    available_count = 0
    catalog_columns = {
        "snapshot_id", "service_id", "service_name", "environment", "owner_team",
        "cluster_id", "namespace", "deployment_name", "declared_dependency_arn",
    }
    for path, collections in declared.items():
        families = {entry["source_family"] for entry in collections.values()}
        require(len(families) == 1, f"{path.name}: multiple source families in one file")
        family = next(iter(families))
        if family == "catalog":
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                require(reader.fieldnames is not None, f"{path.name}: missing CSV header")
                require(len(reader.fieldnames) == len(set(reader.fieldnames)), f"{path.name}: duplicate CSV columns")
                require(set(reader.fieldnames) == catalog_columns, f"{path.name}: unexpected CSV columns")
                rows = list(reader)
            present = set()
            for row in rows:
                require(None not in row and all(v is not None for v in row.values()), f"{path.name}: malformed CSV row")
                sid = row["snapshot_id"]
                require(sid in collections, f"{path.name}: row references an undeclared collection")
                present.add(sid)
            require(present == set(collections), f"{path.name}: manifest collection has no rows in this extract")
            record_count += len(rows)
            available_count += len(collections)
            continue

        payload = read_json(path)
        require(isinstance(payload, dict), f"{path.name}: requires a JSON object")
        groups = [payload] if family == "terraform" else object_list(payload.get("collections"), path.name + ".collections")
        present = set()
        for group in groups:
            sid = group.get("snapshot_id")
            require(isinstance(sid, str), f"{path.name}: collection requires snapshot_id")
            require(sid in collections, f"{path.name}: undeclared snapshot_id {sid}")
            require(sid not in present, f"{path.name}: repeated collection {sid}")
            present.add(sid)
            if family == "aws":
                for key in ("instances", "databases"):
                    record_count += len(object_list(group.get(key), f"{sid}.{key}"))
            elif family == "kubernetes":
                record_count += len(object_list(group.get("items"), sid + ".items"))
            else:
                require(isinstance(group.get("lineage"), str), f"{sid}: state lineage must be a string")
                require(type(group.get("serial")) is int, f"{sid}: state serial must be an integer")
                record_count += len(object_list(group.get("resources"), sid + ".resources"))
        require(present == set(collections), f"{path.name}: missing manifest collection")
        available_count += len(present)
    return available_count, unavailable, record_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Repository root; defaults to the parent of tools/")
    args = parser.parse_args()
    try:
        available, unavailable, records = check(args.root.resolve())
    except (InputError, OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        print(f"Input check failed: {exc}", file=sys.stderr)
        return 1
    print("Input files are readable and match the extract envelope contract.")
    print(f"Available collections: {available}; not provided: {unavailable}; source-object records: {records}.")
    print("This check does not run or evaluate your solution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
