"""Migration älterer .hrp-Dateien auf die aktuelle Struktur.

Das Dateiformat selbst bleibt unverändert – hier werden nur Alt-Strukturen
normalisiert, damit der Model-Layer sie einheitlich lesen kann.
"""

from __future__ import annotations

import copy

#: Schlüssel, unter dem die alte UI ihren Fensterzustand in params ablegte.
LEGACY_UI_STATE_KEY = "_ui_state"


def migrate_raw(raw: dict) -> dict:
    """Normalisiert ein rohes .hrp-Dict. Gibt eine neue Struktur zurück."""
    raw = copy.deepcopy(raw or {})
    raw.setdefault("svg_path", "")
    raw.setdefault("canvas", {})
    raw.setdefault("params", {})
    raw.setdefault("pdf_export_pages", [])

    canvas = raw["canvas"]
    params = raw["params"]

    _migrate_global_helper_lines(canvas)
    _migrate_elec_room_polygons(canvas)
    _migrate_floorplan_order(canvas, params)
    _drop_legacy_ui_state(params)
    return raw


def _migrate_global_helper_lines(canvas: dict) -> None:
    """Alte globale Hilfslinien auf den ersten Grundriss übertragen."""
    legacy = canvas.pop("global_helper_lines", None)
    if not legacy:
        return
    plans = canvas.get("floor_plans") or []
    if not plans:
        return
    first = plans[0].get("fp_id", "")
    if not first:
        return
    canvas.setdefault("floor_helper_lines", {}).setdefault(first, {}).update(legacy)


def _migrate_elec_room_polygons(canvas: dict) -> None:
    """``elec_room_polygons`` (neu) und ``elec_rooms`` (alt) vereinheitlichen."""
    new = canvas.pop("elec_room_polygons", None)
    if new:
        canvas.setdefault("elec_rooms", {}).update(new)


def _migrate_floorplan_order(canvas: dict, params: dict) -> None:
    """Reihenfolge existiert historisch in canvas und params – zusammenführen."""
    order = params.get("floorplans_order") or canvas.get("floor_plan_order") or []
    known = set((params.get("floorplans") or {}).keys())
    merged = [fid for fid in order if fid in known]
    merged += [fid for fid in known if fid not in merged]
    if merged:
        params["floorplans_order"] = merged
    canvas.pop("floor_plan_order", None)


def _drop_legacy_ui_state(params: dict) -> None:
    """Fensterzustand wandert in QSettings – aus dem Projekt entfernen."""
    params.pop(LEGACY_UI_STATE_KEY, None)
