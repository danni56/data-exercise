"""Focused tests for the decisions that matter; each docstring names the bug it would catch."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ctxlayer import build, issues_report, question_a, question_b  # noqa: E402
from ctxlayer.ingest import ingest  # noqa: E402
from ctxlayer.resolve import resolve  # noqa: E402

INPUT_FILES = ("manifest.json", "aws_inventory.json", "k8s_resources.json", "service_catalog.csv")
I = "i-000000000000001"  # prod-account instance id prefix


def copy_inputs(target: Path) -> Path:
    for name in INPUT_FILES:
        shutil.copy(ROOT / name, target / name)
    shutil.copytree(ROOT / "terraform_state", target / "terraform_state")
    return target


def edit_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def snapshot_ids(obj) -> set[str]:
    """Every snapshot_id cited anywhere in an answer (sources, evidence refs, scopes consulted)."""
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "snapshot_id" and isinstance(inner, str):
                    found.add(inner)
                else:
                    walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(obj)
    return found


def instances_by_id(answer_a: dict) -> dict:
    return {entry["instance_id"]: entry for entry in answer_a["instances"]}


def pods_on_path(answer_b: dict) -> dict:
    """pod name -> {"node": node name, "instance_id": ..., "cloud_resource": ...} for every resolved hop."""
    out = {}
    for workload in answer_b["workloads"]:
        for replicaset in workload.get("deployment", {}).get("replicasets", []):
            for pod in replicaset["pods"]:
                node = pod["scheduled_on"].get("node", {})
                resource = node.get("backed_by", {}).get("cloud_resource")
                out[pod["name"]] = {
                    "node": node.get("name"),
                    "instance_id": resource["observed"]["instance_id"][0]["value"] if resource else None,
                    "cloud_resource": resource,
                }
    return out


class ModelFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = build(ROOT)


class TestJustifiedMatches(ModelFixture):
    def test_running_prod_instances_and_managed_binding_evidence(self):
        """Catches: mode=data counted as a binding, wrong workspace attribution, string-compared freshness,
        stopped instances leaking into the running list, state-only resources silently dropped."""
        answer = question_a(self.store, "acme", "prod")
        by_id = instances_by_id(answer)
        self.assertEqual(sorted(by_id), [f"{I}0{n}" for n in range(1, 6)])
        self.assertEqual([e["instance_id"] for e in answer["excluded_instances"]], [f"{I}06"])

        binding = by_id[f"{I}02"]["terraform_binding"]
        self.assertEqual(binding["status"], "binding_found")
        (match,) = binding["managed_bindings"]
        self.assertEqual(match["address"], "module.workers.aws_instance.pool[1]")
        self.assertEqual(match["workspace_id"], "acme-prod-core")
        self.assertEqual(match["observed_at"], "2026-09-02T09:00:00Z")
        self.assertEqual(match["freshness"]["status"], "stale")
        self.assertEqual(match["freshness"]["age_seconds"], 14 * 86400 + 3 * 3600)

        absent = by_id[f"{I}04"]["terraform_binding"]
        self.assertEqual(absent["status"], "no_binding_in_supplied_scope")
        self.assertIn("does not show the object is unmanaged", absent["interpretation"])
        self.assertEqual([t["instance_id"] for t in answer["terraform_managed_instances_not_observed"]], ["i-00000000000000099"])

    def test_payments_api_path_reaches_nodes_instances_and_database(self):
        """Catches: ownerReferences matched by name instead of UID (staging reuses the ReplicaSet name),
        Pod->Node matched across clusters (both clusters have ip-10-0-4-118), dependency not resolved by ARN,
        application team conflated with the node operator."""
        answer = question_b(self.store, "acme", "prod", "payments-api")
        self.assertEqual(answer["application"]["service_id"], "svc-payments-prod")
        self.assertEqual(answer["responsibility"]["application_team"]["teams"], ["team-payments"])

        (dependency,) = answer["declared_dependencies"]
        self.assertEqual(dependency["resolution"]["status"], "resolved")
        self.assertEqual(dependency["resource"]["operator_team"]["distinct_values"], ["team-data-platform"])
        self.assertEqual(dependency["resource"]["terraform"]["status"], "binding_found")

        pods = pods_on_path(answer)
        self.assertEqual(
            {name: p["instance_id"] for name, p in pods.items()},
            {"payments-api-7c9d-a": f"{I}01", "payments-api-7c9d-b": f"{I}02"},
        )
        self.assertEqual(answer["path_stops"], [])
        operators = {o["arn"].rsplit("/", 1)[-1]: o for o in answer["responsibility"]["infrastructure_operators"] if o["kind"] == "ec2_instance"}
        self.assertEqual(operators[f"{I}02"]["distinct_values"], ["team-platform"])
        self.assertNotIn("team-platform", answer["responsibility"]["application_team"]["teams"])


class TestMisleadingSimilarity(ModelFixture):
    def test_data_source_and_name_lookalike_do_not_bind_or_link(self):
        """Catches: matching Terraform records on values.id without checking mode; linking an EC2 instance to
        an application because its Name tag equals the application name."""
        entry = instances_by_id(question_a(self.store, "acme", "prod"))[f"{I}03"]
        binding = entry["terraform_binding"]
        self.assertEqual(binding["status"], "no_binding_in_supplied_scope")
        self.assertEqual(binding["managed_bindings"], [])
        self.assertEqual([d["address"] for d in binding["data_source_references"]], ["data.aws_instance.lookup"])

        answer = question_b(self.store, "acme", "prod", "payments-api")
        self.assertEqual([l["instance_id"] for l in answer["not_linked_lookalikes"]], [f"{I}03"])
        self.assertNotIn(f"{I}03", {p["instance_id"] for p in pods_on_path(answer).values()})

    def test_shared_account_and_instance_id_do_not_cross_tenants(self):
        """Catches: keying entities by provider identity alone (acme's Terraform binding would then answer for
        bravo), or enforcing tenant scope only at the output layer while resolution mixes tenants."""
        bravo = question_a(self.store, "bravo", "prod")
        by_id = instances_by_id(bravo)
        self.assertEqual(list(by_id), [f"{I}01"])
        binding = by_id[f"{I}01"]["terraform_binding"]
        self.assertEqual(binding["status"], "evidence_unavailable")
        self.assertEqual(binding["managed_bindings"], [])
        self.assertEqual([s["snapshot_id"] for s in binding["terraform_scopes_consulted"]], ["tf-bravo-prod-20260916"])
        # acme's stale tag must not reach bravo's entity.
        self.assertEqual([c["value"] for c in by_id[f"{I}01"]["observed"]["operator_team"]], ["team-platform"])
        self.assertEqual(by_id[f"{I}01"]["conflicting_observations"], [])

        manifest = self.store.manifest
        for tenant in manifest.tenants:
            allowed = {c.snapshot_id for c in manifest.for_tenant(tenant)}
            for answer in (question_a(self.store, tenant, "prod"), question_b(self.store, tenant, "prod", "payments-api")):
                self.assertLessEqual(snapshot_ids(answer), allowed, f"{tenant} answer cites another tenant's collection")

    def test_node_without_provider_id_is_not_matched_by_ip(self):
        """Catches: falling back to InternalIP == PrivateIpAddress matching (two prod instances share 10.0.9.9)."""
        view = self.store.view("acme")
        node = next(n for n in view.entities("k8s_node") if n.value("name") == "ip-10-0-9-9")
        (link,) = view.links_from(node.id, ("backed_by",))
        self.assertEqual(link.status, "insufficient_identifier")
        self.assertIsNone(link.target_id)
        self.assertEqual(sorted(c["instance_id"] for c in link.candidates), [f"{I}04", f"{I}05"])

        # Counterexample: with a providerID the same rule resolves.
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_inputs(Path(tmp))

            def mutate(data):
                for group in data["collections"]:
                    for item in group["items"]:
                        if item["kind"] == "Node" and item["metadata"]["name"] == "ip-10-0-9-9":
                            item["spec"]["providerID"] = f"aws:///us-east-1b/{I}05"

            edit_json(root / "k8s_resources.json", mutate)
            view = build(root).view("acme")
            node = next(n for n in view.entities("k8s_node") if n.value("name") == "ip-10-0-9-9")
            (link,) = view.links_from(node.id, ("backed_by",))
            self.assertEqual(link.status, "resolved")
            self.assertTrue(link.target_id.endswith(f"/{I}05"))
            self.assertEqual(link.candidates, [])


class TestQualifiedUncertainty(ModelFixture):
    def test_conflicting_operator_team_is_reported_with_both_sources(self):
        """Catches: last-writer-wins or newest-wins attribute merging that hides the disagreement."""
        answer = question_b(self.store, "acme", "prod", "payments-api")
        resource = pods_on_path(answer)["payments-api-7c9d-a"]["cloud_resource"]
        self.assertTrue(resource["operator_team"]["conflict"])
        statements = {(c["value"], c["source"]["observed_at"]) for c in resource["operator_team"]["claims"]}
        self.assertEqual(
            statements,
            {("team-platform", "2026-09-16T11:50:00Z"), ("team-legacy-platform", "2026-09-02T09:00:00Z")},
        )
        conflicts = [i for i in issues_report(self.store, "acme")["issues"] if i["category"] == "conflicting_observation"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn(f"{I}01", conflicts[0]["entities"][0])
        entry = instances_by_id(question_a(self.store, "acme", "prod"))[f"{I}01"]
        self.assertEqual([c["attribute"] for c in entry["conflicting_observations"]], ["operator_team"])

    def test_unavailable_and_uncovered_collections_are_not_empty_inventories(self):
        """Catches: treating a not_provided collection as an empty payload; reporting 'not found' without checking
        whether any supplied collection covered the scope; comparing timestamps as strings (the staging
        Kubernetes collection uses a -04:00 offset)."""
        answer = question_b(self.store, "bravo", "prod", "payments-api")
        (workload,) = answer["workloads"]
        self.assertEqual(workload["resolution"]["status"], "evidence_unavailable")
        self.assertEqual(workload["resolution"]["scopes_consulted"], ["k8s-bravo-prod-20260916"])
        (dependency,) = answer["declared_dependencies"]
        self.assertEqual(dependency["resolution"]["status"], "no_covering_collection")
        self.assertEqual(len(answer["path_stops"]), 2)

        view = self.store.view("acme")
        reports = view.get("catalog_application", ("svc-reports-prod",))
        (link,) = view.links_from(reports.id, ("runs_as_workload",))
        self.assertEqual(link.status, "not_observed_in_scope")  # complete collection: absence is meaningful

        manifest = self.store.manifest
        staging = manifest.collection("k8s-acme-staging-20260916T115300Z").freshness(manifest.as_of)
        self.assertEqual((staging.status, staging.age_seconds), ("fresh", 420))
        self.assertEqual(manifest.collection("tf-acme-prod-20260902T090000Z").freshness(manifest.as_of).status, "stale")
        self.assertEqual(issues_report(self.store, "bravo")["counts"]["coverage"], 2)


class TestRepeatability(unittest.TestCase):
    def test_rebuild_and_reprocess_are_idempotent(self):
        """Catches: set/dict ordering leaking into output, generated ids, duplicate entities, links, or claims
        when the same inputs are processed again."""

        def render(store) -> str:
            tenants = store.manifest.tenants
            return json.dumps(
                [question_a(store, t, "prod") for t in tenants]
                + [question_b(store, t, "prod", "payments-api") for t in tenants]
                + [issues_report(store, t) for t in tenants]
            )

        first, second = build(ROOT), build(ROOT)
        self.assertEqual(render(first), render(second))

        counts = (len(first.entities()), len(first.links()))
        ingest(first)
        resolve(first)
        self.assertEqual((len(first.entities()), len(first.links())), counts)
        self.assertEqual(render(first), render(second))
        instance = first.view("acme").get("ec2_instance", (f"arn:aws:ec2:us-east-1:111111111111:instance/{I}01",))
        self.assertEqual(len(instance.claims_for("operator_team")), 2)


if __name__ == "__main__":
    unittest.main()
