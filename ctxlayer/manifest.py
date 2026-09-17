"""Manifest loading: collections, their scopes, and freshness relative to the fixed evaluation time."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse an offset-aware timestamp to UTC; offsets other than Z do occur."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError(f"timestamp without UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Freshness:
    status: str  # fresh | stale | unavailable
    observed_at: Optional[str]
    age_seconds: Optional[int]
    budget_seconds: int

    @property
    def within_budget(self) -> Optional[bool]:
        return None if self.age_seconds is None else self.age_seconds <= self.budget_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observed_at": self.observed_at,
            "age_seconds": self.age_seconds,
            "budget_seconds": self.budget_seconds,
            "within_budget": self.within_budget,
        }


@dataclass(frozen=True)
class Collection:
    """One manifest snapshot entry: the trusted metadata context for every record it contains."""

    snapshot_id: str
    source_family: str
    source_instance_id: str
    tenant_id: str
    payload_path: Optional[str]
    observed_at: Optional[datetime]
    exported_at: Optional[datetime]
    freshness_budget_seconds: int
    status: str
    coverage: str
    scope: dict[str, Any]

    @property
    def available(self) -> bool:
        return self.status == "success" and self.payload_path is not None

    def freshness(self, as_of: datetime) -> Freshness:
        if self.observed_at is None:
            return Freshness("unavailable", None, None, self.freshness_budget_seconds)
        age = int((as_of - self.observed_at).total_seconds())
        status = "fresh" if age <= self.freshness_budget_seconds else "stale"
        return Freshness(status, iso(self.observed_at), age, self.freshness_budget_seconds)

    def summary(self, as_of: datetime) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source_family": self.source_family,
            "source_instance_id": self.source_instance_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "coverage": self.coverage,
            "payload_path": self.payload_path,
            "scope": self.scope,
            "freshness": self.freshness(as_of).to_dict(),
        }


@dataclass
class Manifest:
    root: Path
    as_of: datetime
    tenants: list[str]
    query_defaults: dict[str, str]
    collections: list[Collection]

    @classmethod
    def load(cls, root: Path) -> "Manifest":
        with (root / "manifest.json").open(encoding="utf-8") as handle:
            raw = json.load(handle)
        collections = [
            Collection(
                snapshot_id=entry["snapshot_id"],
                source_family=entry["source_family"],
                source_instance_id=entry["source_instance_id"],
                tenant_id=entry["tenant_id"],
                payload_path=entry["payload_path"],
                observed_at=parse_timestamp(entry["observed_at"]),
                exported_at=parse_timestamp(entry["exported_at"]),
                freshness_budget_seconds=entry["freshness_budget_seconds"],
                status=entry["status"],
                coverage=entry["coverage"],
                scope=dict(entry["scope"]),
            )
            for entry in raw["snapshots"]
        ]
        as_of = parse_timestamp(raw["as_of"])
        if as_of is None:
            raise ValueError("manifest.json: as_of is required")
        return cls(
            root=root,
            as_of=as_of,
            tenants=list(raw["tenants"]),
            query_defaults=dict(raw["query_defaults"]),
            collections=collections,
        )

    def collection(self, snapshot_id: str) -> Collection:
        for collection in self.collections:
            if collection.snapshot_id == snapshot_id:
                return collection
        raise KeyError(snapshot_id)

    def for_tenant(self, tenant_id: str) -> list[Collection]:
        return [c for c in self.collections if c.tenant_id == tenant_id]
