"""Lesen und Schreiben von Feldwerten anhand des Schemas.

Die Felder eines Elements liegen im .hrp-Format an unterschiedlichen Stellen:
in ``params`` (die Regel), im Grundriss-Layer oder in einem verschachtelten
canvas-Eintrag (Text-Annotationen). Dieses Modul kapselt diese Unterschiede,
damit die GUI einheitlich mit ``get_field`` / ``set_field`` arbeiten kann.
"""

from __future__ import annotations

from typing import Any

from .elements import (
    AnnotationCircle,
    AnnotationEllipse,
    AnnotationRectangle,
    ElecCable,
    ElecPoint,
    ElecRoom,
    Element,
    FloorPlan,
    Hkv,
    HkvLine,
    TextAnnotation,
)
from .schema import FieldSpec

#: Felder von Grundrissen/Einrichtung, die im canvas-Layer liegen
_LAYER_FIELDS = frozenset(
    {
        "offset_x",
        "offset_y",
        "rotation",
        "opacity",
        "mm_per_px",
        "ref_length_mm",
        "fixed_width_mm",
        "fixed_height_mm",
        "polygon_color",
    }
)

#: Felder von Text-Annotationen, die im verschachtelten canvas-Eintrag liegen
_TEXT_ENTRY_FIELDS = frozenset({"content", "font_size", "color", "comment", "visible"})

#: Felder von Grundrissen, die zusätzlich in id-basierten canvas-Maps liegen
_FLOORPLAN_GEOM_FIELDS = {
    "ref_line_visible": "ref_line_visible",
    "ref_line_color": "ref_line_colors",
}

#: Felder, die das Dateiformat doppelt führt: params-Feld -> canvas-Map.
#: Der Canvas liest aus der canvas-Map, gespeichert wird beides – deshalb
#: müssen Änderungen an beide Stellen geschrieben werden.
_MIRRORED_GEOM_FIELDS: dict[type[Element], dict[str, str]] = {
    ElecPoint: {
        "visible": "elec_visible",
        "position": "elec_point_position",
        "height_from_floor": "elec_point_height",
        "note": "elec_point_notes",
        "smarthome_device": "elec_point_smarthome_device",
        "smarthome_device_color": "elec_point_smarthome_device_color",
    },
    ElecCable: {
        "visible": "elec_visible",
        "comment": "elec_cable_notes",
        "stroke_width": "elec_cable_stroke_width",
        "type": "elec_cable_type_text",
        "type_label_visible": "elec_cable_type_label_visible",
        "start_ap": "cable_start_ap",
        "end_ap": "cable_end_ap",
    },
    ElecRoom: {"visible": "elec_room_visible"},
    Hkv: {"visible": "hkv_visible"},
    HkvLine: {"visible": "hkv_line_visible"},
}

_ANNOTATION_DIMENSION_FIELDS = frozenset({"width_value", "height_value"})
_ANNOTATION_RADIUS_FIELDS = frozenset({"corner_radius_value"})


def _annotation_size_unit(element: Element) -> str:
    unit = str(element.data.get("size_unit", "cm") or "cm").strip().lower()
    return "m" if unit == "m" else "cm"


def _annotation_unit_factor_mm(element: Element) -> float:
    return 1000.0 if _annotation_size_unit(element) == "m" else 10.0


def _annotation_geometry_entry(element: Element) -> dict | None:
    if isinstance(element, AnnotationRectangle):
        entry = element.geom.get("annotation_rectangles")
        return entry if isinstance(entry, dict) else None
    if isinstance(element, AnnotationCircle):
        entry = element.geom.get("annotation_circles")
        return entry if isinstance(entry, dict) else None
    if isinstance(element, AnnotationEllipse):
        entry = element.geom.get("annotation_ellipses")
        return entry if isinstance(entry, dict) else None
    return None


