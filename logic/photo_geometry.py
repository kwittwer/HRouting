"""Planar photo geometry. Photo pixels map to surface mm, x right and y up."""

from __future__ import annotations

from copy import deepcopy
import math

from model.document import Document
from model.elements import ElecPoint, ElecRoom


def _points_array(points: object) -> list[list[float]]:
    try:
        result = [[float(coordinate) for coordinate in point] for point in points]
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("points must contain finite [x, y] pairs") from error
    if any(len(point) != 2 or not all(math.isfinite(value) for value in point) for point in result):
        raise ValueError("points must contain finite [x, y] pairs")
    return result


def _cross(start: list, end: list, point: list) -> float:
    return ((end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0]))


def _segment_distance(point: list, start: list, end: list) -> float:
    length_squared = math.dist(start, end) ** 2
    if length_squared <= 1e-16:
        return math.dist(point, start)
    fraction = sum((point[axis] - start[axis]) * (end[axis] - start[axis])
                   for axis in (0, 1)) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    return math.dist(point, [start[axis] + fraction * (end[axis] - start[axis]) for axis in (0, 1)])


def _polygon_points(points: object) -> list[list[float]]:
    polygon = _points_array(points)
    if len(polygon) > 3 and polygon[0] == polygon[-1]:
        polygon.pop()
    if len(polygon) < 3 or len({tuple(point) for point in polygon}) != len(polygon):
        raise ValueError("polygon needs at least three distinct vertices")
    extent = max(max(point[axis] for point in polygon) - min(point[axis] for point in polygon)
                 for axis in (0, 1))
    tolerance = max(1e-12, extent ** 2 * 1e-12)
    area_twice = sum(_cross(polygon[0], polygon[index], polygon[index + 1])
                     for index in range(1, len(polygon) - 1))
    if abs(area_twice) <= tolerance:
        raise ValueError("polygon is degenerate")
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if math.dist(start, end) <= max(1e-10, extent * 1e-10):
            raise ValueError("polygon has a zero-length edge")
        for other in range(index + 1, len(polygon)):
            if other == index + 1 or (index == 0 and other == len(polygon) - 1):
                continue
            other_start, other_end = polygon[other], polygon[(other + 1) % len(polygon)]
            crosses = (_cross(start, end, other_start), _cross(start, end, other_end),
                       _cross(other_start, other_end, start), _cross(other_start, other_end, end))
            if (crosses[0] * crosses[1] < -tolerance ** 2
                    and crosses[2] * crosses[3] < -tolerance ** 2):
                raise ValueError("polygon self-intersects")
            if any(_segment_distance(vertex, edge_start, edge_end) <= max(1e-10, extent * 1e-10)
                   for vertex, edge_start, edge_end in (
                       (other_start, start, end), (other_end, start, end),
                       (start, other_start, other_end), (end, other_start, other_end))):
                raise ValueError("polygon has overlapping or touching non-adjacent edges")
    return polygon


def _contains(polygon: list, point: list) -> bool:
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _segment_distance(point, start, end) <= 1e-8:
            return True
        if ((start[1] > point[1]) != (end[1] > point[1])
                and point[0] < (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]):
            inside = not inside
    return inside


