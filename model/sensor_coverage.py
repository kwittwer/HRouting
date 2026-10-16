"""Effective AP sensor settings and their floor-plan projection."""

from __future__ import annotations

from math import cos, pi, radians, sin, sqrt, tan
from pathlib import PurePosixPath
from typing import Any, Mapping


SENSOR_FIELDS = frozenset({
    "sensor_type", "sensor_angle_deg", "sensor_cone_angle_deg",
    "sensor_range_mm", "sensor_direction_deg", "sensor_coverage_visible", "sensor_tilt_deg",
})


def sensor_data_for_point(point: Any) -> dict[str, Any]:
    data = dict(point.data)
    for key, geom_key in (("position", "elec_point_position"),
                          ("height_from_floor", "elec_point_height")):
        if data.get(key) in (None, "", []):
            data[key] = point.geom.get(geom_key, data.get(key))
    return data


def sensor_type(data: Mapping[str, Any]) -> str:
    selected = str(data.get("sensor_type") or "auto").strip().lower()
    if selected != "auto":
        return selected
    symbol = str(data.get("builtin_symbol") or "").strip().casefold()
    if not symbol:
        icon_path = str(data.get("icon_path") or "").replace("\\", "/")
        symbol = PurePosixPath(icon_path).stem.casefold()
    return {"bewegungsmelder": "pir", "praesenzmelder": "radar"}.get(symbol, "none")


def effective_sensor_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    kind = sensor_type(data)
    defaults = {
        "sensor_type": str(data.get("sensor_type") or "auto"),
        "sensor_angle_deg": 360.0 if kind == "radar" else 120.0,
        "sensor_cone_angle_deg": 120.0,
        "sensor_range_mm": 6000.0,
        "sensor_direction_deg": 0.0,
        "sensor_tilt_deg": 0.0,
        "sensor_coverage_visible": True,
    }
    for key in SENSOR_FIELDS:
        if key in data:
            defaults[key] = data[key]
    return defaults


def coverage_radius_mm(data: Mapping[str, Any]) -> float | None:
    """Return the floor radius, or None when no valid projection exists."""
    if sensor_type(data) not in {"pir", "radar"}:
        return None
    settings = effective_sensor_fields(data)
    if not settings["sensor_coverage_visible"]:
        return None
    distance = float(settings["sensor_range_mm"])
    position = str(data.get("position") or "").strip().casefold()
    if position == "wand":
        return distance if distance > 0 else None
    if position == "decke":
        height = float(data.get("height_from_floor") or 0.0)
        if height < 1000.0 or distance <= height:
            return None
        angle = float(settings["sensor_cone_angle_deg"])
        if not 0 < angle < 180:
            return None
        if float(settings["sensor_tilt_deg"]) != 0.0:
            return sqrt(distance**2 - height**2)
        return min(height * tan(radians(angle / 2)), sqrt(distance**2 - height**2))
    return None


def ceiling_coverage_polygon_mm(data: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Approximate the finite cone/ground intersection relative to the AP."""
    if str(data.get("position") or "").strip().casefold() != "decke":
        return []
    if coverage_radius_mm(data) is None:
        return []
    settings = effective_sensor_fields(data)
    height = float(data["height_from_floor"])
    distance = float(settings["sensor_range_mm"])
    radius = sqrt(distance**2 - height**2)
    tilt = radians(float(settings["sensor_tilt_deg"]))
    half_angle = radians(float(settings["sensor_cone_angle_deg"]) / 2.0)
    direction = radians(float(settings["sensor_direction_deg"]))
    samples = 180
    polygon = [(radius * cos(2 * pi * index / samples),
                radius * sin(2 * pi * index / samples)) for index in range(samples)]
    for index in range(samples):
        azimuth = 2 * pi * index / samples
        normal_forward = sin(half_angle) * sin(tilt) - cos(half_angle) * cos(tilt) * cos(azimuth)
        normal_side = -cos(half_angle) * sin(azimuth)
        offset = height * (sin(half_angle) * cos(tilt) + cos(half_angle) * sin(tilt) * cos(azimuth))
        clipped = []
        if not polygon:
            return []
        previous = polygon[-1]
        previous_value = normal_forward * previous[0] + normal_side * previous[1] + offset
        for current in polygon:
            value = normal_forward * current[0] + normal_side * current[1] + offset
            if (value >= 0.0) != (previous_value >= 0.0):
                fraction = previous_value / (previous_value - value)
                clipped.append((previous[0] + fraction * (current[0] - previous[0]),
                                previous[1] + fraction * (current[1] - previous[1])))
            if value >= 0.0:
                clipped.append(current)
            previous = current
            previous_value = value
        polygon = clipped
    return [(forward * sin(direction) + side * cos(direction),
             -forward * cos(direction) + side * sin(direction)) for forward, side in polygon]