def _annotation_mm_per_px(element: Element) -> float:
    document = getattr(element, "_document", None)
    if document is None:
        return 1.0
    floor_id = str(getattr(element, "floor_plan_id", "") or "").strip()
    if floor_id and floor_id in document.floorplans:
        floor = document.floorplans[floor_id]
        layer_value = floor.layer.get("mm_per_px")
        try:
            layer_mpp = float(layer_value)
            if layer_mpp > 0.0:
                return layer_mpp
        except (TypeError, ValueError):
            pass
    try:
        global_mpp = float(document.view.get("mm_per_px", 1.0) or 1.0)
    except (TypeError, ValueError):
        global_mpp = 1.0
    return max(global_mpp, 1e-9)


def _annotation_rect_size_px(element: Element) -> tuple[float, float]:
    entry = _annotation_geometry_entry(element)
    if not isinstance(entry, dict):
        return (0.0, 0.0)
    start = entry.get("start")
    end = entry.get("end")
    if not (isinstance(start, list) and isinstance(end, list) and len(start) >= 2 and len(end) >= 2):
        return (0.0, 0.0)
    try:
        width_px = abs(float(end[0]) - float(start[0]))
        height_px = abs(float(end[1]) - float(start[1]))
    except (TypeError, ValueError):
        return (0.0, 0.0)
    return (width_px, height_px)


def _mirror_key(element: Element, key: str) -> str | None:
    """canvas-Map, in der ein params-Feld zusätzlich geführt wird."""
    return _MIRRORED_GEOM_FIELDS.get(type(element), {}).get(key)


def get_field(element: Element, spec: FieldSpec) -> Any:
    """Liest den gespeicherten Wert eines Feldes."""
    key = spec.key

    if key in _ANNOTATION_DIMENSION_FIELDS and isinstance(
        element, (AnnotationRectangle, AnnotationCircle, AnnotationEllipse)
    ):
        width_px, height_px = _annotation_rect_size_px(element)
        mm_per_px = _annotation_mm_per_px(element)
        width_mm = width_px * mm_per_px
        height_mm = height_px * mm_per_px
        factor_mm = _annotation_unit_factor_mm(element)
        if isinstance(element, AnnotationCircle):
            diameter_mm = max(width_mm, height_mm)
            width_mm = diameter_mm
            height_mm = diameter_mm
        if key == "width_value":
            return round(width_mm / factor_mm, 6)
        return round(height_mm / factor_mm, 6)

    if key in _ANNOTATION_RADIUS_FIELDS and isinstance(element, AnnotationRectangle):
        radius_px = float(element.data.get("corner_radius", 0.0) or 0.0)
        radius_mm = radius_px * _annotation_mm_per_px(element)
        return round(radius_mm / _annotation_unit_factor_mm(element), 6)

    if key == "size_unit" and isinstance(
        element, (AnnotationRectangle, AnnotationCircle, AnnotationEllipse)
    ):
        return _annotation_size_unit(element)

    if isinstance(element, TextAnnotation) and key in _TEXT_ENTRY_FIELDS:
        entry = element.geom.get("text_annotations")
        if isinstance(entry, dict) and key in entry:
            return entry[key]
        return element.data.get(key, spec.default)

    if isinstance(element, FloorPlan):
        if key in _LAYER_FIELDS:
            if key in element.layer:
                return element.layer[key]
            return element.data.get(key, spec.default)
        geom_key = _FLOORPLAN_GEOM_FIELDS.get(key)
        if geom_key is not None:
            if geom_key in element.geom:
                return element.geom[geom_key]
            return element.data.get(key, spec.default)

    if key in element.data:
        value = element.data[key]
        # Bei gespiegelt geführten Feldern bevorzugen wir die canvas-Map,
        # wenn params leer ist (z. B. nach Canvas-Zeichenvorgängen).
        mirror = _mirror_key(element, key)
        if mirror is not None and mirror in element.geom:
            mirror_value = element.geom[mirror]
            if value in (None, "", []) and mirror_value not in (None, "", []):
                return mirror_value
        return value

    # Manche Projekte führen den Wert nur in der canvas-Map.
    mirror = _mirror_key(element, key)
    if mirror is not None and mirror in element.geom:
        return element.geom[mirror]

    return spec.default


