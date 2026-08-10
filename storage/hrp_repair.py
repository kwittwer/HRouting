"""Reparatur- und Bereinigungslogik für HRP-Projekte."""

from __future__ import annotations

import copy
from typing import Any

from .migration import migrate_raw


def repair_hrp_data(raw: dict[str, Any], *, aggressive: bool = True) -> tuple[dict[str, Any], list[str]]:
    """Repariert ein rohes HRP-Dict und liefert (repariert, aenderungen).

    Die Reparatur ist standardmäßig aggressiv und entfernt inkonsistente
    oder verwaiste Einträge konsequent.
    """
    data = migrate_raw(copy.deepcopy(raw or {}))
    changes: list[str] = []

    canvas = data.setdefault("canvas", {})
    params = data.setdefault("params", {})

    for key in (
        "circuits",
        "elec_points",
        "elec_rooms",
        "elec_cables",
        "hkv_points",
        "hkv_lines",
        "floorplans",
        "furniture",
        "text_annotations",
    ):
        if not isinstance(params.get(key), dict):
            params[key] = {}
            changes.append(f"params.{key}: auf leeres Objekt normalisiert")

    fp_ids = set(params["floorplans"].keys())
    fur_ids = set(params["furniture"].keys())
    all_layer_ids = fp_ids | fur_ids

    if aggressive:
        _repair_floor_references(params, all_layer_ids, changes)

    circuit_ids = set(params["circuits"].keys())
    ap_ids = set(params["elec_points"].keys())
    er_ids = set(params["elec_rooms"].keys())
    ek_ids = set(params["elec_cables"].keys())
    hkv_ids = set(params["hkv_points"].keys())
    hkvl_ids = set(params["hkv_lines"].keys())
    text_ids = set(params["text_annotations"].keys())

    _repair_cable_refs(params["elec_cables"], ap_ids, changes)
    _repair_hkv_line_refs(params["hkv_lines"], hkv_ids, changes)
    _normalize_floorplans_order(params, all_layer_ids, changes)
    _normalize_canvas_floor_plan_list(canvas, all_layer_ids, changes)

    _drop_unknown_keys(canvas, "polygons", circuit_ids, changes)
    _drop_unknown_keys(canvas, "start_points", circuit_ids, changes)
    _drop_unknown_keys(canvas, "manual_routes", circuit_ids, changes)
    _drop_unknown_keys(canvas, "route_wall_dist_px", circuit_ids, changes)
    _drop_unknown_keys(canvas, "route_line_dist_px", circuit_ids, changes)
    _drop_unknown_keys(canvas, "supply_lines", circuit_ids, changes)

    _drop_unknown_keys(canvas, "elec_points", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_size_px", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_position", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_height", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_notes", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_smarthome_device", ap_ids, changes)
    _drop_unknown_keys(canvas, "elec_point_smarthome_device_color", ap_ids, changes)

    _drop_unknown_keys(canvas, "elec_rooms", er_ids, changes)
    _drop_unknown_keys(canvas, "elec_room_visible", er_ids, changes)

    _drop_unknown_keys(canvas, "elec_cables", ek_ids, changes)
    _drop_unknown_keys(canvas, "elec_cable_notes", ek_ids, changes)

    _drop_unknown_keys(canvas, "hkv_points", hkv_ids, changes)
    _drop_unknown_keys(canvas, "hkv_size_px", hkv_ids, changes)
    _drop_unknown_keys(canvas, "hkv_visible", hkv_ids, changes)

    _drop_unknown_keys(canvas, "hkv_lines", hkvl_ids, changes)
    _drop_unknown_keys(canvas, "hkv_line_start", hkvl_ids, changes)
    _drop_unknown_keys(canvas, "hkv_line_end", hkvl_ids, changes)
    _drop_unknown_keys(canvas, "hkv_line_visible", hkvl_ids, changes)

    _drop_unknown_keys(canvas, "text_annotations", text_ids, changes)

    _repair_supply_hkv(canvas, circuit_ids, hkv_ids, changes)
    _repair_canvas_cable_endpoints(canvas, ek_ids, ap_ids, changes)
    _repair_canvas_hkv_endpoints(canvas, hkvl_ids, hkv_ids, changes)

    _repair_polygons(canvas, "polygons", 3, changes)
    _repair_polygons(canvas, "elec_rooms", 3, changes)

    _repair_nested_floor_helper_maps(canvas, all_layer_ids, changes)

    return data, changes


def _repair_floor_references(params: dict[str, Any], all_layer_ids: set[str], changes: list[str]) -> None:
    sections = (
        "circuits",
        "elec_points",
        "elec_rooms",
        "elec_cables",
        "hkv_points",
        "hkv_lines",
        "text_annotations",
    )
    for section in sections:
        bucket = params.get(section)
        if not isinstance(bucket, dict):
            continue
        drop_ids: list[str] = []
        for eid, entry in bucket.items():
            if not isinstance(entry, dict):
                drop_ids.append(str(eid))
                continue
            fp_id = str(entry.get("floor_plan_id", "") or "")
            if fp_id and fp_id not in all_layer_ids:
                drop_ids.append(str(eid))
        for eid in drop_ids:
            bucket.pop(eid, None)
            changes.append(f"params.{section}.{eid}: entfernt (ungueltige floor_plan_id)")

    furn = params.get("furniture")
    if isinstance(furn, dict):
        drop_ids = []
        for eid, entry in furn.items():
            if not isinstance(entry, dict):
                drop_ids.append(str(eid))
                continue
            parent = str(entry.get("parent_fp_id", "") or "")
            if parent and parent not in all_layer_ids:
                drop_ids.append(str(eid))
        for eid in drop_ids:
            furn.pop(eid, None)
            changes.append(f"params.furniture.{eid}: entfernt (ungueltige parent_fp_id)")


def _repair_cable_refs(cables: dict[str, Any], ap_ids: set[str], changes: list[str]) -> None:
    for cid, entry in cables.items():
        if not isinstance(entry, dict):
            continue
        for key in ("start_ap", "end_ap"):
            ref = str(entry.get(key, "") or "")
            if ref and ref not in ap_ids:
                entry[key] = ""
                changes.append(f"params.elec_cables.{cid}.{key}: auf leer gesetzt (ungueltiger AP)")


def _repair_hkv_line_refs(lines: dict[str, Any], hkv_ids: set[str], changes: list[str]) -> None:
    for lid, entry in lines.items():
        if not isinstance(entry, dict):
            continue
        for key in ("start_hkv", "end_hkv"):
            ref = str(entry.get(key, "") or "")
            if ref and ref not in hkv_ids:
                entry[key] = ""
                changes.append(f"params.hkv_lines.{lid}.{key}: auf leer gesetzt (ungueltiger HKV)")


def _normalize_floorplans_order(params: dict[str, Any], all_layer_ids: set[str], changes: list[str]) -> None:
    raw_order = params.get("floorplans_order", [])
    order: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_order, list):
        for item in raw_order:
            fid = str(item)
            if fid in all_layer_ids and fid not in seen:
                order.append(fid)
                seen.add(fid)
    for fid in sorted(all_layer_ids):
        if fid not in seen:
            order.append(fid)
            seen.add(fid)
    if order != raw_order:
        params["floorplans_order"] = order
        changes.append("params.floorplans_order: normalisiert")


