"""Derive cross-record links inside each tenant; unresolved references stay unresolved."""

from __future__ import annotations

import re
from typing import Optional

from .identity import ARN_SERVICE_TO_KIND, TF_TYPE_TO_KIND, ec2_instance_arn, parse_arn
from .model import Entity, Link, Store, TenantView
from .scope import AMBIGUOUS, INSUFFICIENT, RESOLVED, cloud_scope_covers, coverage, k8s_scope_covers

PROVIDER_ID = re.compile(r"^aws:///(?P<zone>[^/]+)/(?P<instance_id>i-[0-9a-f]+)$")


def resolve(store: Store) -> None:
    for tenant_id in store.manifest.tenants:
        view = store.view(tenant_id)
        link_terraform(store, view)
        link_controllers(store, view)
        link_pods_to_nodes(store, view)
        link_nodes_to_instances(store, view)
        link_catalog(store, view)
    observe_cross_tenant_identities(store)


def link_terraform(store: Store, view: TenantView) -> None:
    for tf in view.entities("tf_resource"):
        mode = tf.value("mode")
        kind = TF_TYPE_TO_KIND.get(tf.value("type"))
        arn_text = tf.value("arn")
        arn = parse_arn(arn_text)
        link_kind = "terraform_manages" if mode == "managed" else "terraform_data_reference"
        reference = {"type": tf.value("type"), "mode": mode, "id": tf.value("provider_id"), "arn": arn_text}
        link = Link(link_kind, view.tenant_id, tf.id, tf.refs[0], reference, rule="terraform_values_arn")

        if kind is None or arn is None:
            link.status = INSUFFICIENT
            link.detail = "Unsupported resource type or missing/unparseable values.arn."
        else:
            notes = []
            if tf.value("provider_id") != arn.resource_id:
                notes.append(f"values.id {tf.value('provider_id')!r} differs from the ARN resource id {arn.resource_id!r}.")
            workspace_account = view.collection(tf.refs[0].snapshot_id).scope.get("account_id")
            if workspace_account and workspace_account != arn.account_id:
                notes.append(f"ARN account {arn.account_id} differs from the workspace scope account {workspace_account}.")
            target = view.get(kind, (arn_text,))
            if target is not None:
                link.status = RESOLVED
                link.target_id = target.id
                # State tags are an observation too.
                tags = tf.value("tags") or {}
                if "operator_team" in tags:
                    target.claim("operator_team", tags["operator_team"], tf.refs[0])
            else:
                link.status, link.scopes_consulted = coverage(view.collections, "aws", cloud_scope_covers(arn))
                link.detail = f"No {kind} with this ARN was observed in the tenant's AWS inventory."
            if notes:
                link.detail = (link.detail + " " + " ".join(notes)).strip()
        store.add_link(link)


def link_controllers(store: Store, view: TenantView) -> None:
    for kind in ("k8s_replicaset", "k8s_pod"):
        for obj in view.entities(kind):
            cluster_id = obj.key[0]
            for owner in obj.value("owner_references") or []:
                reference = dict(owner)
                link = Link("controlled_by", view.tenant_id, obj.id, obj.refs[0], reference, rule="owner_reference_uid_in_cluster")
                target = view.get(f"k8s_{owner['kind'].lower()}", (cluster_id, owner["uid"]))
                if target is not None:
                    link.status = RESOLVED
                    link.target_id = target.id
                    if target.value("name") != owner["name"] or target.value("namespace") != obj.value("namespace"):
                        link.detail = "Owner UID matched but the referenced name/namespace differs from the observed object."
                else:
                    link.status, link.scopes_consulted = coverage(
                        view.collections, "kubernetes", k8s_scope_covers(cluster_id, owner["kind"], obj.value("namespace"))
                    )
                    link.detail = f"No {owner['kind']} with uid {owner['uid']} observed in cluster {cluster_id}."
                store.add_link(link)


def link_pods_to_nodes(store: Store, view: TenantView) -> None:
    nodes: dict[tuple[str, str], list[Entity]] = {}
    for node in view.entities("k8s_node"):
        nodes.setdefault((node.key[0], node.value("name")), []).append(node)

    for pod in view.entities("k8s_pod"):
        cluster_id = pod.key[0]
        node_name = pod.value("node_name")
        link = Link("scheduled_on", view.tenant_id, pod.id, pod.refs[0], {"node_name": node_name}, rule="node_name_in_cluster")
        if not node_name:
            link.status = INSUFFICIENT
            link.detail = "Pod has no spec.nodeName (not scheduled at observation time)."
        else:
            matches = nodes.get((cluster_id, node_name), [])
            if len(matches) == 1:
                link.status = RESOLVED
                link.target_id = matches[0].id
            elif matches:
                link.status = AMBIGUOUS
                link.candidates = [{"entity_id": m.id, "uid": m.key[1]} for m in matches]
            else:
                link.status, link.scopes_consulted = coverage(view.collections, "kubernetes", k8s_scope_covers(cluster_id, "Node", None))
                link.detail = f"No Node named {node_name} observed in cluster {cluster_id}."
        store.add_link(link)


