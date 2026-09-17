"""Coverage reasoning: which supplied collections could have contained a referenced object."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from .identity import ARN_SERVICE_TO_KIND, KIND_TO_TF_TYPE, Arn
from .manifest import Collection

RESOLVED = "resolved"
NOT_OBSERVED = "not_observed_in_scope"
UNAVAILABLE = "evidence_unavailable"
NO_COVERAGE = "no_covering_collection"
INSUFFICIENT = "insufficient_identifier"
AMBIGUOUS = "ambiguous"

STATUS_MEANING = {
    RESOLVED: "The referenced object was observed in this tenant's supplied collections and the identifiers match.",
    NOT_OBSERVED: "A complete collection covers the referenced object's scope but did not contain it at observation time.",
    UNAVAILABLE: "The collection that would cover the referenced object was declared but not provided; absence cannot be inferred.",
    NO_COVERAGE: "No supplied collection for this tenant covers the referenced object's scope; absence cannot be inferred.",
    INSUFFICIENT: "The source record lacks the identifier needed to resolve the reference; similar-looking records are listed but not used.",
    AMBIGUOUS: "More than one observed object matches the reference; none is chosen.",
}

ScopePredicate = Callable[[dict], bool]


def coverage(collections: Iterable[Collection], family: str, covers: ScopePredicate) -> tuple[str, list[str]]:
    """Return (status, snapshot_ids consulted) for a reference that did not resolve."""
    matching = [c for c in collections if c.source_family == family and covers(c.scope)]
    ids = [c.snapshot_id for c in matching]
    if not matching:
        return NO_COVERAGE, ids
    if any(c.available for c in matching):
        return NOT_OBSERVED, ids
    return UNAVAILABLE, ids


def cloud_scope_covers(arn: Arn) -> ScopePredicate:
    """AWS inventory and Terraform state scopes: account, region, resource type, optional instance filter."""
    kind = ARN_SERVICE_TO_KIND.get(arn.service)
    tf_type: Optional[str] = KIND_TO_TF_TYPE.get(kind) if kind else None

    def covers(scope: dict) -> bool:
        if scope.get("account_id") != arn.account_id or scope.get("region") != arn.region:
            return False
        if tf_type is None or tf_type not in scope.get("resource_types", []):
            return False
        wanted = scope.get("instance_ids")
        if wanted and arn.service == "ec2" and arn.resource_id not in wanted:
            return False
        return True

    return covers


def k8s_scope_covers(cluster_id: str, kind: str, namespace: Optional[str]) -> ScopePredicate:
    """Kubernetes scopes: cluster, kind, and namespace for namespaced kinds (Nodes pass namespace=None)."""

    def covers(scope: dict) -> bool:
        if scope.get("cluster_id") != cluster_id or kind not in scope.get("kinds", []):
            return False
        if namespace is not None and namespace not in scope.get("namespaces", []):
            return False
        return True

    return covers
