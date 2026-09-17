"""Deterministic full rebuild of the model from the supplied files."""

from __future__ import annotations

from pathlib import Path

from .ingest import ingest
from .manifest import Manifest
from .model import Store
from .resolve import resolve


def build(root: Path) -> Store:
    store = Store(Manifest.load(Path(root)))
    ingest(store)
    resolve(store)
    return store
