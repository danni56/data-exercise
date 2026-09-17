"""Entities, claims, links, and the tenant-scoped store; identity rules are in DESIGN.md."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .manifest import Collection, Manifest


@dataclass(frozen=True)
class SourceRef:
    """Where a statement came from: file + locator, the collection it belongs to, and when it was observed."""

    file: str
    locator: str
    snapshot_id: str
    tenant_id: str
    observed_at: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "locator": self.locator,
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class Claim:
    """One source's statement of an attribute value."""

    value: Any
    ref: SourceRef

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.ref.to_dict()}


@dataclass
class Entity:
    kind: str
    tenant_id: str
    key: tuple[str, ...]
    refs: list[SourceRef] = field(default_factory=list)
    claims: dict[str, list[Claim]] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.tenant_id}:{self.kind}:" + "/".join(self.key)

    def add_ref(self, ref: SourceRef) -> None:
        if ref not in self.refs:
            self.refs.append(ref)

    def claim(self, attribute: str, value: Any, ref: SourceRef) -> None:
        new = Claim(value, ref)
        existing = self.claims.setdefault(attribute, [])
        if new not in existing:
            existing.append(new)

    def claims_for(self, attribute: str) -> list[Claim]:
        return list(self.claims.get(attribute, []))

    def values(self, attribute: str) -> list[Any]:
        """Distinct claimed values in first-seen order; more than one means the sources disagree."""
        distinct: list[Any] = []
        for claim in self.claims.get(attribute, []):
            if claim.value not in distinct:
                distinct.append(claim.value)
        return distinct

    def value(self, attribute: str, default: Any = None) -> Any:
        """First claimed value; use values() or conflicts() where disagreement matters."""
        claims = self.claims.get(attribute)
        return claims[0].value if claims else default

    def conflicts(self) -> dict[str, list[Claim]]:
        return {attr: list(claims) for attr, claims in self.claims.items() if len(self.values(attr)) > 1}


@dataclass
class Link:
    """A reference stated by one record, resolved (or not) by a named rule within one tenant."""

    kind: str
    tenant_id: str
    source_id: str
    stated_by: SourceRef
    reference: dict[str, Any]
    rule: str
    status: str = "resolved"
    target_id: Optional[str] = None
    detail: str = ""
    scopes_consulted: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def dedupe_key(self) -> tuple:
        return (self.kind, self.source_id, self.stated_by, json.dumps(self.reference, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "status": self.status,
            "rule": self.rule,
            "reference": self.reference,
            "stated_by": self.stated_by.to_dict(),
            "detail": self.detail or None,
            "scopes_consulted": self.scopes_consulted,
            "candidates_not_used": self.candidates,
        }


class Store:
    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._entities: dict[str, Entity] = {}
        self._links: dict[tuple, Link] = {}
        self.cross_tenant_observations: list[dict[str, Any]] = []

    def upsert(self, kind: str, tenant_id: str, key: Iterable[str], ref: SourceRef) -> Entity:
        entity = Entity(kind, tenant_id, tuple(key))
        existing = self._entities.setdefault(entity.id, entity)
        existing.add_ref(ref)
        return existing

    def add_link(self, link: Link) -> Link:
        source = self._entities[link.source_id]
        if source.tenant_id != link.tenant_id:
            raise ValueError(f"link {link.kind} crosses tenants: {source.id} vs {link.tenant_id}")
        if link.target_id is not None and self._entities[link.target_id].tenant_id != link.tenant_id:
            raise ValueError(f"link {link.kind} crosses tenants: {link.target_id} vs {link.tenant_id}")
        return self._links.setdefault(link.dedupe_key, link)

    def get(self, kind: str, tenant_id: str, key: Iterable[str]) -> Optional[Entity]:
        return self._entities.get(Entity(kind, tenant_id, tuple(key)).id)

    def entities(self, tenant_id: Optional[str] = None, kind: Optional[str] = None) -> list[Entity]:
        return sorted(
            (
                e
                for e in self._entities.values()
                if (tenant_id is None or e.tenant_id == tenant_id) and (kind is None or e.kind == kind)
            ),
            key=lambda e: e.id,
        )

    def links(self, tenant_id: Optional[str] = None) -> list[Link]:
        return [l for l in self._links.values() if tenant_id is None or l.tenant_id == tenant_id]

    def view(self, tenant_id: str) -> "TenantView":
        return TenantView(self, tenant_id)


class TenantView:
    """Read access restricted to one tenant."""

    def __init__(self, store: Store, tenant_id: str) -> None:
        if tenant_id not in store.manifest.tenants:
            raise ValueError(f"unknown tenant {tenant_id!r}; manifest declares {store.manifest.tenants}")
        self._store = store
        self.tenant_id = tenant_id

    @property
    def as_of(self):
        return self._store.manifest.as_of

    @property
    def collections(self) -> list[Collection]:
        return self._store.manifest.for_tenant(self.tenant_id)

    def collection(self, snapshot_id: str) -> Collection:
        collection = self._store.manifest.collection(snapshot_id)
        if collection.tenant_id != self.tenant_id:
            raise LookupError(f"collection {snapshot_id} is not visible to tenant {self.tenant_id}")
        return collection

    def entities(self, kind: Optional[str] = None) -> list[Entity]:
        return self._store.entities(self.tenant_id, kind)

    def get(self, kind: str, key: Iterable[str]) -> Optional[Entity]:
        return self._store.get(kind, self.tenant_id, key)

    def entity(self, entity_id: str) -> Entity:
        entity = self._store._entities.get(entity_id)
        if entity is None or entity.tenant_id != self.tenant_id:
            raise LookupError(f"entity {entity_id} is not visible to tenant {self.tenant_id}")
        return entity

    def links(self, kinds: Optional[Iterable[str]] = None) -> list[Link]:
        wanted = None if kinds is None else set(kinds)
        return [l for l in self._store.links(self.tenant_id) if wanted is None or l.kind in wanted]

    def links_from(self, entity_id: str, kinds: Optional[Iterable[str]] = None) -> list[Link]:
        return [l for l in self.links(kinds) if l.source_id == entity_id]

    def links_to(self, entity_id: str, kinds: Optional[Iterable[str]] = None) -> list[Link]:
        return [l for l in self.links(kinds) if l.target_id == entity_id]
