"""Answer questions by reading one tenant's view of the model; nothing here re-derives identity."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from .identity import parse_arn
from .manifest import Collection, iso
from .model import Entity, Link, Store, TenantView
from .scope import (
    AMBIGUOUS,
    INSUFFICIENT,
    NO_COVERAGE,
    NOT_OBSERVED,
    RESOLVED,
    STATUS_MEANING,
    UNAVAILABLE,
    cloud_scope_covers,
    coverage,
)

BINDING_STATUS = {
    NOT_OBSERVED: "no_binding_in_supplied_scope",
    UNAVAILABLE: "evidence_unavailable",
    NO_COVERAGE: "evidence_unavailable",
}

BINDING_MEANING = {
    "binding_found": (
        "A managed-resource record (mode=managed) referencing this object exists in the supplied state. "
        "It shows management at that state's observation time, not at the evaluation time."
    ),
    "no_binding_in_supplied_scope": (
        "The supplied state covering this object's account, region, and resource type contains no "
        "managed-resource record for it at its observation time. This is not evidence that the object is unmanaged."
    ),
    "evidence_unavailable": (
        "The state collection that would cover this object was declared but not provided, or nothing supplied "
        "covers it. Nothing can be concluded either way."
    ),
}

RELATIONSHIP_SEMANTICS = {
    "owner_team (catalog)": "Source statement: the catalog names the team responsible for the application. It says nothing about infrastructure.",
    "declares_dependency": "Source statement (catalog ARN) resolved by ARN identity. A declaration of dependency, not an observed connection.",
    "runs_as_workload": "Source statement (catalog cluster/namespace/deployment_name) resolved by Deployment name within that cluster and namespace. The catalog's only handle is a name, so this hop relies on the Deployment being present in a complete collection.",
    "controlled_by": "Source statement (metadata.ownerReferences) resolved by UID within the cluster. Lifecycle control; UIDs distinguish incarnations that reuse a name.",
    "scheduled_on": "Source statement (Pod spec.nodeName) resolved by Node name within the cluster. Placement at observation time only.",
    "backed_by": "Source statement (Node spec.providerID) combined with the manifest's cluster account and region to form an EC2 ARN, then resolved by identity. Says which instance hosts the Node; it is not an application dependency.",
    "operator_team (tag)": "Source statement by the AWS inventory or the Terraform state about who operates an infrastructure resource. Not application responsibility.",
    "terraform_manages": "Source statement (values.arn of a mode=managed resource) resolved by ARN identity. Management at the state's observation time.",
    "terraform_data_reference": "Source statement (values.arn of a mode=data resource). A read of an existing object; not a management binding.",
}

FAMILY_AFFECTS = {
    "aws": ["question_a", "question_b"],
    "terraform": ["question_a", "question_b"],
    "kubernetes": ["question_b"],
    "catalog": ["question_b"],
}
LINK_AFFECTS = {
    "terraform_manages": ["question_a", "question_b"],
    "terraform_data_reference": ["question_a"],
    "controlled_by": ["question_b"],
    "scheduled_on": ["question_b"],
    "backed_by": ["question_b"],
    "runs_as_workload": ["question_b"],
    "declares_dependency": ["question_b"],
}
SEVERITY_BY_STATUS = {NOT_OBSERVED: "medium", UNAVAILABLE: "high", NO_COVERAGE: "high", INSUFFICIENT: "medium", AMBIGUOUS: "medium"}
RESOLVING_EVIDENCE = {
    ("terraform_manages", NOT_OBSERVED): "A current AWS inventory that contains the instance, or a refreshed state showing the resource removed.",
    ("backed_by", INSUFFICIENT): "A Node spec.providerID, or a cloud-side statement (instance metadata, ENI attachment, node-group membership) naming the backing instance.",
    ("runs_as_workload", NOT_OBSERVED): "A Deployment with the referenced name in that cluster and namespace, or a catalog correction.",
    ("runs_as_workload", UNAVAILABLE): "The Kubernetes collection for the referenced cluster.",
    ("declares_dependency", NO_COVERAGE): "An AWS collection for this tenant covering the dependency's account, region, and resource type.",
    ("declares_dependency", UNAVAILABLE): "The AWS collection covering the dependency's scope.",
}
DEFAULT_RESOLVING_EVIDENCE = "A collection that covers the referenced object's scope, observed within its freshness budget."


def _claims(entity: Entity, attribute: str) -> list[dict]:
    return [c.to_dict() for c in entity.claims_for(attribute)]


def _conflicts(entity: Entity) -> list[dict]:
    return [{"attribute": attr, "values": [c.to_dict() for c in claims]} for attr, claims in entity.conflicts().items()]


def _refs(entity: Entity) -> list[dict]:
    return [r.to_dict() for r in entity.refs]


def _resolution(link: Link) -> dict:
    return {
        "status": link.status,
        "meaning": STATUS_MEANING[link.status],
        "rule": link.rule,
        "detail": link.detail or None,
        "scopes_consulted": link.scopes_consulted,
        "candidates_not_used": link.candidates,
    }


def _summaries(view: TenantView, family: str, predicate: Optional[Callable[[Collection], bool]] = None) -> list[dict]:
    return [
        c.summary(view.as_of)
        for c in view.collections
        if c.source_family == family and (predicate is None or predicate(c))
    ]


def _resource_id(entity: Entity) -> str:
    arn = parse_arn(entity.key[0])
    return arn.resource_id if arn else entity.key[0]


def terraform_binding(view: TenantView, resource: Entity) -> dict:
    arn = parse_arn(resource.key[0])
    managed = view.links_to(resource.id, ("terraform_manages",))
    data = view.links_to(resource.id, ("terraform_data_reference",))
    coverage_status, consulted = coverage(view.collections, "terraform", cloud_scope_covers(arn))
    status = "binding_found" if managed else BINDING_STATUS[coverage_status]

    def describe(link: Link) -> dict:
        tf = view.entity(link.source_id)
        col = view.collection(link.stated_by.snapshot_id)
        return {
            "workspace_id": tf.value("workspace_id"),
            "address": tf.value("address"),
            "mode": tf.value("mode"),
            "state_lineage": tf.value("lineage"),
            "state_serial": tf.value("serial"),
            "snapshot_id": col.snapshot_id,
            "observed_at": iso(col.observed_at),
            "freshness": col.freshness(view.as_of).to_dict(),
            "source": link.stated_by.to_dict(),
            "derived_by": link.rule,
            "detail": link.detail or None,
        }

    scopes = []
    for snapshot_id in consulted:
        col = view.collection(snapshot_id)
        scopes.append(
            {
                "snapshot_id": snapshot_id,
                "workspace_id": col.scope.get("workspace_id"),
                "status": col.status,
                "coverage": col.coverage,
                "observed_at": iso(col.observed_at),
                "freshness": col.freshness(view.as_of).to_dict(),
            }
        )
    managed_desc = [describe(l) for l in managed]
    data_desc = [describe(l) for l in data]
    return {
        "status": status,
        "meaning": BINDING_MEANING[status],
        "managed_bindings": managed_desc,
        "data_source_references": data_desc,
        "terraform_scopes_consulted": scopes,
        "interpretation": _binding_interpretation(status, managed_desc, data_desc, scopes),
    }


def _binding_interpretation(status: str, managed: list[dict], data: list[dict], scopes: list[dict]) -> str:
    parts: list[str] = []
    if status == "binding_found":
        for m in managed:
            parts.append(
                f"Workspace {m['workspace_id']} held a managed-resource record {m['address']} for this object in the "
                f"state observed at {m['observed_at']} (lineage {m['state_lineage']}, serial {m['state_serial']}). "
                "This establishes management at that observation, not at the evaluation time."
            )
            if m["freshness"]["status"] == "stale":
                parts.append(
                    f"That observation is {m['freshness']['age_seconds']} s old and exceeds the "
                    f"{m['freshness']['budget_seconds']} s freshness budget; the object may since have been removed "
                    "from state, moved to another workspace, or re-imported."
                )
    elif status == "no_binding_in_supplied_scope":
        supplied = [s for s in scopes if s["status"] == "success"]
        workspaces = ", ".join(s["workspace_id"] for s in supplied)
        observed = ", ".join(s["observed_at"] for s in supplied)
        parts.append(
            f"No managed-resource record references this object in the supplied state for workspace(s) {workspaces} "
            f"as observed at {observed}. This does not show the object is unmanaged: a workspace that was not supplied "
            "may manage it, or it may have been created or imported after the state observation."
        )
        if any(s["freshness"]["status"] == "stale" for s in supplied):
            parts.append("The consulted state is stale relative to its freshness budget, which widens the window in which management could have changed.")
    else:
        ids = ", ".join(s["snapshot_id"] for s in scopes) or "none declared"
        parts.append(
            f"The Terraform collection(s) that would cover this object's account and region ({ids}) were not provided; "
            "whether a managed binding exists cannot be determined from the supplied evidence."
        )
    if data:
        refs = ", ".join(f"{d['workspace_id']}:{d['address']}" for d in data)
        parts.append(f"A data-source record (mode=data) references this object: {refs}. A data source reads an existing object; it is not a management binding.")
    return " ".join(parts)


def cloud_resource_context(view: TenantView, entity: Entity) -> dict:
    arn = parse_arn(entity.key[0])
    observed: dict[str, Any] = {}
    for attribute in ("instance_id", "identifier", "state", "status", "name", "private_ip", "vpc_id", "environment_tag"):
        if entity.claims_for(attribute):
            observed[attribute] = _claims(entity, attribute)
    operator_values = entity.values("operator_team")
    return {
        "entity_id": entity.id,
        "kind": entity.kind,
        "arn": entity.key[0],
        "account_id": arn.account_id,
        "region": arn.region,
        "observed": observed,
        "operator_team": {
            "statement_kind": "source_statement",
            "statement": "AWS tag operator_team / Terraform values.tags.operator_team",
            "claims": _claims(entity, "operator_team"),
            "distinct_values": operator_values,
            "conflict": len(operator_values) > 1,
            "meaning": "The source's statement about who operates this infrastructure resource. It does not state application responsibility.",
        },
        "conflicting_observations": _conflicts(entity),
        "terraform": terraform_binding(view, entity),
    }


# Question A
def question_a(store: Store, tenant_id: str, environment: str) -> dict:
    view = store.view(tenant_id)
    as_of = view.as_of
    inventory = [
        c
        for c in view.collections
        if c.source_family == "aws"
        and c.scope.get("environment") == environment
        and "aws_instance" in c.scope.get("resource_types", [])
    ]
    answer: dict[str, Any] = {
        "question": "A",
        "title": "EC2 instances observed running in the requested environment, and Terraform managed-resource binding evidence",
        "query_scope": {
            "tenant_id": tenant_id,
            "environment": environment,
            "resource_kind": "ec2_instance",
            "observed_state": "running",
            "inventory_scope": "AWS collections for this tenant whose manifest scope environment matches and whose resource_types include aws_instance",
            "terraform_scope": "Every Terraform collection supplied for this tenant whose scope covers the instance's account, region, and resource type",
        },
        "evaluation_time": iso(as_of),
        "sources": {
            "aws_inventory": [c.summary(as_of) for c in inventory],
            "terraform_state": _summaries(view, "terraform"),
        },
        "status_meanings": BINDING_MEANING,
        "instances": [],
        "excluded_instances": [],
        "terraform_managed_instances_not_observed": [],
        "limitations": [],
    }

    available = [c for c in inventory if c.available]
    if not inventory:
        answer["limitations"].append(
            f"No AWS inventory collection is declared for tenant {tenant_id} in environment {environment}; the running-instance list cannot be produced."
        )
        return answer
    if not available:
        answer["limitations"].append(
            "The AWS inventory collection(s) for this scope were declared but not provided: "
            + ", ".join(c.snapshot_id for c in inventory)
            + ". An unavailable collection is not an empty inventory."
        )
        return answer
    inventory_ids = {c.snapshot_id for c in available}

    for instance in view.entities("ec2_instance"):
        state_claims = [c for c in instance.claims_for("state") if c.ref.snapshot_id in inventory_ids]
        if not state_claims:
            continue  # observed only outside this query's scope
        states: list[str] = []
        for claim in state_claims:
            if claim.value not in states:
                states.append(claim.value)
        if "running" not in states:
            answer["excluded_instances"].append(
                {
                    "instance_id": instance.value("instance_id"),
                    "arn": instance.key[0],
                    "observed_state": [c.to_dict() for c in state_claims],
                    "reason": "not observed as running",
                }
            )
            continue
        entry = _instance_entry(view, instance, state_claims)
        if len(states) > 1:
            entry["limitations"].append("Inventory collections in this scope disagree about the state; listed because at least one observed it running.")
        answer["instances"].append(entry)

    for link in view.links(("terraform_manages",)):
        if link.status == RESOLVED or link.reference.get("type") != "aws_instance":
            continue
        col = view.collection(link.stated_by.snapshot_id)
        if col.scope.get("environment") != environment:
            continue
        tf = view.entity(link.source_id)
        answer["terraform_managed_instances_not_observed"].append(
            {
                "workspace_id": tf.value("workspace_id"),
                "address": tf.value("address"),
                "instance_id": link.reference.get("id"),
                "arn": link.reference.get("arn"),
                "state_observed_at": iso(col.observed_at),
                "resolution": _resolution(link),
                "source": link.stated_by.to_dict(),
                "meaning": (
                    "The state records a managed instance that the complete AWS inventory did not contain at its observation time. "
                    "Either the instance was terminated after the state was refreshed, or the state is stale. "
                    "It bears on how far this state can be trusted for the instances listed above."
                ),
            }
        )

    for col in view.collections:
        if col.source_family != "terraform":
            continue
        fresh = col.freshness(as_of)
        workspace = col.scope.get("workspace_id")
        if fresh.status == "stale":
            answer["limitations"].append(
                f"Terraform collection {col.snapshot_id} (workspace {workspace}) was observed at {fresh.observed_at}, "
                f"{fresh.age_seconds} s before the evaluation time, exceeding its {fresh.budget_seconds} s freshness budget. "
                f"Bindings and absences taken from it describe {fresh.observed_at}, not the evaluation time."
            )
        elif fresh.status == "unavailable":
            answer["limitations"].append(
                f"Terraform collection {col.snapshot_id} (workspace {workspace}) was not provided; instances in its scope have unavailable binding evidence."
            )
    answer["limitations"].append(
        "A Terraform collection establishes coverage only for its own workspace. An instance with no binding here may be managed by a workspace that was not supplied."
    )
    return answer


def _instance_entry(view: TenantView, instance: Entity, state_claims) -> dict:
    arn = parse_arn(instance.key[0])
    entry: dict[str, Any] = {
        "instance_id": instance.value("instance_id"),
        "arn": instance.key[0],
        "account_id": arn.account_id,
        "region": arn.region,
        "observed": {"state": [c.to_dict() for c in state_claims]},
        "conflicting_observations": _conflicts(instance),
        "terraform_binding": terraform_binding(view, instance),
        "limitations": [],
    }
    for attribute in ("name", "private_ip", "vpc_id", "environment_tag", "operator_team"):
        if instance.claims_for(attribute):
            entry["observed"][attribute] = _claims(instance, attribute)

    binding = entry["terraform_binding"]
    for match in binding["managed_bindings"]:
        if match["freshness"]["status"] == "stale":
            entry["limitations"].append(
                f"Binding in workspace {match['workspace_id']} comes from state observed at {match['observed_at']} (stale); current management is not established."
            )
    if binding["status"] == "no_binding_in_supplied_scope" and any(
        s["freshness"]["status"] == "stale" for s in binding["terraform_scopes_consulted"]
    ):
        entry["limitations"].append("The consulted state is stale; the instance could have been created or imported after the state observation.")
    if binding["data_source_references"]:
        entry["limitations"].append("A Terraform data source references this instance; that is a read of an existing object and is not a management binding.")
    for conflict in entry["conflicting_observations"]:
        entry["limitations"].append(
            f"Sources disagree on {conflict['attribute']}; both statements are shown with their observation times and neither is preferred."
        )
    return entry


# Question B
def question_b(store: Store, tenant_id: str, environment: str, application_name: str) -> dict:
    view = store.view(tenant_id)
    as_of = view.as_of
    answer: dict[str, Any] = {
        "question": "B",
        "title": "Application context and responsibility",
        "query_scope": {
            "tenant_id": tenant_id,
            "environment": environment,
            "application_name": application_name,
            "catalog_scope": "Catalog collections for this tenant; rows matched on service_name and the row's own environment column",
        },
        "evaluation_time": iso(as_of),
        "sources": {
            "catalog": _summaries(view, "catalog"),
            "kubernetes": _summaries(view, "kubernetes"),
            "aws_inventory": _summaries(view, "aws"),
            "terraform_state": _summaries(view, "terraform"),
        },
        "relationship_semantics": RELATIONSHIP_SEMANTICS,
        "application": None,
        "declared_dependencies": [],
        "workloads": [],
        "responsibility": None,
        "path_stops": [],
        "not_linked_lookalikes": [],
        "limitations": [],
    }

    applications = [
        a
        for a in view.entities("catalog_application")
        if application_name in a.values("service_name") and environment in a.values("environment")
    ]
    if not applications:
        answer["limitations"].append(
            f"No catalog application named {application_name!r} with environment {environment!r} exists in the catalog collections supplied for tenant {tenant_id}."
        )
        return answer
    if len(applications) > 1:
        answer["limitations"].append(
            "More than one catalog application matches; answering for the first by service_id and listing the others: "
            + ", ".join(a.key[0] for a in applications[1:])
        )
    app = applications[0]
    answer["application"] = {
        "service_id": app.key[0],
        "service_name": _claims(app, "service_name"),
        "environment": _claims(app, "environment"),
        "owner_team": {"statement_kind": "source_statement", "claims": _claims(app, "owner_team")},
        "source": _refs(app),
    }

    path_resources: dict[str, dict] = {}  # cloud entity id -> {"entity", "roles"}

    for link in view.links_from(app.id, ("declares_dependency",)):
        dependency = {
            "declared_arn": link.reference["arn"],
            "statement_kind": "source_statement",
            "stated_by": link.stated_by.to_dict(),
            "resolution": _resolution(link),
        }
        if link.target_id:
            target = view.entity(link.target_id)
            dependency["resource"] = cloud_resource_context(view, target)
            path_resources.setdefault(target.id, {"entity": target, "roles": []})["roles"].append("declared dependency target")
        else:
            answer["path_stops"].append(
                {"at": f"declared dependency {link.reference['arn']}", "status": link.status, "why": link.detail, "scopes_consulted": link.scopes_consulted}
            )
        answer["declared_dependencies"].append(dependency)

    for link in view.links_from(app.id, ("runs_as_workload",)):
        workload = {
            "workload_reference": link.reference,
            "statement_kind": "source_statement",
            "stated_by": link.stated_by.to_dict(),
            "resolution": _resolution(link),
        }
        if link.target_id:
            workload["deployment"] = _deployment_path(view, view.entity(link.target_id), answer["path_stops"], path_resources)
        else:
            ref = link.reference
            answer["path_stops"].append(
                {
                    "at": f"catalog workload reference -> Deployment {ref.get('deployment_name')} in {ref.get('cluster_id')}/{ref.get('namespace')}",
                    "status": link.status,
                    "why": link.detail,
                    "scopes_consulted": link.scopes_consulted,
                }
            )
        answer["workloads"].append(workload)
    if not answer["workloads"]:
        answer["path_stops"].append({"at": "catalog application", "status": "no_statement", "why": "The catalog row has no workload reference."})

    answer["responsibility"] = _responsibility(app, path_resources)

    for instance in view.entities("ec2_instance"):
        if application_name in instance.values("name") and instance.id not in path_resources:
            answer["not_linked_lookalikes"].append(
                {
                    "entity_id": instance.id,
                    "instance_id": instance.value("instance_id"),
                    "arn": instance.key[0],
                    "similarity": f"EC2 Name tag equals the application name {application_name!r}",
                    "why_not_linked": (
                        "A display label is not an identity, and no source states a relationship between this instance and the "
                        "application or its workloads. Listed so the similarity is visible; it is neither a dependency nor a host."
                    ),
                    "source": _refs(instance),
                }
            )

    _question_b_limitations(view, answer, path_resources)
    return answer


def _deployment_path(view: TenantView, deployment: Entity, stops: list, path_resources: dict) -> dict:
    node = {
        "kind": "Deployment",
        "name": deployment.value("name"),
        "namespace": deployment.value("namespace"),
        "cluster_id": deployment.key[0],
        "uid": deployment.key[1],
        "replicas_desired": deployment.value("replicas"),
        "selector_match_labels": deployment.value("selector_match_labels"),
        "source": _refs(deployment),
        "replicasets": [],
    }
    def by_name(link: Link) -> str:
        return view.entity(link.source_id).value("name")

    for rs_link in sorted(view.links_to(deployment.id, ("controlled_by",)), key=by_name):
        replicaset = view.entity(rs_link.source_id)
        rs_entry = {
            "kind": "ReplicaSet",
            "name": replicaset.value("name"),
            "uid": replicaset.key[1],
            "replicas_desired": replicaset.value("replicas"),
            "source": _refs(replicaset),
            "controlled_by": _hop("metadata.ownerReferences", rs_link),
            "pods": [],
        }
        for pod_link in sorted(view.links_to(replicaset.id, ("controlled_by",)), key=by_name):
            rs_entry["pods"].append(_pod_entry(view, view.entity(pod_link.source_id), pod_link, stops, path_resources))
        node["replicasets"].append(rs_entry)
    node["pods_observed"] = sum(len(rs["pods"]) for rs in node["replicasets"])
    return node


def _hop(statement: str, link: Link) -> dict:
    return {"statement_kind": "source_statement", "statement": statement, "reference": link.reference, "resolution": _resolution(link)}


def _pod_entry(view: TenantView, pod: Entity, control_link: Link, stops: list, path_resources: dict) -> dict:
    entry = {
        "kind": "Pod",
        "name": pod.value("name"),
        "uid": pod.key[1],
        "phase": _claims(pod, "phase"),
        "labels": pod.value("labels"),
        "source": _refs(pod),
        "controlled_by": _hop("metadata.ownerReferences", control_link),
    }
    scheduled = view.links_from(pod.id, ("scheduled_on",))
    if not scheduled:
        entry["scheduled_on"] = {"status": "no_statement"}
        stops.append({"at": f"Pod {pod.value('name')}", "status": "no_statement", "why": "No scheduling statement was ingested for this pod."})
        return entry
    link = scheduled[0]
    entry["scheduled_on"] = _hop("spec.nodeName", link)
    if link.target_id:
        entry["scheduled_on"]["node"] = _node_entry(view, view.entity(link.target_id), pod, stops, path_resources)
    else:
        stops.append({"at": f"Pod {pod.value('name')} -> Node {link.reference.get('node_name')}", "status": link.status, "why": link.detail})
    return entry


def _node_entry(view: TenantView, node: Entity, pod: Entity, stops: list, path_resources: dict) -> dict:
    entry: dict[str, Any] = {
        "kind": "Node",
        "name": node.value("name"),
        "uid": node.key[1],
        "provider_id": node.value("provider_id"),
        "internal_ip": node.value("internal_ip"),
        "source": _refs(node),
    }
    link = view.links_from(node.id, ("backed_by",))[0]
    entry["backed_by"] = _hop("spec.providerID + manifest cluster account/region", link)
    if link.target_id:
        instance = view.entity(link.target_id)
        entry["backed_by"]["cloud_resource"] = cloud_resource_context(view, instance)
        path_resources.setdefault(instance.id, {"entity": instance, "roles": []})["roles"].append(
            f"hosts Pod {pod.value('name')} via Node {node.value('name')}"
        )
    else:
        stops.append({"at": f"Node {node.value('name')} -> EC2 instance", "status": link.status, "why": link.detail})
    others = [view.entity(l.source_id) for l in view.links_to(node.id, ("scheduled_on",)) if l.source_id != pod.id]
    entry["other_pods_on_node"] = [
        {"name": o.value("name"), "namespace": o.value("namespace"), "uid": o.key[1], "meaning": "shares the node; placement only"}
        for o in others
    ]
    return entry


def _responsibility(app: Entity, path_resources: dict) -> dict:
    teams = app.values("owner_team")
    operators = []
    for entity_id in sorted(path_resources):
        entity = path_resources[entity_id]["entity"]
        values = entity.values("operator_team")
        operators.append(
            {
                "arn": entity.key[0],
                "kind": entity.kind,
                "roles_in_this_answer": path_resources[entity_id]["roles"],
                "operator_team_statements": _claims(entity, "operator_team"),
                "distinct_values": values,
                "conflict": len(values) > 1,
                "statement_kind": "source_statement",
                "meaning": (
                    "Who the source says operates this infrastructure resource. Hosting the application's pods, or being its declared "
                    "dependency, does not transfer application responsibility to this team nor infrastructure responsibility to the application team."
                ),
            }
        )

    conclusions = [f"Application team: {', '.join(teams) or 'not stated'} (catalog statement, not derived)."]
    hosts = [o for o in operators if any(r.startswith("hosts") for r in o["roles_in_this_answer"])]
    if hosts:
        statements = "; ".join(
            f"{parse_arn(o['arn']).resource_id}: {', '.join(o['distinct_values']) or 'no statement'}"
            + (" (sources disagree; see statements)" if o["conflict"] else "")
            for o in hosts
        )
        conclusions.append(
            "Derived: the application's observed pods are placed on nodes backed by "
            + ", ".join(parse_arn(o["arn"]).resource_id for o in hosts)
            + f". Operator statements for those instances: {statements}."
        )
        conclusions.append(
            "Derived: placement is a scheduling fact at the Kubernetes observation time. It identifies the infrastructure currently hosting "
            "the workload; it does not establish an application dependency on those specific instances, and it does not make their operator responsible for the application."
        )
    for o in operators:
        if "declared dependency target" in o["roles_in_this_answer"]:
            conclusions.append(
                f"Derived: declared dependency {o['arn']} resolves to an observed {o['kind']} whose operator statement is "
                f"{', '.join(o['distinct_values']) or 'not stated'}. The dependency itself is the catalog's declaration; the extract contains no observed connection from the workload to it."
            )
    return {
        "application_team": {
            "teams": teams,
            "statement_kind": "source_statement",
            "statement": "service_catalog.owner_team",
            "source": _refs(app),
            "meaning": "The catalog's statement of application responsibility. It says nothing about who operates nodes or databases.",
        },
        "infrastructure_operators": operators,
        "derived_conclusions": conclusions,
    }


def _question_b_limitations(view: TenantView, answer: dict, path_resources: dict) -> None:
    limitations = answer["limitations"]
    for col in view.collections:
        fresh = col.freshness(view.as_of)
        if fresh.status == "stale":
            limitations.append(
                f"Collection {col.snapshot_id} ({col.source_family}) was observed at {fresh.observed_at}, {fresh.age_seconds} s before the "
                f"evaluation time and beyond its {fresh.budget_seconds} s budget; statements taken from it describe that time."
            )
        elif not col.available:
            limitations.append(
                f"Collection {col.snapshot_id} ({col.source_family}, scope {json.dumps(col.scope, sort_keys=True)}) was not provided; "
                "references into that scope cannot be resolved or ruled out."
            )
    stale_bindings = sorted(
        {
            f"{_resource_id(item['entity'])} ({b['workspace_id']} @ {b['observed_at']})"
            for item in path_resources.values()
            for b in terraform_binding(view, item["entity"])["managed_bindings"]
            if b["freshness"]["status"] == "stale"
        }
    )
    if stale_bindings:
        limitations.append(
            "Terraform bindings on the path come from stale state and show management at the state's observation time only: " + "; ".join(stale_bindings) + "."
        )
    if answer["path_stops"]:
        limitations.append("One or more hops did not resolve; each is listed in path_stops with the reason and the collections consulted.")
    limitations.append(
        "Kubernetes Services, endpoints, network policies, and live traffic are not in the extract; the answer shows placement and declarations, not an observed runtime dependency."
    )
    limitations.append("VPC, subnet, and security-group objects are not supplied; the path ends at the EC2 and RDS instances.")
    limitations.append("The catalog extract carries at most one workload reference and one dependency per entry; undeclared dependencies may exist.")


# Issues report
def issues_report(store: Store, tenant_id: str) -> dict:
    view = store.view(tenant_id)
    as_of = view.as_of
    issues: list[dict] = []

    def add(category: str, severity: str, summary: str, *, evidence=(), entities=(), affects=(), resolving_evidence=None) -> None:
        issues.append(
            {
                "id": f"{category}-{len(issues) + 1:03d}",
                "category": category,
                "severity": severity,
                "summary": summary,
                "entities": list(entities),
                "evidence": list(evidence),
                "affects": list(affects),
                "resolving_evidence": resolving_evidence,
            }
        )

    for col in view.collections:
        fresh = col.freshness(as_of)
        scope = json.dumps(col.scope, sort_keys=True)
        if not col.available:
            add(
                "coverage",
                "high",
                f"Collection {col.snapshot_id} ({col.source_family}, scope {scope}) was not provided. Nothing in that scope can be confirmed or ruled out; an unavailable collection is not an empty inventory.",
                affects=FAMILY_AFFECTS[col.source_family],
                resolving_evidence=f"A successful {col.source_family} collection from {col.source_instance_id} with the declared scope.",
            )
        elif fresh.status == "stale":
            add(
                "freshness",
                "high",
                f"Collection {col.snapshot_id} ({col.source_family}, scope {scope}) was observed at {fresh.observed_at}, {fresh.age_seconds} s before the evaluation time and over its {fresh.budget_seconds} s budget. Statements from it describe that time, not the evaluation time.",
                affects=FAMILY_AFFECTS[col.source_family],
                resolving_evidence=f"A refreshed {col.source_family} collection from {col.source_instance_id} observed within {fresh.budget_seconds} s of the evaluation time.",
            )

    for link in sorted(view.links(), key=lambda l: (l.kind, l.source_id, json.dumps(l.reference, sort_keys=True))):
        if link.status == RESOLVED:
            continue
        source = view.entity(link.source_id)
        summary = f"{link.kind} from {source.id}: {link.status}. {link.detail}".strip()
        if link.candidates:
            lookalikes = ", ".join(str(c.get("instance_id") or c.get("entity_id")) for c in link.candidates)
            summary += f" Look-alike records were listed but not used: {lookalikes}."
        add(
            "unresolved_link",
            SEVERITY_BY_STATUS.get(link.status, "medium"),
            summary,
            evidence=[link.stated_by.to_dict()],
            entities=[source.id],
            affects=LINK_AFFECTS.get(link.kind, []),
            resolving_evidence=RESOLVING_EVIDENCE.get((link.kind, link.status), DEFAULT_RESOLVING_EVIDENCE),
        )

    for entity in view.entities():
        for attribute, claims in entity.conflicts().items():
            statements = "; ".join(f"{c.value!r} per {c.ref.snapshot_id} observed {c.ref.observed_at}" for c in claims)
            add(
                "conflicting_observation",
                "medium",
                f"{entity.id}: sources disagree on {attribute}: {statements}. Neither value is preferred; both are shown wherever the attribute is reported.",
                evidence=[c.ref.to_dict() for c in claims],
                entities=[entity.id],
                affects=["question_a", "question_b"],
                resolving_evidence=f"A current observation of {attribute} from the older source, or a designated source of record for that attribute.",
            )

    for link in view.links(("terraform_data_reference",)):
        add(
            "misleading_similarity",
            "info",
            f"Terraform data source {view.entity(link.source_id).value('address')} references {link.reference.get('id')}; a mode=data record reads an existing object and was not treated as a management binding.",
            evidence=[link.stated_by.to_dict()],
            entities=[link.source_id] + ([link.target_id] if link.target_id else []),
            affects=["question_a"],
        )

    instances = view.entities("ec2_instance")
    for i, a in enumerate(instances):
        for b in instances[i + 1 :]:
            shared = [attr for attr in ("name", "private_ip") if set(a.values(attr)) & set(b.values(attr))]
            if shared:
                add(
                    "misleading_similarity",
                    "info",
                    f"{a.value('instance_id')} (account {parse_arn(a.key[0]).account_id}) and {b.value('instance_id')} (account {parse_arn(b.key[0]).account_id}) share {', '.join(shared)}; they are distinct provider objects and were never matched on those attributes.",
                    entities=[a.id, b.id],
                    affects=["question_a", "question_b"],
                )

    by_name: dict[str, list[Entity]] = {}
    for node in view.entities("k8s_node"):
        by_name.setdefault(node.value("name"), []).append(node)
    for name, nodes in sorted(by_name.items()):
        if len(nodes) > 1:
            add(
                "misleading_similarity",
                "info",
                f"Node name {name} is used in clusters {sorted(n.key[0] for n in nodes)}; Node identity is (cluster, uid) and Pods resolve only to Nodes in their own cluster.",
                entities=[n.id for n in nodes],
                affects=["question_b"],
            )

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["category"]] = counts.get(issue["category"], 0) + 1
    cross_tenant = [o for o in store.cross_tenant_observations if tenant_id in o["tenants"]]
    return {
        "report": "issues",
        "query_scope": {"tenant_id": tenant_id},
        "evaluation_time": iso(as_of),
        "counts": counts,
        "issues": issues,
        "cross_tenant_observations": {
            "visibility": "platform operators only; never used in tenant answers",
            "note": (
                "The same provider object (ARN) was observed under more than one tenant. An external account is not a tenant "
                "identifier; each tenant's answers use only that tenant's collections."
            ),
            "items": cross_tenant,
        },
    }
