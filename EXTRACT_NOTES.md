# Source extract notes

The exercise instructions are in `README.md`. This file documents the supplied input formats and collection metadata. It does not prescribe a model, storage engine, or output schema.

All data is synthetic. Identifiers, teams, accounts, and infrastructure names are fictional. No live access, credentials, API calls, or additional downloads are needed. These are selected-field extracts, not complete native AWS, Terraform, or Kubernetes exports.

## Files and local check

| Path | Format |
|---|---|
| `manifest.json` | Collection metadata and evaluation context |
| `aws_inventory.json` | JSON object containing `collections` |
| `terraform_state/acme-production.json` | One flattened JSON state extract |
| `terraform_state/acme-staging.json` | One flattened JSON state extract |
| `k8s_resources.json` | JSON object containing `collections` |
| `service_catalog.csv` | UTF-8 CSV with a header row |
| `tools/check_inputs.py` | Optional, read-only input-format check |

From the repository root, run the optional check using Python 3.10 or newer:

```bash
python3 tools/check_inputs.py
```

The checker uses only Python's standard library. It checks file readability, required envelope fields, and manifest/payload membership. It does not execute candidate code, resolve entities, calculate answers, or replace the tests requested in the brief. No application scaffold or dependencies are imposed on your solution.

All paths in the manifest are relative to this repository root. JSON files are UTF-8. CSV values are strings; blank cells mean a value was not supplied.

## Evaluation context

`manifest.json` sets `as_of` to `2026-09-16T12:00:00Z`. Use this fixed evaluation time regardless of when you run the exercise.

`query_defaults` supplies an initial request: tenant `acme`, environment `prod`, application name `payments-api`. Those are default query parameters, not instructions to discard other supplied tenants or environments. Your query interface and output structure are your choice.

## Manifest contract

The root contains `extract_version`, `schema_version`, `as_of`, `extract_notes`, `tenants`, `query_defaults`, and `snapshots`. Version fields describe these input files, not a required versioning scheme for your model.

Each `snapshots` entry describes one logical collection:

| Field | Meaning |
|---|---|
| `snapshot_id` | Opaque identifier for this collection entry; links metadata to its payload |
| `source_family` | `aws`, `terraform`, `kubernetes`, or `catalog` |
| `source_instance_id` | The logical collector, workspace connection, or catalog across collections |
| `tenant_id` | Trusted internal tenant context for this collection |
| `payload_path`, `format` | Relative file path and its serialization; unavailable input has a null path |
| `observed_at` | When the collector actually observed the supplied values |
| `exported_at` | When the extract was packaged; separate from observation time |
| `freshness_budget_seconds` | Maximum acceptable observation age at `as_of`; equality is within budget |
| `status` | `success` or `not_provided` in these inputs |
| `coverage` | `complete` or `unknown` in these inputs |
| `scope` | The boundaries and filters of this collection |

All non-null timestamps include `Z` or an explicit UTC offset. The snapshot identifier is opaque; its spelling is not a replacement for timestamp fields. There is no input ingestion timestamp.

A `success` / `complete` collection contains the selected fields for all objects within its declared scope at `observed_at`. It says nothing about scopes not collected. `not_provided` / `unknown` means no payload was supplied, and both timestamps are null. No placeholder file is needed for an unavailable collection.

Scope fields are interpreted as follows:

- `account_id`, `region`, and `resource_types` constrain cloud and state collections. `workspace_id` further bounds a state extract. Optional `instance_ids` is an additional inventory filter. Collections do not establish coverage of other workspaces.
- `cluster_id`, `kinds`, and `namespaces` bound Kubernetes collections. Namespace filtering applies to namespaced kinds; Nodes are cluster-scoped. The selected Nodes are not restricted to those hosting a supplied Pod. The accompanying account and region establish this fixture's cluster cloud context.
- `environment` is the authoritative environment classification for the objects in that cloud, state, or Kubernetes collection. Catalog rows declare their own `environment`, within the collection's `environments` list. Do not infer environment from name spelling.

The manifest is trusted for tenant context. An external provider account is not the internal tenant identifier. Collection completeness and freshness are metadata about supplied evidence, not instructions to prefer one source's values in every situation.

## Associating payloads with collections

`aws_inventory.json` and `k8s_resources.json` each contain a top-level `collections` array. Each member has a `snapshot_id` that refers to the corresponding manifest entry. Use that member's metadata context for its records, not one tenant context for the entire file.

