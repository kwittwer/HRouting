"""Datei-I/O für .hrp-Projekte."""

from __future__ import annotations

import json
from pathlib import Path

from model.document import Document

from .migration import migrate_raw


def load_raw(path: str | Path) -> dict:
    """Liest eine .hrp-Datei und migriert sie auf die aktuelle Struktur."""
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return migrate_raw(raw)


def save_raw(raw: dict, path: str | Path) -> None:
    """Schreibt ein rohes Projekt-Dict im HRouting-Format."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)


def load_document(path: str | Path) -> Document:
    doc = Document.from_dict(load_raw(path))
    doc.source_path = Path(path)  # type: ignore[attr-defined]
    return doc


def save_document(doc: Document, path: str | Path) -> None:
    save_raw(doc.to_dict(), path)
    doc.source_path = Path(path)  # type: ignore[attr-defined]
