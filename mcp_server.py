# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
MCP Server für HRouting – Model Context Protocol Integration.

Stellt einen lokalen HTTP-Server bereit, über den KI-Agenten
(Claude, Copilot, etc.) HRouting-Projekte lesen und bearbeiten können.

Endpunkt: http://127.0.0.1:3274/mcp

Konfiguration für Claude Desktop (claude_desktop_config.json):
    {
      "mcpServers": {
        "hrouting": {
          "url": "http://127.0.0.1:3274/mcp"
        }
      }
    }
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("hrouting.mcp")

MCP_HOST = "127.0.0.1"
MCP_PORT = 3274


# ── Verfügbarkeits-Check ───────────────────────────────────────────


def is_available() -> bool:
    """Prüft ob die MCP-Pakete installiert sind."""
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        import uvicorn  # noqa: F401
        return True
    except ImportError:
        return False


# ── Thread-safe Bridge ─────────────────────────────────────────────


def _create_bridge(window: MainWindow):
    """Erzeugt ein Bridge-Objekt für thread-sichere Aufrufe auf dem Qt-Main-Thread.

    Muss auf dem Main-Thread aufgerufen werden (nach QApplication-Start).
    """
    from PySide6.QtCore import QObject, QEvent, QCoreApplication

    event_type = QEvent.Type(QEvent.registerEventType())

    class _McpReceiver(QObject):
        """Empfängt Custom-Events und führt Callables auf dem Main-Thread aus."""

        def customEvent(self, event):
            if event.type() == event_type and hasattr(event, "_fn"):
                try:
                    event._result[0] = event._fn()
                except Exception as e:
                    event._result[1] = e
                finally:
                    event._done.set()

    class _McpEvent(QEvent):
        def __init__(self, fn, result, done):
            super().__init__(event_type)
            self._fn = fn
            self._result = result
            self._done = done

    receiver = _McpReceiver()

    class Bridge:
        """Thread-safe Bridge zwischen MCP-Server-Thread und Qt-Main-Thread."""

        def invoke(self, fn):
            """Führt fn() auf dem Qt-Main-Thread aus. Blockiert bis zum Ergebnis."""
            result = [None, None]  # [value, error]
            done = threading.Event()
            QCoreApplication.postEvent(receiver, _McpEvent(fn, result, done))
            if not done.wait(timeout=30):
                raise TimeoutError(
                    "Qt-Main-Thread hat nicht rechtzeitig geantwortet.")
            if result[1]:
                raise result[1]
            return result[0]

    return Bridge()


# ── MCP Server erstellen ──────────────────────────────────────────