def link_nodes_to_instances(store: Store, view: TenantView) -> None:
    clusters = {c.scope["cluster_id"]: c for c in view.collections if c.source_family == "kubernetes" and c.available}
    for node in view.entities("k8s_node"):
        cluster = clusters[node.key[0]]
        account_id, region = cluster.scope["account_id"], cluster.scope["region"]
        provider_id = node.value("provider_id")
        internal_ip = node.value("internal_ip")
        reference = {"provider_id": provider_id, "cluster_account_id": account_id, "cluster_region": region}
        link = Link("backed_by", view.tenant_id, node.id, node.refs[0], reference, rule="provider_id_instance_in_cluster_account")

        match = PROVIDER_ID.match(provider_id) if provider_id else None
        if match is None:
            link.status = INSUFFICIENT
            link.detail = (
                "Node has no spec.providerID; an omitted provider reference is not an empty instance id."
                if not provider_id
                else f"spec.providerID {provider_id!r} is not in the aws:///<zone>/<instance-id> form."
            )
            link.candidates = _ip_lookalikes(view, account_id, region, internal_ip)
        else:
            arn = ec2_instance_arn(account_id, region, match["instance_id"])
            target = view.get("ec2_instance", (arn,))
            if target is not None:
                link.status = RESOLVED
                link.target_id = target.id
                if internal_ip and target.value("private_ip") not in (None, internal_ip):
                    link.detail = f"Node InternalIP {internal_ip} differs from the instance PrivateIpAddress {target.value('private_ip')}."
            else:
                link.status, link.scopes_consulted = coverage(view.collections, "aws", cloud_scope_covers(parse_arn(arn)))
                link.detail = f"No EC2 instance {match['instance_id']} observed in account {account_id} / {region} for this tenant."
        store.add_link(link)


def _ip_lookalikes(view: TenantView, account_id: str, region: str, internal_ip: Optional[str]) -> list[dict]:
    if not internal_ip:
        return []
    out = []
    for instance in view.entities("ec2_instance"):
        arn = parse_arn(instance.key[0])
        if arn.account_id == account_id and arn.region == region and internal_ip in instance.values("private_ip"):
            out.append(
                {
                    "entity_id": instance.id,
                    "instance_id": instance.value("instance_id"),
                    "private_ip": internal_ip,
                    "vpc_id": instance.value("vpc_id"),
                    "why_not_used": "A private IP is a network address, not a provider identifier; it is reusable across VPCs.",
                }
            )
    return out


def link_catalog(store: Store, view: TenantView) -> None:
    deployments: dict[tuple[str, str, str], list[Entity]] = {}
    for dep in view.entities("k8s_deployment"):
        deployments.setdefault((dep.key[0], dep.value("namespace"), dep.value("name")), []).append(dep)

    for app in view.entities("catalog_application"):
        workload = app.value("workload_reference")
        if workload:
            link = Link("runs_as_workload", view.tenant_id, app.id, app.refs[0], dict(workload), rule="deployment_name_in_cluster_namespace")
            key = (workload.get("cluster_id"), workload.get("namespace"), workload.get("deployment_name"))
            if not all(key):
                link.status = INSUFFICIENT
                link.detail = "Workload reference is missing cluster_id, namespace, or deployment_name."
            else:
                matches = deployments.get(key, [])
                if len(matches) == 1:
                    link.status = RESOLVED
                    link.target_id = matches[0].id
                elif matches:
                    link.status = AMBIGUOUS
                    link.candidates = [{"entity_id": m.id, "uid": m.key[1]} for m in matches]
                else:
                    link.status, link.scopes_consulted = coverage(
                        view.collections, "kubernetes", k8s_scope_covers(key[0], "Deployment", key[1])
                    )
                    link.detail = f"No Deployment {key[2]} observed in cluster {key[0]} namespace {key[1]}."
            store.add_link(link)

        dependency = app.value("declared_dependency_arn")
        if dependency:
            arn = parse_arn(dependency)
            kind = ARN_SERVICE_TO_KIND.get(arn.service) if arn else None
            link = Link("declares_dependency", view.tenant_id, app.id, app.refs[0], {"arn": dependency}, rule="dependency_arn_identity")
            if kind is None:
                link.status = INSUFFICIENT
                link.detail = "Dependency ARN is unparseable or names an unsupported service."
            else:
                target = view.get(kind, (dependency,))
                if target is not None:
                    link.status = RESOLVED
                    link.target_id = target.id
                else:
                    link.status, link.scopes_consulted = coverage(view.collections, "aws", cloud_scope_covers(arn))
                    link.detail = f"No {kind} with this ARN observed in the tenant's AWS inventory."
            store.add_link(link)


def observe_cross_tenant_identities(store: Store) -> None:
    """Same ARN under more than one tenant; recorded for the issues report only, never linked."""
    groups: dict[tuple[str, tuple[str, ...]], list[Entity]] = {}
    for entity in store.entities():
        if entity.kind in ("ec2_instance", "db_instance"):
            groups.setdefault((entity.kind, entity.key), []).append(entity)
    store.cross_tenant_observations = [
        {
            "kind": kind,
            "arn": key[0],
            "tenants": sorted({e.tenant_id for e in entities}),
            "sources": [r.to_dict() | {"tenant_id": e.tenant_id} for e in entities for r in e.refs],
        }
        for (kind, key), entities in sorted(groups.items())
        if len({e.tenant_id for e in entities}) > 1
    ]
