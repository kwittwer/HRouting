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
import sys as _sys
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
    except ImportError as e:
        logger.info(f"MCP-Import fehlgeschlagen: {e}")
        return False
    except Exception as e:
        logger.warning(f"MCP-Verfügbarkeitscheck Fehler: {e}")
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
            "Nutze get_project_summary() als Einstieg. Koordinaten sind in "
            "Canvas-Pixeln. Einheiten in params: mm (diameter, spacing, "
            "wall_dist, width, height). Nutze get_schema() für das "
            "Dateiformat. Verfügbare Tools: Heizkreise (add/modify/delete/"
            "list_circuits, set_circuit_polygon, set_circuit_route, "
            "set_supply_line), Elektropunkte (add/modify/delete/"
            "list_elec_points, configure_uv_distribution, "
            "get_uv_config, set_uv_slot, delete_uv_slot, "
            "clear_uv_distribution, configure_up_distribution, "
            "clear_up_distribution), Elektro-Räume (add/modify/delete/"
            "list_elec_rooms), Elektro-Kabel (add/modify/delete/"
            "list_elec_cables), HKV (add/modify/delete/list_hkvs), "
            "HKV-Leitungen (add/modify/delete/list_hkv_lines), "
            "Texte (add/modify/delete/list_texts), Grundrisse "
            "(add/modify/delete/list_floor_plans), Berechnungen "
            "(calculate_heating, calculate_all_circuits, set_heating_params),"
            " Projekt (save_project, validate_project, get_project_json)."
        ),
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
        json_response=True,
    )

    invoke = bridge.invoke

    # ── Tool-Logging: Jeden Tool-Aufruf ins Log-Fenster schreiben ──
    import functools as _functools
    import inspect as _inspect

    _orig_mcp_tool = mcp.tool

    def _logged_tool(*tool_args, **tool_kw):
        """Wrapper um mcp.tool() – loggt jeden Aufruf mit Parametern
        und Ergebnis ins MCP-Log-Fenster."""
        # @mcp.tool  (ohne Klammern – fn direkt übergeben)
        if tool_args and callable(tool_args[0]):
            fn = tool_args[0]
            return _logged_tool()(fn)

        # @mcp.tool() oder @mcp.tool(**kw)
        orig_decorator = _orig_mcp_tool(*tool_args, **tool_kw)

        def decorator(fn):
            @_functools.wraps(fn)
            def wrapper(*args, **kwargs):
                # ── Parameter-Zusammenfassung erstellen ──
                try:
                    sig = _inspect.signature(fn)
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    parts = []
                    for k, v in bound.arguments.items():
                        if isinstance(v, list) and len(v) > 3:
                            parts.append(f"{k}=[…{len(v)} Punkte]")
                        else:
                            s = repr(v)
                            if len(s) > 100:
                                s = s[:97] + "…"
                            parts.append(f"{k}={s}")
                    params_str = ", ".join(parts)
                except Exception:
                    params_str = "…"

                logger.info(f"⚙ {fn.__name__}({params_str})")

                # ── Tool ausführen ──
                try:
                    result = fn(*args, **kwargs)

                    # ── Ergebnis-Zusammenfassung loggen ──
                    if isinstance(result, dict):
                        if "error" in result:
                            logger.warning(
                                f"  ✗ {result['error']}")
                        else:
                            status = result.get("status", "ok")
                            # Relevante IDs/Namen extrahieren
                            ids = {
                                k: result[k] for k in result
                                if k.endswith("_id") or k == "path"
                                or k == "name"
                            }
                            summary = [f"status={status}"]
                            summary += [
                                f"{k}={v}" for k, v in ids.items()]
                            logger.info(
                                f"  ✓ {', '.join(summary)}")
                    elif isinstance(result, list):
                        logger.info(
                            f"  ✓ {len(result)} Einträge")
                    elif isinstance(result, str):
                        logger.info(
                            f"  ✓ {len(result)} Zeichen")
                    else:
                        logger.info(f"  ✓ ok")

                    return result

                except Exception as e:
                    logger.error(f"  ✗ Exception: {e}")
                    raise

            return orig_decorator(wrapper)
        return decorator

    mcp.tool = _logged_tool

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

    @mcp.resource("hrp://uv_device_types")
    def get_uv_device_types_resource() -> str:
        """Gültige Gerätetypen für UV-Slots (device_type-Werte).

        Verwende diese Werte in configure_uv_distribution und set_uv_slot.
        Typische TE-Breiten: LS=1, FI=2, FI/LS=1-2, Hauptschalter=3,
        Überspannungsschutz=2, Schütz=2-3, Zeitschalter=2.
        """
        import json
        return json.dumps([
            "",
            "Reserve",
            "Hauptschalter",
            "LS",
            "LS 3-polig",
            "FI",
            "FI 4-polig",
            "FI/LS",
            "Überspannungsschutz",
            "Motorschutz",
            "Schütz",
            "Zeitschalter",
            "Klemme",
            "Steckdose UV",
            "Freitext",
        ], ensure_ascii=False)

    @mcp.resource("hrp://builtin_symbols")
    def get_builtin_symbols_resource() -> str:
        """Verfügbare Builtin-Symbole für Elektro-Anschlusspunkte.

        Gibt alle gültigen Werte für den builtin_symbol-Parameter in
        add_elec_point / modify_elec_point zurück.  Die tatsächlich
        verfügbaren Symbole hängen von den installierten Icon-Dateien ab.
        Typische Werte: 'Steckdose', 'Steckdose 2fach', 'Licht',
        'Lichtquelle', 'Taster', 'Rauchmelder', 'Thermostat', 'LAN', 'TV',
        'Heizkreisverteiler', 'Bewegungsmelder', 'Gong', ...
        """
        import json
        from gui.parameter_panel import BUILTIN_SYMBOLS
        labels = [lbl for lbl in BUILTIN_SYMBOLS.keys() if lbl != "(kein Symbol)"]
        return json.dumps(sorted(labels), ensure_ascii=False)

    @mcp.resource("hrp://ap_types")
    def get_ap_types_resource() -> str:
        """Gültige Werte für ap_type in add_elec_point / modify_elec_point.

        standard       – normaler Anschlusspunkt (Steckdose, Licht, etc.)
        uv             – Unterverteilung (aktiviert uv_config / UV-Planung)
        up_distribution – Verteilung in Unterputzdose (aktiviert
                          up_distribution_config / Aderzuordnung)
        """
        import json
        return json.dumps([
            {"value": "standard",         "label": "Normaler Anschlusspunkt"},
            {"value": "uv",               "label": "Unterverteilung (UV)"},
            {"value": "up_distribution",   "label": "Verteilung in Unterputzdose"},
        ], ensure_ascii=False)

    def _normalize_ap_type(ap_type: str | None) -> str:
        value = str(ap_type or "standard").strip().lower()
        if value == "uv":
            return "uv"
        if value in {"up_distribution", "up", "unterputzdose", "verteilung_in_unterputzdose"}:
            return "up_distribution"
        return "standard"

    def _resolve_ap_type_for_configs(
        ap_type: str | None,
        uv_config: dict,
        up_distribution_config: dict,
        *,
        current_ap_type: str = "standard",
    ) -> tuple[str, str | None]:
        effective_ap_type = (
            _normalize_ap_type(ap_type)
            if ap_type is not None else _normalize_ap_type(current_ap_type)
        )
        if uv_config and up_distribution_config:
            return effective_ap_type, (
                "uv_config und up_distribution_config können nicht gleichzeitig gesetzt sein."
            )
        if uv_config and ap_type is None:
            return "uv", None
        if uv_config and effective_ap_type != "uv":
            return effective_ap_type, (
                "uv_config erfordert ap_type='uv' "
                "oder keinen expliziten ap_type-Wert."
            )
        if up_distribution_config and ap_type is None:
            return "up_distribution", None
        if up_distribution_config and effective_ap_type != "up_distribution":
            return effective_ap_type, (
                "up_distribution_config erfordert ap_type='up_distribution' "
                "oder keinen expliziten ap_type-Wert."
            )
        return effective_ap_type, None

    def _normalize_uv_config(
        uv_config: dict | None,
        *,
        require_layout: bool = False,
    ) -> dict:
        if uv_config is None:
            return {}
        if not isinstance(uv_config, dict):
            raise ValueError("uv_config muss ein Objekt sein.")
        if not uv_config:
            return {}

        preset = str(
            uv_config.get("preset", "Benutzerdefiniert")
            or "Benutzerdefiniert"
        ).strip() or "Benutzerdefiniert"

        try:
            rows = int(uv_config.get("rows", 0) or 0)
            modules_per_row = int(uv_config.get("modules_per_row", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "uv_config.rows und uv_config.modules_per_row "
                "müssen Ganzzahlen sein."
            ) from exc

        if rows < 0 or modules_per_row < 0:
            raise ValueError(
                "uv_config.rows und uv_config.modules_per_row "
                "dürfen nicht negativ sein."
            )
        if bool(rows) != bool(modules_per_row):
            raise ValueError(
                "uv_config.rows und uv_config.modules_per_row "
                "müssen gemeinsam gesetzt werden."
            )
        if require_layout and (rows < 1 or modules_per_row < 1):
            raise ValueError(
                "Für eine UV-Konfiguration müssen rows und "
                "modules_per_row >= 1 sein."
            )

        slots_raw = uv_config.get("slots", [])
        if slots_raw is None:
            slots_raw = []
        if not isinstance(slots_raw, list):
            raise ValueError("uv_config.slots muss ein Array sein.")

        seen_slots: set[tuple[int, int]] = set()
        slots: list[dict] = []
        for index, slot in enumerate(slots_raw):
            if not isinstance(slot, dict):
                raise ValueError(
                    f"uv_config.slots[{index}] muss ein Objekt sein."
                )
            try:
                row_no = int(slot.get("row", 0) or 0)
                slot_no = int(slot.get("slot", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"uv_config.slots[{index}] benötigt gültige "
                    "row/slot-Werte."
                ) from exc
            if row_no < 1 or slot_no < 1:
                raise ValueError(
                    f"uv_config.slots[{index}] muss row/slot >= 1 haben."
                )
            if rows and row_no > rows:
                raise ValueError(
                    f"uv_config.slots[{index}].row = {row_no} liegt "
                    f"außerhalb der UV-Reihen ({rows})."
                )
            if modules_per_row and slot_no > modules_per_row:
                raise ValueError(
                    f"uv_config.slots[{index}].slot = {slot_no} liegt "
                    f"außerhalb der TE-Anzahl ({modules_per_row})."
                )
            key = (row_no, slot_no)
            if key in seen_slots:
                raise ValueError(
                    f"uv_config enthält eine doppelte Belegung für "
                    f"Reihe {row_no}, TE {slot_no}."
                )
            seen_slots.add(key)
            try:
                te_size = max(1, int(slot.get("te_size", 1) or 1))
            except (TypeError, ValueError):
                te_size = 1
            slots.append({
                "row": row_no,
                "slot": slot_no,
                "device_type": str(slot.get("device_type", "") or "").strip(),
                "te_size": te_size,
                "spec": str(slot.get("spec", "") or "").strip(),
                "label": str(slot.get("label", "") or "").strip(),
                "assignment": str(slot.get("assignment", "") or "").strip(),
                "note": str(slot.get("note", "") or "").strip(),
            })

        slots.sort(key=lambda entry: (entry["row"], entry["slot"]))

        # Normalize busbars
        busbars_raw = uv_config.get("busbars", [])
        if busbars_raw is None:
            busbars_raw = []
        if not isinstance(busbars_raw, list):
            raise ValueError("uv_config.busbars muss ein Array sein.")
        busbars: list[dict] = []
        for bidx, bb in enumerate(busbars_raw):
            if not isinstance(bb, dict):
                raise ValueError(f"uv_config.busbars[{bidx}] muss ein Objekt sein.")
            phase = str(bb.get("phase", "") or "").strip()
            if not phase:
                raise ValueError(f"uv_config.busbars[{bidx}].phase darf nicht leer sein.")
            color = str(bb.get("color", "#888888") or "#888888").strip()

            # Support te_ranges: [[start, end], ...] for non-contiguous ranges
            te_ranges = bb.get("te_ranges")
            if te_ranges is not None:
                if not isinstance(te_ranges, list) or not te_ranges:
                    raise ValueError(
                        f"uv_config.busbars[{bidx}].te_ranges muss ein nicht-leeres Array von [start, end]-Paaren sein."
                    )
                for ridx, rng in enumerate(te_ranges):
                    if (not isinstance(rng, (list, tuple)) or len(rng) != 2):
                        raise ValueError(
                            f"uv_config.busbars[{bidx}].te_ranges[{ridx}] muss ein [start, end]-Paar sein."
                        )
                    try:
                        r_start = int(rng[0])
                        r_end = int(rng[1])
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"uv_config.busbars[{bidx}].te_ranges[{ridx}] enthält ungültige Werte."
                        ) from exc
                    if r_start < 1 or r_end < 1:
                        raise ValueError(
                            f"uv_config.busbars[{bidx}].te_ranges[{ridx}]: Werte müssen >= 1 sein."
                        )
                    if r_start > r_end:
                        raise ValueError(
                            f"uv_config.busbars[{bidx}].te_ranges[{ridx}]: start ({r_start}) muss <= end ({r_end}) sein."
                        )
                    busbars.append({
                        "phase": phase,
                        "color": color,
                        "te_start": r_start,
                        "te_end": r_end,
                    })
            else:
                # Classic single-range format
                try:
                    te_start = int(bb.get("te_start", 1) or 1)
                    te_end = int(bb.get("te_end", 1) or 1)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"uv_config.busbars[{bidx}] benötigt gültige te_start/te_end-Werte oder te_ranges."
                    ) from exc
                if te_start < 1 or te_end < 1:
                    raise ValueError(f"uv_config.busbars[{bidx}]: te_start und te_end müssen >= 1 sein.")
                if te_start > te_end:
                    raise ValueError(
                        f"uv_config.busbars[{bidx}]: te_start ({te_start}) muss <= te_end ({te_end}) sein."
                    )
                busbars.append({
                    "phase": phase,
                    "color": color,
                    "te_start": te_start,
                    "te_end": te_end,
                })

        return {
            "preset": preset,
            "rows": rows,
            "modules_per_row": modules_per_row,
            "slots": slots,
            "busbars": busbars,
        }

    def _normalize_up_distribution_config(
        up_distribution_config: dict | None,
        *,
        require_incoming: bool = False,
    ) -> dict:
        if up_distribution_config is None:
            return {}
        if not isinstance(up_distribution_config, dict):
            raise ValueError("up_distribution_config muss ein Objekt sein.")
        if not up_distribution_config:
            return {}

        incoming_cable_id = str(
            up_distribution_config.get("incoming_cable_id", "") or ""
        ).strip()
        note = str(up_distribution_config.get("note", "") or "").strip()

        outgoing_raw = up_distribution_config.get("outgoing_cable_ids", [])
        if outgoing_raw is None:
            outgoing_raw = []
        if not isinstance(outgoing_raw, list):
            raise ValueError("up_distribution_config.outgoing_cable_ids muss ein Array sein.")
        outgoing_cable_ids: list[str] = []
        for index, cable_id in enumerate(outgoing_raw):
            text = str(cable_id or "").strip()
            if not text:
                raise ValueError(
                    f"up_distribution_config.outgoing_cable_ids[{index}] darf nicht leer sein."
                )
            if text not in outgoing_cable_ids:
                outgoing_cable_ids.append(text)

        mappings_raw = up_distribution_config.get("mappings", [])
        if mappings_raw is None:
            mappings_raw = []
        if not isinstance(mappings_raw, list):
            raise ValueError("up_distribution_config.mappings muss ein Array sein.")

        mappings: list[dict] = []
        seen_mappings: set[tuple[str, str, str]] = set()
        for index, mapping in enumerate(mappings_raw):
            if not isinstance(mapping, dict):
                raise ValueError(
                    f"up_distribution_config.mappings[{index}] muss ein Objekt sein."
                )
            from_conductor = str(mapping.get("from_conductor", "") or "").strip()
            to_cable_id = str(mapping.get("to_cable_id", "") or "").strip()
            to_conductor = str(mapping.get("to_conductor", "") or "").strip()
            note_text = str(mapping.get("note", "") or "").strip()
            if not from_conductor or not to_cable_id or not to_conductor:
                raise ValueError(
                    "up_distribution_config.mappings benötigt from_conductor, "
                    "to_cable_id und to_conductor."
                )
            if to_cable_id not in outgoing_cable_ids:
                outgoing_cable_ids.append(to_cable_id)
            key = (from_conductor, to_cable_id, to_conductor)
            if key in seen_mappings:
                raise ValueError(
                    f"up_distribution_config enthält eine doppelte Zuordnung: "
                    f"{from_conductor} -> {to_cable_id}:{to_conductor}."
                )
            seen_mappings.add(key)
            mappings.append({
                "from_conductor": from_conductor,
                "to_cable_id": to_cable_id,
                "to_conductor": to_conductor,
                "note": note_text,
            })

        if require_incoming and not incoming_cable_id:
            raise ValueError(
                "Für eine UP-Verteilung muss incoming_cable_id gesetzt sein."
            )
        if incoming_cable_id and incoming_cable_id in outgoing_cable_ids:
            raise ValueError(
                "incoming_cable_id darf nicht in outgoing_cable_ids enthalten sein."
            )

        return {
            "incoming_cable_id": incoming_cable_id,
            "outgoing_cable_ids": outgoing_cable_ids,
            "mappings": mappings,
            "note": note,
        }

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
        ap_type: str | None = None,
        uv_config: dict | None = None,
        up_distribution_config: dict | None = None,
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
            ap_type: AP-Typ – 'standard', 'uv' oder 'up_distribution'
            uv_config: Optionale UV-Belegung als Objekt mit preset, rows,
                modules_per_row und slots[]
            up_distribution_config: Optionale Zuordnung für Verteilung in
                Unterputzdose mit incoming_cable_id, outgoing_cable_ids,
                mappings[] und note
        """
        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._elec_point_counter += 1
            pid = f"AP-{w._elec_point_counter}"
            normalized_uv_config = _normalize_uv_config(uv_config)
            normalized_up_distribution_config = _normalize_up_distribution_config(
                up_distribution_config
            )
            effective_ap_type, ap_type_error = _resolve_ap_type_for_configs(
                ap_type,
                normalized_uv_config,
                normalized_up_distribution_config,
            )
            if ap_type_error:
                return {
                    "error": ap_type_error
                }

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
                "ap_type": effective_ap_type,
                "uv_config": normalized_uv_config,
                "up_distribution_config": normalized_up_distribution_config,
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
        smarthome_device_color: str | None = None,
        note: str | None = None,
        ap_type: str | None = None,
        uv_config: dict | None = None,
        up_distribution_config: dict | None = None,
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
            smarthome_device_color: Neue Gerätefarbe
            note: Neue Notiz
            ap_type: Neuer AP-Typ – 'standard', 'uv' oder 'up_distribution'
            uv_config: Neue UV-Belegung als vollständiges Objekt;
                übergib {} zum Leeren; bei gesetzter UV-Belegung muss
                ap_type 'uv' sein oder leer bleiben
            up_distribution_config: Neue Verteilung in Unterputzdose als
                vollständiges Objekt; übergib {} zum Leeren; bei gesetzter
                Konfiguration muss ap_type 'up_distribution' sein oder leer
                bleiben
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
            if smarthome_device_color is not None:
                panel.set_smarthome_device_color_text(smarthome_device_color)
                window.canvas._elec_point_smarthome_device_color[point_id] = (
                    smarthome_device_color)
            if note is not None:
                panel.te_note.setPlainText(note)
                window.canvas._elec_point_notes[point_id] = note
            normalized_uv_config = None
            normalized_up_distribution_config = None
            if uv_config is not None:
                normalized_uv_config = _normalize_uv_config(uv_config)
            if up_distribution_config is not None:
                normalized_up_distribution_config = _normalize_up_distribution_config(
                    up_distribution_config
                )
            if normalized_uv_config is not None or normalized_up_distribution_config is not None:
                _, ap_type_error = _resolve_ap_type_for_configs(
                    ap_type,
                    normalized_uv_config or {},
                    normalized_up_distribution_config or {},
                    current_ap_type=panel.get_ap_type(),
                )
                if ap_type_error:
                    return {
                        "error": ap_type_error
                    }
            if ap_type is not None:
                panel.set_ap_type(ap_type)
            if normalized_uv_config is not None:
                panel.set_uv_config(normalized_uv_config)
                if normalized_uv_config and ap_type is None:
                    panel.set_ap_type("uv")
            if normalized_up_distribution_config is not None:
                panel.set_up_distribution_config(normalized_up_distribution_config)
                if normalized_up_distribution_config and ap_type is None:
                    panel.set_ap_type("up_distribution")
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

    @mcp.tool()
    def configure_uv_distribution(
        point_id: str,
        rows: int,
        modules_per_row: int,
        slots: list[dict] | None = None,
        preset: str = "Benutzerdefiniert",
        busbars: list[dict] | None = None,
    ) -> dict:
        """UV-/Verteilungsbelegung eines Anschlusspunkts setzen.

        Args:
            point_id: ID des Anschlusspunkts (z.B. 'AP-1')
            rows: Anzahl Reihen in der UV
            modules_per_row: Teilungseinheiten pro Reihe
            slots: Liste der Belegungen mit row, slot, device_type,
                label, assignment und note
            preset: Preset-Name (z.B. '2-reihig / 12 TE')
            busbars: Phasenschienen-Konfiguration, z.B.
                [{"phase": "L1", "color": "#e53935", "te_start": 1, "te_end": 6}]
                Für nicht-zusammenhängende Bereiche te_ranges verwenden:
                [{"phase": "L1", "te_ranges": [[15,16],[28,28]]}]
                Mehrere Einträge mit derselben Phase sind erlaubt.
        """
        def _configure():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}

            normalized_uv_config = _normalize_uv_config({
                "preset": preset,
                "rows": rows,
                "modules_per_row": modules_per_row,
                "slots": slots or [],
                "busbars": busbars or [],
            }, require_layout=True)
            panel.set_ap_type("uv")
            panel.set_uv_config(normalized_uv_config)

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "configured",
                "uv_config": normalized_uv_config,
            }

        return invoke(_configure)

    @mcp.tool()
    def clear_uv_distribution(
        point_id: str,
        reset_ap_type: bool = False,
    ) -> dict:
        """UV-/Verteilungsbelegung eines Anschlusspunkts leeren.

        Args:
            point_id: ID des Anschlusspunkts (z.B. 'AP-1')
            reset_ap_type: Setzt den AP-Typ zusätzlich auf 'standard'
        """
        def _clear():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}

            panel.set_uv_config({})
            if reset_ap_type:
                panel.set_ap_type("standard")

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "cleared",
                "ap_type": panel.get_ap_type(),
                "uv_config": {},
            }

        return invoke(_clear)

    @mcp.tool()
    def get_uv_config(
        point_id: str,
    ) -> dict:
        """Aktuelle UV-Belegung eines Anschlusspunkts lesen.

        Gibt rows, modules_per_row, preset und die vollständige
        Slots-Liste (row, slot, device_type, te_size, label,
        assignment, note) zurück. Nutze dies vor set_uv_slot um
        den aktuellen Stand zu prüfen.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
        """
        def _get():
            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id)
            if ep is None:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if ep.get("ap_type") != "uv":
                return {
                    "error": (
                        f"AP '{point_id}' ist kein UV-Punkt "
                        f"(ap_type='{ep.get('ap_type', 'standard')}')."
                    )
                }
            uv = ep.get("uv_config") or {}
            rows = uv.get("rows", 0)
            mpr = uv.get("modules_per_row", 0)
            return {
                "point_id": point_id,
                "rows": rows,
                "modules_per_row": mpr,
                "preset": uv.get("preset", ""),
                "total_te": rows * mpr,
                "occupied_count": len(uv.get("slots", [])),
                "slots": uv.get("slots", []),
                "busbars": uv.get("busbars", []),
            }
        return invoke(_get)

    @mcp.tool()
    def set_uv_slot(
        point_id: str,
        row: int,
        slot: int,
        device_type: str,
        label: str = "",
        assignment: str = "",
        note: str = "",
        te_size: int = 1,
        spec: str = "",
    ) -> dict:
        """Einzelnen TE-Slot in einer UV setzen oder überschreiben.

        Ändert genau einen Slot ohne die restlichen Belegungen zu berühren.
        Wird device_type leer übergeben, wird der Slot gelöscht (= Reserve).

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            row: Reihe (1-basiert)
            slot: TE-Position innerhalb der Reihe (1-basiert)
            device_type: Gerätetyp aus hrp://uv_device_types.
                Leer = Slot löschen.
            label: Bezeichnung (z.B. 'Küche', 'Licht OG')
            assignment: Kabel-/Stromkreis-Zuordnung (z.B. 'KV-1')
            note: Freitext-Notiz
            te_size: Anzahl belegter TE (Standard 1; FI=2, HS=3)
            spec: Typ-Kennzeichnung des Geräts, z.B. 'B16', 'Typ A 30mA', '63A'
        """
        def _set():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}

            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            if ep.get("ap_type") != "uv":
                return {
                    "error": (
                        f"AP '{point_id}' ist kein UV-Punkt "
                        f"(ap_type='{ep.get('ap_type', 'standard')}')."
                        " Setze ap_type='uv' via add_elec_point / modify_elec_point"
                        " und lege das Layout mit configure_uv_distribution fest."
                    )
                }

            current_uv = dict(ep.get("uv_config") or {})
            rows = current_uv.get("rows", 0)
            mpr = current_uv.get("modules_per_row", 0)

            if rows < 1 or mpr < 1:
                return {
                    "error": (
                        "UV hat kein gültiges Layout (rows/modules_per_row fehlt). "
                        "Lege zuerst das Layout mit configure_uv_distribution fest."
                    )
                }
            if not (1 <= row <= rows):
                return {"error": f"row={row} liegt außerhalb 1..{rows}."}
            if not (1 <= slot <= mpr):
                return {"error": f"slot={slot} liegt außerhalb 1..{mpr}."}
            if int(te_size) < 1:
                return {"error": "te_size muss >= 1 sein."}

            # Remove existing entry for this position, then optionally re-add
            new_slots = [
                s for s in (current_uv.get("slots") or [])
                if not (s["row"] == row and s["slot"] == slot)
            ]
            if str(device_type or "").strip():
                new_slots.append({
                    "row": row,
                    "slot": slot,
                    "device_type": str(device_type).strip(),
                    "te_size": max(1, int(te_size)),
                    "spec": str(spec or "").strip(),
                    "label": str(label or "").strip(),
                    "assignment": str(assignment or "").strip(),
                    "note": str(note or "").strip(),
                })
            new_slots.sort(key=lambda s: (s["row"], s["slot"]))

            new_uv = dict(current_uv)
            new_uv["slots"] = new_slots
            panel.set_uv_config(new_uv)

            window.canvas.update()
            window._dirty = True
            window._update_title()
            action = "deleted" if not str(device_type or "").strip() else "set"
            return {
                "point_id": point_id,
                "row": row,
                "slot": slot,
                "action": action,
                "occupied_count": len(new_slots),
            }
        return invoke(_set)

    @mcp.tool()
    def delete_uv_slot(
        point_id: str,
        row: int,
        slot: int,
    ) -> dict:
        """Einzelnen TE-Slot in einer UV löschen (auf leer zurücksetzen).

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            row: Reihe (1-basiert)
            slot: TE-Position (1-basiert)
        """
        def _del():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}

            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            if ep.get("ap_type") != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            current_uv = dict(ep.get("uv_config") or {})
            old_slots = current_uv.get("slots") or []
            new_slots = [
                s for s in old_slots
                if not (s["row"] == row and s["slot"] == slot)
            ]
            if len(new_slots) == len(old_slots):
                return {
                    "point_id": point_id,
                    "action": "not_found",
                    "row": row, "slot": slot,
                }

            new_uv = dict(current_uv)
            new_uv["slots"] = new_slots
            panel.set_uv_config(new_uv)

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "action": "deleted",
                "row": row, "slot": slot,
                "occupied_count": len(new_slots),
            }
        return invoke(_del)

    @mcp.tool()
    def configure_up_distribution(
        point_id: str,
        incoming_cable_id: str,
        mappings: list[dict],
        outgoing_cable_ids: list[str] | None = None,
        note: str = "",
    ) -> dict:
        """Aderzuordnung für Verteilung in Unterputzdose setzen.

        Args:
            point_id: ID des Anschlusspunkts (z.B. 'AP-1')
            incoming_cable_id: ID der Zuleitung (z.B. 'KV-1')
            mappings: Zuordnungen als Array mit from_conductor,
                to_cable_id, to_conductor und optional note
            outgoing_cable_ids: Optionale Liste abgehender Kabel-IDs;
                wird bei Bedarf aus mappings ergänzt
            note: Optionale Notiz
        """
        def _configure():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}

            normalized_up_distribution_config = _normalize_up_distribution_config({
                "incoming_cable_id": incoming_cable_id,
                "outgoing_cable_ids": outgoing_cable_ids or [],
                "mappings": mappings or [],
                "note": note,
            }, require_incoming=True)
            panel.set_ap_type("up_distribution")
            panel.set_up_distribution_config(normalized_up_distribution_config)

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "configured",
                "up_distribution_config": normalized_up_distribution_config,
            }

        return invoke(_configure)

    @mcp.tool()
    def clear_up_distribution(
        point_id: str,
        reset_ap_type: bool = False,
    ) -> dict:
        """Aderzuordnung für Verteilung in Unterputzdose leeren.

        Args:
            point_id: ID des Anschlusspunkts (z.B. 'AP-1')
            reset_ap_type: Setzt den AP-Typ zusätzlich auf 'standard'
        """
        def _clear():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {
                    "error": f"Anschlusspunkt '{point_id}' "
                             f"nicht gefunden."}

            panel.set_up_distribution_config({})
            if reset_ap_type:
                panel.set_ap_type("standard")

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "cleared",
                "ap_type": panel.get_ap_type(),
                "up_distribution_config": {},
            }

        return invoke(_clear)

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

    @mcp.tool()
    def modify_hkv(
        hkv_id: str,
        name: str | None = None,
        x: float | None = None,
        y: float | None = None,
        color: str | None = None,
        width: float | None = None,
        height: float | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Parameter eines Heizkreisverteilers (HKV) ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            hkv_id: ID des HKV (z.B. 'HKV-1')
            name: Neuer Anzeigename
            x: Neue X-Position (Canvas-Pixel)
            y: Neue Y-Position (Canvas-Pixel)
            color: Neue Farbe (#rrggbb)
            width: Neue Breite in mm
            height: Neue Höhe in mm
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.hkv_panels.get(hkv_id)
            if not panel:
                return {"error": f"HKV '{hkv_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[hkv_id] = name
                window.param_panel.update_all_hkv_choices()
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas.set_color(hkv_id, QC(color))
            if width is not None:
                panel.sb_width.setValue(width / 10.0)
            if height is not None:
                panel.sb_height.setValue(height / 10.0)
            if x is not None or y is not None:
                old = window.canvas._hkv_points.get(hkv_id)
                nx = x if x is not None else (old.x() if old else 0)
                ny = y if y is not None else (old.y() if old else 0)
                window.canvas._hkv_points[hkv_id] = QPointF(nx, ny)
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._hkv_visible[hkv_id] = visible

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "hkv_id": hkv_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    # ── Elektro-Raum-Tools ─────────────────────────────────────────

    @mcp.tool()
    def list_elec_rooms() -> list[dict]:
        """Liste aller Elektro-Räume mit Parametern und Polygon-Info."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for rid, rdata in p.get("elec_rooms", {}).items():
                entry = dict(rdata)
                poly = c.get("elec_rooms", {}).get(rid, [])
                entry["polygon_points"] = len(poly)
                entry["has_polygon"] = len(poly) >= 3
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def add_elec_room(
        name: str,
        polygon: list[list[float]],
        floor_plan_id: str = "",
        color: str = "#43aa8b",
    ) -> dict:
        """Elektro-Raum mit Polygon hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Wohnzimmer', 'Küche')
            polygon: Raum-Polygon als [[x1,y1], [x2,y2], ...] in
                Canvas-Pixeln. Mindestens 3 Punkte.
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
        """
        if len(polygon) < 3:
            return {"error": "Polygon muss mindestens 3 Punkte haben."}

        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._elec_room_counter += 1
            rid = f"R-{w._elec_room_counter}"

            panel = w._create_elec_room_panel(
                rid, fp_id=floor_plan_id or None, name=name)

            # Polygon setzen
            w.canvas._elec_room_polygons[rid] = [
                QPointF(p[0], p[1]) for p in polygon]
            w.canvas._elec_room_visible[rid] = True
            w.canvas.set_color(rid, QC(color))
            w.canvas._label_map[rid] = name
            w.canvas._ensure_color(rid)

            # Panel-Parameter setzen
            panel.le_name.setText(name)
            panel._color = QC(color)
            panel._update_color_button()

            w._update_elec_point_room_assignments()
            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {
                "room_id": rid,
                "name": name,
                "polygon_points": len(polygon),
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_elec_room(
        room_id: str,
        name: str | None = None,
        color: str | None = None,
        polygon: list[list[float]] | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Parameter eines Elektro-Raums ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            room_id: ID des Raums (z.B. 'R-1')
            name: Neuer Anzeigename
            color: Neue Farbe (#rrggbb)
            polygon: Neues Polygon [[x,y], ...] (mind. 3 Punkte)
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.elec_room_panels.get(room_id)
            if not panel:
                return {"error": f"Raum '{room_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[room_id] = name
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas.set_color(room_id, QC(color))
            if polygon is not None:
                if len(polygon) < 3:
                    return {"error": "Polygon muss mindestens 3 Punkte haben."}
                window.canvas._elec_room_polygons[room_id] = [
                    QPointF(p[0], p[1]) for p in polygon]
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._elec_room_visible[room_id] = visible

            window._update_elec_point_room_assignments()
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "room_id": room_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_elec_room(room_id: str) -> dict:
        """Elektro-Raum löschen.

        Args:
            room_id: ID des zu löschenden Raums (z.B. 'R-1')
        """
        def _delete():
            if room_id not in window.param_panel.elec_room_panels:
                return {"error": f"Raum '{room_id}' nicht gefunden."}
            window._delete_elec_room(room_id)
            window._dirty = True
            window._update_title()
            return {"room_id": room_id, "status": "deleted"}

        return invoke(_delete)

    # ── Elektro-Kabel-Tools ────────────────────────────────────────

    @mcp.tool()
    def list_elec_cables() -> list[dict]:
        """Liste aller Elektro-Kabel mit Parametern, Länge und AP-Verbindungen."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for kid, kdata in p.get("elec_cables", {}).items():
                entry = dict(kdata)
                pts = c.get("elec_cables", {}).get(kid, [])
                entry["polyline_points"] = len(pts)
                entry["has_route"] = len(pts) >= 2
                start_ap = c.get("cable_start_ap", {}).get(kid, "")
                end_ap = c.get("cable_end_ap", {}).get(kid, "")
                entry["start_ap_id"] = start_ap
                entry["end_ap_id"] = end_ap
                length_px = window.canvas.get_elec_cable_length_px(kid)
                entry["length_mm"] = round(
                    length_px * window.canvas.get_mm_per_px(), 1)
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def add_elec_cable(
        name: str,
        polyline: list[list[float]],
        floor_plan_id: str = "",
        color: str = "#ff9800",
        cable_type: str = "5x1,5",
        comment: str = "",
        start_ap_id: str = "",
        end_ap_id: str = "",
        stroke_width: float = 2.0,
    ) -> dict:
        """Elektro-Kabel als Polylinie hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Zuleitung Küche')
            polyline: Kabelführung als [[x1,y1], [x2,y2], ...] in
                Canvas-Pixeln. Mindestens 2 Punkte.
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
            cable_type: Kabeltyp (z.B. '5x1,5', '3x2,5', 'NYM-J 3x1,5')
            comment: Kommentar / Notiz
            start_ap_id: Start-Anschlusspunkt-ID (z.B. 'AP-1'),
                leer = kein Start-AP
            end_ap_id: End-Anschlusspunkt-ID (z.B. 'AP-2'),
                leer = kein End-AP
            stroke_width: Strichstärke der Kabellinie in px (0.5–10.0,
                Standard: 2.0)
        """
        if len(polyline) < 2:
            return {"error": "Polylinie muss mindestens 2 Punkte haben."}

        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._elec_cable_counter += 1
            kid = f"KV-{w._elec_cable_counter}"

            panel = w._create_elec_cable_panel(
                kid, fp_id=floor_plan_id or None, name=name)

            # Polylinie setzen
            w.canvas._elec_cables[kid] = [
                QPointF(p[0], p[1]) for p in polyline]
            w.canvas._elec_visible[kid] = True
            w.canvas._elec_cable_notes[kid] = comment
            w.canvas.set_color(kid, QC(color))
            w.canvas._label_map[kid] = name
            w.canvas._ensure_color(kid)

            # Strichstärke setzen
            w.canvas.set_elec_cable_stroke_width(kid, stroke_width)
            panel.sb_stroke_width.setValue(max(0.5, min(10.0, stroke_width)))

            # AP-Verbindungen setzen
            if start_ap_id:
                w.canvas._cable_start_ap[kid] = start_ap_id
            if end_ap_id:
                w.canvas._cable_end_ap[kid] = end_ap_id

            # Panel-Parameter setzen
            panel.le_name.setText(name)
            panel.set_type_text(cable_type)
            panel.te_comment.setPlainText(comment)
            panel._color = QC(color)
            panel._update_color_button()

            # Länge anzeigen
            length_px = w.canvas.get_elec_cable_length_px(kid)
            length_mm = length_px * w.canvas.get_mm_per_px()
            w.param_panel.set_cable_length(kid, length_mm)
            w._update_cable_ap_labels(kid)

            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {
                "cable_id": kid,
                "name": name,
                "polyline_points": len(polyline),
                "length_mm": round(length_mm, 1),
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_elec_cable(
        cable_id: str,
        name: str | None = None,
        color: str | None = None,
        cable_type: str | None = None,
        comment: str | None = None,
        polyline: list[list[float]] | None = None,
        start_ap_id: str | None = None,
        end_ap_id: str | None = None,
        visible: bool | None = None,
        stroke_width: float | None = None,
    ) -> dict:
        """Parameter eines Elektro-Kabels ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            cable_id: ID des Kabels (z.B. 'KV-1')
            name: Neuer Anzeigename
            color: Neue Farbe (#rrggbb)
            cable_type: Neuer Kabeltyp (z.B. '3x2,5')
            comment: Neue Notiz
            polyline: Neue Kabelführung [[x,y], ...] (mind. 2 Punkte)
            start_ap_id: Neue Start-AP-ID (leer = entfernen)
            end_ap_id: Neue End-AP-ID (leer = entfernen)
            visible: Sichtbarkeit
            stroke_width: Neue Strichstärke in px (0.5–10.0)
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.elec_cable_panels.get(cable_id)
            if not panel:
                return {"error": f"Kabel '{cable_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[cable_id] = name
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas.set_color(cable_id, QC(color))
            if cable_type is not None:
                panel.set_type_text(cable_type)
            if comment is not None:
                panel.te_comment.setPlainText(comment)
                window.canvas._elec_cable_notes[cable_id] = comment
            if polyline is not None:
                if len(polyline) < 2:
                    return {"error": "Polylinie muss mindestens 2 Punkte haben."}
                window.canvas._elec_cables[cable_id] = [
                    QPointF(p[0], p[1]) for p in polyline]
                length_px = window.canvas.get_elec_cable_length_px(cable_id)
                length_mm = length_px * window.canvas.get_mm_per_px()
                window.param_panel.set_cable_length(cable_id, length_mm)
            if start_ap_id is not None:
                if start_ap_id:
                    window.canvas._cable_start_ap[cable_id] = start_ap_id
                else:
                    window.canvas._cable_start_ap.pop(cable_id, None)
                window._update_cable_ap_labels(cable_id)
            if end_ap_id is not None:
                if end_ap_id:
                    window.canvas._cable_end_ap[cable_id] = end_ap_id
                else:
                    window.canvas._cable_end_ap.pop(cable_id, None)
                window._update_cable_ap_labels(cable_id)
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._elec_visible[cable_id] = visible
            if stroke_width is not None:
                window.canvas.set_elec_cable_stroke_width(cable_id, stroke_width)
                panel.sb_stroke_width.setValue(max(0.5, min(10.0, stroke_width)))

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "cable_id": cable_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_elec_cable(cable_id: str) -> dict:
        """Elektro-Kabel löschen.

        Args:
            cable_id: ID des zu löschenden Kabels (z.B. 'KV-1')
        """
        def _delete():
            if cable_id not in window.param_panel.elec_cable_panels:
                return {"error": f"Kabel '{cable_id}' nicht gefunden."}
            window._delete_elec_cable(cable_id)
            window._dirty = True
            window._update_title()
            return {"cable_id": cable_id, "status": "deleted"}

        return invoke(_delete)

    # ── HKV-Leitungs-Tools ────────────────────────────────────────

    @mcp.tool()
    def list_hkv_lines() -> list[dict]:
        """Liste aller HKV-Verbindungsleitungen mit Parametern und Länge."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for lid, ldata in p.get("hkv_lines", {}).items():
                entry = dict(ldata)
                pts = c.get("hkv_lines", {}).get(lid, [])
                entry["polyline_points"] = len(pts)
                entry["has_route"] = len(pts) >= 2
                entry["start_hkv_id"] = c.get(
                    "hkv_line_start", {}).get(lid, "")
                entry["end_hkv_id"] = c.get(
                    "hkv_line_end", {}).get(lid, "")
                length_px = window.canvas.get_hkv_line_length_px(lid)
                entry["length_mm"] = round(
                    length_px * window.canvas.get_mm_per_px(), 1)
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def add_hkv_line(
        name: str,
        polyline: list[list[float]],
        floor_plan_id: str = "",
        color: str = "#e53935",
        pipe_type: str = "DN20",
        start_hkv_id: str = "",
        end_hkv_id: str = "",
    ) -> dict:
        """HKV-Verbindungsleitung als Polylinie hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Leitung EG→OG')
            polyline: Leitungsführung als [[x1,y1], [x2,y2], ...] in
                Canvas-Pixeln. Mindestens 2 Punkte.
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
            pipe_type: Rohrtyp (z.B. 'DN20', 'DN25')
            start_hkv_id: Start-HKV-ID (z.B. 'HKV-1'),
                leer = kein Start-HKV
            end_hkv_id: End-HKV-ID (z.B. 'HKV-2'),
                leer = kein End-HKV
        """
        if len(polyline) < 2:
            return {"error": "Polylinie muss mindestens 2 Punkte haben."}

        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._hkv_line_counter += 1
            lid = f"HL-{w._hkv_line_counter}"

            panel = w._create_hkv_line_panel(
                lid, fp_id=floor_plan_id or None, name=name)

            # Polylinie setzen
            w.canvas._hkv_lines[lid] = [
                QPointF(p[0], p[1]) for p in polyline]
            w.canvas._hkv_line_visible[lid] = True
            w.canvas.set_color(lid, QC(color))
            w.canvas._label_map[lid] = name
            w.canvas._ensure_color(lid)

            # HKV-Verbindungen setzen
            if start_hkv_id:
                w.canvas._hkv_line_start[lid] = start_hkv_id
            if end_hkv_id:
                w.canvas._hkv_line_end[lid] = end_hkv_id

            # Panel-Parameter setzen
            panel.le_name.setText(name)
            panel.le_type.setText(pipe_type)
            panel._color = QC(color)
            panel._update_color_button()

            # Länge und Labels anzeigen
            length_px = w.canvas.get_hkv_line_length_px(lid)
            length_mm = length_px * w.canvas.get_mm_per_px()
            w.param_panel.set_hkv_line_length(lid, length_mm)
            w._update_hkv_line_labels(lid)

            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {
                "line_id": lid,
                "name": name,
                "polyline_points": len(polyline),
                "length_mm": round(length_mm, 1),
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_hkv_line(
        line_id: str,
        name: str | None = None,
        color: str | None = None,
        pipe_type: str | None = None,
        polyline: list[list[float]] | None = None,
        start_hkv_id: str | None = None,
        end_hkv_id: str | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Parameter einer HKV-Verbindungsleitung ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            line_id: ID der Leitung (z.B. 'HL-1')
            name: Neuer Anzeigename
            color: Neue Farbe (#rrggbb)
            pipe_type: Neuer Rohrtyp
            polyline: Neue Leitungsführung [[x,y], ...] (mind. 2 Punkte)
            start_hkv_id: Neue Start-HKV-ID (leer = entfernen)
            end_hkv_id: Neue End-HKV-ID (leer = entfernen)
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.hkv_line_panels.get(line_id)
            if not panel:
                return {"error": f"HKV-Leitung '{line_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
                window.canvas._label_map[line_id] = name
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas.set_color(line_id, QC(color))
            if pipe_type is not None:
                panel.le_type.setText(pipe_type)
            if polyline is not None:
                if len(polyline) < 2:
                    return {"error": "Polylinie muss mindestens 2 Punkte haben."}
                window.canvas._hkv_lines[line_id] = [
                    QPointF(p[0], p[1]) for p in polyline]
                length_px = window.canvas.get_hkv_line_length_px(line_id)
                length_mm = length_px * window.canvas.get_mm_per_px()
                window.param_panel.set_hkv_line_length(line_id, length_mm)
            if start_hkv_id is not None:
                if start_hkv_id:
                    window.canvas._hkv_line_start[line_id] = start_hkv_id
                else:
                    window.canvas._hkv_line_start.pop(line_id, None)
                window._update_hkv_line_labels(line_id)
            if end_hkv_id is not None:
                if end_hkv_id:
                    window.canvas._hkv_line_end[line_id] = end_hkv_id
                else:
                    window.canvas._hkv_line_end.pop(line_id, None)
                window._update_hkv_line_labels(line_id)
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._hkv_line_visible[line_id] = visible

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "line_id": line_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_hkv_line(line_id: str) -> dict:
        """HKV-Verbindungsleitung löschen.

        Args:
            line_id: ID der zu löschenden Leitung (z.B. 'HL-1')
        """
        def _delete():
            if line_id not in window.param_panel.hkv_line_panels:
                return {"error": f"HKV-Leitung '{line_id}' nicht gefunden."}
            window._delete_hkv_line(line_id)
            window._dirty = True
            window._update_title()
            return {"line_id": line_id, "status": "deleted"}

        return invoke(_delete)

    # ── Text-Annotations-Tools ─────────────────────────────────────

    @mcp.tool()
    def list_texts() -> list[dict]:
        """Liste aller Text-Annotationen mit Inhalt und Position."""
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            result = []
            for tid, tdata in p.get("text_annotations", {}).items():
                entry = dict(tdata)
                pos = c.get("text_annotations", {}).get(tid)
                entry["canvas_position"] = list(pos) if pos else None
                entry["is_placed"] = pos is not None
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def add_text(
        content: str,
        x: float,
        y: float,
        floor_plan_id: str = "",
        name: str = "",
        color: str = "#ffffff",
        font_size: float = 14.0,
        comment: str = "",
    ) -> dict:
        """Text-Annotation auf dem Canvas platzieren.

        Args:
            content: Der anzuzeigende Text
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            name: Interner Name für die Sidebar (leer = auto)
            color: Textfarbe (#rrggbb), Standard: weiß
            font_size: Schriftgröße in pt (Standard: 14)
            comment: Kommentar (wird als Tooltip angezeigt)
        """
        def _add():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            w = window
            w._text_counter += 1
            tid = f"Text-{w._text_counter}"
            display_name = name or tid

            panel = w._create_text_panel(
                tid, fp_id=floor_plan_id or None, name=display_name)

            # Position setzen
            w.canvas._text_annotations[tid] = QPointF(x, y)
            w.canvas._text_contents[tid] = content
            w.canvas._text_font_sizes[tid] = font_size
            w.canvas._text_colors[tid] = color
            w.canvas._text_comments[tid] = comment
            w.canvas._text_visible[tid] = True

            # Panel-Parameter setzen
            panel.le_name.setText(display_name)
            panel.te_content.setPlainText(content)
            panel.sb_font_size.setValue(font_size)
            panel._color = QC(color)
            panel._update_color_button()
            panel.te_comment.setPlainText(comment)

            w.canvas.update()
            w._dirty = True
            w._update_title()

            return {
                "text_id": tid,
                "content": content,
                "position_px": [x, y],
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_text(
        text_id: str,
        content: str | None = None,
        x: float | None = None,
        y: float | None = None,
        color: str | None = None,
        font_size: float | None = None,
        comment: str | None = None,
        name: str | None = None,
        visible: bool | None = None,
    ) -> dict:
        """Text-Annotation ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            text_id: ID der Annotation (z.B. 'Text-1')
            content: Neuer Textinhalt
            x: Neue X-Position (Canvas-Pixel)
            y: Neue Y-Position (Canvas-Pixel)
            color: Neue Textfarbe (#rrggbb)
            font_size: Neue Schriftgröße in pt
            comment: Neuer Kommentar
            name: Neuer interner Name
            visible: Sichtbarkeit
        """
        def _modify():
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QColor as QC

            panel = window.param_panel.text_panels.get(text_id)
            if not panel:
                return {"error": f"Text '{text_id}' nicht gefunden."}

            if content is not None:
                panel.te_content.setPlainText(content)
                window.canvas._text_contents[text_id] = content
            if x is not None or y is not None:
                old = window.canvas._text_annotations.get(text_id)
                nx = x if x is not None else (old.x() if old else 0)
                ny = y if y is not None else (old.y() if old else 0)
                window.canvas._text_annotations[text_id] = QPointF(nx, ny)
            if color is not None:
                panel._color = QC(color)
                panel._update_color_button()
                window.canvas._text_colors[text_id] = color
            if font_size is not None:
                panel.sb_font_size.setValue(font_size)
                window.canvas._text_font_sizes[text_id] = font_size
            if comment is not None:
                panel.te_comment.setPlainText(comment)
                window.canvas._text_comments[text_id] = comment
            if name is not None:
                panel.le_name.setText(name)
            if visible is not None:
                panel.chk_visible.setChecked(visible)
                window.canvas._text_visible[text_id] = visible

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "text_id": text_id,
                "status": "modified",
                "params": panel.get_parameters(),
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_text(text_id: str) -> dict:
        """Text-Annotation löschen.

        Args:
            text_id: ID der zu löschenden Annotation (z.B. 'Text-1')
        """
        def _delete():
            if text_id not in window.param_panel.text_panels:
                return {"error": f"Text '{text_id}' nicht gefunden."}
            window._delete_text(text_id)
            return {"text_id": text_id, "status": "deleted"}

        return invoke(_delete)

    # ── Grundriss-Tools ───────────────────────────────────────────

    @mcp.tool()
    def list_floor_plans() -> list[dict]:
        """Liste aller Grundriss-Layer mit Eigenschaften."""
        def _read():
            p = window.param_panel.to_dict()
            result = []
            for fid, fdata in p.get("floorplans", {}).items():
                entry = dict(fdata)
                layer = window.canvas._floor_plans.get(fid)
                if layer:
                    entry["mm_per_px"] = layer.mm_per_px
                    entry["offset_x"] = layer.offset_x
                    entry["offset_y"] = layer.offset_y
                    entry["rotation"] = layer.rotation
                    entry["opacity"] = layer.opacity
                    entry["visible"] = layer.visible
                result.append(entry)
            return result
        return invoke(_read)

    @mcp.tool()
    def add_floor_plan(
        name: str,
        file_path: str = "",
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        opacity: float = 1.0,
    ) -> dict:
        """Neuen Grundriss-Layer hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Erdgeschoss')
            file_path: Pfad zum Bild (PNG/JPG/SVG), relativ zum Projekt
                oder absolut. Leer = leerer Layer.
            offset_x: Horizontale Verschiebung in Canvas-Pixeln
            offset_y: Vertikale Verschiebung in Canvas-Pixeln
            opacity: Deckkraft 0.0 (transparent) bis 1.0 (opak)
        """
        def _add():
            import os as _os

            w = window
            w._floorplan_counter += 1
            fp_id = f"grundriss-{w._floorplan_counter}"

            w.canvas.add_floor_plan(fp_id)
            panel = w.param_panel.add_floorplan_panel(fp_id, name=name)

            # Bild laden wenn angegeben
            resolved_path = ""
            if file_path:
                # Relativen Pfad auflösen
                if w._project_path and not _os.path.isabs(file_path):
                    resolved = w._project_path.parent / file_path
                    if resolved.exists():
                        resolved_path = str(resolved)
                    else:
                        resolved_path = file_path
                else:
                    resolved_path = file_path

                if _os.path.exists(resolved_path):
                    panel.set_file_path(resolved_path)
                    w.canvas.load_floor_plan_image(fp_id, resolved_path)
                    if not w._svg_path:
                        w._svg_path = resolved_path

            # Transform setzen
            w.canvas.set_floor_plan_transform(
                fp_id, offset_x, offset_y, 0.0)
            w.canvas.set_floor_plan_opacity(fp_id, opacity)
            w._dirty = True
            w._update_title()

            return {
                "floor_plan_id": fp_id,
                "name": name,
                "file_path": resolved_path,
                "status": "created",
            }

        return invoke(_add)

    @mcp.tool()
    def modify_floor_plan(
        floor_plan_id: str,
        name: str | None = None,
        offset_x: float | None = None,
        offset_y: float | None = None,
        rotation: float | None = None,
        opacity: float | None = None,
        visible: bool | None = None,
        ref_length_mm: float | None = None,
    ) -> dict:
        """Grundriss-Layer Eigenschaften ändern.
        Nur angegebene Parameter werden geändert.

        Args:
            floor_plan_id: ID des Grundrisses (z.B. 'grundriss-1')
            name: Neuer Anzeigename
            offset_x: Neue X-Verschiebung in Canvas-Pixeln
            offset_y: Neue Y-Verschiebung in Canvas-Pixeln
            rotation: Drehwinkel in Grad
            opacity: Deckkraft 0.0–1.0
            visible: Sichtbarkeit
            ref_length_mm: Referenzlänge in mm zum Setzen des Maßstabs
                (erfordert eine gezeichnete Referenzlinie)
        """
        def _modify():
            panel = window.param_panel.floorplan_panels.get(floor_plan_id)
            if not panel:
                return {"error": f"Grundriss '{floor_plan_id}' nicht gefunden."}

            layer = window.canvas._floor_plans.get(floor_plan_id)
            if not layer:
                return {"error": f"Grundriss-Layer '{floor_plan_id}' nicht gefunden."}

            if name is not None:
                panel.le_name.setText(name)
            if visible is not None:
                window.canvas.set_floor_plan_visible(floor_plan_id, visible)
            if opacity is not None:
                window.canvas.set_floor_plan_opacity(floor_plan_id, opacity)

            # Transform: get current values if not specified
            new_ox = offset_x if offset_x is not None else layer.offset_x
            new_oy = offset_y if offset_y is not None else layer.offset_y
            new_rot = rotation if rotation is not None else layer.rotation
            if any(v is not None for v in [offset_x, offset_y, rotation]):
                window.canvas.set_floor_plan_transform(
                    floor_plan_id, new_ox, new_oy, new_rot)

            if ref_length_mm is not None:
                # Use existing ref line points
                p1 = layer.ref_p1 or window.canvas._ref_p1
                p2 = layer.ref_p2 or window.canvas._ref_p2
                if p1 is not None and p2 is not None:
                    import math as _math
                    px_len = _math.hypot(
                        p2.x() - p1.x(), p2.y() - p1.y())
                    if px_len > 0:
                        mm_per_px = ref_length_mm / px_len
                        layer.mm_per_px = mm_per_px
                        layer.ref_length_mm = ref_length_mm
                        if (window.canvas._mm_per_px == 1.0 or (
                                window.canvas._floor_plan_order
                                and window.canvas._floor_plan_order[0]
                                == floor_plan_id)):
                            window.canvas.set_mm_per_px(mm_per_px)
                        panel.update_scale_label(mm_per_px)

            window.canvas.update()
            window._dirty = True
            window._update_title()

            return {
                "floor_plan_id": floor_plan_id,
                "status": "modified",
                "mm_per_px": layer.mm_per_px,
                "offset_x": layer.offset_x,
                "offset_y": layer.offset_y,
                "rotation": layer.rotation,
                "opacity": layer.opacity,
                "visible": layer.visible,
            }

        return invoke(_modify)

    @mcp.tool()
    def delete_floor_plan(floor_plan_id: str) -> dict:
        """Grundriss-Layer löschen.

        Args:
            floor_plan_id: ID des zu löschenden Grundrisses
                (z.B. 'grundriss-1')
        """
        def _delete():
            if floor_plan_id not in window.param_panel.floorplan_panels:
                return {"error": f"Grundriss '{floor_plan_id}' nicht gefunden."}
            window._delete_floorplan(floor_plan_id)
            window._dirty = True
            window._update_title()
            return {"floor_plan_id": floor_plan_id, "status": "deleted"}

        return invoke(_delete)

    # ── Heizkreis-Geometrie-Tools ──────────────────────────────────

    @mcp.tool()
    def set_circuit_polygon(
        circuit_id: str,
        polygon: list[list[float]],
    ) -> dict:
        """Raumpolygon eines bestehenden Heizkreises setzen/aktualisieren.

        Args:
            circuit_id: ID des Heizkreises (z.B. 'HK-1')
            polygon: Raumpolygon [[x,y], ...] in Canvas-Pixeln.
                Mindestens 3 Punkte.
        """
        if len(polygon) < 3:
            return {"error": "Polygon muss mindestens 3 Punkte haben."}

        def _set():
            from PySide6.QtCore import QPointF

            if circuit_id not in window.param_panel.circuit_panels:
                return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}

            window.canvas._polygons[circuit_id] = [
                QPointF(p[0], p[1]) for p in polygon]
            # Startpunkt auf ersten Polygon-Punkt setzen falls noch keiner
            if circuit_id not in window.canvas._start_points:
                window.canvas._start_points[circuit_id] = QPointF(
                    polygon[0][0], polygon[0][1])
            window._update_circuit_area(circuit_id)
            window._recalc_circuit_hydraulics(circuit_id)
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "circuit_id": circuit_id,
                "polygon_points": len(polygon),
                "status": "updated",
            }

        return invoke(_set)

    @mcp.tool()
    def set_circuit_route(
        circuit_id: str,
        route: list[list[float]],
    ) -> dict:
        """Manuellen Rohrverlauf eines Heizkreises setzen/aktualisieren.

        Args:
            circuit_id: ID des Heizkreises (z.B. 'HK-1')
            route: Rohrverlauf-Punkte [[x,y], ...] in Canvas-Pixeln.
                Mindestens 2 Punkte. Leer = Rohrverlauf löschen.
        """
        def _set():
            from PySide6.QtCore import QPointF

            if circuit_id not in window.param_panel.circuit_panels:
                return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}

            if route and len(route) < 2:
                return {"error": "Route muss mindestens 2 Punkte haben."}

            if route:
                window.canvas._manual_routes[circuit_id] = [
                    QPointF(p[0], p[1]) for p in route]
            else:
                window.canvas._manual_routes.pop(circuit_id, None)

            length_px = window.canvas.get_manual_route_length_px(circuit_id)
            length_mm = length_px * window.canvas.get_mm_per_px()
            window.param_panel.set_circuit_length(circuit_id, length_mm)
            window._update_total_length(circuit_id)
            window._recalc_circuit_hydraulics(circuit_id)
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "circuit_id": circuit_id,
                "route_points": len(route) if route else 0,
                "length_mm": round(length_mm, 1),
                "status": "updated",
            }

        return invoke(_set)

    @mcp.tool()
    def set_supply_line(
        circuit_id: str,
        supply_line: list[list[float]],
        hkv_id: str = "",
    ) -> dict:
        """Zuleitung eines Heizkreises setzen/aktualisieren.

        Args:
            circuit_id: ID des Heizkreises (z.B. 'HK-1')
            supply_line: Zuleitungs-Punkte [[x,y], ...] in Canvas-Pixeln.
                Mindestens 2 Punkte. Leer = Zuleitung löschen.
            hkv_id: HKV-ID, mit dem dieser Kreis verbunden ist
                (z.B. 'HKV-1'). Leer = keine Verbindung.
        """
        def _set():
            from PySide6.QtCore import QPointF

            if circuit_id not in window.param_panel.circuit_panels:
                return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}

            if supply_line and len(supply_line) < 2:
                return {"error": "Zuleitung muss mindestens 2 Punkte haben."}

            if supply_line:
                window.canvas._supply_lines[circuit_id] = [
                    QPointF(p[0], p[1]) for p in supply_line]
            else:
                window.canvas._supply_lines.pop(circuit_id, None)

            if hkv_id:
                window.canvas._supply_hkv[circuit_id] = hkv_id
            else:
                window.canvas._supply_hkv.pop(circuit_id, None)

            supply_px = window.canvas.get_supply_line_length_px(circuit_id)
            supply_mm = supply_px * window.canvas.get_mm_per_px()
            window.param_panel.set_supply_length(circuit_id, supply_mm)
            window._update_total_length(circuit_id)
            window._recalc_circuit_hydraulics(circuit_id)
            window._update_supply_hkv_label(circuit_id)
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "circuit_id": circuit_id,
                "supply_points": len(supply_line) if supply_line else 0,
                "supply_mm": round(supply_mm, 1),
                "connected_hkv": hkv_id,
                "status": "updated",
            }

        return invoke(_set)

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

    # ── Erweiterte Abfrage-Tools ───────────────────────────────────

    @mcp.tool()
    def bulk_set_uv_slots(
        point_id: str,
        slots: list[dict],
        replace_all: bool = False,
    ) -> dict:
        """Mehrere TE-Slots einer UV auf einmal setzen.

        Effizienter als wiederholte set_uv_slot-Aufrufe.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            slots: Liste von Slot-Dicts. Jedes Dict muss enthalten:
                row (int), slot (int), device_type (str).
                Optional: te_size (int, Standard 1), spec (str),
                label (str), assignment (str), note (str).
            replace_all: True = gesamte Belegung ersetzen (alle
                bisherigen Slots werden gelöscht). False = nur die
                angegebenen Positionen werden überschrieben.
        """
        def _bulk():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}

            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            if ep.get("ap_type") != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            current_uv = dict(ep.get("uv_config") or {})
            rows = current_uv.get("rows", 0)
            mpr = current_uv.get("modules_per_row", 0)
            if rows < 1 or mpr < 1:
                return {"error": "UV hat kein gültiges Layout."}

            # Validate incoming slots
            errors = []
            for i, s in enumerate(slots):
                r = s.get("row", 0)
                sl = s.get("slot", 0)
                if not (1 <= r <= rows):
                    errors.append(f"slots[{i}]: row={r} außerhalb 1..{rows}")
                if not (1 <= sl <= mpr):
                    errors.append(f"slots[{i}]: slot={sl} außerhalb 1..{mpr}")
            if errors:
                return {"error": "; ".join(errors)}

            # Build new slot list
            if replace_all:
                base_slots: list[dict] = []
            else:
                incoming_keys = {(s["row"], s["slot"]) for s in slots}
                base_slots = [
                    s for s in (current_uv.get("slots") or [])
                    if (s["row"], s["slot"]) not in incoming_keys
                ]

            for s in slots:
                dt = str(s.get("device_type", "") or "").strip()
                if not dt:
                    continue  # skip empty = delete
                base_slots.append({
                    "row": int(s["row"]),
                    "slot": int(s["slot"]),
                    "device_type": dt,
                    "te_size": max(1, int(s.get("te_size", 1) or 1)),
                    "spec": str(s.get("spec", "") or "").strip(),
                    "label": str(s.get("label", "") or "").strip(),
                    "assignment": str(s.get("assignment", "") or "").strip(),
                    "note": str(s.get("note", "") or "").strip(),
                })

            base_slots.sort(key=lambda x: (x["row"], x["slot"]))
            new_uv = dict(current_uv)
            new_uv["slots"] = base_slots
            panel.set_uv_config(new_uv)

            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "occupied_count": len(base_slots),
                "total_te": rows * mpr,
                "status": "updated",
            }

        return invoke(_bulk)

    @mcp.tool()
    def find_free_uv_slots(
        point_id: str,
        min_te_size: int = 1,
    ) -> dict:
        """Freie TE-Positionen in einer UV finden.

        Args:
            point_id: ID des UV-Anschlusspunkts
            min_te_size: Nur Lücken zurückgeben die mindestens
                diese Anzahl zusammenhängender freier TEs haben.
                Standard 1 = alle freien Positionen.

        Returns:
            Dict mit free_slots (Liste freier Positionen) und
            free_runs (zusammenhängende freie Blöcke je Reihe).
        """
        def _find():
            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id)
            if ep is None:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if ep.get("ap_type") != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            uv = ep.get("uv_config") or {}
            rows = uv.get("rows", 0)
            mpr = uv.get("modules_per_row", 0)
            if rows < 1 or mpr < 1:
                return {"error": "UV hat kein gültiges Layout."}

            # Build occupied map (row, slot) → te_size
            occupied: dict[tuple[int, int], int] = {}
            for s in (uv.get("slots") or []):
                ts = max(1, int(s.get("te_size", 1) or 1))
                row_no = int(s["row"])
                slot_no = int(s["slot"])
                for t in range(ts):
                    occupied[(row_no, slot_no + t)] = ts

            free_slots: list[dict] = []
            free_runs: list[dict] = []

            for row_no in range(1, rows + 1):
                te_offset = (row_no - 1) * mpr
                run_start: int | None = None
                run_len = 0
                for te in range(1, mpr + 1):
                    if (row_no, te) not in occupied:
                        global_te = te_offset + te
                        free_slots.append({"row": row_no, "slot": te,
                                           "global_te": global_te})
                        if run_start is None:
                            run_start = te
                        run_len += 1
                    else:
                        if run_start is not None and run_len >= min_te_size:
                            free_runs.append({
                                "row": row_no,
                                "start_slot": run_start,
                                "length_te": run_len,
                                "global_te_start": (row_no - 1) * mpr + run_start,
                            })
                        run_start = None
                        run_len = 0
                    # advance past multi-TE block
                    ts = occupied.get((row_no, te), 1)
                if run_start is not None and run_len >= min_te_size:
                    free_runs.append({
                        "row": row_no,
                        "start_slot": run_start,
                        "length_te": run_len,
                        "global_te_start": (row_no - 1) * mpr + run_start,
                    })

            total_te = rows * mpr
            used_te = sum(
                max(1, int(s.get("te_size", 1) or 1))
                for s in (uv.get("slots") or [])
            )
            return {
                "point_id": point_id,
                "rows": rows,
                "modules_per_row": mpr,
                "total_te": total_te,
                "used_te": used_te,
                "free_te": total_te - used_te,
                "free_slots": free_slots,
                "free_runs": free_runs,
            }

        return invoke(_find)

    @mcp.tool()
    def get_uv_busbars(point_id: str) -> dict:
        """Phasenschienen (Busbars) einer UV-Unterverteilung lesen.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')

        Returns:
            Dict mit point_id, busbars (flache Liste aller Einträge) und
            phases (nach Phase gruppierte Übersicht mit te_ranges).
        """
        def _get():
            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id)
            if ep is None:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if ep.get("ap_type") != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}
            uv = ep.get("uv_config") or {}
            raw_busbars = uv.get("busbars", [])
            # Build grouped view
            from collections import OrderedDict
            grouped: dict[str, dict] = OrderedDict()
            for bb in raw_busbars:
                ph = str(bb.get("phase", "") or "")
                if ph not in grouped:
                    grouped[ph] = {
                        "phase": ph,
                        "color": str(bb.get("color", "#888888") or "#888888"),
                        "te_ranges": [],
                    }
                grouped[ph]["te_ranges"].append([
                    int(bb.get("te_start", 1) or 1),
                    int(bb.get("te_end", 1) or 1),
                ])
            return {
                "point_id": point_id,
                "busbars": raw_busbars,
                "phases": list(grouped.values()),
            }
        return invoke(_get)

    @mcp.tool()
    def set_uv_busbar(
        point_id: str,
        phase: str,
        te_start: int = 0,
        te_end: int = 0,
        color: str = "",
        te_ranges: list[list[int]] | None = None,
        replace_existing: bool = True,
    ) -> dict:
        """Phasenschiene in einer UV-Unterverteilung hinzufügen oder ersetzen.

        Unterstützt nicht-zusammenhängende Bereiche über te_ranges.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            phase: Phasenbezeichnung (z.B. 'L1', 'L2', 'L3', 'N', 'PE').
                Für dreiphasige Sammelschiene 'L1/L2/L3' verwenden – dann rotiert
                die Phase L1→L2→L3 automatisch pro TE.
            te_start: Erste globale TE-Nummer (inklusiv, >= 1).
                Wird ignoriert wenn te_ranges gesetzt ist.
            te_end: Letzte globale TE-Nummer (inklusiv, >= te_start).
                Wird ignoriert wenn te_ranges gesetzt ist.
            color: Hex-Farbe (#rrggbb). Leer = Standardfarbe für die Phase.
            te_ranges: Nicht-zusammenhängende TE-Bereiche als Liste von
                [start, end]-Paaren, z.B. [[15,16],[28,28],[39,39]].
                Ersetzt te_start/te_end. Erzeugt einen Busbar-Eintrag
                pro Bereich — alle mit derselben Phase und Farbe.
            replace_existing: True (Standard) = vorhandene Einträge mit
                derselben Phase werden ersetzt. False = neue Bereiche
                werden zu vorhandenen hinzugefügt.
        """
        _PHASE_COLORS = {
            "L1": "#e53935",
            "L2": "#43a047",
            "L3": "#1e88e5",
            "N": "#1565c0",
            "PE": "#558b2f",
            "L": "#ff7043",
        }

        def _set():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if panel.get_ap_type() != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            phase_s = str(phase or "").strip()
            if not phase_s:
                return {"error": "phase darf nicht leer sein."}

            resolved_color = str(color or "").strip() or _PHASE_COLORS.get(phase_s, "#888888")

            # Build list of new busbar entries from te_ranges or te_start/te_end
            new_entries: list[dict] = []
            if te_ranges is not None:
                if not isinstance(te_ranges, list) or not te_ranges:
                    return {"error": "te_ranges muss eine nicht-leere Liste von [start, end]-Paaren sein."}
                for ridx, rng in enumerate(te_ranges):
                    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
                        return {"error": f"te_ranges[{ridx}] muss ein [start, end]-Paar sein."}
                    try:
                        r_s, r_e = int(rng[0]), int(rng[1])
                    except (TypeError, ValueError):
                        return {"error": f"te_ranges[{ridx}] enthält ungültige Werte."}
                    if r_s < 1 or r_e < r_s:
                        return {"error": f"te_ranges[{ridx}]: ungültig ({r_s}, {r_e})."}
                    new_entries.append({
                        "phase": phase_s, "color": resolved_color,
                        "te_start": r_s, "te_end": r_e,
                    })
            else:
                if te_start < 1 or te_end < 1 or te_start > te_end:
                    return {"error": f"Ungültige TE-Range: te_start={te_start}, te_end={te_end}."}
                new_entries.append({
                    "phase": phase_s, "color": resolved_color,
                    "te_start": te_start, "te_end": te_end,
                })

            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            uv = dict(ep.get("uv_config") or {})
            existing_busbars = list(uv.get("busbars", []) or [])
            if replace_existing:
                # Remove all existing entries with the same phase
                base = [bb for bb in existing_busbars if str(bb.get("phase", "")) != phase_s]
            else:
                base = list(existing_busbars)
            base.extend(new_entries)
            base.sort(key=lambda b: b["te_start"])
            uv["busbars"] = base
            panel.set_uv_config(_normalize_uv_config(uv))
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "set",
                "entries_added": len(new_entries),
                "busbars_total": len(base),
                "busbars": base,
            }
        return invoke(_set)

    @mcp.tool()
    def delete_uv_busbar(point_id: str, phase: str) -> dict:
        """Phasenschiene aus einer UV-Unterverteilung entfernen.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            phase: Phasenbezeichnung der zu löschenden Schiene (z.B. 'L1')
        """
        def _del():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if panel.get_ap_type() != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            phase_s = str(phase or "").strip()
            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            uv = dict(ep.get("uv_config") or {})
            existing = list(uv.get("busbars", []) or [])
            new_busbars = [bb for bb in existing if str(bb.get("phase", "")) != phase_s]
            removed = len(existing) - len(new_busbars)
            uv["busbars"] = new_busbars
            panel.set_uv_config(_normalize_uv_config(uv))
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "deleted" if removed > 0 else "not_found",
                "phase": phase_s,
                "removed_count": removed,
            }
        return invoke(_del)

    @mcp.tool()
    def bulk_set_uv_busbars(
        point_id: str,
        busbars: list[dict],
        replace_all: bool = True,
    ) -> dict:
        """Mehrere Phasenschienen einer UV auf einmal setzen.

        Unterstützt mehrere Einträge pro Phase (nicht-zusammenhängende
        Bereiche) und te_ranges als Alternative zu te_start/te_end.

        Args:
            point_id: ID des UV-Anschlusspunkts (z.B. 'AP-1')
            busbars: Liste von Phasenschienen, jede mit:
                phase (Pflicht),
                te_start + te_end ODER te_ranges (Pflicht),
                color (optional, Standard = Phasenfarbe).
                te_ranges: [[start, end], ...] für nicht-zusammenhängende
                Bereiche derselben Phase.
            replace_all: True (Standard) = alle bisherigen Phasenschienen
                ersetzen. False = vorhandene behalten, neue ergänzen.

        Beispiel (klassisch):
            bulk_set_uv_busbars('AP-1', [
                {'phase': 'L1', 'te_start': 1, 'te_end': 6},
                {'phase': 'L2', 'te_start': 7, 'te_end': 12},
            ])

        Beispiel (te_ranges für nicht-zusammenhängende Bereiche):
            bulk_set_uv_busbars('AP-1', [
                {'phase': 'L1', 'te_ranges': [[15,16],[28,28],[39,39]]},
                {'phase': 'L2', 'te_ranges': [[17,18],[40,40]]},
            ])
        """
        _PHASE_COLORS = {
            "L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5",
            "N": "#1565c0", "PE": "#558b2f", "L": "#ff7043",
        }

        def _bulk():
            panel = window.param_panel.elec_point_panels.get(point_id)
            if not panel:
                return {"error": f"Anschlusspunkt '{point_id}' nicht gefunden."}
            if panel.get_ap_type() != "uv":
                return {"error": f"AP '{point_id}' ist kein UV-Punkt."}

            if not isinstance(busbars, list):
                return {"error": "busbars muss eine Liste sein."}

            p = window.param_panel.to_dict()
            ep = p.get("elec_points", {}).get(point_id, {})
            uv = dict(ep.get("uv_config") or {})
            base_busbars = [] if replace_all else list(uv.get("busbars", []) or [])

            errors: list[str] = []
            for idx, bb in enumerate(busbars):
                if not isinstance(bb, dict):
                    errors.append(f"busbars[{idx}] muss ein Objekt sein.")
                    continue
                phase_s = str(bb.get("phase", "") or "").strip()
                if not phase_s:
                    errors.append(f"busbars[{idx}].phase darf nicht leer sein.")
                    continue
                col = str(bb.get("color", "") or "").strip() or _PHASE_COLORS.get(phase_s, "#888888")

                # Support te_ranges
                bb_te_ranges = bb.get("te_ranges")
                if bb_te_ranges is not None:
                    if not isinstance(bb_te_ranges, list) or not bb_te_ranges:
                        errors.append(f"busbars[{idx}].te_ranges muss nicht-leer sein.")
                        continue
                    valid = True
                    for ridx, rng in enumerate(bb_te_ranges):
                        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
                            errors.append(f"busbars[{idx}].te_ranges[{ridx}] muss [start, end] sein.")
                            valid = False
                            break
                        try:
                            r_s, r_e = int(rng[0]), int(rng[1])
                        except (TypeError, ValueError):
                            errors.append(f"busbars[{idx}].te_ranges[{ridx}] ungültig.")
                            valid = False
                            break
                        if r_s < 1 or r_e < r_s:
                            errors.append(f"busbars[{idx}].te_ranges[{ridx}]: ({r_s},{r_e}) ungültig.")
                            valid = False
                            break
                        base_busbars.append({
                            "phase": phase_s, "color": col,
                            "te_start": r_s, "te_end": r_e,
                        })
                    if not valid:
                        continue
                else:
                    # Classic te_start/te_end
                    try:
                        te_s = int(bb.get("te_start", 0) or 0)
                        te_e = int(bb.get("te_end", 0) or 0)
                    except (TypeError, ValueError):
                        errors.append(f"busbars[{idx}]: te_start/te_end ungültig.")
                        continue
                    if te_s < 1 or te_e < te_s:
                        errors.append(f"busbars[{idx}]: te_start={te_s} te_end={te_e} ungültig.")
                        continue
                    base_busbars.append({
                        "phase": phase_s,
                        "color": col,
                        "te_start": te_s,
                        "te_end": te_e,
                    })

            if errors:
                return {"error": "; ".join(errors)}

            base_busbars.sort(key=lambda b: b["te_start"])
            uv["busbars"] = base_busbars
            panel.set_uv_config(_normalize_uv_config(uv))
            window.canvas.update()
            window._dirty = True
            window._update_title()
            return {
                "point_id": point_id,
                "status": "configured",
                "busbars_count": len(base_busbars),
                "busbars": base_busbars,
            }
        return invoke(_bulk)

    @mcp.tool()
    def get_rooms_with_aps() -> list[dict]:
        """Räume mit ihren zugeordneten Anschlusspunkten.

        Gibt für jeden Elektro-Raum eine Liste der darin
        enthaltenen APs zurück — nützlich um zu verstehen welche
        Geräte in welchem Raum geplant werden sollen.

        Returns:
            Liste von Dicts: room_id, room_name, floor_plan_id,
            aps (Liste mit point_id, name, ap_type, builtin_symbol,
            position, height_from_floor).
            Zusätzlich: unassigned_aps (APs ohne Raum).
        """
        def _read():
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()

            # room_id → set of point_ids (from canvas room assignment)
            room_to_aps: dict[str, list[str]] = {}
            for rid in p.get("elec_rooms", {}):
                room_to_aps[rid] = []

            # Use the window's room-assignment helper if available
            assigned: set[str] = set()
            try:
                mapping = window._elec_point_room_map  # {ap_id: room_id}
                for ap_id, rid in mapping.items():
                    if rid in room_to_aps:
                        room_to_aps[rid].append(ap_id)
                        assigned.add(ap_id)
            except AttributeError:
                pass

            def _ap_summary(pid: str) -> dict:
                ep = p.get("elec_points", {}).get(pid, {})
                return {
                    "point_id": pid,
                    "name": ep.get("name", pid),
                    "ap_type": ep.get("ap_type", "standard"),
                    "builtin_symbol": ep.get("builtin_symbol", ""),
                    "position": ep.get("position", ""),
                    "height_from_floor": ep.get("height_from_floor", 0),
                }

            result = []
            for rid, rdata in p.get("elec_rooms", {}).items():
                result.append({
                    "room_id": rid,
                    "room_name": rdata.get("name", rid),
                    "floor_plan_id": rdata.get("floor_plan_id", ""),
                    "ap_count": len(room_to_aps[rid]),
                    "aps": [_ap_summary(pid) for pid in room_to_aps[rid]],
                })

            all_ap_ids = set(p.get("elec_points", {}).keys())
            unassigned = [_ap_summary(pid)
                          for pid in sorted(all_ap_ids - assigned)]
            return {
                "rooms": result,
                "unassigned_aps": unassigned,
                "unassigned_count": len(unassigned),
            }

        return invoke(_read)

    @mcp.tool()
    def get_floor_plan_info(
        floor_plan_id: str = "",
    ) -> dict:
        """Grundriss-Informationen: Maßstab, Abmessungen, Koordinatenhilfe.

        Gibt mm_per_px, reale Abmessungen und Umrechnungsformeln
        zurück — nötig um Pixelkoordinaten für add_circuit /
        add_elec_point korrekt zu berechnen.

        Args:
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1').
                Leer = erster/einziger Grundriss.
        """
        def _read():
            p = window.param_panel.to_dict()
            fps = p.get("floorplans", {})
            if not fps:
                return {"error": "Keine Grundrisse im Projekt."}

            if floor_plan_id:
                fid = floor_plan_id
                if fid not in fps:
                    return {"error": f"Grundriss '{fid}' nicht gefunden."}
            else:
                order = p.get("floorplans_order") or list(fps.keys())
                fid = order[0] if order else list(fps.keys())[0]

            fdata = fps[fid]
            layer = window.canvas._floor_plans.get(fid)
            mpp = layer.mm_per_px if layer else fdata.get("mm_per_px", 1.0)

            # Get canvas image dimensions
            img_w_px = img_h_px = 0
            if layer and getattr(layer, "image", None) is not None:
                img_w_px = layer.image.width()
                img_h_px = layer.image.height()

            real_w_mm = img_w_px * mpp if img_w_px else 0
            real_h_mm = img_h_px * mpp if img_h_px else 0

            return {
                "floor_plan_id": fid,
                "name": fdata.get("name", fid),
                "mm_per_px": mpp,
                "image_width_px": img_w_px,
                "image_height_px": img_h_px,
                "real_width_mm": round(real_w_mm, 1),
                "real_height_mm": round(real_h_mm, 1),
                "real_width_m": round(real_w_mm / 1000, 2),
                "real_height_m": round(real_h_mm / 1000, 2),
                "offset_x": layer.offset_x if layer else 0,
                "offset_y": layer.offset_y if layer else 0,
                "ref_length_mm": fdata.get("ref_length_mm", 0),
                "hint_px_to_mm": f"real_mm = pixel * {mpp:.4f}",
                "hint_mm_to_px": f"pixel = real_mm / {mpp:.4f}",
                "hint_example": (
                    f"5000 mm = {5000 / mpp:.0f} px"
                    if mpp > 0 else "mm_per_px not calibrated"
                ),
            }

        return invoke(_read)

    @mcp.tool()
    def get_heating_load_summary() -> dict:
        """Heizlast-Zusammenfassung für alle Heizkreise.

        Berechnet alle Heizkreise neu und gibt aggregierte Werte
        zurück: Gesamtleistung, Gesamtvolumenstrom, Gesamtrohrlänge
        sowie eine Übersicht je Heizkreis.

        Nützlich um die normgerechte Auslegung zu prüfen.
        """
        def _calc():
            from logic.heating_calc import HeatingCalc
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            t_supply = p.get("t_supply", 35.0)
            t_return = p.get("t_return", 30.0)
            t_norm = p.get("t_norm_outdoor", -12.0)

            total_power_w = 0.0
            total_flow_lmin = 0.0
            total_pipe_m = 0.0
            circuits_summary = []

            for cid, cdata in p.get("circuits", {}).items():
                polygon = c.get("polygons", {}).get(cid, [])
                if not polygon:
                    continue
                try:
                    calc = HeatingCalc(
                        polygon=polygon,
                        t_supply=t_supply,
                        t_return=t_return,
                        t_room=cdata.get("room_temp", 20.0),
                        spacing_mm=cdata.get("spacing", 150.0),
                        diameter_mm=cdata.get("diameter", 16.0),
                        wall_dist_mm=cdata.get("wall_dist", 200.0),
                        floor_covering=cdata.get("floor_covering", "Fliesen / Keramik"),
                        mm_per_px=window.canvas._mm_per_px,
                    )
                    res = calc.calculate()
                    power = res.get("power_w", 0.0)
                    flow = res.get("volume_flow_lmin", 0.0)
                    pipe = res.get("pipe_length_m", 0.0)
                    total_power_w += power
                    total_flow_lmin += flow
                    total_pipe_m += pipe
                    circuits_summary.append({
                        "circuit_id": cid,
                        "name": cdata.get("name", cid),
                        "power_w": round(power, 1),
                        "q_wm2": round(res.get("q_wm2", 0.0), 1),
                        "area_m2": round(res.get("area_m2", 0.0), 2),
                        "volume_flow_lmin": round(flow, 3),
                        "pressure_drop_mbar": round(res.get("pressure_drop_mbar", 0.0), 1),
                        "pipe_length_m": round(pipe, 1),
                        "room_temp": cdata.get("room_temp", 20.0),
                        "floor_covering": cdata.get("floor_covering", ""),
                    })
                except Exception as e:
                    circuits_summary.append({
                        "circuit_id": cid,
                        "name": cdata.get("name", cid),
                        "error": str(e),
                    })

            return {
                "t_supply": t_supply,
                "t_return": t_return,
                "t_norm_outdoor": t_norm,
                "circuit_count": len(circuits_summary),
                "total_power_w": round(total_power_w, 1),
                "total_power_kw": round(total_power_w / 1000, 3),
                "total_volume_flow_lmin": round(total_flow_lmin, 3),
                "total_pipe_length_m": round(total_pipe_m, 1),
                "circuits": circuits_summary,
            }

        return invoke(_calc)

    @mcp.tool()
    def get_cable_length_summary() -> dict:
        """Kabellängen-Zusammenfassung nach Typ und gesamt.

        Gibt Gesamtlängen gruppiert nach Leitungstyp zurück —
        nützlich für Materiallisten und Kostenschätzungen.
        """
        def _calc():
            from math import hypot
            p = window.param_panel.to_dict()
            c = window.canvas.to_dict()
            mpp = window.canvas._mm_per_px

            cables = p.get("elec_cables", {})
            canvas_cables = c.get("elec_cables", {})

            total_m = 0.0
            by_type: dict[str, float] = {}
            details: list[dict] = []

            for cid, cdata in cables.items():
                points = canvas_cables.get(cid, [])
                length_m = 0.0
                for i in range(1, len(points)):
                    dx = points[i][0] - points[i - 1][0]
                    dy = points[i][1] - points[i - 1][1]
                    length_m += hypot(dx, dy) * mpp / 1000.0

                ctype = cdata.get("cable_type", cdata.get("name", cid))
                total_m += length_m
                by_type[ctype] = by_type.get(ctype, 0.0) + length_m
                details.append({
                    "cable_id": cid,
                    "name": cdata.get("name", cid),
                    "type": ctype,
                    "length_m": round(length_m, 2),
                    "start_ap": cdata.get("start_ap_id", ""),
                    "end_ap": cdata.get("end_ap_id", ""),
                })

            return {
                "total_length_m": round(total_m, 2),
                "cable_count": len(cables),
                "by_type": {k: round(v, 2)
                            for k, v in sorted(by_type.items())},
                "cables": sorted(details, key=lambda x: x["type"]),
            }

        return invoke(_calc)

    @mcp.tool()
    def coordinate_convert(
        floor_plan_id: str,
        points_mm: list[list[float]],
    ) -> dict:
        """Reale Koordinaten (mm) in Canvas-Pixel umrechnen.

        Unverzichtbar um Polygone, Punkte und Routen für add_circuit,
        set_circuit_polygon, add_elec_point etc. korrekt zu berechnen.
        Gibt für jeden Eingabepunkt [x_px, y_px] zurück.

        Voraussetzung: Der Grundriss muss kalibriert sein (ref_line gesetzt).
        Die Formel lautet: px = mm / mm_per_px.

        Args:
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1').
                           Leer = erster verfügbarer Grundriss.
            points_mm: Liste von [x_mm, y_mm]-Koordinaten relativ zur
                       oberen linken Ecke des Grundrissbildes.
                       Beispiel: [[500, 1000], [3200, 1000]]
        Returns:
            Dict mit mm_per_px, scale_info und
            points_px (Liste von [x_px, y_px]).
        """
        def _conv():
            p = window.param_panel.to_dict()
            fps = p.get("floorplans", {})

            fp_id = floor_plan_id
            if not fp_id:
                order = p.get("floorplans_order", [])
                fp_id = order[0] if order else (next(iter(fps), ""))

            fp = fps.get(fp_id)
            if fp is None:
                return {"error": f"Grundriss '{fp_id}' nicht gefunden. "
                                 f"Verfügbar: {list(fps.keys())}"}

            mpp = float(fp.get("mm_per_px", 0) or 0)
            if mpp <= 0:
                return {
                    "error": "Grundriss nicht kalibriert (mm_per_px = 0). "
                             "Bitte zuerst eine Referenzlinie setzen."
                }

            offset_x = float(fp.get("offset_x", 0) or 0)
            offset_y = float(fp.get("offset_y", 0) or 0)

            result_px: list[list[float]] = []
            for pt in points_mm:
                if len(pt) < 2:
                    return {"error": f"Ungültiger Punkt: {pt}. Erwartet [x_mm, y_mm]."}
                x_px = pt[0] / mpp + offset_x
                y_px = pt[1] / mpp + offset_y
                result_px.append([round(x_px, 1), round(y_px, 1)])

            return {
                "floor_plan_id": fp_id,
                "mm_per_px": mpp,
                "offset_x_px": offset_x,
                "offset_y_px": offset_y,
                "points_px": result_px,
            }

        return invoke(_conv)

    @mcp.tool()
    def get_tool_overview() -> dict:
        """Alle verfügbaren MCP-Tools gruppiert nach Kategorie.

        Gibt eine Übersicht zurück, ohne dass der Agent alle Tool-
        Definitionen einzeln lesen muss. Als Einstieg empfohlen.
        """
        return {
            "resources": [
                "hrp://schema            – Vollständiges JSON-Schema der HRP-Datei",
                "hrp://instructions      – Agenten-Anleitung (Koordinatensystem, Formeln, …)",
                "hrp://uv_device_types   – Gültige Gerätetypen für UV-Slots",
                "hrp://builtin_symbols   – Verfügbare Icon-Symbole für Anschlusspunkte",
                "hrp://ap_types          – Gültige ap_type-Werte (standard/uv/up_distribution)",
            ],
            "read": [
                "get_project_summary     – Einstieg: Zähler + globale Parameter",
                "get_project_json        – Vollständige Projektdaten als JSON",
                "list_circuits           – Alle Heizkreise mit Parametern",
                "list_elec_points        – Alle Elektro-Anschlusspunkte",
                "list_elec_cables        – Alle Leitungen/Kabel",
                "list_elec_rooms         – Alle Elektro-Räume",
                "list_hkvs               – Alle Heizkreisverteiler",
                "list_hkv_lines          – Alle HKV-Verbindungsleitungen",
                "list_floor_plans        – Alle Grundriss-Layer",
                "list_texts              – Alle Text-Annotationen",
                "get_uv_config           – UV-Belegung eines Anschlusspunkts",
                "get_rooms_with_aps      – Räume mit zugehörigen APs",
                "get_floor_plan_info     – Massstab + reale Abmessungen eines Grundrisses",
                "get_heating_load_summary– Heizlast-Übersicht aller Heizkreise",
                "get_cable_length_summary– Kabellängen-Zusammenfassung nach Typ",
            ],
            "heizkreise": [
                "add_circuit             – Neuen Heizkreis mit Polygon anlegen",
                "modify_circuit          – Parameter eines Heizkreises ändern",
                "delete_circuit          – Heizkreis löschen",
                "set_circuit_polygon     – Raumpolygon setzen (Canvas-Pixel)",
                "set_circuit_route       – Manuellen Rohrverlauf setzen",
                "set_supply_line         – Zuleitung zum HKV setzen",
                "calculate_heating       – Heizlast für einen Heizkreis berechnen",
                "calculate_all_circuits  – Heizlast aller Heizkreise berechnen",
                "set_heating_params      – Vorlauf-/Rücklauftemperatur setzen",
            ],
            "elektro": [
                "add_elec_point          – Anschlusspunkt platzieren",
                "modify_elec_point       – AP-Parameter ändern",
                "delete_elec_point       – AP löschen",
                "add_elec_cable          – Leitung zwischen zwei APs anlegen",
                "modify_elec_cable       – Leitungsparameter ändern",
                "delete_elec_cable       – Leitung löschen",
                "add_elec_room           – Elektro-Raum-Polygon anlegen",
                "modify_elec_room        – Raum-Parameter ändern",
                "delete_elec_room        – Raum löschen",
            ],
            "uv_unterverteilung": [
                "configure_uv_distribution  – UV-Layout (Raster + alle Slots) setzen",
                "clear_uv_distribution      – UV-Belegung leeren",
                "get_uv_config              – Aktuellen UV-Zustand lesen",
                "set_uv_slot                – Einzelnen TE-Slot setzen/überschreiben",
                "delete_uv_slot             – Einzelnen TE-Slot löschen",
                "bulk_set_uv_slots          – Mehrere Slots auf einmal setzen (effizient)",
                "find_free_uv_slots         – Freie TE-Plätze in einer UV auflisten",
                "get_uv_busbars             – Phasenschienen einer UV lesen (mit te_ranges-Gruppierung)",
                "set_uv_busbar              – Phasenschiene hinzufügen/ersetzen (unterstützt te_ranges)",
                "delete_uv_busbar           – Phasenschiene entfernen",
                "bulk_set_uv_busbars        – Mehrere Phasenschienen auf einmal setzen (unterstützt te_ranges)",
            ],
            "hkv": [
                "add_hkv                 – Heizkreisverteiler platzieren",
                "modify_hkv              – HKV-Parameter ändern",
                "delete_hkv              – HKV löschen",
                "add_hkv_line            – Verbindungsleitung zwischen HKVs",
                "modify_hkv_line         – Leitungsparameter ändern",
                "delete_hkv_line         – Verbindungsleitung löschen",
            ],
            "grundriss": [
                "add_floor_plan          – Neuen Grundriss-Layer hinzufügen",
                "modify_floor_plan       – Grundriss-Parameter ändern (Massstab etc.)",
                "delete_floor_plan       – Grundriss-Layer entfernen",
                "coordinate_convert      – mm → Canvas-Pixel umrechnen (WICHTIG)",
                "get_floor_plan_info     – Massstab + Abmessungen lesen",
            ],
            "projekt": [
                "save_project            – Projekt speichern",
                "validate_project        – Schema + Konsistenz prüfen",
                "add_text                – Text-Annotation hinzufügen",
                "modify_text             – Text-Annotation ändern",
                "delete_text             – Text-Annotation löschen",
            ],
            "workflow_empfehlung": [
                "1. get_tool_overview() oder get_project_summary() als Einstieg",
                "2. list_floor_plans() + get_floor_plan_info() für Massstab",
                "3. coordinate_convert() um mm → px zu wandeln",
                "4. add_circuit() / add_elec_point() mit berechneten Pixelkoordinaten",
                "5. calculate_all_circuits() für Heizlast-Prüfung",
                "6. validate_project() + save_project()",
            ],
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
    import sys as _sys

    # Bei frozen EXE: Logger in Datei schreiben (stderr ist unsichtbar)
    if getattr(_sys, 'frozen', False):
        import logging as _logging
        import os as _os
        _appdata = Path(_os.environ.get("LOCALAPPDATA", Path.home()))
        _log_dir = _appdata / "HRouting"
        _log_dir.mkdir(parents=True, exist_ok=True)
        log_path = _log_dir / "hrouting_mcp.log"
        _fh = _logging.FileHandler(str(log_path), encoding="utf-8")
        _fh.setLevel(_logging.DEBUG)
        _fh.setFormatter(_logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(_fh)
        logger.setLevel(_logging.DEBUG)

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
            # uvicorn.config.Config bindet LOGGING_CONFIG als Default-Parameter
            # bei der Klassendefinition (log_config=LOGGING_CONFIG). Ein einfaches
            # Neusetzen der Modulvariable ändert diesen Default nicht mehr.
            # In-place Mutation des Dicts greift hingegen, da der Default-Parameter
            # weiterhin auf dasselbe Dict-Objekt zeigt.
            try:
                import uvicorn.config as _uvc
                _uvc.LOGGING_CONFIG.clear()
                _uvc.LOGGING_CONFIG.update({
                    "version": 1,
                    "disable_existing_loggers": False,
                    "handlers": {},
                    "loggers": {},
                })
            except Exception:
                pass
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