def _normalize_canvas_floor_plan_list(canvas: dict[str, Any], all_layer_ids: set[str], changes: list[str]) -> None:
    floor_plans = canvas.get("floor_plans")
    if not isinstance(floor_plans, list):
        return
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in floor_plans:
        if not isinstance(entry, dict):
            continue
        fid = str(entry.get("fp_id", "") or "")
        if not fid or fid in seen or fid not in all_layer_ids:
            continue
        cleaned.append(entry)
        seen.add(fid)
    if cleaned != floor_plans:
        canvas["floor_plans"] = cleaned
        changes.append("canvas.floor_plans: normalisiert (ungueltige/duplizierte Layer entfernt)")


def _drop_unknown_keys(
    canvas: dict[str, Any],
    map_name: str,
    valid_ids: set[str],
    changes: list[str],
) -> None:
    bucket = canvas.get(map_name)
    if not isinstance(bucket, dict):
        return
    drop = [key for key in list(bucket.keys()) if str(key) not in valid_ids]
    for key in drop:
        bucket.pop(key, None)
        changes.append(f"canvas.{map_name}.{key}: entfernt (verwaist)")


def _repair_supply_hkv(canvas: dict[str, Any], circuit_ids: set[str], hkv_ids: set[str], changes: list[str]) -> None:
    mapping = canvas.get("supply_hkv")
    if not isinstance(mapping, dict):
        return
    for cid in list(mapping.keys()):
        if str(cid) not in circuit_ids:
            mapping.pop(cid, None)
            changes.append(f"canvas.supply_hkv.{cid}: entfernt (verwaister Heizkreis)")
            continue
        hkv_id = str(mapping.get(cid, "") or "")
        if hkv_id and hkv_id not in hkv_ids:
            mapping[cid] = ""
            changes.append(f"canvas.supply_hkv.{cid}: auf leer gesetzt (ungueltiger HKV)")


