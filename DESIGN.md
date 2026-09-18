# Context layer: how to run it, and why it looks like this

## Run

```bash
python -m ctxlayer                        # writes output/<tenant>/{question_a,question_b,issues}.json for every tenant
python -m ctxlayer --tenant acme          # one tenant; --environment / --application override the manifest defaults
python -m unittest discover -s tests -v   # 8 tests (pytest tests/ works too)
python tools/check_inputs.py              # optional input check from the brief
```

Python 3.10+, standard library only. Answers for the default request (acme / prod / payments-api) are in `output/acme/`. `output/bravo/` shows the same questions for a tenant whose Terraform and Kubernetes were not provided.

## Schema

| Entity | Identity (within a tenant) | Source | Main claims |
|---|---|---|---|
| `ec2_instance` | ARN from InstanceId + collection account/region | AWS | state, private_ip, vpc_id, name, environment_tag, operator_team |
| `db_instance` | DBInstanceArn | AWS | identifier, status, environment_tag, operator_team |
| `tf_resource` | (workspace_id, address) | Terraform | mode, type, provider_id, arn, tags, lineage, serial |
| `k8s_node` / `k8s_deployment` / `k8s_replicaset` / `k8s_pod` | (cluster_id, uid) | Kubernetes | name, namespace, owner_references, node_name, provider_id, internal_ip, phase, replicas |
| `catalog_application` | service_id | Catalog | service_name, environment, owner_team, workload_reference, declared_dependency_arn |

| Link | From -> to | Stated by | Resolved by |
|---|---|---|---|
| `terraform_manages` / `terraform_data_reference` | tf_resource -> cloud resource | `values.arn` + `mode` | ARN identity |
| `controlled_by` | ReplicaSet -> Deployment, Pod -> ReplicaSet | `ownerReferences` | UID within cluster |
| `scheduled_on` | Pod -> Node | `spec.nodeName` | Node name within cluster |
| `backed_by` | Node -> ec2_instance | `spec.providerID` + cluster account/region | constructed ARN |
| `runs_as_workload` | application -> Deployment | catalog cluster/namespace/deployment_name | name within cluster + namespace |
| `declares_dependency` | application -> cloud resource | `declared_dependency_arn` | ARN identity |

Every claim and link carries a source ref (file, JSON pointer or CSV line, snapshot_id, observed_at). Link status is one of `resolved`, `not_observed_in_scope`, `evidence_unavailable`, `no_covering_collection`, `insufficient_identifier`, `ambiguous`.

## Model and design

Everything is plain Python objects in memory, rebuilt from the files on every run (`ctxlayer/model.py`). Three ideas: an **entity** is one object as one tenant sees it; a **claim** is one source stating one attribute value, with a file locator and observation time; a **link** is one record pointing at another, kept even when it does not resolve, with a status that says why. I chose dataclasses over a database or graph library because the hard part here is not querying, it is never losing who said what and when. That fits in a few hundred lines and is easy to test.

Disagreements stay visible. i-101's `operator_team` is `team-platform` per AWS on 2026-09-16 and `team-legacy-platform` per Terraform on 2026-09-02. Both are reported; neither wins.

Rules the code enforces:

1. **Tenant first.** `Store.add_link` rejects links across tenants, and queries only see a `TenantView`. The same instance in a shared account (i-101 for both `acme` and `bravo`) is two entities; bravo never sees acme's Terraform binding.
2. **Absence needs a complete collection.** An unresolved reference is `not_observed_in_scope` only when a provided, complete collection covers that account/region/type (or cluster/kind/namespace). Otherwise it is `evidence_unavailable` or `no_covering_collection`. That is the difference between "no binding for i-104 in acme-prod-core" and "no Terraform evidence at all for bravo".
3. **Labels are not identity.** Name tags, Node names and private IPs never match records. `mode=data` is never a binding.

## Two decisions

**Entities are per tenant, not per ARN.** One entity per ARN with tenant-tagged claims would be more compact and would show both views of i-101 in one place. I split them so tenant isolation is a property of the store rather than something every query must get right. I would revisit this if tenants were guaranteed disjoint by account, or if someone needed cross-tenant reconciliation.

**Link status comes from manifest scopes.** The statuses rest on predicates in `ctxlayer/scope.py` that mirror the manifest scope fields. Simple and right for this contract, brittle if scopes grow filters that are not plain field matches. A `coverage: partial` value, or scope shapes that differ per source, would push me to per-collection coverage functions owned by each ingester.

## One thing I checked

I assumed a Node without `providerID` could fall back to matching `InternalIP` to `PrivateIpAddress`. The data says no: i-104 and i-105 share `10.0.9.9` in different VPCs, and prod and staging both use `10.0.4.118`. So IP matching is not a rule; look-alikes appear only as `candidates_not_used`. The test adds a `providerID` to a copy of the input and confirms the same rule then resolves to exactly i-105. I also checked that the `-04:00` staging timestamp parses to a 420 s age instead of being compared as a string.

## What you can rely on, and what you cannot

Safe to rely on: tenant isolation of answers and evidence; the identity rules above; the status vocabulary (each answer carries its meanings); a file locator and observation time on every claim; staleness flagged wherever a stale collection contributes.

Do not read `binding_found` as "managed now". The only Terraform state for acme prod is 14 days past its 24 h budget, so every Question A finding describes 2026-09-02. That state also lists i-099, which the complete inventory no longer has. That drift is the biggest risk.

Next: (1) ingest a fresh acme-prod state and compare `lineage` and `serial`, so the layer can say whether the state moved rather than only that it is old; (2) add Service and Endpoints objects to the Kubernetes collection so Question B can show a runtime path to `payments-db` instead of a catalog declaration plus placement.

## Time and loose ends

About four hours: 40 min reading, 1 h 15 on model, ingest and resolution, 1 h on queries and output, 30 min tests, 30 min this note. Not done: the issues report's `affects` is by link kind, not by whether the entity is on a queried path; an ambiguous catalog match answers for the first `service_id` and lists the rest; inventory collections that disagree on a state are noted, not reconciled; no incremental processing or history.
