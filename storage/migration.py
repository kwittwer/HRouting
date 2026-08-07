"""Migration älterer .hrp-Dateien auf die aktuelle Struktur.

Das Dateiformat selbst bleibt unverändert – hier werden nur Alt-Strukturen
normalisiert, damit der Model-Layer sie einheitlich lesen kann.
"""

from __future__ import annotations

import copy
import math
import re

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
    _migrate_legacy_electrical_ids(canvas, params)
    _migrate_icon_paths(params)
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


_ABSOLUTE_PATH_RE = re.compile(r"^(?:[a-zA-Z]:[\\/]|\\\\|/)")


def _normalize_icon_path(path_value: object) -> str:
    """Normalisiert icon_path zu portablem Posix-Format.

    Absolute Pfade werden auf ``icons/<Dateiname>`` reduziert, damit Projekte
    nicht an temporäre oder benutzerspezifische Pfade gebunden sind.
    """
    raw = str(path_value or "").strip()
    if not raw:
        return ""

    normalized = raw.replace("\\", "/")
    if _ABSOLUTE_PATH_RE.match(raw):
        filename = normalized.rsplit("/", 1)[-1]
        return f"icons/{filename}" if filename else ""
    return re.sub(r"/{2,}", "/", normalized)


def _migrate_icon_paths(params: dict) -> None:
    """Bereinigt icon_path-Felder in params auf portable relative Pfade."""
    for bucket_name in ("elec_points", "hkv_points"):
        bucket = params.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for entry in bucket.values():
            if not isinstance(entry, dict) or "icon_path" not in entry:
                continue
            entry["icon_path"] = _normalize_icon_path(entry.get("icon_path", ""))


def _migrate_legacy_electrical_ids(canvas: dict, params: dict) -> None:
    """Migriert Legacy-IDs und AP-Referenzen im Elektrobereich.

    - Kabel: ``KV-N`` -> ``EK-N``
    - Räume: ``R-N`` -> ``ER-N``
    - Kabel-Endpoints mit AP-Namen werden auf AP-IDs aufgelöst (falls eindeutig).
    """
    cable_map = _migrate_params_bucket_ids(
        params,
        bucket_name="elec_cables",
        old_prefix="KV",
        new_prefix="EK",
        id_field="cable_id",
    )
    room_map = _migrate_params_bucket_ids(
        params,
        bucket_name="elec_rooms",
        old_prefix="R",
        new_prefix="ER",
        id_field="room_id",
    )

    _remap_canvas_keys(
        canvas,
        cable_map,
        map_names=(
            "elec_cables",
            "elec_cable_notes",
            "elec_cable_stroke_width",
            "elec_cable_type_text",
            "elec_cable_type_label_visible",
            "cable_start_ap",
            "cable_end_ap",
            "elec_visible",
            "label_positions",
            "label_font_sizes",
            "label_visible",
        ),
    )
    _remap_canvas_keys(
        canvas,
        room_map,
        map_names=(
            "elec_rooms",
            "elec_room_visible",
            "elec_visible",
            "label_positions",
            "label_font_sizes",
            "label_visible",
        ),
    )

    _migrate_cable_id_references_in_point_configs(params, cable_map)
    _migrate_cable_endpoint_refs(params, canvas)


def _migrate_params_bucket_ids(
    params: dict,
    *,
    bucket_name: str,
    old_prefix: str,
    new_prefix: str,
    id_field: str,
) -> dict[str, str]:
    """Benennt Schlüssel und internes ID-Feld in einem params-Bucket um."""
    bucket = params.get(bucket_name)
    if not isinstance(bucket, dict) or not bucket:
        return {}

    id_map: dict[str, str] = {}
    migrated: dict[str, object] = {}
    used = set(bucket.keys())

    for old_id, entry in bucket.items():
        new_id = _map_prefixed_id(old_id, old_prefix=old_prefix, new_prefix=new_prefix)
        if new_id != old_id and new_id in used:
            # Kollision vermeiden: in diesem seltenen Fall nicht umbenennen.
            new_id = old_id
        id_map[old_id] = new_id
        used.add(new_id)

        if isinstance(entry, dict):
            migrated_entry = dict(entry)
            migrated_entry[id_field] = new_id
            migrated[new_id] = migrated_entry
        else:
            migrated[new_id] = entry

    params[bucket_name] = migrated
    return {k: v for k, v in id_map.items() if k != v}


def _map_prefixed_id(raw_id: object, *, old_prefix: str, new_prefix: str) -> str:
    text = str(raw_id or "")
    match = re.fullmatch(rf"{re.escape(old_prefix)}-(\d+)", text)
    if not match:
        return text
    return f"{new_prefix}-{match.group(1)}"


def _remap_canvas_keys(
    canvas: dict,
    id_map: dict[str, str],
    *,
    map_names: tuple[str, ...],
) -> None:
    """Wendet eine ID-Umbenennung auf id-basierte canvas-Maps an."""
    if not id_map:
        return
    for map_name in map_names:
        values = canvas.get(map_name)
        if not isinstance(values, dict) or not values:
            continue
        remapped: dict[str, object] = {}
        for old_id, payload in values.items():
            remapped[id_map.get(old_id, old_id)] = payload
        canvas[map_name] = remapped


