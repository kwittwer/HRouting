#!/usr/bin/env python3
"""
HRP-Datei-Validator für HRouting-Projektdateien.

Prüft eine .hrp-Datei gegen das JSON-Schema (hrp_schema.json) und
führt zusätzliche semantische Validierungen durch.

Verwendung:
    python validate_hrp.py projekt.hrp              # Vollständige Prüfung
    python validate_hrp.py --schema-only projekt.hrp # Nur Schema
    python validate_hrp.py --json projekt.hrp        # JSON-Ausgabe
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from storage.hrp_io import repair_and_save_hrp


def _load_schema(schema_path: Path) -> dict:
    """Lädt das JSON-Schema."""
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_hrp(hrp_path: Path) -> dict:
    """Lädt und parst eine HRP-Datei."""
    with open(hrp_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
# Schema-Validierung (mit jsonschema wenn verfügbar, sonst Basis)
# ─────────────────────────────────────────────────────────────

def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validiert data gegen das JSON-Schema. Gibt Fehlerliste zurück."""
    try:
        import jsonschema
        validator_cls = jsonschema.Draft202012Validator
        validator = validator_cls(schema)
        errors = []
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"[Schema] {path}: {err.message}")
        return errors
    except ImportError:
        # Fallback: Basisprüfungen ohne jsonschema
        return _validate_schema_basic(data)


def _validate_schema_basic(data: dict) -> list[str]:
    """Minimale Schema-Prüfung ohne jsonschema-Bibliothek."""
    errors = []
    if not isinstance(data, dict):
        errors.append("[Schema] Root ist kein JSON-Objekt.")
        return errors

    if "canvas" not in data:
        errors.append("[Schema] Pflichtfeld 'canvas' fehlt.")
    elif not isinstance(data["canvas"], dict):
        errors.append("[Schema] 'canvas' muss ein Objekt sein.")

    if "params" not in data:
        errors.append("[Schema] Pflichtfeld 'params' fehlt.")
    elif not isinstance(data["params"], dict):
        errors.append("[Schema] 'params' muss ein Objekt sein.")

    if "pdf_export_pages" in data and not isinstance(data["pdf_export_pages"], list):
        errors.append("[Schema] 'pdf_export_pages' muss ein Array sein.")

    # Canvas Typen
    canvas = data.get("canvas", {})
    if isinstance(canvas, dict):
        vs = canvas.get("view_scale")
        if vs is not None and (not isinstance(vs, (int, float)) or vs < 0.1 or vs > 50.0):
            errors.append("[Schema] canvas.view_scale muss zwischen 0.1 und 50.0 liegen.")

        for field in ["polygons", "start_points", "manual_routes", "elec_points",
                       "elec_rooms", "elec_cables", "hkv_points", "hkv_lines"]:
            v = canvas.get(field)
            if v is not None and not isinstance(v, dict):
                errors.append(f"[Schema] canvas.{field} muss ein Objekt sein.")

    # Params Typen
    params = data.get("params", {})
    if isinstance(params, dict):
        for field in ["circuits", "elec_points", "elec_rooms", "elec_cables",
                       "hkv_points", "hkv_lines", "floorplans", "text_annotations"]:
            v = params.get(field)
            if v is not None and not isinstance(v, dict):
                errors.append(f"[Schema] params.{field} muss ein Objekt sein.")
        elec_points = params.get("elec_points", {})
        if isinstance(elec_points, dict):
            for pid, pdata in elec_points.items():
                uv_config = pdata.get("uv_config")
                if uv_config is not None and not isinstance(uv_config, dict):
                    errors.append(f"[Schema] params.elec_points.{pid}.uv_config muss ein Objekt sein.")
                up_config = pdata.get("up_distribution_config")
                if up_config is not None and not isinstance(up_config, dict):
                    errors.append(
                        f"[Schema] params.elec_points.{pid}.up_distribution_config muss ein Objekt sein."
                    )

    return errors


# ─────────────────────────────────────────────────────────────
# Semantische Validierung
# ─────────────────────────────────────────────────────────────