def nearest_surface(document: Document, config: dict, point_id: str) -> str:
    """Suggest only a unique current surface, within 500 mm for wall points.

    Explicit room_id wins over polygon containment, but not a floor-plan mismatch.
    Wall ties within 5 mm or 5 percent are deliberately left unassigned.
    """
    point = document.get(point_id)
    if not isinstance(point, ElecPoint):
        return ""
    try:
        position = _points_array([point.pos])[0]
    except ValueError:
        return ""
    floor = document.floorplans.get(point.floor_plan_id)
    raw_scale = floor.layer.get("mm_per_px") if floor else None
    try:
        scale = float(raw_scale if raw_scale is not None else document.view.get("mm_per_px", 1.0))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(scale) or scale <= 0:
        return ""
    room_candidates = []
    for room in document.elements_of(ElecRoom):
        if room.floor_plan_id != point.floor_plan_id or (point.room_id and room.id != point.room_id):
            continue
        try:
            polygon = _polygon_points(room.polygon)
        except ValueError:
            continue
        if _contains(polygon, position) or (point.room_id and min(
                _segment_distance(position, start, polygon[(index + 1) % len(polygon)])
                for index, start in enumerate(polygon)) * scale <= 500):
            room_candidates.append((room.id, polygon))
    if len(room_candidates) != 1:
        return ""
    installation = str(point.position).strip().lower()
    kind = {"boden": "floor", "floor": "floor", "decke": "ceiling", "ceiling": "ceiling"}.get(installation, "wall")
    candidates = []
    for surface_id, surface in config.get("surfaces", {}).items():
        if (surface.get("room_id") != room_candidates[0][0] or surface.get("kind") != kind
            or surface.get("source_polygon") != room_candidates[0][1]
                or surface.get("floor_plan_id", "") != point.floor_plan_id
                or surface.get("status") != "current"):
            continue
        if kind != "wall":
            candidates.append((0.0, surface_id))
            continue
        try:
            endpoints = _points_array(surface.get("plan_points"))
            if len(endpoints) != 2:
                continue
            distance = _segment_distance(position, *endpoints) * scale
        except ValueError:
            continue
        if distance <= 500:
            candidates.append((distance, surface_id))
    candidates.sort()
    if not candidates or (len(candidates) > 1 and
                          candidates[1][0] - candidates[0][0] <= max(5.0, candidates[0][0] * 0.05)):
        return ""
    return candidates[0][1]


def _matrix(value: object):
    import numpy as np

    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("H must be a finite nondegenerate 3x3 matrix") from error
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("H must be a finite nondegenerate 3x3 matrix")
    magnitude = float(np.max(np.abs(matrix)))
    if magnitude == 0:
        raise ValueError("H is degenerate")
    matrix = matrix / magnitude
    singular_values = np.linalg.svd(matrix, compute_uv=False)
    if singular_values[-1] <= singular_values[0] * 1e-14:
        raise ValueError("H is degenerate or numerically unstable")
    return matrix


def transform_points(points, H) -> list:
    """Transform finite [x,y] pairs; reject points on the projective horizon."""
    import numpy as np

    matrix = _matrix(H)
    source = _points_array(points)
    if not source:
        return []
    homogeneous = np.column_stack((source, np.ones(len(source)))) @ matrix.T
    denominator = homogeneous[:, 2]
    scale = np.maximum(1.0, np.max(np.abs(homogeneous), axis=1))
    if np.any(np.abs(denominator) <= 1e-12 * scale):
        raise ValueError("point lies on or too close to the projective horizon")
    result = homogeneous[:, :2] / denominator[:, None]
    if not np.isfinite(result).all():
        raise ValueError("transformed points are not finite")
    return result.tolist()


def _check_domain(points: list, matrix) -> None:
    denominators = [matrix[2, 0] * point[0] + matrix[2, 1] * point[1] + matrix[2, 2]
                    for point in points]
    if denominators and min(denominators) <= 0 <= max(denominators):
        raise ValueError("Geometry crosses the projective horizon")


def measurement(region: dict, points) -> dict:
    """Measure exactly two canonical photo pixels, returning signed surface deltas."""
    source = _points_array(points)
    if len(source) != 2:
        raise ValueError("Measurement requires exactly two photo points")
    matrix = _matrix(region.get("H"))
    _check_domain(source, matrix)
    transformed = transform_points(source, matrix)
    delta_x = transformed[1][0] - transformed[0][0]
    delta_y = transformed[1][1] - transformed[0][1]
    warnings = list(region.get("warnings", []))
    if not region.get("absolute_reference", False):
        warnings.append("local_reference_only")
    warnings.extend(["uncalibrated_lens", "unchecked_control_distance"])
    support = region.get("reference_polygon_px")
    if support:
        polygon = _polygon_points(support)
        if any(not _contains(polygon, point) for point in source):
            warnings.append("measurement_extrapolates_reference_area")
    else:
        warnings.append("unknown_reference_support")
    return {"distance_mm": math.hypot(delta_x, delta_y), "dx_mm": delta_x,
            "dy_mm": delta_y, "warnings": list(dict.fromkeys(warnings))}