def set_field(element: Element, spec: FieldSpec, value: Any) -> None:
    """Schreibt einen Feldwert an die im Format vorgesehene(n) Stelle(n)."""
    key = spec.key

    if key in _ANNOTATION_DIMENSION_FIELDS and isinstance(
        element, (AnnotationRectangle, AnnotationCircle, AnnotationEllipse)
    ):
        entry = _annotation_geometry_entry(element)
        if not isinstance(entry, dict):
            entry = {}
            if isinstance(element, AnnotationRectangle):
                element.geom["annotation_rectangles"] = entry
            elif isinstance(element, AnnotationCircle):
                element.geom["annotation_circles"] = entry
            else:
                element.geom["annotation_ellipses"] = entry

        mm_per_px = _annotation_mm_per_px(element)
        try:
            value_float = max(0.0, float(value))
        except (TypeError, ValueError):
            value_float = 0.0
        target_mm = value_float * _annotation_unit_factor_mm(element)
        target_px = target_mm / mm_per_px

        start = entry.get("start")
        end = entry.get("end")
        if not (isinstance(start, list) and isinstance(end, list) and len(start) >= 2 and len(end) >= 2):
            start = [0.0, 0.0]
            end = [0.0, 0.0]
        try:
            x1, y1 = float(start[0]), float(start[1])
            x2, y2 = float(end[0]), float(end[1])
        except (TypeError, ValueError):
            x1, y1, x2, y2 = 0.0, 0.0, 0.0, 0.0

        left, right = (x1, x2) if x1 <= x2 else (x2, x1)
        top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
        cx = (left + right) * 0.5
        cy = (top + bottom) * 0.5
        width_px = max(0.0, right - left)
        height_px = max(0.0, bottom - top)

        if isinstance(element, AnnotationCircle):
            width_px = target_px
            height_px = target_px
        elif key.startswith("width_"):
            width_px = target_px
        elif key.startswith("height_"):
            height_px = target_px

        half_w = width_px * 0.5
        half_h = height_px * 0.5
        entry["start"] = [cx - half_w, cy - half_h]
        entry["end"] = [cx + half_w, cy + half_h]
        return

    if key in _ANNOTATION_RADIUS_FIELDS and isinstance(element, AnnotationRectangle):
        try:
            value_float = max(0.0, float(value))
        except (TypeError, ValueError):
            value_float = 0.0
        radius_mm = value_float * _annotation_unit_factor_mm(element)
        element.data["corner_radius"] = radius_mm / _annotation_mm_per_px(element)
        return

    if key == "size_unit" and isinstance(
        element, (AnnotationRectangle, AnnotationCircle, AnnotationEllipse)
    ):
        element.data["size_unit"] = "m" if str(value).strip().lower() == "m" else "cm"
        return

    if isinstance(element, TextAnnotation) and key in _TEXT_ENTRY_FIELDS:
        entry = element.geom.get("text_annotations")
        if not isinstance(entry, dict):
            entry = {}
            element.geom["text_annotations"] = entry
        entry[key] = value
        # Der Navigator liest den Namen aus params – Konsistenz wahren.
        if key == "visible":
            element.data[key] = value
        return

    if isinstance(element, FloorPlan):
        if key in _LAYER_FIELDS:
            element.layer[key] = value
            element.data[key] = value  # Format hält beide Stellen redundant
            return
        geom_key = _FLOORPLAN_GEOM_FIELDS.get(key)
        if geom_key is not None:
            element.geom[geom_key] = value
            element.data[key] = value
            return
        if key == "visible":
            element.layer["visible"] = value
            element.data["visible"] = value
            return

    element.data[key] = value

    mirror = _mirror_key(element, key)
    if mirror is not None:
        element.geom[mirror] = value


def display_value(element: Element, spec: FieldSpec) -> Any:
    """Wert für die Anzeige (inkl. Einheitenumrechnung)."""
    return spec.to_display(get_field(element, spec))


def apply_display_value(element: Element, spec: FieldSpec, value: Any) -> None:
    """Anzeigewert übernehmen (inkl. Einheitenumrechnung)."""
    set_field(element, spec, spec.to_storage(value))