_ID_PATTERNS = {
    "HK": re.compile(r"^HK-\d+$"),
    "AP": re.compile(r"^AP-\d+$"),
    "ER": re.compile(r"^ER-\d+$"),
    "EK": re.compile(r"^EK-\d+$"),
    "HKV": re.compile(r"^HKV-\d+$"),
    "HKVL": re.compile(r"^HKVL-\d+$"),
    "TEXT": re.compile(r"^TEXT-\d+$"),
    "grundriss": re.compile(r"^grundriss-\d+$"),
    "einrichtung": re.compile(r"^einrichtung-\d+$"),
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

VALID_FLOOR_COVERINGS = {
    "Estrich (kein Belag)",
    "Fliesen / Keramik",
    "Naturstein",
    "PVC / Vinyl",
    "Laminat",
    "Parkett dünn (≤ 10 mm)",
    "Parkett dick (> 10 mm)",
    "Teppich dünn",
    "Teppich dick",
}
VALID_AP_TYPES = {"standard", "uv", "up_distribution"}


def validate_semantic(data: dict) -> tuple[list[str], list[str]]:
    """
    Semantische Prüfung über das Schema hinaus.
    Gibt (errors, warnings) zurück.
    """
    errors: list[str] = []
    warnings: list[str] = []
    canvas = data.get("canvas", {})
    params = data.get("params", {})

    # Verfügbare IDs sammeln
    fp_ids = set(params.get("floorplans", {}).keys())
    circuit_ids = set(params.get("circuits", {}).keys())
    ap_ids = set(params.get("elec_points", {}).keys())
    er_ids = set(params.get("elec_rooms", {}).keys())
    ek_ids = set(params.get("elec_cables", {}).keys())
    hkv_ids = set(params.get("hkv_points", {}).keys())
    hkvl_ids = set(params.get("hkv_lines", {}).keys())
    text_ids = set(params.get("text_annotations", {}).keys())

    # Furniture-IDs (leben auch als floor_plan layer)
    fur_ids = set(params.get("furniture", {}).keys())
    all_layer_ids = fp_ids | fur_ids

    # ── ID-Format prüfen ──
    for cid in circuit_ids:
        if not _ID_PATTERNS["HK"].match(cid):
            errors.append(f"[ID] Heizkreis-ID '{cid}' entspricht nicht dem Muster HK-N.")
    for pid in ap_ids:
        if not _ID_PATTERNS["AP"].match(pid):
            errors.append(f"[ID] Elektropunkt-ID '{pid}' entspricht nicht dem Muster AP-N.")
    for rid in er_ids:
        if not _ID_PATTERNS["ER"].match(rid):
            errors.append(f"[ID] Elektroraum-ID '{rid}' entspricht nicht dem Muster ER-N.")
    for eid in ek_ids:
        if not _ID_PATTERNS["EK"].match(eid):
            errors.append(f"[ID] Kabel-ID '{eid}' entspricht nicht dem Muster EK-N.")
    for hid in hkv_ids:
        if not _ID_PATTERNS["HKV"].match(hid):
            errors.append(f"[ID] HKV-ID '{hid}' entspricht nicht dem Muster HKV-N.")
    for lid in hkvl_ids:
        if not _ID_PATTERNS["HKVL"].match(lid):
            errors.append(f"[ID] HKV-Leitungs-ID '{lid}' entspricht nicht dem Muster HKVL-N.")

    # ── floor_plan_id Referenzen prüfen ──
    def _check_fp_ref(section_name: str, items: dict, key: str = "floor_plan_id"):
        for eid, edata in items.items():
            fp_id = edata.get(key, "")
            if fp_id and fp_id not in all_layer_ids:
                errors.append(
                    f"[Ref] {section_name}.{eid}.{key} = '{fp_id}' "
                    f"verweist auf nicht existierenden Grundriss."
                )

    _check_fp_ref("circuits", params.get("circuits", {}))
    _check_fp_ref("elec_points", params.get("elec_points", {}))
    _check_fp_ref("elec_rooms", params.get("elec_rooms", {}))
    _check_fp_ref("elec_cables", params.get("elec_cables", {}))
    _check_fp_ref("hkv_points", params.get("hkv_points", {}))
    _check_fp_ref("hkv_lines", params.get("hkv_lines", {}))
    _check_fp_ref("text_annotations", params.get("text_annotations", {}))
    _check_fp_ref("furniture", params.get("furniture", {}), "parent_fp_id")

    # ── Kabel Start/End AP prüfen ──
    for cid, cdata in params.get("elec_cables", {}).items():
        for field in ("start_ap", "end_ap"):
            ref = cdata.get(field, "")
            if ref and ref not in ap_ids:
                errors.append(
                    f"[Ref] elec_cables.{cid}.{field} = '{ref}' "
                    f"verweist auf nicht existierenden Anschlusspunkt."
                )
    # Canvas-Kabel-Referenzen
    for cid, ref in canvas.get("cable_start_ap", {}).items():
        if ref and ref not in ap_ids:
            errors.append(
                f"[Ref] canvas.cable_start_ap.{cid} = '{ref}' "
                f"verweist auf nicht existierenden AP."
            )
    for cid, ref in canvas.get("cable_end_ap", {}).items():
        if ref and ref not in ap_ids:
            errors.append(
                f"[Ref] canvas.cable_end_ap.{cid} = '{ref}' "
                f"verweist auf nicht existierenden AP."
            )

    # ── HKV-Leitungs-Referenzen ──
    for lid, ldata in params.get("hkv_lines", {}).items():
        for field in ("start_hkv", "end_hkv"):
            ref = ldata.get(field, "")
            if ref and ref not in hkv_ids:
                errors.append(
                    f"[Ref] hkv_lines.{lid}.{field} = '{ref}' "
                    f"verweist auf nicht existierenden HKV."
                )
    for lid, ref in canvas.get("hkv_line_start", {}).items():
        if ref and ref not in hkv_ids:
            errors.append(
                f"[Ref] canvas.hkv_line_start.{lid} = '{ref}' "
                f"verweist auf nicht existierenden HKV."
            )
    for lid, ref in canvas.get("hkv_line_end", {}).items():
        if ref and ref not in hkv_ids:
            errors.append(
                f"[Ref] canvas.hkv_line_end.{lid} = '{ref}' "
                f"verweist auf nicht existierenden HKV."
            )

    # ── supply_hkv prüfen ──
    for cid, hkv_id in canvas.get("supply_hkv", {}).items():
        if hkv_id and hkv_id not in hkv_ids:
            errors.append(
                f"[Ref] canvas.supply_hkv.{cid} = '{hkv_id}' "
                f"verweist auf nicht existierenden HKV."
            )
        if cid not in circuit_ids:
            warnings.append(
                f"[Ref] canvas.supply_hkv.{cid}: Heizkreis '{cid}' "
                f"existiert nicht in params.circuits."
            )

    # ── Polygon-Konsistenz ──
    for cid, poly in canvas.get("polygons", {}).items():
        if isinstance(poly, list) and len(poly) < 3:
            errors.append(
                f"[Geo] canvas.polygons.{cid}: Polygon hat {len(poly)} Punkte "
                f"(mindestens 3 erforderlich)."
            )
    for rid, poly in canvas.get("elec_rooms", {}).items():
        if isinstance(poly, list) and len(poly) < 3:
            errors.append(
                f"[Geo] canvas.elec_rooms.{rid}: Polygon hat {len(poly)} Punkte "
                f"(mindestens 3 erforderlich)."
            )

    # ── Canvas ↔ Params Konsistenz ──
    canvas_circuit_ids = set(canvas.get("polygons", {}).keys())
    for cid in canvas_circuit_ids - circuit_ids:
        warnings.append(
            f"[Sync] Polygon für '{cid}' in canvas vorhanden, "
            f"aber kein Eintrag in params.circuits."
        )
    for cid in circuit_ids - canvas_circuit_ids:
        # Kein Polygon ist okay (kann noch gezeichnet werden)
        pass

    canvas_ap_ids = set(canvas.get("elec_points", {}).keys())
    for pid in canvas_ap_ids - ap_ids:
        warnings.append(
            f"[Sync] Elektropunkt '{pid}' in canvas vorhanden, "
            f"aber kein Eintrag in params.elec_points."
        )

    canvas_hkv_ids = set(canvas.get("hkv_points", {}).keys())
    for hid in canvas_hkv_ids - hkv_ids:
        warnings.append(
            f"[Sync] HKV '{hid}' in canvas vorhanden, "
            f"aber kein Eintrag in params.hkv_points."
        )

    # ── floorplans_order ──
    fp_order = params.get("floorplans_order", [])
    for fid in fp_ids:
        if fid not in fp_order:
            warnings.append(
                f"[Sync] Grundriss '{fid}' in params.floorplans vorhanden, "
                f"aber nicht in floorplans_order."
            )

    # ── floor_covering prüfen ──
    for cid, cdata in params.get("circuits", {}).items():
        fc = cdata.get("floor_covering", "")
        if fc and fc not in VALID_FLOOR_COVERINGS:
            errors.append(
                f"[Value] circuits.{cid}.floor_covering = '{fc}' "
                f"ist kein gültiger Bodenbelag."
            )

    for pid, pdata in params.get("elec_points", {}).items():
        ap_type = str(pdata.get("ap_type", "standard")).strip().lower()
        if ap_type not in VALID_AP_TYPES:
            errors.append(
                f"[Value] elec_points.{pid}.ap_type = '{pdata.get('ap_type', '')}' "
                f"ist kein gültiger AP-Typ."
            )

        uv_config = pdata.get("uv_config")
        if uv_config is None:
            uv_config = {}
        if not isinstance(uv_config, dict):
            errors.append(f"[Value] elec_points.{pid}.uv_config muss ein Objekt sein.")
            uv_config = {}

        up_config = pdata.get("up_distribution_config")
        if up_config is None:
            up_config = {}
        if not isinstance(up_config, dict):
            errors.append(
                f"[Value] elec_points.{pid}.up_distribution_config muss ein Objekt sein."
            )
            up_config = {}

        # UV-Konfiguration prüfen
        try:
            rows = int(uv_config.get("rows", 0) or 0)
            modules_per_row = int(uv_config.get("modules_per_row", 0) or 0)
        except (TypeError, ValueError):
            errors.append(
                f"[Value] elec_points.{pid}.uv_config.rows/modules_per_row müssen Ganzzahlen sein."
            )
            rows = 0
            modules_per_row = 0

        slots = uv_config.get("slots", [])
        if slots is None:
            slots = []
        if not isinstance(slots, list):
            errors.append(f"[Value] elec_points.{pid}.uv_config.slots muss ein Array sein.")
            slots = []

        has_uv_data = rows > 0 or modules_per_row > 0 or len(slots) > 0
        if has_uv_data and ap_type != "uv":
            warnings.append(
                f"[Value] elec_points.{pid}.uv_config ist gesetzt, aber ap_type ist nicht 'uv'."
            )

        if rows < 0 or modules_per_row < 0:
            errors.append(
                f"[Value] elec_points.{pid}.uv_config.rows/modules_per_row dürfen nicht negativ sein."
            )
        elif bool(rows) != bool(modules_per_row):
            errors.append(
                f"[Value] elec_points.{pid}.uv_config benötigt rows und modules_per_row gemeinsam."
            )

        seen_slots: set[tuple[int, int]] = set()
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config.slots[{index}] muss ein Objekt sein."
                )
                continue
            try:
                row_no = int(slot.get("row", 0) or 0)
                slot_no = int(slot.get("slot", 0) or 0)
            except (TypeError, ValueError):
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config.slots[{index}] benötigt gültige row/slot-Werte."
                )
                continue
            if row_no < 1 or slot_no < 1:
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config.slots[{index}] muss row/slot >= 1 haben."
                )
                continue
            if rows and row_no > rows:
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config.slots[{index}].row = {row_no} "
                    f"liegt außerhalb der UV-Reihen ({rows})."
                )
            if modules_per_row and slot_no > modules_per_row:
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config.slots[{index}].slot = {slot_no} "
                    f"liegt außerhalb der TE-Anzahl ({modules_per_row})."
                )
            key = (row_no, slot_no)
            if key in seen_slots:
                errors.append(
                    f"[Value] elec_points.{pid}.uv_config enthält eine doppelte Belegung für Reihe {row_no}, TE {slot_no}."
                )
            else:
                seen_slots.add(key)

        # Unterputzdosen-Verteilung prüfen
        incoming_cable_id = str(up_config.get("incoming_cable_id", "") or "").strip()
        outgoing_raw = up_config.get("outgoing_cable_ids", [])
        mappings_raw = up_config.get("mappings", [])
        note = str(up_config.get("note", "") or "").strip()

        if outgoing_raw is None:
            outgoing_raw = []
        if not isinstance(outgoing_raw, list):
            errors.append(
                f"[Value] elec_points.{pid}.up_distribution_config.outgoing_cable_ids muss ein Array sein."
            )
            outgoing_raw = []
        outgoing: list[str] = []
        for index, cable_id in enumerate(outgoing_raw):
            text = str(cable_id or "").strip()
            if not text:
                warnings.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.outgoing_cable_ids[{index}] ist leer."
                )
                continue
            if text in outgoing:
                warnings.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.outgoing_cable_ids enthält '{text}' mehrfach."
                )
                continue
            outgoing.append(text)

        if mappings_raw is None:
            mappings_raw = []
        if not isinstance(mappings_raw, list):
            errors.append(
                f"[Value] elec_points.{pid}.up_distribution_config.mappings muss ein Array sein."
            )
            mappings_raw = []

        has_up_data = bool(incoming_cable_id) or bool(outgoing) or bool(mappings_raw) or bool(note)
        if has_up_data and ap_type != "up_distribution":
            warnings.append(
                f"[Value] elec_points.{pid}.up_distribution_config ist gesetzt, aber ap_type ist nicht 'up_distribution'."
            )
        if has_up_data and has_uv_data:
            errors.append(
                f"[Value] elec_points.{pid} hat gleichzeitig uv_config und up_distribution_config gesetzt."
            )
        if has_up_data and not incoming_cable_id:
            errors.append(
                f"[Value] elec_points.{pid}.up_distribution_config benötigt incoming_cable_id."
            )
        if incoming_cable_id and incoming_cable_id in outgoing:
            errors.append(
                f"[Value] elec_points.{pid}.up_distribution_config.outgoing_cable_ids enthält die Zuleitung '{incoming_cable_id}'."
            )

        seen_mapping_keys: set[tuple[str, str, str]] = set()
        for index, mapping in enumerate(mappings_raw):
            if not isinstance(mapping, dict):
                errors.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.mappings[{index}] muss ein Objekt sein."
                )
                continue
            from_conductor = str(mapping.get("from_conductor", "") or "").strip()
            to_cable_id = str(mapping.get("to_cable_id", "") or "").strip()
            to_conductor = str(mapping.get("to_conductor", "") or "").strip()
            if not from_conductor or not to_cable_id or not to_conductor:
                errors.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.mappings[{index}] benötigt from_conductor, to_cable_id und to_conductor."
                )
                continue
            if incoming_cable_id and to_cable_id == incoming_cable_id:
                errors.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.mappings[{index}] verwendet die Zuleitung auch als Abgang."
                )
            if outgoing and to_cable_id not in outgoing:
                warnings.append(
                    f"[Value] elec_points.{pid}.up_distribution_config.mappings[{index}].to_cable_id = '{to_cable_id}' ist nicht in outgoing_cable_ids enthalten."
                )
            map_key = (from_conductor, to_cable_id, to_conductor)
            if map_key in seen_mapping_keys:
                warnings.append(
                    f"[Value] elec_points.{pid}.up_distribution_config enthält die Zuordnung {from_conductor}->{to_cable_id}:{to_conductor} mehrfach."
                )
            else:
                seen_mapping_keys.add(map_key)

    # ── Farbformat prüfen ──
    def _check_color(path: str, value: str):
        if value and not _HEX_COLOR.match(value):
            warnings.append(f"[Color] {path} = '{value}' ist kein gültiges #rrggbb-Format.")

    for cid, cdata in params.get("circuits", {}).items():
        _check_color(f"circuits.{cid}.color", cdata.get("color", ""))
    for pid, pdata in params.get("elec_points", {}).items():
        _check_color(f"elec_points.{pid}.color", pdata.get("color", ""))
    _check_color("canvas.bg_color", canvas.get("bg_color", ""))

    return errors, warnings


