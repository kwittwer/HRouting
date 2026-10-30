"""Qt-free photo metadata, stored exclusively in params.photo_documentation."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import PurePosixPath
import re
from uuid import uuid4

from .document import Document
from .elements import ElecRoom


def default_config() -> dict:
    """Create metadata without generating a dictionary or allocating image data."""
    return {
        "version": 1,
        "project_uuid": str(uuid4()),
        "dictionary": {},
        "markers": {},
        "surfaces": {},
        "assignments": {},
        "rooms": {},
        "photos": {},
        "regions": {},
        "measurements": {},
    }


def get_config(document: Document) -> dict:
    """Return an independent copy; callers explicitly commit it to settings."""
    saved = document.settings.get("photo_documentation")
    if saved is None:
        return default_config()
    if not isinstance(saved, dict):
        raise ValueError("photo_documentation must be an object")
    return deepcopy(saved)


def _next_id(entries: dict, prefix: str) -> str:
    numbers = [int(key[len(prefix) + 1:]) for key in entries
               if key.startswith(prefix + "-") and key[len(prefix) + 1:].isdigit()]
    return f"{prefix}-{max(numbers, default=0) + 1}"


def ensure_surfaces(document: Document, config: dict) -> dict:
    """Append room-side surfaces; never move historical surfaces onto new edges.

    All coordinates are plan pixels, not measured surface coordinates. Changed,
    invalid or deleted source rooms leave their previous surfaces marked stale.
    The returned object is the passed config, not an implicit document commit.
    """
    from logic.photo_geometry import _polygon_points

    surfaces = config.setdefault("surfaces", {})
    rooms = document.elements_of(ElecRoom)
    sources = {}
    for room in rooms:
        try:
            polygon = _polygon_points(room.polygon)
        except ValueError:
            continue
        sources[room.id] = (room, polygon)
        config.setdefault("rooms", {}).setdefault(room.id, {"ffb_offset_mm": 0.0})
    for surface in surfaces.values():
        source = sources.get(surface.get("room_id"))
        if (source is None or surface.get("source_polygon") != source[1]
                or surface.get("floor_plan_id", "") != source[0].floor_plan_id):
            surface["status"] = "stale"
            surface["stale_reason"] = "Source room geometry changed, is invalid or was deleted"
    for room_id, (room, polygon) in sources.items():
        specifications = [("wall", [start, polygon[(index + 1) % len(polygon)]], index)
                          for index, start in enumerate(polygon)]
        specifications.extend([("floor", polygon, None), ("ceiling", polygon, None)])
        for kind, points, edge_index in specifications:
            if any(surface.get("room_id") == room_id
                   and surface.get("status") == "current"
                   and surface.get("source_polygon") == polygon
                   and surface.get("kind") == kind
                   and surface.get("source_edge_index") == edge_index
                   for surface in surfaces.values()):
                continue
            surface_id = _next_id(surfaces, "SURF")
            suffix = f"Wall {edge_index + 1}" if kind == "wall" else kind.capitalize()
            surfaces[surface_id] = {
                "id": surface_id, "room_id": room_id,
                "floor_plan_id": room.floor_plan_id,
                "name": f"{room.name or room_id} / {suffix}", "kind": kind,
                "plan_points": deepcopy(points), "source_polygon": deepcopy(polygon),
                "source_edge_index": edge_index, "status": "current",
            }
    return config


def validate_config(config: dict) -> list[str]:
    """Return actionable errors/warnings without importing OpenCV or mutating data.

    Historical AP identities are intentionally not resolved against a Document.
    Stale surfaces and regions remain loadable, but are reported for review.
    """
    from logic.photo_geometry import _matrix, _polygon_points, _points_array

    issues = []
    if not isinstance(config, dict):
        return ["photo_documentation must be an object"]
    if type(config.get("version")) is not int or config.get("version") != 1:
        issues.append("version must be 1")
    if not isinstance(config.get("project_uuid"), str) or not config.get("project_uuid"):
        issues.append("project_uuid is required")
    containers = {}
    for key in ("markers", "surfaces", "assignments", "rooms", "photos", "regions", "measurements"):
        value = config.get(key, {})
        if not isinstance(value, dict):
            issues.append(f"{key} must be an object")
            value = {}
        containers[key] = value

    def finite_values(value: object, path: str) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not finite_number(value):
            issues.append(f"{path}: value must be finite")
        elif isinstance(value, dict):
            for key, child in value.items():
                finite_values(child, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                finite_values(child, f"{path}[{index}]")

    def finite_number(value: object) -> bool:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    def positive(value: object) -> bool:
        return finite_number(value) and value > 0

    def known(key: object, entries: dict) -> bool:
        return isinstance(key, str) and key in entries

    finite_values(config, "photo_documentation")
    surfaces = containers["surfaces"]
    for surface_id, surface in surfaces.items():
        if not isinstance(surface, dict):
            issues.append(f"{surface_id}: surface must be an object")
            continue
        if surface.get("status") != "current":
            issues.append(f"{surface_id}: stale source geometry; review required")
        kind = surface.get("kind")
        if kind not in ("wall", "floor", "ceiling"):
            issues.append(f"{surface_id}: invalid surface kind")
        try:
            _polygon_points(surface.get("source_polygon"))
            points = _points_array(surface.get("plan_points"))
            if kind == "wall":
                if len(points) != 2 or math.dist(points[0], points[1]) <= 1e-8:
                    raise ValueError("wall needs two distinct endpoints")
            else:
                _polygon_points(surface.get("plan_points"))
        except (ValueError, TypeError) as error:
            issues.append(f"{surface_id}: {error}")
    for point_id, surface_id in containers["assignments"].items():
        if not isinstance(point_id, str) or not point_id.startswith("AP-"):
            issues.append(f"{point_id}: assignment needs an AP ID")
        surface = surfaces.get(surface_id) if isinstance(surface_id, str) else None
        if not isinstance(surface, dict) or surface.get("status") != "current":
            issues.append(f"{point_id}: assignment references missing/stale surface")
    for room_id, room in containers["rooms"].items():
        if (not isinstance(room, dict)
            or not isinstance(room.get("ffb_offset_mm"), (int, float))
            or isinstance(room.get("ffb_offset_mm"), bool)):
            issues.append(f"{room_id}: ffb_offset_mm must be numeric")
    registry = containers["markers"]
    used_ids = set()
    for marker_id, marker in registry.items():
        if not isinstance(marker, dict):
            issues.append(f"{marker_id}: marker must be an object")
            continue
        if marker.get("id") != marker_id:
            issues.append(f"{marker_id}: marker id does not match registry key")
        aruco_id = marker.get("aruco_id")
        if not isinstance(aruco_id, int) or isinstance(aruco_id, bool) or aruco_id < 0:
            issues.append(f"{marker_id}: invalid aruco_id")
        elif aruco_id in used_ids:
            issues.append(f"{marker_id}: duplicate aruco_id")
        else:
            used_ids.add(aruco_id)
        if not positive(marker.get("side_mm")):
            issues.append(f"{marker_id}: side_mm must be positive and finite")
        surface_id = marker.get("surface_id", "")
        if not isinstance(surface_id, str) or (surface_id and not known(surface_id, surfaces)):
            issues.append(f"{marker_id}: unknown surface")
        reference = marker.get("reference")
        if reference is not None:
            try:
                if not surface_id or not isinstance(reference, dict):
                    raise ValueError("reference requires a surface and an object")
                _points_array([reference.get("origin_mm")])
                rotation = reference.get("rotation_deg")
                if not finite_number(rotation):
                    raise ValueError("reference rotation_deg must be finite")
            except (TypeError, ValueError) as error:
                issues.append(f"{marker_id}: {error}")
    dictionary = config.get("dictionary", {})
    if not isinstance(dictionary, dict):
        issues.append("dictionary must be an object")
    if dictionary or registry:
        try:
            import numpy as np

            if not isinstance(dictionary, dict):
                raise ValueError("dictionary must be an object")
            marker_size = dictionary.get("marker_size")
            if type(marker_size) is not int or marker_size not in (5, 6):
                raise ValueError("dictionary marker_size must be 5 or 6")
            raw_bytes = np.asarray(dictionary.get("bytes_list"))
            if (raw_bytes.ndim != 3 or not 1 <= len(raw_bytes) <= 2048
                    or raw_bytes.shape[1:] != ((marker_size ** 2 + 7) // 8, 4)
                    or raw_bytes.dtype.kind not in "iu" or raw_bytes.size == 0
                    or np.any(raw_bytes < 0) or np.any(raw_bytes > 255)):
                raise ValueError("invalid dictionary bytes_list shape or byte values")
            if used_ids and max(used_ids) >= len(raw_bytes):
                raise ValueError("marker ID exceeds dictionary capacity")
            correction = dictionary.get("max_correction_bits")
            if (not isinstance(correction, int) or isinstance(correction, bool)
                    or not 0 <= correction <= marker_size ** 2 // 2):
                raise ValueError("invalid dictionary max_correction_bits")
        except (TypeError, ValueError) as error:
            issues.append(str(error))
    photos = containers["photos"]
    for photo_id, photo in photos.items():
        if not isinstance(photo, dict):
            issues.append(f"{photo_id}: photo must be an object")
            continue
        for dimension in ("width", "height"):
            if not positive(photo.get(dimension)):
                issues.append(f"{photo_id}: {dimension} must be positive")
        path = photo.get("path")
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            issues.append(f"{photo_id}: path must be relative POSIX without traversal")
        digest = photo.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            issues.append(f"{photo_id}: invalid sha256")
    regions = containers["regions"]
    for region_id, region in regions.items():
        if not isinstance(region, dict):
            issues.append(f"{region_id}: region must be an object")
            continue
        if not known(region.get("photo_id"), photos):
            issues.append(f"{region_id}: unknown photo")
        surface_id = region.get("surface_id")
        surface = surfaces.get(surface_id) if isinstance(surface_id, str) else None
        if not isinstance(surface, dict) or surface.get("status") != "current":
            issues.append(f"{region_id}: missing/stale surface")
        elif region.get("source_polygon") and region["source_polygon"] != surface.get("source_polygon"):
            issues.append(f"{region_id}: stale calibration source geometry")
        marker_ids = region.get("marker_ids", [])
        if not isinstance(marker_ids, list) or any(not isinstance(key, str) or key not in registry for key in marker_ids):
            issues.append(f"{region_id}: unknown marker references")
        calibration_ids = region.get("calibration_marker_ids", marker_ids)
        if not isinstance(calibration_ids, list):
            issues.append(f"{region_id}: calibration_marker_ids must be a list")
            calibration_ids = []
        for marker_id in calibration_ids:
            marker = registry.get(marker_id) if isinstance(marker_id, str) else None
            if not isinstance(marker, dict):
                issues.append(f"{region_id}: unknown calibration marker")
                continue
            marker_point_id = marker.get("point_id", "")
            marker_surface = marker.get("surface_id") or (
                containers["assignments"].get(marker_point_id) if isinstance(marker_point_id, str) else None)
            if marker_surface != surface_id:
                issues.append(f"{region_id}: calibration marker belongs to another surface")
            if region.get("absolute_reference") and marker.get("reference") is None:
                issues.append(f"{region_id}: absolute calibration requires measured references")
        if region.get("absolute_reference") and not calibration_ids:
            issues.append(f"{region_id}: absolute calibration requires measured reference markers")
        if not isinstance(region.get("absolute_reference"), bool):
            issues.append(f"{region_id}: absolute_reference must be boolean")
        try:
            _matrix(region.get("H"))
        except ValueError as error:
            issues.append(f"{region_id}: {error}")
    for measurement_id, value in containers["measurements"].items():
        if not isinstance(value, dict):
            issues.append(f"{measurement_id}: measurement must be an object")
            continue
        if not known(value.get("region_id"), regions):
            issues.append(f"{measurement_id}: unknown region")
        try:
            if len(_points_array(value.get("points"))) != 2:
                raise ValueError("measurement needs two photo points")
        except ValueError as error:
            issues.append(f"{measurement_id}: {error}")
    return issues