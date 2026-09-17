# Context layer: run instructions and design note

## Run

Python 3.10+ and the standard library only. From the repository root:

```bash
python -m ctxlayer                    # builds the model, writes output/<tenant>/{question_a,question_b,issues}.json for every tenant
python -m ctxlayer --tenant acme      # one tenant; --environment / --application override the manifest query_defaults
python -m unittest discover -s tests -v   # 8 tests (pytest tests/ also works)
python tools/check_inputs.py          # supplied input-envelope check (optional)
```

Generated answers for the default request (tenant `acme`, environment `prod`, application `payments-api`) are in `output/acme/`; `output/bravo/` shows the same questions for the tenant whose Terraform and Kubernetes collections were not provided.

## Schema

| Entity kind | Identity (within tenant) | From | Main claims |
|---|---|---|---|
| `ec2_instance` | ARN built from InstanceId + collection account/region | AWS inventory | state, private_ip, vpc_id, name, environment_tag, operator_team |
| `db_instance` | DBInstanceArn | AWS inventory | identifier, status, environment_tag, operator_team |
| `tf_resource` | (workspace_id, address) | Terraform state | mode, type, provider_id, arn, tags, lineage, serial |
| `k8s_node` / `k8s_deployment` / `k8s_replicaset` / `k8s_pod` | (cluster_id, metadata.uid) | Kubernetes | name, namespace, owner_references, node_name, provider_id, internal_ip, phase, replicas |
| `catalog_application` | service_id | Catalog CSV | service_name, environment, owner_team, workload_reference, declared_dependency_arn |

| Link kind | From -> to | Source statement | Resolution rule |
|---|---|---|---|
| `terraform_manages` / `terraform_data_reference` | tf_resource -> cloud resource | `values.arn` (+ `mode`) | ARN identity within tenant |
| `controlled_by` | ReplicaSet -> Deployment, Pod -> ReplicaSet | `metadata.ownerReferences` | UID within cluster |
| `scheduled_on` | Pod -> Node | `spec.nodeName` | Node name within cluster |
| `backed_by` | Node -> ec2_instance | `spec.providerID` + manifest cluster account/region | constructed ARN |
| `runs_as_workload` | application -> Deployment | catalog cluster/namespace/deployment_name | name within cluster+namespace |
| `declares_dependency` | application -> cloud resource | `declared_dependency_arn` | ARN identity |

Every claim and link carries a `SourceRef` (file, JSON pointer or CSV line, snapshot_id, observed_at). Link status is one of `resolved`, `not_observed_in_scope`, `evidence_unavailable`, `no_covering_collection`, `insufficient_identifier`, `ambiguous`.

## Model and design

The model is a small in-memory store of typed entities, claims, and links (`ctxlayer/model.py`), rebuilt deterministically from the files. I chose typed objects over a relational or graph engine because the hard part is not query power but keeping three things attached to every fact: which record said it, when it was observed, and whether the reference resolved. Dataclasses make that explicit and testable; a database would add setup without changing the reasoning.

An **entity** is one object as seen by one tenant. A **claim** is one source's statement of an attribute, so an attribute with two disagreeing claims (i-101's `operator_team`: AWS says `team-platform` on 2026-09-16, Terraform says `team-legacy-platform` on 2026-09-02) stays a visible disagreement rather than a merged value. A **link** is a reference one record makes to another object, kept even when it does not resolve, with a status that says *why*.

Rules the implementation preserves:

1. **Tenant is the outermost identity scope.** `Store.add_link` refuses links whose endpoints belong to different tenants, and queries only read through a `TenantView` that will not hand out another tenant's entities, links, or collections. The same provider object observed under two tenants (i-101 in account 111111111111 for both `acme` and `bravo`) is two entities; bravo's answer never sees acme's Terraform binding.
2. **Absence is only asserted inside a complete, available collection.** A reference that fails to resolve gets `not_observed_in_scope` only when a `success/complete` collection covers the referenced account/region/type (or cluster/kind/namespace); otherwise it is `evidence_unavailable` or `no_covering_collection`. This is what separates "no Terraform binding for i-104 in acme-prod-core" from "no Terraform evidence at all for bravo".
3. **Labels and addresses are never identity.** Name tags, Node names, and private IPs are attributes; they never merge or match records. `mode=data` Terraform records are never bindings.

## Consequential decisions

**Tenant-scoped entities rather than one entity per ARN with tenant-tagged claims.** The alternative is more compact and shows both observations of i-101 in one place. I chose separate entities so tenant enforcement holds structurally in the store instead of relying on every query to filter claims. The cross-tenant fact is still recorded, but only in the issues report's `cross_tenant_observations` section, which is marked platform-only. I would revisit this if tenants are guaranteed disjoint by account (the split becomes redundant) or if a cross-tenant reconciliation use case appears.

**Coverage-derived statuses computed from manifest scopes, rather than a resolved/unresolved boolean.** The statuses depend on scope predicates (`ctxlayer/scope.py`) that mirror the manifest's scope fields: right for this bounded contract, brittle if scopes gain filters that are not field matches (tag-based selection, partial coverage). If `coverage: partial` appears or scope shapes diverge across source instances, I would move to per-collection coverage functions supplied by each ingester.

## Verification

Assumption checked: a Node without `spec.providerID` could fall back to matching `InternalIP` against `PrivateIpAddress`. I queried the built model for instances sharing `10.0.9.9` and found two in the same account (i-104 in `vpc-…0001`, i-105 in `vpc-…0002`), and the prod/staging pair both use `10.0.4.118`. IP matching is therefore not a resolution rule; look-alikes appear only as `candidates_not_used` on the link, so a reader sees why the hop stopped. The test builds a modified copy with a `providerID` added and confirms the same rule then resolves to exactly i-105. Along the way I also confirmed the `-04:00` staging timestamp parses to an age of 420 s (within budget) rather than being compared as a string.

## Readiness and next steps

A read-only internal consumer may rely on: tenant isolation of answers and evidence; the identity rules above; the link-status vocabulary and its meanings (emitted in each answer); every claim carrying a file locator and observation time; and staleness being flagged wherever a stale collection contributes. They should not read `binding_found` as "managed now": the only Terraform state for acme prod is 14 days past its 24 h budget, so Question A's findings, positive and negative, describe 2026-09-02.

Highest-risk gap: that stale state is the sole management evidence, and the state also references an instance (i-099) the complete inventory no longer contains, which suggests the state and the inventory have drifted. Next two changes: (1) ingest a fresh acme-prod state and compare `lineage`/`serial` so the layer can say whether the state moved, not just that it is old; (2) add Service/Endpoints kinds to the Kubernetes collection so Question B can substantiate a runtime path from `payments-api` to `payments-db` instead of stopping at a catalog declaration plus placement.

## Time and unfinished work

Roughly: reading the brief and extract notes 40 min; model, ingest, and resolution 1 h 15; queries and output shaping 1 h; tests 30 min; this note 30 min. Unfinished: the issues report's `affects` field is by link kind, not by whether the entity is on a queried path; an ambiguous catalog match answers for the first `service_id` and lists the rest; inventory collections that disagree on an instance's state are noted but not reconciled; no incremental processing or history.