# ─────────────────────────────────────────────────────────────
# Hauptprogramm
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validiert HRP-Projektdateien gegen Schema und semantische Regeln."
    )
    parser.add_argument("hrp_file", type=Path, help="Pfad zur .hrp-Datei.")
    parser.add_argument(
        "--schema-only", action="store_true",
        help="Nur JSON-Schema-Validierung, keine semantischen Prüfungen."
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Ausgabe im JSON-Format (maschinenlesbar)."
    )
    parser.add_argument(
        "--schema", type=Path, default=None,
        help="Pfad zur Schema-Datei (Standard: hrp_schema.json neben diesem Skript)."
    )
    parser.add_argument(
        "--repair", action="store_true",
        help="Datei reparieren und bereinigen."
    )
    parser.add_argument(
        "--in-place", action="store_true",
        help="Reparatur in derselben Datei speichern."
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Zieldatei für repariertes Projekt (ohne --in-place)."
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Kein .bak-Backup vor Reparatur erstellen."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Nur anzeigen, was repariert wuerde – keine Datei schreiben."
    )
    args = parser.parse_args()

    # HRP laden
    hrp_path = args.hrp_file.resolve()
    if not hrp_path.exists():
        print(f"Fehler: Datei nicht gefunden: {hrp_path}", file=sys.stderr)
        sys.exit(2)

    try:
        data = _load_hrp(hrp_path)
    except json.JSONDecodeError as e:
        result = {"file": str(hrp_path), "valid": False,
                  "errors": [f"JSON-Syntaxfehler: {e}"], "warnings": []}
        if args.json_output:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"✗ JSON-Syntaxfehler: {e}", file=sys.stderr)
        sys.exit(1)

    repair_changes: list[str] = []
    backup_path: Path | None = None
    validated_path = hrp_path

    if getattr(args, 'dry_run', False) and not args.repair:
        print("Fehler: --dry-run erfordert auch --repair.", file=sys.stderr)
        sys.exit(2)

    if args.repair:
        if args.in_place and args.output is not None:
            print("Fehler: --in-place und --output koennen nicht kombiniert werden.", file=sys.stderr)
            sys.exit(2)
        if getattr(args, 'dry_run', False) and args.in_place:
            print("Fehler: --dry-run und --in-place koennen nicht kombiniert werden.", file=sys.stderr)
            sys.exit(2)

        if getattr(args, 'dry_run', False):
            # Dry-run: repair_hrp_data aufrufen, aber nicht schreiben
            from storage.hrp_repair import repair_hrp_data  # noqa: PLC0415
            import copy as _copy  # noqa: PLC0415
            raw_original = _load_hrp(hrp_path)
            _repaired_dry, repair_changes = repair_hrp_data(_copy.deepcopy(raw_original), aggressive=True)
            data = _repaired_dry
            validated_path = hrp_path
            backup_path = None
        else:
            output_path = args.output
            if args.in_place:
                output_path = hrp_path
            elif output_path is None:
                output_path = hrp_path.with_name(f"{hrp_path.stem}.repaired{hrp_path.suffix}")

            try:
                data, repair_changes, backup_path, validated_path = repair_and_save_hrp(
                    hrp_path,
                    output_path=output_path,
                    backup=not args.no_backup,
                    aggressive=True,
                )
            except Exception as exc:  # noqa: BLE001
                if args.json_output:
                    result = {
                        "file": str(hrp_path),
                        "valid": False,
                        "errors": [f"Reparatur fehlgeschlagen: {exc}"],
                        "warnings": [],
                    }
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    print(f"✗ Reparatur fehlgeschlagen: {exc}", file=sys.stderr)
                sys.exit(1)

    # Schema laden
    schema_path = args.schema
    if schema_path is None:
        schema_path = Path(__file__).parent / "hrp_schema.json"
    schema_path = schema_path.resolve()

    if not schema_path.exists():
        print(f"Warnung: Schema-Datei nicht gefunden: {schema_path}", file=sys.stderr)
        schema = None
    else:
        schema = _load_schema(schema_path)

    # Validierung durchführen
    all_errors: list[str] = []
    all_warnings: list[str] = []

    if schema:
        all_errors.extend(validate_schema(data, schema))

    if not args.schema_only:
        sem_errors, sem_warnings = validate_semantic(data)
        all_errors.extend(sem_errors)
        all_warnings.extend(sem_warnings)

    # Ausgabe
    is_valid = len(all_errors) == 0

    if args.json_output:
        result = {
            "file": str(validated_path),
            "valid": is_valid,
            "errors": all_errors,
            "warnings": all_warnings,
        }
        if args.repair:
            result["repair"] = {
                "dry_run": getattr(args, 'dry_run', False),
                "changes": repair_changes,
                "backup": str(backup_path) if backup_path is not None else None,
            }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if args.repair:
            if getattr(args, 'dry_run', False):
                print(f"i Dry-run: keine Datei geschrieben ({hrp_path.name})")
            else:
                print(f"i Reparierte Datei: {validated_path}")
                if backup_path is not None:
                    print(f"i Backup erstellt: {backup_path}")
            if repair_changes:
                print(f"i Reparatur-Aenderungen: {len(repair_changes)}")
                for change in repair_changes:
                    print(f"  - {change}")
            else:
                print("i Reparatur-Aenderungen: 0")

        if is_valid and not all_warnings:
            print(f"✓ {validated_path.name}: Gültig.")
        elif is_valid:
            print(f"✓ {validated_path.name}: Gültig (mit Warnungen).")
            for w in all_warnings:
                print(f"  ⚠ {w}")
        else:
            print(f"✗ {validated_path.name}: Ungültig ({len(all_errors)} Fehler).")
            for e in all_errors:
                print(f"  ✗ {e}")
            for w in all_warnings:
                print(f"  ⚠ {w}")

    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