def _marker_surface_corners(marker: dict, absolute: bool) -> list:
    from .photo_markers import _side

    half_side = _side(marker.get("side_mm")) / 2
    origin = [0.0, 0.0]
    angle = 0.0
    if absolute:
        reference = marker.get("reference")
        if not isinstance(reference, dict):
            raise ValueError("Marker reference must be an object")
        origin = _points_array([reference.get("origin_mm")])[0]
        try:
            angle = math.radians(float(reference["rotation_deg"]))
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Marker reference needs a finite rotation_deg") from error
        if not math.isfinite(angle):
            raise ValueError("Marker reference needs a finite rotation_deg")
    cosine, sine = math.cos(angle), math.sin(angle)
    return [[origin[0] + cosine * horizontal - sine * vertical,
             origin[1] + sine * horizontal + cosine * vertical]
            for horizontal, vertical in ((-half_side, half_side), (half_side, half_side),
                                         (half_side, -half_side), (-half_side, -half_side))]


def _quad(corners: object) -> list:
    polygon = _polygon_points(corners)
    if len(polygon) != 4:
        raise ValueError("Marker requires four corners")
    crosses = [_cross(polygon[index], polygon[(index + 1) % 4], polygon[(index + 2) % 4])
               for index in range(4)]
    if min(abs(value) for value in crosses) < 1e-6 or min(crosses) < 0 < max(crosses):
        raise ValueError("Marker corners must form a convex quadrilateral")
    return polygon


def _area(polygon: list) -> float:
    return abs(sum(_cross(polygon[0], polygon[index], polygon[index + 1])
                   for index in range(1, len(polygon) - 1))) / 2


def analyze_photo(image, config: dict, photo_id='') -> dict:
    """Detect independently calibrated planar regions, without allocating IDs.

    Surveyed references use actual marker CENTER coordinates and mathematical
    counterclockwise rotation in surface mm. Other marker positions, including
    planned AP heights, are never used as calibration correspondences.
    """
    import cv2
    import numpy as np
    from .photo_markers import _image_array, detect_markers

    source_image = _image_array(image)
    height, width = source_image.shape[:2]
    detections = detect_markers(source_image, config)
    result = {"photo_id": photo_id, "width": width, "height": height,
              "detections": detections, "regions": [], "warnings": []}
    groups = {}
    for detection in detections:
        warnings = detection.setdefault("warnings", [])
        marker = config.get("markers", {}).get(detection.get("marker_id"))
        if not isinstance(marker, dict) or warnings:
            continue
        point_id = marker.get("point_id", "")
        assigned_surface = config.get("assignments", {}).get(point_id, "")
        surface_id = marker.get("surface_id", "") or assigned_surface
        detection.update({"point_id": point_id, "surface_id": surface_id})
        if assigned_surface and assigned_surface != surface_id:
            warnings.append("assignment_surface_conflict")
            continue
        surface = config.get("surfaces", {}).get(surface_id)
        if not isinstance(surface, dict):
            warnings.append("unassigned_surface")
            continue
        if surface.get("status") != "current":
            warnings.append("stale_surface_geometry")
            continue
        try:
            corners = _quad(detection.get("corners"))
            if _area(corners) < 16:
                raise ValueError("Marker covers too few photo pixels")
            absolute = marker.get("reference") is not None
            target = _marker_surface_corners(marker, absolute)
        except ValueError as error:
            warnings.append(f"invalid_marker_geometry: {error}")
            continue
        groups.setdefault(surface_id, []).append((detection, marker, corners, target, absolute))
    for surface_id, candidates in groups.items():
        surveyed = [candidate for candidate in candidates if candidate[4]]
        selected = surveyed or [max(candidates, key=lambda candidate: _area(candidate[2]))]
        warnings = ["uncalibrated_lens", "unchecked_control_distance",
                    "extrapolation_outside_reference_area", "planar_projection_only"]
        rejected_ids = []
        if not surveyed:
            warnings.append("local_reference_only")
            if len(candidates) > 1:
                warnings.append("independent_markers_not_jointly_fitted")
        try:
            photo_points = np.asarray([point for candidate in selected for point in candidate[2]], np.float64)
            surface_points = np.asarray([point for candidate in selected for point in candidate[3]], np.float64)
            if len(selected) > 1:
                fitted, inliers = cv2.findHomography(photo_points, surface_points, cv2.RANSAC, 3.0)
                if fitted is None or inliers is None:
                    raise ValueError("Measured references do not support a homography")
                accepted = [candidate for index, candidate in enumerate(selected)
                            if bool(inliers[index * 4:index * 4 + 4].all())]
                rejected_ids = [candidate[0]["marker_id"] for candidate in selected if not any(
                    candidate is accepted_candidate for accepted_candidate in accepted)]
                if not accepted:
                    raise ValueError("No complete measured reference agrees with the fit")
                if rejected_ids:
                    warnings.append("conflicting_measured_references")
                selected = accepted
                photo_points = np.asarray([point for candidate in selected for point in candidate[2]], np.float64)
                surface_points = np.asarray([point for candidate in selected for point in candidate[3]], np.float64)
            fitted, _mask = cv2.findHomography(photo_points, surface_points, 0)
            matrix = _matrix(fitted)
            _check_domain(photo_points.tolist(), matrix)
            projected = np.asarray(transform_points(photo_points.tolist(), matrix))
            residual = float(np.sqrt(np.mean(np.sum((projected - surface_points) ** 2, axis=1))))
            if not math.isfinite(residual) or residual > 3:
                raise ValueError("Measured reference reprojection error exceeds 3 mm")
        except (ValueError, cv2.error) as error:
            result["warnings"].append(f"{surface_id}: calibration_failed: {error}")
            continue
        if len(selected) == 1:
            warnings.append("single_marker_accuracy_not_verified")
        minimum_edge = min(math.dist(candidate[2][index], candidate[2][(index + 1) % 4])
                           for candidate in selected for index in range(4))
        if minimum_edge < 60:
            warnings.append("low_marker_resolution")
        if any(min(math.dist(candidate[2][index], candidate[2][(index + 1) % 4]) for index in range(4))
               / max(math.dist(candidate[2][index], candidate[2][(index + 1) % 4]) for index in range(4)) < 0.2
               for candidate in selected):
            warnings.append("extreme_perspective")
        support = cv2.convexHull(photo_points.astype(np.float32)).reshape(-1, 2).astype(float).tolist()
        result["regions"].append({
            "photo_id": photo_id, "surface_id": surface_id,
            "marker_ids": [candidate[0]["marker_id"] for candidate in candidates],
            "calibration_marker_ids": [candidate[0]["marker_id"] for candidate in selected],
            "rejected_marker_ids": rejected_ids,
            "H": matrix.tolist(), "absolute_reference": bool(surveyed),
            "local_origin_marker_id": "" if surveyed else selected[0][0]["marker_id"],
            "reference_polygon_px": support,
            "source_polygon": deepcopy(config["surfaces"][surface_id].get("source_polygon", [])),
            "quality": {"status": "limited", "reprojection_error_mm": residual,
                        "minimum_marker_edge_px": minimum_edge, "control_distance_checked": False,
                        "calibration_marker_count": len(selected)},
            "warnings": warnings,
        })
    return result


