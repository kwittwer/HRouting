"""Lesen und Schreiben von Feldwerten anhand des Schemas.

Die Felder eines Elements liegen im .hrp-Format an unterschiedlichen Stellen:
in ``params`` (die Regel), im Grundriss-Layer oder in einem verschachtelten
canvas-Eintrag (Text-Annotationen). Dieses Modul kapselt diese Unterschiede,
damit die GUI einheitlich mit ``get_field`` / ``set_field`` arbeiten kann.
"""

from __future__ import annotations

from typing import Any

from .elements import Element, FloorPlan, TextAnnotation
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


def get_field(element: Element, spec: FieldSpec) -> Any:
    """Liest den gespeicherten Wert eines Feldes."""
    key = spec.key

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

    return element.data.get(key, spec.default)


def set_field(element: Element, spec: FieldSpec, value: Any) -> None:
    """Schreibt einen Feldwert an die im Format vorgesehene Stelle."""
    key = spec.key

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


def display_value(element: Element, spec: FieldSpec) -> Any:
    """Wert für die Anzeige (inkl. Einheitenumrechnung)."""
    return spec.to_display(get_field(element, spec))


def apply_display_value(element: Element, spec: FieldSpec, value: Any) -> None:
    """Anzeigewert übernehmen (inkl. Einheitenumrechnung)."""
    set_field(element, spec, spec.to_storage(value))
