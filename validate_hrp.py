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
            "file": str(hrp_path),
            "valid": is_valid,
            "errors": all_errors,
            "warnings": all_warnings,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if is_valid and not all_warnings:
            print(f"✓ {hrp_path.name}: Gültig.")
        elif is_valid:
            print(f"✓ {hrp_path.name}: Gültig (mit Warnungen).")
            for w in all_warnings:
                print(f"  ⚠ {w}")
        else:
            print(f"✗ {hrp_path.name}: Ungültig ({len(all_errors)} Fehler).")
            for e in all_errors:
                print(f"  ✗ {e}")
            for w in all_warnings:
                print(f"  ⚠ {w}")

    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
