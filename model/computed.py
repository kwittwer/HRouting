"""Abgeleitete Kennwerte eines Elements (read-only Anzeigen).

Kapselt die Berechnungen aus :mod:`logic.heating_calc` und einfache
Geometrieauswertungen, damit die Eigenschaften-Editoren sie einheitlich
abfragen können.
"""

from __future__ import annotations

import math
from typing import Any

from .document import Document
from .elements import (
    Circuit,
    ElecCable,
    ElecRoom,
    Element,
    FloorPlan,
    HkvLine,
)


def _points(value: Any) -> list[tuple[float, float]]:
    if not value:
        return []
    result: list[tuple[float, float]] = []
    for entry in value:
        try:
            result.append((float(entry[0]), float(entry[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return result


def polygon_area_px2(points: list[tuple[float, float]]) -> float:
    """Fläche eines Polygons in Pixel² (Gaußsche Trapezformel)."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polyline_length_px(points: list[tuple[float, float]], closed: bool = False) -> float:
    """Länge eines Linienzugs in Pixeln."""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        total += math.hypot(x2 - x1, y2 - y1)
    if closed and len(points) > 2:
        x1, y1 = points[-1]
        x2, y2 = points[0]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def mm_per_px_for(document: Document, element: Element) -> float:
    """Maßstab des Grundrisses, zu dem ein Element gehört."""
    fp_id = element.floor_plan_id or document.active_floorplan_id
    floor = document.floorplans.get(fp_id)
    if floor is None and document.floorplan_order:
        floor = document.floorplans.get(document.floorplan_order[0])
    if floor is None:
        return 1.0
    value = float(floor.layer.get("mm_per_px", 1.0) or 1.0)
    return value if value > 0 else 1.0


def _format_number(value: float, decimals: int, unit: str) -> str:
    return f"{value:.{decimals}f} {unit}".strip()


def computed_values(document: Document, element: Element) -> dict[str, str]:
    """Alle abgeleiteten Kennwerte eines Elements als Anzeigetexte."""
    if isinstance(element, Circuit):
        return _circuit_values(document, element)
    if isinstance(element, ElecCable):
        return _cable_values(document, element)
    if isinstance(element, ElecRoom):
        return _room_values(document, element)
    if isinstance(element, HkvLine):
        return _hkv_line_values(document, element)
    if isinstance(element, FloorPlan):
        return {"mm_per_px": f"{float(element.layer.get('mm_per_px', 1.0)):.3f} mm/px"}
    return {}


def _circuit_values(document: Document, circuit: Circuit) -> dict[str, str]:
    mm_per_px = mm_per_px_for(document, circuit)
    polygon = _points(circuit.polygon)
    route = _points(circuit.route)
    supply = _points(circuit.supply_line)

    area_m2 = polygon_area_px2(polygon) * (mm_per_px**2) / 1_000_000.0
    perimeter_m = polyline_length_px(polygon, closed=True) * mm_per_px / 1000.0
    pipe_length_m = polyline_length_px(route) * mm_per_px / 1000.0
    supply_length_m = polyline_length_px(supply) * mm_per_px / 1000.0
    # Zuleitung wird hin und zurück verlegt
    total_length_m = pipe_length_m + supply_length_m * 2.0

    values = {
        "area_m2": _format_number(area_m2, 2, "m²"),
        "perimeter_m": _format_number(perimeter_m, 2, "m"),
        "pipe_length_m": _format_number(pipe_length_m, 2, "m"),
        "supply_length_m": _format_number(supply_length_m, 2, "m"),
        "total_length_m": _format_number(total_length_m, 2, "m"),
        "power_w": "–",
        "q_wm2": "–",
        "volume_flow_lmin": "–",
        "pressure_drop_mbar": "–",
    }

    if area_m2 <= 0:
        return values

    try:
        from logic.heating_calc import FLOOR_COVERINGS, calc_circuit  # noqa: PLC0415

        covering = circuit.floor_covering or ""
        r_lambda_b = FLOOR_COVERINGS.get(covering, 0.0)
        result = calc_circuit(
            t_supply=float(document.settings.get("t_supply", 35.0)),
            t_return=float(document.settings.get("t_return", 30.0)),
            t_room=float(circuit.room_temp or 20.0),
            spacing_cm=float(circuit.spacing or 150.0) / 10.0,
            r_lambda_b=r_lambda_b,
            area_m2=area_m2,
            pipe_length_m=pipe_length_m,
            outer_diameter_mm=float(circuit.diameter or 16.0),
            total_pipe_length_m=total_length_m or None,
        )
    except Exception:  # pragma: no cover - Berechnung darf die UI nie stoppen
        return values

    values["power_w"] = _format_number(result["power_w"], 0, "W")
    values["q_wm2"] = _format_number(result["q_wm2"], 1, "W/m²")
    values["volume_flow_lmin"] = _format_number(result["volume_flow_lmin"], 2, "l/min")
    values["pressure_drop_mbar"] = _format_number(result["pressure_drop_mbar"], 1, "mbar")
    return values


def _cable_values(document: Document, cable: ElecCable) -> dict[str, str]:
    mm_per_px = mm_per_px_for(document, cable)
    length_m = polyline_length_px(_points(cable.path)) * mm_per_px / 1000.0

    def _ap_name(ap_id: str) -> str:
        if not ap_id:
            return "–"
        point = document.elements["elec_points"].get(ap_id)
        if point is None:
            return ap_id
        return point.name or ap_id

    return {
        "length_m": _format_number(length_m, 2, "m"),
        "start_ap_name": _ap_name(cable.start_ap),
        "end_ap_name": _ap_name(cable.end_ap),
    }


def _room_values(document: Document, room: ElecRoom) -> dict[str, str]:
    mm_per_px = mm_per_px_for(document, room)
    area_m2 = polygon_area_px2(_points(room.polygon)) * (mm_per_px**2) / 1_000_000.0
    return {"area_m2": _format_number(area_m2, 2, "m²")}


def _hkv_line_values(document: Document, line: HkvLine) -> dict[str, str]:
    mm_per_px = mm_per_px_for(document, line)
    length_m = polyline_length_px(_points(line.path)) * mm_per_px / 1000.0
    return {"length_m": _format_number(length_m, 2, "m")}


def heating_length_overview(document: Document) -> tuple[list[dict[str, float | str]], float, float]:
    """Collect heating rows for the length / hydraulics overview."""
    from logic.heating_calc import FLOOR_COVERINGS, calc_circuit  # noqa: PLC0415

    circuits = document.elements.get("circuits", {})
    t_supply = float(document.settings.get("t_supply", 35.0))
    t_return = float(document.settings.get("t_return", 30.0))

    rows: list[dict[str, float | str]] = []
    for cid, circuit in sorted(circuits.items()):
        scale = mm_per_px_for(document, circuit)
        polygon = circuit.polygon or []
        route = circuit.route or []
        supply = circuit.supply_line or []

        area_m2 = 0.0
        if polygon and len(polygon) >= 3:
            area_px2 = polygon_area_px2([(float(x), float(y)) for x, y in polygon])
            area_m2 = area_px2 * (scale ** 2) / 1_000_000.0

        perimeter_m = 0.0
        if polygon and len(polygon) >= 2:
            perimeter_px = polyline_length_px([(float(x), float(y)) for x, y in polygon], closed=True)
            perimeter_m = perimeter_px * scale / 1000.0

        route_m = polyline_length_px([(float(x), float(y)) for x, y in route]) * scale / 1000.0
        supply_m = polyline_length_px([(float(x), float(y)) for x, y in supply]) * scale / 1000.0
        total_m = route_m + supply_m * 2.0

        floor_name = str(circuit.floor_covering or "Fliesen / Keramik")
        r_lambda = FLOOR_COVERINGS.get(floor_name, 0.01)
        room_temp = float(circuit.room_temp or 20.0)
        diameter_mm = float(circuit.diameter or 16.0)
        spacing_cm = float(circuit.spacing or 150.0) / 10.0

        try:
            hc = calc_circuit(
                t_supply=t_supply,
                t_return=t_return,
                t_room=room_temp,
                spacing_cm=spacing_cm,
                r_lambda_b=r_lambda,
                area_m2=area_m2,
                pipe_length_m=route_m,
                outer_diameter_mm=diameter_mm,
                total_pipe_length_m=total_m,
            )
            power_w = float(hc.get("power_w", 0.0) or 0.0)
            q_wm2 = float(hc.get("q_wm2", 0.0) or 0.0)
            volume_flow_lmin = float(hc.get("volume_flow_lmin", 0.0) or 0.0)
            pressure_drop_mbar = float(hc.get("pressure_drop_mbar", 0.0) or 0.0)
        except Exception:  # pragma: no cover - overview must not fail the UI
            power_w = q_wm2 = volume_flow_lmin = pressure_drop_mbar = 0.0

        rows.append({
            "id": cid,
            "name": str(circuit.name or cid),
            "diameter_mm": diameter_mm,
            "spacing_mm": float(circuit.spacing or 150.0),
            "route_m": route_m,
            "supply_m": supply_m,
            "total_m": total_m,
            "perimeter_m": perimeter_m,
            "area_m2": area_m2,
            "room_temp": room_temp,
            "floor_covering": floor_name,
            "distributor": str(circuit.distributor or ""),
            "power_w": power_w,
            "q_wm2": q_wm2,
            "volume_flow_lmin": volume_flow_lmin,
            "pressure_drop_mbar": pressure_drop_mbar,
        })

    return rows, t_supply, t_return
