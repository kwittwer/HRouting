"""Datei-I/O für .hrp-Projekte."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from model.document import Document

from .asset_data_uri import encode_file_to_data_uri, is_data_uri
from .migration import CURRENT_HRP_FORMAT_VERSION, FORMAT_VERSION_KEY
from .migration import migrate_raw
from .hrp_repair import repair_hrp_data


def load_raw(path: str | Path) -> dict:
    """Liest eine .hrp-Datei und migriert sie auf die aktuelle Struktur."""
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return migrate_raw(raw)


def save_raw(raw: dict, path: str | Path) -> None:
    """Schreibt ein rohes Projekt-Dict im HRouting-Format."""
    raw = dict(raw or {})
    raw[FORMAT_VERSION_KEY] = CURRENT_HRP_FORMAT_VERSION
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)


def load_document(path: str | Path) -> Document:
    doc = Document.from_dict(load_raw(path))
    doc.source_path = Path(path)  # type: ignore[attr-defined]
    return doc


def save_document(doc: Document, path: str | Path) -> None:
    target = Path(path)
    raw = _embed_assets_for_save(doc.to_dict(), target, doc)
    save_raw(raw, target)
    doc.source_path = Path(path)  # type: ignore[attr-defined]


def _embed_assets_for_save(raw: dict, target_path: Path, doc: Document | None = None) -> dict:
    """Ersetzt referenzierte Bildpfade durch Data-URIs für portable .hrp-Dateien."""
    params = raw.get("params")
    if not isinstance(params, dict):
        return raw

    source_base: Path | None = None
    if doc is not None:
        source_path = getattr(doc, "source_path", None)
        if source_path:
            source_base = Path(source_path).parent
    target_base = target_path.parent

    _embed_bucket_path_field(
        params,
        bucket_names=("floorplans", "furniture"),
        field_name="file_path",
        target_base=target_base,
        source_base=source_base,
    )
    _embed_bucket_path_field(
        params,
        bucket_names=("elec_points", "hkv_points"),
        field_name="icon_path",
        target_base=target_base,
        source_base=source_base,
    )
    return raw


def _embed_bucket_path_field(
    params: dict,
    *,
    bucket_names: tuple[str, ...],
    field_name: str,
    target_base: Path,
    source_base: Path | None,
) -> None:
    for bucket_name in bucket_names:
        bucket = params.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for entry in bucket.values():
            if not isinstance(entry, dict):
                continue
            raw_path = str(entry.get(field_name, "") or "").strip()
            if not raw_path or is_data_uri(raw_path):
                continue
            resolved = _resolve_asset_path(raw_path, target_base, source_base)
            if resolved is None:
                continue
            try:
                entry[field_name] = encode_file_to_data_uri(resolved)
            except OSError:
                # Wenn die Datei nicht lesbar ist, Referenz unverändert lassen.
                continue


def _resolve_asset_path(raw_path: str, target_base: Path, source_base: Path | None) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    candidates: list[Path] = []
    if source_base is not None:
        candidates.append(source_base / raw_path)
    candidates.append(target_base / raw_path)
    candidates.append(Path.cwd() / raw_path)
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


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
