#!/usr/bin/env python3
# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# Standalone MCP-Server (stdio) für die Copilot CLI.
# Arbeitet direkt auf .hrp-Dateien ohne die GUI.
#
# Nutzung:
#   copilot --additional-mcp-config @.copilot/mcp-config.json
#   copilot -p "Welche Heizkreise gibt es?" --additional-mcp-config @.copilot/mcp-config.json

"""
Standalone MCP-Server für HRouting (stdio-Transport).

Dieser Server kann von der GitHub Copilot CLI direkt gestartet werden
und arbeitet auf .hrp-Projektdateien ohne laufende GUI. Er bietet
Lese- und Schreibzugriff auf HRouting-Projekte.

Konfiguration in ~/.copilot/mcp-config.json oder per --additional-mcp-config.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

logger = logging.getLogger("hrouting.mcp-stdio")

BASE_DIR = Path(__file__).parent

# ── Projekt-State ──────────────────────────────────────────────────

class ProjectState:
    """Hält den Zustand eines geladenen .hrp-Projekts."""

    def __init__(self):
        self.path: Path | None = None
        self.data: dict = self._empty_project()
        self._dirty = False

    @property
    def dirty(self) -> bool:
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool):
        self._dirty = value
        if value and self.path:
            self.save()
            logger.debug("Auto-save: %s", self.path)

    @staticmethod
    def _empty_project() -> dict:
        return {
            "svg_path": "",
            "canvas": {
                "view_scale": 1.0, "view_offset": [0, 0],
                "bg_color": "#2b2b2b",
                "grid_visible": True, "grid_spacing_mm": 100,
                "grid_color": [255, 255, 255, 30],
                "snap_angle": 0, "export_frame": None,
                "measure_color": "#ffdd00", "mm_per_px": 1.0,
                "ref_line": None, "ref_line_colors": {},
                "ref_line_visible": {}, "polygons": {},
                "start_points": {}, "manual_routes": {},
                "route_wall_dist_px": {}, "route_line_dist_px": {},
                "supply_lines": {}, "elec_points": {},
                "elec_point_size_px": {}, "elec_point_position": {},
                "elec_point_height": {}, "elec_point_notes": {},
                "elec_point_smarthome_device": {},
                "elec_point_smarthome_device_color": {},
                "elec_rooms": {}, "elec_room_visible": {},
                "elec_cables": {}, "elec_cable_notes": {},
                "cable_start_ap": {}, "cable_end_ap": {},
                "elec_visible": {}, "hkv_points": {},
                "hkv_size_px": {}, "hkv_visible": {},
                "supply_hkv": {}, "hkv_lines": {},
                "hkv_line_start": {}, "hkv_line_end": {},
                "hkv_line_visible": {}, "label_positions": {},
                "label_font_sizes": {}, "label_visible": {},
                "text_annotations": {}, "floor_plans": [],
            },
            "params": {
                "t_supply": 35.0, "t_return": 30.0,
                "t_norm_outdoor": -12.0,
                "elec_cable_defaults": {},
                "floorplans_order": [], "floorplans": {},
                "furniture": {}, "circuits": {},
                "elec_points": {}, "elec_rooms": {},
                "elec_cables": {}, "hkv_points": {},
                "hkv_lines": {}, "text_annotations": {},
            },
            "pdf_export_pages": [],
        }

    def load(self, path: str) -> dict:
        """Lädt ein .hrp-Projekt."""
        p = Path(path)
        if not p.exists():
            return {"error": f"Datei nicht gefunden: {path}"}
        try:
            self.data = json.loads(p.read_text(encoding="utf-8"))
            self.path = p
            self._dirty = False
            return {"status": "ok", "path": str(p), "info": self._summary()}
        except Exception as e:
            return {"error": f"Fehler beim Laden: {e}"}

    def save(self, path: str = "") -> dict:
        """Speichert das Projekt."""
        target = Path(path) if path else self.path
        if not target:
            return {"error": "Kein Dateipfad angegeben."}
        try:
            target.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.path = target
            self._dirty = False
            return {"status": "ok", "path": str(target)}
        except Exception as e:
            return {"error": f"Fehler beim Speichern: {e}"}

    def _summary(self) -> dict:
        p = self.data.get("params", {})
        c = self.data.get("canvas", {})
        return {
            "circuits": len(p.get("circuits", {})),
            "elec_points": len(p.get("elec_points", {})),
            "floor_plans": len(p.get("floorplans", {})),
            "elec_rooms": len(p.get("elec_rooms", {})),
            "elec_cables": len(p.get("elec_cables", {})),
            "hkv_count": len(p.get("hkv_points", {})),
            "mm_per_px": c.get("mm_per_px", 1.0),
        }

    def _next_id(self, prefix: str, collection: dict) -> str:
        """Erzeugt die nächste freie ID (z.B. HK-3)."""
        existing = [
            int(k.split("-")[1]) for k in collection
            if k.startswith(prefix + "-") and k.split("-")[1].isdigit()
        ]
        n = max(existing, default=0) + 1
        return f"{prefix}-{n}"


# Globaler State
_state = ProjectState()


def _create_stdio_mcp():
    """Erstellt den FastMCP-Server mit stdio-Transport."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "HRouting",
        instructions=(
            "HRouting MCP Server – Fußbodenheizungs- und Elektroplanung. "
            "Arbeitet auf .hrp-Projektdateien. "
            "Nutze open_project() zum Laden und get_project_summary() als Einstieg. "
            "Koordinaten sind in Canvas-Pixeln. Einheiten: mm."
        ),
    )

    # ── Projekt-Management ────────────────────────────────────────

    @mcp.tool()
    def open_project(path: str) -> dict:
        """HRP-Projektdatei öffnen/laden.

        Args:
            path: Pfad zur .hrp-Datei (absolut oder relativ)
        """
        return _state.load(path)

    @mcp.tool()
    def save_project(path: str = "") -> dict:
        """Aktuelles Projekt speichern.

        Args:
            path: Dateipfad (.hrp). Leer = aktueller Projektpfad.
        """
        return _state.save(path)

    @mcp.tool()
    def get_project_summary() -> dict:
        """Projektübersicht: Anzahl Elemente, Parameter, Maßstab."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        return {
            "project_file": str(_state.path) if _state.path else None,
            "dirty": _state.dirty,
            "floor_plans": list(p.get("floorplans", {}).keys()),
            "circuit_count": len(p.get("circuits", {})),
            "circuit_ids": list(p.get("circuits", {}).keys()),
            "elec_point_count": len(p.get("elec_points", {})),
            "elec_point_ids": list(p.get("elec_points", {}).keys()),
            "elec_room_count": len(p.get("elec_rooms", {})),
            "elec_cable_count": len(p.get("elec_cables", {})),
            "hkv_count": len(p.get("hkv_points", {})),
            "t_supply": p.get("t_supply", 35.0),
            "t_return": p.get("t_return", 30.0),
            "t_norm_outdoor": p.get("t_norm_outdoor", -12.0),
            "mm_per_px": c.get("mm_per_px", 1.0),
        }

    @mcp.tool()
    def get_project_json() -> dict:
        """Vollständiges Projekt als JSON zurückgeben."""
        return _state.data

    # ── Lese-Tools ────────────────────────────────────────────────

    @mcp.tool()
    def list_circuits() -> list[dict]:
        """Liste aller Heizkreise mit Parametern."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for cid, cdata in p.get("circuits", {}).items():
            entry = dict(cdata)
            poly = c.get("polygons", {}).get(cid, [])
            entry["polygon_points"] = len(poly)
            entry["has_polygon"] = len(poly) >= 3
            route = c.get("manual_routes", {}).get(cid, [])
            entry["has_route"] = len(route) >= 2
            result.append(entry)
        return result

    @mcp.tool()
    def list_elec_points() -> list[dict]:
        """Liste aller Elektro-Anschlusspunkte."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for pid, pdata in p.get("elec_points", {}).items():
            entry = dict(pdata)
            pos = c.get("elec_points", {}).get(pid)
            entry["canvas_position"] = list(pos) if pos else None
            result.append(entry)
        return result

    @mcp.tool()
    def list_hkvs() -> list[dict]:
        """Liste aller Heizkreisverteiler."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for hid, hdata in p.get("hkv_points", {}).items():
            entry = dict(hdata)
            pos = c.get("hkv_points", {}).get(hid)
            entry["canvas_position"] = list(pos) if pos else None
            result.append(entry)
        return result

    @mcp.tool()
    def list_elec_rooms() -> list[dict]:
        """Liste aller Elektro-Räume."""
        return list(_state.data.get("params", {}).get("elec_rooms", {}).values())

    @mcp.tool()
    def list_elec_cables() -> list[dict]:
        """Liste aller Elektro-Kabel."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for eid, edata in p.get("elec_cables", {}).items():
            entry = dict(edata)
            entry["start_ap"] = c.get("cable_start_ap", {}).get(eid, "")
            entry["end_ap"] = c.get("cable_end_ap", {}).get(eid, "")
            result.append(entry)
        return result

    @mcp.tool()
    def list_floor_plans() -> list[dict]:
        """Liste aller Grundriss-Layer."""
        return _state.data.get("canvas", {}).get("floor_plans", [])

    @mcp.tool()
    def list_texts() -> list[dict]:
        """Liste aller Text-Annotationen."""
        return list(_state.data.get("params", {}).get("text_annotations", {}).values())

    # ── Heizkreis-Tools ───────────────────────────────────────────

    @mcp.tool()
    def add_circuit(
        name: str,
        polygon: list[list[float]],
        floor_plan_id: str = "",
        color: str = "#2a9d8f",
        diameter: float = 16.0,
        spacing: float = 150.0,
        wall_dist: float = 200.0,
        room_temp: float = 20.0,
        floor_covering: str = "Fliesen / Keramik",
    ) -> dict:
        """Neuen Heizkreis mit Raumpolygon hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Wohnzimmer', 'Badezimmer')
            polygon: Raumpolygon [[x1,y1], [x2,y2], ...] in Canvas-Pixeln (min. 3 Punkte)
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
            diameter: Rohrdurchmesser in mm
            spacing: Verlegeabstand in mm
            wall_dist: Wandabstand in mm
            room_temp: Raumtemperatur °C
            floor_covering: Bodenbelag
        """
        if len(polygon) < 3:
            return {"error": "Polygon muss mindestens 3 Punkte haben."}

        circuits = _state.data["params"].setdefault("circuits", {})
        cid = _state._next_id("HK", circuits)

        circuits[cid] = {
            "circuit_id": cid,
            "floor_plan_id": floor_plan_id,
            "name": name,
            "color": color,
            "diameter": diameter,
            "spacing": spacing,
            "wall_dist": wall_dist,
            "visible": True,
            "label_visible": True,
            "label_size": 12.0,
            "room_temp": room_temp,
            "floor_covering": floor_covering,
            "distributor": "",
        }
        _state.data["canvas"].setdefault("polygons", {})[cid] = polygon
        _state.data["canvas"].setdefault("start_points", {})[cid] = polygon[0]
        _state.dirty = True
        return {"circuit_id": cid, "status": "ok", "name": name}

    @mcp.tool()
    def modify_circuit(
        circuit_id: str,
        name: str | None = None,
        color: str | None = None,
        diameter: float | None = None,
        spacing: float | None = None,
        wall_dist: float | None = None,
        room_temp: float | None = None,
        floor_covering: str | None = None,
    ) -> dict:
        """Heizkreis-Parameter ändern.

        Args:
            circuit_id: Heizkreis-ID (z.B. 'HK-1')
        """
        circuits = _state.data["params"].get("circuits", {})
        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        c = circuits[circuit_id]
        if name is not None: c["name"] = name
        if color is not None: c["color"] = color
        if diameter is not None: c["diameter"] = diameter
        if spacing is not None: c["spacing"] = spacing
        if wall_dist is not None: c["wall_dist"] = wall_dist
        if room_temp is not None: c["room_temp"] = room_temp
        if floor_covering is not None: c["floor_covering"] = floor_covering
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "modified"}

    @mcp.tool()
    def delete_circuit(circuit_id: str) -> dict:
        """Heizkreis löschen.

        Args:
            circuit_id: ID (z.B. 'HK-1')
        """
        circuits = _state.data["params"].get("circuits", {})
        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        del circuits[circuit_id]
        canvas = _state.data["canvas"]
        for key in ["polygons", "start_points", "manual_routes",
                     "route_wall_dist_px", "route_line_dist_px", "supply_lines"]:
            canvas.get(key, {}).pop(circuit_id, None)
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "deleted"}

    @mcp.tool()
    def set_circuit_polygon(circuit_id: str, polygon: list[list[float]]) -> dict:
        """Raumpolygon eines Heizkreises setzen/ersetzen.

        Args:
            circuit_id: Heizkreis-ID
            polygon: Neues Polygon [[x,y], ...] in Canvas-Pixeln
        """
        if circuit_id not in _state.data["params"].get("circuits", {}):
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        if len(polygon) < 3:
            return {"error": "Mindestens 3 Punkte nötig."}
        _state.data["canvas"]["polygons"][circuit_id] = polygon
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "ok"}

    # ── Elektro-Tools ─────────────────────────────────────────────

    @mcp.tool()
    def add_elec_point(
        name: str,
        x: float,
        y: float,
        floor_plan_id: str = "",
        color: str = "#4fc3f7",
        builtin_symbol: str = "Steckdose",
        width: float = 30.0,
        height: float = 30.0,
        position: str = "Wand",
        height_from_floor: float = 30.0,
        note: str = "",
    ) -> dict:
        """Elektro-Anschlusspunkt platzieren.

        Args:
            name: Anzeigename
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
        """
        points = _state.data["params"].setdefault("elec_points", {})
        pid = _state._next_id("AP", points)

        points[pid] = {
            "point_id": pid,
            "floor_plan_id": floor_plan_id,
            "name": name,
            "color": color,
            "width": width,
            "height": height,
            "icon_path": "",
            "builtin_symbol": builtin_symbol,
            "visible": True,
            "label_visible": True,
            "label_size": 12.0,
            "position": position,
            "height_from_floor": height_from_floor,
            "smarthome_device": "",
            "smarthome_device_color": "",
            "note": note,
        }
        _state.data["canvas"].setdefault("elec_points", {})[pid] = [x, y]
        _state.dirty = True
        return {"point_id": pid, "status": "ok"}

    @mcp.tool()
    def modify_elec_point(
        point_id: str,
        name: str | None = None,
        x: float | None = None,
        y: float | None = None,
        color: str | None = None,
        builtin_symbol: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Anschlusspunkt-Parameter ändern."""
        pts = _state.data["params"].get("elec_points", {})
        if point_id not in pts:
            return {"error": f"AP '{point_id}' nicht gefunden."}
        p = pts[point_id]
        if name is not None: p["name"] = name
        if color is not None: p["color"] = color
        if builtin_symbol is not None: p["builtin_symbol"] = builtin_symbol
        if note is not None: p["note"] = note
        if x is not None or y is not None:
            pos = _state.data["canvas"].get("elec_points", {}).get(point_id, [0, 0])
            if x is not None: pos[0] = x
            if y is not None: pos[1] = y
            _state.data["canvas"]["elec_points"][point_id] = pos
        _state.dirty = True
        return {"point_id": point_id, "status": "modified"}

    @mcp.tool()
    def delete_elec_point(point_id: str) -> dict:
        """Anschlusspunkt löschen."""
        pts = _state.data["params"].get("elec_points", {})
        if point_id not in pts:
            return {"error": f"AP '{point_id}' nicht gefunden."}
        del pts[point_id]
        _state.data["canvas"].get("elec_points", {}).pop(point_id, None)
        _state.data["canvas"].get("elec_point_size_px", {}).pop(point_id, None)
        _state.dirty = True
        return {"point_id": point_id, "status": "deleted"}

    # ── Heizungsparameter ─────────────────────────────────────────

    @mcp.tool()
    def set_heating_params(
        t_supply: float | None = None,
        t_return: float | None = None,
        t_norm_outdoor: float | None = None,
    ) -> dict:
        """Globale Heizungsparameter ändern.

        Args:
            t_supply: Vorlauftemperatur °C (20–90)
            t_return: Rücklauftemperatur °C (15–80)
            t_norm_outdoor: Normaußentemperatur °C (-30 bis 5)
        """
        p = _state.data["params"]
        if t_supply is not None:
            p["t_supply"] = max(20, min(90, t_supply))
        if t_return is not None:
            p["t_return"] = max(15, min(80, t_return))
        if t_norm_outdoor is not None:
            p["t_norm_outdoor"] = max(-30, min(5, t_norm_outdoor))
        _state.dirty = True
        return {
            "status": "ok",
            "t_supply": p["t_supply"],
            "t_return": p["t_return"],
            "t_norm_outdoor": p["t_norm_outdoor"],
        }

    # ── Heizlast-Berechnung ───────────────────────────────────────

    @mcp.tool()
    def calculate_heating(circuit_id: str) -> dict:
        """Heizlast für einen Heizkreis berechnen (DIN EN 1264).

        Args:
            circuit_id: Heizkreis-ID (z.B. 'HK-1')
        """
        params = _state.data["params"]
        circuits = params.get("circuits", {})
        canvas = _state.data["canvas"]

        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}

        circ = circuits[circuit_id]
        poly = canvas.get("polygons", {}).get(circuit_id, [])
        if len(poly) < 3:
            return {"error": "Kein gültiges Polygon vorhanden."}

        try:
            from logic.heating_calc import calc_circuit
        except ImportError:
            return {"error": "heating_calc Modul nicht verfügbar."}

        mm_per_px = canvas.get("mm_per_px", 1.0)
        # Fläche berechnen (Shoelace)
        n = len(poly)
        area_px = abs(sum(
            poly[i][0] * poly[(i+1) % n][1] - poly[(i+1) % n][0] * poly[i][1]
            for i in range(n)
        )) / 2.0
        area_m2 = area_px * (mm_per_px ** 2) / 1e6

        # Rohrlänge schätzen
        spacing_m = circ.get("spacing", 150) / 1000
        pipe_length = area_m2 / spacing_m if spacing_m > 0 else 0

        result = calc_circuit(
            t_supply=params.get("t_supply", 35.0),
            t_return=params.get("t_return", 30.0),
            room_temp=circ.get("room_temp", 20.0),
            spacing_mm=circ.get("spacing", 150),
            diameter_mm=circ.get("diameter", 16),
            area_m2=area_m2,
            pipe_length_m=pipe_length,
            floor_covering=circ.get("floor_covering", "Fliesen / Keramik"),
        )
        result["circuit_id"] = circuit_id
        result["area_m2"] = round(area_m2, 2)
        result["pipe_length_m"] = round(pipe_length, 1)
        return result

    @mcp.tool()
    def calculate_all_circuits() -> list[dict]:
        """Heizlast aller Heizkreise berechnen."""
        results = []
        for cid in _state.data["params"].get("circuits", {}):
            results.append(calculate_heating(cid))
        return results

    # ── Validierung ───────────────────────────────────────────────

    @mcp.tool()
    def validate_project() -> dict:
        """Projekt gegen das HRP-Schema validieren."""
        try:
            import jsonschema
            schema_path = BASE_DIR / "hrp_schema.json"
            if not schema_path.exists():
                return {"valid": False, "errors": ["Schema nicht gefunden."]}
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema)
            errors = [
                {"path": "/".join(str(s) for s in e.absolute_path), "message": e.message}
                for e in validator.iter_errors(_state.data)
            ]
            return {"valid": len(errors) == 0, "errors": errors[:10]}
        except ImportError:
            return {"error": "jsonschema-Paket nicht installiert."}
        except Exception as e:
            return {"valid": False, "errors": [str(e)]}

    # ── Grundriss-Tools ───────────────────────────────────────────

    @mcp.tool()
    def add_floor_plan(
        name: str,
        file_path: str = "",
        offset_x: float = 0,
        offset_y: float = 0,
        opacity: float = 1.0,
    ) -> dict:
        """Neuen Grundriss-Layer hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Erdgeschoss')
            file_path: Pfad zum Bild (relativ zum Projekt)
        """
        fps = _state.data["params"].setdefault("floorplans", {})
        fp_id = _state._next_id("grundriss", fps)

        fps[fp_id] = {
            "fp_id": fp_id,
            "name": name,
            "file_path": file_path,
        }

        order = _state.data["params"].setdefault("floorplans_order", [])
        order.append(fp_id)

        floor_plans = _state.data["canvas"].setdefault("floor_plans", [])
        floor_plans.append({
            "fp_id": fp_id,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "rotation": 0.0,
            "opacity": opacity,
            "visible": True,
            "mm_per_px": 1.0,
            "ref_length_mm": 1000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "polygon_color": "#8d99ae",
            "ref_line": [],
            "polygon": [],
        })
        _state.dirty = True
        return {"floor_plan_id": fp_id, "status": "ok", "name": name}

    # ── Text-Tools ────────────────────────────────────────────────

    @mcp.tool()
    def add_text(
        text: str,
        x: float,
        y: float,
        color: str = "#ffffff",
        font_size: float = 14.0,
    ) -> dict:
        """Text-Annotation hinzufügen.

        Args:
            text: Anzuzeigender Text
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
        """
        annotations = _state.data["params"].setdefault("text_annotations", {})
        tid = _state._next_id("TEXT", annotations)
        annotations[tid] = {
            "text_id": tid,
            "text": text,
            "color": color,
            "font_size": font_size,
            "visible": True,
        }
        _state.data["canvas"].setdefault("text_annotations", {})[tid] = [x, y]
        _state.dirty = True
        return {"text_id": tid, "status": "ok"}

    @mcp.tool()
    def delete_text(text_id: str) -> dict:
        """Text-Annotation löschen."""
        annotations = _state.data["params"].get("text_annotations", {})
        if text_id not in annotations:
            return {"error": f"Text '{text_id}' nicht gefunden."}
        del annotations[text_id]
        _state.data["canvas"].get("text_annotations", {}).pop(text_id, None)
        _state.dirty = True
        return {"text_id": text_id, "status": "deleted"}

    # ── Resources ─────────────────────────────────────────────────

    @mcp.resource("hrp://schema")
    def get_schema() -> str:
        """JSON-Schema für .hrp-Projektdateien."""
        schema_path = BASE_DIR / "hrp_schema.json"
        if schema_path.exists():
            return schema_path.read_text(encoding="utf-8")
        return '{"error": "Schema nicht gefunden"}'

    @mcp.resource("hrp://instructions")
    def get_instructions() -> str:
        """Agenten-Anleitung."""
        doc_path = BASE_DIR / ".github" / "copilot-instructions.md"
        if doc_path.exists():
            return doc_path.read_text(encoding="utf-8")
        return "# Anleitung nicht gefunden"

    return mcp


# ── Entry Point ────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,  # Logs auf stderr, stdio für MCP
    )

    # Auto-load: .hrp-Datei als Argument
    if len(sys.argv) > 1 and sys.argv[1].endswith(".hrp"):
        result = _state.load(sys.argv[1])
        logger.info(f"Projekt geladen: {result}")

    mcp = _create_stdio_mcp()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