def _repair_canvas_cable_endpoints(
    canvas: dict[str, Any],
    cable_ids: set[str],
    ap_ids: set[str],
    changes: list[str],
) -> None:
    for map_name in ("cable_start_ap", "cable_end_ap"):
        mapping = canvas.get(map_name)
        if not isinstance(mapping, dict):
            continue
        for cid in list(mapping.keys()):
            if str(cid) not in cable_ids:
                mapping.pop(cid, None)
                changes.append(f"canvas.{map_name}.{cid}: entfernt (verwaistes Kabel)")
                continue
            ref = str(mapping.get(cid, "") or "")
            if ref and ref not in ap_ids:
                mapping[cid] = ""
                changes.append(f"canvas.{map_name}.{cid}: auf leer gesetzt (ungueltiger AP)")


def _repair_canvas_hkv_endpoints(
    canvas: dict[str, Any],
    line_ids: set[str],
    hkv_ids: set[str],
    changes: list[str],
) -> None:
    for map_name in ("hkv_line_start", "hkv_line_end"):
        mapping = canvas.get(map_name)
        if not isinstance(mapping, dict):
            continue
        for lid in list(mapping.keys()):
            if str(lid) not in line_ids:
                mapping.pop(lid, None)
                changes.append(f"canvas.{map_name}.{lid}: entfernt (verwaiste HKV-Leitung)")
                continue
            ref = str(mapping.get(lid, "") or "")
            if ref and ref not in hkv_ids:
                mapping[lid] = ""
                changes.append(f"canvas.{map_name}.{lid}: auf leer gesetzt (ungueltiger HKV)")


def _repair_polygons(canvas: dict[str, Any], map_name: str, min_points: int, changes: list[str]) -> None:
    polys = canvas.get(map_name)
    if not isinstance(polys, dict):
        return
    for eid in list(polys.keys()):
        pts = polys.get(eid)
        if not isinstance(pts, list) or len(pts) < min_points:
            polys.pop(eid, None)
            changes.append(f"canvas.{map_name}.{eid}: entfernt (zu wenige Punkte)")


def _repair_nested_floor_helper_maps(canvas: dict[str, Any], all_layer_ids: set[str], changes: list[str]) -> None:
    nested_names = (
        "floor_helper_lines",
        "floor_helper_line_visible",
        "floor_helper_line_length_mm",
        "floor_helper_line_fixed",
        "helper_label_positions",
        "floor_helper_settings",
    )

    for name in nested_names:
        root = canvas.get(name)
        if not isinstance(root, dict):
            continue
        for fid in list(root.keys()):
            if str(fid) not in all_layer_ids:
                root.pop(fid, None)
                changes.append(f"canvas.{name}.{fid}: entfernt (ungueltiger Grundriss)")
                continue
            if name == "floor_helper_settings":
                if not isinstance(root.get(fid), dict):
                    root[fid] = {}
                    changes.append(f"canvas.floor_helper_settings.{fid}: auf Objekt normalisiert")
                continue
            value = root.get(fid)
            if not isinstance(value, dict):
                root[fid] = {}
                changes.append(f"canvas.{name}.{fid}: auf Objekt normalisiert")

    lines = canvas.get("floor_helper_lines")
    if not isinstance(lines, dict):
        return
    for fid, helper_map in lines.items():
        if not isinstance(helper_map, dict):
            continue
        for hid in list(helper_map.keys()):
            pts = helper_map.get(hid)
            if not isinstance(pts, list) or len(pts) < 2:
                helper_map.pop(hid, None)
                changes.append(f"canvas.floor_helper_lines.{fid}.{hid}: entfernt (ungueltige Linie)")
