"""Datei-I/O für .hrp-Projekte."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from model.document import Document

from .migration import migrate_raw
from .hrp_repair import repair_hrp_data


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


def create_hrp_backup(path: str | Path) -> Path:
    """Erstellt ein .bak-Backup neben der Quelldatei."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Quelldatei nicht gefunden: {source}")
    backup_path = source.with_suffix(source.suffix + ".bak")
    shutil.copy2(source, backup_path)
    return backup_path


def repair_and_save_hrp(
    path: str | Path,
    *,
    output_path: str | Path | None = None,
    backup: bool = True,
    aggressive: bool = True,
) -> tuple[dict, list[str], Path | None, Path]:
    """Repariert eine HRP-Datei und schreibt das Ergebnis.

    Returns:
        (repaired_data, change_log, backup_path_or_none, written_path)
    """
    source = Path(path)
    target = Path(output_path) if output_path is not None else source

    with open(source, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    backup_path: Path | None = None
    if backup:
        backup_path = create_hrp_backup(source)

    repaired, change_log = repair_hrp_data(raw, aggressive=aggressive)
    save_raw(repaired, target)
    return repaired, change_log, backup_path, target