Each Terraform file carries its own top-level `snapshot_id`. Each catalog row has a `snapshot_id` column, so a single CSV can contain multiple collections. There is one payload group for every successful collection, and none for unavailable collections.

For evidence references, a file path plus a record locator is sufficient. JSON pointers or array indexes, source-native identifiers within a snapshot, and CSV row numbers are all available. Your implementation chooses its reference format.

## AWS selected fields

Each collection contains `instances` and `databases` arrays. An empty array applies only to that collection's declared resource-type scope.

| Object | Fields |
|---|---|
| EC2 instance | `InstanceId`, `State.Name`, `PrivateIpAddress`, `VpcId`, `Tags` |
| Database | `DBInstanceIdentifier`, `DBInstanceArn`, `DBInstanceStatus`, `Tags` |

`Tags` is normalized by the extractor to an array of `{ "Key": "...", "Value": "..." }` objects for both kinds. Keys are unique within one object's tags. `Name` is a display label; `Environment` is a source tag; `operator_team`, when present, is that source's statement about infrastructure operation. Omitted tags are not empty-string claims.

All supplied cloud resources use the AWS `aws` partition. EC2 ARNs follow `arn:aws:ec2:<region>:<account_id>:instance/<InstanceId>`; account and region come from the manifest. Database ARNs are supplied directly. Private IP addresses are network addresses, not provider identifiers. VPC identifiers refer to network scope; separate VPC objects are not supplied or required.

## Terraform selected fields

Each file contains `snapshot_id`, `lineage`, `serial`, and a flattened `resources` array. Each resource has `address`, `mode`, `type`, and `values`. Supported types are `aws_instance` and `aws_db_instance`.

`address` is the resource address within a workspace. `values.id` and `values.arn` identify the referenced provider object. `mode=managed` denotes a managed-resource record; `mode=data` denotes a data-source reference. Optional `values.tags` is a string-to-string mapping, unlike the AWS tag array.

`lineage` identifies the state lineage; `serial` is that state's revision counter. Neither is a wall-clock time. For these extracts, the manifest's `observed_at` is collector-supplied metadata from the last successful refresh represented by the values. It is not a native Terraform timestamp. `exported_at` does not imply another refresh. No HCL parsing, module traversal, provider authentication, or backend discovery is needed.

## Kubernetes selected fields

Each collection contains `items` with exactly the kinds listed in its manifest scope. The exercise includes selected fields only; reconstructing complete API objects is unnecessary.

| Field | Meaning in the extract |
|---|---|
| `apiVersion`, `kind` | Source object type |
| `metadata.uid` | Identifier of the object's incarnation |
| `metadata.name`, `metadata.namespace` | Name and, for namespaced objects, namespace |
| `metadata.ownerReferences` | Controller/lifecycle references, including target kind, name, and UID |
| `metadata.labels` | Source labels where supplied |
| `spec.replicas`, `spec.selector.matchLabels` | Selected workload specification fields |
| Pod `spec.nodeName` | Name of its assigned Node in the same cluster |
| Pod `status.phase` | Reported Pod phase |
| Node `spec.providerID` | Optional provider reference |
| Node `status.addresses` | Reported addresses, including `InternalIP` |

All supplied controller references point within the same cluster and namespace. ReplicaSets reference Deployments, and Pods reference ReplicaSets. A namespace's name is supplied on objects; separate Namespace records are outside this extract.

For these inputs, a Node provider reference has the format `aws:///<availability-zone>/<instance-id>`. The cluster's account and region are supplied by the manifest; the provider reference itself does not encode the account. An omitted provider reference is not an empty instance identifier. Kubernetes UIDs distinguish object incarnations; display names can be reused.

Services, readiness endpoints, disruption policies, scheduling capacity, and live traffic are not part of these files. No drain or availability simulation is required.

## Service catalog columns

| Column | Meaning |
|---|---|
| `snapshot_id` | Manifest collection for this row |
| `service_id` | Catalog application identifier within its tenant |
| `service_name` | Application display name |
| `environment` | Environment classification for this entry |
| `owner_team` | Catalog statement of application-team responsibility |
| `cluster_id`, `namespace`, `deployment_name` | A workload reference; `deployment_name` refers to a Kubernetes Deployment |
| `declared_dependency_arn` | A dependency stated by the catalog, when supplied |

Each entry has at most one workload reference and one dependency ARN in this bounded extract. This is a format simplification, not a required limitation of your model. Team names are opaque labels; there is no separate team directory. Blank cells mean not supplied, not an assertion that no team, workload, or dependency exists.