def _create_mcp(window: MainWindow, bridge):
    """Erstellt den FastMCP-Server mit allen Tools und Resources."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "HRouting",
        instructions=(
            "HRouting MCP Server – Fußbodenheizungs- und Elektroplanung. "
            "Nutze get_project_summary() als Einstieg, um den aktuellen "
            "Projektzustand zu sehen. Koordinaten sind in Canvas-Pixeln. "
            "Einheiten in params: mm (diameter, spacing, wall_dist, width, "
            "height). Nutze get_schema() für das vollständige Dateiformat."
        ),
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
        json_response=True,
    )

    invoke = bridge.invoke

    # ── Resources ──────────────────────────────────────────────────

    @mcp.resource("hrp://schema")
    def get_schema() -> str:
        """Das JSON-Schema für HRP-Projektdateien (hrp_schema.json)."""
        # PyInstaller: Resourcen liegen in sys._MEIPASS
        import sys as _sys
        base = Path(getattr(_sys, '_MEIPASS', Path(__file__).parent))
        schema_path = base / "hrp_schema.json"
        if schema_path.exists():
            return schema_path.read_text(encoding="utf-8")
        return '{"error": "hrp_schema.json nicht gefunden"}'

    @mcp.resource("hrp://instructions")
    def get_instructions() -> str:
        """Agenten-Anleitung für HRP-Dateien (.github/copilot-instructions.md)."""
        import sys as _sys
        base = Path(getattr(_sys, '_MEIPASS', Path(__file__).parent))
        doc_path = base / ".github" / "copilot-instructions.md"
        if doc_path.exists():
            return doc_path.read_text(encoding="utf-8")
        return "# Anleitung nicht gefunden"

    # ── Lese-Tools ─────────────────────────────────────────────────

    @mcp.tool()
    def get_project_summary() -> dict:
        """Projektübersicht: Anzahl aller Elemente, globale Heizungsparameter,
        Maßstab. Nutze dieses Tool als Einstieg."""
        def _read():
            p = window.param_panel.to_dict()
            return {
                "floor_plans": list(p.get("floorplans", {}).keys()),
                "floor_plan_count": len(p.get("floorplans", {})),
                "furniture_count": len(p.get("furniture", {})),
                "circuit_count": len(p.get("circuits", {})),
                "circuit_ids": list(p.get("circuits", {}).keys()),
                "elec_point_count": len(p.get("elec_points", {})),
                "elec_point_ids": list(p.get("elec_points", {}).keys()),
                "elec_room_count": len(p.get("elec_rooms", {})),
                "elec_cable_count": len(p.get("elec_cables", {})),
                "hkv_count": len(p.get("hkv_points", {})),
                "hkv_ids": list(p.get("hkv_points", {}).keys()),
                "t_supply": p.get("t_supply", 35.0),
                "t_return": p.get("t_return", 30.0),
                "t_norm_outdoor": p.get("t_norm_outdoor", -12.0),
                "mm_per_px": window.canvas.get_mm_per_px(),
                "has_project_file": window._project_path is not None,
            }
        return invoke(_read)

    @mcp.tool()
    def list_circuits() -> list[dict]:
        """Liste aller Heizkreise mit Parametern und Geometrie-Info."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
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
        return invoke(_read)

    @mcp.tool()
    def list_elec_points() -> list[dict]:
        """Liste aller Elektro-Anschlusspunkte mit Parametern und Position."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for pid, pdata in p.get("elec_points", {}).items():
                entry = dict(pdata)
                pos = c.get("elec_points", {}).get(pid)
                entry["canvas_position"] = list(pos) if pos else None
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def list_hkvs() -> list[dict]:
        """Liste aller Heizkreisverteiler mit Parametern und Position."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for hid, hdata in p.get("hkv_points", {}).items():
                entry = dict(hdata)
                pos = c.get("hkv_points", {}).get(hid)
                entry["canvas_position"] = list(pos) if pos else None
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def get_project_json() -> dict:
        """Vollständiges Projekt als JSON-Objekt (canvas + params).
        Gibt die komplette Datenstruktur zurück, wie sie in einer
        .hrp-Datei gespeichert würde."""
        def _read():
            return {
                "svg_path": getattr(window, "_svg_path", ""),
                "canvas": window.canvas.to_dict(),
                "params": window.param_panel.to_dict(),
            }
        return invoke(_read)

    # ── Heizkreis-Tools ────────────────────────────────────────────

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
            polygon: Raumpolygon als [[x1,y1], [x2,y2], ...] in
                Canvas-Pixeln. Mindestens 3 Punkte.
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1'),
                leer = erster Grundriss
            color: Hex-Farbe (#rrggbb)
            diameter: Rohraußendurchmesser in mm (Standard: 16)
            spacing: Verlegeabstand in mm (Standard: 150, Bad: 100)
            wall_dist: Wandabstand in mm (Standard: 200)
            room_temp: Raumtemperatur °C (Wohnraum: 20, Bad: 24)
            floor_covering: Bodenbelag – gültige Werte:
                'Estrich (kein Belag)', 'Fliesen / Keramik',
                'Naturstein', 'PVC / Vinyl', 'Laminat',
                'Parkett dünn (≤ 10 mm)', 'Parkett dick (> 10 mm)',
                'Teppich dünn', 'Teppich dick'
        """
        if len(polygon) < 3:
            return {"error": "Polygon muss mindestens 3 Punkte haben."}

        def _add():
            from PySide6.QtCore import QPointF

            w = window
            w._circuit_counter += 1
            cid = f"HK-{w._circuit_counter}"

            w._create_circuit_panel(cid, fp_id=floor_plan_id or None,
                                    name=name, color=color)

            # Polygon setzen
            w.canvas._polygons[cid] = [
                QPointF(p[0], p[1]) for p in polygon]
            w.canvas._ensure_color(cid)

            # Startpunkt = erster Polygon-Punkt
            w.canvas._start_points[cid] = QPointF(
                polygon[0][0], polygon[0][1])

            # Parameter setzen
            panel = w.param_panel.circuit_panels.get(cid)
            if panel:
                panel.from_dict({
                    "circuit_id": cid,
                    "name": name,
                    "color": color,
                    "diameter": diameter,
                    "spacing": spacing,
                    "wall_dist": wall_dist,
                    "room_temp": room_temp,
                    "floor_covering": floor_covering,
                })

            w.canvas.update()
            w._update_circuit_area(cid)
            w._recalc_circuit_hydraulics(cid)
            w._dirty = True
            w._update_title()

            return {
                "circuit_id": cid,
                "name": name,
                "polygon_points": len(polygon),
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_circuit(
        circuit_id: str,
        name: str | None = None,
        spacing: float | None = None,
        diameter: float | None = None,
        wall_dist: float | None = None,
        room_temp: float | None = None,
        floor_covering: str | None = None,
        color: str | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Parameter eines bestehenden Heizkreises ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            circuit_id: ID des Heizkreises (z.B. 'HK-1')
            name: Neuer Anzeigename
            spacing: Verlegeabstand in mm
            diameter: Rohrdurchmesser in mm
            wall_dist: Wandabstand in mm
            room_temp: Raumtemperatur °C
            floor_covering: Bodenbelag (gültige Werte siehe add_circuit)
            color: Farbe (#rrggbb)
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtGui import QColor

            panel = window.param_panel.circuit_panels.get(circuit_id)
            if not panel:
                return {
                    "error": f"Heizkreis '{circuit_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[circuit_id] = name
            if color is not None:
                panel.set_color(color)
                window.canvas.set_color(circuit_id, QColor(color))
            if diameter is not None:
                panel.sb_diameter.setValue(diameter / 10.0)
            if spacing is not None:
                panel.sb_spacing.setValue(spacing / 10.0)
            if wall_dist is not None:
                panel.sb_wall_dist.setValue(wall_dist / 10.0)
            if room_temp is not None:
                panel.sb_room_temp.setValue(room_temp)
            if floor_covering is not None:
                idx = panel.cb_floor_covering.findText(floor_covering)
                if idx >= 0:
                    panel.cb_floor_covering.setCurrentIndex(idx)
                else:
                    return {
                        "error": f"Ungültiger Bodenbelag: "
                                 f"'{floor_covering}'"}
            if visible is not None:
                panel.chk_visible.setChecked(visible)

            window._recalc_circuit_hydraulics(circuit_id)
            window.canvas.update()
            window._dirty = True
            window._update_title()

            return {
                "circuit_id": circuit_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_circuit(circuit_id: str) -> dict:
        """Heizkreis löschen.

        Args:
            circuit_id: ID des zu löschenden Heizkreises (z.B. 'HK-1')
        """
        def _delete():
            if circuit_id not in window.param_panel.circuit_panels:
                return {
                    "error": f"Heizkreis '{circuit_id}' nicht gefunden."}
            window._delete_circuit(circuit_id)
            window._dirty = True
            window._update_title()
            return {"circuit_id": circuit_id, "status": "deleted"}

        return invoke(_delete)

    # ── Elektro-Tools ──────────────────────────────────────────────

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
        smarthome_device: str = "",
        smarthome_device_color: str = "",
        note: str = "",
    ) -> dict:
        """Elektro-Anschlusspunkt platzieren.

        Args:
            name: Anzeigename (z.B. 'Steckdose Küche', 'Licht Bad')
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
            builtin_symbol: Builtin-Symbol – verfügbar:
                Audio, Audiobuchse, Bewegungsmelder, E-Rollladen,
                E-Rollo, Fensterkontakt, Garagentor, Gong, HDMI,
                Heizkreisverteiler, Hitzemelder, LAN, LAN 2fach,
                Licht, Lichtquelle, Lichtquelle dimmbar,
                Lichtquelle Wand, Markise, Praesenzmelder,
                Rauchmelder, Steckdose, Steckdose 2fach,
                Steckdose 5fach, Steckdose schaltbar,
                Steckdose Starkstrom, Taster, Taster 4fach,
                Temperaturfuehler, Temperaturmessung, Thermostat,
                TV, Türkontakt, Wetterstation, WLanHotspot, Zutritt
            width: Symbolbreite in mm (Standard: 30)
            height: Symbolhöhe in mm (Standard: 30)
            position: Einbauort – 'Wand', 'Decke', 'Boden'
                oder Freitext
            height_from_floor: Einbauhöhe vom Boden in cm
            smarthome_device: z.B. 'Shelly'
            smarthome_device_color: z.B. 'weiß'
            note: Freitext-Notiz
        """
        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._elec_point_counter += 1
            pid = f"AP-{w._elec_point_counter}"

            panel = w._create_elec_point_panel(
                pid, fp_id=floor_plan_id or None, name=name)

            # Position auf Canvas setzen
            w.canvas._elec_points[pid] = QPointF(x, y)

            # Größe in Pixeln setzen
            mm_px = w.canvas.get_mm_per_px()
            w_px = width / mm_px if mm_px > 0 else width
            h_px = height / mm_px if mm_px > 0 else height
            w.canvas._elec_point_size_px[pid] = (w_px, h_px)
            w.canvas._elec_visible[pid] = True
            w.canvas._elec_point_position[pid] = position
            w.canvas._elec_point_height[pid] = height_from_floor
            w.canvas._elec_point_notes[pid] = note
            w.canvas._elec_point_smarthome_device[pid] = (
                smarthome_device)
            w.canvas._elec_point_smarthome_device_color[pid] = (
                smarthome_device_color)
            w.canvas.set_color(pid, QC(color))
            w.canvas._ensure_color(pid)

            # Parameter setzen
            panel.from_dict({
                "point_id": pid,
                "name": name,
                "color": color,
                "width": width,
                "height": height,
                "builtin_symbol": builtin_symbol,
                "icon_path": "",
                "position": position,
                "height_from_floor": height_from_floor,
                "smarthome_device": smarthome_device,
                "smarthome_device_color": smarthome_device_color,
                "note": note,
            })

            # Builtin-Symbol laden
            if builtin_symbol:
                from gui.parameter_panel import BUILTIN_SYMBOLS
                icon_path = BUILTIN_SYMBOLS.get(builtin_symbol, "")
                if icon_path:
                    w.canvas.set_elec_point_icon(pid, icon_path)

            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {
                "point_id": pid,
                "name": name,
                "position_px": [x, y],
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_elec_point(
        point_id: str,
        name: str | None = None,
        x: float | None = None,
        y: float | None = None,
        builtin_symbol: str | None = None,
        color: str | None = None,
        position: str | None = None,
        height_from_floor: float | None = None,
        smarthome_device: str | None = None,
        note: str | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Parameter eines Elektro-Anschlusspunkts ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            point_id: ID des Punkts (z.B. 'AP-1')
            name: Neuer Anzeigename
            x: Neue X-Position (Canvas-Pixel)
            y: Neue Y-Position (Canvas-Pixel)
            builtin_symbol: Neues Builtin-Symbol
            color: Neue Farbe (#rrggbb)
            position: Neuer Einbauort
            height_from_floor: Neue Einbauhöhe in cm
            smarthome_device: Neues Smarthome-Gerät
            note: Neue Notiz
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[point_id] = name
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas.set_color(point_id, QC(color))
            if x is not None or y is not None:
                old = window.canvas._elec_points.get(point_id)
                nx = x if x is not None else (
                    old.x() if old else 0)
                ny = y if y is not None else (
                    old.y() if old else 0)
                window.canvas._elec_points[point_id] = QPointF(nx, ny)
            if builtin_symbol is not None:
                idx = panel.cmb_symbol.findText(builtin_symbol)
                if idx >= 0:
                    panel.cmb_symbol.setCurrentIndex(idx)
            if position is not None:
                idx = panel.cmb_position.findText(position)
                if idx >= 0:
                    panel.cmb_position.setCurrentIndex(idx)
                window.canvas._elec_point_position[point_id] = position
            if height_from_floor is not None:
                panel.sb_height_from_floor.setValue(height_from_floor)
                window.canvas._elec_point_height[point_id] = (
                    height_from_floor)
            if smarthome_device is not None:
                panel.set_smarthome_device_text(smarthome_device)
                window.canvas._elec_point_smarthome_device[point_id] = (
                    smarthome_device)
            if note is not None:
                panel.te_note.setPlainText(note)
                window.canvas._elec_point_notes[point_id] = note
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._elec_visible[point_id] = visible

            window.canvas.update()
            window._dirty = True
            window._update_title()

            return {
                "point_id": point_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_elec_point(point_id: str) -> dict:
        """Elektro-Anschlusspunkt löschen.

        Args:
            point_id: ID des zu löschenden Punkts (z.B. 'AP-1')
        """
        def _delete():
            if point_id not in window.param_panel.elec_point_panels:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}
            window._delete_elec_point(point_id)
            window._dirty = True
            window._update_title()
            return {"point_id": point_id, "status": "deleted"}

        return invoke(_delete)

    # ── HKV-Tools ──────────────────────────────────────────────────

    @mcp.tool()
    def add_hkv(
        name: str,
        x: float,
        y: float,
        floor_plan_id: str = "",
        color: str = "#e53935",
        width: float = 50.0,
        height: float = 50.0,
    ) -> dict:
        """Heizkreisverteiler (HKV) platzieren.

        Args:
            name: Anzeigename (z.B. 'Verteiler EG')
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
            floor_plan_id: Grundriss-ID
            color: Hex-Farbe (#rrggbb)
            width: Breite in mm (Standard: 50)
            height: Höhe in mm (Standard: 50)
        """
        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._hkv_counter += 1
            hid = f"HKV-{w._hkv_counter}"

            panel = w._create_hkv_panel(
                hid, fp_id=floor_plan_id or None, name=name)
            w.param_panel.update_all_hkv_choices()

            # Position setzen
            w.canvas._hkv_points[hid] = QPointF(x, y)
            mm_px = w.canvas.get_mm_per_px()
            w_px = width / mm_px if mm_px > 0 else width
            h_px = height / mm_px if mm_px > 0 else height
            w.canvas._hkv_size_px[hid] = (w_px, h_px)
            w.canvas._hkv_visible[hid] = True
            w.canvas.set_color(hid, QC(color))
            w.canvas._ensure_color(hid)

            panel.from_dict({
                "hkv_id": hid,
                "name": name,
                "color": color,
                "width": width,
                "height": height,
            })

            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {"hkv_id": hid, "name": name, "status": "created"}

        return invoke(_add)

    @mcp.tool()
    def delete_hkv(hkv_id: str) -> dict:
        """Heizkreisverteiler löschen.

        Args:
            hkv_id: ID des zu löschenden HKV (z.B. 'HKV-1')
        """
        def _delete():
            if hkv_id not in window.param_panel.hkv_panels:
                return {"error": f"HKV '{hkv_id}' nicht gefunden."}
            window._delete_hkv(hkv_id)
            window._dirty = True
            window._update_title()
            return {"hkv_id": hkv_id, "status": "deleted"}

        return invoke(_delete)

    # ── Berechnungs-Tools ──────────────────────────────────────────

    @mcp.tool()
    def calculate_heating(circuit_id: str) -> dict:
        """Heizlastberechnung für einen Heizkreis nach DIN EN 1264.

        Berechnet spezifische Heizleistung, Gesamtleistung, Volumenstrom
        und Druckverlust basierend auf den aktuellen Parametern.

        Args:
            circuit_id: ID des Heizkreises (z.B. 'HK-1')

        Returns:
            Dict mit: power_w, q_wm2, volume_flow_lmin,
            pressure_drop_mbar, area_m2, pipe_length_m, und
            allen Eingangsparametern.
        """
        def _calc():
            from logic.heating_calc import calc_circuit, FLOOR_COVERINGS

            panel = window.param_panel.circuit_panels.get(circuit_id)
            if not panel:
                return {
                    "error": f"Heizkreis '{circuit_id}' nicht gefunden."}

            params = panel.get_parameters()
            heat = window.param_panel.get_heating_params()
            scale = window.canvas.get_mm_per_px()

            # Fläche
            area_mm2 = window._compute_polygon_area_mm2(circuit_id)
            area_m2 = (area_mm2 or 0.0) / 1_000_000.0

            # Rohrlängen
            route_m = (
                window.canvas.get_manual_route_length_px(circuit_id)
                * scale / 1000.0)
            supply_m = (
                window.canvas.get_supply_line_length_px(circuit_id)
                * scale / 1000.0)
            total_m = route_m + supply_m

            spacing_cm = params["spacing"] / 10.0
            floor_name = params.get(
                "floor_covering", "Fliesen / Keramik")
            r_lambda_b = FLOOR_COVERINGS.get(floor_name, 0.01)
            room_temp = params.get("room_temp", 20.0)
            diameter_mm = params.get("diameter", 16.0)

            hc = calc_circuit(
                t_supply=heat["t_supply"],
                t_return=heat["t_return"],
                t_room=room_temp,
                spacing_cm=spacing_cm,
                r_lambda_b=r_lambda_b,
                area_m2=area_m2,
                pipe_length_m=route_m,
                outer_diameter_mm=diameter_mm,
                total_pipe_length_m=total_m,
            )

            return {
                "circuit_id": circuit_id,
                "name": params.get("name", circuit_id),
                "power_w": round(hc["power_w"], 1),
                "q_wm2": round(hc["q_wm2"], 1),
                "volume_flow_lmin": round(hc["volume_flow_lmin"], 3),
                "pressure_drop_mbar": round(
                    hc["pressure_drop_mbar"], 1),
                "area_m2": round(area_m2, 2),
                "pipe_length_m": round(route_m, 2),
                "supply_length_m": round(supply_m, 2),
                "total_pipe_length_m": round(total_m, 2),
                "t_supply": heat["t_supply"],
                "t_return": heat["t_return"],
                "room_temp": room_temp,
                "spacing_mm": params["spacing"],
                "diameter_mm": diameter_mm,
                "floor_covering": floor_name,
            }

        return invoke(_calc)

    @mcp.tool()
    def calculate_all_circuits() -> list[dict]:
        """Heizlastberechnung für ALLE Heizkreise.

        Returns:
            Liste von Berechnungsergebnissen (wie calculate_heating).
        """
        ids = invoke(
            lambda: list(window.param_panel.circuit_panels.keys()))
        return [calculate_heating(cid) for cid in ids]

    # ── Globale Parameter ──────────────────────────────────────────

    @mcp.tool()
    def set_heating_params(
        t_supply: float | None = None,
        t_return: float | None = None,
        t_norm_outdoor: float | None = None,
    ) -> dict:
        """Globale Heizungsparameter ändern.

        Args:
            t_supply: Vorlauftemperatur in °C (20–90)
            t_return: Rücklauftemperatur in °C (15–80)
            t_norm_outdoor: Normaußentemperatur in °C (-30 bis 5)
        """
        def _set():
            if t_supply is not None:
                window.param_panel.sb_vorlauf.setValue(t_supply)
            if t_return is not None:
                window.param_panel.sb_ruecklauf.setValue(t_return)
            if t_norm_outdoor is not None:
                window.param_panel.sb_norm_aussen.setValue(t_norm_outdoor)

            window._recalc_all_circuits()
            window._dirty = True
            window._update_title()

            return {
                "t_supply": window.param_panel.sb_vorlauf.value(),
                "t_return": window.param_panel.sb_ruecklauf.value(),
                "t_norm_outdoor":
                    window.param_panel.sb_norm_aussen.value(),
                "status": "updated",
            }

        return invoke(_set)

    # ── Projekt-Tools ──────────────────────────────────────────────

    @mcp.tool()
    def save_project(path: str = "") -> dict:
        """Aktuelles Projekt speichern.

        Args:
            path: Dateipfad (.hrp). Leer = aktueller Projektpfad.
        """
        def _save():
            save_path = Path(path) if path else window._project_path
            if not save_path:
                return {
                    "error": "Kein Projektpfad. "
                             "Bitte 'path' angeben."}
            if save_path.suffix.lower() not in (".hrp", ".json"):
                save_path = save_path.with_suffix(".hrp")
            window._write_project(save_path)
            window._project_path = save_path
            return {"path": str(save_path), "status": "saved"}

        return invoke(_save)

    @mcp.tool()
    def validate_project() -> dict:
        """Aktuelles Projekt gegen das HRP-Schema validieren.

        Prüft Schema-Konformität und semantische Regeln.

        Returns:
            Dict mit valid (bool), errors (list), warnings (list).
        """
        project_data = invoke(lambda: {
            "svg_path": getattr(window, "_svg_path", ""),
            "canvas": window.canvas.to_dict(),
            "params": window.param_panel.to_dict(),
            "pdf_export_pages": getattr(
                window, "_pdf_export_pages", []),
        })

        # Validierung ist thread-safe (kein Qt)
        errors: list[str] = []
        warnings: list[str] = []

        try:
            import sys as _sys
            base = Path(getattr(_sys, '_MEIPASS', Path(__file__).parent))
            schema_path = base / "hrp_schema.json"
            if schema_path.exists():
                from validate_hrp import (
                    validate_schema, validate_semantic, _load_schema)
                schema = _load_schema(schema_path)
                errors.extend(validate_schema(project_data, schema))

            from validate_hrp import validate_semantic
            sem_errors, sem_warnings = validate_semantic(project_data)
            errors.extend(sem_errors)
            warnings.extend(sem_warnings)
        except Exception as e:
            errors.append(f"Validierungsfehler: {e}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    return mcp


# ── Server starten ─────────────────────────────────────────────────


def start_mcp_server(
    window: MainWindow,
    port: int = MCP_PORT,
) -> threading.Thread | None:
    """Startet den MCP-Server in einem Hintergrund-Thread.

    Args:
        window: Das MainWindow der laufenden HRouting-Anwendung.
        port: Port für den HTTP-Server (Standard: 3274).

    Returns:
        Den gestarteten Thread oder None bei Fehler.
    """
    if not is_available():
        logger.info(
            "MCP-Pakete nicht installiert "
            "(pip install \"mcp[cli]\" uvicorn). "
            "MCP-Server wird nicht gestartet.")
        return None

    try:
        bridge = _create_bridge(window)
        mcp = _create_mcp(window, bridge)
        mcp.settings.port = port
    except Exception as e:
        logger.error(f"MCP-Server konnte nicht erstellt werden: {e}")
        return None

    def _run():
        try:
            logger.info(
                f"MCP-Server startet auf "
                f"http://{MCP_HOST}:{port}/mcp")
            mcp.run(transport="streamable-http")
        except OSError as e:
            msg = str(e).lower()
            if "address already in use" in msg or "10048" in str(e):
                logger.warning(
                    f"MCP-Port {port} bereits belegt. "
                    "MCP-Server wird nicht gestartet.")
            else:
                logger.error(f"MCP-Server-Fehler: {e}")
        except Exception as e:
            logger.error(f"MCP-Server-Fehler: {e}")

    thread = threading.Thread(
        target=_run, daemon=True, name="mcp-server")
    thread.start()
    return thread