def rectify_photo(image, region: dict, max_size=2400) -> tuple:
    """Return BGR/gray preview and H_photo_to_preview, with a bounded canvas.

    Preview y points down. Invert the returned matrix to convert GUI preview
    clicks back into canonical photo pixels before persisting or measuring.
    A projective horizon through the photo is rejected, not silently cropped.
    """
    import cv2
    import numpy as np
    from .photo_markers import _image_array

    source = _image_array(image)
    if not isinstance(max_size, int) or isinstance(max_size, bool) or not 2 <= max_size <= 4096:
        raise ValueError("max_size must be an integer from 2 to 4096")
    matrix = _matrix(region.get("H"))
    height, width = source.shape[:2]
    if min(height, width) < 2:
        raise ValueError("Photo is too small to rectify")
    photo_corners = [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]
    _check_domain(photo_corners, matrix)
    transformed = np.asarray(transform_points(photo_corners, matrix))
    minimum, maximum = transformed.min(axis=0), transformed.max(axis=0)
    span = maximum - minimum
    if np.any(span < 1e-8) or np.any(span > 1e8):
        raise ValueError("Rectified photo bounds are degenerate or unsafe")
    scale = (max_size - 1) / float(max(span))
    surface_to_preview = np.asarray([[scale, 0, -minimum[0] * scale],
                                     [0, -scale, maximum[1] * scale], [0, 0, 1]], np.float64)
    photo_to_preview = _matrix(surface_to_preview @ matrix)
    output_size = tuple(min(max_size, max(2, int(math.ceil(value * scale)) + 1)) for value in span)
    border = 255 if source.ndim == 2 else (255,) * source.shape[2]
    preview = cv2.warpPerspective(source, photo_to_preview, output_size,
                                  flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return preview, photo_to_preview.tolist()