def _migrate_cable_id_references_in_point_configs(
    params: dict,
    cable_id_map: dict[str, str],
) -> None:
    """Aktualisiert Kabel-Referenzen in UV/UP-Konfigurationen von APs."""
    if not cable_id_map:
        return

    points = params.get("elec_points")
    if not isinstance(points, dict):
        return

    for point in points.values():
        if not isinstance(point, dict):
            continue

        uv = point.get("uv_config")
        if isinstance(uv, dict):
            for slot in uv.get("slots", []) or []:
                if not isinstance(slot, dict):
                    continue
                cable = str(slot.get("cable", "") or "")
                if cable:
                    slot["cable"] = cable_id_map.get(cable, cable)

        up = point.get("up_distribution_config")
        if not isinstance(up, dict):
            continue
        incoming = str(up.get("incoming_cable_id", "") or "")
        if incoming:
            up["incoming_cable_id"] = cable_id_map.get(incoming, incoming)

        outgoing = up.get("outgoing_cable_ids")
        if isinstance(outgoing, list):
            up["outgoing_cable_ids"] = [
                cable_id_map.get(str(c), str(c)) for c in outgoing
            ]

        for mapping in up.get("mappings", []) or []:
            if not isinstance(mapping, dict):
                continue
            for key in ("to_cable_id", "cable_id"):
                value = str(mapping.get(key, "") or "")
                if value:
                    mapping[key] = cable_id_map.get(value, value)


def _migrate_cable_endpoint_refs(params: dict, canvas: dict) -> None:
    """Normalisiert start/end-AP-Referenzen auf AP-IDs.

    Legacy-Dateien können AP-Namen statt IDs verwenden.
    """
    points = params.get("elec_points")
    cables = params.get("elec_cables")
    if not isinstance(points, dict) or not isinstance(cables, dict):
        return

    ap_ids = set(points.keys())
    name_to_id: dict[str, str] = {}
    name_to_ids: dict[str, list[str]] = {}
    name_floor_to_ids: dict[tuple[str, str], list[str]] = {}
    duplicate_names: set[str] = set()

    for ap_id, ap_data in points.items():
        if not isinstance(ap_data, dict):
            continue
        name = str(ap_data.get("name", "") or "").strip()
        floor_plan_id = str(ap_data.get("floor_plan_id", "") or "")
        if not name:
            continue
        name_to_ids.setdefault(name, []).append(ap_id)
        name_floor_to_ids.setdefault((name, floor_plan_id), []).append(ap_id)
        if name in name_to_id and name_to_id[name] != ap_id:
            duplicate_names.add(name)
        else:
            name_to_id[name] = ap_id

    for name in duplicate_names:
        name_to_id.pop(name, None)

    point_positions: dict[str, tuple[float, float]] = {}
    elec_points_geo = canvas.get("elec_points")
    if isinstance(elec_points_geo, dict):
        for ap_id, pos in elec_points_geo.items():
            if not isinstance(pos, list) or len(pos) != 2:
                continue
            try:
                point_positions[ap_id] = (float(pos[0]), float(pos[1]))
            except (TypeError, ValueError):
                continue

    cable_paths = canvas.get("elec_cables") if isinstance(canvas.get("elec_cables"), dict) else {}

    def _path_anchor(cable_id: str, side: str) -> tuple[float, float] | None:
        poly = cable_paths.get(cable_id) if isinstance(cable_paths, dict) else None
        if not isinstance(poly, list) or not poly:
            return None
        anchor = poly[0] if side == "start" else poly[-1]
        if not isinstance(anchor, list) or len(anchor) != 2:
            return None
        try:
            return (float(anchor[0]), float(anchor[1]))
        except (TypeError, ValueError):
            return None

    def _select_by_proximity(
        candidates: list[str],
        cable_id: str,
        side: str,
    ) -> str | None:
        if len(candidates) == 1:
            return candidates[0]
        anchor = _path_anchor(cable_id, side)
        if anchor is None:
            return None
        best_id: str | None = None
        best_dist = math.inf
        ax, ay = anchor
        for ap_id in candidates:
            pos = point_positions.get(ap_id)
            if pos is None:
                continue
            px, py = pos
            dist = (px - ax) ** 2 + (py - ay) ** 2
            if dist < best_dist:
                best_dist = dist
                best_id = ap_id
        return best_id

    def _resolve(
        value: object,
        floor_plan_id: str = "",
        cable_id: str = "",
        side: str = "start",
    ) -> str:
        token = str(value or "").strip()
        if not token:
            return ""
        if token in ap_ids:
            return token
        local_candidates = name_floor_to_ids.get((token, floor_plan_id), [])
        if len(local_candidates) == 1:
            return local_candidates[0]
        picked_local = _select_by_proximity(local_candidates, cable_id, side)
        if picked_local:
            return picked_local
        all_candidates = name_to_ids.get(token, [])
        picked_global = _select_by_proximity(all_candidates, cable_id, side)
        if picked_global:
            return picked_global
        return name_to_id.get(token, token)

    for cable_id, cable_data in cables.items():
        if not isinstance(cable_data, dict):
            continue
        cable_floor = str(cable_data.get("floor_plan_id", "") or "")
        cable_data["start_ap"] = _resolve(
            cable_data.get("start_ap", ""),
            cable_floor,
            cable_id,
            "start",
        )
        cable_data["end_ap"] = _resolve(
            cable_data.get("end_ap", ""),
            cable_floor,
            cable_id,
            "end",
        )

    for map_name in ("cable_start_ap", "cable_end_ap"):
        values = canvas.get(map_name)
        if not isinstance(values, dict):
            continue
        for cable_id in list(values.keys()):
            cable_floor = ""
            cable = cables.get(cable_id)
            if isinstance(cable, dict):
                cable_floor = str(cable.get("floor_plan_id", "") or "")
            side = "start" if map_name == "cable_start_ap" else "end"
            values[cable_id] = _resolve(
                values.get(cable_id, ""),
                cable_floor,
                cable_id,
                side,
            )
