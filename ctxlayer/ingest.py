"""Load the four source families into the store as claims with source refs; linking happens in resolve.py."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .identity import ec2_instance_arn
from .manifest import Collection, iso
from .model import SourceRef, Store


def ingest(store: Store) -> None:
    manifest = store.manifest
    cache: dict[Path, Any] = {}

    def load_json(path: Path) -> Any:
        if path not in cache:
            with path.open(encoding="utf-8") as handle:
                cache[path] = json.load(handle)
        return cache[path]

    for collection in manifest.collections:
        if not collection.available:
            continue
        path = manifest.root / collection.payload_path
        family = collection.source_family
        if family == "aws":
            index, group = _group(load_json(path), collection.snapshot_id)
            ingest_aws(store, collection, group, index)
        elif family == "kubernetes":
            index, group = _group(load_json(path), collection.snapshot_id)
            ingest_kubernetes(store, collection, group, index)
        elif family == "terraform":
            payload = load_json(path)
            if payload.get("snapshot_id") != collection.snapshot_id:
                raise ValueError(f"{path.name}: snapshot_id does not match manifest entry {collection.snapshot_id}")
            ingest_terraform(store, collection, payload)
        elif family == "catalog":
            ingest_catalog(store, collection, path)
        else:
            raise ValueError(f"unsupported source family {family!r}")


def _group(payload: dict, snapshot_id: str) -> tuple[int, dict]:
    for index, group in enumerate(payload["collections"]):
        if group.get("snapshot_id") == snapshot_id:
            return index, group
    raise ValueError(f"payload has no collection {snapshot_id}")


def _ref(collection: Collection, locator: str) -> SourceRef:
    assert collection.payload_path is not None
    return SourceRef(
        file=collection.payload_path,
        locator=locator,
        snapshot_id=collection.snapshot_id,
        tenant_id=collection.tenant_id,
        observed_at=iso(collection.observed_at),
    )


def _tags(record: dict) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in record.get("Tags") or []}


def ingest_aws(store: Store, collection: Collection, group: dict, group_index: int) -> None:
    account_id = collection.scope["account_id"]
    region = collection.scope["region"]
    tenant = collection.tenant_id

    for i, record in enumerate(group.get("instances", [])):
        ref = _ref(collection, f"/collections/{group_index}/instances/{i}")
        arn = ec2_instance_arn(account_id, region, record["InstanceId"])
        entity = store.upsert("ec2_instance", tenant, (arn,), ref)
        entity.claim("instance_id", record["InstanceId"], ref)
        entity.claim("state", record["State"]["Name"], ref)
        if "PrivateIpAddress" in record:
            entity.claim("private_ip", record["PrivateIpAddress"], ref)
        if "VpcId" in record:
            entity.claim("vpc_id", record["VpcId"], ref)
        _claim_tags(entity, _tags(record), ref)

    for i, record in enumerate(group.get("databases", [])):
        ref = _ref(collection, f"/collections/{group_index}/databases/{i}")
        entity = store.upsert("db_instance", tenant, (record["DBInstanceArn"],), ref)
        entity.claim("identifier", record["DBInstanceIdentifier"], ref)
        entity.claim("status", record["DBInstanceStatus"], ref)
        _claim_tags(entity, _tags(record), ref)


def _claim_tags(entity, tags: dict[str, str], ref: SourceRef) -> None:
    # Absent tags are not claims.
    if "Name" in tags:
        entity.claim("name", tags["Name"], ref)
    if "Environment" in tags:
        entity.claim("environment_tag", tags["Environment"], ref)
    if "operator_team" in tags:
        entity.claim("operator_team", tags["operator_team"], ref)


def ingest_terraform(store: Store, collection: Collection, payload: dict) -> None:
    workspace_id = collection.scope["workspace_id"]
    tenant = collection.tenant_id
    for i, resource in enumerate(payload["resources"]):
        ref = _ref(collection, f"/resources/{i}")
        entity = store.upsert("tf_resource", tenant, (workspace_id, resource["address"]), ref)
        entity.claim("workspace_id", workspace_id, ref)
        entity.claim("address", resource["address"], ref)
        entity.claim("mode", resource["mode"], ref)
        entity.claim("type", resource["type"], ref)
        entity.claim("lineage", payload["lineage"], ref)
        entity.claim("serial", payload["serial"], ref)
        values = resource.get("values") or {}
        if "id" in values:
            entity.claim("provider_id", values["id"], ref)
        if "arn" in values:
            entity.claim("arn", values["arn"], ref)
        if "tags" in values:
            entity.claim("tags", dict(values["tags"]), ref)


def ingest_kubernetes(store: Store, collection: Collection, group: dict, group_index: int) -> None:
    cluster_id = collection.scope["cluster_id"]
    tenant = collection.tenant_id
    for i, item in enumerate(group.get("items", [])):
        ref = _ref(collection, f"/collections/{group_index}/items/{i}")
        metadata = item["metadata"]
        kind = item["kind"]
        entity = store.upsert(f"k8s_{kind.lower()}", tenant, (cluster_id, metadata["uid"]), ref)
        entity.claim("cluster_id", cluster_id, ref)
        entity.claim("kind", kind, ref)
        entity.claim("name", metadata["name"], ref)
        if "namespace" in metadata:
            entity.claim("namespace", metadata["namespace"], ref)
        if "labels" in metadata:
            entity.claim("labels", dict(metadata["labels"]), ref)
        if "ownerReferences" in metadata:
            owners = [
                {
                    "kind": owner["kind"],
                    "name": owner["name"],
                    "uid": owner["uid"],
                    "controller": owner.get("controller"),
                }
                for owner in metadata["ownerReferences"]
            ]
            entity.claim("owner_references", owners, ref)
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        if "replicas" in spec:
            entity.claim("replicas", spec["replicas"], ref)
        if "selector" in spec and "matchLabels" in spec["selector"]:
            entity.claim("selector_match_labels", dict(spec["selector"]["matchLabels"]), ref)
        if "nodeName" in spec:
            entity.claim("node_name", spec["nodeName"], ref)
        if "providerID" in spec:
            entity.claim("provider_id", spec["providerID"], ref)
        if "phase" in status:
            entity.claim("phase", status["phase"], ref)
        for address in status.get("addresses", []):
            if address.get("type") == "InternalIP":
                entity.claim("internal_ip", address["address"], ref)


def ingest_catalog(store: Store, collection: Collection, path: Path) -> None:
    tenant = collection.tenant_id
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):  # header is line 1
            if row["snapshot_id"] != collection.snapshot_id:
                continue
            ref = _ref(collection, f"line {line_number}")
            entity = store.upsert("catalog_application", tenant, (row["service_id"],), ref)
            for column in ("service_name", "environment", "owner_team"):
                if row[column]:
                    entity.claim(column, row[column], ref)
            workload = {k: row[k] for k in ("cluster_id", "namespace", "deployment_name") if row[k]}
            if workload:
                entity.claim("workload_reference", workload, ref)
            if row["declared_dependency_arn"]:
                entity.claim("declared_dependency_arn", row["declared_dependency_arn"], ref)
